"""Kural 9 olcumu (Faz 2a): icerikte maskelenen terim yolda acik mi?

Yalnizca raporlanir: dosya engellenmez, failed_check/blocked_by_check
degismez; AuditLog'a deger degil sayi, rapora yol degil dosya kimligi yazilir.
"""

from __future__ import annotations

import asyncio

import pytest

from app.db.models import AuditLog
from app.services import exporter
from app.services.audit_reviewer import AuditVerdict
from app.services.detectors import DetectionResult, DetectorOutput
from app.services.exporter import FileOutcome, export_project
from app.services.failed_checks import FailedCheck


class _FindValue:
    """LLM'in yuksek guvenle bir degeri buldugu durumu canlandirir."""

    def __init__(self, value: str) -> None:
        self.value = value

    async def scan(self, text, metadata=None):
        start = text.find(self.value)
        if start < 0:
            return DetectorOutput(results=[])
        return DetectorOutput(results=[DetectionResult(
            deger=self.value, tip="PROJE_KOD_ADI", guven_seviyesi="yuksek", kaynak_motor="llm",
            start=start, end=start + len(self.value),
        )])


async def _clean_audit(*args, **kwargs):
    return AuditVerdict(risky=False)


def _export(db_session, tmp_path, monkeypatch, value, files):
    monkeypatch.setattr(exporter, "build_orchestrator", lambda *a, **k: _FindValue(value))
    monkeypatch.setattr(exporter, "audit_masked_text", _clean_audit)
    source = tmp_path / "kaynak"
    for rel, content in files.items():
        (source / rel).parent.mkdir(parents=True, exist_ok=True)
        (source / rel).write_text(content, encoding="utf-8")
    return asyncio.run(export_project(
        db_session, source_path=str(source), target_path=str(tmp_path / "cikti"),
        project_name="pytest-yol-icerik", sicil_no="P-YOL", branch_name="main", initiated_by="P-YOL",
    ))


def test_llm_term_open_in_path_is_measured_but_not_blocked(db_session, tmp_path, monkeypatch):
    report = _export(db_session, tmp_path, monkeypatch, "Zefiron",
                     {"zefiron/notlar.md": "Proje kod adi: Zefiron\n", "diger/notlar.md": "Proje kod adi: Zefiron\n"})

    outcome = next(o for o in report.outcomes if o.relative_path == "zefiron/notlar.md")
    assert outcome.final_state == "READY" and outcome.failed_check is None
    assert report.blocked_by_check == {}
    assert list(report.path_content_mismatch.values()) == [1]
    assert report.path_content_mismatch == {report.file_label("zefiron/notlar.md"): 1}
    text = report.summary_text()
    assert "Yol/icerik uyusmazligi" in text and "1 dosya, 1 terim" in text
    ref = report.file_label("zefiron/notlar.md").rsplit("#", 1)[1]
    assert f"#{ref}" in text
    details = [row.detail for row in db_session.query(AuditLog).filter_by(run_id=report.run_id)
               if (row.detail or "").startswith("path_content_check")]
    assert details == ["path_content_check check=yol_icerik_uyusmazligi terms=1"]


def test_term_is_matched_only_on_part_boundaries(db_session, tmp_path, monkeypatch):
    report = _export(db_session, tmp_path, monkeypatch, "Kora", {"korali_yol/notlar.md": "Sahip: Kora\n"})

    assert report.path_content_mismatch == {}
    assert "Yol/icerik uyusmazligi" not in report.summary_text()


def test_report_only_code_can_never_block_a_file():
    with pytest.raises(ValueError):
        FileOutcome("a.py", status="quarantined_pending_audit", failed_check=FailedCheck.YOL_ICERIK_UYUSMAZLIGI)


def test_failed_check_summary_counts_mismatch_separately(db_session, tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path

    report = _export(db_session, tmp_path, monkeypatch, "Zefiron", {"zefiron/notlar.md": "Kod adi: Zefiron\n"})
    db_session.flush()
    script = Path(__file__).resolve().parents[1] / "scripts" / "failed_check_summary.py"
    spec = importlib.util.spec_from_file_location("failed_check_summary", script)
    summary = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(summary)
    # Testin geri alinan transaction'i icinde, ayni sqlite3 baglantisiyla okunur.
    result = summary.summarize(db_session.connection().connection.driver_connection, report.run_id)
    assert result["yol_icerik_uyusmazligi"] == {"dosya": 1, "terim": 1}
    assert result["nedene_gore"] == {}
