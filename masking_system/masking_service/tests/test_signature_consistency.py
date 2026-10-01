"""Moduller arasi imza uyumu (Faz 2a/e): saf AST, hicbir app/ modulu import edilmez.

Karisik surumde (bir modul yeni, cagirani eski) olusan TypeError deseni
preflight'ta acik bir FAIL satiriyla yakalanir.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import signature_consistency

SERVICE = Path(__file__).resolve().parents[1]


@pytest.fixture
def app_copy(tmp_path):
    target = tmp_path / "app"
    shutil.copytree(SERVICE / "app", target, ignore=shutil.ignore_patterns("__pycache__", "BUILD_STAMP.json"))
    return target


def test_clean_tree_has_no_signature_findings():
    stats = {}
    assert signature_consistency.check(SERVICE / "app", stats) == []
    assert stats["checked"] > 100


def _replace(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_old_callee_signature_is_reported(app_copy):
    # TypeError olayinin deseni: cagrilan modul eski (daha kisa) imzayla kopyalanmis.
    _replace(app_copy / "services" / "llm_recognizer.py",
             "    extra_instructions: list[str] | None = None,\n    repair_stats: FindingRepairStats | None = None,\n"
             "    known_spans",
             "    extra_instructions: list[str] | None = None,\n    known_spans")
    findings = signature_consistency.check(app_copy)
    assert any(f.callee == "app.services.llm_recognizer.find_llm_detections"
               and f.caller.startswith("app/services/llm_detector.py:")
               and "bilinmeyen_keyword(repair_stats)" in f.reason for f in findings), findings


def test_missing_function_and_missing_required_argument_are_reported(app_copy):
    _replace(app_copy / "services" / "log_refs.py", "def current_file_label()", "def renamed_label()")
    _replace(app_copy / "services" / "log_refs.py",
             "def file_label(masked_relative_path: str | Path | None, ref: str)",
             "def file_label(masked_relative_path: str | Path | None, ref: str, run_id: int)")
    reasons = {(f.callee, f.reason.split("(")[0]) for f in signature_consistency.check(app_copy)}
    assert ("app.services.log_refs.current_file_label", "tanimsiz") in reasons
    assert ("app.services.log_refs.file_label", "eksik_parametre") in reasons


def test_signature_check_does_not_import_app_modules(app_copy):
    # Yan etki (config, DB, spaCy) olmadan calisir: ayri bir surecte, app/
    # import edilebilir olmayan bir kopya uzerinde.
    result = subprocess.run(
        [sys.executable, str(SERVICE / "scripts" / "signature_consistency.py"), "--app-dir", str(app_copy)],
        capture_output=True, text=True, cwd=app_copy.parent, timeout=120, env={"PATH": ""},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RESULT=OK" in result.stdout


def test_preflight_reports_version_stages(capsys, monkeypatch):
    from scripts import check_llm_preflight as preflight

    assert preflight.check_version_consistency() == 0
    output = capsys.readouterr().out
    assert "build state=" in output and "PASS stage=signature_consistency" in output
