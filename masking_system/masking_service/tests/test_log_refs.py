"""Kural 7 (Faz 2a): log ve export raporunda kaynak yol yerine "maskeli yol#kimlik".

Kimlik job anahtarli HMAC'tir; sunucuda `dosya-kimligi` ile DB'deki kaynak yola
eslenir. Kok klasorler de dosya yollariyla ayni yol maskelemesinden gecer.
"""

from __future__ import annotations

import asyncio
import logging
import re

from app.services import exporter
from app.services.detectors import DetectorOutput
from app.services.exporter import export_project
from app.services.log_refs import FILE_REF_CHARS, file_label, file_ref
from app.services.reporting import find_file_by_ref
from app.services.term_upload import commit_term_upload


class _Crash:
    async def scan(self, text, metadata=None):
        raise RuntimeError("tespit sonrasi isleme cokmesi")


class _NoDetections:
    async def scan(self, text, metadata=None):
        return DetectorOutput(results=[])


def test_file_ref_is_keyed_short_and_stable():
    a = file_ref(1, 7, "src/karayel/a.py")
    assert re.fullmatch(rf"[0-9a-f]{{{FILE_REF_CHARS}}}", a)
    assert a == file_ref(1, 7, "src/karayel/a.py")
    assert a != file_ref(1, 8, "src/karayel/a.py") and a != file_ref(2, 7, "src/karayel/a.py")
    assert file_label("src/mask_x_1/a.py", a) == f"src/mask_x_1/a.py#{a}"


def _export(db_session, tmp_path, files, root_name):
    source = tmp_path / root_name
    for rel, content in files.items():
        path = source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return asyncio.run(export_project(
        db_session, source_path=str(source), target_path=str(tmp_path / f"{root_name}-cikti"),
        project_name="pytest-logref", sicil_no="P-LOGREF", branch_name="main", initiated_by="P-LOGREF",
    ))


def test_report_roots_and_files_are_masked_and_ids_resolve(db_session, tmp_path, monkeypatch, caplog):
    commit_term_upload(db_session, filename="terms.txt", content=b"Zeferan\n", category="pytest_logref")
    monkeypatch.setattr(exporter, "build_orchestrator", lambda *a, **k: _NoDetections())
    monkeypatch.setattr(exporter, "audit_masked_text", lambda *a, **k: asyncio.sleep(0, result=exporter.AuditVerdict(risky=False)))

    report = _export(db_session, tmp_path, {"zeferan/app.py": "x = 1\n"}, "zeferan-kaynak")

    text = report.summary_text()
    assert "zeferan" not in text.casefold()
    assert "Kaynak: " in text and "<gizlendi>" not in text
    label = report.file_label("zeferan/app.py")
    assert "zeferan" not in label.casefold() and label.endswith("/app.py#" + label.rsplit("#", 1)[1])
    ref = label.rsplit("#", 1)[1]
    assert find_file_by_ref(db_session, report.run_id, ref) == ["zeferan/app.py"]
    assert find_file_by_ref(db_session, report.run_id, "#" + ref.upper()) == ["zeferan/app.py"]
    assert find_file_by_ref(db_session, report.run_id, "0" * FILE_REF_CHARS) == []


def test_detection_failure_keeps_source_path_out_of_logs_and_report(db_session, tmp_path, monkeypatch, caplog):
    commit_term_upload(db_session, filename="terms.txt", content=b"Zeferan\n", category="pytest_logref")
    monkeypatch.setattr(exporter, "build_orchestrator", lambda *a, **k: _Crash())

    with caplog.at_level(logging.INFO):
        report = _export(db_session, tmp_path, {"zeferan/app.py": "x = 1\n"}, "crash-kaynak")

    assert "zeferan" not in caplog.text.casefold()
    assert "zeferan" not in report.summary_text().casefold()
