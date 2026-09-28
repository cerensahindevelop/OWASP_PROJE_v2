"""Bolum 3 duzeltmelerinin regresyon testleri:
  - Shannon entropy hesaplamasi (pure function).
  - PresidioDetector._entities_for_file: dosya uzantisina gore izinli
    kategori + ozel kural entity type birlesimi.
  - PresidioDetector.detect(): entropy-gate entegrasyonu (yuksek entropili
    bir "dogal dil" bulgusu bastiriliyor mu).
  - Gercek DB'ye karsi entegrasyon: wrong_category_match ornekleri (.py),
    .md dosyasinda PERSON'in hala calismasi, ve KOD DEGISIKLIGI OLMADAN
    yeni bir uzanti (.tf) kuralinin devreye girmesi.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from app.services.detectors import DetectionResult
from app.services.entropy import NATURAL_LANGUAGE_ENTITY_TYPES, is_high_entropy, shannon_entropy
from app.services.presidio_detector import PresidioDetector, PresidioRuleSpec


class TestShannonEntropy:
    def test_empty_string_is_zero(self):
        assert shannon_entropy("") == 0.0

    def test_single_repeated_character_is_zero(self):
        assert shannon_entropy("aaaaaaaa") == 0.0

    def test_real_name_is_below_default_threshold(self):
        assert not is_high_entropy("John Smith", 3.5)

    def test_api_key_like_string_is_above_default_threshold(self):
        assert is_high_entropy("AIzaSyBUPHAjZl3n8Eza66ka6JbNv9qeg_actualKeyHere", 3.5)

    def test_threshold_is_configurable_not_hardcoded(self):
        value = "AIzaSyBUPHAjZl3n8Eza66ka6JbNv9qeg_actualKeyHere"
        assert is_high_entropy(value, 3.5)
        assert not is_high_entropy(value, 10.0)  # cok yuksek bir esikle artik "yuksek" sayilmaz


class TestEntitiesForFile:
    def _make_detector(self, category_restrictions):
        custom_rule = PresidioRuleSpec(
            rule_name="presidio_internal_domain_name",
            regex_pattern=r"internal\.example\.com",
            entity_type="IC_DOMAIN_ADI",
            confidence_score=0.9,
            is_allow_list=False,
        )
        return PresidioDetector(
            [custom_rule], category_restrictions=category_restrictions, entropy_threshold=3.5
        )

    def test_no_row_for_extension_means_unrestricted(self):
        detector = self._make_detector({"py": ["EMAIL_ADDRESS"]})
        entities = detector._entities_for_file({"file_path": "README.md"})
        assert entities is None

    def test_restricted_extension_includes_custom_rule_entities(self):
        detector = self._make_detector({"py": ["EMAIL_ADDRESS", "IP_ADDRESS"]})
        entities = detector._entities_for_file({"file_path": "app/config.py"})
        assert entities is not None
        assert set(entities) == {"EMAIL_ADDRESS", "IP_ADDRESS", "IC_DOMAIN_ADI"}

    def test_extension_is_case_insensitive_and_dot_stripped(self):
        detector = self._make_detector({"py": ["EMAIL_ADDRESS"]})
        entities = detector._entities_for_file({"file_path": "CONFIG.PY"})
        assert entities is not None
        assert "EMAIL_ADDRESS" in entities

    def test_no_file_path_metadata_means_unrestricted(self):
        detector = self._make_detector({"py": ["EMAIL_ADDRESS"]})
        assert detector._entities_for_file({}) is None
        assert detector._entities_for_file(None) is None


class TestEntropyGateIntegration:
    """detect()'in, _analyze()'den donen sonuclari entropy'ye gore
    filtreledigini - AnalyzerEngine'i gercekten calistirmadan, _analyze'i
    monkeypatch'leyerek - dogrular."""

    def _detector_with_fake_analyze(self, monkeypatch, results):
        detector = PresidioDetector([])
        monkeypatch.setattr(detector, "_analyze", lambda content, entities=None: results)
        return detector

    def test_high_entropy_person_result_is_suppressed(self, monkeypatch):
        from app.services.presidio_detector import _AnalyzerResult

        content = "owner = AIzaSyBUPHAjZl3n8Eza66ka6JbNv9qeg_actualKeyHere"
        fake_result = _AnalyzerResult(
            entity_type="PERSON",
            start=content.index("AIza"),
            end=len(content),
            score=0.9,
        )
        detector = self._detector_with_fake_analyze(monkeypatch, [fake_result])

        output = asyncio.run(detector.detect(content, metadata={"file_path": "notes.md"}))

        assert output.results == []

    def test_low_entropy_person_result_passes_through(self, monkeypatch):
        from app.services.presidio_detector import _AnalyzerResult

        content = "owner = John Smith"
        fake_result = _AnalyzerResult(
            entity_type="PERSON",
            start=content.index("John"),
            end=len(content),
            score=0.9,
        )
        detector = self._detector_with_fake_analyze(monkeypatch, [fake_result])

        output = asyncio.run(detector.detect(content, metadata={"file_path": "notes.md"}))

        assert len(output.results) == 1
        assert output.results[0].tip == "PERSON"

    def test_non_natural_language_category_bypasses_entropy_check(self, monkeypatch):
        # EMAIL_ADDRESS gibi yapisal kategoriler NATURAL_LANGUAGE_ENTITY_TYPES
        # icinde degil - entropy'si ne olursa olsun entropy-gate'e takilmamali.
        from app.services.presidio_detector import _AnalyzerResult

        assert "EMAIL_ADDRESS" not in NATURAL_LANGUAGE_ENTITY_TYPES
        content = "contact=x7Kf9mQ2pL@example.com"
        fake_result = _AnalyzerResult(
            entity_type="EMAIL_ADDRESS", start=content.index("x7Kf"), end=len(content), score=0.9
        )
        detector = self._detector_with_fake_analyze(monkeypatch, [fake_result])

        output = asyncio.run(detector.detect(content, metadata={"file_path": "notes.md"}))

        assert len(output.results) == 1


