"""Pasif (supheli) sozluk terimi maskelenmez; disa aktarma raporu yalnizca not dusurur."""

from __future__ import annotations

import asyncio

from app.db.models import FilterRule
from app.services.exporter import export_project
from app.services.term_upload import commit_term_upload, rule_name_for_term


def test_passive_term_is_reported_without_value_and_never_blocks(db_session, tmp_path):
    commit_term_upload(db_session, filename="t.txt", content=b"config\n", category="pytest_pasif_not")
    rule = db_session.query(FilterRule).filter_by(rule_name=rule_name_for_term("pytest_pasif_not", "config")).one()
    assert rule.is_active is False

    source, target = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    (source / "a.py").write_text("config = 1\nprint(config)\n", encoding="utf-8")
    (source / "b.txt").write_text("temiz icerik\n", encoding="utf-8")

    report = asyncio.run(export_project(
        db_session, source_path=str(source), target_path=str(target), project_name="pytest-pasif",
        sicil_no="P-PASIF", branch_name="test", initiated_by="P-PASIF",
    ))

    assert (target / "a.py").read_text(encoding="utf-8") == "config = 1\nprint(config)\n"
    assert report.files_quarantined_pending_audit == 0
    assert list(report.passive_term_files.values()) == [2]
    summary = report.summary_text()
    assert "Pasif sozluk terimi" in summary
    note = next(line for line in summary.splitlines() if "Pasif sozluk terimi" in line)
    assert "config" not in note and "pytest_pasif_not" not in note
