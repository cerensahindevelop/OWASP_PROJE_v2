"""FastAPI backend katmani (app/api/*) icin testler.

`db_session` fixture'i (bkz. conftest.py) transaction-rollback ile izole
edildigi icin bu testler gercek veritabaninda kalici hicbir iz birakmaz.
`app.dependency_overrides` ile get_request_db, testin kendi transaction-
scoped session'ina yonlendirilir - boylece TestClient cagrilari da diger
testlerle ayni izolasyon garantisine sahip olur.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.deps import get_request_db
from app.api.main import app
from app.api.routers import downloads
from app.db.models import MaskingContext, MaskingRun, ReviewQueue


def _make_context(db_session, suffix: str) -> MaskingContext:
    context = MaskingContext(
        project_name=f"pytest-api-{suffix}", sicil_no="P-API-0001", branch_name="pytest-branch"
    )
    db_session.add(context)
    db_session.flush()
    return context


def _make_run(db_session, context: MaskingContext, **overrides) -> MaskingRun:
    defaults = dict(
        context_id=context.id,
        operation_type="mask",
        source_path="/tmp/api-src",
        target_path="/tmp/api-dst",
        initiated_by="P-API-0001",
        status="in_progress",
    )
    defaults.update(overrides)
    run = MaskingRun(**defaults)
    db_session.add(run)
    db_session.flush()
    return run


def _make_review(db_session, run: MaskingRun, **overrides) -> ReviewQueue:
    defaults = dict(
        run_id=run.id,
        file_path="src/app.py",
        found_value="Ahmet Yilmaz",
        entity_type="PERSON",
        confidence_level="orta",
        reason="Muhtemel kisi adi",
    )
    defaults.update(overrides)
    item = ReviewQueue(**defaults)
    db_session.add(item)
    db_session.flush()
    return item


@pytest.fixture()
def client(db_session):
    def _override():
        yield db_session

    app.dependency_overrides[get_request_db] = _override
    try:
        # raise_server_exceptions=False: TestClient'in varsayilani, kayitli
        # bir exception handler tarafindan uretilen yaniti OKUMAK yerine
        # orijinal exception'i tekrar firlatir (debug/traceback gorunurlugu
        # icin) - biz burada tam olarak o handler'in (app/api/errors.py)
        # urettigi status/govde ciftini test etmek istiyoruz.
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.pop(get_request_db, None)


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.parametrize('branch', ['feature-java-check', 'main'])
def test_class_upload_masks_binary_constants_with_real_rules(client, monkeypatch, tmp_path, branch):
    import base64
    from pathlib import Path
    from app.services import exporter
    from app.services.detectors import DetectorRegistry, DetectionOrchestrator, RuleBasedDetector
    from app.webapp import uploads

    def build(rules, params, *args, **kwargs):
        registry = DetectorRegistry()
        registry.register(RuleBasedDetector(rules, params))
        return DetectionOrchestrator(registry)
    monkeypatch.setattr(exporter, "build_orchestrator", build)
    monkeypatch.setattr(exporter.settings.vllm, "enabled", False)
    monkeypatch.setattr(uploads, "UPLOADS_OUTPUT_ROOT", tmp_path)
    raw = base64.decodebytes((Path(__file__).parent/'fixtures/java/Sample.class.b64').read_bytes())
    response = client.post('/export/upload', files={'files':('Sample.class',raw,'application/octet-stream')}, data={
        'project_name':'pytest-class-api', 'sicil_no':'CLASS-TEST', 'branch_name':branch,
        'initiated_by':'test', 'is_directory_upload':'false',
    })
    assert response.status_code == 200, response.text
    body = response.json()
    if branch == 'main':
        # If policy treats "main" as sensitive, changing the JVM method name
        # is outside literal masking support and must not silently pass.
        assert body['validation_failed_count'] == 1
        assert 'yapısal' in body['report']['outcomes'][0]['error']
        assert not (tmp_path/body['output_token']/'Sample.class').exists()
        return
    assert body['report']['files_masked'] == 1, body['report']['outcomes']
    assert body['validation_failed_count'] == 0
    assert body['report']['validation_warnings']
    masked = (tmp_path/body['output_token']/'Sample.class').read_bytes()
    assert masked.startswith(b'\xca\xfe\xba\xbe')
    assert b'alice@example.com' not in masked
    assert b'localSecretValue123' not in masked


@pytest.mark.parametrize("mode", ["files", "zip", "directory"])
def test_upload_reports_unsupported_content_without_release_request(client, monkeypatch, tmp_path, mode):
    from app.services import exporter
    from app.services.detectors import DetectorOutput
    from app.webapp import uploads

    class TextDetector:
        async def scan(self, text, metadata=None):
            return DetectorOutput()

    monkeypatch.setattr(exporter, "build_orchestrator", lambda *a, **kw: TextDetector())
    monkeypatch.setattr(exporter.settings.vllm, "enabled", False)
    monkeypatch.setattr(uploads, "UPLOADS_OUTPUT_ROOT", tmp_path)
    prefix = "assets/" if mode == "directory" else ""
    files = [("files", (prefix + "blob.bin", bytes(range(256)), "application/octet-stream"))]
    if mode == "zip":
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("assets/blob.bin", bytes(range(256)))
        files = [("files", ("project.zip", buffer.getvalue(), "application/zip"))]
    response = client.post("/export/upload", files=files, data={
        "project_name": "pytest-unsupported-upload-" + mode, "sicil_no": "P-OPAQUE",
        "branch_name": "main", "initiated_by": "P-OPAQUE",
        "is_directory_upload": str(mode == "directory").lower(),
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["validation_failed_count"] == body["quarantined_count"] == body["pending_count"] == 0
    assert body["report"]["files_skipped_unsupported"] == 1
    assert body["report"]["files_ready"] == 0
    assert body["report"]["status"] == "completed_with_warnings"
    assert not list((tmp_path / body["output_token"]).rglob("*.bin"))


def test_audit_warning_api_exposes_summary_and_location_without_file_content(client, db_session):
    from app.db.models import AuditWarning

    context = _make_context(db_session, "audit-location")
    run = _make_run(db_session, context, status="completed_with_warnings")
    item = AuditWarning(
        run_id=run.id, file_path="query.sql", masked_content="private file content",
        reasoning="Kurumsal terim kontrolü: 1 açık eşleşme kaldı.\nSatır 9, sütun 3: proje (kurumsal_terim_hash)",
        audit_failed=False,
    )
    db_session.add(item)
    db_session.flush()
    response = client.get("/audit-warnings", params={
        "project_name": context.project_name, "sicil_no": context.sicil_no,
        "branch_name": context.branch_name,
    })
    assert response.status_code == 200
    row = response.json()[0]
    assert row["location"] == "Satır 9, sütun 3"
    assert row["file_path"] == "query.sql"
    assert "proje" in row["summary"]
    assert row["evidence"][0]["line"] == 9
    assert "masked_content" not in row
    assert "private file content" not in response.text


def test_export_in_progress_maps_to_409_not_400(client, db_session):
    """ExportInProgressError bir ValueError DEGIL bir MaskingSystemError'dur -
    genel bir except ValueError'a yakalanip yanlislikla 400/'beklenmeyen
    hata' donmemeli (bkz. app/core/error_translation.py docstring'i,
    gecmiste tam olarak bu hataya dusulmustu)."""
    context = _make_context(db_session, "in-progress")
    _make_run(db_session, context, operation_type="mask", status="in_progress")

    files = {"files": ("ornek.txt", b"merhaba", "text/plain")}
    data = {
        "project_name": context.project_name,
        "sicil_no": context.sicil_no,
        "branch_name": context.branch_name,
        "initiated_by": context.sicil_no,
        "is_directory_upload": "false",
    }
    resp = client.post("/export/upload", data=data, files=files)

    assert resp.status_code == 409
    body = resp.json()
    assert "devam eden" in body["message"]


def test_review_double_approve_conflicts_with_409(client, db_session):
    context = _make_context(db_session, "double-approve")
    run = _make_run(db_session, context, status="completed")
    review = _make_review(db_session, run)

    first = client.post(f"/reviews/{review.id}/approve")
    assert first.status_code == 200
    assert first.json()["status"] == "approved"

    second = client.post(f"/reviews/{review.id}/approve")
    assert second.status_code == 409


def test_export_path_mode_rejected_when_allowed_roots_not_configured(client):
    """WEB_ALLOWED_ROOTS bos oldugu surece (varsayilan) 'Klasor Yolu' modu
    TAMAMEN kapali olmali - istemci taraf bir kontrol yapmasa/atlasa bile
    sunucu bunu reddetmeli (bkz. plan dosyasi - artik ag uzerinden gelen
    guvenilmeyen bir girdi)."""
    resp = client.post(
        "/export",
        json={
            "source_path": "/tmp/whatever-src",
            "target_path": "/tmp/whatever-dst",
            "project_name": "PathModeTest",
            "sicil_no": "P-PATH-0001",
            "branch_name": "main",
            "initiated_by": "P-PATH-0001",
        },
    )
    assert resp.status_code == 400
    assert "Klasör Yolu" in resp.json()["message"]


def test_export_upload_zip_slip_rejected(client):
    """Zip-slip korumasi (app/webapp/uploads.py::_safe_extract_zip),
    FastAPI UploadFile -> Streamlit-shaped adapter uzerinden AYNEN reuse
    ediliyor - adapter'daki bir hata bu korumayi sessizce bozabilirdi."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("../evil.txt", "kotu niyetli icerik")
    buffer.seek(0)

    files = {"files": ("kotu.zip", buffer.getvalue(), "application/zip")}
    data = {
        "project_name": "ZipSlipTest",
        "sicil_no": "P-ZIP-0001",
        "branch_name": "main",
        "initiated_by": "P-ZIP-0001",
        "is_directory_upload": "false",
    }
    resp = client.post("/export/upload", data=data, files=files)

    assert resp.status_code == 400


