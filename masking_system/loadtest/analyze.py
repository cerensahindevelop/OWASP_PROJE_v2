"""Ham olcumlerden ozet tablolar, grafikler ve CSV'ler uretir.

    loadtest/.venv/bin/python -m loadtest.analyze loadtest/results/main [loadtest/results/workers ...]

Her kampanya klasorune yazar:
  summary_conditions.csv / .json   kosul basina birlesik ozet (3 tekrar havuzlanmis)
  summary_reps.csv                 tekrar basina ozet
  jobs_all.csv                     is basina satir (istemci + sunucu asama sureleri)
  llm_requests_all.csv             LLM istegi basina satir
  files_all.csv                    dosya basina asama sureleri
  server_tasks_all.csv             llama-server gorev basina (prefill/decode) sureleri
  hw_summary.csv                   tekrar x GPU donanim ozeti
  charts/*.png

Yuzdelikler en-yakin-sira (nearest-rank) yontemiyle hesaplanir. Ornek sayisi
p95 icin < 20, p99 icin < 100 ise deger "yetersiz" bayragiyla isaretlenir.
Paralel asamalarin sureleri TOPLANMAZ: is basina asama suresi, o asamaya ait
araliklarin BIRLESIMININ (union) uzunlugudur.
"""

from __future__ import annotations

import csv
import json
import math
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

P95_MIN_N = 20
P99_MIN_N = 100


# ---------------------------------------------------------------------------
# Istatistik yardimcilari
# ---------------------------------------------------------------------------

def pct(values, p):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    return vals[max(0, math.ceil(p / 100 * len(vals)) - 1)]


def stats(values) -> dict:
    vals = [v for v in values if v is not None]
    n = len(vals)
    if not n:
        return {"n": 0, "mean": None, "min": None, "p50": None, "p95": None, "p99": None, "max": None,
                "p95_ok": False, "p99_ok": False}
    return {"n": n, "mean": statistics.fmean(vals), "min": min(vals), "p50": pct(vals, 50), "p95": pct(vals, 95),
            "p99": pct(vals, 99), "max": max(vals), "p95_ok": n >= P95_MIN_N, "p99_ok": n >= P99_MIN_N}


def union_length(intervals) -> float:
    iv = sorted((a, b) for a, b in intervals if a is not None and b is not None and b >= a)
    total, cur_a, cur_b = 0.0, None, None
    for a, b in iv:
        if cur_b is None or a > cur_b:
            if cur_b is not None:
                total += cur_b - cur_a
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    if cur_b is not None:
        total += cur_b - cur_a
    return total


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Ollama / llama-server gunlugu
# ---------------------------------------------------------------------------

_TS = re.compile(r"^(\d+\.\d+)\s")
_PROMPT = re.compile(r"task (\d+) \| prompt eval time =\s+([\d.]+) ms /\s+(\d+) tokens")
_EVAL = re.compile(r"task (\d+) \|\s+eval time =\s+([\d.]+) ms /\s+(\d+) tokens")
_LAUNCH = re.compile(r"launch_slot_: id\s+\d+ \| task (\d+) \| processing task")
_RELEASE = re.compile(r"release: id\s+\d+ \| task (\d+) \| stop processing: n_tokens = (\d+), truncated = (\d+)")
_GIN = re.compile(r"\[GIN\] .*?\| (\d{3}) \|\s+([\d.]+)(µs|ms|s|m\d*\.?\d*s)? \|.*POST\s+\"/v1/chat/completions\"")
_CANCEL = re.compile(r"cancel task, id_task = (\d+)")


def _gin_seconds(value: str, unit: str | None) -> float:
    v = float(value)
    return {"µs": 1e-6, "ms": 1e-3, "s": 1.0}.get(unit or "s", 1.0) * v


def parse_journal(path: Path) -> dict:
    tasks: dict[int, dict] = {}
    gins = []
    loads = []
    if not path.exists():
        return {"tasks": [], "gin": [], "loads": []}
    for line in path.read_text(errors="replace").splitlines():
        m = _TS.match(line)
        if not m:
            continue
        ts = float(m.group(1))
        if (mm := _LAUNCH.search(line)):
            tasks.setdefault(int(mm.group(1)), {})["launch"] = ts
        elif (mm := _PROMPT.search(line)):
            t = tasks.setdefault(int(mm.group(1)), {})
            t["prompt_ms"], t["prompt_tokens"] = float(mm.group(2)), int(mm.group(3))
        elif (mm := _EVAL.search(line)):
            t = tasks.setdefault(int(mm.group(1)), {})
            t["eval_ms"], t["eval_tokens"] = float(mm.group(2)), int(mm.group(3))
        elif (mm := _RELEASE.search(line)):
            t = tasks.setdefault(int(mm.group(1)), {})
            t["release"], t["n_tokens"], t["truncated"] = ts, int(mm.group(2)), int(mm.group(3))
        elif (mm := _CANCEL.search(line)):
            tasks.setdefault(int(mm.group(1)), {})["cancelled"] = True
        elif "[GIN]" in line and "/v1/chat/completions" in line:
            g = re.search(r"\| (\d{3}) \|\s+([\d.]+)(µs|ms|s)\s+\|", line)
            if g:
                gins.append({"ts": ts, "status": int(g.group(1)), "latency_s": _gin_seconds(g.group(2), g.group(3))})
            else:
                g2 = re.search(r"\| (\d{3}) \|\s+(\d+)m([\d.]+)s\s+\|", line)
                if g2:
                    gins.append({"ts": ts, "status": int(g2.group(1)),
                                 "latency_s": int(g2.group(2)) * 60 + float(g2.group(3))})
        elif "loading model via llama-server" in line:
            loads.append({"start": ts})
        elif "llama_server: model loaded" in line and loads and "end" not in loads[-1]:
            loads[-1]["end"] = ts
            loads[-1]["load_s"] = ts - loads[-1]["start"]
    task_list = [dict(id=k, **v) for k, v in sorted(tasks.items()) if "launch" in v]
    # np=1: gorevler sirayla biter; her basarili GIN satiri en eski eslenmemis
    # tamamlanmis gorevle eslenir (FIFO). Sunucu ici bekleme = GIN gecikmesi -
    # (release - launch); Ollama'nin kendi kuyrugu + sablon/ayristirma suresi.
    pending = [t for t in task_list if "release" in t and not t.get("cancelled")]
    pending.sort(key=lambda t: t["release"])
    idx = 0
    for g in gins:
        if g["status"] != 200:
            continue
        while idx < len(pending) and pending[idx]["release"] > g["ts"] + 0.5:
            break
        if idx < len(pending):
            t = pending[idx]
            idx += 1
            t["gin_latency_s"] = g["latency_s"]
            t["gin_arrival"] = g["ts"] - g["latency_s"]
            t["server_wait_s"] = max(0.0, t["launch"] - t["gin_arrival"])
    return {"tasks": task_list, "gin": gins, "loads": loads}


