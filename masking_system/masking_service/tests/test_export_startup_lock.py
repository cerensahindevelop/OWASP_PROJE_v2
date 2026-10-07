"""Startup must release SQLite writes before expensive detector construction."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, MaskingContext, MaskingRun
from app.core.exceptions import ExportInProgressError
from app.services import exporter
from app.services.detectors import DetectorOutput
from app.services.output_publication import recover_output


@pytest.fixture
def sessions(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'startup.db'}",
                           connect_args={"check_same_thread": False, "timeout": 2}, pool_size=12)
    @event.listens_for(engine, "connect")
    def configure(connection, _):
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False)
    engine.dispose()


class CleanDetector:
    async def scan(self, text, metadata=None):
        return DetectorOutput(results=[], errors=[])


def run_export(sessions, tmp_path, name):
    source = tmp_path / f"src-{name}"
    source.mkdir(exist_ok=True)
    (source / "plain.txt").write_text("hello world\n")
    with sessions() as db:
        return asyncio.run(exporter.export_project(
            db, source_path=str(source), target_path=str(tmp_path / f"out-{name}"),
            project_name=name, sicil_no="test", branch_name="main", initiated_by="test"))


def test_ten_jobs_can_construct_detectors_without_holding_write_lock(sessions, tmp_path, monkeypatch):
    barrier = threading.Barrier(10, timeout=15)
    def build(*args, **kwargs):
        # All ten exports must reach this point BEFORE any detector finishes.
        barrier.wait()
        return CleanDetector()
    monkeypatch.setattr(exporter, "build_orchestrator", build)
    with ThreadPoolExecutor(max_workers=10) as pool:
        reports = list(pool.map(lambda i: run_export(sessions, tmp_path, str(i)), range(10)))
    assert len({r.run_id for r in reports}) == 10
    assert all(r.status == "completed" for r in reports)
    assert all((tmp_path / f"out-{i}" / "plain.txt").read_text() == "hello world\n" for i in range(10))
    assert not list(tmp_path.glob(".out-*.masking.lock"))


def test_failed_setup_preserves_old_output_and_allows_retry(sessions, tmp_path, monkeypatch):
    target = tmp_path / "out-fail"
    target.mkdir()
    (target / "old.txt").write_text("previous output")
    def fail(*args, **kwargs):
        journal = json.loads((tmp_path / ".out-fail.masking.lock").read_text())
        with sessions() as observer:
            assert observer.get(MaskingRun, journal["run_id"]).status == "in_progress"
            # A separate writer can commit while detector construction runs.
            observer.add(MaskingContext(project_name="other", sicil_no="test", branch_name="main"))
            observer.commit()
        raise RuntimeError("detector startup failed")
    monkeypatch.setattr(exporter, "build_orchestrator", fail)
    with pytest.raises(RuntimeError, match="detector startup failed"):
        run_export(sessions, tmp_path, "fail")
    with sessions() as db:
        assert db.scalar(select(MaskingRun)).status == "failed"
    assert (target / "old.txt").read_text() == "previous output"
    assert not list(tmp_path.glob(".out-fail.masking*"))
    monkeypatch.setattr(exporter, "build_orchestrator", lambda *a, **kw: CleanDetector())
    assert run_export(sessions, tmp_path, "fail").status == "completed"


def test_same_identity_still_rejects_duplicate_during_unlocked_setup(sessions, tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def build(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return CleanDetector()
    monkeypatch.setattr(exporter, "build_orchestrator", build)
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(run_export, sessions, tmp_path, "same")
        try:
            assert entered.wait(5)
            with pytest.raises(ExportInProgressError):
                run_export(sessions, tmp_path, "same")
        finally:
            release.set()
        assert running.result(timeout=5).status == "completed"
    with sessions() as db:
        assert len(db.scalars(select(MaskingRun)).all()) == 1


def test_failed_status_write_keeps_recovery_journal(sessions, tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("setup failed")
    monkeypatch.setattr(exporter, "build_orchestrator", fail)
    monkeypatch.setattr(exporter, "_mark_run_failed", lambda *a: False)
    with pytest.raises(RuntimeError, match="setup failed"):
        run_export(sessions, tmp_path, "recover")
    assert (tmp_path / ".out-recover.masking.lock").exists()
    with sessions() as db:
        recover_output(db, tmp_path / "out-recover", application_stopped=True)
        db.commit()
        assert db.scalar(select(MaskingRun)).status == "failed"
    assert not (tmp_path / ".out-recover.masking.lock").exists()