def test_upload_output_download_does_not_depend_on_committed_run(tmp_path, monkeypatch):
    """Upload POST'undan hemen sonraki indirme DB commit'iyle yarismamali.

    Burada bilerek hic MaskingRun satiri olusturulmuyor. Token'in gosterdigi
    cikti hazirsa endpoint paketi dondurebilmeli; eski run-id endpoint'i bu
    anda db.get(...) -> None nedeniyle gecici 404 uretiyordu.
    """
    output_root = tmp_path / "uploads_output"
    token = "0123456789abcdef"
    target = output_root / token
    target.mkdir(parents=True)
    (target / "nested").mkdir()
    (target / "nested" / "masked.txt").write_text("mask_email_1", encoding="utf-8")
    monkeypatch.setattr(downloads, "UPLOADS_OUTPUT_ROOT", output_root)

    resp = downloads.download_uploaded_export_output(token)

    assert resp.status_code == 200
    assert resp.media_type == "application/zip"
    with zipfile.ZipFile(io.BytesIO(resp.body)) as archive:
        assert archive.namelist() == ["nested/masked.txt"]
        assert archive.read("nested/masked.txt") == b"mask_email_1"


@pytest.mark.parametrize("token", ["../masking.db", "not-a-token", "a" * 15, "A" * 16])
def test_upload_output_download_rejects_invalid_token(token):
    with pytest.raises(HTTPException) as exc_info:
        downloads.download_uploaded_export_output(token)
    assert exc_info.value.status_code == 404