# --------------------------------------------------------------------------
# Gercek DB'ye karsi entegrasyon testleri (verification_steps 1-3)
# --------------------------------------------------------------------------

_WRONG_CATEGORIES = {
    "ORGANIZATION", "PERSON", "DATE_TIME", "LOCATION", "NRP",
    "US_DRIVER_LICENSE", "US_BANK_NUMBER", "US_PASSPORT", "US_SSN",
    "UK_NHS", "MEDICAL_LICENSE", "US_ITIN",
}


def _scan(content: str, file_path: str):
    from app.db.session import SessionLocal
    from app.services.mapping_service import (
        build_orchestrator,
        load_active_presidio_rules,
        load_active_rules,
        load_file_category_restrictions,
    )

    with SessionLocal() as db:
        rules = load_active_rules(db)
        presidio_rules = load_active_presidio_rules(db)
        restrictions = load_file_category_restrictions(db)
    orchestrator = build_orchestrator(
        rules,
        {"project_name": "x", "sicil_no": "y", "branch_name": "z"},
        presidio_rules,
        restrictions,
    )
    return asyncio.run(orchestrator.scan(content, metadata={"file_path": file_path}))


def test_verification_1_wrong_category_match_no_longer_occurs_on_py_file():
    content = (
        "GOOGLE_CAPTCHA_KEY = 'AIzaSyBUPHAjZl3n8Eza66ka6JbNv9qeg_actualKeyHere'\n"
        "PRIVATE_KEY_FRAGMENT = 'MIIEpAIBAAKCAQEA7X9nP2vQm5Kj3RtY8wXz1LpN4sB6dH0gC2fE9uV7aI5oT3kM'\n"
    )
    output = _scan(content, "config.py")
    found_wrong = {r.tip for r in output.results if r.tip in _WRONG_CATEGORIES}
    assert found_wrong == set(), f"kod dosyasinda hala yanlis kategori uretiliyor: {found_wrong}"


def test_verification_2_person_category_still_works_on_md_file():
    content = "Bu proje John Smith tarafindan gelistirilmistir."
    output = _scan(content, "README.md")
    assert any(r.tip == "PERSON" for r in output.results), "PERSON kategorisi .md dosyasinda calismiyor"


def test_verification_3_new_extension_rule_takes_effect_without_code_change():
    from sqlalchemy import text as sqltext

    from app.db.session import SessionLocal

    with SessionLocal() as db:
        db.execute(
            sqltext(
                "INSERT INTO dosya_tipi_kategori_kisitlamasi (dosya_uzantisi, izinli_kategori, aktif_mi) "
                "VALUES ('tf_pytest', 'EMAIL_ADDRESS', true), ('tf_pytest', 'IP_ADDRESS', true)"
            )
        )
        db.commit()

    try:
        content = 'owner_name = "John Smith"\nadmin_email = "admin@example.com"\nserver_ip = "10.0.0.5"\n'
        output = _scan(content, "main.tf_pytest")
        categories = {r.tip for r in output.results}
        assert "PERSON" not in categories, "yeni eklenen kisitlama devreye girmedi - PERSON hala uretiliyor"
        assert "EMAIL_ADDRESS" in categories
        assert "IP_ADDRESS" in categories
    finally:
        with SessionLocal() as db:
            db.execute(
                sqltext("DELETE FROM dosya_tipi_kategori_kisitlamasi WHERE dosya_uzantisi = 'tf_pytest'")
            )
            db.commit()
