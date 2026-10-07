"""Background export jobs with progress.

Buyuk bir projede LLM acikken export saatler surebilir. Tek bir bloklayan
HTTP istegi arayuzde zaman asimina ugruyor (backend arkada devam etse de
kullanici sonucu goremiyordu) ve ilerleme gosterilemiyordu. Burada export
ayri bir thread'de, kendi DB oturumuyla calisir; arayuz GET ile durumu ve
"islenen/toplam dosya" ilerlemesini sorgular.

Kayitlar surec belleginde tutulur (tek uvicorn sureci - README). Surec
yeniden baslarsa calisan is kaybolur; export'un kendisi bu durumda
recover-output ile kapatilir (bkz. output_publication.py).
"""

from __future__ import annotations

import threading
import queue
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from fastapi import HTTPException
from app.core.config import settings

logger = logging.getLogger(__name__)

_MAX_FINISHED_JOBS = 50


@dataclass
class ExportJob:
    job_id: str
    status: str = "queued"  # queued | running | completed | failed
    processed: int = 0
    total: int = 0
    current_file: str | None = None
    result: dict[str, Any] | None = None
    error_message: str | None = None
    error_detail: str | None = None
    error_status: int | None = None
    finished_at: float | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "job_id": self.job_id,
                "status": self.status,
                "processed": self.processed,
                "total": self.total,
                "current_file": self.current_file,
                "result": self.result,
                "error_message": self.error_message,
                "error_detail": self.error_detail,
                "error_status": self.error_status,
            }


class ExportJobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, ExportJob] = {}
        self._lock = threading.Lock()

    def create(self) -> ExportJob:
        job = ExportJob(job_id=uuid.uuid4().hex)
        with self._lock:
            self._prune()
            self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> ExportJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def discard(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)

    def _prune(self) -> None:
        finished = sorted(
            (job for job in self._jobs.values() if job.finished_at is not None),
            key=lambda job: job.finished_at,
        )
        for job in finished[: max(0, len(finished) - _MAX_FINISHED_JOBS)]:
            self._jobs.pop(job.job_id, None)


registry = ExportJobRegistry()


class ExportJobExecutor:
    """FIFO admission with bounded total work and a fixed daemon worker pool.

    Process-local, like the job registry. This does not enable multi-worker
    deployment or restart persistence.
    """
    def __init__(self, max_running: int, max_queued: int):
        if max_running < 1 or max_queued < 0:
            raise ValueError("Invalid export queue limits")
        self._capacity = threading.BoundedSemaphore(max_running + max_queued)
        self._queue = queue.Queue()
        self._max_running = max_running
        self._threads = []
        self._lock = threading.Lock()

    def submit(self, work: Callable[[], None]) -> None:
        if not self._capacity.acquire(blocking=False):
            raise HTTPException(status_code=429, detail="İşlem kuyruğu dolu. Lütfen daha sonra tekrar deneyin.",
                                headers={"Retry-After": "30"})
        try:
            with self._lock:
                while len(self._threads) < self._max_running:
                    thread = threading.Thread(target=self._worker, name="export-worker", daemon=True)
                    thread.start()
                    self._threads.append(thread)
            self._queue.put_nowait(work)
        except BaseException:
            self._capacity.release()
            raise

    def _worker(self) -> None:
        while True:
            work = self._queue.get()
            try:
                work()
            except BaseException:
                # A callback must never kill a worker and strand the queue.
                logger.error("Export worker callback failed", exc_info=False)
            finally:
                self._capacity.release()
                self._queue.task_done()


executor = ExportJobExecutor(settings.export_jobs.max_running, settings.export_jobs.max_queued)


def start_job(work: Callable[[Callable[[int, int, str], None]], dict[str, Any]],
              on_error: Callable[[Exception], tuple[str, str | None, int]],
              cleanup: Callable[[], None] | None = None) -> ExportJob:
    """Queue work; reject excess admission before retaining uploads or job IDs."""
    job = registry.create()

    def progress(processed: int, total: int, current: str) -> None:
        with job.lock:
            job.processed, job.total, job.current_file = processed, total, current

    def runner() -> None:
        with job.lock:
            job.status = "running"
        try:
            result = work(progress)
            with job.lock:
                job.result, job.status = result, "completed"
        except BaseException as exc:  # worker cancellation must also terminate the job
            try:
                message, detail, status = on_error(exc) if isinstance(exc, Exception) else (
                    "Tarama kesildi.", None, 500)
            except Exception:
                message, detail, status = "Tarama tamamlanamadı.", None, 500
            with job.lock:
                job.status = "failed"
                job.error_message, job.error_detail, job.error_status = message, detail, status
        finally:
            if cleanup is not None:
                try:
                    cleanup()
                except Exception:  # noqa: BLE001 - cleanup must not hide the result
                    pass
            with job.lock:
                job.finished_at = time.monotonic()

    try:
        executor.submit(runner)
    except BaseException:
        registry.discard(job.job_id)
        if cleanup is not None:
            try:
                cleanup()
            except Exception:
                logger.error("Rejected export upload cleanup failed", exc_info=False)
        raise
    return job
