"""Saniyelik donanim/surec/LLM-sunucu ornekleyicisi.

    loadtest/.venv/bin/python -m loadtest.hwmon --out DIR [--interval 1.0]

SIGTERM/SIGINT ile durur. Uretilen dosyalar (her satirda wall=epoch sn ve
mono=time.monotonic(); ayni makinedeki is olaylariyla dogrudan eslesir):

  gpu_info.json   GPU model adi, UUID, toplam VRAM, surucu surumu
  gpu.csv         GPU basina: kullanim %, bellek-denetleyici %, VRAM, sicaklik,
                  guc, SM/bellek saat hizi, throttling nedenleri, P-state
  sys.csv         CPU % (toplam), load1, RAM, swap, disk okuma/yazma bayt/sn
  proc.csv        test backend surecleri + llama-server + ollama: CPU %, RSS, thread
  slots.csv       llama-server /slots: calisan slot sayisi, slot basina token sayaclari
  ollama_ps.csv   Ollama /api/ps: yuklu model ve VRAM boyutu

NVML'in GPU kullanim yuzdesi, surucunun son ornekleme penceresinde (GPU'ya
gore 1/6 sn - 1 sn) en az bir kernel calisan zaman oranidir; yani
ORNEKLENMIS bir olcumdur, hesaplama doluluğu degildir.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import signal
import time
from pathlib import Path

import httpx
import psutil
import pynvml

THROTTLE_BITS = {
    0x1: "gpu_idle", 0x2: "app_clocks_setting", 0x4: "sw_power_cap", 0x8: "hw_slowdown",
    0x10: "sync_boost", 0x20: "sw_thermal", 0x40: "hw_thermal", 0x80: "hw_power_brake",
    0x100: "display_clocks",
}

_stop = False


def _handle(*_):
    global _stop
    _stop = True


def throttle_names(mask: int) -> str:
    return "|".join(name for bit, name in THROTTLE_BITS.items() if mask & bit) or "none"


def find_processes() -> dict[int, str]:
    found = {}
    for p in psutil.process_iter(["pid", "cmdline", "name"]):
        cmd = " ".join(p.info.get("cmdline") or [])
        if "instrumented_server.py" in cmd:
            port = re.search(r"--port\s+(\d+)", cmd)
            found[p.info["pid"]] = f"backend:{port.group(1) if port else '?'}"
        elif "llama-server" in cmd:
            found[p.info["pid"]] = "llama-server"
        elif cmd.endswith("ollama serve"):
            found[p.info["pid"]] = "ollama"
    return found


def llama_port() -> int | None:
    for p in psutil.process_iter(["cmdline"]):
        cmd = p.info.get("cmdline") or []
        if any("llama-server" in c for c in cmd) and "--port" in cmd:
            return int(cmd[cmd.index("--port") + 1])
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--ollama", default="http://127.0.0.1:11434")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)

    pynvml.nvmlInit()
    handles = [pynvml.nvmlDeviceGetHandleByIndex(i) for i in range(pynvml.nvmlDeviceGetCount())]
    info = {"driver": pynvml.nvmlSystemGetDriverVersion(), "gpus": []}
    for i, h in enumerate(handles):
        info["gpus"].append({
            "index": i, "name": pynvml.nvmlDeviceGetName(h), "uuid": pynvml.nvmlDeviceGetUUID(h),
            "memory_total_mib": pynvml.nvmlDeviceGetMemoryInfo(h).total / 2**20,
            "power_limit_w": pynvml.nvmlDeviceGetEnforcedPowerLimit(h) / 1000,
            "pci_bus_id": pynvml.nvmlDeviceGetPciInfo(h).busId,
        })
    (out / "gpu_info.json").write_text(json.dumps(info, indent=2))

    files = {name: open(out / f"{name}.csv", "w", newline="") for name in ("gpu", "sys", "proc", "slots", "ollama_ps")}
    w = {name: csv.writer(fh) for name, fh in files.items()}
    w["gpu"].writerow(["wall", "mono", "gpu", "uuid", "util_gpu", "util_mem", "mem_used_mib", "mem_total_mib",
                       "temp_c", "power_w", "sm_clock_mhz", "mem_clock_mhz", "pstate", "throttle"])
    w["sys"].writerow(["wall", "mono", "cpu_pct", "load1", "mem_used_mib", "mem_available_mib", "swap_used_mib",
                       "disk_read_bps", "disk_write_bps"])
    w["proc"].writerow(["wall", "mono", "pid", "role", "cpu_pct", "rss_mib", "threads"])
    w["slots"].writerow(["wall", "mono", "port", "slots_total", "slots_processing", "slot_id", "id_task",
                         "n_ctx", "n_prompt_tokens", "n_prompt_tokens_processed", "n_decoded", "is_processing"])
    w["ollama_ps"].writerow(["wall", "mono", "model", "size_vram", "context_length", "expires_at"])

    procs: dict[int, psutil.Process] = {}
    roles: dict[int, str] = {}
    last_disk = psutil.disk_io_counters()
    last_t = time.monotonic()
    psutil.cpu_percent(None)
    client = httpx.Client(timeout=0.8)
    port = llama_port()
    tick = 0
    next_t = time.monotonic()
    while not _stop:
        wall, mono = time.time(), time.monotonic()
        for i, h in enumerate(handles):
            try:
                util = pynvml.nvmlDeviceGetUtilizationRates(h)
                mem = pynvml.nvmlDeviceGetMemoryInfo(h)
                try:
                    reasons = pynvml.nvmlDeviceGetCurrentClocksEventReasons(h)
                except Exception:  # eski surucu
                    reasons = pynvml.nvmlDeviceGetCurrentClocksThrottleReasons(h)
                w["gpu"].writerow([
                    f"{wall:.3f}", f"{mono:.3f}", i, info["gpus"][i]["uuid"], util.gpu, util.memory,
                    f"{mem.used / 2**20:.0f}", f"{mem.total / 2**20:.0f}",
                    pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU),
                    f"{pynvml.nvmlDeviceGetPowerUsage(h) / 1000:.1f}",
                    pynvml.nvmlDeviceGetClockInfo(h, pynvml.NVML_CLOCK_SM),
                    pynvml.nvmlDeviceGetClockInfo(h, pynvml.NVML_CLOCK_MEM),
                    pynvml.nvmlDeviceGetPerformanceState(h), throttle_names(reasons),
                ])
            except pynvml.NVMLError as exc:
                w["gpu"].writerow([f"{wall:.3f}", f"{mono:.3f}", i, "", "", "", "", "", "", "", "", "", "", f"nvml_error:{exc}"])

        vm, sw = psutil.virtual_memory(), psutil.swap_memory()
        disk = psutil.disk_io_counters()
        dt = max(1e-6, mono - last_t)
        w["sys"].writerow([
            f"{wall:.3f}", f"{mono:.3f}", psutil.cpu_percent(None), f"{psutil.getloadavg()[0]:.2f}",
            f"{(vm.total - vm.available) / 2**20:.0f}", f"{vm.available / 2**20:.0f}", f"{sw.used / 2**20:.0f}",
            f"{(disk.read_bytes - last_disk.read_bytes) / dt:.0f}", f"{(disk.write_bytes - last_disk.write_bytes) / dt:.0f}",
        ])
        last_disk, last_t = disk, mono

        if tick % 5 == 0:
            current = find_processes()
            for pid, role in current.items():
                if pid not in procs:
                    try:
                        procs[pid] = psutil.Process(pid)
                        procs[pid].cpu_percent(None)
                        roles[pid] = role
                    except psutil.Error:
                        pass
            port = llama_port() or port
        for pid, proc in list(procs.items()):
            try:
                with proc.oneshot():
                    w["proc"].writerow([f"{wall:.3f}", f"{mono:.3f}", pid, roles[pid], proc.cpu_percent(None),
                                        f"{proc.memory_info().rss / 2**20:.0f}", proc.num_threads()])
            except psutil.Error:
                procs.pop(pid, None)

        if port:
            try:
                slots = client.get(f"http://127.0.0.1:{port}/slots").json()
                busy = sum(1 for s in slots if s.get("is_processing"))
                for s in slots:
                    nt = (s.get("next_token") or [{}])[0]
                    w["slots"].writerow([f"{wall:.3f}", f"{mono:.3f}", port, len(slots), busy, s.get("id"),
                                         s.get("id_task"), s.get("n_ctx"), s.get("n_prompt_tokens"),
                                         s.get("n_prompt_tokens_processed"), nt.get("n_decoded"),
                                         int(bool(s.get("is_processing")))])
            except Exception:
                w["slots"].writerow([f"{wall:.3f}", f"{mono:.3f}", port, "", "", "", "", "", "", "", "", "unreachable"])
        try:
            models = client.get(f"{args.ollama}/api/ps").json().get("models", [])
            if not models:
                w["ollama_ps"].writerow([f"{wall:.3f}", f"{mono:.3f}", "", 0, "", ""])
            for m in models:
                w["ollama_ps"].writerow([f"{wall:.3f}", f"{mono:.3f}", m.get("name"), m.get("size_vram"),
                                         m.get("context_length"), m.get("expires_at")])
        except Exception:
            w["ollama_ps"].writerow([f"{wall:.3f}", f"{mono:.3f}", "unreachable", "", "", ""])

        for fh in files.values():
            fh.flush()
        tick += 1
        next_t += args.interval
        time.sleep(max(0.0, next_t - time.monotonic()))
    for fh in files.values():
        fh.close()


if __name__ == "__main__":
    main()