# ---------------------------------------------------------------------------
# Tek tekrar
# ---------------------------------------------------------------------------

def req_status(e: dict) -> str:
    """HTTP basarili ama max_tokens'ta kesilen yanit (bolunup yeniden taranir) ayri sinif."""
    ok_att = [a for a in e.get("attempts", []) if a["status"] == "ok"]
    if e["status"] == "parse_error" and ok_att and ok_att[-1].get("finish_reason") == "length":
        return "truncated_split"
    return e["status"]


def load_events(rep_dir: Path) -> list[dict]:
    out = []
    for p in sorted(rep_dir.glob("server_events_*.jsonl")):
        with open(p) as fh:
            for line in fh:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def window_of(rep: dict, jobs: list[dict]) -> tuple[float, float, float, float]:
    """(mono0, mono1, wall0, wall1) olcum penceresi."""
    m0 = rep.get("measure_start_mono")
    w0 = rep.get("measure_start_wall")
    if rep.get("window_end_mono"):
        m1 = rep["window_end_mono"]
    else:
        m1 = rep.get("measure_end_mono") or max((j.get("t_end", 0) for j in jobs), default=m0)
    w1 = w0 + (m1 - m0) if w0 is not None and m0 is not None else None
    return m0, m1, w0, w1


def analyze_rep(rep_dir: Path) -> dict | None:
    rep_path = rep_dir / "rep.json"
    if not rep_path.exists():
        return None
    rep = json.loads(rep_path.read_text())
    cond = rep["condition"]
    jobs = [json.loads(line) for line in open(rep_dir / "jobs.jsonl")] if (rep_dir / "jobs.jsonl").exists() else []
    events = load_events(rep_dir)
    m0, m1, w0, w1 = window_of(rep, [j for j in jobs if not j["warmup"]])
    if m0 is None:
        return None
    duration = max(1e-6, m1 - m0)

    by_job: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        if e.get("job"):
            by_job[e["job"]].append(e)

    # ---- is satirlari --------------------------------------------------------
    job_rows = []
    for j in jobs:
        ev = by_job.get(j["project_name"], [])
        get = lambda name: [e for e in ev if e["ev"] == name]  # noqa: E731
        reg = get("job_registered")
        start = get("job_start")
        end = get("job_end")
        exp = get("export_stage_start")
        row = dict(j)
        row["rep_dir"] = str(rep_dir)
        if reg and start:
            row["app_queue_s"] = start[0]["t0"] - reg[0]["mono"]
        if start and end:
            row["server_job_s"] = end[0]["t1"] - start[0]["t0"]
            row["server_status"] = end[0]["status"]
            row["job_end_mono"] = end[0]["t1"]
        if end and j.get("t_job_done_seen"):
            row["completion_notice_delay_s"] = j["t_job_done_seen"] - end[0]["t1"]
        if exp and end:
            row["export_s"] = end[0]["t1"] - exp[0]["mono"]
        ob = get("orchestrator_build")
        row["orchestrator_build_s"] = sum(e["t1"] - e["t0"] for e in ob) if ob else None
        # Is basindan spaCy kurulumuna kadar gecen sure: baglam/run kaydi yazilirken
        # SQLite yazma kilidinde bekleme burada gorunur (fiili uygulama kuyrugu).
        if start and ob:
            row["startup_wait_s"] = ob[0]["t0"] - start[0]["t0"]
        ctxs = get("context_get_or_create")
        if ctxs:
            row["context_create_s"] = ctxs[0]["t1"] - ctxs[0]["t0"]
        if end:
            row["server_error_type"] = end[0].get("error_type")
            row["server_error_frames"] = end[0].get("error_frames")
            row["server_db_error"] = end[0].get("db_error")
        for name, key in (("detect_file", "detection_wall_s"), ("mask_file", "masking_wall_s"),
                          ("audit_llm_file", "audit_wall_s"), ("finalize_file", "finalize_wall_s"),
                          ("presidio_detect", "presidio_wall_s"), ("llm_detect_file", "llm_detect_wall_s")):
            spans = [(e["t0"], e["t1"]) for e in get(name)]
            row[key] = union_length(spans) if spans else None
        reqs = get("llm_request")
        attempts = [a for r in reqs for a in r.get("attempts", [])]
        row["llm_requests"] = len(reqs)
        row["llm_requests_detection"] = sum(r["phase"] == "detection" for r in reqs)
        row["llm_requests_audit"] = sum(r["phase"] == "audit" for r in reqs)
        row["llm_attempts"] = len(attempts)
        row["llm_failed_requests"] = sum(req_status(r) not in ("ok", "truncated_split") for r in reqs)
        row["llm_truncated_requests"] = sum(req_status(r) == "truncated_split" for r in reqs)
        row["llm_timeouts"] = sum(a["status"] == "timeout" for a in attempts)
        row["llm_http_errors"] = sum(a["status"].startswith("http_") for a in attempts)
        row["llm_http_busy_s"] = union_length([(a["t0"], a["t1"]) for a in attempts]) if attempts else None
        row["llm_admission_total_s"] = sum(r["admission_s"] for r in reqs)
        row["llm_prompt_tokens"] = sum(a.get("prompt_tokens") or 0 for a in attempts)
        row["llm_completion_tokens"] = sum(a.get("completion_tokens") or 0 for a in attempts)
        scans = get("llm_scan")
        row["llm_chunks_detection"] = sum(s["chunks"] for s in scans if s["phase"] == "detection")
        row["llm_chunks_audit"] = sum(s["chunks"] for s in scans if s["phase"] == "audit")
        row["db_lock_wait_s"] = sum(e["t1"] - e["t0"] for e in get("db_write_lock"))
        pa = get("presidio_analyze")
        row["presidio_lock_wait_s"] = sum(e["t_lock"] - e["t0"] for e in pa)
        job_rows.append(row)

    meas = [r for r in job_rows if not r["warmup"]]
    completed = [r for r in meas if r["outcome"] == "completed"]

    # ---- dosya / LLM istegi satirlari ---------------------------------------
    measured_jobs = {r["project_name"] for r in meas}
    file_rows, req_rows = [], []
    for e in events:
        if e.get("job") not in measured_jobs:
            continue
        if e["ev"] in ("detect_file", "mask_file", "audit_llm_file", "finalize_file", "presidio_detect",
                       "llm_detect_file", "rule_detect"):
            file_rows.append({"rep_dir": str(rep_dir), "job": e["job"], "stage": e["ev"], "file": e.get("file"),
                              "dur_s": e["t1"] - e["t0"], "ok": e.get("ok")})
        elif e["ev"] == "presidio_analyze":
            file_rows.append({"rep_dir": str(rep_dir), "job": e["job"], "stage": "presidio_lock_wait",
                              "file": None, "dur_s": e["t_lock"] - e["t0"], "ok": True})
        elif e["ev"] == "llm_request":
            att = e.get("attempts", [])
            ok_att = [a for a in att if a["status"] == "ok"]
            ct = ok_att[-1].get("completion_tokens") if ok_att else None
            http_last = (ok_att[-1]["t1"] - ok_att[-1]["t0"]) if ok_att else None
            status = req_status(e)
            req_rows.append({
                "rep_dir": str(rep_dir), "job": e["job"], "file": e.get("file"), "phase": e["phase"],
                "chunk": e["chunk"], "chunks": e["chunks"], "status": status, "error_type": e.get("error_type"),
                "total_s": e["t1"] - e["t0"], "admission_s": e["admission_s"], "http_s": e["http_s"],
                "attempts": len(att), "prompt_tokens": ok_att[-1].get("prompt_tokens") if ok_att else None,
                "completion_tokens": ct, "finish_reason": ok_att[-1].get("finish_reason") if ok_att else None,
                "out_tok_per_s_e2e": (ct / http_last) if ct and http_last else None,
                "t0": e["t0"], "t1": e["t1"], "wall0": e["wall0"], "payload_chars": e.get("payload_chars"),
            })

    # ---- sayac (gauge) ------------------------------------------------------
    per_sec: dict[int, dict] = defaultdict(lambda: defaultdict(int))
    rss_by_pid: dict[int, list[int]] = defaultdict(list)
    for e in events:
        if e["ev"] == "gauge" and w0 is not None and w0 <= e["wall"] <= w1:
            s = int(e["wall"])
            for k in ("active_jobs", "llm_inflight", "llm_waiting", "presidio_waiting", "db_lock_waiting"):
                per_sec[s][k] += e[k]
            rss_by_pid[e["pid"]].append(e["rss"])
    gauge_max = {k: max((v[k] for v in per_sec.values()), default=0)
                 for k in ("active_jobs", "llm_inflight", "llm_waiting", "presidio_waiting", "db_lock_waiting")}
    gauge_mean = {k: statistics.fmean([v[k] for v in per_sec.values()]) if per_sec else None
                  for k in ("active_jobs", "llm_inflight", "llm_waiting")}

    # ---- donanim ------------------------------------------------------------
    hw = rep_dir / "hw"
    in_win = lambda r: w0 is not None and w0 <= float(r["wall"]) <= w1  # noqa: E731
    gpu_rows = [r for r in read_csv(hw / "gpu.csv") if r.get("util_gpu") not in ("", None) and in_win(r)]
    gpu_info = json.loads((hw / "gpu_info.json").read_text()) if (hw / "gpu_info.json").exists() else {"gpus": []}
    gpus = {}
    for g in gpu_info["gpus"]:
        rows = [r for r in gpu_rows if int(r["gpu"]) == g["index"]]
        util = [fnum(r["util_gpu"]) for r in rows]
        mem = [fnum(r["mem_used_mib"]) for r in rows]
        thr = defaultdict(int)
        for r in rows:
            for t in r["throttle"].split("|"):
                thr[t] += 1
        gpus[g["index"]] = {
            "name": g["name"], "uuid": g["uuid"], "mem_total_mib": g["memory_total_mib"], "samples": len(rows),
            "util_mean": statistics.fmean(util) if util else None, "util_p95": pct(util, 95),
            "util_max": max(util) if util else None,
            "mem_used_mean_mib": statistics.fmean(mem) if mem else None, "mem_used_max_mib": max(mem) if mem else None,
            "temp_mean": statistics.fmean([fnum(r["temp_c"]) for r in rows]) if rows else None,
            "temp_max": max((fnum(r["temp_c"]) for r in rows), default=None),
            "power_mean_w": statistics.fmean([fnum(r["power_w"]) for r in rows]) if rows else None,
            "power_max_w": max((fnum(r["power_w"]) for r in rows), default=None),
            "sm_clock_mean": statistics.fmean([fnum(r["sm_clock_mhz"]) for r in rows]) if rows else None,
            "sm_clock_max": max((fnum(r["sm_clock_mhz"]) for r in rows), default=None),
            "throttle_samples": dict(thr),
        }
    sys_rows = [r for r in read_csv(hw / "sys.csv") if in_win(r)]
    proc_rows = [r for r in read_csv(hw / "proc.csv") if in_win(r)]
    backend_rss_by_t: dict[str, float] = defaultdict(float)
    backend_cpu_by_t: dict[str, float] = defaultdict(float)
    llama_rss, llama_cpu = [], []
    for r in proc_rows:
        if r["role"].startswith("backend"):
            backend_rss_by_t[r["wall"]] += fnum(r["rss_mib"]) or 0
            backend_cpu_by_t[r["wall"]] += fnum(r["cpu_pct"]) or 0
        elif r["role"] == "llama-server":
            llama_rss.append(fnum(r["rss_mib"]))
            llama_cpu.append(fnum(r["cpu_pct"]))
    sysinfo = {
        "cpu_mean": statistics.fmean([fnum(r["cpu_pct"]) for r in sys_rows]) if sys_rows else None,
        "cpu_p95": pct([fnum(r["cpu_pct"]) for r in sys_rows], 95),
        "cpu_max": max((fnum(r["cpu_pct"]) for r in sys_rows), default=None),
        "ram_used_max_mib": max((fnum(r["mem_used_mib"]) for r in sys_rows), default=None),
        "ram_avail_min_mib": min((fnum(r["mem_available_mib"]) for r in sys_rows), default=None),
        "swap_used_max_mib": max((fnum(r["swap_used_mib"]) for r in sys_rows), default=None),
        "disk_read_mean_bps": statistics.fmean([fnum(r["disk_read_bps"]) for r in sys_rows]) if sys_rows else None,
        "disk_write_mean_bps": statistics.fmean([fnum(r["disk_write_bps"]) for r in sys_rows]) if sys_rows else None,
        "backend_rss_max_mib": max(backend_rss_by_t.values(), default=None),
        "backend_cpu_mean_pct": statistics.fmean(backend_cpu_by_t.values()) if backend_cpu_by_t else None,
        "backend_cpu_max_pct": max(backend_cpu_by_t.values(), default=None),
        "llama_rss_max_mib": max((v for v in llama_rss if v is not None), default=None),
        "llama_cpu_mean_pct": statistics.fmean([v for v in llama_cpu if v is not None]) if llama_cpu else None,
    }
    slot_rows = [r for r in read_csv(hw / "slots.csv") if in_win(r) and r.get("slots_processing") not in ("", None)]
    slot_busy = [fnum(r["slots_processing"]) for r in slot_rows]
    slots = {
        "samples": len(slot_rows), "unreachable_samples": sum(1 for r in read_csv(hw / "slots.csv")
                                                              if in_win(r) and r.get("is_processing") == "unreachable"),
        "busy_fraction": (sum(1 for v in slot_busy if v and v > 0) / len(slot_busy)) if slot_busy else None,
        "max_processing": max(slot_busy, default=None),
        "slots_total": max((fnum(r["slots_total"]) for r in slot_rows), default=None),
    }

    # ---- llama-server gorevleri ---------------------------------------------
    jr = parse_journal(rep_dir / "ollama_journal.log")
    tasks = [t for t in jr["tasks"] if w0 is not None and w0 <= t["launch"] <= w1]
    gins = [g for g in jr["gin"] if w0 is not None and w0 <= g["ts"] <= w1 + 1]
    task_rows = []
    for t in tasks:
        row = {"rep_dir": str(rep_dir), **t}
        if t.get("eval_tokens") and t.get("eval_ms"):
            row["tpot_ms"] = t["eval_ms"] / t["eval_tokens"]
            row["decode_tok_s"] = t["eval_tokens"] / (t["eval_ms"] / 1000)
        if t.get("prompt_tokens") and t.get("prompt_ms"):
            row["prefill_tok_s"] = t["prompt_tokens"] / (t["prompt_ms"] / 1000)
        task_rows.append(row)
    our_attempts = [a for r in req_rows for a in range(r["attempts"])]
    our_attempts_in_win = sum(r["attempts"] for r in req_rows if w0 <= r["wall0"] <= w1)
    server = {
        "tasks": len(tasks),
        "gin_chat_requests": len(gins),
        "gin_non200": sum(g["status"] != 200 for g in gins),
        "truncated_tasks": sum(1 for t in tasks if t.get("truncated")),
        "cancelled_tasks": sum(1 for t in tasks if t.get("cancelled")),
        "prompt_tokens": sum(t.get("prompt_tokens", 0) for t in tasks),
        "eval_tokens": sum(t.get("eval_tokens", 0) for t in tasks),
        "ctx_fill_max": max((t.get("n_tokens", 0) for t in tasks), default=None),
        "foreign_requests_estimate": max(0, len(gins) - our_attempts_in_win),
        "model_loads": [l for l in jr["loads"] if w0 is not None and rep.get("rep_start_wall", w0) - 1 <= l["start"] <= w1],
    }
    server["total_tok_s"] = (server["prompt_tokens"] + server["eval_tokens"]) / duration
    server["gen_tok_s"] = server["eval_tokens"] / duration

    # ---- verim ---------------------------------------------------------------
    ok_reqs_in_win = [r for r in req_rows if r["status"] in ("ok", "truncated_split") and m0 <= r["t1"] <= m1]
    files_done = sum(r["file_count"] for r in completed)
    outcome_counts = defaultdict(int)
    for r in meas:
        outcome_counts[r["outcome"]] += 1
    summary = {
        "condition": cond["cid"], "scenario": cond["scenario"], "group": cond.get("group"), "rep": rep["rep"],
        "users": cond["users"], "sizes": "/".join(cond["sizes"]), "backend_workers": cond["backend_workers"],
        "file_batch": cond["file_batch"], "llm_conc": cond["llm_conc"], "window_s": duration,
        "submitted": len(meas), "completed": len(completed),
        "complete_outputs": sum(r.get("output_quality") == "complete" for r in completed),
        "partial_outputs": sum(r.get("output_quality") == "partial" for r in completed),
        "unverified_outputs": sum(not r.get("output_validated") for r in completed),
        "invalid_outputs": outcome_counts.get("invalid_output", 0),
        "failed": sum(1 for r in meas if r["outcome"] == "job_failed"),
        "pending_at_window_end": outcome_counts.get("completed_after_window", 0)
                                 + outcome_counts.get("test_deadline_incomplete", 0),
        "http_errors": sum(1 for r in meas if "http_" in r["outcome"] or "conn_error" in r["outcome"]),
        "timeouts": sum(1 for r in meas if "timeout" in r["outcome"] or "deadline" in r["outcome"]),
        "aborted": sum(1 for r in meas if r["outcome"].startswith("aborted")),
        "outcomes": dict(outcome_counts),
        "llm_timeouts": sum(r["llm_timeouts"] for r in meas),
        "llm_http_errors": sum(r["llm_http_errors"] for r in meas),
        "llm_failed_requests": sum(r["llm_failed_requests"] for r in meas),
        "files_security_quarantine": sum(r.get("files_security_quarantine") or 0 for r in completed),
        "files_review_required": sum(r.get("files_review_required") or 0 for r in completed),
        "files_validation_failed": sum(r.get("files_validation_failed") or 0 for r in completed),
        "files_blocked_technical": sum(r.get("files_blocked_technical") or 0 for r in completed),
        "files_ready": sum(r.get("files_ready") or 0 for r in completed),
        "projects_per_min": len(completed) / duration * 60,
        "files_per_s": files_done / duration,
        "llm_req_per_s": len(ok_reqs_in_win) / duration,
        "gauge_max": gauge_max, "gauge_mean": gauge_mean,
        "gpus": gpus, "sys": sysinfo, "slots": slots, "server": server,
        "first_llm_http_s": (min(req_rows, key=lambda q: q["t0"])["http_s"] if req_rows else None),
        "first_job_e2e_s": (min(completed, key=lambda j: j["t_submit"]).get("e2e_s") if completed else None),
        "isolation_ok": rep.get("isolation", {}).get("ok"),
        "isolation_problems": rep.get("isolation", {}).get("problems"),
        "abort_reason": rep.get("abort_reason"), "runner_error": rep.get("runner_error"),
        "model_loaded_at_start": rep.get("model_loaded_at_start"),
        "backend_start_s": rep.get("backend_start_s"),
        "measure_start_wall": w0, "measure_end_wall": w1,
    }
    return {"summary": summary, "jobs": job_rows, "files": file_rows, "reqs": req_rows, "tasks": task_rows,
            "per_sec": per_sec, "rep_dir": rep_dir}


