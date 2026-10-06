"""Shared detection/audit admission and content-free timing records.

Admission is shared across loops, threads and workers using OS file locks.
The HTTP timeout starts AFTER admission. Only the failed request itself is
retried, and only for transient transport failures (timeout, connection,
HTTP 429/5xx) - never for parse/schema/truncation errors. Completed chunks are
never resubmitted; when retries are exhausted the error still reaches the
existing quarantine path.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
import httpx
from contextvars import ContextVar
import logging
import math
import threading
from time import monotonic
from uuid import uuid4

from app.services.log_refs import current_file_label
from app.services.llm_admission import admission_pool

# Inherits Uvicorn's configured INFO handler; CLI can configure this logger too.
logger = logging.getLogger("uvicorn.error.llm")
_file_path = ContextVar("llm_file_path", default="<unknown>")


@contextmanager
def llm_file_context(file_path: str):
    token = _file_path.set(file_path)
    try:
        yield
    finally:
        _file_path.reset(token)


# llm_file_context ile isaretlenmis, su an taranan dosyanin yolu (yoksa None).
def current_llm_file() -> str | None:
    path = _file_path.get()
    return None if path == "<unknown>" else path


# Bir dosyanin bir export boyunca harcadigi LLM kaynagi (tespit + denetim +
# otomatik duzeltme yeniden denetimleri). Yalnizca sayi/sure; icerik yok.
# llm_seconds: dosyanin tarama (scan) duvar saati surelerinin toplami - parca
# istekleri eszamanli calistigi icin istek surelerinin toplami degildir.
@dataclass
class LLMFileUsage:
    detection_scans: int = 0
    audit_scans: int = 0
    failed_scans: int = 0
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_seconds: float = 0.0
    queue_seconds: float = 0.0


# Bir export'un LLMScanMetrics kayitlarini dosya bazinda toplar. Contextvar
# ile tasinir: export_project kurar, Faz A/C/E'nin asyncio gorevleri
# (ensure_future/gather) olusturulduklari baglami kopyaladigi icin AYNI
# nesneye yazar. LLM cagrisi thread/executor icinde yapilmaz; yine de kayit
# bir kilitle korunur. Kurulu degilse (orn. serbest birakma denetimi) kayit
# atlanir.
class LLMUsageCollector:
    def __init__(self) -> None:
        self.by_file: dict[str, LLMFileUsage] = {}
        self._lock = threading.Lock()

    def record(self, metrics: "LLMScanMetrics", elapsed: float, ok: bool) -> None:
        with self._lock:
            usage = self.by_file.setdefault(metrics.file_path or "<unknown>", LLMFileUsage())
            if metrics.phase == "detection":
                usage.detection_scans += 1
            else:
                usage.audit_scans += 1
            usage.failed_scans += 0 if ok else 1
            usage.requests += metrics.requests
            usage.prompt_tokens += metrics.prompt_tokens
            usage.completion_tokens += metrics.completion_tokens
            usage.llm_seconds += elapsed
            usage.queue_seconds += metrics.queue_seconds


_usage_collector: ContextVar[LLMUsageCollector | None] = ContextVar("llm_usage_collector", default=None)


# Bu baglamda (ve buradan sonra olusturulan asyncio gorevlerinde) LLM
# kullanimini `collector`a yazdirir. Donen token stop_llm_usage'a verilir.
def start_llm_usage(collector: LLMUsageCollector):
    return _usage_collector.set(collector)


def stop_llm_usage(token) -> None:
    _usage_collector.reset(token)


# En yakin sira (nearest-rank) yuzdeligi; bos listede 0.
def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct / 100 * len(ordered)) - 1)]


# Dosya bazli kullanimdan rapor ozeti (yol icermez).
def usage_summary(by_file: dict[str, LLMFileUsage]) -> dict[str, float]:
    usages = list(by_file.values())
    if not usages:
        return {}
    seconds = [usage.llm_seconds for usage in usages]
    return {
        "files": len(usages),
        "requests": sum(usage.requests for usage in usages),
        "prompt_tokens": sum(usage.prompt_tokens for usage in usages),
        "completion_tokens": sum(usage.completion_tokens for usage in usages),
        "failed_scans": sum(usage.failed_scans for usage in usages),
        "scans_per_file": round(sum(u.detection_scans + u.audit_scans for u in usages) / len(usages), 2),
        "requests_per_file": round(sum(usage.requests for usage in usages) / len(usages), 2),
        "llm_seconds_p50": round(percentile(seconds, 50), 3),
        "llm_seconds_p95": round(percentile(seconds, 95), 3),
    }


def _gate(settings):
    key = settings.host.rstrip("/")
    limit = max(1, getattr(settings, "max_concurrent_requests", 1))
    return admission_pool("llm:" + key, limit).lease()


_RETRY_BASE_DELAY_SECONDS = 1.0


def is_transient_llm_error(exc: BaseException) -> bool:
    """Timeout/baglanti kopmasi/429/5xx: ayni istegi tekrar gondermek anlamli."""
    cause = exc.__cause__ or exc
    if isinstance(cause, (TimeoutError, asyncio.TimeoutError, httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(cause, httpx.HTTPStatusError):
        code = cause.response.status_code
        return code == 429 or 500 <= code < 600
    return False


class LLMScanMetrics:
    def __init__(self, phase, chunk_count, file_path=None):
        self.phase = phase
        self.chunk_count = chunk_count
        self.file_path = file_path or _file_path.get()
        # Loglara kaynak yol degil maskeli yol + kisa kimlik yazilir (kural 7).
        self.log_label = current_file_label()
        self.scan_id = uuid4().hex
        self.requests = 0
        self.completed = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.usage_responses = 0
        self.queue_seconds = 0.0

    def __enter__(self):
        self.started = monotonic()
        return self

    def __exit__(self, kind, exc, tb):
        elapsed = monotonic() - self.started
        collector = _usage_collector.get()
        if collector is not None:
            collector.record(self, elapsed, ok=kind is None)
        logger.info(
            "llm_file scan_id=%s file=%r phase=%s chunks=%d completed=%d "
            "requests=%d elapsed_seconds=%.3f queue_seconds=%.3f status=%s "
            "error_type=%s prompt_tokens=%d completion_tokens=%d usage_responses=%d",
            self.scan_id, self.log_label, self.phase, self.chunk_count, self.completed,
            self.requests, elapsed, self.queue_seconds,
            "ok" if kind is None else "error", kind.__name__ if kind else "none",
            self.prompt_tokens, self.completion_tokens, self.usage_responses,
        )

    async def request(self, settings, payload, caller, parser, chunk_index):
        started = monotonic()
        queued = started
        queue_seconds = 0.0
        error = "none"
        status = "ok"
        usage = {}
        finish = None
        stage = "admission"
        # Kapasite alinamadan (ayar hatasi, kuyrukta iptal) biten cagri model
        # istegi degildir: llm_request satiri yalnizca gonderilen istek icin yazilir.
        sent = False
        try:
            retries = max(0, int(getattr(settings, "transient_retries", 0) or 0))
            attempt = 0
            while True:
                try:
                    queued = monotonic()
                    stage = "admission"
                    async with _gate(settings):
                        waited = monotonic() - queued
                        queue_seconds += waited
                        self.queue_seconds += waited
                        stage = "http"
                        sent = True
                        self.requests += 1
                        raw = await caller(settings.host, settings.timeout_seconds, payload,
                                           getattr(settings, "api_key", None))
                    break
                except Exception as exc:
                    if attempt >= retries or not is_transient_llm_error(exc):
                        raise
                    attempt += 1
                    logger.info(
                        "llm_retry scan_id=%s file=%r phase=%s chunk=%d attempt=%d error_type=%s",
                        self.scan_id, self.log_label, self.phase, chunk_index, attempt,
                        type(exc.__cause__ or exc).__name__,
                    )
                    # Backoff owns no model slot: other users can make progress.
                    await asyncio.sleep(_RETRY_BASE_DELAY_SECONDS * attempt)
            if isinstance(raw, dict):
                usage = raw.get("usage") or {}
                if not isinstance(usage, dict):
                    usage = {}
                choices = raw.get("choices")
                if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                    finish = choices[0].get("finish_reason")
                    if finish not in (None, "stop", "length", "content_filter", "tool_calls"):
                        finish = "other"
            stage = "parse"
            result = parser(raw)
            self.completed += 1
            return result
        except BaseException as exc:
            if stage == "admission":
                waited = monotonic() - queued
                queue_seconds += waited
                self.queue_seconds += waited
            cause = exc.__cause__ or exc
            error = type(cause).__name__
            status = "timeout" if isinstance(cause, (TimeoutError,)) or "Timeout" in error else "error"
            if hasattr(cause, "response"):
                status += "_http_" + str(cause.response.status_code)
            raise
        finally:
            input_tokens = usage.get("prompt_tokens")
            output_tokens = usage.get("completion_tokens")
            input_tokens = input_tokens if isinstance(input_tokens, int) else None
            output_tokens = output_tokens if isinstance(output_tokens, int) else None
            if input_tokens is not None and output_tokens is not None:
                self.prompt_tokens += input_tokens
                self.completion_tokens += output_tokens
                self.usage_responses += 1
            if sent:
                logger.info(
                    "llm_request scan_id=%s file=%r phase=%s chunk=%d chunks=%d "
                    "request=%d queue_seconds=%.3f elapsed_seconds=%.3f status=%s "
                    "error_type=%s error_stage=%s finish_reason=%r prompt_tokens=%s completion_tokens=%s",
                    self.scan_id, self.log_label, self.phase, chunk_index, self.chunk_count,
                    self.requests, queue_seconds, max(0.0, monotonic() - started - queue_seconds), status, error,
                    stage if status != "ok" else "none", finish, input_tokens, output_tokens,
                )