def test_term_upload_preview_then_commit_round_trip(client):
    """Onizleme ile onay arasinda sunucuda HICBIR taslak cache'lenmez -
    istemci ayni byte'lari iki cagriya da kendisi gonderir (bkz. plan
    dosyasi)."""
    content = b"AtlasProjesi\n"

    preview_resp = client.post(
        "/term-upload/preview",
        data={"category": "pytest_kategori"},
        files={"file": ("terimler.txt", content, "text/plain")},
    )
    assert preview_resp.status_code == 200
    preview = preview_resp.json()
    assert preview["new_valid"] == ["AtlasProjesi"]
    assert preview["new_count"] == 1

    commit_resp = client.post(
        "/term-upload/commit",
        data={"category": "pytest_kategori"},
        files={"file": ("terimler.txt", content, "text/plain")},
    )
    assert commit_resp.status_code == 200
    result = commit_resp.json()
    assert result["added_count"] == 1
    assert result["category"] == "pytest_kategori"


def test_corporate_term_list_and_delete_api(client):
    content = b"Zeta Internal Api Term\n"
    commit_resp = client.post(
        "/term-upload/commit",
        data={"category": "pytest_api_term_management"},
        files={"file": ("terimler.txt", content, "text/plain")},
    )
    assert commit_resp.status_code == 200

    list_resp = client.get("/term-upload/terms")
    assert list_resp.status_code == 200
    selected = next(
        item
        for item in list_resp.json()
        if item["category"] == "pytest_api_term_management"
    )
    assert selected["term"] == "Zeta Internal Api Term"
    assert selected["is_active"] is True

    delete_resp = client.delete(f"/term-upload/terms/{selected['id']}")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["is_active"] is False
    assert all(item["id"] != selected["id"] for item in client.get("/term-upload/terms").json())

    deleted_rows = client.get("/term-upload/terms", params={"include_deleted": True}).json()
    deleted = next(item for item in deleted_rows if item["id"] == selected["id"])
    assert deleted["deleted_at"] is not None


