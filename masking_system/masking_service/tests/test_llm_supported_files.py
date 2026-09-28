"""Only excluded or unsupported files bypass LLM detection and audit."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db.models import AuditLog
from app.services import exporter
from app.services.audit_reviewer import AuditVerdict
from app.services.detectors import DetectionOrchestrator, DetectorOutput, DetectorRegistry, DetectionResult, synthetic_llm_rule
from app.services.llm_detector import LLMDetector
from app.services.scanner import ScannedFile, iter_project_files


def _orchestrator(monkeypatch, local_calls, llm_calls, *, fail_llm=False):
    class LocalDetector:
        name = "dictionary"

        async def detect(self, text, metadata=None):
            local_calls.append(metadata["file_path"])
            value = "Aster Harbor"
            if value not in text:
                return DetectorOutput()
            start = text.index(value)
            return DetectorOutput(results=[DetectionResult(
                deger=value, tip="kurum", guven_seviyesi="yuksek",
                kaynak_motor=self.name, start=start, end=start + len(value),
                rule=synthetic_llm_rule("kurum"),
            )])

    async def fake_find(text, consumed, settings, metadata, extra):
        llm_calls.append(metadata["file_path"])
        if fail_llm:
            from app.services.llm_recognizer import LLMRecognitionError
            raise LLMRecognitionError("simulated unavailable model")
        return []

    monkeypatch.setattr("app.services.llm_detector.find_llm_detections", fake_find)
    registry = DetectorRegistry()
    registry.register(LocalDetector())
    registry.register(LLMDetector(SimpleNamespace(enabled=True)))
    return DetectionOrchestrator(registry)


def test_export_scans_all_supported_text_and_excludes_unsupported_files(tmp_path, db_session, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "out"
    files = {
        "math.py": "def add(a, b):\n    return a + b\n",
        "auth.yaml": "mode: local\n",
        "notes.txt": "Customer: Aster Harbor\n",
        "plain.lock": "stable content\n",
        "credentials.lock": "password: private-value\n",
        "auth/empty.yaml": "",
        ".git/config": "password: private-value\n",
        ".git/nested/package-lock.json": '{"token": "private-value"}',
        "vendor/private.lock": "password: private-value\n",
        "build/auth.yaml": "password: private-value\n",
        "report.pdf": "password: private-value\n",
        "auth.zip": "password: private-value\n",
    }
    for name, content in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    local_calls, llm_calls, audit_calls = [], [], []
    orchestrator = _orchestrator(monkeypatch, local_calls, llm_calls)
    monkeypatch.setattr(exporter, "build_orchestrator", lambda *a, **kw: orchestrator)
    monkeypatch.setattr(exporter.settings.vllm, "enabled", True)

    async def fake_audit(text, file_path=""):
        audit_calls.append(file_path)
        return AuditVerdict(risky=False)

    monkeypatch.setattr(exporter, "_audit_one", fake_audit)
    report = asyncio.run(exporter.export_project(
        db_session, source_path=str(source), target_path=str(target),
        project_name=tmp_path.name, sicil_no="TEST-SCOPE", branch_name="test",
        initiated_by="TEST-SCOPE", enable_path_masking=False,
    ))
    assert set(llm_calls) == {"math.py", "auth.yaml", "notes.txt", "plain.lock", "credentials.lock"}
    assert set(audit_calls) == {"math.py", "auth.yaml", "notes.txt", "auth/empty.yaml"}
    assert set(local_calls) == {"math.py", "auth.yaml", "notes.txt", "plain.lock", "credentials.lock", "auth/empty.yaml"}
    assert (target / "math.py").read_bytes() == (source / "math.py").read_bytes()
    assert (target / "plain.lock").read_bytes() == (source / "plain.lock").read_bytes()
    assert "Aster Harbor" not in (target / "notes.txt").read_text()
    for name in (".git", "vendor", "build", "report.pdf", "auth.zip"):
        assert not (target / name).exists(), name
    logs = db_session.scalars(select(AuditLog).where(AuditLog.run_id == report.run_id)).all()
    assert any(row.file_path == "math.py" and "scan_policy mode=mask presidio=True llm=True" in (row.detail or "") for row in logs)
    assert any(row.file_path == "notes.txt" and "scan_policy mode=mask presidio=True llm=True" in (row.detail or "") for row in logs)


@pytest.mark.parametrize("name", [".git/config", "nested/.git", "vendor/poetry.lock", "auth.pdf", "auth.zip", r"nested\.git\config"])
def test_direct_llm_detector_cannot_scan_excluded_formats(monkeypatch, name):
    calls = []

    async def fake_find(*args):
        calls.append(args)
        return []

    monkeypatch.setattr("app.services.llm_detector.find_llm_detections", fake_find)
    output = asyncio.run(LLMDetector(SimpleNamespace(enabled=True)).detect(
        "password: private-value", {"file_path": name, "enable_llm": True},
    ))
    assert calls == []
    assert output.results == []


def test_builtin_directory_exclusion_prunes_before_reading(tmp_path, monkeypatch):
    path = tmp_path / ".git" / "objects" / "secret.lock"
    path.parent.mkdir(parents=True)
    path.write_text("password: private-value")
    (tmp_path / "normal.txt").write_text("normal")
    files = list(iter_project_files(tmp_path, [], prune_ignored=True))
    assert {str(f.relative_path) for f in files} == {".git", "normal.txt"}
    assert next(f for f in files if f.relative_path.name == ".git").excluded_by is not None

    def must_not_read(*args, **kwargs):
        pytest.fail("excluded files must be rejected before reading")

    monkeypatch.setattr(exporter, "read_scanned_file", must_not_read)
    prep = exporter._prepare_file(
        SimpleNamespace(add=lambda row: None), None,
        ScannedFile(path, path.relative_to(tmp_path), False), tmp_path / "out", 10000,
    )
    assert prep.outcome.status == "excluded"


@pytest.mark.parametrize("failure", ["detection", "missing_audit"])
def test_llm_failure_still_quarantines_all_supported_files(tmp_path, db_session, monkeypatch, failure):
    source = tmp_path / "source"
    source.mkdir()
    (source / "auth.yaml").write_text("mode: local\n")
    (source / "plain.txt").write_text("Ordinary documentation\n")
    target = tmp_path / "out"
    local_calls, llm_calls = [], []
    orchestrator = _orchestrator(monkeypatch, local_calls, llm_calls, fail_llm=failure == "detection")
    monkeypatch.setattr(exporter, "build_orchestrator", lambda *a, **kw: orchestrator)
    monkeypatch.setattr(exporter.settings.vllm, "enabled", True)

    async def clean_audit(*args):
        return None if failure == "missing_audit" else AuditVerdict(risky=False)

    monkeypatch.setattr(exporter, "_audit_one", clean_audit)
    report = asyncio.run(exporter.export_project(
        db_session, source_path=str(source), target_path=str(target),
        project_name=tmp_path.name, sicil_no="TEST-SCOPE", branch_name="test",
        initiated_by="TEST-SCOPE", enable_path_masking=False,
    ))
    assert llm_calls == ["auth.yaml", "plain.txt"]
    assert not (target / "auth.yaml").exists()
    assert not (target / "plain.txt").exists()
    assert next(o for o in report.outcomes if o.relative_path == "auth.yaml").final_state == "VALIDATION_FAILED"
