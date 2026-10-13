"""Export oncesi "uzun surebilir" tahmini (app/services/slow_files.py).

Tahminin degeri, exporter'in gercekten tarayacagi dosyalarla ayni kumeye
bakmasindadir: haric tutulan, ikili, arsiv ya da boyut sinirini asan bir
dosya icin uyari vermek kullaniciyi bosuna durdurur. Yukleme modunda arayuz
dosyalari degil yol/boyut listesini gonderir; bu liste klasorun kendisiyle
ayni sonucu vermelidir.
"""

from __future__ import annotations

import io
from pathlib import Path
import zipfile

import pytest
from fastapi.testclient import TestClient

from api_app import app
from app.api.deps import get_request_db
from app.core.config import settings
from app.db.models import ExcludePattern
from app.services.exclude_engine import ExcludeSpec
from app.services.slow_files import FileEntry, SlowFile, directory_entries, find_slow_files
from app.webapp.quick_text import InMemoryUpload
from app.webapp.uploads import save_uploaded_files_to_temp_dir, cleanup_temp_dir, upload_manifest

KB = 1024
WARN = 4 * KB
MAX = 64 * KB


def _slow(entries, specs=(), warn=WARN, max_bytes=MAX):
    return find_slow_files(list(entries), list(specs), warn_bytes=warn, max_bytes=max_bytes)


def test_only_text_files_at_or_above_threshold_are_reported():
    entries = [
        FileEntry("veri/islem.csv", 10 * KB, True),
        FileEntry("veri/esik.csv", WARN, True),
        FileEntry("veri/kucuk.csv", WARN - 1, True),
        FileEntry("veri/ikili.dat", 10 * KB, False),
    ]
    assert _slow(entries) == [SlowFile("veri/islem.csv", 10 * KB), SlowFile("veri/esik.csv", WARN)]


def test_files_that_will_not_be_scanned_are_not_reported():
    entries = [
        FileEntry("lib/paket.jar", 10 * KB, True),       # opak ikili: taranmaz
        FileEntry("yedek.zip", 10 * KB, True),           # arsiv: acilmaz
        FileEntry("dev.csv", MAX + 1, True),             # boyut siniri: taranmaz
        FileEntry("node_modules/x/data.json", 10 * KB, True),  # yerlesik yoksayilan klasor
    ]
    assert _slow(entries) == []


def test_lock_files_are_reported_because_they_are_still_scanned():
    assert _slow([FileEntry("package-lock.json", 10 * KB, True)]) == [SlowFile("package-lock.json", 10 * KB)]


def test_exclude_patterns_apply_to_file_and_parent_directories():
    specs = [ExcludeSpec(1, "csv", "*.csv", "file"), ExcludeSpec(2, "loglar", "loglar", "directory")]
    entries = [
        FileEntry("veri/islem.csv", 10 * KB, True),
        FileEntry("loglar/app.log", 10 * KB, True),
        FileEntry("veri/islem.json", 10 * KB, True),
    ]
    assert _slow(entries, specs) == [SlowFile("veri/islem.json", 10 * KB)]


def test_largest_file_first_and_zero_threshold_disables_warning():
    entries = [FileEntry("a.csv", 5 * KB, True), FileEntry("b\\c.csv", 9 * KB, True)]
    assert _slow(entries) == [SlowFile("b/c.csv", 9 * KB), SlowFile("a.csv", 5 * KB)]
    assert _slow(entries, warn=0) == []


def _project_files() -> dict[str, bytes]:
    return {
        "proje/veri/islem.csv": b"id,ad\n" + b"1,ayse\n" * 2000,
        "proje/veri/resim.bin": b"\x00\x01\x02" * 4000,
        "proje/kod/app.py": b"print('x')\n",
        "proje/node_modules/lib/dev.json": b"{}" * 5000,
        "proje/arsiv.zip": b"PK" * 5000,
    }


@pytest.mark.parametrize("mode", ["directory", "zip"])
def test_upload_manifest_matches_scanning_the_saved_folder(mode):
    files = _project_files()
    if mode == "directory":
        uploads = [InMemoryUpload(name, data) for name, data in files.items()]
    else:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            for name, data in files.items():
                zf.writestr(name, data)
        uploads = [InMemoryUpload("proje.zip", buffer.getvalue())]
    is_directory = mode == "directory"

    from_manifest = _slow(
        [FileEntry(**item) for item in upload_manifest(uploads, is_directory_upload=is_directory)]
    )
    saved = save_uploaded_files_to_temp_dir(uploads, is_directory_upload=is_directory)
    try:
        from_folder = _slow(directory_entries(saved, [], warn_bytes=WARN))
    finally:
        cleanup_temp_dir(saved)

    assert from_manifest == from_folder == [SlowFile("proje/veri/islem.csv", len(files["proje/veri/islem.csv"]))]


def test_upload_manifest_flat_files_use_base_name():
    manifest = upload_manifest([InMemoryUpload("C:\\Users\\a\\veri.csv", b"a,b\n")])
    assert manifest == [{"path": "veri.csv", "size_bytes": 4, "is_text": True}]


@pytest.fixture()
def client(db_session, monkeypatch):
    def _override():
        yield db_session

    monkeypatch.setattr(settings.scan, "slow_file_warn_mb", WARN / (1024 * 1024))
    app.dependency_overrides[get_request_db] = _override
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.pop(get_request_db, None)


