"""Altin test kumesinin (tests/fixtures/golden) kendi icinde tutarli oldugunu dogrular.

Olcum betigi (scripts/measure_golden.py) bu manifeste guvenir: manifestte
olup projede gecmeyen bir deger sizinti/recall olcumunu sessizce bozar.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "golden"


def _manifest() -> dict:
    return json.loads((GOLDEN_DIR / "expected.json").read_text(encoding="utf-8"))


def _project_text() -> str:
    project = GOLDEN_DIR / _manifest()["project_dir"]
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(project.rglob("*")) if path.is_file())


def test_every_manifest_value_occurs_in_project():
    manifest = _manifest()
    text = _project_text()
    for entry in manifest["sensitive"]:
        assert entry["value"] in text, entry["id"]
        for variant in entry["variants"]:
            assert variant in text, (entry["id"], variant)
    for value in manifest["must_not_mask"]:
        assert value in text, value
    for entry in manifest["stub_false_positives"]:
        assert entry["value"] in text, entry["value"]


def test_manifest_ids_and_dictionary_terms_are_consistent():
    manifest = _manifest()
    ids = [entry["id"] for entry in manifest["sensitive"]]
    assert len(ids) == len(set(ids))
    dictionary_values = {entry["value"] for entry in manifest["sensitive"] if entry["kaynak"] == "dictionary"}
    assert {term["term"] for term in manifest["dictionary_terms"]} == dictionary_values
    for entry in manifest["sensitive"]:
        if entry["kaynak"] == "llm":
            assert "llm" in entry or entry.get("denetim"), entry["id"]
    assert any(entry["canary"] for entry in manifest["sensitive"])


def test_runtime_entries_match_measurement_identity():
    # 'runtime' girisleri export'a verilen kimlikten gelir; deger IDENTITY ile ayni olmali.
    import importlib.util

    script = Path(__file__).resolve().parents[1] / "scripts" / "measure_golden.py"
    spec = importlib.util.spec_from_file_location("measure_golden_identity", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    runtime = [entry["value"] for entry in _manifest()["sensitive"] if entry["kaynak"] == "runtime"]
    assert runtime == [module.IDENTITY[1]]


@pytest.mark.skipif(shutil.which("javac") is None, reason="javac yok")
def test_unmasked_golden_java_project_compiles(tmp_path):
    sources = [str(path) for path in sorted(GOLDEN_DIR.rglob("*.java"))]
    result = subprocess.run(["javac", "-d", str(tmp_path), *sources], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
