"""Uygulama koduna DOKUNMADAN olcum kancalari takilmis backend sureci.

Uygulamanin kendi venv'i ile, masking_service/ dizininden calisir:

    cd masking_service
    LOADTEST_EVENTS=/yol/events.jsonl .venv/bin/python ../loadtest/instrumented_server.py --port 8110

Ne yapar:
  * app.api.main:app'i aynen yukler (butun guvenlik/denetim asamalari acik).
  * Asama fonksiyonlarini calisma aninda sarar ve her cagrinin monotonic
    baslangic/bitis zamanini JSONL olay dosyasina yazar. Fonksiyonlarin
    davranisi, argumanlari ve donus degerleri degismez.
  * Her saniye surec ici sayaclari (aktif is, ucustaki LLM istegi, LLM
    kapasite kuyrugunda bekleyen istek, RSS) yazar.
  * Indirilebilir cikti kokunu LOADTEST_OUTPUT_ROOT'a yonlendirir; boylece
    test ciktilari gercek uploads_output/ klasorune karismaz.

Olay dosyasina dosya ICERIGI veya hassas deger yazilmaz: yalnizca goreli
yol, sure, sayac, token sayisi ve hata tipi.
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import functools
import json
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))

EVENTS_PATH = os.environ.get("LOADTEST_EVENTS")
OUTPUT_ROOT = os.environ.get("LOADTEST_OUTPUT_ROOT")
PID = os.getpid()

_lock = threading.Lock()
_fh = open(EVENTS_PATH, "a", buffering=1, encoding="utf-8") if EVENTS_PATH else None

JOB: contextvars.ContextVar[str | None] = contextvars.ContextVar("lt_job", default=None)
_REQ: contextvars.ContextVar[dict | None] = contextvars.ContextVar("lt_req", default=None)

_gauges = {"active_jobs": 0, "llm_inflight": 0, "llm_waiting": 0, "presidio_waiting": 0,
           "presidio_running": 0, "db_lock_waiting": 0}
_glock = threading.Lock()


def emit(ev: str, **fields) -> None:
    if _fh is None:
        return
    fields.setdefault("job", JOB.get())
    record = {"ev": ev, "pid": PID, "mono": time.monotonic(), "wall": time.time(), **fields}
    line = json.dumps(record, ensure_ascii=False, default=str)
    with _lock:
        _fh.write(line + "\n")


def gauge(name: str, delta: int) -> None:
    with _glock:
        _gauges[name] += delta


def _rss_bytes() -> int:
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return -1


def _sampler() -> None:
    while True:
        with _glock:
            snap = dict(_gauges)
        emit("gauge", job=None, rss=_rss_bytes(), threads=threading.active_count(), **snap)
        time.sleep(1.0)


def _span(ev: str, file_of=None, extra_of=None, is_async=False):
    """Bir fonksiyonu sure olcen sarmalayici ile degistirir (davranis ayni)."""
    def deco(fn):
        if is_async:
            @functools.wraps(fn)
            async def wrapper(*args, **kwargs):
                t0, w0 = time.monotonic(), time.time()
                ok = True
                try:
                    return await fn(*args, **kwargs)
                except BaseException:
                    ok = False
                    raise
                finally:
                    emit(ev, t0=t0, t1=time.monotonic(), wall0=w0, ok=ok,
                         file=file_of(*args, **kwargs) if file_of else None,
                         **(extra_of(*args, **kwargs) if extra_of else {}))
        else:
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                t0, w0 = time.monotonic(), time.time()
                ok = True
                try:
                    return fn(*args, **kwargs)
                except BaseException:
                    ok = False
                    raise
                finally:
                    emit(ev, t0=t0, t1=time.monotonic(), wall0=w0, ok=ok,
                         file=file_of(*args, **kwargs) if file_of else None,
                         **(extra_of(*args, **kwargs) if extra_of else {}))
        return wrapper
    return deco


def install() -> None:
    from app.api import export_jobs  # noqa: F401
    from app.api.routers import downloads as downloads_router
    from app.api.routers import export as export_router
    from app.services import audit_reviewer, exporter, llm_recognizer, llm_runtime
    from app.services import presidio_detector, detectors, llm_detector
    from app.webapp import uploads

    if OUTPUT_ROOT:
        root = Path(OUTPUT_ROOT)
        root.mkdir(parents=True, exist_ok=True)
        uploads.UPLOADS_OUTPUT_ROOT = root
        downloads_router.UPLOADS_OUTPUT_ROOT = root

    # --- Is (export_project) ------------------------------------------------
    original_export = exporter.export_project

    @functools.wraps(original_export)
    async def export_project(db, **kwargs):
        JOB.set(kwargs.get("project_name"))
        t0, w0 = time.monotonic(), time.time()
        gauge("active_jobs", 1)
        emit("job_start", t0=t0, wall0=w0, sicil=kwargs.get("sicil_no"))
        status, run_id, err, frames, db_msg = "failed", None, None, None, None
        try:
            report = await original_export(db, **kwargs)
            status, run_id = report.status, report.run_id
            return report
        except BaseException as exc:
            err = type(exc).__name__
            # Yalnizca kod konumu (dosya:satir:fonksiyon); istisna mesaji dosya
            # icerigi tasiyabilecegi icin yazilmaz. DB surucu hatasinin kendi
            # kisa mesaji (orn. "database is locked") SQL/parametre icermez.
            import traceback
            frames = [f"{Path(fr.filename).name}:{fr.lineno}:{fr.name}"
                      for fr in traceback.extract_tb(exc.__traceback__)][-10:]
            orig = getattr(exc, "orig", None)
            db_msg = str(orig)[:120] if orig is not None else None
            raise
        finally:
            gauge("active_jobs", -1)
            emit("job_end", t0=t0, t1=time.monotonic(), wall0=w0, status=status, run_id=run_id, error_type=err,
                 error_frames=frames, db_error=db_msg)

    exporter.export_project = export_project
    export_router.export_project = export_project

    original_work = export_router._export_job_work

    def _export_job_work(kwargs, output_token):
        project = kwargs.get("project_name")
        emit("job_registered", job=project, output_token=output_token)
        work = original_work(kwargs, output_token)

        def timed_work(progress):
            JOB.set(project)
            emit("job_thread_start")
            return work(progress)
        return timed_work

    export_router._export_job_work = _export_job_work
    export_router.save_uploaded_files_to_temp_dir = _span(
        "upload_save", extra_of=lambda files, **k: {"n_files": len(files)},
    )(export_router.save_uploaded_files_to_temp_dir)

    # --- Exporter asamalari -------------------------------------------------
    rel = lambda *a, **k: a[1].rel  # noqa: E731  (orchestrator/db, prep, ...)
    exporter.build_orchestrator = _span("orchestrator_build")(exporter.build_orchestrator)
    exporter.get_or_create_context = _span("context_get_or_create")(exporter.get_or_create_context)
    exporter._ensure_no_in_progress_run = _span("in_progress_check")(exporter._ensure_no_in_progress_run)
    exporter.load_active_rules = _span("load_active_rules")(exporter.load_active_rules)
    exporter._prepare_batch = _span("prepare_batch", extra_of=lambda db, run_id, files, *a, **k: {"n_files": len(files)})(exporter._prepare_batch)
    exporter._detect_for_prep = _span("detect_file", file_of=rel, is_async=True)(exporter._detect_for_prep)
    exporter._apply_masking = _span("mask_file", file_of=lambda db, run_ctx, prep, *a, **k: prep.rel)(exporter._apply_masking)
    exporter._audit_one = _span("audit_llm_file", file_of=lambda text, file_path="", **k: file_path, is_async=True)(exporter._audit_one)
    exporter._finalize_file = _span("finalize_file", file_of=lambda db, run_id, mf, *a, **k: mf.prep.rel)(exporter._finalize_file)
    exporter._run_scan_only_final_verification = _span("scan_only_verify")(exporter._run_scan_only_final_verification)
    exporter._measure_path_content_mismatch = _span("path_mismatch_measure")(exporter._measure_path_content_mismatch)
    exporter.write_manifest = _span("write_manifest")(exporter.write_manifest)
    exporter.publish_run = _span("publish_run")(exporter.publish_run)

    original_consistency = exporter._run_consistency_pass

    def _run_consistency_pass(*args, **kwargs):
        emit("export_stage_start")
        return _span("consistency_pass")(original_consistency)(*args, **kwargs)

    exporter._run_consistency_pass = _run_consistency_pass

    original_lock = exporter._acquire_write_lock

    def _acquire_write_lock(db, run_id):
        t0 = time.monotonic()
        gauge("db_lock_waiting", 1)
        try:
            return original_lock(db, run_id)
        finally:
            gauge("db_lock_waiting", -1)
            emit("db_write_lock", t0=t0, t1=time.monotonic(), run_id=run_id)

    exporter._acquire_write_lock = _acquire_write_lock

    # --- Detector katmanlari ------------------------------------------------
    meta_file = lambda self, content, metadata=None: (metadata or {}).get("file_path")  # noqa: E731
    detectors.RuleBasedDetector.detect = _span("rule_detect", file_of=meta_file, is_async=True)(detectors.RuleBasedDetector.detect)
    presidio_detector.PresidioDetector.detect = _span("presidio_detect", file_of=meta_file, is_async=True)(presidio_detector.PresidioDetector.detect)
    llm_detector.LLMDetector.detect = _span("llm_detect_file", file_of=meta_file, is_async=True)(llm_detector.LLMDetector.detect)

    def _analyze_serialized(self, content, entities):
        # Ozgun govdeyle ayni (kilit + _analyze); yalnizca kilit beklemesi ayrica olculur.
        t0 = time.monotonic()
        gauge("presidio_waiting", 1)
        with self._analyze_lock:
            t1 = time.monotonic()
            gauge("presidio_waiting", -1)
            gauge("presidio_running", 1)
            try:
                return self._analyze(content, entities=entities)
            finally:
                gauge("presidio_running", -1)
                emit("presidio_analyze", t0=t0, t_lock=t1, t1=time.monotonic(), chars=len(content))

    presidio_detector.PresidioDetector._analyze_serialized = _analyze_serialized

    # --- LLM: istek bazinda kapasite bekleme + HTTP ---------------------------
    original_gate = llm_runtime._gate

    @asynccontextmanager
    async def timed_gate(settings):
        holder = _REQ.get()
        t0 = time.monotonic()
        gauge("llm_waiting", 1)
        waiting = True
        try:
            async with original_gate(settings):
                gauge("llm_waiting", -1)
                waiting = False
                if holder is not None:
                    holder["admission_s"] += time.monotonic() - t0
                yield
        finally:
            if waiting:
                gauge("llm_waiting", -1)
                if holder is not None:
                    holder["admission_s"] += time.monotonic() - t0

    llm_runtime._gate = timed_gate

    def _classify(exc: BaseException) -> str:
        import httpx
        cause = exc.__cause__ or exc
        if isinstance(cause, (TimeoutError, asyncio.TimeoutError, httpx.TimeoutException)):
            return "timeout"
        if isinstance(cause, httpx.HTTPStatusError):
            return f"http_{cause.response.status_code}"
        if isinstance(cause, httpx.TransportError):
            return "connection"
        return "error"

    def make_caller(original):
        @functools.wraps(original)
        async def timed_call(host, timeout_seconds, payload, api_key=None):
            holder = _REQ.get()
            t0, w0 = time.monotonic(), time.time()
            gauge("llm_inflight", 1)
            outcome, usage, finish = "ok", {}, None
            try:
                raw = await original(host, timeout_seconds, payload, api_key)
                if isinstance(raw, dict):
                    usage = raw.get("usage") or {}
                    choices = raw.get("choices") or []
                    if choices and isinstance(choices[0], dict):
                        finish = choices[0].get("finish_reason")
                return raw
            except BaseException as exc:
                outcome = _classify(exc)
                raise
            finally:
                gauge("llm_inflight", -1)
                t1 = time.monotonic()
                if holder is not None:
                    holder["attempts"].append({
                        "t0": t0, "t1": t1, "wall0": w0, "status": outcome,
                        "prompt_tokens": usage.get("prompt_tokens"),
                        "completion_tokens": usage.get("completion_tokens"),
                        "finish_reason": finish,
                    })
        return timed_call

    llm_recognizer.call_vllm = make_caller(llm_recognizer.call_vllm)
    audit_reviewer.call_vllm = make_caller(audit_reviewer.call_vllm)

    original_request = llm_runtime.LLMScanMetrics.request

    @functools.wraps(original_request)
    async def request(self, settings, payload, caller, parser, chunk_index):
        holder = {"admission_s": 0.0, "attempts": []}
        token = _REQ.set(holder)
        t0, w0 = time.monotonic(), time.time()
        status, err = "ok", None
        try:
            return await original_request(self, settings, payload, caller, parser, chunk_index)
        except BaseException as exc:
            cause = exc.__cause__ or exc
            err = type(cause).__name__
            last = holder["attempts"][-1]["status"] if holder["attempts"] else None
            status = last if last and last != "ok" else ("parse_error" if holder["attempts"] else "not_sent")
            raise
        finally:
            _REQ.reset(token)
            t1 = time.monotonic()
            http_s = sum(a["t1"] - a["t0"] for a in holder["attempts"])
            emit("llm_request", t0=t0, t1=t1, wall0=w0, file=self.file_path, phase=self.phase,
                 chunk=chunk_index, chunks=self.chunk_count, status=status, error_type=err,
                 admission_s=holder["admission_s"], http_s=http_s, attempts=holder["attempts"],
                 max_chars=None, payload_chars=sum(len(m.get("content", "")) for m in payload.get("messages", [])
                                                   if isinstance(m.get("content"), str)))

    llm_runtime.LLMScanMetrics.request = request

    original_exit = llm_runtime.LLMScanMetrics.__exit__

    def scan_exit(self, kind, exc, tb):
        try:
            return original_exit(self, kind, exc, tb)
        finally:
            emit("llm_scan", t0=self.started, t1=time.monotonic(), file=self.file_path, phase=self.phase,
                 chunks=self.chunk_count, requests=self.requests, completed=self.completed,
                 ok=kind is None, prompt_tokens=self.prompt_tokens, completion_tokens=self.completion_tokens)

    llm_runtime.LLMScanMetrics.__exit__ = scan_exit

    # --- Indirme (zip) ------------------------------------------------------
    downloads_router._zip_response = _span(
        "download_zip", extra_of=lambda target_dir, **k: {"output_token": Path(target_dir).name},
    )(downloads_router._zip_response)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    args = ap.parse_args()

    install()
    from app.core.config import settings

    v = settings.vllm
    emit("server_config", job=None, port=args.port, db_path=str(settings.database.resolved_path),
         vllm_enabled=v.enabled, vllm_host=v.host, vllm_model=v.model, vllm_profile=v.profile,
         max_concurrent_requests=v.max_concurrent_requests, file_batch_size=v.file_batch_size,
         max_file_chars=v.max_file_chars, chunk_overlap_chars=v.chunk_overlap_chars,
         max_tokens=v.max_tokens, timeout_seconds=v.timeout_seconds, transient_retries=v.transient_retries,
         audit_unchanged_files=v.audit_unchanged_files, redact_known_findings=v.redact_known_findings,
         admission_dir=str(v.admission_dir), reasoning_effort=v.reasoning_effort,
         disable_thinking=v.disable_thinking, presence_penalty=v.presence_penalty,
         output_root=OUTPUT_ROOT)
    threading.Thread(target=_sampler, name="lt-gauge", daemon=True).start()

    import uvicorn
    from app.api.main import app
    uvicorn.run(app, host=args.host, port=args.port, log_level="info", workers=1)


if __name__ == "__main__":
    main()
