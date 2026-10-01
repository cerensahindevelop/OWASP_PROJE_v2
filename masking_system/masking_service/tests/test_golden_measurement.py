"""scripts/measure_golden.py: sizinti arama bicimleri, stub LLM ve uctan uca olcum.

Baseline sayilarina (orn. "sizinti = 0") burada assert konmaz; onlar sonraki
fazlarin kabul kriteridir. Burada olcum aracinin kendisi dogrulanir.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import subprocess
import sys
import urllib.parse
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "measure_golden.py"


def _module():
    spec = importlib.util.spec_from_file_location("measure_golden", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("prefix", [b"", b"a", b"ab", b"abc", b"x" * 7])
def test_base64_leak_is_found_at_any_byte_alignment(prefix):
    measure = _module()
    value = "Cnry-Pw-7Q2x9"
    stream = base64.b64encode(prefix + value.encode() + b"-suffix")
    entry = {"value": value, "variants": []}
    assert measure.find_leaks({"f.txt": stream}, entry) == {f"{value}/base64": ["f.txt"]}
    urlsafe = base64.urlsafe_b64encode(prefix + value.encode() + b"?>?>")
    assert measure.find_leaks({"g.txt": urlsafe}, entry), prefix


def test_url_encoded_and_partial_variant_leaks_are_found():
    measure = _module()
    entry = {"value": "hakan.yilmaz@karayel-bank.com.tr", "variants": ["hakan.yilmaz"]}
    encoded = urllib.parse.quote(entry["value"], safe="").encode()
    leaks = measure.find_leaks({"a.properties": b"x=" + encoded, "b.txt": b"mail: hakan.yilmaz@mask_x_1"}, entry)
    assert leaks[f"{entry['value']}/url"] == ["a.properties"]
    # Yerel kisim URL kodlamasinda da acik kalir (nokta kodlanmaz).
    assert leaks["hakan.yilmaz/duz"] == ["a.properties", "b.txt"]
    assert measure.find_leaks({"c.txt": b"mask_email_1"}, entry) == {}


def test_stub_answers_from_manifest_and_injects_deterministic_errors():
    measure = _module()
    manifest = measure.load_manifest()
    found = measure.stub_detection(manifest, 'password=Cnry-Pw-7Q2x9 class UserService')
    assert {f["bulunan_deger"] for f in found} == {"Cnry-Pw-7Q2x9", "UserService"}
    audit = measure.stub_audit(manifest, "jdbc:@cnry-db01.mask_kurumsal_ifade_1.intra")
    assert [f["ilgili_bolum"] for f in audit] == ["cnry-db01"]
    texts = [f"parca {i}" for i in range(400)]
    first = [measure._stub_fails("audit", t, 0.02) for t in texts]
    assert first == [measure._stub_fails("audit", t, 0.02) for t in texts]
    assert 0 < sum(first) < 30
    assert not any(measure._stub_fails("audit", t, 0.0) for t in texts)


@pytest.mark.parametrize("llm_mode", ["off", "stub"])
def test_measurement_runs_end_to_end(tmp_path, llm_mode):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--llm", llm_mode, "--runs", "2", "--out", str(tmp_path)],
        capture_output=True, text=True, timeout=600,
    )
    assert result.returncode == 0, result.stderr[-3000:]
    report = json.loads((tmp_path / f"{llm_mode}.json").read_text(encoding="utf-8"))
    assert (tmp_path / f"{llm_mode}.md").is_file()
    assert report["kosu_sayisi"] == 2
    project = Path(__file__).parent / "fixtures" / "golden" / "project"
    project_files = sum(1 for path in project.rglob("*") if path.is_file())
    for run in report["kosular"]:
        assert run["taranan_dosya"] == project_files
        # Degismez kural 1: yayinlanan her dosya bayt bayt geri alinir.
        assert run["geri_alma"]["farkli"] == []
        assert run["geri_alma"]["cozulemeyen_placeholder"] == 0
        assert run["derleme"]["durum"] in {"basarili", "basarisiz", "atlandi"}
    assert report["determinizm"]["jaccard_min"] == 1.0
    if llm_mode == "stub":
        assert report["stub_istek"] > 0
        assert all(run["llm"]["requests"] > 0 for run in report["kosular"])
    else:
        assert all(run["llm"] == {} for run in report["kosular"])