# ---------------------------------------------------------------------------
# Kosul birlestirme
# ---------------------------------------------------------------------------

def combine(reps: list[dict]) -> dict:
    s0 = reps[0]["summary"]
    jobs = [j for r in reps for j in r["jobs"] if not j["warmup"]]
    ok = [j for j in jobs if j["outcome"] == "completed"]
    notok = [j for j in jobs if j["outcome"] != "completed"]
    reqs = [q for r in reps for q in r["reqs"]]
    ok_reqs = [q for q in reqs if q["status"] == "ok"]
    bad_reqs = [q for q in reqs if q["status"] not in ("ok", "truncated_split")]
    trunc_reqs = [q for q in reqs if q["status"] == "truncated_split"]
    files = [f for r in reps for f in r["files"]]
    tasks = [t for r in reps for t in r["tasks"]]
    fstage = lambda name: [f["dur_s"] for f in files if f["stage"] == name]  # noqa: E731
    per_rep = lambda key: [r["summary"][key] for r in reps]  # noqa: E731
    out = {
        "condition": s0["condition"], "scenario": s0["scenario"], "group": s0["group"], "users": s0["users"],
        "sizes": s0["sizes"], "backend_workers": s0["backend_workers"], "file_batch": s0["file_batch"],
        "llm_conc": s0["llm_conc"], "reps": len(reps),
        "submitted": sum(per_rep("submitted")), "completed": sum(per_rep("completed")),
        "complete_outputs": sum(per_rep("complete_outputs")),
        "partial_outputs": sum(per_rep("partial_outputs")),
        "unverified_outputs": sum(per_rep("unverified_outputs")),
        "invalid_outputs": sum(per_rep("invalid_outputs")),
        "failed": sum(per_rep("failed")), "pending_at_window_end": sum(per_rep("pending_at_window_end")),
        "http_errors": sum(per_rep("http_errors")), "timeouts": sum(per_rep("timeouts")),
        "aborted": sum(per_rep("aborted")),
        "llm_timeouts": sum(per_rep("llm_timeouts")), "llm_http_errors": sum(per_rep("llm_http_errors")),
        "llm_failed_requests": sum(per_rep("llm_failed_requests")),
        "files_security_quarantine": sum(per_rep("files_security_quarantine")),
        "files_review_required": sum(per_rep("files_review_required")),
        "files_validation_failed": sum(per_rep("files_validation_failed")),
        "files_blocked_technical": sum(per_rep("files_blocked_technical")),
        "files_ready": sum(per_rep("files_ready")),
        "isolation_ok": all(per_rep("isolation_ok")),
        "abort_reasons": [x for x in per_rep("abort_reason") if x],
        "runner_errors": [x for x in per_rep("runner_error") if x],
        "projects_per_min": stats(per_rep("projects_per_min")),
        "files_per_s": stats(per_rep("files_per_s")),
        "llm_req_per_s": stats(per_rep("llm_req_per_s")),
        "e2e_ok": stats([j.get("e2e_s") for j in ok]),
        "first_llm_http": stats(per_rep("first_llm_http_s")),
        "model_load_s": stats([l.get("load_s") for r in reps for l in r["summary"]["server"]["model_loads"]]),
        "first_job_e2e": stats(per_rep("first_job_e2e_s")),
        "e2e_not_ok": stats([j["t_end"] - j["t_submit"] for j in notok]),
        "upload": stats([j.get("upload_s") for j in jobs]),
        "app_queue": stats([j.get("app_queue_s") for j in jobs]),
        "server_job": stats([j.get("server_job_s") for j in ok]),
        "detection_wall": stats([j.get("detection_wall_s") for j in ok]),
        "llm_http_busy_per_job": stats([j.get("llm_http_busy_s") for j in ok]),
        "llm_admission_per_job": stats([j.get("llm_admission_total_s") for j in ok]),
        "masking_wall": stats([j.get("masking_wall_s") for j in ok]),
        "audit_wall": stats([j.get("audit_wall_s") for j in ok]),
        "finalize_wall": stats([j.get("finalize_wall_s") for j in ok]),
        "export": stats([j.get("export_s") for j in ok]),
        "download": stats([j.get("download_s") for j in ok]),
        "completion_notice_delay": stats([j.get("completion_notice_delay_s") for j in ok]),
        "orchestrator_build": stats([j.get("orchestrator_build_s") for j in ok]),
        "startup_wait": stats([j.get("startup_wait_s") for j in jobs]),
        "failure_causes": sorted({f"{j.get('outcome')}:{j.get('server_error_type')}:{j.get('server_db_error')}"
                                  for j in notok}),
        "db_lock_wait_per_job": stats([j.get("db_lock_wait_s") for j in ok]),
        "presidio_lock_wait_per_job": stats([j.get("presidio_lock_wait_s") for j in ok]),
        "file_detect": stats(fstage("detect_file")), "file_mask": stats(fstage("mask_file")),
        "file_audit": stats(fstage("audit_llm_file")), "file_finalize": stats(fstage("finalize_file")),
        "file_presidio": stats(fstage("presidio_detect")), "file_presidio_lock_wait": stats(fstage("presidio_lock_wait")),
        "llm_req_total_ok": stats([q["total_s"] for q in ok_reqs]),
        "llm_req_http_ok": stats([q["http_s"] for q in ok_reqs]),
        "llm_req_admission": stats([q["admission_s"] for q in reqs]),
        "llm_req_total_failed": stats([q["total_s"] for q in bad_reqs]),
        "llm_req_total_truncated": stats([q["total_s"] for q in trunc_reqs]),
        "llm_truncated_requests": len(trunc_reqs),
        "llm_requests_total": len(reqs),
        "llm_req_out_tok_s": stats([q["out_tok_per_s_e2e"] for q in ok_reqs]),
        "llm_prompt_tokens": stats([q["prompt_tokens"] for q in ok_reqs]),
        "llm_completion_tokens": stats([q["completion_tokens"] for q in ok_reqs]),
        "llm_requests_per_job": stats([j.get("llm_requests") for j in ok]),
        "llm_chunks_detection_per_job": stats([j.get("llm_chunks_detection") for j in ok]),
        "llm_chunks_audit_per_job": stats([j.get("llm_chunks_audit") for j in ok]),
        "srv_prefill_ms": stats([t.get("prompt_ms") for t in tasks]),
        "srv_tpot_ms": stats([t.get("tpot_ms") for t in tasks]),
        "srv_decode_tok_s": stats([t.get("decode_tok_s") for t in tasks]),
        "srv_prefill_tok_s": stats([t.get("prefill_tok_s") for t in tasks]),
        "srv_wait_s": stats([t.get("server_wait_s") for t in tasks]),
        "srv_total_tok_s": stats([r["summary"]["server"]["total_tok_s"] for r in reps]),
        "srv_gen_tok_s": stats([r["summary"]["server"]["gen_tok_s"] for r in reps]),
        "srv_ctx_fill_max": max((r["summary"]["server"]["ctx_fill_max"] or 0 for r in reps), default=None),
        "srv_truncated": sum(r["summary"]["server"]["truncated_tasks"] for r in reps),
        "srv_non200": sum(r["summary"]["server"]["gin_non200"] for r in reps),
        "srv_foreign_requests": sum(r["summary"]["server"]["foreign_requests_estimate"] for r in reps),
        "slots_busy_fraction": stats([r["summary"]["slots"]["busy_fraction"] for r in reps]),
        "slots_max_processing": max((r["summary"]["slots"]["max_processing"] or 0 for r in reps), default=None),
        "gauge_llm_inflight_max": max(r["summary"]["gauge_max"]["llm_inflight"] for r in reps),
        "gauge_llm_waiting_max": max(r["summary"]["gauge_max"]["llm_waiting"] for r in reps),
        "gauge_active_jobs_max": max(r["summary"]["gauge_max"]["active_jobs"] for r in reps),
        "gauge_presidio_waiting_max": max(r["summary"]["gauge_max"]["presidio_waiting"] for r in reps),
        "cpu_mean": stats([r["summary"]["sys"]["cpu_mean"] for r in reps]),
        "cpu_max": max((r["summary"]["sys"]["cpu_max"] or 0 for r in reps), default=None),
        "ram_used_max_mib": max((r["summary"]["sys"]["ram_used_max_mib"] or 0 for r in reps), default=None),
        "swap_used_max_mib": max((r["summary"]["sys"]["swap_used_max_mib"] or 0 for r in reps), default=None),
        "backend_rss_max_mib": max((r["summary"]["sys"]["backend_rss_max_mib"] or 0 for r in reps), default=None),
        "backend_cpu_mean_pct": stats([r["summary"]["sys"]["backend_cpu_mean_pct"] for r in reps]),
        "llama_rss_max_mib": max((r["summary"]["sys"]["llama_rss_max_mib"] or 0 for r in reps), default=None),
        "disk_write_mean_bps": stats([r["summary"]["sys"]["disk_write_mean_bps"] for r in reps]),
        "gpus": {},
    }
    for idx in reps[0]["summary"]["gpus"]:
        gs = [r["summary"]["gpus"][idx] for r in reps if idx in r["summary"]["gpus"]]
        thr = defaultdict(int)
        for g in gs:
            for k, v in g["throttle_samples"].items():
                thr[k] += v
        out["gpus"][idx] = {
            "name": gs[0]["name"], "uuid": gs[0]["uuid"], "mem_total_mib": gs[0]["mem_total_mib"],
            "samples": sum(g["samples"] for g in gs),
            "util_mean": statistics.fmean([g["util_mean"] for g in gs if g["util_mean"] is not None]) if gs else None,
            "util_p95": max((g["util_p95"] or 0 for g in gs), default=None),
            "util_max": max((g["util_max"] or 0 for g in gs), default=None),
            "mem_used_mean_mib": statistics.fmean([g["mem_used_mean_mib"] for g in gs if g["mem_used_mean_mib"]]) if gs else None,
            "mem_used_max_mib": max((g["mem_used_max_mib"] or 0 for g in gs), default=None),
            "temp_max": max((g["temp_max"] or 0 for g in gs), default=None),
            "temp_mean": statistics.fmean([g["temp_mean"] for g in gs if g["temp_mean"]]) if gs else None,
            "power_mean_w": statistics.fmean([g["power_mean_w"] for g in gs if g["power_mean_w"]]) if gs else None,
            "power_max_w": max((g["power_max_w"] or 0 for g in gs), default=None),
            "sm_clock_mean": statistics.fmean([g["sm_clock_mean"] for g in gs if g["sm_clock_mean"]]) if gs else None,
            "sm_clock_max": max((g["sm_clock_max"] or 0 for g in gs), default=None),
            "throttle_samples": dict(thr),
        }
    return out


