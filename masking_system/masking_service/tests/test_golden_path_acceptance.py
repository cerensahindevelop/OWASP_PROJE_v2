"""Yol maskelemesi kabul testleri (altin kume, stub LLM).

Karar (docs/faz3-tasarim-notu.md, K1): bir dosyanin iceriginde maskelenen her
terim, o dosyanin ve ust dizinlerinin yolunda da maskelenir; job sozlugunde
olmayan yol parcasi maskelenmez. Maske yazim stilini korur ve Java'da public
sinif adi = dosya adi, package = dizin yapisi kalir. Geri alma dosya ve dizin
adlarini da kapsar; rapor ve loglara orijinal yol yazilmaz.

`xfail(strict=True)` olanlar Faz 3'un kabul kriteridir: bugun basarisizdir,
Faz 3 bunlari gecirdiginde strict xfail test takimini kirar ve isaret
kaldirilir. Isaretsiz olanlar bugun gecer ve regresyon korumasidir.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = SERVICE_ROOT / "tests" / "fixtures" / "golden"
SCRIPT = SERVICE_ROOT / "scripts" / "measure_golden.py"
FAZ3 = pytest.mark.xfail(strict=True, reason="Faz 3 kabul kriteri (proje terim sozlugu + parca bazli yol maskelemesi)")
_INVALID_PATH_CHARS = re.compile(r'[<>:"|?*\\\x00-\x1f]')
# Windows MAX_PATH (260) icinde, hedef klasor oneki icin pay birakilir.
_MAX_RELATIVE_PATH = 200


@pytest.fixture(scope="module")
def golden_run(tmp_path_factory):
    work = tmp_path_factory.mktemp("golden-yol")
    keep, log_file, out = work / "calisma", work / "uygulama.log", work / "rapor"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--llm", "stub", "--runs", "1", "--out", str(out), "--name", "stub",
         "--keep", str(keep), "--log-file", str(log_file)],
        capture_output=True, text=True, timeout=600,
    )
    assert result.returncode == 0, result.stderr[-3000:]
    manifest = json.loads((GOLDEN_DIR / "expected.json").read_text(encoding="utf-8"))
    report = json.loads((out / "stub.json").read_text(encoding="utf-8"))["kosular"][0]
    output = keep / "cikti_1"
    return {
        "manifest": manifest,
        "report": report,
        "source": GOLDEN_DIR / manifest["project_dir"],
        "output": output,
        "restored": keep / "geri_1",
        "log": log_file.read_text(encoding="utf-8") if log_file.exists() else "",
        "paths": sorted(p.relative_to(output).as_posix() for p in output.rglob("*")
                        if p.is_file() and p.name != ".masking-integrity.json"),
    }


def _ready(run) -> list[str]:
    return sorted(path for path, state in run["report"]["dosya_durumlari"].items() if state == "READY")


# --- Bugun gecen: regresyon korumasi -----------------------------------------

def test_restore_recreates_file_and_directory_names_byte_for_byte(golden_run):
    """diff -r: yayinlanan her dosya kaynaktaki ADI ve icerigiyle geri gelir."""
    restored = golden_run["restored"]
    restored_files = sorted(p.relative_to(restored).as_posix() for p in restored.rglob("*")
                            if p.is_file() and p.name != ".masking-integrity.json")
    assert restored_files == _ready(golden_run)
    for rel in restored_files:
        assert (restored / rel).read_bytes() == (golden_run["source"] / rel).read_bytes(), rel
    assert (golden_run["output"] / ".masking-integrity.json").is_file()


def test_output_paths_do_not_collide_even_case_insensitively(golden_run):
    folded = [path.casefold() for path in golden_run["paths"]]
    assert len(folded) == len(set(folded))


def test_output_paths_are_portable(golden_run):
    for path in golden_run["paths"]:
        assert len(path) <= _MAX_RELATIVE_PATH, path
        for part in path.split("/"):
            assert part not in ("", ".", "..") and not _INVALID_PATH_CHARS.search(part), path
            assert len(part.encode("utf-8")) <= 255 and not part.endswith((" ", ".")), path


def test_non_dictionary_path_parts_are_kept(golden_run):
    """Job sozlugunde olmayan yol parcasi maskelenmez."""
    output_parts = {part for path in golden_run["paths"] for part in path.split("/")}
    ready_names = {Path(rel).name for rel in _ready(golden_run)}
    source_names = {p.name for p in golden_run["source"].rglob("*") if p.is_file()}
    for part in golden_run["manifest"]["yol_beklentileri"]["korunacak_yol_parcalari"]:
        if part in source_names and part not in ready_names:
            continue  # dosya karantinada; ciktida olmamasi beklenir
        assert part in output_parts, part


def test_pom_group_id_points_to_existing_package_directory(golden_run):
    pom = (golden_run["output"] / "pom.xml").read_text(encoding="utf-8")
    group_id = re.search(r"<groupId>([^<]+)</groupId>", pom).group(1)
    package_dir = golden_run["output"] / golden_run["manifest"]["java_source_root"].split("/", 1)[1] / group_id.replace(".", "/")
    assert package_dir.is_dir(), group_id


# --- Faz 3 kabul kriterleri ----------------------------------------------------

def _require_path_term_files_published(run) -> None:
    """On kosul: yolunda maskelenmesi gereken terim gecen kaynak dosyalarin hepsi
    ciktida (READY). Aksi halde yol testleri bos kumeye karsi gecer: dosya
    karantinadaysa yolu ciktida hic olmaz ve kriter sinanmamis olur."""
    terms = [t.casefold() for t in run["manifest"]["yol_beklentileri"]["yolda_gecmemeli"]]
    sources = [p.relative_to(run["source"]).as_posix() for p in run["source"].rglob("*") if p.is_file()]
    missing = [rel for rel in sources if any(t in rel.casefold() for t in terms) and rel not in _ready(run)]
    assert missing == [], ("yol terimi iceren dosya ciktida degil; test sinanamaz", missing)


@FAZ3
def test_content_masked_terms_never_appear_in_output_paths(golden_run):
    """Yeni degismez kural: iceride maskelenen terim yolda da maskelenir."""
    _require_path_term_files_published(golden_run)
    leaking = [(path, term) for path in golden_run["paths"]
               for term in golden_run["manifest"]["yol_beklentileri"]["yolda_gecmemeli"]
               if term.casefold() in path.casefold()]
    assert leaking == []


@FAZ3
def test_path_masks_preserve_naming_style(golden_run):
    """PoseidonGatewayClient.java -> Mask<..>GatewayClient.java; karayel/poseidon -> mask<..>."""
    _require_path_term_files_published(golden_run)
    style = {name: re.compile(pattern) for name, pattern in golden_run["manifest"]["yol_beklentileri"]["stil"].items()}
    java_files = [path for path in golden_run["paths"] if path.endswith(".java")]
    source_names = {p.name for p in golden_run["source"].rglob("*.java")}
    renamed = [Path(path) for path in java_files if Path(path).name not in source_names]
    assert len(renamed) == 1 and style["PoseidonGatewayClient.java"].fullmatch(renamed[0].name), renamed
    # .../java/com/acme/<karayel>/<poseidon>/Mask..GatewayClient.java
    assert style["karayel"].fullmatch(renamed[0].parts[-3]), renamed[0]
    assert style["poseidon"].fullmatch(renamed[0].parts[-2]), renamed[0]


@FAZ3
def test_java_public_class_matches_file_name(golden_run):
    _require_path_term_files_published(golden_run)
    mismatched = []
    for path in golden_run["paths"]:
        if path.endswith(".java"):
            text = (golden_run["output"] / path).read_text(encoding="utf-8")
            declared = re.search(r"public\s+(?:final\s+|abstract\s+)*(?:class|interface|enum|record)\s+(\w+)", text)
            if declared and declared.group(1) != Path(path).stem:
                mismatched.append(path)
    assert mismatched == []


@FAZ3
def test_java_package_matches_directory(golden_run):
    _require_path_term_files_published(golden_run)
    java_root = golden_run["manifest"]["java_source_root"].split("/", 1)[1]
    mismatched = []
    for path in golden_run["paths"]:
        if path.endswith(".java"):
            text = (golden_run["output"] / path).read_text(encoding="utf-8")
            package = re.search(r"^package\s+([\w.]+)\s*;", text, re.MULTILINE).group(1)
            if str(Path(path).parent.relative_to(java_root)).replace("/", ".") != package:
                mismatched.append(path)
    assert mismatched == []


@FAZ3
def test_masked_java_project_compiles(golden_run):
    compiled = golden_run["report"]["derleme"]
    if compiled["durum"] == "atlandi":
        pytest.skip("javac yok")
    assert compiled["durum"] == "basarili" and compiled["eksik_java_dosyasi"] == 0, compiled


def test_report_and_logs_do_not_contain_original_paths(golden_run):
    """Faz 2a (K1/7): log ve raporda KAYNAK yol gecmez; dosya "maskeli yol#kimlik"
    ile anilir. Yol maskelemesinin bugun kapsadigi (sozluk) terimler hicbir
    yerde gecmez. LLM kaynakli `poseidon`'un maskeli yolda kalmasi ayri kabul
    testinin konusudur (test_content_masked_terms_never_appear_in_output_paths)."""
    manifest = golden_run["manifest"]
    path_terms = [t for t in manifest["yol_beklentileri"]["yolda_gecmemeli"]
                  if any(e["kaynak"] == "dictionary" and e["value"].casefold() == t.casefold()
                         for e in manifest["sensitive"])]
    assert path_terms, "sozluk kaynakli yol terimi yok; test anlamsizlasir"
    source_paths = [p.relative_to(golden_run["source"]).as_posix()
                    for p in golden_run["source"].rglob("*") if p.is_file()]
    masked_source_paths = [rel for rel in source_paths if any(t.casefold() in rel.casefold() for t in path_terms)]
    assert masked_source_paths
    report, log = golden_run["report"]["rapor_metni"], golden_run["log"]
    assert log and re.search(r"llm_request .*file='[^']+#[0-9a-f]{12}'", log)
    for name, text in (("rapor", report), ("log", log)):
        assert [rel for rel in masked_source_paths if rel in text] == [], name
        assert [t for t in path_terms if t.casefold() in text.casefold()] == [], name
    assert "<gizlendi>" not in report.split("Kaynak:", 1)[1].split("\n", 1)[0]
