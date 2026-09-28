"""Shared detection/audit admission and content-free timing records.

The gate is shared by jobs on one event loop, not by independent workers.
The HTTP timeout starts AFTER admission. Only the failed request itself is
retried, and only for transient transport failures (timeout, connection,
HTTP 429/5xx) - never for parse/schema/truncation errors. Completed chunks are
never resubmitted; when retries are exhausted the error still reaches the
existing quarantine path.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import httpx
from contextvars import ContextVar
import logging
from time import monotonic
from uuid import uuid4

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


def _gate(settings):
    loop = asyncio.get_running_loop()
    gates = getattr(loop, "_masking_llm_gates", None)
    if gates is None:
        gates = {}
        loop._masking_llm_gates = gates
    key = settings.host.rstrip("/")
    limit = max(1, getattr(settings, "max_concurrent_requests", 1))
    if key not in gates:
        gates[key] = (limit, asyncio.Semaphore(limit))
    configured, semaphore = gates[key]
    if configured != limit:
        # Runtime configuration is immutable: changing it requires a restart.
        raise ValueError("Ayni endpoint icin eszamanlilik ayarlari tutarsiz; servisi yeniden baslatin")
    return semaphore


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
        logger.info(
            "llm_file scan_id=%s file=%r phase=%s chunks=%d completed=%d "
            "requests=%d elapsed_seconds=%.3f queue_seconds=%.3f status=%s "
            "error_type=%s prompt_tokens=%d completion_tokens=%d usage_responses=%d",
            self.scan_id, self.file_path, self.phase, self.chunk_count, self.completed,
            self.requests, monotonic() - self.started, self.queue_seconds,
            "ok" if kind is None else "error", kind.__name__ if kind else "none",
            self.prompt_tokens, self.completion_tokens, self.usage_responses,
        )

    async def request(self, settings, payload, caller, parser, chunk_index):
        queued = monotonic()
        async with _gate(settings):
            queue_seconds = monotonic() - queued
            self.queue_seconds += queue_seconds
            started = monotonic()
            self.requests += 1
            error = "none"
            status = "ok"
            usage = {}
            finish = None
            stage = "http"
            try:
                retries = max(0, int(getattr(settings, "transient_retries", 0) or 0))
                attempt = 0
                while True:
                    try:
                        raw = await caller(settings.host, settings.timeout_seconds, payload,
                                           getattr(settings, "api_key", None))
                        break
                    except Exception as exc:
                        if attempt >= retries or not is_transient_llm_error(exc):
                            raise
                        attempt += 1
                        self.requests += 1
                        logger.info(
                            "llm_retry scan_id=%s file=%r phase=%s chunk=%d attempt=%d error_type=%s",
                            self.scan_id, self.file_path, self.phase, chunk_index, attempt,
                            type(exc.__cause__ or exc).__name__,
                        )
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
                # Include parsing in the result status; HTTP 200 != completed scan.
                stage = "parse"
                result = parser(raw)
                self.completed += 1
                return result
            except BaseException as exc:
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
                logger.info(
                    "llm_request scan_id=%s file=%r phase=%s chunk=%d chunks=%d "
                    "request=%d queue_seconds=%.3f elapsed_seconds=%.3f status=%s "
                    "error_type=%s error_stage=%s finish_reason=%r prompt_tokens=%s completion_tokens=%s",
                    self.scan_id, self.file_path, self.phase, chunk_index, self.chunk_count,
                    self.requests, queue_seconds, monotonic() - started, status, error,
                    stage if status != "ok" else "none", finish, input_tokens, output_tokens,
                )
