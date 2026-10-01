"""SCAN_GENERIC_COMPOUND_FILTER (Faz 2a, varsayilan kapali).

Tum parcalari generic olan bilesik adlar (UserService) yalnizca sezgisel
kaynaklarda (LLM, llm_audit, Presidio NER) maskelenmez. Sozluk/alias/runtime
terimleri bu filtreyi asla atlatmaz. Genel isim/sifatlardan olusan kod adlari
(KARAYEL benzeri) bayrak acikken de maskelenmeye devam eder.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from app.core.config import settings
from app.services import exporter
from app.services.audit_reviewer import AuditVerdict
from app.services.consistency_masking import registry_authority
from app.services.detectors import DetectionResult, DetectorOutput, synthetic_llm_rule
from app.services.exporter import export_project
from app.services.mapping_service import detect_matches
from app.services.rule_engine import Match
from app.services.term_classifier import is_generic_compound
from app.services.term_upload import commit_term_upload


@pytest.fixture
def flag(monkeypatch):
    def set_flag(value: bool) -> None:
        monkeypatch.setattr(settings.scan, "generic_compound_filter", value)
    return set_flag


def test_flag_defaults_to_off():
    from app.core.config import ScanSettings

    assert ScanSettings.model_fields["generic_compound_filter"].default is False


@pytest.mark.parametrize("value, generic", [
    ("UserService", True),
    ("OrderController", True),
    ("KayitSorguServisi", True),
    ("getKullaniciListesi", True),
    ("KULLANICI_BILGI_SERVISI", True),
    ("kayıt-sorgu-servisi", True),
    ("IslemDurumu2", True),
    # en az bir parca genel kelime degil
    ("PoseidonGatewayClient", False),
    ("KaraKartalServisi", False),
    ("MaviYildiz", False),
    ("MusteriService", False),
    ("CustomerRepository", False),
    # tek parca ve bosluklu degerler bu kuralin konusu degil
    ("Service", False),
    ("User Service", False),
    ("", False),
])
def test_is_generic_compound(value, generic):
    assert is_generic_compound(value) is generic


class _Fixed:
    def __init__(self, *results: DetectionResult) -> None:
        self.results = list(results)

    async def scan(self, text, metadata=None):
        return DetectorOutput(results=self.results)


def _llm(text: str, value: str, source: str = "llm", tip: str = "IC_SERVIS_ADI", rule=None) -> DetectionResult:
    start = text.index(value)
    return DetectionResult(deger=value, tip=tip, guven_seviyesi="yuksek", kaynak_motor=source,
                           start=start, end=start + len(value), rule=rule)


def _masked_values(text: str, *results: DetectionResult) -> list[str]:
    outcome = asyncio.run(detect_matches(_Fixed(*results), text, {"file_path": "Notlar.md"}))
    return [m.original_value for m in outcome.matches]


def test_llm_generic_compound_is_masked_only_when_flag_off(flag):
    text = "Ilgili sinif: UserService\n"
    flag(False)
    assert _masked_values(text, _llm(text, "UserService")) == ["UserService"]
    flag(True)
    assert _masked_values(text, _llm(text, "UserService")) == []


def test_codename_of_common_words_from_llm_stays_masked_with_flag_on(flag):
    # KARAYEL senaryosu (kara + yel), sozlukte olmayan bir ornekle.
    flag(True)
    text = "Kod adi KaraKartal; istemci: KaraKartalServisi\n"
    assert _masked_values(text, _llm(text, "KaraKartal"), _llm(text, "KaraKartalServisi")) == [
        "KaraKartal", "KaraKartalServisi"]


def test_presidio_ner_generic_compound_is_ignored_but_custom_rules_are_not(flag):
    flag(True)
    text = "Ekip: UserService ve OrderController\n"
    ner = _llm(text, "UserService", source="katman2_presidio", tip="ORGANIZATION",
               rule=synthetic_llm_rule("ORGANIZATION"))
    custom_rule = replace(synthetic_llm_rule("KURUM_KODU"), pattern_type="presidio")
    custom = _llm(text, "OrderController", source="katman2_presidio", tip="KURUM_KODU", rule=custom_rule)
    assert _masked_values(text, ner, custom) == ["OrderController"]
    flag(False)
    assert _masked_values(text, ner) == ["UserService"]


def test_registry_does_not_propagate_generic_compounds_when_flag_on(flag):
    rule = synthetic_llm_rule("DENETIM_BULGUSU")
    match = Match(rule=rule, original_value="UserService", start=0, end=11, entity_type=rule.category,
                  source_detector="llm_audit", confidence="orta")
    flag(False)
    assert registry_authority("UserService", "llm_audit", match) == "weak"
    flag(True)
    assert registry_authority("UserService", "llm_audit", match) is None
    assert registry_authority("KaraKartalServisi", "llm_audit", match) == "weak"


def test_dictionary_term_is_never_filtered(db_session, tmp_path, monkeypatch, flag):
    flag(True)
    commit_term_upload(db_session, filename="terms.txt", content=b"UserService\n", category="pytest_generic")

    async def clean(*args, **kwargs):
        return AuditVerdict(risky=False)

    monkeypatch.setattr(exporter, "audit_masked_text", clean)
    source = tmp_path / "kaynak"
    source.mkdir()
    (source / "notlar.md").write_text("Sinif: UserService\n", encoding="utf-8")
    report = asyncio.run(export_project(
        db_session, source_path=str(source), target_path=str(tmp_path / "cikti"),
        project_name="pytest-generic", sicil_no="P-GEN", branch_name="main", initiated_by="P-GEN",
    ))

    assert report.outcomes[0].final_state == "READY"
    assert "UserService" not in (tmp_path / "cikti" / "notlar.md").read_text(encoding="utf-8")
