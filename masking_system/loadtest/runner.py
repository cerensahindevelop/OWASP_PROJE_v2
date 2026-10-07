"""Cok kullanicili yuk testi kosucusu.

    loadtest/.venv/bin/python -m loadtest.runner --plan smoke  --campaign loadtest/results/smoke
    loadtest/.venv/bin/python -m loadtest.runner --plan main   --campaign loadtest/results/main
    loadtest/.venv/bin/python -m loadtest.runner --plan workers --campaign loadtest/results/workers

Kosucu yeniden baslatilabilir: DONE dosyasi olan tekrarlar atlanir.

Her tekrar (rep) icin:
  1. Onceki kosulun kuyrugu bosalmis mi kontrol edilir (test backend'i yok,
     llama-server slotlari bos).
  2. Kullanicinin canli DB'sinin tutarli bir kopyasi (sqlite backup) alinir;
     test bu kopya uzerinde calisir, gercek masking.db'ye yazilmaz.
  3. Istenen sayida instrumented backend sureci (ayri port) baslatilir.
     Ayarlar yalnizca bu sureclerin ortam degiskenleriyle verilir; .env
     dosyasi ve Ollama ayarlari DEGISTIRILMEZ.
  4. Soguk kosulda model Ollama'dan bosaltilir (keep_alive=0); sicak kosulda
     her backend surecinde bir isinma isi calisir ve istatistikten cikarilir.
  5. Sanal kullanicilar (her biri ayri httpx oturumu, ayri sicil, her is ayri
     proje adi = ayri islem kaydi) projeyi yukler, isin bitmesini bekler,
     ciktiyi indirir.
  6. Is bitince donanim ornekleyici durur, Ollama gunlugu kesilir, kayit
     yalitimi DB uzerinden dogrulanir.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import platform
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx
import psutil

from loadtest.output_checks import inspect_output, inspect_synthetic_sql

HERE = Path(__file__).resolve().parent
SYSTEM_ROOT = HERE.parent
SERVICE_DIR = SYSTEM_ROOT / "masking_service"
APP_PYTHON = SERVICE_DIR / ".venv" / "bin" / "python"
LIVE_DB = SYSTEM_ROOT / "masking.db"
DATA_DIR = HERE / "data"
WORK_ROOT = Path(os.environ.get("LOADTEST_WORK", "/tmp/masking-loadtest-work"))
PORT_BASE = 8110
OLLAMA = "http://127.0.0.1:11434"
POLL_S = 0.5

# .env'deki mevcut (degistirilmeyen) degerler - kosul tanimlarinin "varsayilan"i.
DEFAULT_BACKEND_WORKERS = 1
DEFAULT_FILE_BATCH = 8
DEFAULT_LLM_CONC = 1


@dataclass
class Condition:
    cid: str
    scenario: str
    users: int
    sizes: list[str]
    jobs_per_user: int | None = 1
    duration_s: float | None = None
    backend_workers: int = DEFAULT_BACKEND_WORKERS
    file_batch: int = DEFAULT_FILE_BATCH
    llm_conc: int = DEFAULT_LLM_CONC
    cold: bool = False
    warmup: bool = True
    barrier: bool = True
    stagger_s: float = 0.0
    deadline_s: float = 3 * 3600
    reps: int = 3
    group: str = "main"


def plan_smoke() -> list[Condition]:
    return [
        Condition("smoke_small_u1", "warm", 1, ["small"], reps=1, group="smoke"),
        Condition("smoke_medium_u1", "warm", 1, ["medium"], reps=1, warmup=False, group="smoke"),
        Condition("smoke_large_u1", "warm", 1, ["large"], reps=1, warmup=False, group="smoke"),
    ]


def plan_main() -> list[Condition]:
    conds: list[Condition] = []
    for n in (1, 4):
        conds.append(Condition(f"S1_cold_u{n:02d}", "cold", n, ["small"], cold=True, warmup=False))
    for n in (1, 2, 4, 10):
        conds.append(Condition(f"S2_warm_u{n:02d}", "warm", n, ["small"], jobs_per_user=2, barrier=False, stagger_s=1.0))
    for n in (1, 2, 4, 10):
        conds.append(Condition(f"S3_burst_u{n:02d}", "burst", n, ["medium"]))
    for n in (1, 2, 4, 10):
        conds.append(Condition(f"S4_sustained_u{n:02d}", "sustained", n, ["small"], jobs_per_user=None,
                               duration_s=600, barrier=False, stagger_s=1.0))
    for n in (1, 2, 4, 10):
        conds.append(Condition(f"S5_mixed_u{n:02d}", "mixed", n, ["large", "small", "medium"]))
    return conds


def plan_workers() -> list[Condition]:
    conds: list[Condition] = []
    seen: set[tuple[int, int, int]] = set()
    stages = [("A", "backend_workers", (1, 2, 4)), ("B", "llm_conc", (1, 2, 4)), ("C", "file_batch", (1, 2, 4))]
    for stage, knob, values in stages:
        for v in values:
            cfg = {"backend_workers": DEFAULT_BACKEND_WORKERS, "file_batch": DEFAULT_FILE_BATCH, "llm_conc": DEFAULT_LLM_CONC}
            cfg[knob] = v
            key = (cfg["backend_workers"], cfg["file_batch"], cfg["llm_conc"])
            for n in (1, 2, 4, 8):
                cid = f"W_bw{key[0]}_fb{key[1]}_lc{key[2]}_u{n:02d}"
                if (key, n) in seen:
                    continue
                conds.append(Condition(cid, "workers", n, ["small"], deadline_s=2400, group=f"W{stage}", **cfg))
            seen.add(key)
    # ayni (bw,fb,lc,n) yalnizca bir kez; ilk gorundugu asama etiketiyle
    uniq, ids = [], set()
    for c in conds:
        if c.cid not in ids:
            uniq.append(c)
            ids.add(c.cid)
    return uniq


def plan_regression() -> list[Condition]:
    return [
        Condition("R_startup_u10", "burst", 10, ["small"], reps=1, group="regression", deadline_s=1800),
        Condition("R_dense_u01", "burst", 1, ["medium"], reps=1, group="regression", deadline_s=1800),
    ]


PLANS = {"smoke": plan_smoke, "main": plan_main, "workers": plan_workers, "regression": plan_regression}


# ---------------------------------------------------------------------------
# Yardimcilar
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def load_datasets() -> dict[str, dict]:
    manifest = json.loads((DATA_DIR / "manifest.json").read_text())
    out = {}
    for size, meta in manifest["datasets"].items():
        files = [(f["path"], (DATA_DIR / size / f["path"]).read_bytes()) for f in meta["files"]]
        out[size] = {"meta": meta, "files": files}
    return out


def snapshot_db(dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
    dst = sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()


def llama_port() -> int | None:
    for p in psutil.process_iter(["cmdline"]):
        cmd = p.info.get("cmdline") or []
        if any("llama-server" in c for c in cmd) and "--port" in cmd:
            return int(cmd[cmd.index("--port") + 1])
    return None


def llama_busy() -> bool | None:
    port = llama_port()
    if port is None:
        return False
    try:
        slots = httpx.get(f"http://127.0.0.1:{port}/slots", timeout=2).json()
        return any(s.get("is_processing") for s in slots)
    except Exception:
        return None


def our_backends() -> list[psutil.Process]:
    out = []
    for p in psutil.process_iter(["cmdline"]):
        if "instrumented_server.py" in " ".join(p.info.get("cmdline") or []):
            out.append(p)
    return out


def wait_idle(max_wait: float = 1800) -> dict:
    """Onceki kosulun kuyrugu bosalmadan devam etme."""
    t0 = time.monotonic()
    stale = our_backends()
    for p in stale:
        p.terminate()
    psutil.wait_procs(stale, timeout=15)
    quiet = 0
    while time.monotonic() - t0 < max_wait:
        busy = llama_busy()
        quiet = quiet + 1 if busy is False else 0
        if quiet >= 5:
            return {"idle_wait_s": time.monotonic() - t0, "stale_backends_killed": len(stale)}
        time.sleep(1)
    return {"idle_wait_s": time.monotonic() - t0, "stale_backends_killed": len(stale), "idle_timeout": True}


def model_loaded(model: str) -> bool:
    try:
        return any(m.get("name") == model for m in httpx.get(f"{OLLAMA}/api/ps", timeout=5).json().get("models", []))
    except Exception:
        return False


def unload_model(model: str) -> float:
    t0 = time.monotonic()
    httpx.post(f"{OLLAMA}/api/generate", json={"model": model, "keep_alive": 0}, timeout=120)
    while model_loaded(model) and time.monotonic() - t0 < 120:
        time.sleep(0.5)
    time.sleep(2)
    return time.monotonic() - t0


def env_model() -> str:
    for line in (SYSTEM_ROOT / ".env").read_text().splitlines():
        if line.startswith("VLLM_MODEL="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("VLLM_MODEL bulunamadi")


# ---------------------------------------------------------------------------
# Backend surecleri
# ---------------------------------------------------------------------------

@dataclass
class Backend:
    port: int
    proc: subprocess.Popen
    events: Path
    log: Path


def start_backends(cond: Condition, rep_dir: Path, work: Path) -> list[Backend]:
    env = dict(os.environ)
    env.update({
        "DB_PATH": str(work / "masking.db"),
        "VLLM_ADMISSION_DIR": str(work / "admission"),
        "VLLM_MAX_CONCURRENT_REQUESTS": str(cond.llm_conc),
        "VLLM_FILE_BATCH_SIZE": str(cond.file_batch),
        "LOADTEST_OUTPUT_ROOT": str(work / "outputs"),
        "PYTHONUNBUFFERED": "1",
    })
    backends = []
    for i in range(cond.backend_workers):
        port = PORT_BASE + i
        events = rep_dir / f"server_events_{port}.jsonl"
        logf = rep_dir / f"server_{port}.log"
        env_i = dict(env, LOADTEST_EVENTS=str(events))
        proc = subprocess.Popen(
            [str(APP_PYTHON), str(HERE / "instrumented_server.py"), "--port", str(port)],
            cwd=SERVICE_DIR, env=env_i, stdout=open(logf, "w"), stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        backends.append(Backend(port, proc, events, logf))
    deadline = time.monotonic() + 120
    for b in backends:
        while True:
            if b.proc.poll() is not None:
                raise RuntimeError(f"backend {b.port} acilirken kapandi; bkz {b.log}")
            try:
                r = httpx.get(f"http://127.0.0.1:{b.port}/health", timeout=2)
                if r.status_code == 200:
                    if r.json().get("status") != "ok":
                        raise RuntimeError(f"backend {b.port} health={r.json()}")
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError(f"backend {b.port} 120 sn icinde hazir olmadi")
            time.sleep(0.3)
    return backends


def stop_backends(backends: list[Backend]) -> list[dict]:
    status = []
    for b in backends:
        status.append({"port": b.port, "pid": b.proc.pid, "exit_code_before_stop": b.proc.poll()})
        if b.proc.poll() is None:
            b.proc.send_signal(signal.SIGTERM)
    for b in backends:
        try:
            b.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            b.proc.kill()
    return status


# ---------------------------------------------------------------------------
# Sanal kullanici
# ---------------------------------------------------------------------------

class Abort(Exception):
    pass


@dataclass
class RunCtx:
    cond: Condition
    rep: int
    datasets: dict
    backends: list[Backend]
    jobs_out: io.TextIOBase
    hard_deadline: float
    window_end: float | None = None
    abort_reason: str | None = None
    records: list = field(default_factory=list)


def classify_outcomes(report: dict) -> dict:
    states: dict[str, int] = {}
    checks: dict[str, int] = {}
    technical = 0
    tech_checks = {"llm_tespit", "llm_denetimi_tamamlanamadi", "tespit_katmani", "maskeleme_hatasi",
                   "faz_d_cokmesi", "yazma", "okuma", "sonlandirma"}
    for o in report.get("outcomes", []):
        st = o.get("final_state") or "NONE"
        states[st] = states.get(st, 0) + 1
        fc = o.get("failed_check")
        if fc:
            checks[fc] = checks.get(fc, 0) + 1
            if st != "READY" and fc in tech_checks:
                technical += 1
    return {"final_states": states, "failed_checks": checks, "files_blocked_technical": technical}


async def run_job(ctx: RunCtx, client: httpx.AsyncClient, vu: int, seq: int, size: str, *, warmup: bool,
                  backend: Backend) -> dict:
    cond = ctx.cond
    ds = ctx.datasets[size]
    tag = "w" if warmup else "j"
    project = f"lt-{cond.cid}-r{ctx.rep}-u{vu:02d}-{tag}{seq:03d}"
    sicil = f"LT{vu:02d}"
    rec = {
        "condition": cond.cid, "scenario": cond.scenario, "group": cond.group, "rep": ctx.rep, "vu": vu,
        "seq": seq, "warmup": warmup, "size": size, "project_name": project, "sicil_no": sicil,
        "branch_name": "main", "port": backend.port, "file_count": ds["meta"]["file_count"],
        "total_bytes": ds["meta"]["total_bytes"], "users": cond.users, "backend_workers": cond.backend_workers,
        "file_batch": cond.file_batch, "llm_conc": cond.llm_conc,
    }
    base = f"http://127.0.0.1:{backend.port}"
    rec["t_submit"] = time.monotonic()
    rec["wall_submit"] = time.time()
    outcome = "unknown"
    try:
        files = [("files", (rel, data, "application/octet-stream")) for rel, data in ds["files"]]
        data = {"project_name": project, "sicil_no": sicil, "branch_name": "main",
                "initiated_by": f"loadtest-vu{vu:02d}", "is_directory_upload": "true"}
        try:
            r = await client.post(f"{base}/export/upload/jobs", data=data, files=files, timeout=600)
        except httpx.TimeoutException:
            outcome = "upload_timeout"
            return rec
        except httpx.HTTPError as exc:
            outcome = f"upload_conn_error:{type(exc).__name__}"
            return rec
        rec["t_upload_done"] = time.monotonic()
        rec["upload_s"] = rec["t_upload_done"] - rec["t_submit"]
        rec["upload_http_status"] = r.status_code
        if r.status_code != 202:
            outcome = f"upload_http_{r.status_code}"
            return rec
        job_id = r.json()["job_id"]
        rec["job_id"] = job_id
        poll_errors = 0
        while True:
            if time.monotonic() > ctx.hard_deadline:
                outcome = "test_deadline_incomplete"
                return rec
            if ctx.abort_reason:
                outcome = "aborted:" + ctx.abort_reason
                return rec
            try:
                pr = await client.get(f"{base}/export/jobs/{job_id}", timeout=30)
            except httpx.HTTPError:
                poll_errors += 1
                rec["poll_errors"] = poll_errors
                if poll_errors >= 20:
                    outcome = "poll_conn_error"
                    return rec
                await asyncio.sleep(POLL_S)
                continue
            if pr.status_code != 200:
                poll_errors += 1
                rec["poll_errors"] = poll_errors
                if pr.status_code == 404 or poll_errors >= 20:
                    outcome = f"poll_http_{pr.status_code}"
                    return rec
                await asyncio.sleep(POLL_S)
                continue
            js = pr.json()
            rec["progress_total"] = js.get("total")
            if js["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(POLL_S)
        rec["t_job_done_seen"] = time.monotonic()
        rec["job_status"] = js["status"]
        if js["status"] == "failed":
            outcome = "job_failed"
            rec["job_error_status"] = js.get("error_status")
            rec["job_error_message"] = (js.get("error_message") or "")[:300]
            return rec
        result = js["result"]
        report = result["report"]
        rec.update({
            "run_id": report["run_id"], "context_id": report["context_id"], "report_status": report["status"],
            "report_project": report["project_name"], "report_sicil": report["sicil_no"],
            "files_scanned": report["files_scanned"], "files_masked": report["files_masked"],
            "files_ready": report.get("files_ready"), "files_review_required": report.get("files_review_required"),
            "files_security_quarantine": report.get("files_security_quarantine"),
            "files_validation_failed": report.get("files_validation_failed"),
            "files_quarantined_pending_audit": report.get("files_quarantined_pending_audit"),
            "files_failed_detection": report.get("files_failed_detection"),
            "total_matches": report["total_matches"], "pending_count": result.get("pending_count"),
            "quarantined_count": result.get("quarantined_count"),
            "validation_failed_count": result.get("validation_failed_count"),
            "llm_usage_summary": report.get("llm_usage_summary"), "output_token": result.get("output_token"),
            "blocked_by_check": report.get("blocked_by_check"),
        })
        rec.update(classify_outcomes(report))
        t_dl = time.monotonic()
        try:
            dr = await client.get(f"{base}/export/outputs/{result['output_token']}/download", timeout=600)
        except httpx.HTTPError as exc:
            outcome = f"download_error:{type(exc).__name__}"
            return rec
        rec["t_download_done"] = time.monotonic()
        rec["download_s"] = rec["t_download_done"] - t_dl
        rec["download_http_status"] = dr.status_code
        if dr.status_code != 200:
            outcome = f"download_http_{dr.status_code}"
            return rec
        rec["download_bytes"] = len(dr.content)
        try:
            rec.update(inspect_output(dr.content, report))
            if rec["output_quality"] == "complete":
                rec.update(inspect_synthetic_sql(dr.content, ds["files"]))
        except Exception as exc:  # noqa: BLE001
            rec["zip_error"] = type(exc).__name__
            rec["output_validated"] = False
            rec["output_quality"] = "invalid"
            outcome = "invalid_output"
            return rec
        rec["e2e_s"] = rec["t_download_done"] - rec["t_submit"]
        outcome = "completed"
        return rec
    finally:
        rec["t_end"] = time.monotonic()
        rec["outcome"] = outcome
        if outcome == "completed" and ctx.window_end is not None and rec["t_end"] > ctx.window_end:
            rec["outcome"] = "completed_after_window"
        ctx.jobs_out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        ctx.jobs_out.flush()
        ctx.records.append(rec)


async def virtual_user(ctx: RunCtx, vu: int, start_evt: asyncio.Event, measure_start: float) -> None:
    cond = ctx.cond
    backend = ctx.backends[vu % len(ctx.backends)]
    size = cond.sizes[vu % len(cond.sizes)]
    # Her sanal kullanici kendi HTTP oturumunu (baglanti havuzu, cerez kabi) kullanir.
    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=4)) as client:
        await start_evt.wait()
        if cond.stagger_s:
            await asyncio.sleep(vu * cond.stagger_s)
        seq = 0
        while True:
            if cond.jobs_per_user is not None and seq >= cond.jobs_per_user:
                return
            if cond.duration_s is not None and time.monotonic() >= measure_start + cond.duration_s:
                return
            if ctx.abort_reason or time.monotonic() > ctx.hard_deadline:
                return
            await run_job(ctx, client, vu, seq, size, warmup=False, backend=backend)
            seq += 1


async def watchdog(ctx: RunCtx, stop: asyncio.Event) -> None:
    """Bellek tasmasi / surec cokmesi durumunda kosulu durdurur."""
    while not stop.is_set():
        for b in ctx.backends:
            if b.proc.poll() is not None:
                ctx.abort_reason = f"backend_{b.port}_exited_{b.proc.returncode}"
        vm = psutil.virtual_memory()
        if vm.available < 1.5 * 2**30:
            ctx.abort_reason = f"ram_available_{vm.available // 2**20}MiB"
        if psutil.swap_memory().percent > 90:
            ctx.abort_reason = "swap_over_90pct"
        await asyncio.sleep(1)


async def run_measurement(ctx: RunCtx, marks: dict) -> None:
    cond = ctx.cond
    if cond.warmup:
        # Isinma: her backend surecinde bir kucuk is; istatistige girmez.
        for i, b in enumerate(ctx.backends):
            async with httpx.AsyncClient() as client:
                rec = await run_job(ctx, client, 900 + i, 0, "small", warmup=True, backend=b)
            if rec["outcome"] != "completed":
                log(f"  UYARI: isinma isi basarisiz: {rec['outcome']}")
    marks["measure_start_mono"] = time.monotonic()
    marks["measure_start_wall"] = time.time()
    if cond.duration_s is not None:
        ctx.window_end = marks["measure_start_mono"] + cond.duration_s
    ctx.hard_deadline = marks["measure_start_mono"] + cond.deadline_s
    start_evt = asyncio.Event()
    stop = asyncio.Event()
    wd = asyncio.create_task(watchdog(ctx, stop))
    users = [asyncio.create_task(virtual_user(ctx, vu, start_evt, marks["measure_start_mono"]))
             for vu in range(cond.users)]
    await asyncio.sleep(0.2)
    start_evt.set()  # bariyer: hepsi ayni anda serbest
    await asyncio.gather(*users)
    stop.set()
    await wd
    marks["measure_end_mono"] = time.monotonic()
    marks["measure_end_wall"] = time.time()
    if cond.duration_s is not None:
        marks["window_end_mono"] = ctx.window_end


# ---------------------------------------------------------------------------
# Yalitim dogrulamasi
# ---------------------------------------------------------------------------

def verify_isolation(db_path: Path, records: list[dict], datasets: dict, output_root: Path) -> dict:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    problems = []
    run_ids = [r["run_id"] for r in records if r.get("run_id") is not None]
    if len(run_ids) != len(set(run_ids)):
        problems.append("ayni run_id birden fazla ise atanmis")
    tokens = [r.get("output_token") for r in records if r.get("output_token")]
    if len(tokens) != len(set(tokens)):
        problems.append("ayni cikti tokeni birden fazla ise atanmis")
    checked = 0
    for r in records:
        rid = r.get("run_id")
        if rid is None:
            continue
        checked += 1
        row = con.execute(
            "select c.id, c.proje_adi, c.personel_no, c.branch_adi, r.hedef_yol from maskeleme_calismalari r "
            "join maskeleme_baglamlari c on c.id = r.baglam_id where r.id = ?", (rid,)).fetchone()
        if row is None:
            problems.append(f"run {rid}: DB kaydi yok")
            continue
        ctx_id, proj, sicil, branch, target = row
        if (proj, sicil, branch) != (r["project_name"], r["sicil_no"], r["branch_name"]):
            problems.append(f"run {rid}: kimlik uyusmuyor DB=({proj},{sicil},{branch}) is=({r['project_name']},{r['sicil_no']})")
        if r.get("report_project") != r["project_name"] or r.get("report_sicil") != r["sicil_no"]:
            problems.append(f"run {rid}: rapor kimligi uyusmuyor")
        if r.get("output_token") and Path(target).name != r["output_token"]:
            problems.append(f"run {rid}: hedef klasor tokeni uyusmuyor")
        foreign = con.execute("select count(*) from deger_eslemeleri where calisma_id = ? and baglam_id != ?",
                              (rid, ctx_id)).fetchone()[0]
        if foreign:
            problems.append(f"run {rid}: {foreign} deger eslemesi baska baglama yazilmis")
        allowed = {p for p, _ in datasets[r["size"]]["files"]} | {""}
        paths = {p for (p,) in con.execute("select distinct dosya_yolu from denetim_kaydi where calisma_id = ?", (rid,))}
        stray = paths - allowed
        if stray:
            problems.append(f"run {rid}: denetim kaydinda bu projeye ait olmayan {len(stray)} yol")
        if r.get("manifest_job_id") is not None and r["manifest_job_id"] != rid:
            problems.append(f"run {rid}: indirilen paketin manifest job_id={r['manifest_job_id']}")
    con.close()
    return {"jobs_checked": checked, "problems": problems, "ok": not problems}


# ---------------------------------------------------------------------------
# Kosul / tekrar
# ---------------------------------------------------------------------------

def journal_slice(t0_wall: float, t1_wall: float, out: Path) -> None:
    keep = ("print_timing", "[GIN]", "processing task", "stop processing", "cancel task", "error", "CUDA",
            "runner", "load", "offload", "truncated", "context shift")
    try:
        txt = subprocess.run(
            ["journalctl", "-u", "ollama", "--since", f"@{int(t0_wall) - 2}", "--until", f"@{int(t1_wall) + 3}",
             "-o", "short-unix", "--no-pager"], capture_output=True, text=True, timeout=120).stdout
    except Exception as exc:  # noqa: BLE001
        txt = f"journalctl_failed {exc}\n"
    with open(out, "w") as fh:
        for line in txt.splitlines():
            if any(k in line for k in keep):
                fh.write(line + "\n")


def run_rep(cond: Condition, rep: int, campaign: Path, datasets: dict, model: str) -> None:
    rep_dir = campaign / cond.cid / f"rep{rep}"
    if (rep_dir / "DONE").exists():
        log(f"atla {cond.cid} rep{rep} (DONE)")
        return
    if rep_dir.exists():
        shutil.rmtree(rep_dir)
    rep_dir.mkdir(parents=True)
    work = WORK_ROOT / campaign.name / cond.cid / f"rep{rep}"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    log(f"=== {cond.cid} rep{rep}: users={cond.users} sizes={cond.sizes} bw={cond.backend_workers} "
        f"fb={cond.file_batch} lc={cond.llm_conc}")
    marks: dict = {"condition": asdict(cond), "rep": rep}
    marks.update(wait_idle())
    snapshot_db(work / "masking.db")
    hw = subprocess.Popen([sys.executable, "-m", "loadtest.hwmon", "--out", str(rep_dir / "hw")], cwd=SYSTEM_ROOT,
                          stdout=subprocess.DEVNULL, stderr=open(rep_dir / "hwmon.err", "w"))
    marks["rep_start_wall"] = time.time()
    marks["rep_start_mono"] = time.monotonic()
    if cond.cold:
        marks["model_unload_s"] = unload_model(model)
        marks["model_loaded_at_start"] = model_loaded(model)
    else:
        marks["model_loaded_at_start"] = model_loaded(model)
    backends: list[Backend] = []
    jobs_path = rep_dir / "jobs.jsonl"
    ctx = None
    try:
        t_b = time.monotonic()
        backends = start_backends(cond, rep_dir, work)
        marks["backend_start_s"] = time.monotonic() - t_b
        marks["backend_pids"] = [b.proc.pid for b in backends]
        with open(jobs_path, "w") as jobs_out:
            ctx = RunCtx(cond, rep, datasets, backends, jobs_out, hard_deadline=time.monotonic() + cond.deadline_s)
            asyncio.run(run_measurement(ctx, marks))
        marks["abort_reason"] = ctx.abort_reason
    except Exception as exc:  # noqa: BLE001
        marks["runner_error"] = f"{type(exc).__name__}: {exc}"
        log(f"  HATA: {marks['runner_error']}")
    finally:
        marks["backend_exit"] = stop_backends(backends)
        marks["drain"] = wait_idle(max_wait=600)
        marks["rep_end_wall"] = time.time()
        marks["rep_end_mono"] = time.monotonic()
        hw.send_signal(signal.SIGTERM)
        try:
            hw.wait(timeout=10)
        except subprocess.TimeoutExpired:
            hw.kill()
        journal_slice(marks["rep_start_wall"], marks["rep_end_wall"], rep_dir / "ollama_journal.log")
    if ctx is not None:
        marks["isolation"] = verify_isolation(work / "masking.db", ctx.records, datasets, work / "outputs")
        n_ok = sum(r["outcome"] == "completed" and not r["warmup"] for r in ctx.records)
        n_all = sum(not r["warmup"] for r in ctx.records)
        log(f"  bitti: {n_ok}/{n_all} tamamlandi, yalitim={'OK' if marks['isolation']['ok'] else 'SORUN'}, "
            f"sure={marks['rep_end_mono'] - marks['rep_start_mono']:.0f}s")
    (rep_dir / "rep.json").write_text(json.dumps(marks, indent=2, ensure_ascii=False, default=str))
    shutil.rmtree(work, ignore_errors=True)
    if "runner_error" not in marks:
        (rep_dir / "DONE").write_text("ok\n")


def environment_info(campaign: Path, plan: str, conds: list[Condition]) -> None:
    def sh(cmd: list[str]) -> str:
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.strip()
        except Exception as exc:  # noqa: BLE001
            return f"ERR {exc}"
    env_lines = {}
    for line in (SYSTEM_ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env_lines[k.strip()] = "***" if any(s in k for s in ("KEY", "SECRET", "PASSWORD")) else v.strip()
    llama_cmd = ""
    for p in psutil.process_iter(["cmdline"]):
        cmd = p.info.get("cmdline") or []
        if any("llama-server" in c for c in cmd):
            llama_cmd = " ".join(cmd)
    info = {
        "plan": plan, "created_wall": time.time(), "host": platform.node(),
        "cpu": sh(["bash", "-c", "lscpu | grep -E 'Model name|^CPU\\(s\\)|Thread|Socket'"]),
        "ram_total_gib": psutil.virtual_memory().total / 2**30, "kernel": platform.release(),
        "git_commit": sh(["git", "-C", str(SYSTEM_ROOT), "rev-parse", "HEAD"]),
        "git_status": sh(["git", "-C", str(SYSTEM_ROOT), "status", "--short"]),
        "app_python": sh([str(APP_PYTHON), "--version"]), "loadtest_python": sys.version,
        "env_settings": env_lines,
        "ollama_version": sh(["ollama", "--version"]),
        "ollama_systemd_env": sh(["systemctl", "show", "ollama", "-p", "Environment"]),
        "llama_server_cmdline_at_start": llama_cmd,
        "ollama_show": sh(["bash", "-c", f"ollama show {env_model()} 2>&1 | head -40"]),
        "nvidia_smi": sh(["nvidia-smi", "--query-gpu=index,name,uuid,memory.total,driver_version,power.limit",
                          "--format=csv"]),
        "datasets": json.loads((DATA_DIR / "manifest.json").read_text()),
        "conditions": [asdict(c) for c in conds],
        "defaults": {"backend_workers": DEFAULT_BACKEND_WORKERS, "file_batch": DEFAULT_FILE_BATCH,
                     "llm_conc": DEFAULT_LLM_CONC},
        "poll_interval_s": POLL_S,
    }
    (campaign / "environment.json").write_text(json.dumps(info, indent=2, ensure_ascii=False))


def _terminate(*_):
    raise KeyboardInterrupt  # finally bloklari backend'leri ve ornekleyiciyi kapatsin


def main() -> None:
    signal.signal(signal.SIGTERM, _terminate)
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", choices=sorted(PLANS), required=True)
    ap.add_argument("--campaign", required=True)
    ap.add_argument("--only", nargs="*", help="yalnizca bu kosul kimlikleri (onek eslesmesi)")
    ap.add_argument("--reps", type=int, help="tekrar sayisini gecersiz kil")
    args = ap.parse_args()
    campaign = Path(args.campaign).resolve()
    campaign.mkdir(parents=True, exist_ok=True)
    conds = PLANS[args.plan]()
    if args.only:
        conds = [c for c in conds if any(c.cid.startswith(o) for o in args.only)]
    if args.reps:
        for c in conds:
            c.reps = args.reps
    datasets = load_datasets()
    model = env_model()
    if not (campaign / "environment.json").exists():
        environment_info(campaign, args.plan, conds)
    log(f"plan={args.plan} kosul={len(conds)} kampanya={campaign}")
    for cond in conds:
        for rep in range(1, cond.reps + 1):
            run_rep(cond, rep, campaign, datasets, model)
    log("kampanya tamamlandi")


if __name__ == "__main__":
    main()
