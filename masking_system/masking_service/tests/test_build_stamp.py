"""Surum damgasi (Faz 2a/e): TypeError olayinin (karisik surum) tekrarini onler.

- BUILD_STAMP.json yok: yalnizca uyari, export calisir.
- Damga var ama uyusmuyor (ya da kod surec basladiktan sonra degisti): backend
  acilir, export uc noktalari 503 ile reddeder; health bunu gosterir.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.core import build_info
from app.core.build_info import BuildStatus, check_build, ensure_export_allowed, write_stamp
from app.core.exceptions import BuildMismatchError

SERVICE = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(db_session):
    from fastapi.testclient import TestClient

    from app.api.deps import get_request_db
    from app.api.main import app

    def _override():
        yield db_session

    app.dependency_overrides[get_request_db] = _override
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.pop(get_request_db, None)


@pytest.fixture
def app_copy(tmp_path):
    target = tmp_path / "app"
    shutil.copytree(SERVICE / "app", target, ignore=shutil.ignore_patterns("__pycache__", build_info.STAMP_NAME))
    return target


def test_no_stamp_only_warns(app_copy):
    status = check_build(app_copy)
    assert status.state == "no_stamp" and not status.blocks_export


def test_stamp_detects_changed_missing_and_extra_files(app_copy):
    stamp = write_stamp(app_copy, commit="abc123", built_at="2026-10-01T00:00:00+00:00")
    assert check_build(app_copy).state == "ok"
    assert stamp["commit"] == "abc123" and "services/exporter.py" in stamp["files"]

    (app_copy / "services" / "extra_old_module.py").write_text("x = 1\n", encoding="utf-8")
    status = check_build(app_copy)
    assert status.state == "ok" and status.extra == ("services/extra_old_module.py",)

    target = app_copy / "services" / "llm_runtime.py"
    target.write_text(target.read_text(encoding="utf-8") + "\n# eski surum\n", encoding="utf-8")
    (app_copy / "services" / "audit_reviewer.py").unlink()
    status = check_build(app_copy)
    assert status.state == "mismatch" and status.blocks_export
    assert status.mismatched == ("services/llm_runtime.py",)
    assert status.missing == ("services/audit_reviewer.py",)
    assert "services/llm_runtime.py" in status.message() and "abc123" in status.message()
    assert status.line().isascii()


def test_crlf_copy_matches_lf_stamp(app_copy):
    write_stamp(app_copy, commit="abc123")
    target = app_copy / "services" / "exporter.py"
    target.write_bytes(target.read_bytes().replace(b"\n", b"\r\n"))
    assert check_build(app_copy).state == "ok"


def test_unreadable_stamp_fails_closed(app_copy):
    (app_copy / build_info.STAMP_NAME).write_text("{bozuk", encoding="utf-8")
    assert check_build(app_copy).state == "mismatch"


def _mismatch() -> BuildStatus:
    return BuildStatus("mismatch", "abc123", "stamp", "000000000000", mismatched=("services/llm_runtime.py",))


def test_export_is_refused_on_mismatch(monkeypatch):
    monkeypatch.setattr(build_info, "current_build_status", _mismatch)
    with pytest.raises(BuildMismatchError, match="karisik surumde"):
        ensure_export_allowed()


def test_export_is_refused_when_code_changed_after_start(monkeypatch):
    started = BuildStatus("ok", "abc123", "stamp", "ffffffffffff")
    monkeypatch.setattr(build_info, "current_build_status", lambda: started)
    with pytest.raises(BuildMismatchError, match="yeniden başlatın"):
        ensure_export_allowed()


def test_api_rejects_export_and_health_reports_mismatch(client, monkeypatch, tmp_path):
    from app.api import main as api_main

    monkeypatch.setattr(build_info, "current_build_status", _mismatch)
    monkeypatch.setattr(api_main, "current_build_status", _mismatch)
    payload = dict(source_path=str(tmp_path), target_path=str(tmp_path / "out"), project_name="p",
                   sicil_no="s", branch_name="b", initiated_by="s")
    for url in ("/export", "/export/jobs"):
        resp = client.post(url, json=payload)
        assert resp.status_code == 503, (url, resp.text)
        assert "karisik surumde" in resp.json()["message"]
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["build"]["state"] == "mismatch" and "app/ klasörünün" in health.json()["build_message"]


def test_export_project_itself_refuses_on_mismatch(db_session, monkeypatch, tmp_path):
    import asyncio

    from app.services.exporter import export_project

    monkeypatch.setattr(build_info, "current_build_status", _mismatch)
    with pytest.raises(BuildMismatchError):
        asyncio.run(export_project(db_session, source_path=str(tmp_path), project_name="p", sicil_no="s",
                                   branch_name="b", target_path=str(tmp_path / "out"), initiated_by="s"))
