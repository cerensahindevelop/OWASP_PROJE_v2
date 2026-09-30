"""Kodlanmis (base64/hex/bayt dizisi) metnin icindeki hassas veri:
cozulmus metin yerel katmanlarla taranir, bulgu varsa dosya karantinaya
alinir; deger ya da cozulmus metin hicbir rapora/loga yazilmaz."""
from __future__ import annotations

import asyncio
import base64
import json

import pytest

from app.db.models import AuditWarning
from app.services import exporter as exporter_module
from app.services.audit_reviewer import AuditVerdict
from app.services.detectors import DetectionOrchestrator, DetectorRegistry, RuleBasedDetector
from app.services.encoded_blobs import find_encoded_text_blocks
from app.services.encoded_text_detector import EncodedTextDetector
from app.services.rule_engine import RuleSpec

CONNECTION = "Server=10.20.30.40;Database=Faturalar;User Id=sa;Password=Sup3rGizli!;"
SECRETS = ("Sup3rGizli!", "10.20.30.40", "Passw0rd", "hakan:Sifre123")


def _b64(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


# --- Cozme ---------------------------------------------------------------

@pytest.mark.parametrize("text,kind,decoded", [
    (json.dumps({"Kodlu": _b64(CONNECTION)}), "base64", CONNECTION),
    (f'auth = "{_b64("sa:Passw0rd")}"', "base64", "sa:Passw0rd"),
    (f'x = "{base64.urlsafe_b64encode(b"user=ali;pwd=Gizli?>").decode().rstrip("=")}"', "base64",
     "user=ali;pwd=Gizli?>"),
    (f'key = "{b"password1".hex()}"', "hex", "password1"),
    ("int[] k = {" + ", ".join(str(b) for b in b"hakan:Sifre123") + "};", "bayt_dizisi", "hakan:Sifre123"),
    ("K = b'" + "".join(f"\\x{b:02x}" for b in b"db.acme.local") + "'", "bayt_dizisi", "db.acme.local"),
])
def test_encoded_text_is_decoded(text, kind, decoded):
    [block] = find_encoded_text_blocks(text)
    assert (block.kind, block.decoded) == (kind, decoded)


@pytest.mark.parametrize("text", [
    "class ConnectionStringBuilder { void encrypt_blowfish() {} }",
    "sha = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'",
    "values = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}",
])
def test_code_and_hashes_are_not_decoded_as_text(text):
    assert find_encoded_text_blocks(text) == []


# --- Tespit ---------------------------------------------------------------

def _detector() -> EncodedTextDetector:
    ip_rule = RuleSpec(
        id=1, rule_name="ipv4", category="ip", pattern_type="regex",
        regex_pattern=r"\b(?:\d{1,3}\.){3}\d{1,3}\b", regex_flags=None, placeholder_prefix="mask_ip", priority=10,
    )
    corporate = RuleSpec(
        id=2, rule_name="kurumsal_terim_7", category="Gizli Proje Poseidon", pattern_type="regex",
        regex_pattern=r"Poseidon", regex_flags=None, placeholder_prefix="mask_kurumsal_ifade", priority=10,
    )
    registry = DetectorRegistry()
    registry.register(RuleBasedDetector([ip_rule, corporate]))
    return EncodedTextDetector(DetectionOrchestrator(registry))


def _leaks(text: str) -> list[str]:
    return asyncio.run(_detector().detect(text, {"file_path": "a.json"})).encoded_leaks


def test_decoded_findings_are_reported_without_values():
    [leak] = _leaks('{\n  "Kodlu": "' + _b64(CONNECTION) + '"\n}')
    assert leak.startswith("satir 2: base64")
    assert "IP" in leak.upper() and "KIMLIK_BILGISI" in leak
    assert not any(secret in leak for secret in SECRETS)


@pytest.mark.parametrize("value", ["sa:Passw0rd", "Password=abc", '{"sifre": "x1y2"}', "api_key: q9w8e7"])
def test_short_credentials_are_caught_by_credential_shapes(value):
    [leak] = _leaks(f'v = "{_b64(value)}"')
    assert "KIMLIK_BILGISI" in leak


def test_corporate_term_title_is_not_exposed():
    [leak] = _leaks(f'v = "{_b64("owner team Poseidon")}"')
    assert "KURUMSAL_TERIM" in leak and "Poseidon" not in leak


def test_harmless_encoded_text_is_not_a_leak():
    assert _leaks(f'greeting = "{_b64("Hello World from the test suite")}"') == []


# --- Export: karantina ----------------------------------------------------

def test_export_quarantines_file_with_encoded_secret(db_session, tmp_path, monkeypatch):
    source = tmp_path / "src"
    source.mkdir()
    (source / "appsettings.json").write_text(json.dumps({"Kodlu": _b64(CONNECTION)}), encoding="utf-8")
    (source / "Client.cs").write_text(f'var auth = "{_b64("sa:Passw0rd")}";\n', encoding="utf-8")
    (source / "Clean.cs").write_text(f'var s = "{_b64("Hello World from the test suite")}";\n', encoding="utf-8")

    async def clean_audit(masked_text, vllm_settings):
        return AuditVerdict(risky=False)

    monkeypatch.setattr(exporter_module, "audit_masked_text", clean_audit)
    monkeypatch.setattr(exporter_module.settings.vllm, "enabled", False)
    target = tmp_path / "out"
    report = asyncio.run(exporter_module.export_project(
        db_session, source_path=str(source), project_name="pytest-encoded-leak", sicil_no="T-ENC",
        branch_name="main", target_path=str(target), initiated_by="T-ENC",
    ))

    assert sorted(p.name for p in target.iterdir() if not p.name.startswith(".")) == ["Clean.cs"]
    assert report.files_quarantined_pending_audit == 2
    warnings = db_session.query(AuditWarning).filter(AuditWarning.run_id == report.run_id).all()
    assert sorted(w.file_path for w in warnings) == ["Client.cs", "appsettings.json"]
    for warning in warnings:
        assert "Kodlanmış" in warning.reasoning and warning.audit_failed is False
        assert not any(secret in warning.reasoning for secret in SECRETS)
