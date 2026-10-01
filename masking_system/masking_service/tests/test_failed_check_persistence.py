"""Ciktidan alikonan her dosyanin nedeni (failed_check) kalici ve olculebilir olmali.

- Kodlar tek kaynaktan (failed_checks.FailedCheck) gelir; exporter'da serbest
  string kullanilmaz.
- AuditWarning.failed_check (DB: basarisiz_kontrol) export sirasinda doldurulur.
- ExportReport.blocked_by_check ve rapor metni nedene gore kirilim verir.
"""

from __future__ import annotations

import ast
from pathlib import Path

from sqlalchemy import select

from app.db.models import AuditWarning
from app.db.session import SessionLocal
from app.services import exporter as exporter_module
from app.services.audit_reviewer import AuditFinding, AuditVerdict
from app.services.detectors import DetectorOutput
from app.services.failed_checks import FAILED_CHECK_LABELS, FailedCheck, failed_check_label
from tests.test_exporter_failure_handling import _cleanup_identity, _run_export

_IDENTITY_PREFIX = "pytest-failed-check"


def test_every_failed_check_has_a_label():
    assert set(FAILED_CHECK_LABELS) == set(FailedCheck)
    assert failed_check_label("tanimsiz_kod") == "bilinmiyor"
    assert failed_check_label(None) == "bilinmiyor"


def test_exporter_never_passes_failed_check_as_free_string():
    source = Path(exporter_module.__file__).read_text(encoding="utf-8")
    offenders = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.keyword) and node.arg in ("failed_check", "check"):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                offenders.append(node.value.lineno)
        if isinstance(node, (ast.Assign, ast.Return)):
            value = node.value
            if (isinstance(value, ast.Constant) and isinstance(value.value, str)
                    and value.value in {check.value for check in FailedCheck}):
                offenders.append(value.lineno)
    assert offenders == [], f"serbest failed_check string'i: satir {offenders}"


def _source(tmp_path, name: str, content: str) -> Path:
    source = tmp_path / "src"
    source.mkdir()
    (source / name).write_text(content, encoding="utf-8")
    return source


def _warnings(run_id: int) -> list[AuditWarning]:
    with SessionLocal() as db:
        return list(db.scalars(select(AuditWarning).where(AuditWarning.run_id == run_id)).all())


def test_llm_detection_failure_is_persisted_as_llm_tespit(tmp_path, monkeypatch):
    project = f"{_IDENTITY_PREFIX}-llm-tespit"
    try:
        class _FailingLLM:
            async def scan(self, text, metadata=None):
                return DetectorOutput(results=[], errors=["LLM taramasi basarisiz (simulated)"])

        async def _clean_audit(*args, **kwargs):
            return AuditVerdict(risky=False)

        monkeypatch.setattr(exporter_module, "build_orchestrator", lambda *a, **kw: _FailingLLM())
        monkeypatch.setattr(exporter_module, "audit_masked_text", _clean_audit)
        monkeypatch.setattr(exporter_module.settings.vllm, "enabled", True)

        report = _run_export(_source(tmp_path, "app.py", "value = 1\n"), tmp_path / "target", project)

        assert report.blocked_by_check == {"llm_tespit": 1}
        assert [o.failed_check for o in report.outcomes] == [FailedCheck.LLM_TESPIT]
        assert [w.failed_check for w in _warnings(report.run_id)] == ["llm_tespit"]
        assert "Ciktiya alinmama nedenleri" in report.summary_text()
        assert "LLM tespiti tamamlanamadı (llm_tespit): 1" in report.summary_text()
    finally:
        _cleanup_identity(project)


def test_unremediable_audit_finding_is_persisted_as_llm_denetimi(tmp_path, monkeypatch):
    """Cok satirli denetim alintisi otomatik duzeltilemez; dosya llm_denetimi ile karantinaya duser."""
    project = f"{_IDENTITY_PREFIX}-llm-denetimi"
    try:
        class _Clean:
            async def scan(self, text, metadata=None):
                return DetectorOutput(results=[], errors=[])

        async def _risky_audit(text, *args, **kwargs):
            return AuditVerdict(risky=True, findings=[AuditFinding("adres", "Cad. No:1\nMaslak")])

        monkeypatch.setattr(exporter_module, "build_orchestrator", lambda *a, **kw: _Clean())
        monkeypatch.setattr(exporter_module, "audit_masked_text", _risky_audit)
        monkeypatch.setattr(exporter_module.settings.vllm, "enabled", True)

        report = _run_export(_source(tmp_path, "notes.txt", "Cad. No:1\nMaslak\n"), tmp_path / "target", project)

        assert report.blocked_by_check == {"llm_denetimi": 1}
        warnings = _warnings(report.run_id)
        assert [w.failed_check for w in warnings] == ["llm_denetimi"]
        assert "alıntı birden fazla satıra yayılıyor" in warnings[0].reasoning
    finally:
        _cleanup_identity(project)


def test_clean_export_has_no_blocked_breakdown(tmp_path, monkeypatch):
    project = f"{_IDENTITY_PREFIX}-clean"
    try:
        class _Clean:
            async def scan(self, text, metadata=None):
                return DetectorOutput(results=[], errors=[])

        monkeypatch.setattr(exporter_module, "build_orchestrator", lambda *a, **kw: _Clean())
        monkeypatch.setattr(exporter_module.settings.vllm, "enabled", False)

        report = _run_export(_source(tmp_path, "app.py", "value = 1\n"), tmp_path / "target", project)

        assert report.blocked_by_check == {}
        assert "Ciktiya alinmama nedenleri" not in report.summary_text()
    finally:
        _cleanup_identity(project)
