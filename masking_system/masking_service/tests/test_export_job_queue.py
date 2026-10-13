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
    def first(progress, cancel):
        entered.set()
        assert release.wait(5)
        order.append(1)
        return {}
    active = export_jobs.start_job(first, error)
    try:
        assert entered.wait(5)
        second = export_jobs.start_job(lambda p, c: order.append(2) or {}, error)
        third = export_jobs.start_job(lambda p, c: order.append(3) or {}, error)
        assert active.snapshot()["status"] == "running"
        assert second.snapshot()["status"] == third.snapshot()["status"] == "queued"
        with pytest.raises(HTTPException) as exc:
            export_jobs.start_job(lambda p, c: {}, error, cleanup=lambda: cleaned.append("rejected"))
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
    def fail(progress, cancel):
        raise RuntimeError("work failed")
    def handler(exc):
        if bad_handler:
            raise RuntimeError("handler failed")
        return error(exc)
    failed = export_jobs.start_job(fail, handler, cleanup=lambda: cleaned.append(1))
    queue._queue.join()
    assert failed.snapshot()["status"] == "failed"
    assert cleaned == [1]
    succeeded = export_jobs.start_job(lambda p, c: {"ok": True}, error)
    queue._queue.join()
    assert succeeded.snapshot()["result"] == {"ok": True}


def test_running_limit_across_multiple_workers(monkeypatch):
    executor = export_jobs.ExportJobExecutor(2, 1)
    monkeypatch.setattr(export_jobs, "executor", executor)
    entered = threading.Barrier(3, timeout=5)
    release = threading.Event()
    def block(progress, cancel):
        entered.wait()
        assert release.wait(5)
        return {}
    first = export_jobs.start_job(block, error)
    second = export_jobs.start_job(block, error)
    try:
        entered.wait()
        third = export_jobs.start_job(lambda p, c: {}, error)
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


def test_queued_job_cancelled_before_it_starts(queue):
    entered, release = threading.Event(), threading.Event()
    ran, cleaned = [], []
    def first(progress, cancel):
        entered.set()
        assert release.wait(5)
        return {}
    active = export_jobs.start_job(first, error)
    try:
        assert entered.wait(5)
        waiting = export_jobs.start_job(lambda p, c: ran.append(1) or {}, error, cleanup=lambda: cleaned.append(1))
        export_jobs.registry.cancel(waiting.job_id)
        assert waiting.snapshot()["status"] == "cancelled"
    finally:
        release.set()
        queue._queue.join()
    assert ran == []
    assert cleaned == [1]
    assert active.snapshot()["status"] == "completed"


def test_running_job_stops_at_cancellation_point(queue):
    from app.core.exceptions import ExportCancelledError
    entered = threading.Event()
    def work(progress, cancel):
        entered.set()
        assert cancel.wait(5)
        raise ExportCancelledError("durduruldu")
    job = export_jobs.start_job(work, error)
    assert entered.wait(5)
    export_jobs.registry.cancel(job.job_id)
    assert job.snapshot()["cancel_requested"] is True
    queue._queue.join()
    snapshot = job.snapshot()
    assert snapshot["status"] == "cancelled"
    assert "durduruldu" in snapshot["error_message"]


def test_find_active_matches_only_running_or_queued_same_identity(queue):
    release = threading.Event()
    identity = ("proje", "S1", "main")
    job = export_jobs.start_job(lambda p, c: release.wait(5) and {}, error, identity=identity)
    try:
        assert export_jobs.registry.find_active(identity) is job
        assert export_jobs.registry.find_active(("proje", "S1", "dev")) is None
    finally:
        release.set()
        queue._queue.join()
    assert export_jobs.registry.find_active(identity) is None


def test_api_rejects_second_job_with_active_id_and_cancels(api_client, queue, monkeypatch):
    from app.api.routers import export as routes
    monkeypatch.setattr(routes, "ensure_path_allowed", lambda *a, **kw: None)
    release = threading.Event()
    monkeypatch.setattr(routes, "_export_job_work",
                        lambda kwargs, token: (lambda p, c: c.wait(5) and {}))
    payload = dict(source_path="/kaynak", target_path="/hedef", project_name="proje", sicil_no="S1",
                   branch_name="main", initiated_by="S1")
    first = api_client.post("/export/jobs", json=payload)
    assert first.status_code == 202
    job_id = first.json()["job_id"]

    second = api_client.post("/export/jobs", json=payload)
    assert second.status_code == 409
    assert second.json()["active_job_id"] == job_id

    cancelled = api_client.post(f"/export/jobs/{job_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["cancel_requested"] is True
    release.set()
    queue._queue.join()
    assert api_client.post("/export/jobs/yok/cancel").status_code == 404