def test_get_request_db_commits_on_success_and_rolls_back_on_exception(monkeypatch):
    """app/api/deps.py::get_request_db - TestClient/gercek DB'ye dokunmadan,
    generator'in kendi commit/rollback disiplinini sahte bir Session ile
    dogrudan dogrular."""
    import app.api.deps as deps_module

    class _FakeSession:
        def __init__(self) -> None:
            self.committed = False
            self.rolled_back = False
            self.closed = False

        def commit(self) -> None:
            self.committed = True

        def rollback(self) -> None:
            self.rolled_back = True

        def close(self) -> None:
            self.closed = True

    created: list[_FakeSession] = []

    def _fake_session_local():
        session = _FakeSession()
        created.append(session)
        return session

    monkeypatch.setattr(deps_module, "SessionLocal", _fake_session_local)

    # Basari yolu: generator normal tuketilirse commit edilir, rollback edilmez.
    gen_ok = deps_module.get_request_db()
    session_ok = next(gen_ok)
    with pytest.raises(StopIteration):
        next(gen_ok)
    assert session_ok.committed is True
    assert session_ok.rolled_back is False
    assert session_ok.closed is True

    # Hata yolu: generator'a exception enjekte edilirse rollback edilir, commit edilmez.
    gen_err = deps_module.get_request_db()
    session_err = next(gen_err)
    with pytest.raises(ValueError):
        gen_err.throw(ValueError("boom"))
    assert session_err.committed is False
    assert session_err.rolled_back is True
    assert session_err.closed is True


@pytest.mark.parametrize('endpoint', ['/export/upload', '/unmask/upload'])
def test_duplicate_upload_returns_400(client, endpoint):
    response = client.post(endpoint, data={
        'project_name': 'duplicate-test', 'sicil_no': 'test',
        'branch_name': 'main', 'initiated_by': 'test',
    }, files=[('files', ('same.txt', b'first')), ('files', ('same.txt', b'second'))])
    assert response.status_code == 400
    assert 'aynı hedefe' in response.json()['message']


