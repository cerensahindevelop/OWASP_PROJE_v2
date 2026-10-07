"""Analiz ciktilarindan Turkce sonuc raporu (Markdown) uretir.

    loadtest/.venv/bin/python -m loadtest.report --main loadtest/results/main \
        --workers loadtest/results/workers --out loadtest/results/RAPOR.md

Yorum/darbogaz bolumu, olcumlere bakilarak elle yazilan
loadtest/results/yorum.md dosyasindan (varsa) eklenir.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

DAGGER = "†"


def f(x, d=1, unit=""):
    if x is None:
        return "–"
    if isinstance(x, (int,)) and not isinstance(x, bool):
        return f"{x}{unit}"
    return f"{x:.{d}f}{unit}"


def p95(s: dict, d=1) -> str:
    if not s or s.get("n", 0) == 0:
        return "–"
    return f(s["p95"], d) + ("" if s["p95_ok"] else DAGGER)


def p99(s: dict, d=1) -> str:
    if not s or s.get("n", 0) == 0:
        return "–"
    return f(s["p99"], d) + ("" if s["p99_ok"] else DAGGER)


def stat_row(label: str, s: dict, d=2) -> str:
    if not s or s.get("n", 0) == 0:
        return f"| {label} | 0 | – | – | – | – | – |"
    return f"| {label} | {s['n']} | {f(s['mean'], d)} | {f(s['p50'], d)} | {p95(s, d)} | {p99(s, d)} | {f(s['max'], d)} |"


SIZE_TR = {"small": "küçük", "medium": "orta", "large": "büyük"}
SCEN_TR = {"cold": "Soğuk başlangıç", "warm": "Isınmış sistem", "burst": "Eşzamanlı yükleme",
           "sustained": "Sürekli yük (600 sn pencere)", "mixed": "Karışık yük", "workers": "Worker/concurrency"}


def sizes_tr(s: str) -> str:
    return "+".join(SIZE_TR.get(x, x) for x in s.split("/"))


def gpu_cell(c: dict, idx: str) -> str:
    g = c["gpus"].get(idx) or c["gpus"].get(int(idx)) if c.get("gpus") else None
    if not g:
        return "–"
    return f"{f(g['util_mean'], 0)}% / p95 {f(g['util_p95'], 0)}% / {f((g['mem_used_max_mib'] or 0) / 1024, 1)} GiB"


def errors_cell(c: dict) -> str:
    return (f"iş hata {c['failed']}, HTTP {c['http_errors']}, timeout {c['timeouts']}"
            f" · LLM timeout {c['llm_timeouts']}, LLM HTTP {c['llm_http_errors']}"
            f" · karantina {c['files_security_quarantine']}, teknik blok {c['files_blocked_technical']}"
            f", inceleme {c['files_review_required']}")


def scenario_table(conds: list[dict]) -> list[str]:
    lines = [
        "| kullanıcı | proje boyutu | tamamlanan / gönderilen | uçtan uca p50 / p95 (sn) | LLM istek p95 (sn) | "
        "LLM kuyruk p95 (sn) | proje/dk (ort. [min–maks]) | GPU0 kull. ort/p95 / VRAM maks | GPU1 kull. ort/p95 / VRAM maks | "
        "hata / timeout / karantina |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in sorted(conds, key=lambda c: c["users"]):
        ppm = c["projects_per_min"]
        pend = f" (+{c['pending_at_window_end']} pencere sonunda bitmemiş)" if c["pending_at_window_end"] else ""
        lines.append(
            f"| {c['users']} | {sizes_tr(c['sizes'])} | {c['completed']} / {c['submitted']}{pend} | "
            f"{f(c['e2e_ok']['p50'])} / {p95(c['e2e_ok'])} | {p95(c['llm_req_total_ok'])} | "
            f"{p95(c['llm_req_admission'])} | {f(ppm['mean'], 2)} [{f(ppm['min'], 2)}–{f(ppm['max'], 2)}] | "
            f"{gpu_cell(c, '0')} | {gpu_cell(c, '1')} | {errors_cell(c)} |")
    return lines


def min_of(s: dict):
    return s.get("min", s.get("p50"))


def stage_table(c: dict) -> list[str]:
    lines = [f"**{c['condition']}** ({c['users']} kullanıcı, {sizes_tr(c['sizes'])}, {c['reps']} tekrar)", "",
             "| aşama (saniye) | n | ort. | p50 | p95 | p99 | maks |", "|---|---|---|---|---|---|---|"]
    rows = [
        ("Yükleme (POST, istemci)", "upload"),
        ("Uygulama iş kuyruğu (kayıt→iş thread'i)", "app_queue"),
        ("İş başlatma beklemesi (SQLite kilidi dahil; iş başı→spaCy kurulumu)", "startup_wait"),
        ("Detection, iş başına duvar saati", "detection_wall"),
        ("  · dosya başına detection", "file_detect"),
        ("  · dosya başına Presidio", "file_presidio"),
        ("  · Presidio kilit beklemesi (analiz başına)", "file_presidio_lock_wait"),
        ("LLM isteği toplam (başarılı; kuyruk+HTTP)", "llm_req_total_ok"),
        ("  · LLM HTTP süresi (başarılı)", "llm_req_http_ok"),
        ("  · uygulama LLM kuyruğu (tüm istekler)", "llm_req_admission"),
        ("  · LLM isteği toplam (max_tokens'ta kesilip bölünen)", "llm_req_total_truncated"),
        ("  · LLM isteği toplam (başarısız)", "llm_req_total_failed"),
        ("Maskeleme, iş başına duvar saati", "masking_wall"),
        ("  · dosya başına maskeleme", "file_mask"),
        ("Audit (LLM denetimi), iş başına duvar saati", "audit_wall"),
        ("  · dosya başına LLM denetimi", "file_audit"),
        ("Sonlandırma (Faz D), iş başına duvar saati", "finalize_wall"),
        ("Export (tutarlılık+manifest+yayın)", "export"),
        ("İndirme (GET zip, istemci)", "download"),
        ("Tamamlanma bildirim gecikmesi (yoklama)", "completion_notice_delay"),
        ("spaCy/Presidio kurulumu (iş başına)", "orchestrator_build"),
        ("SQLite yazma kilidi bekleme (iş başına toplam)", "db_lock_wait_per_job"),
        ("Sunucuda iş süresi (başarılı)", "server_job"),
        ("**Uçtan uca (başarılı)**", "e2e_ok"),
        ("Uçtan uca (başarısız/timeout)", "e2e_not_ok"),
    ]
    for label, key in rows:
        lines.append(stat_row(label, c.get(key)))
    if c.get("failure_causes"):
        lines += ["", "Başarısız işlerin nedeni (istemci sonucu : sunucu istisnası : DB sürücü mesajı): "
                  + "; ".join(f"`{x}`" for x in c["failure_causes"])]
    return lines


def llm_table(conds: list[dict]) -> list[str]:
    lines = [
        "| koşul | LLM isteği (toplam / kesilip bölünen / başarısız) | iş başına istek (ort.) | iş başına chunk det/audit (ort.) | "
        "istek/sn (ort.) | prompt tok p50 | çıktı tok p50/p95 | istek çıktı tok/s p50 (prefill dahil) | "
        "sunucu prefill ms p50/p95 | sunucu TPOT ms p50/p95 | sunucu decode tok/s p50 | sunucu toplam tok/s (ort.) | "
        "sunucu içi bekleme p95 (sn) | slot dolu oranı | maks. bağlam doluluğu | eşzamanlı LLM (ölçülen maks) |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in conds:
        lines.append(
            f"| {c['condition']} | {c['llm_requests_total']} / {c['llm_truncated_requests']} / {c['llm_failed_requests']} | "
            f"{f(c['llm_requests_per_job']['mean'])} | {f(c['llm_chunks_detection_per_job']['mean'])} / {f(c['llm_chunks_audit_per_job']['mean'])} | "
            f"{f(c['llm_req_per_s']['mean'], 3)} | {f(c['llm_prompt_tokens']['p50'], 0)} | "
            f"{f(c['llm_completion_tokens']['p50'], 0)} / {p95(c['llm_completion_tokens'], 0)} | {f(c['llm_req_out_tok_s']['p50'], 1)} | "
            f"{f(c['srv_prefill_ms']['p50'], 0)} / {p95(c['srv_prefill_ms'], 0)} | {f(c['srv_tpot_ms']['p50'], 1)} / {p95(c['srv_tpot_ms'], 1)} | "
            f"{f(c['srv_decode_tok_s']['p50'], 1)} | {f(c['srv_total_tok_s']['mean'], 0)} | {p95(c['srv_wait_s'], 2)} | "
            f"{f((c['slots_busy_fraction']['mean'] or 0) * 100, 0)}% | {c['srv_ctx_fill_max']} / 8192 | "
            f"{c['gauge_llm_inflight_max']} (sunucu slotu {c['slots_max_processing']}) |")
    return lines


def hw_table(conds: list[dict]) -> list[str]:
    lines = [
        "| koşul | CPU ort. / maks % | sistem RAM maks (GiB) | swap maks (GiB) | backend RSS maks (MiB) | llama-server RSS maks (MiB) | "
        "disk yazma ort. (MB/s) | GPU0 sıcaklık maks / güç ort–maks / SM saat ort | GPU1 sıcaklık maks / güç ort–maks / SM saat ort | throttling nedenleri (örnek sayısı) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in conds:
        cells = []
        thr = []
        for idx in ("0", "1"):
            g = c["gpus"].get(idx) or c["gpus"].get(int(idx))
            if not g:
                cells.append("–")
                continue
            cells.append(f"{f(g['temp_max'], 0)}°C / {f(g['power_mean_w'], 0)}–{f(g['power_max_w'], 0)} W / {f(g['sm_clock_mean'], 0)} MHz")
            t = {k: v for k, v in g["throttle_samples"].items() if k not in ("none",)}
            thr.append(f"GPU{idx}: " + (", ".join(f"{k}={v}" for k, v in sorted(t.items())) or "yok"))
        lines.append(
            f"| {c['condition']} | {f(c['cpu_mean']['mean'], 0)} / {f(c['cpu_max'], 0)} | {f((c['ram_used_max_mib'] or 0) / 1024, 1)} | "
            f"{f((c['swap_used_max_mib'] or 0) / 1024, 2)} | {f(c['backend_rss_max_mib'], 0)} | {f(c['llama_rss_max_mib'], 0)} | "
            f"{f((c['disk_write_mean_bps']['mean'] or 0) / 1e6, 2)} | {cells[0]} | {cells[1]} | {'; '.join(thr)} |")
    return lines


def workers_table(conds: list[dict]) -> list[str]:
    lines = [
        "| aşama | backend worker | dosya worker | toplam LLM concurrency | kullanıcı | tamamlanan/gönderilen | proje/dk (ort.) | "
        "uçtan uca p95 (sn) | LLM kuyruk p95 (sn) | CPU ort.% / backend RSS maks MiB / RAM maks GiB | GPU0 kull./VRAM | GPU1 kull./VRAM | "
        "ölçülen eşzamanlı LLM (uygulama / sunucu slotu) | hata / timeout | durum |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in conds:
        status = "OK"
        if c["abort_reasons"]:
            status = "DURDURULDU: " + ", ".join(c["abort_reasons"])
        elif c["runner_errors"]:
            status = "HATA: " + "; ".join(c["runner_errors"])
        lines.append(
            f"| {c['group']} | {c['backend_workers']} | {c['file_batch']} | {c['llm_conc']} | {c['users']} | "
            f"{c['completed']}/{c['submitted']} | {f(c['projects_per_min']['mean'], 2)} | {p95(c['e2e_ok'])} | "
            f"{p95(c['llm_req_admission'])} | {f(c['cpu_mean']['mean'], 0)} / {f(c['backend_rss_max_mib'], 0)} / "
            f"{f((c['ram_used_max_mib'] or 0) / 1024, 1)} | {gpu_cell(c, '0')} | {gpu_cell(c, '1')} | "
            f"{c['gauge_llm_inflight_max']} / {c['slots_max_processing']} | "
            f"{c['failed'] + c['http_errors']} / {c['timeouts'] + c['llm_timeouts']} | {status} |")
    return lines


def env_section(env: dict) -> list[str]:
    e = env["env_settings"]
    keys = ["VLLM_ENABLED", "VLLM_HOST", "VLLM_MODEL", "VLLM_PROFILE", "VLLM_MAX_CONCURRENT_REQUESTS",
            "VLLM_FILE_BATCH_SIZE", "VLLM_MAX_FILE_CHARS", "VLLM_CHUNK_OVERLAP_CHARS", "VLLM_MAX_TOKENS",
            "VLLM_TIMEOUT_SECONDS", "VLLM_TRANSIENT_RETRIES", "VLLM_DISABLE_THINKING", "VLLM_REASONING_EFFORT",
            "VLLM_PRESENCE_PENALTY", "VLLM_REDACT_KNOWN_FINDINGS", "VLLM_AUDIT_UNCHANGED_FILES",
            "VLLM_AUTO_MASK_MIN_CONFIDENCE", "VLLM_LOW_CONFIDENCE_ACTION", "SCAN_MAX_FILE_MB",
            "SCAN_ENCODED_BLOB_MIN_CHARS", "WEB_API_REQUEST_TIMEOUT_SECONDS", "PRESIDIO_SPACY_MODEL"]
    lines = ["| ayar | değer (.env, test boyunca değiştirilmedi) |", "|---|---|"]
    for k in keys:
        lines.append(f"| `{k}` | `{e.get(k, '(tanımsız → kod varsayılanı)')}` |")
    return lines


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--main")
    ap.add_argument("--workers")
    ap.add_argument("--smoke")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    L: list[str] = []
    load = lambda p: json.loads((Path(p) / "summary_conditions.json").read_text()) if p else []  # noqa: E731
    main_c, work_c = load(args.main), load(args.workers)
    env = json.loads((Path(args.main or args.workers) / "environment.json").read_text())
    rel = lambda p: os.path.relpath(p, out.parent)  # noqa: E731

    L += ["# Maskeleme Sistemi — Çok Kullanıcılı Yük Testi Raporu", ""]
    yorum = out.parent / "yorum.md"
    if yorum.exists():
        L += [yorum.read_text(), ""]

    L += ["## 1. Test ortamı ve mevcut ayarlar", "",
          f"- Makine: `{env['host']}`, çekirdek `{env['kernel']}`, RAM {env['ram_total_gib']:.0f} GiB",
          "- CPU:", "```", env["cpu"], "```",
          "- GPU'lar:", "```", env["nvidia_smi"], "```",
          f"- Git commit: `{env['git_commit']}` (çalışma ağacı: {'temiz' if not env['git_status'] else 'değişiklik var — yalnız loadtest/ eklendi'})",
          f"- Uygulama Python: {env['app_python']}",
          f"- LLM sunucusu: Ollama `{env['ollama_version']}`; systemd ortamı: `{env['ollama_systemd_env']}`",
          "- Ollama'nın başlattığı llama-server komut satırı (test başında):", "```",
          env["llama_server_cmdline_at_start"] or "(model o anda yüklü değildi)", "```",
          "- Uygulama backend'i: `start.py` uvicorn'u `--workers` vermeden başlatır → **1 süreç**. "
          "Her export işi ayrı bir daemon thread'de, kendi asyncio event loop'unda çalışır; uygulamada iş kuyruğu/iş sınırı yoktur.",
          "- Dosya işleme: bir iş içinde `VLLM_FILE_BATCH_SIZE` dosya aynı anda hazırlanır/taranır (asyncio semaforu); "
          "Presidio/spaCy analizi `asyncio.to_thread` ile ayrı thread'de ama **detector örneği başına kilitli** (seri) çalışır. "
          "spaCy modeli her işte yeniden yüklenir (`build_orchestrator`).",
          "- LLM eşzamanlılık sınırı `VLLM_MAX_CONCURRENT_REQUESTS`: işletim sistemi dosya kilitleriyle (VLLM_ADMISSION_DIR) "
          "**host genelinde** uygulanır — thread, event loop ve backend süreçleri arasında ortaktır; worker başına değildir. "
          "HTTP timeout kuyruktan sonra başlar.", "",
          *env_section(env), ""]

    if args.main:
        man = env["datasets"]["datasets"]
        L += ["## 2. Veri grupları (sabit, tekrar üretilebilir)", "",
              f"Üretici: `loadtest/datasets.py` (tohum {env['datasets']['seed']}). Tüm değerler sentetiktir (RFC 5737 IP'ler, `.ornek.local` alan adları, rastgele ad-soyad).", "",
              "| grup | dosya sayısı | toplam bayt | 6000 karakteri aşan dosya | veri seti sha256 | iş başına LLM chunk (detection / audit, ölçülen ort.) | iş başına LLM isteği (ölçülen ort.) |",
              "|---|---|---|---|---|---|---|"]
        per_size = {}
        for c in main_c + work_c:
            if "/" not in c["sizes"] and c["completed"]:
                per_size.setdefault(c["sizes"], []).append(c)
        for size, m in man.items():
            cs = per_size.get(size, [])
            det = [x["llm_chunks_detection_per_job"]["mean"] for x in cs if x["llm_chunks_detection_per_job"]["mean"] is not None]
            aud = [x["llm_chunks_audit_per_job"]["mean"] for x in cs if x["llm_chunks_audit_per_job"]["mean"] is not None]
            req = [x["llm_requests_per_job"]["mean"] for x in cs if x["llm_requests_per_job"]["mean"] is not None]
            L.append(f"| {SIZE_TR[size]} | {m['file_count']} | {m['total_bytes']} | {m['files_over_6000_chars']} | "
                     f"`{m['dataset_sha256'][:16]}` | {f(sum(det) / len(det)) if det else '–'} / {f(sum(aud) / len(aud)) if aud else '–'} | "
                     f"{f(sum(req) / len(req)) if req else '–'} |")
        L += ["", "Chunk = uygulamanın LLM'e gönderdiği parça (`chunk_text`); max_tokens'ta kesilen parça ikiye bölünüp yeniden "
              "gönderildiği için istek sayısı chunk sayısından büyük olabilir.", ""]

        L += ["## 3. Senaryo sonuçları", "",
              f"Sütun notları: `{DAGGER}` = örnek sayısı yüzdelik için yetersiz (p95 için n<20, p99 için n<100); bu durumda "
              "değer pratikte maksimuma eşittir ve istatistiksel olarak güvenilmezdir. Uçtan uca = istemcinin yüklemeye "
              "başlamasından zip indirmesi bitene kadar (monotonic saat). LLM istek p95 = başarılı isteğin kuyruk+HTTP toplamı. "
              "LLM kuyruk = uygulamanın host-genel LLM kapasite kilidinde bekleme. GPU kullanım % NVML'in örneklenmiş "
              "değeridir (1 sn aralık). Karantina = güvenlik karantinası (SECURITY_QUARANTINE) dosya sayısı; teknik "
              "blok = LLM/tespit hatası nedeniyle bloklanan dosya; ikisi de iş başarısından ayrı raporlanır.", ""]
        by_scen: dict[str, list[dict]] = {}
        for c in main_c:
            by_scen.setdefault(c["scenario"], []).append(c)
        for scen, cs in by_scen.items():
            L += [f"### {SCEN_TR.get(scen, scen)}", ""] + scenario_table(cs) + [""]
            low = [c for c in cs if c["e2e_ok"]["n"] < 20]
            if low:
                L += ["> p95 uyarısı: " + ", ".join(f"{c['condition']} (n={c['e2e_ok']['n']})" for c in low)
                      + " — başarılı iş sayısı p95 için yeterli değil.", ""]
        cw = [c for c in main_c if c["scenario"] in ("cold", "warm")]
        if cw:
            L += ["### Soğuk başlangıç etkisi: ilk istek", "",
                  "| koşul | sunucuda model yükleme, sn (n / ort. / maks) | ilk LLM isteği HTTP süresi, sn (tekrar ort. / maks) | ilk işin uçtan uca süresi, sn (tekrar ort. / maks) | LLM isteği HTTP p50 (tüm istekler) |",
                  "|---|---|---|---|---|"]
            for c in cw:
                ml = c["model_load_s"]
                L.append(f"| {c['condition']} | {ml['n']} / {f(ml['mean'])} / {f(ml['max'])} | {f(c['first_llm_http']['mean'])} / {f(c['first_llm_http']['max'])} | "
                         f"{f(c['first_job_e2e']['mean'])} / {f(c['first_job_e2e']['max'])} | {f(c['llm_req_http_ok']['p50'], 2)} |")
            L += ["", "Soğuk koşulda model her tekrar öncesi Ollama'dan boşaltıldı (`keep_alive=0`) ve backend yeni süreçle "
                  "başlatıldı; ısınma işi yapılmadı. Isınmış koşulda her backend sürecinde bir küçük ısınma işi çalıştı ve istatistikten çıkarıldı.", ""]
        L += ["### Aşama süreleri (her koşul, 3 tekrar havuzlanmış)", "",
              "İş başına aşama süresi, o aşamaya ait aralıkların birleşiminin uzunluğudur (paralel dosyalar toplanmaz). "
              "Aşamalar boru hattında üst üste bindiği için satırlar toplanarak uçtan uca süre elde edilemez.", ""]
        for c in main_c:
            L += stage_table(c) + [""]
        L += ["### LLM metrikleri", ""] + llm_table(main_c) + [""]
        L += ["### Donanım", ""] + hw_table(main_c) + [""]
        charts = Path(args.main) / "charts"
        L += ["### Grafikler", ""]
        for name in ["e2e_p50.png", "e2e_p95.png", "throughput.png", "llm_admission_p95.png", "gpu0_vram_max.png",
                     "gpu1_vram_max.png", "gpu0_util_mean.png", "gpu1_util_mean.png", "backend_rss_max.png"]:
            if (charts / name).exists():
                L += [f"![{name}]({rel(charts / name)})", ""]
        for p in sorted(charts.glob("timeline_*.png")):
            L += [f"![{p.name}]({rel(p)})", ""]

    if args.workers:
        L += ["## 4. Worker ve concurrency testi", "",
              "Her koşul: kullanıcı başına 1 küçük proje, bariyerle aynı anda gönderim, 3 tekrar. "
              "Aşama A backend süreç sayısını, B uygulamanın toplam LLM eşzamanlılık sınırını, C iş başına dosya "
              "işleme paralelliğini (`VLLM_FILE_BATCH_SIZE`) değiştirir; diğer iki ayar .env değerinde sabittir. "
              "Model sunucusu (Ollama/llama-server) ayarlarına dokunulmadı; sunucu tek slot (`-np 1`) ve tek model kopyasıyla çalıştı. "
              "Backend worker > 1: iş durumu süreç belleğinde tutulduğu için (`export_jobs.registry`) `uvicorn --workers N` "
              "ile yoklama başka sürece düşüp 404 alır; bu yüzden N ayrı backend süreci ayrı portlarda başlatıldı ve her "
              "sanal kullanıcı bir sürece yapıştırıldı (aynı DB, aynı LLM kapasite kilidi).", ""]
        L += workers_table(work_c) + [""]
        L += ["### Worker koşulları — LLM ve donanım ayrıntısı", ""] + llm_table(work_c) + [""] + hw_table(work_c) + [""]
        charts = Path(args.workers) / "charts"
        for name in ["throughput.png", "e2e_p95.png", "llm_admission_p95.png", "backend_rss_max.png",
                     "gpu0_vram_max.png", "gpu1_vram_max.png"]:
            if (charts / name).exists():
                L += [f"![workers {name}]({rel(charts / name)})", ""]

    all_c = main_c + work_c
    L += ["## 5. Kayıt yalıtımı doğrulaması", "",
          "Her tekrar sonunda test DB kopyasında, her iş için: run kaydının proje/sicil/branch'i işi gönderen kullanıcıyla "
          "aynı mı; run'a ait değer eşlemeleri yalnız o bağlamda mı; denetim kaydındaki dosya yolları yalnız o projenin "
          "dosyaları mı; indirilen paketin bütünlük manifestindeki job_id run_id ile aynı mı; run_id ve çıktı tokenı "
          "işler arasında tekil mi kontrol edildi.", "",
          f"Sonuç: {sum(1 for c in all_c if c['isolation_ok'])}/{len(all_c)} koşulda tüm kontroller geçti."
          + ("" if all(c["isolation_ok"] for c in all_c) else
             " Sorunlu koşullar: " + ", ".join(c["condition"] for c in all_c if not c["isolation_ok"])), ""]

    L += ["## 6. Ölçülemeyen / sınırlı metrikler", "",
          "- **TTFT (ilk tokena kadar süre): ölçülemedi.** Uygulama `/v1/chat/completions`'a streaming olmadan istek atıyor; "
          "toplam süreden tahmin yapılmadı. Yerine llama-server'ın kendi ölçtüğü **prefill (prompt eval) süresi** verildi; "
          "bu TTFT değildir (sunucu kuyruğu ve ağ hariç).",
          "- **Tokenlar arası süre**: llama-server'ın `eval time ... ms per token` değeri (sunucu ölçümü). MTP spekülatif "
          "çözümleme açık olduğundan (`--spec-type draft-mtp`) bu, kabul edilen taslak tokenlar dahil ortalamadır.",
          "- **Sunucu kuyruğunda bekleme**: Ollama kendi kuyruğu için metrik sunmuyor. Tabloda verilen 'sunucu içi bekleme', "
          "Ollama GIN günlüğündeki istek süresinden geriye hesaplanan varış anı ile llama-server'ın görevi başlattığı an "
          "arasındaki farktır (iki sunucu zaman damgası; np=1 olduğundan görevler sırayla eşlenir). Uygulama kuyruğu ayrıca verildi.",
          "- **KV cache kullanımı**: llama-server `--metrics` olmadan başlatıldığı için `/metrics` kapalı (501). Yerine slot bağlam "
          "doluluğu (görev sonundaki `n_tokens` / 8192) verildi. **Preemption sayısı: ölçülemedi** (llama.cpp tek slotta preemption "
          "yapmaz; kesme yerine `truncated` sayacı raporlandı).",
          "- **Sunucunun eşzamanlı çalışan istek sayısı**: `/slots` her saniye yoklandı (yoğun decode sırasında yanıt gecikirse örnek 'ulaşılamadı' sayıldı).",
          "- İstek bazında çıktı token/s, istemci tarafında `completion_tokens / HTTP süresi` ile hesaplandı; prefill ve "
          "Ollama içi bekleme dahildir, saf decode hızı değildir (saf decode için sunucu sütunu).",
          f"- Diğer istemcilerin (kullanıcının canlı backend'i) aynı Ollama'ya istek atıp atmadığı GIN günlüğü ile testin "
          f"kendi isteklerinin farkından izlendi: tahmini yabancı istek toplamı {sum(c['srv_foreign_requests'] for c in all_c)}.", ""]

    L += ["## 7. Ham veriler ve tekrar çalıştırma", "",
          "Ham dosyalar (her koşul/tekrar klasöründe):", "",
          "- `jobs.jsonl` — istemci tarafı iş kayıtları (her iş: kullanıcı, proje, job_id, run_id, monotonic zaman damgaları, durum, karantina sayıları)",
          "- `server_events_<port>.jsonl` — sunucu olayları (iş/dosya/LLM isteği bazında monotonic başlangıç-bitiş, kuyruk, HTTP, token; saniyelik sayaçlar)",
          "- `hw/gpu.csv`, `hw/sys.csv`, `hw/proc.csv`, `hw/slots.csv`, `hw/ollama_ps.csv` — saniyelik, zaman damgalı (wall + monotonic) donanım/sunucu ölçümleri; `hw/gpu_info.json` GPU adı/UUID",
          "- `ollama_journal.log` — test penceresine ait Ollama/llama-server günlük satırları (prefill/decode süreleri)",
          "- `rep.json` — koşul ayarları, ölçüm penceresi, kuyruk boşaltma, backend çıkış kodları, yalıtım doğrulaması",
          "- Kampanya kökünde: `environment.json`, `summary_conditions.csv/json`, `summary_reps.csv`, `jobs_all.csv`, "
          "`llm_requests_all.csv`, `files_all.csv`, `server_tasks_all.csv`, `hw_summary.csv`, `charts/`", "",
          "Komutlar (`masking_system/` dizininden):", "", "```bash",
          "python3 -m venv loadtest/.venv && loadtest/.venv/bin/pip install psutil nvidia-ml-py matplotlib httpx",
          "loadtest/.venv/bin/python -m loadtest.datasets                      # veri setleri (bayt-bayt aynı)",
          "export LOADTEST_WORK=/tmp/masking-loadtest-work                     # geçici DB kopyaları/çıktılar",
          "loadtest/.venv/bin/python -m loadtest.runner --plan main    --campaign loadtest/results/main",
          "loadtest/.venv/bin/python -m loadtest.runner --plan workers --campaign loadtest/results/workers",
          "loadtest/.venv/bin/python -m loadtest.analyze loadtest/results/main loadtest/results/workers",
          "loadtest/.venv/bin/python -m loadtest.report --main loadtest/results/main --workers loadtest/results/workers --out loadtest/results/RAPOR.md",
          "# tek koşul: --only S3_burst_u10   tekrar sayısı: --reps 1   (DONE olan tekrarlar atlanır)",
          "```", ""]
    out.write_text("\n".join(L))
    print(f"yazildi: {out}")


if __name__ == "__main__":
    main()
