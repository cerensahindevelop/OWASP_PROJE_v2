"""Calisan export'u durdurma ve sureci kapanmis export'un otomatik kurtarilmasi.

- Durdurulan export hicbir sey yayimlamaz: onceki cikti korunur, run 'failed'
  olur, kilit kalkar ve ayni proje/branch hemen yeniden taranabilir.
- Yazicisi yasamayan (kilidi alinabilen) 'in_progress' run'i bir sonraki
  export kendiliginden kurtarir; kilidi tutulan run ise korunur.
"""
import asyncio
import threading

import pytest
from sqlalchemy import select

from app.core.exceptions import ExportCancelledError, ExportInProgressError
from app.db.models import AuditLog, MaskingRun
from app.services import exporter
from app.services.mapping_service import get_or_create_context
from app.services.output_publication import OutputPublication

IDENTITY = dict(project_name="durdurma", sicil_no="P-STOP", branch_name="main", initiated_by="test")


def _project(tmp_path, files=3):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    for index in range(files):
        (source / f"dosya{index}.txt").write_text(f"icerik {index}\n", encoding="utf-8")
    target.mkdir()
    (target / "onceki.txt").write_text("onceki cikti", encoding="utf-8")
    return source, target


def _export(db, source, target, **kwargs):
    return asyncio.run(exporter.export_project(
        db, source_path=str(source), target_path=str(target), **IDENTITY, **kwargs))


def _in_progress_run(db, target):
    context = get_or_create_context(db, IDENTITY["project_name"], IDENTITY["sicil_no"], IDENTITY["branch_name"])
    run = MaskingRun(context_id=context.id, mapping_version=2, operation_type="mask", source_path="kaynak",
                     target_path=str(target.resolve()), initiated_by="test", status="in_progress")
    db.add(run)
    db.flush()
    return run


def test_cancelled_export_publishes_nothing_and_can_restart(db_session, tmp_path):
    source, target = _project(tmp_path)
    cancel = threading.Event()

    def stop_after_first_file(processed, total, current):
        cancel.set()

    with pytest.raises(ExportCancelledError):
        _export(db_session, source, target, progress_callback=stop_after_first_file, cancel_event=cancel)

    assert sorted(p.name for p in target.iterdir()) == ["onceki.txt"]
    assert not list(tmp_path.glob(".target.masking*"))
    run = db_session.scalars(select(MaskingRun).order_by(MaskingRun.id.desc())).first()
    assert run.status == "failed"
    details = db_session.scalars(select(AuditLog.detail).where(AuditLog.run_id == run.id)).all()
    assert any("durduruldu" in detail for detail in details)

    report = _export(db_session, source, target)
    assert report.status in ("completed", "completed_with_warnings")
    assert (target / "dosya0.txt").exists()


def test_abandoned_run_is_recovered_by_next_export(db_session, tmp_path):
    source, target = _project(tmp_path)
    run = _in_progress_run(db_session, target)
    publication = OutputPublication(target, run.id)
    (publication.stage / "yarim.txt").write_text("yarim", encoding="utf-8")
    target.rename(publication.backup)  # cokme: eski hedef yedege tasinmisti
    publication.release_lock()  # surec kapandi

    report = _export(db_session, source, target)

    db_session.refresh(run)
    assert run.status == "failed"
    assert report.status in ("completed", "completed_with_warnings")
    assert (target / "dosya0.txt").exists()
    assert not (target / "yarim.txt").exists()
    assert not list(tmp_path.glob(".target.masking*"))


def test_run_with_live_writer_still_blocks(db_session, tmp_path):
    source, target = _project(tmp_path)
    run = _in_progress_run(db_session, target)
    publication = OutputPublication(target, run.id)
    try:
        with pytest.raises(ExportInProgressError):
            _export(db_session, source, target)
        db_session.refresh(run)
        assert run.status == "in_progress"
        assert (target / "onceki.txt").exists()
    finally:
        publication.close()


def test_in_progress_run_without_journal_is_closed(db_session, tmp_path):
    source, target = _project(tmp_path)
    run = _in_progress_run(db_session, target)

    _export(db_session, source, target)

    db_session.refresh(run)
    assert run.status == "failed"


def test_cancel_interrupts_a_long_wait():
    cancel = threading.Event()
    finished = []

    async def slow_llm_call():
        await asyncio.sleep(30)
        finished.append(True)

    async def scenario():
        asyncio.get_running_loop().call_later(0.1, cancel.set)
        await exporter._await_unless_cancelled(slow_llm_call(), cancel)

    with pytest.raises(ExportCancelledError):
        asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert finished == []


def test_abandoned_journal_from_another_operation_does_not_block_target(db_session, tmp_path):
    source, target = _project(tmp_path)
    other = get_or_create_context(db_session, "baska", "P-OTHER", "main")
    run = MaskingRun(context_id=other.id, operation_type="unmask", source_path="kaynak",
                     target_path=str(target.resolve()), initiated_by="test", status="in_progress")
    db_session.add(run)
    db_session.flush()
    OutputPublication(target, run.id).release_lock()  # cokmus geri donusum

    report = _export(db_session, source, target)

    assert report.status in ("completed", "completed_with_warnings")
    db_session.refresh(run)
    assert run.status == "failed"