# ---------------------------------------------------------------------------
# Cikti
# ---------------------------------------------------------------------------

def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    keys: list[str] = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                        for k, v in r.items()})


def flat_condition(c: dict) -> dict:
    row = {k: v for k, v in c.items() if not isinstance(v, dict)}
    for k, v in c.items():
        if isinstance(v, dict) and k != "gpus":
            for sk, sv in v.items():
                row[f"{k}.{sk}"] = sv
    for idx, g in c["gpus"].items():
        for sk, sv in g.items():
            row[f"gpu{idx}.{sk}"] = sv
    return row


def charts(campaign: Path, conds: list[dict], reps_by_cond: dict) -> None:
    out = campaign / "charts"
    out.mkdir(exist_ok=True)
    scen = defaultdict(list)
    for c in conds:
        key = c["scenario"] if c["scenario"] != "workers" else c["group"] + f" bw{c['backend_workers']} fb{c['file_batch']} lc{c['llm_conc']}"
        scen[key].append(c)

    def line_chart(fname, title, ylabel, getter, keys=None, logy=False):
        fig, ax = plt.subplots(figsize=(8, 4.8))
        for name, cs in sorted(scen.items()):
            if keys and not any(name.startswith(k) for k in keys):
                continue
            cs = sorted(cs, key=lambda c: c["users"])
            xs = [c["users"] for c in cs]
            ys = [getter(c) for c in cs]
            if all(y is None for y in ys):
                continue
            ax.plot(xs, ys, marker="o", label=name)
        ax.set_title(title)
        ax.set_xlabel("Eszamanli kullanici")
        ax.set_ylabel(ylabel)
        if logy:
            ax.set_yscale("log")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(out / fname, dpi=110)
        plt.close(fig)

    line_chart("e2e_p50.png", "Uctan uca sure p50 (basarili isler)", "saniye", lambda c: c["e2e_ok"]["p50"])
    line_chart("e2e_p95.png", "Uctan uca sure p95 (basarili isler; n<20 ise p95=max'a yakin)", "saniye",
               lambda c: c["e2e_ok"]["p95"])
    line_chart("throughput.png", "Islem kapasitesi (3 tekrar ortalamasi)", "proje / dakika",
               lambda c: c["projects_per_min"]["mean"])
    line_chart("llm_admission_p95.png", "Uygulama LLM kuyrugu bekleme p95 (istek basina)", "saniye",
               lambda c: c["llm_req_admission"]["p95"])
    for idx in ("0", "1", 0, 1):
        if not conds or idx not in conds[0]["gpus"]:
            continue
        name = conds[0]["gpus"][idx]["name"]
        line_chart(f"gpu{idx}_vram_max.png", f"GPU{idx} ({name}) VRAM maksimum", "MiB",
                   lambda c, i=idx: c["gpus"][i]["mem_used_max_mib"])
        line_chart(f"gpu{idx}_util_mean.png", f"GPU{idx} ({name}) kullanim ortalamasi (orneklenmis)", "%",
                   lambda c, i=idx: c["gpus"][i]["util_mean"])
    line_chart("backend_rss_max.png", "Backend surec(ler)i toplam RSS maksimum", "MiB",
               lambda c: c["backend_rss_max_mib"])

    # zaman cizelgesi: her senaryonun en yuksek kullanici sayili ilk tekrari
    for name, cs in scen.items():
        c = max(cs, key=lambda c: c["users"])
        rep = reps_by_cond[c["condition"]][0]
        rep_dir = rep["rep_dir"]
        w0 = rep["summary"]["measure_start_wall"]
        w1 = rep["summary"]["measure_end_wall"]
        if w0 is None:
            continue
        gpu = read_csv(rep_dir / "hw" / "gpu.csv")
        fig, axes = plt.subplots(3, 1, figsize=(10, 7.5), sharex=True)
        for idx in sorted({r["gpu"] for r in gpu}):
            rows = [r for r in gpu if r["gpu"] == idx and r["util_gpu"] not in ("", None)]
            t = [float(r["wall"]) - w0 for r in rows]
            axes[0].plot(t, [fnum(r["util_gpu"]) for r in rows], lw=0.8, label=f"GPU{idx} kullanim %")
            axes[1].plot(t, [fnum(r["mem_used_mib"]) for r in rows], lw=0.8, label=f"GPU{idx} VRAM MiB")
        ps = rep["per_sec"]
        secs = sorted(ps)
        t = [s - w0 for s in secs]
        axes[2].step(t, [ps[s]["active_jobs"] for s in secs], where="post", label="aktif is")
        axes[2].step(t, [ps[s]["llm_inflight"] for s in secs], where="post", label="ucustaki LLM istegi")
        axes[2].step(t, [ps[s]["llm_waiting"] for s in secs], where="post", label="LLM kuyrugunda bekleyen")
        for ax in axes:
            ax.axvline(0, color="k", lw=0.5)
            ax.axvline(w1 - w0, color="k", lw=0.5, ls="--")
            ax.legend(fontsize=7, loc="upper right")
            ax.grid(alpha=0.3)
        axes[2].set_xlabel("olcum baslangicindan itibaren saniye")
        fig.suptitle(f"Zaman cizelgesi: {c['condition']} rep1")
        fig.tight_layout()
        fig.savefig(out / f"timeline_{c['condition']}.png", dpi=110)
        plt.close(fig)


