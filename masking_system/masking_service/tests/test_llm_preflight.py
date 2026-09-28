"""Distinguish requests=0 preparation/admission failures from response failures."""
import asyncio
import logging
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from app.services import llm_recognizer
from scripts import check_llm_preflight as preflight


def settings(**changes):
    return SimpleNamespace(**(dict(enabled=True, host="http://llm.test", model="synthetic",
                                  timeout_seconds=1, max_file_chars=6000,
                                  max_concurrent_requests=1) | changes))


def test_offline_checks_run_without_http_client(monkeypatch, capsys):
    import httpx

    def forbidden(*args, **kwargs):
        raise AssertionError("HTTP client must never be constructed")

    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    original_caller = llm_recognizer.call_vllm
    assert asyncio.run(preflight.check_paths(settings(), ["PRIVATE_INSTRUCTION"])) == 0
    output = capsys.readouterr().out
    assert "PASS stage=detection_without_rules" in output
    assert "PASS stage=detection_with_db_rules" in output
    assert "PASS stage=audit" in output
    assert "PRIVATE" not in output
    assert llm_recognizer.call_vllm is original_caller


def test_old_request_builder_signature_reproduces_zero_requests_and_audit_success(monkeypatch, caplog, capsys):
    # Deliberately emulate a caller/builder mismatch; this is a diagnostic
    # control, not evidence that the intranet has this mismatch.
    def old_builder(text, model, seed, extra_instructions=None, max_tokens=512):
        raise AssertionError("Argument binding should fail before the body")

    monkeypatch.setattr(llm_recognizer, "build_detection_request", old_builder)
    with caplog.at_level(logging.INFO, logger="uvicorn.error.llm"):
        assert asyncio.run(preflight.check_paths(settings(), [])) == 2
    output = capsys.readouterr().out
    assert "FAIL stage=detection_without_rules error_type=TypeError" in output
    assert "PASS stage=audit" in output
    assert "llm_recognizer.py:" in output
    logs = [r.getMessage() for r in caplog.records]
    assert any("phase=detection chunks=1 completed=0 requests=0" in line and
               "error_type=TypeError" in line for line in logs)
    assert any("phase=audit chunks=1 completed=1 requests=1" in line and
               "status=ok" in line for line in logs)


def test_invalid_gate_limit_is_distinguished_from_detection_only_error(caplog, capsys):
    with caplog.at_level(logging.INFO, logger="uvicorn.error.llm"):
        assert asyncio.run(preflight.check_paths(settings(max_concurrent_requests="1"), [])) == 3
    output = capsys.readouterr().out
    assert "FAIL stage=audit error_type=TypeError" in output
    assert "_gate" in output
    assert not any("llm_request " in record.getMessage() for record in caplog.records)


def test_prompt_error_does_not_expose_instruction_or_exception_text(monkeypatch, capsys):
    real_augment = llm_recognizer._augment_prompt

    def broken_augment(base, instructions):
        if instructions:
            raise TypeError("PRIVATE_EXCEPTION " + instructions[0])
        return real_augment(base, instructions)

    monkeypatch.setattr(llm_recognizer, "_augment_prompt", broken_augment)
    assert asyncio.run(preflight.check_paths(settings(), ["PRIVATE_RULE"])) == 1
    output = capsys.readouterr().out
    assert "PASS stage=detection_without_rules" in output
    assert "FAIL stage=detection_with_db_rules error_type=TypeError" in output
    assert "PASS stage=audit" in output
    assert "PRIVATE" not in output


def test_missing_database_is_not_created(tmp_path):
    path = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        preflight.read_instructions(path)
    assert not path.exists()


def test_db_rules_match_active_detection_scope_without_mutation(tmp_path):
    path = tmp_path / "rules.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE filtre_kurallari (aciklama, aktif_mi, desen_tipi, kaynak_katman, kurumsal_terim_silinme_tarihi, oncelik)")
        db.executemany("INSERT INTO filtre_kurallari VALUES (?, ?, ?, ?, ?, ?)", [
            ("PRIVATE_SECOND", 1, "llm", "katman1", None, 20),
            ("PRIVATE_FIRST", 1, "llm", "llm", None, 10),
            ("DISABLED", 0, "llm", "llm", None, 1),
            ("DELETED", 1, "llm", "llm", "2026-01-01", 1),
            ("OTHER_LAYER", 1, "llm", "katman2_presidio", None, 1),
            ("OTHER_TYPE", 1, "regex", "katman1", None, 1),
        ])
    before = path.read_bytes()
    assert preflight.read_instructions(path) == ["PRIVATE_FIRST", "PRIVATE_SECOND"]
    assert path.read_bytes() == before


def test_disabled_llm_is_not_reported_as_success(capsys):
    assert asyncio.run(preflight.check_paths(settings(enabled=False), [])) == 3
    assert "PASS" not in capsys.readouterr().out
