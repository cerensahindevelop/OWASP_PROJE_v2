"""Regression tests for:

1. Files released from quarantine after a review decision must land on the
   MASKED output path (the source path may contain the project/org name),
   be recorded in the signed integrity manifest, and pass the run-wide
   consistency check.
2. LLM confidence routing: findings at/above VLLM_AUTO_MASK_MIN_CONFIDENCE are
   masked without waiting for a human; lower ones are ignored (default) or
   sent to review.
3. Post-mask audit: only findings whose clear-text quote is verified in the
   masked text can quarantine a file.
4. Free-form LLM entity types never reach placeholder names.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text as sqltext

from app.db.session import SessionLocal
from app.db.models import AuditWarning, ReviewQueue
from app.services import exporter as exporter_module
from app.services.audit_reviewer import AuditFinding, verify_audit_findings
from app.services.detectors import normalize_llm_entity_type, synthetic_llm_rule
from app.services.integrity_manifest import read_manifest
from app.services.review_service import ReviewService
from tests.test_exporter_failure_handling import _run_export


def _cleanup(project_name: str) -> None:
    with SessionLocal() as db:
        row = db.execute(
            sqltext("SELECT id FROM maskeleme_baglamlari WHERE proje_adi=:p"), {"p": project_name}
        ).first()
        if row is None:
            return
        context_id = row[0]
        run_ids = [r[0] for r in db.execute(
            sqltext("SELECT id FROM maskeleme_calismalari WHERE baglam_id=:c"), {"c": context_id}
        ).all()]
        db.execute(sqltext("DELETE FROM ogrenilen_bulgu_kararlari WHERE baglam_id=:c"), {"c": context_id})
        for run_id in run_ids:
            for table in ("denetim_kaydi", "gozden_gecirme_kuyrugu", "denetim_uyarilari", "islem_yer_tutucu_sayaclari"):
                db.execute(sqltext(f"DELETE FROM {table} WHERE calisma_id=:r"), {"r": run_id})
        db.execute(sqltext("DELETE FROM deger_eslemeleri WHERE baglam_id=:c"), {"c": context_id})
        db.execute(sqltext("DELETE FROM maskeleme_calismalari WHERE baglam_id=:c"), {"c": context_id})
        db.execute(sqltext("DELETE FROM maskeleme_baglamlari WHERE id=:c"), {"c": context_id})
        db.commit()


def _project() -> str:
    # Letters only: a classifiable, unique project name for path masking.
    return "Zeph" + "".join(chr(ord("a") + int(c, 16) % 26) for c in uuid.uuid4().hex[:8])


def _fake_llm(monkeypatch, *, detections, audit=None):
    from app.services import audit_reviewer, llm_recognizer

    calls = {"detection": 0, "audit": 0}

    async def fake(host, timeout, payload, api_key=None):
        if payload["response_format"]["json_schema"]["name"] == "denetim_semasi":
            calls["audit"] += 1
            data = audit(payload["messages"][1]["content"]) if audit else {"risk_var": False, "bulgular": []}
        else:
            calls["detection"] += 1
            data = {"bulgular": detections}
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(data)}}]}

    s = exporter_module.settings.vllm
    monkeypatch.setattr(s, "enabled", True)
    monkeypatch.setattr(s, "host", "http://fake")
    monkeypatch.setattr(s, "model", "fake")
    monkeypatch.setattr(llm_recognizer, "call_vllm", fake)
    monkeypatch.setattr(audit_reviewer, "call_vllm", fake)
    return calls


def _finding(value, confidence, tip="IC_SERVIS_ADI"):
    return {"bulunan_deger": value, "tip": tip, "guven_seviyesi": confidence, "gerekce": "ic servis"}


def _tree(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


def test_review_release_writes_masked_path_and_manifest(tmp_path, monkeypatch):
    project = _project()
    try:
        source = tmp_path / "source"
        (source / f"{project}-core").mkdir(parents=True)
        (source / f"{project}-core" / "notes.txt").write_text("service: qorvexa gateway\n", encoding="utf-8")
        (source / "other.txt").write_text("plain text\n", encoding="utf-8")
        target = tmp_path / "target"
        monkeypatch.setattr(exporter_module.settings.vllm, "low_confidence_action", "review")
        _fake_llm(monkeypatch, detections=[_finding("qorvexa", "dusuk")])

        report = _run_export(source, target, project)
        held = [o for o in report.outcomes if o.final_state == "REVIEW_REQUIRED"]
        assert [o.relative_path for o in held] == [f"{project}-core/notes.txt"]
        assert all(project not in path for path in _tree(target))

        with SessionLocal() as db:
            item = db.query(ReviewQueue).filter(ReviewQueue.run_id == report.run_id).one()
            ReviewService(db).approve(item.id)
            db.commit()
            warning = db.query(AuditWarning).filter(AuditWarning.run_id == report.run_id).one()
            assert warning.status == "dismissed"
            assert warning.output_path == "mask_proje_adi_1-core/notes.txt"
            context_id = db.execute(sqltext(
                "SELECT baglam_id FROM maskeleme_calismalari WHERE id=:r"), {"r": report.run_id}).scalar_one()

        tree = _tree(target)
        assert all(project not in path for path in tree), tree
        released = target / "mask_proje_adi_1-core" / "notes.txt"
        assert released.is_file()
        assert "qorvexa" not in released.read_text(encoding="utf-8")
        manifest = read_manifest(target, context_id)
        assert "mask_proje_adi_1-core/notes.txt" in manifest["files"]
    finally:
        _cleanup(project)


def test_release_applies_run_consistency(tmp_path, monkeypatch):
    """A value masked in another file of the same run must not stay open in a released file."""
    project = _project()
    try:
        source = tmp_path / "source"
        source.mkdir()
        (source / "a.txt").write_text("owner: ops@qorvexa-intra.example\n", encoding="utf-8")
        (source / "b.txt").write_text("contact ops@qorvexa-intra.example later; codename vantrel\n", encoding="utf-8")
        target = tmp_path / "target"
        monkeypatch.setattr(exporter_module.settings.vllm, "low_confidence_action", "review")
        _fake_llm(monkeypatch, detections=[_finding("vantrel", "dusuk", "PROJE_KOD_ADI")])
        report = _run_export(source, target, project)

        with SessionLocal() as db:
            warning = db.query(AuditWarning).filter(
                AuditWarning.run_id == report.run_id, AuditWarning.file_path == "b.txt").one()
            # Simulate a stale quarantine copy that still carries the e-mail in clear text.
            warning.masked_content = warning.masked_content.replace(
                next(tok for tok in warning.masked_content.split() if tok.startswith("mask_email")),
                "ops@qorvexa-intra.example",
            )
            db.commit()
            for item in db.query(ReviewQueue).filter(ReviewQueue.run_id == report.run_id).all():
                ReviewService(db).approve(item.id)
            db.commit()
        released = (target / "b.txt").read_text(encoding="utf-8")
        assert "ops@qorvexa-intra.example" not in released
        assert "vantrel" not in released
    finally:
        _cleanup(project)


@pytest.mark.parametrize(
    "confidence,action,expected_state,expected_in_output",
    [
        ("yuksek", "ignore", "READY", False),
        ("orta", "ignore", "READY", False),
        ("dusuk", "ignore", "READY", True),
        ("dusuk", "review", "REVIEW_REQUIRED", None),
    ],
)
def test_llm_confidence_routing(tmp_path, monkeypatch, confidence, action, expected_state, expected_in_output):
    project = _project()
    try:
        source = tmp_path / "source"
        source.mkdir()
        (source / "app.txt").write_text("service: qorvexa gateway\n", encoding="utf-8")
        target = tmp_path / "target"
        monkeypatch.setattr(exporter_module.settings.vllm, "low_confidence_action", action)
        _fake_llm(monkeypatch, detections=[_finding("qorvexa", confidence)])
        report = _run_export(source, target, project)
        assert report.outcomes[0].final_state == expected_state
        if expected_in_output is not None:
            output = (target / "app.txt").read_text(encoding="utf-8")
            assert ("qorvexa" in output) is expected_in_output
    finally:
        _cleanup(project)


def test_unverifiable_audit_findings_do_not_quarantine(tmp_path, monkeypatch):
    project = _project()
    try:
        source = tmp_path / "source"
        source.mkdir()
        (source / "svc.py").write_text("class UserService:\n    pass\n", encoding="utf-8")
        target = tmp_path / "target"

        def audit(_chunk):
            return {"risk_var": True, "bulgular": [
                {"aciklama": "sayaclar kisi sayisini ele veriyor", "ilgili_bolum": "mask_email_1"},
                {"aciklama": "metinde olmayan alinti", "ilgili_bolum": "Hakan Yilmaz"},
                {"aciklama": "genel anahtar kelime", "ilgili_bolum": "class"},
            ]}

        _fake_llm(monkeypatch, detections=[], audit=audit)
        report = _run_export(source, target, project)
        assert report.outcomes[0].final_state == "READY"
        assert (target / "svc.py").is_file()
    finally:
        _cleanup(project)


def test_verified_audit_finding_is_masked_never_released_open(tmp_path, monkeypatch):
    # Dogrulanmis denetim bulgusu artik insan onayi yerine otomatik
    # maskelenir; dosya ancak deger acik KALMADAN ciktiya yazilir.
    project = _project()
    try:
        source = tmp_path / "source"
        source.mkdir()
        (source / "notes.txt").write_text("sahibi Hakan Yilmaz\n", encoding="utf-8")
        target = tmp_path / "target"

        def audit(_chunk):
            return {"risk_var": True, "bulgular": [{"aciklama": "kisi adi", "ilgili_bolum": "Hakan Yilmaz"}]}

        monkeypatch.setattr(exporter_module.settings.presidio, "use_builtin_recognizers", False)
        _fake_llm(monkeypatch, detections=[], audit=audit)
        report = _run_export(source, target, project)
        assert report.outcomes[0].final_state == "READY"
        assert "Hakan Yilmaz" not in (target / "notes.txt").read_text(encoding="utf-8")
    finally:
        _cleanup(project)


def test_verify_audit_findings_trims_placeholders():
    text = "// sahibi Hakan Yilmaz mask_email_1\nhost = mask_hostname_2\n"
    kept, dropped = verify_audit_findings(text, [
        AuditFinding("kisi", "Hakan Yilmaz mask_email_1"),
        AuditFinding("sayac", "mask_hostname_2"),
        AuditFinding("uydurma", "Poseidon"),
    ])
    assert [f.ilgili_bolum for f in kept] == ["Hakan Yilmaz"]
    assert dropped == 2


_CS_SOURCE = (
    'string anaMusteriAd = "{value}";\n'
    'anaMusteriAd = dr["MusteriAd"].ToString(); // Musteri adini aldik\n'
    'detay += $"- {{anaMusteriAd}} (Rezervasyon Sahibi)\\n";\n'
)


@pytest.mark.parametrize("text,quote,expected", [
    # Degisken adi degil, ona atanan acik deger hassastir.
    (_CS_SOURCE.format(value="Ayşe Yılmaz"), "anaMusteriAd", ["Ayşe Yılmaz"]),
    (_CS_SOURCE.format(value="Ayşe Yılmaz"), 'anaMusteriAd = "Ayşe Yılmaz"', ["Ayşe Yılmaz"]),
    ('{"anaMusteriAd": "Ayşe Yılmaz"}', "anaMusteriAd", ["Ayşe Yılmaz"]),
    ("owner: Hakan Yilmaz\n", "owner", ["Hakan Yilmaz"]),
    ("DB_PASSWORD=Sup3rS3cret\n", "DB_PASSWORD", ["Sup3rS3cret"]),
    ("WHERE MusteriAd = 'Ayşe Yılmaz'", "MusteriAd", ["Ayşe Yılmaz"]),
    # Atanan deger yoksa (bos, maskeli, calisma aninda okunan) sizinti yoktur.
    (_CS_SOURCE.format(value=""), "anaMusteriAd", []),
    (_CS_SOURCE.format(value="mask_kisi_adi_1"), "anaMusteriAd", []),
    ('ad = musteri_adi\nmusteri_adi = row["x"]\n', "ad", []),
    # Tanimlayici olarak kullanilmayan degerler oldugu gibi kalir.
    ("// sahibi Hakan Yilmaz\n", "Hakan Yilmaz", ["Hakan Yilmaz"]),
    ("host = srvprod01\n", "srvprod01", ["srvprod01"]),
    ("// TODO Hakan: duzelt\n", "Hakan", ["Hakan"]),
])
def test_audit_finding_resolves_variable_name_to_assigned_value(text, quote, expected):
    kept, dropped = verify_audit_findings(text, [AuditFinding("kisi adi", quote)])
    assert [f.ilgili_bolum for f in kept] == expected
    assert dropped == (0 if expected else 1)


@pytest.mark.parametrize("raw,expected", [
    ("PERSON", "PERSON"),
    ("person name", "PERSON"),
    ("ic servis adi", "IC_SERVIS_ADI"),
    ("Poseidon kod adi", "KURUMSAL_TANIMLAYICI"),
    ("", "KURUMSAL_TANIMLAYICI"),
])
def test_llm_entity_type_is_normalized(raw, expected):
    assert normalize_llm_entity_type(raw) == expected


def test_free_form_type_never_reaches_placeholder():
    from app.services.llm_recognizer import parse_and_verify_detections

    raw = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"bulgular": [
        {"bulunan_deger": "zx9", "tip": "POSEIDON_SERVISI", "guven_seviyesi": "yuksek", "gerekce": "g"}
    ]})}}]}
    [result] = parse_and_verify_detections(raw, "id=zx9", [])
    assert result.tip == "KURUMSAL_TANIMLAYICI"
    assert "poseidon" not in synthetic_llm_rule(result.tip).placeholder_prefix


def _llm_settings(**overrides):
    from types import SimpleNamespace
    values = dict(enabled=True, host="http://retry-test", model="m", api_key=None, timeout_seconds=5.0,
                  max_file_chars=20_000, seed=42, max_concurrent_requests=1, transient_retries=1)
    values.update(overrides)
    return SimpleNamespace(**values)


def test_transient_llm_failure_is_retried_once(monkeypatch):
    import asyncio
    import httpx
    from app.services import audit_reviewer, llm_runtime
    from app.services.llm_recognizer import LLMRecognitionError

    monkeypatch.setattr(llm_runtime, "_RETRY_BASE_DELAY_SECONDS", 0)
    calls = []

    async def flaky(host, timeout, payload, api_key=None):
        calls.append(1)
        if len(calls) == 1:
            raise LLMRecognitionError("timeout") from httpx.ReadTimeout("slow")
        return {"choices": [{"finish_reason": "stop", "message": {"content": '{"risk_var": false, "bulgular": []}'}}]}

    monkeypatch.setattr(audit_reviewer, "call_vllm", flaky)
    verdict = asyncio.run(audit_reviewer.audit_masked_text("metin", _llm_settings()))
    assert verdict.risky is False and len(calls) == 2


def test_non_transient_llm_failure_is_not_retried(monkeypatch):
    import asyncio
    from app.services import audit_reviewer
    from app.services.llm_recognizer import LLMRecognitionError

    calls = []

    async def broken(host, timeout, payload, api_key=None):
        calls.append(1)
        raise LLMRecognitionError("schema")

    monkeypatch.setattr(audit_reviewer, "call_vllm", broken)
    with pytest.raises(LLMRecognitionError):
        asyncio.run(audit_reviewer.audit_masked_text("metin", _llm_settings(transient_retries=3)))
    assert len(calls) == 1


def test_llm_calls_do_not_hold_sqlite_write_lock(tmp_path, monkeypatch):
    """While the LLM is being awaited, another connection must be able to write."""
    import sqlite3
    from app.core.config import settings

    project = _project()
    try:
        source = tmp_path / "source"
        source.mkdir()
        for i in range(3):
            (source / f"f{i}.txt").write_text(f"service qorvexa {i}\n", encoding="utf-8")
        target = tmp_path / "target"
        write_results = []

        def probe():
            conn = sqlite3.connect(str(settings.database.resolved_path), timeout=0.2)
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("ROLLBACK")
                write_results.append(True)
            except sqlite3.OperationalError:
                write_results.append(False)
            finally:
                conn.close()

        def audit(_chunk):
            probe()
            return {"risk_var": False, "bulgular": []}

        _fake_llm(monkeypatch, detections=[_finding("qorvexa", "yuksek")], audit=audit)
        report = _run_export(source, target, project)
        assert report.status in ("completed", "completed_with_warnings")
        assert write_results and all(write_results), write_results
    finally:
        _cleanup(project)


def test_failed_export_marks_run_failed_and_allows_next_export(tmp_path, monkeypatch):
    project = _project()
    try:
        source = tmp_path / "source"
        source.mkdir()
        (source / "a.txt").write_text("plain\n", encoding="utf-8")
        target = tmp_path / "target"
        _fake_llm(monkeypatch, detections=[])
        original = exporter_module._run_consistency_pass

        def boom(*args, **kwargs):
            raise RuntimeError("simulated crash after intermediate commits")

        monkeypatch.setattr(exporter_module, "_run_consistency_pass", boom)
        with pytest.raises(RuntimeError):
            _run_export(source, target, project)
        with SessionLocal() as db:
            statuses = [row[0] for row in db.execute(sqltext(
                "SELECT r.durum FROM maskeleme_calismalari r JOIN maskeleme_baglamlari b ON r.baglam_id=b.id "
                "WHERE b.proje_adi=:p"), {"p": project}).all()]
        assert statuses == ["failed"]
        assert not target.exists()

        monkeypatch.setattr(exporter_module, "_run_consistency_pass", original)
        report = _run_export(source, target, project)
        assert report.status in ("completed", "completed_with_warnings")
    finally:
        _cleanup(project)


def _poll_job(client, job_id, timeout=60):
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/export/jobs/{job_id}").json()
        if body["status"] != "running":
            return body
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_background_export_job_reports_progress_and_result(monkeypatch):
    from fastapi.testclient import TestClient
    from app.api.main import app

    project = _project()
    try:
        monkeypatch.setattr(exporter_module.settings.vllm, "enabled", False)
        client = TestClient(app)
        files = [("files", (f"src/f{i}.txt", f"mail ops{i}@corp-intra.example\n".encode(), "text/plain"))
                 for i in range(3)]
        data = {"project_name": project, "sicil_no": "P-JOB-1", "branch_name": "pytest-branch",
                "initiated_by": "P-JOB-1", "is_directory_upload": "true"}
        started = client.post("/export/upload/jobs", data=data, files=files)
        assert started.status_code == 202
        body = _poll_job(client, started.json()["job_id"])
        assert body["status"] == "completed", body
        assert body["processed"] == body["total"] == 3
        assert body["result"]["report"]["files_masked"] == 3
        assert body["result"]["output_token"]
        assert client.get(f"/export/outputs/{body['result']['output_token']}/download").status_code == 200
    finally:
        _cleanup(project)


def test_background_export_job_failure_is_reported(monkeypatch):
    from fastapi.testclient import TestClient
    from app.api.main import app

    def boom(*args, **kwargs):
        raise ValueError("kurulum hatasi")

    monkeypatch.setattr(exporter_module, "load_active_rules", boom)
    project = _project()
    try:
        client = TestClient(app)
        data = {"project_name": project, "sicil_no": "P-JOB-2", "branch_name": "pytest-branch",
                "initiated_by": "P-JOB-2"}
        started = client.post("/export/upload/jobs", data=data,
                              files=[("files", ("a.txt", b"x\n", "text/plain"))])
        body = _poll_job(client, started.json()["job_id"])
        assert body["status"] == "failed"
        assert body["error_status"] == 400
        assert "kurulum hatasi" in body["error_message"]
        assert client.get("/export/jobs/doesnotexist").status_code == 404
    finally:
        _cleanup(project)


def test_csharp_interpolation_holes_are_code_not_string_content():
    from app.services.string_literal_index import StringLiteralIndex

    text = 'detay += $"TCKN : {dr["kimlikNo"]}\\n";\nx = $@"C:\\yol ""a"" {ad}";\ny = $"{{lit}} {v:N2} son";\n'
    spans = [text[a:b] for a, b, _, _ in StringLiteralIndex(text, "a.cs").spans]
    assert spans == ["TCKN : ", "\\n", 'C:\\yol ""a"" ', "{{lit}} ", " son"]


@pytest.mark.parametrize("path", ["Rezervasyon.cs", "notes.txt"])
@pytest.mark.parametrize("value", ['"]}\\n"', ']}\\n', 'dr["kimlikNo"]}\\n'])
def test_punctuation_only_detection_is_never_masked(path, value):
    from app.services.detectors import DetectionResult
    from app.services.token_boundary_validator import TokenBoundaryValidator

    text = 'detay += $"TCKN : {dr["kimlikNo"]}\\n";\n'
    start = text.find(value)
    result = DetectionResult(deger=value, tip="KIMLIK_NO", guven_seviyesi="yuksek", kaynak_motor="llm",
                             gerekce="", start=start, end=start + len(value))
    accepted, rejected = TokenBoundaryValidator().validate(text, [result], file_path=path)
    assert accepted == []
    assert rejected


def test_csharp_verbatim_string_with_doubled_quotes_is_one_literal():
    from app.services.string_literal_index import StringLiteralIndex

    text = 'string sql = @"SELECT k.""kimlikNo""\n  FROM x ORDER BY k.""kisiAdi""";\nvar y = "a";\n'
    spans = [text[a:b] for a, b, _, _ in StringLiteralIndex(text, "a.cs").spans]
    assert spans == ['SELECT k.""kimlikNo""\n  FROM x ORDER BY k.""kisiAdi""', "a"]


def test_llm_finding_is_not_widened_to_a_multiline_string():
    from app.services.detectors import DetectionResult
    from app.services.token_boundary_validator import TokenBoundaryValidator

    text = 'string sql = @"SELECT k.""kimlikNo""\n  FROM ""Kisi"" k";\nreturn sql;\n'
    start = text.find("kimlikNo")
    result = DetectionResult(deger="kimlikNo", tip="KIMLIK_NO", guven_seviyesi="yuksek", kaynak_motor="llm",
                             gerekce="", start=start, end=start + len("kimlikNo"))
    accepted, rejected = TokenBoundaryValidator().validate(text, [result], file_path="a.cs")
    assert accepted == []
    assert "cok satirli" in rejected[0].reason