def analyze_campaign(campaign: Path) -> list[dict]:
    reps_by_cond: dict[str, list[dict]] = defaultdict(list)
    for rep_dir in sorted(campaign.glob("*/rep*")):
        if not (rep_dir / "DONE").exists():
            continue
        res = analyze_rep(rep_dir)
        if res:
            reps_by_cond[res["summary"]["condition"]].append(res)
    order = json.loads((campaign / "environment.json").read_text())["conditions"]
    cids = [c["cid"] for c in order if c["cid"] in reps_by_cond]
    conds = [combine(reps_by_cond[cid]) for cid in cids]
    write_csv(campaign / "summary_conditions.csv", [flat_condition(c) for c in conds])
    (campaign / "summary_conditions.json").write_text(json.dumps(conds, indent=2, ensure_ascii=False, default=str))
    write_csv(campaign / "summary_reps.csv", [
        {k: v for k, v in r["summary"].items()} for cid in cids for r in reps_by_cond[cid]])
    write_csv(campaign / "jobs_all.csv", [j for cid in cids for r in reps_by_cond[cid] for j in r["jobs"]])
    write_csv(campaign / "llm_requests_all.csv", [q for cid in cids for r in reps_by_cond[cid] for q in r["reqs"]])
    write_csv(campaign / "files_all.csv", [f for cid in cids for r in reps_by_cond[cid] for f in r["files"]])
    write_csv(campaign / "server_tasks_all.csv", [t for cid in cids for r in reps_by_cond[cid] for t in r["tasks"]])
    hw_rows = []
    for cid in cids:
        for r in reps_by_cond[cid]:
            for idx, g in r["summary"]["gpus"].items():
                hw_rows.append({"condition": cid, "rep": r["summary"]["rep"], "gpu": idx, **g})
    write_csv(campaign / "hw_summary.csv", hw_rows)
    charts(campaign, conds, reps_by_cond)
    return conds


def main() -> None:
    for arg in sys.argv[1:]:
        conds = analyze_campaign(Path(arg))
        print(f"{arg}: {len(conds)} kosul analiz edildi")


if __name__ == "__main__":
    main()
