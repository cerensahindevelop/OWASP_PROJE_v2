"""Unsupported content stays out of output without becoming a release request."""

import asyncio

import pytest
from sqlalchemy import select

from app.api.routers.export import _pending_and_quarantined_counts
from app.api.schemas import ExportReportOut
from app.db.models import AuditLog, AuditWarning
from app.services import exporter
from app.services.detectors import DetectorOutput
from app.services.integrity_manifest import read_manifest
from app.webapp.audit_presentation import summarize_audit_entries
from app.webapp.export_page import _clean_copied_label


@pytest.fixture()
def export_files(tmp_path, db_session, monkeypatch):
    class TextDetector:
        async def scan(self, text, metadata=None):
            return DetectorOutput()

    monkeypatch.setattr(exporter, "build_orchestrator", lambda *a, **kw: TextDetector())
    monkeypatch.setattr(exporter.settings.vllm, "enabled", False)

    def run(files, **options):
        source = tmp_path / "source"
        target = tmp_path / "output"
        source.mkdir()
        for name, content in files.items():
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        report = asyncio.run(exporter.export_project(
            db_session, source_path=str(source), target_path=str(target),
            project_name=tmp_path.name, sicil_no="TEST-UNSUPPORTED", branch_name="test",
            initiated_by="TEST-UNSUPPORTED", enable_path_masking=False, **options,
        ))
        return report, source, target

    return run


@pytest.mark.parametrize("name", ["sample.bin", "assets/blob.dat", "notes.txt", "unknown.custom", "no_extension"])
def test_binary_content_is_excluded_across_names_and_projects(export_files, db_session, name):
    binary = bytes(range(256)) * 4
    report, source, target = export_files({name: binary, "good.py": b"value = 1\n"})

    assert report.status == "completed_with_warnings"
    assert report.files_scanned == 2
    assert report.files_ready == 1
    assert report.files_skipped_unsupported == 1
    assert report.files_validation_failed == report.files_copied_binary == 0
    assert not (target / name).exists()
    assert (source / name).read_bytes() == binary
    assert (target / "good.py").read_bytes() == b"value = 1\n"
    manifest = read_manifest(target, report.context_id)
    assert manifest["complete"] is False
    assert set(manifest["files"]) == {"good.py"}
    assert not db_session.scalars(select(AuditWarning).where(AuditWarning.run_id == report.run_id)).all()
    assert _pending_and_quarantined_counts(
        db_session, project_name=report.project_name, sicil_no=report.sicil_no,
        branch_name=report.branch_name, run_id=report.run_id,
    ) == (0, 0, 0)

    api_report = ExportReportOut.model_validate(report)
    assert api_report.files_skipped_unsupported == 1
    outcome = next(o for o in api_report.outcomes if o.relative_path == name)
    assert outcome.status == "skipped_unsupported"
    assert outcome.final_state == "SKIPPED"
    assert "çıktıya alınmadı" in outcome.error
    assert name in report.summary_text()
    assert "desteklenmeyen içerik" in _clean_copied_label(report)
    entries = db_session.scalars(select(AuditLog).where(AuditLog.run_id == report.run_id)).all()
    row = next(r for r in summarize_audit_entries(entries) if r["Dosya"] == name)
    assert row["Sonuç"] == "Desteklenmeyen içerik — çıktıya alınmadı"
    assert row["Tarama"] == "Taranmadı"
    assert row["Hata"] == 0


def test_all_binary_project_is_not_reported_complete(export_files):
    report, _, target = export_files({"payload.bin": bytes(range(256))})
    assert report.status == "completed_with_warnings"
    assert report.files_ready == report.files_validation_failed == 0
    assert report.files_skipped_unsupported == 1
    manifest = read_manifest(target, report.context_id)
    assert manifest["complete"] is False
    assert manifest["files"] == {}


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-32"])
def test_text_with_binary_extension_is_still_scanned(export_files, encoding):
    content = "ordinary readable text\n".encode(encoding)
    report, _, target = export_files({"readable.bin": content})
    assert report.status == "completed"
    assert report.files_skipped_unsupported == 0
    assert report.files_ready == 1
    assert (target / "readable.bin").read_bytes() == content


@pytest.mark.parametrize("content,options,status", [
    (b"readable text", {"max_inline_size": 4}, "skipped_too_large"),
    (b"\xef\xbb\xbfbroken utf8\xff", {}, "copied_undecodable"),
])
def test_real_read_failures_remain_blocked(export_files, db_session, content, options, status):
    report, _, target = export_files({"broken.txt": content}, **options)
    assert report.files_skipped_unsupported == 0
    assert report.files_validation_failed == 1
    assert report.outcomes[0].status == status
    assert not (target / "broken.txt").exists()
    warnings = db_session.scalars(select(AuditWarning).where(AuditWarning.run_id == report.run_id)).all()
    assert len(warnings) == 1
    assert warnings[0].audit_failed


@pytest.mark.parametrize("name", ["lib/app.jar", "LIB/Big.JAR"])
def test_large_jar_is_unsupported_type_not_failed_check(export_files, db_session, name):
    # Size check must not run before the known-type check: a JAR over the
    # inline limit is still "unsupported type", not a failed security check.
    payload = b"PK\x03\x04" + bytes(range(256)) * 64
    report, _, target = export_files({name: payload, "good.py": b"value = 1\n"}, max_inline_size=16)

    assert report.files_skipped_unsupported == 1
    assert report.files_validation_failed == 0
    outcome = next(o for o in report.outcomes if o.relative_path == name)
    assert outcome.status == "skipped_unsupported"
    assert "Desteklenmeyen dosya türü (.jar)" in outcome.error
    assert "taranmadı ve çıktıya alınmadı" in outcome.error
    assert not (target / name).exists()
    assert not db_session.scalars(select(AuditWarning).where(AuditWarning.run_id == report.run_id)).all()
