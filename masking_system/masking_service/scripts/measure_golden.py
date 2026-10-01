"""Altin test kumesi (tests/fixtures/golden) uzerinde export olcumu.

Uretim veritabanina ve .env'deki SECURITY_ENCRYPTION_KEY/DB_PATH'e DOKUNMAZ:
her calisma gecici bir SQLite DB'si ve gecici bir anahtarla yapilir.

LLM modlari:
  off   VLLM_ENABLED=false (yalnizca sozluk/regex + Presidio).
  stub  127.0.0.1'de manifestten beslenen deterministik, OpenAI uyumlu sahte
        LLM. Tespit ve denetim yanitlari expected.json'daki 'llm'/'denetim'
        alanlarindan gelir. --stub-error-rate ile parca hash'ine bagli
        deterministik kalici hata (HTTP 503) enjekte edilir.
  real  .env'deki VLLM_* ayarlariyla gercek model (intranette calistirilir).

Raporlanan metrikler (JSON + Markdown):
  onay kuyrugu ve ciktidan disarida kalan dosya orani (nedene gore), dosya
  basina LLM suresi p50/p95 ve istek/token, canary sizintisi (duz metin,
  base64, base64url, URL-encoded), sozluk/LLM terim sizintisi, maskelenmemesi
  gereken adlarin maskelenmesi, geri alma bayt diff'i, maskeli Java'nin
  javac ile derlenmesi, N kosuda maskelenen deger kumelerinin Jaccard
  benzerligi ve cikti dosyalarinin birebir ayniligi.

Ornek:
  python scripts/measure_golden.py --llm stub --runs 3 --out ../diagnostics/golden-x
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = SERVICE_ROOT / "tests" / "fixtures" / "golden"
IDENTITY = ("golden-olcum", "P-GOLDEN-0001", "golden-branch")
BLOCKING_STATES = {"REVIEW_REQUIRED", "SECURITY_QUARANTINE", "VALIDATION_FAILED"}
MANIFEST_FILE = ".masking-integrity.json"


def load_manifest(golden_dir: Path = GOLDEN_DIR) -> dict:
    manifest = json.loads((golden_dir / "expected.json").read_text(encoding="utf-8"))
    if manifest.get("version") != 1:
        raise SystemExit("expected.json surumu desteklenmiyor")
    return manifest


# ---------------------------------------------------------------------------
# Stub LLM (OpenAI uyumlu /v1/chat/completions)
# ---------------------------------------------------------------------------

def _stub_fails(phase: str, text: str, error_rate: float) -> bool:
    if error_rate <= 0:
        return False
    digest = hashlib.sha256(f"{phase}\x00{text}".encode("utf-8", "surrogatepass")).digest()
    return int.from_bytes(digest[:4], "big") / 2**32 < error_rate


def stub_detection(manifest: dict, text: str) -> list[dict]:
    candidates = [entry for entry in manifest["sensitive"] if "llm" in entry] + manifest["stub_false_positives"]
    return [
        {"bulunan_deger": entry["value"], "tip": entry["llm"]["tip"],
         "guven_seviyesi": entry["llm"]["guven"], "gerekce": "altin kume stub"}
        for entry in candidates if entry["value"] in text
    ]


# Denetim, degerin kendisini ya da kismen maskelenmis bir degerin acik kalan
# varyantini (orn. host kisa adi) alintilar; daha uzun bir alintinin parcasi
# olan kisa varyant ayrica alintilanmaz.
def stub_audit(manifest: dict, text: str) -> list[dict]:
    findings = []
    for entry in manifest["sensitive"]:
        if not entry.get("denetim"):
            continue
        quoted: list[str] = []
        for form in sorted([entry["value"], *entry["variants"]], key=len, reverse=True):
            if form in text and not any(form in longer for longer in quoted):
                quoted.append(form)
        findings.extend({"aciklama": f"maskelenmemis {entry['id']}", "ilgili_bolum": form} for form in quoted)
    return findings


class StubLLM:
    def __init__(self, manifest: dict, error_rate: float) -> None:
        self.manifest = manifest
        self.error_rate = error_rate
        self.requests = 0
        self.injected_errors = 0
        self._lock = threading.Lock()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # sessiz
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                schema = payload["response_format"]["json_schema"]["name"]
                text = payload["messages"][-1]["content"]
                phase = "detection" if schema == "bulgular_semasi" else "audit"
                with stub._lock:
                    stub.requests += 1
                if _stub_fails(phase, text, stub.error_rate):
                    with stub._lock:
                        stub.injected_errors += 1
                    self.send_response(503)
                    self.end_headers()
                    return
                if phase == "detection":
                    content = {"bulgular": stub_detection(stub.manifest, text)}
                else:
                    findings = stub_audit(stub.manifest, text)
                    content = {"risk_var": bool(findings), "bulgular": findings}
                body = json.dumps({
                    "choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": sum(len(m["content"]) for m in payload["messages"]) // 4,
                              "completion_tokens": len(json.dumps(content)) // 4},
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self) -> "StubLLM":
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()


# ---------------------------------------------------------------------------
# Sizinti / maskeleme kontrolleri
# ---------------------------------------------------------------------------

def _base64_forms(value: bytes) -> set[bytes]:
    """Degerin daha uzun bir base64 akisinin icinde gorunebilecegi bicimler.

    Deger akista 3 farkli bayt hizasinda baslayabilir; her hiza icin yalnizca
    degerin baytlarina bagli olan (komsu baytlardan etkilenmeyen) karakterler
    alinir. Standart ve URL-guvenli alfabe birlikte aranir.
    """
    forms: set[bytes] = set()
    for pad in range(3):
        encoded = base64.b64encode(b"\x00" * pad + value)
        start = (pad * 4 + 2) // 3
        usable = (pad + len(value)) // 3 * 4
        core = encoded[start:usable]
        if len(core) >= 8:
            forms.add(core)
            forms.add(core.replace(b"+", b"-").replace(b"/", b"_"))
    return forms


def leak_forms(value: str) -> dict[str, set[bytes]]:
    raw = value.encode("utf-8")
    return {
        "duz": {raw},
        "base64": _base64_forms(raw),
        "url": {urllib.parse.quote(value, safe="").encode(), urllib.parse.quote_plus(value).encode(),
                urllib.parse.quote(value, safe="").lower().encode()} - {raw},
    }


def output_files(output_dir: Path) -> dict[str, bytes]:
    return {
        path.relative_to(output_dir).as_posix(): path.read_bytes()
        for path in sorted(output_dir.rglob("*")) if path.is_file() and path.name != MANIFEST_FILE
    }


# Canary degeri ya da varyantlarindan (kismi sizinti) herhangi biri, herhangi
# bir kodlamada ciktida geciyor mu? Donus: {"bicim/kodlama": [dosyalar]}.
def find_leaks(files: dict[str, bytes], entry: dict) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for value in [entry["value"], *entry["variants"]]:
        for encoding, forms in leak_forms(value).items():
            hits = sorted({name for name, data in files.items() for form in forms if form and form in data})
            if hits:
                found[f"{value}/{encoding}"] = hits
    return found


def term_leaks(files: dict[str, bytes], entry: dict) -> dict[str, int]:
    counts = {}
    for form in [entry["value"], *entry["variants"]]:
        count = sum(data.count(form.encode("utf-8")) for data in files.values())
        if count:
            counts[form] = count
    return counts


def must_not_mask_violations(source: Path, ready_files: list[str], files: dict[str, bytes],
                             names: list[str]) -> dict[str, dict[str, int]]:
    source_text = b"".join((source / rel).read_bytes() for rel in ready_files)
    output_text = b"".join(files.values())
    violations = {}
    for name in names:
        expected, actual = source_text.count(name.encode()), output_text.count(name.encode())
        if actual < expected:
            violations[name] = {"kaynakta": expected, "ciktida": actual}
    return violations


def compile_java(output_dir: Path, stubs_dir: Path, expected_java: int, work: Path) -> dict:
    if shutil.which("javac") is None:
        return {"durum": "atlandi", "neden": "javac yok"}
    sources = sorted(str(p) for p in output_dir.rglob("*.java"))
    stub_sources = sorted(str(p) for p in stubs_dir.rglob("*.java"))
    classes = work / "classes"
    shutil.rmtree(classes, ignore_errors=True)
    result = subprocess.run(["javac", "-d", str(classes), *stub_sources, *sources], capture_output=True, text=True)
    errors = [line for line in result.stderr.splitlines() if ": error:" in line]
    return {
        "durum": "basarili" if result.returncode == 0 else "basarisiz",
        "java_dosyasi": len(sources),
        "beklenen_java_dosyasi": expected_java,
        "eksik_java_dosyasi": expected_java - len(sources),
        "hata_sayisi": len(errors),
        "ilk_hatalar": [line.split("/")[-1] for line in errors[:5]],
    }


def jaccard(a: set, b: set) -> float:
    return 1.0 if not a and not b else len(a & b) / len(a | b)


# ---------------------------------------------------------------------------
# Calistirma
# ---------------------------------------------------------------------------

def _prepare_environment(work: Path, llm_mode: str, stub_url: str | None) -> None:
    from cryptography.fernet import Fernet

    os.environ["DB_PATH"] = str(work / "golden.db")
    os.environ["SECURITY_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    if llm_mode == "off":
        os.environ["VLLM_ENABLED"] = "false"
    elif llm_mode == "stub":
        os.environ.update({"VLLM_ENABLED": "true", "VLLM_HOST": stub_url, "VLLM_MODEL": "golden-stub",
                           "VLLM_TIMEOUT_SECONDS": "30"})
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=SERVICE_ROOT, check=True,
                   capture_output=True)


def _seed_dictionary(manifest: dict) -> None:
    from app.db.session import SessionLocal
    from app.services.mapping_service import get_or_create_context
    from app.services.term_upload import add_single_corporate_term

    with SessionLocal() as db:
        for term in manifest["dictionary_terms"]:
            add_single_corporate_term(db, term=term["term"], title=term["title"], confirmed_sensitive=True)
        get_or_create_context(db, *IDENTITY)
        db.commit()


def _one_run(manifest: dict, source: Path, work: Path, index: int) -> dict:
    from sqlalchemy import func, select

    from app.db.models import AuditWarning, ValueMapping
    from app.db.session import SessionLocal
    from app.services.exporter import export_project
    from app.services.unmasker import unmask_project

    target = work / f"cikti_{index}"
    restored = work / f"geri_{index}"
    with SessionLocal() as db:
        report = asyncio.run(export_project(
            db, source_path=str(source), project_name=IDENTITY[0], sicil_no=IDENTITY[1],
            branch_name=IDENTITY[2], target_path=str(target), initiated_by=IDENTITY[1],
        ))
        db.commit()
    with SessionLocal() as db:
        masked_values = set(db.scalars(
            select(ValueMapping.original_value_plain).where(ValueMapping.run_id == report.run_id)
        ).all())
        warning_files = db.scalar(
            select(func.count(func.distinct(AuditWarning.file_path))).where(AuditWarning.run_id == report.run_id)
        )
        unmask_report = unmask_project(
            db, source_path=str(target), project_name=IDENTITY[0], sicil_no=IDENTITY[1],
            branch_name=IDENTITY[2], target_path=str(restored), initiated_by=IDENTITY[1],
        )
        db.commit()

    ready = sorted(o.relative_path for o in report.outcomes if o.final_state == "READY")
    roundtrip_diff = [rel for rel in ready
                      if not (restored / rel).is_file() or (restored / rel).read_bytes() != (source / rel).read_bytes()]
    files = output_files(target)
    scanned = report.files_scanned or 1
    blocked = sum(1 for o in report.outcomes if o.final_state in BLOCKING_STATES)
    expected_java = sum(1 for _ in source.rglob("*.java"))
    return {
        "run_id": report.run_id,
        "durum": report.status,
        "taranan_dosya": report.files_scanned,
        "hazir_dosya": len(ready),
        "onay_kuyrugu_dosya": warning_files,
        "onay_kuyrugu_orani": round(warning_files / scanned, 3),
        "disarida_kalan_dosya": blocked,
        "disarida_kalan_orani": round(blocked / scanned, 3),
        "nedene_gore": report.blocked_by_check,
        "dosya_durumlari": {o.relative_path: (o.failed_check.value if o.failed_check else o.final_state)
                            for o in sorted(report.outcomes, key=lambda o: o.relative_path)},
        "llm": report.llm_usage_summary,
        "llm_dosya_basina": {path: vars(usage) for path, usage in sorted(report.llm_usage_by_file.items())},
        "canary_sizintisi": {e["id"]: leaks for e in manifest["sensitive"]
                             if e["canary"] and (leaks := find_leaks(files, e))},
        "terim_sizintisi": {e["id"]: leaks for e in manifest["sensitive"]
                            if not e["canary"] and (leaks := term_leaks(files, e))},
        "maskelenmemesi_gereken_ihlal": must_not_mask_violations(source, ready, files, manifest["must_not_mask"]),
        "geri_alma": {"karsilastirilan": len(ready), "farkli": roundtrip_diff,
                      "cozulemeyen_placeholder": unmask_report.total_placeholders_unresolved},
        "derleme": compile_java(target, GOLDEN_DIR / manifest["stubs_dir"], expected_java, work / f"javac_{index}"),
        "rapor_metni": report.summary_text(),
        "_maskelenen_degerler": masked_values,
        "_cikti": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
    }


def measure(llm_mode: str, runs: int, stub_error_rate: float = 0.0, keep: Path | None = None,
            log_file: Path | None = None) -> dict:
    manifest = load_manifest()
    source = GOLDEN_DIR / manifest["project_dir"]
    work = Path(tempfile.mkdtemp(prefix="golden-olcum-"))
    if log_file is not None:
        # Uygulama loglari (orn. uvicorn.error.llm) kok logger'a akar; yol sizintisi kontrolu icin.
        logging.basicConfig(level=logging.INFO, filename=str(log_file), encoding="utf-8", force=True)
    stub = StubLLM(manifest, stub_error_rate) if llm_mode == "stub" else None
    try:
        if stub is not None:
            stub.__enter__()
        _prepare_environment(work, llm_mode, stub.url if stub else None)
        sys.path.insert(0, str(SERVICE_ROOT))
        _seed_dictionary(manifest)
        results = [_one_run(manifest, source, work, index) for index in range(1, runs + 1)]
    finally:
        if stub is not None:
            stub.__exit__()
        if keep is not None:
            shutil.copytree(work, keep, dirs_exist_ok=True)
        shutil.rmtree(work, ignore_errors=True)

    value_sets = [r.pop("_maskelenen_degerler") for r in results]
    outputs = [r.pop("_cikti") for r in results]
    pairs = [(i, j) for i in range(len(results)) for j in range(i + 1, len(results))]
    all_names = set().union(*outputs) if outputs else set()
    identical = sum(1 for name in all_names if len({o.get(name) for o in outputs}) == 1)
    return {
        "llm_modu": llm_mode,
        "stub_hata_orani": stub_error_rate if llm_mode == "stub" else None,
        "stub_istek": stub.requests if stub else None,
        "stub_enjekte_hata": stub.injected_errors if stub else None,
        "kosu_sayisi": runs,
        "determinizm": {
            "jaccard_min": round(min((jaccard(value_sets[i], value_sets[j]) for i, j in pairs), default=1.0), 4),
            "maskelenen_deger_sayisi": [len(v) for v in value_sets],
            "birebir_ayni_cikti_dosyasi": f"{identical}/{len(all_names)}",
        },
        "kosular": results,
    }


def to_markdown(result: dict) -> str:
    first = result["kosular"][0]
    lines = [
        f"# Altin kume olcumu - LLM modu: {result['llm_modu']}"
        + (f" (stub hata orani {result['stub_hata_orani']})" if result["llm_modu"] == "stub" else ""),
        "",
        "| Metrik | " + " | ".join(f"Kosu {i}" for i in range(1, len(result["kosular"]) + 1)) + " |",
        "|---|" + "---|" * len(result["kosular"]),
    ]

    def row(label, fn):
        lines.append(f"| {label} | " + " | ".join(str(fn(r)) for r in result["kosular"]) + " |")

    row("Taranan / hazir dosya", lambda r: f"{r['taranan_dosya']} / {r['hazir_dosya']}")
    row("Onay kuyrugu orani", lambda r: f"{r['onay_kuyrugu_orani']:.0%} ({r['onay_kuyrugu_dosya']})")
    row("Ciktidan disarida kalan orani", lambda r: f"{r['disarida_kalan_orani']:.0%} ({r['disarida_kalan_dosya']})")
    row("Nedene gore", lambda r: ", ".join(f"{k}={v}" for k, v in r["nedene_gore"].items()) or "-")
    row("LLM istek (dosya basina)", lambda r: f"{r['llm'].get('requests', 0)} ({r['llm'].get('requests_per_file', 0)})"
        if r["llm"] else "-")
    row("Dosya basina LLM sn p50/p95", lambda r: f"{r['llm']['llm_seconds_p50']}/{r['llm']['llm_seconds_p95']}"
        if r["llm"] else "-")
    row("Canary sizintisi", lambda r: ", ".join(f"{k} ({'; '.join(v)})" for k, v in r["canary_sizintisi"].items())
        or "0")
    row("Terim sizintisi", lambda r: ", ".join(f"{k}={sum(v.values())}" for k, v in r["terim_sizintisi"].items()) or "0")
    row("Maskelenmemesi gereken ihlali", lambda r: ", ".join(r["maskelenmemesi_gereken_ihlal"]) or "0")
    row("Geri alma bayt farki", lambda r: f"{len(r['geri_alma']['farkli'])}/{r['geri_alma']['karsilastirilan']}")
    row("javac", lambda r: f"{r['derleme']['durum']} (hata {r['derleme'].get('hata_sayisi', '-')}, "
        f"eksik java {r['derleme'].get('eksik_java_dosyasi', '-')})")
    det = result["determinizm"]
    lines += [
        "",
        f"Determinizm: Jaccard (min) {det['jaccard_min']}, maskelenen deger sayilari {det['maskelenen_deger_sayisi']}, "
        f"birebir ayni cikti dosyasi {det['birebir_ayni_cikti_dosyasi']}.",
        "",
        "## Dosya bazinda sonuc (kosu 1)",
        "",
        "| Dosya | Sonuc |",
        "|---|---|",
        *[f"| `{path}` | {state} |" for path, state in first["dosya_durumlari"].items()],
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--llm", choices=["off", "stub", "real"], required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--stub-error-rate", type=float, default=0.0)
    parser.add_argument("--out", type=Path, help="JSON ve Markdown raporunun yazilacagi klasor")
    parser.add_argument("--name", default=None, help="Rapor dosya adi oneki (varsayilan: llm modu)")
    parser.add_argument("--keep", type=Path, help="Gecici calisma klasorunu (cikti, geri alma, DB) buraya kopyala")
    parser.add_argument("--log-file", type=Path, help="Uygulama loglarini (INFO) bu dosyaya yaz")
    args = parser.parse_args()

    result = measure(args.llm, args.runs, args.stub_error_rate, args.keep, args.log_file)
    markdown = to_markdown(result)
    print(markdown)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        name = args.name or args.llm
        (args.out / f"{name}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (args.out / f"{name}.md").write_text(markdown, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