def test_unmask_cleanup_failure_returns_500_and_attempts_both(client, tmp_path, monkeypatch):
    from app.api.routers import unmask
    from app.webapp import uploads

    source, target = tmp_path / 'source', tmp_path / 'target'
    source.mkdir()
    target.mkdir()
    monkeypatch.setattr(unmask, 'save_uploaded_files_to_temp_dir', lambda *a, **kw: source)
    monkeypatch.setattr(unmask.tempfile, 'mkdtemp', lambda **kw: str(target))
    monkeypatch.setattr(unmask, 'unmask_project', lambda *a, **kw: object())
    original = uploads.shutil.rmtree
    attempted = []

    def remove(path):
        attempted.append(path)
        if path == source:
            raise PermissionError('simulated lock')
        original(path)

    monkeypatch.setattr(uploads.shutil, 'rmtree', remove)
    response = client.post('/unmask/upload', data={
        'project_name': 'cleanup-test', 'sicil_no': 'test',
        'branch_name': 'main', 'initiated_by': 'test',
    }, files=[('files', ('file.txt', b'content'))])
    assert response.status_code == 500
    assert 'hassas veri diskte' in response.json()['detail']
    assert attempted == [source, target]
    assert not target.exists()
    assert 'download_base64' not in response.json()


@pytest.mark.parametrize('mode', ['upload', 'path'])
def test_unmask_job_id_selects_only_its_mapping(client, db_session, tmp_path, monkeypatch, mode):
    import base64
    from app.api.routers import unmask as router_module
    from app.services.detectors import synthetic_llm_rule
    from app.services.mapping_service import get_or_create_mapping

    monkeypatch.setattr(router_module, 'ensure_path_allowed', lambda *a, **kw: None)
    context = _make_context(db_session, 'job-scoped-' + mode)
    identity = dict(project_name=context.project_name, sicil_no=context.sicil_no,
                    branch_name=context.branch_name, initiated_by='test')
    jobs = []
    for original in ['10.10.10.1', '192.168.1.50']:
        job = _make_run(db_session, context, mapping_version=2, status='completed')
        mapping, _ = get_or_create_mapping(db_session, context.id, synthetic_llm_rule('IP'), original, run_id=job.id)
        assert mapping.placeholder_value == 'mask_ip_1'
        jobs.append((job.id, original))
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'sample.txt').write_text('mask_ip_1')
    target = tmp_path / 'restored'

    def request(job_id=None):
        payload = dict(identity)
        if job_id is not None:
            payload['job_id'] = job_id
        if mode == 'upload':
            return client.post('/unmask/upload', data=payload,
                               files=[('files', ('sample.txt', b'mask_ip_1', 'text/plain'))])
        return client.post('/unmask', json=dict(payload, source_path=str(source), target_path=str(target)))

    missing = request()
    assert missing.status_code == 400, missing.text
    assert 'islem kimligi eksik' in missing.text
    assert not target.exists()
    for job_id, original in jobs:
        response = request(job_id)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['report']['job_id'] == job_id
        if mode == 'upload':
            assert base64.b64decode(result['download_base64']).decode() == original
        else:
            assert (target / 'sample.txt').read_text() == original


def test_pending_lists_hide_unfinished_runs(client, db_session):
    """Bitmemis/basarisiz bir export'un kayitlari onay ekraninda gorunmemeli:
    hedef klasor o islem icin yayimlanmadi."""
    from app.db.models import AuditWarning

    context = _make_context(db_session, "audit-unfinished")
    for status in ("in_progress", "failed"):
        run = _make_run(db_session, context, status=status)
        db_session.add(AuditWarning(
            run_id=run.id, file_path="a.txt", masked_content="x", reasoning="r", audit_failed=False,
        ))
    db_session.flush()
    params = {"project_name": context.project_name, "sicil_no": context.sicil_no,
              "branch_name": context.branch_name}
    assert client.get("/audit-warnings", params=params).json() == []
    assert client.get("/reviews", params=params).json() == []
