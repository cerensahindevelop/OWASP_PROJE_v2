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
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

_MAX_FINISHED_JOBS = 50


@dataclass
class ExportJob:
    job_id: str
    status: str = "running"  # running | completed | failed
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

    def _prune(self) -> None:
        finished = sorted(
            (job for job in self._jobs.values() if job.finished_at is not None),
            key=lambda job: job.finished_at,
        )
        for job in finished[: max(0, len(finished) - _MAX_FINISHED_JOBS)]:
            self._jobs.pop(job.job_id, None)


registry = ExportJobRegistry()


def start_job(work: Callable[[Callable[[int, int, str], None]], dict[str, Any]],
              on_error: Callable[[Exception], tuple[str, str | None, int]],
              cleanup: Callable[[], None] | None = None) -> ExportJob:
    """Run `work(progress)` in a daemon thread; `work` returns the JSON result."""
    job = registry.create()

    def progress(processed: int, total: int, current: str) -> None:
        with job.lock:
            job.processed, job.total, job.current_file = processed, total, current

    def runner() -> None:
        try:
            result = work(progress)
            with job.lock:
                job.result, job.status = result, "completed"
        except Exception as exc:  # noqa: BLE001 - reported to the client
            message, detail, status = on_error(exc)
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

    threading.Thread(target=runner, name=f"export-job-{job.job_id[:8]}", daemon=True).start()
    return job
