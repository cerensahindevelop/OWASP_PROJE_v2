import threading
import asyncio
import io

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from app.api import export_jobs


def error(exc):
    return "failed", None, 500


@pytest.fixture
def queue(monkeypatch):
    executor = export_jobs.ExportJobExecutor(1, 2)
    monkeypatch.setattr(export_jobs, "executor", executor)
    monkeypatch.setattr(export_jobs, "registry", export_jobs.ExportJobRegistry())
    return executor


def test_fifo_capacity_queued_status_and_rejected_upload_cleanup(queue):
    entered, release = threading.Event(), threading.Event()
    order, cleaned = [], []
    def first(progress):
        entered.set()
        assert release.wait(5)
        order.append(1)
        return {}
    active = export_jobs.start_job(first, error)
    try:
        assert entered.wait(5)
        second = export_jobs.start_job(lambda p: order.append(2) or {}, error)
        third = export_jobs.start_job(lambda p: order.append(3) or {}, error)
        assert active.snapshot()["status"] == "running"
        assert second.snapshot()["status"] == third.snapshot()["status"] == "queued"
        with pytest.raises(HTTPException) as exc:
            export_jobs.start_job(lambda p: {}, error, cleanup=lambda: cleaned.append("rejected"))
        assert exc.value.status_code == 429
        assert exc.value.headers["Retry-After"] == "30"
        assert cleaned == ["rejected"]
        assert len(export_jobs.registry._jobs) == 3
    finally:
        release.set()
        queue._queue.join()
    assert order == [1, 2, 3]
    assert all(j.snapshot()["status"] == "completed" for j in (active, second, third))


@pytest.mark.parametrize("bad_handler", [False, True])
def test_failed_job_releases_capacity_and_worker_survives(queue, bad_handler):
    cleaned = []
    def fail(progress):
        raise RuntimeError("work failed")
    def handler(exc):
        if bad_handler:
            raise RuntimeError("handler failed")
        return error(exc)
    failed = export_jobs.start_job(fail, handler, cleanup=lambda: cleaned.append(1))
    queue._queue.join()
    assert failed.snapshot()["status"] == "failed"
    assert cleaned == [1]
    succeeded = export_jobs.start_job(lambda p: {"ok": True}, error)
    queue._queue.join()
    assert succeeded.snapshot()["result"] == {"ok": True}


def test_running_limit_across_multiple_workers(monkeypatch):
    executor = export_jobs.ExportJobExecutor(2, 1)
    monkeypatch.setattr(export_jobs, "executor", executor)
    entered = threading.Barrier(3, timeout=5)
    release = threading.Event()
    def block(progress):
        entered.wait()
        assert release.wait(5)
        return {}
    first = export_jobs.start_job(block, error)
    second = export_jobs.start_job(block, error)
    try:
        entered.wait()
        third = export_jobs.start_job(lambda p: {}, error)
        assert third.snapshot()["status"] == "queued"
        assert first.snapshot()["status"] == second.snapshot()["status"] == "running"
    finally:
        release.set()
        executor._queue.join()
    assert third.snapshot()["status"] == "completed"


def test_rejected_upload_removes_source_and_empty_output(tmp_path, monkeypatch):
    from app.api.routers import export as routes
    source, target = tmp_path / "source", tmp_path / "output"
    source.mkdir()
    target.mkdir()
    (source / "sample.txt").write_text("uploaded data")
    monkeypatch.setattr(routes, "save_uploaded_files_to_temp_dir", lambda *a, **kw: source)
    monkeypatch.setattr(routes, "uploaded_output_dir", lambda _: target)
    class Full:
        def submit(self, work):
            raise HTTPException(429, "full")
    monkeypatch.setattr(export_jobs, "executor", Full())
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.start_export_job_upload(
            project_name="test", sicil_no="test", branch_name="main", initiated_by="test",
            is_directory_upload=False, files=[UploadFile(io.BytesIO(b"data"), filename="sample.txt")]))
    assert exc.value.status_code == 429
    assert not source.exists()
    assert not target.exists()