def test_upload_preflight_applies_db_exclusions(client, db_session):
    db_session.add(ExcludePattern(pattern_name="test-csv", glob_pattern="*.csv", applies_to="file", is_active=True))
    db_session.flush()
    files = [
        {"path": "veri/islem.csv", "size_bytes": 10 * KB, "is_text": True},
        {"path": "veri/islem.json", "size_bytes": 10 * KB, "is_text": True},
    ]

    resp = client.post("/export/upload/preflight", json={"files": files})

    assert resp.status_code == 200
    body = resp.json()
    assert body["slow_files"] == [{"path": "veri/islem.json", "size_bytes": 10 * KB}]
    assert body["warn_mb"] == pytest.approx(WARN / (1024 * 1024))


def test_path_preflight_reads_folder_inside_allowed_root(client, monkeypatch, tmp_path):
    (tmp_path / "veri").mkdir()
    (tmp_path / "veri" / "islem.csv").write_bytes(b"1,ayse\n" * 2000)
    (tmp_path / "app.py").write_text("x = 1\n")
    monkeypatch.setattr(settings.web, "allowed_roots", str(tmp_path))

    resp = client.post("/export/preflight", json={"source_path": str(tmp_path)})

    assert resp.status_code == 200
    assert resp.json()["slow_files"] == [{"path": "veri/islem.csv", "size_bytes": 14000}]


def test_path_preflight_rejected_outside_allowed_roots(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings.web, "allowed_roots", "")
    resp = client.post("/export/preflight", json={"source_path": str(tmp_path)})
    assert resp.status_code == 400
    assert "Klasör Yolu" in resp.json()["message"]


# --- Kullanicinin uyarida taramadan cikardigi dosyalar (skip_paths) ---


@pytest.fixture()
def export_with_skips(tmp_path, db_session, monkeypatch):
    import asyncio

    from app.services import exporter
    from app.services.detectors import DetectorOutput

    scanned_paths = []

    class RecordingDetector:
        async def scan(self, text, metadata=None):
            scanned_paths.append((metadata or {}).get("file_path"))
            return DetectorOutput()

    monkeypatch.setattr(exporter, "build_orchestrator", lambda *a, **kw: RecordingDetector())
    monkeypatch.setattr(exporter.settings.vllm, "enabled", False)

    def run(files, skip_paths):
        source, target = tmp_path / "source", tmp_path / "output"
        for name, content in files.items():
            (source / name).parent.mkdir(parents=True, exist_ok=True)
            (source / name).write_bytes(content)
        report = asyncio.run(exporter.export_project(
            db_session, source_path=str(source), target_path=str(target), project_name=tmp_path.name,
            sicil_no="TEST-SKIP", branch_name="test", initiated_by="TEST-SKIP", enable_path_masking=False,
            skip_paths=skip_paths,
        ))
        return report, source, target, scanned_paths

    return run


def test_user_skipped_file_is_neither_scanned_nor_copied(export_with_skips, db_session):
    from sqlalchemy import select

    from app.api.schemas import ExportReportOut
    from app.db.models import AuditLog

    files = {"veri/buyuk.csv": b"1,ayse\n" * 100, "app.py": b"value = 1\n"}
    # Windows ayracli ve listede olmayan bir yol da gelebilir: biri eslesir, digeri yok sayilir.
    report, source, target, scanned = export_with_skips(files, ["veri\\buyuk.csv", "yok/boyle.txt"])

    assert report.user_skipped_paths == ["veri/buyuk.csv"]
    assert report.files_excluded == 1
    assert "veri/buyuk.csv" not in scanned and "app.py" in scanned
    assert not (target / "veri" / "buyuk.csv").exists()
    assert (target / "app.py").read_bytes() == b"value = 1\n"
    assert (source / "veri" / "buyuk.csv").exists()  # klasor modunda kaynak dosyaya dokunulmaz
    details = [r.detail for r in db_session.scalars(
        select(AuditLog).where(AuditLog.run_id == report.run_id, AuditLog.file_path.like("%buyuk.csv"))
    )]
    assert any("kullanici secimiyle taramadan cikarildi" in d for d in details)
    assert not any("guvenlik politikasi" in d for d in details)
    assert ExportReportOut.model_validate(report).user_skipped_paths == ["veri/buyuk.csv"]
    summary = report.summary_text()
    assert "1 buyuk dosya kullanici secimiyle taramadan cikarildi" in summary
    assert "haric tutma kurali geregi" not in summary
    assert "buyuk.csv" not in summary  # haric tutulan dosyanin yolu maskelenmez (kural 7)


def test_upload_job_forwards_skip_paths(client, monkeypatch):
    from types import SimpleNamespace

    from app.api.routers import export as export_router

    captured = {}

    def fake_work(kwargs, token):
        captured.update(kwargs)
        return lambda progress, cancel_event: {}

    monkeypatch.setattr(export_router, "_export_job_work", fake_work)
    monkeypatch.setattr(export_router.export_jobs, "start_job", lambda *a, **kw: SimpleNamespace(job_id="job-x"))
    data = {
        "project_name": "SkipTest", "sicil_no": "P-SKIP-1", "branch_name": "main", "initiated_by": "P-SKIP-1",
        "skip_paths": ["veri/a.csv", "veri/b.jsonl"],
    }
    files = [("files", ("a.txt", b"x", "text/plain"))]

    resp = client.post("/export/upload/jobs", data=data, files=files)

    assert resp.status_code == 202, resp.text
    assert captured["skip_paths"] == ["veri/a.csv", "veri/b.jsonl"]
    cleanup_temp_dir(Path(captured["source_path"]), Path(captured["target_path"]))
