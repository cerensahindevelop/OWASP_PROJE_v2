"""Karantinadan serbest birakma (dismiss/mask/review) sertlestirme testleri.

1. "Yanlis alarm" kanitlarin tamamini ogrenir (ekran siniri 6 degil).
2. SQLite yazma kilidi LLM final denetimi boyunca tutulmaz.
3. Commit olmazsa yazilan dosya ve manifest kaydi geri alinir.
4. Es zamanli iki serbest birakma birbirinin manifest kaydini silmez.
5. Kaynakta zaten bulunan sozdizimi hatasi serbest birakmayi engellemez.
6. Bastirma kararlari alinti metnine degil konuma gore eslenir.
7. Basarisiz dogrulama hicbir karari kalici yapmaz.
"""
from __future__ import annotations

import asyncio
import sqlite3
import threading
import uuid

import pytest
from sqlalchemy import event, select

from app.core.config import settings
from app.db.models import AuditWarning, LearnedDecision, MaskingContext, MaskingRun
from app.db.session import SessionLocal
from app.services import audit_warning_service as service_module
from app.services.audit_reviewer import AuditFinding, AuditVerdict
from app.services.audit_warning_service import AuditWarningService
from app.services.integrity_manifest import read_manifest, write_manifest
from app.services.learned_decisions import covered_by_values


def _make(db, tmp_path, content, *, values=(), file_path="src/config.py", reasoning=None):
    context = MaskingContext(project_name=f"pytest-harden-{uuid.uuid4().hex[:8]}", sicil_no="H-1",
                             branch_name="test")
    db.add(context)
    db.flush()
    run = MaskingRun(context_id=context.id, operation_type="mask", source_path=str(tmp_path / "source"),
                     target_path=str(tmp_path / "output"), initiated_by="H-1",
                     status="completed_with_warnings", mapping_version=2)
    db.add(run)
    db.flush()
    if reasoning is None:
        reasoning = " | ".join(f"maskelenmemis ad (ilgili bolum: '{value}')" for value in values)
    warning = AuditWarning(run_id=run.id, file_path=file_path, masked_content=content, encoding="utf-8",
                           reasoning=reasoning, audit_failed=False)
    db.add(warning)
    db.flush()
    return context, run, warning


def _audit_returns(monkeypatch, *quotes):
    """Final denetim bu alintilari (metinde geciyorsa) risk olarak bildirir."""
    async def fake(content, _settings):
        findings = [AuditFinding("maskelenmemis ad", quote) for quote in quotes if quote in content]
        return AuditVerdict(risky=bool(findings), findings=findings)
    monkeypatch.setattr(service_module, "audit_masked_text", fake)


# 1 -------------------------------------------------------------------------

def test_dismiss_learns_every_evidence_value_beyond_display_limit(db_session, tmp_path, monkeypatch):
    values = [f"Kisi{i}Soyad" for i in range(8)]
    # Ilk deger 6 kez gecer: ekran siniri tek degere harcanabilir.
    lines = [f"owner = '{values[0]}'"] * 6 + [f"name{i} = '{value}'" for i, value in enumerate(values[1:])]
    _, _, warning = _make(db_session, tmp_path, "\n".join(lines) + "\n", values=values)
    _audit_returns(monkeypatch, *values)

    released = asyncio.run(AuditWarningService(db_session).dismiss(warning.id))

    assert released.status == "dismissed"
    assert (tmp_path / "output" / "src" / "config.py").is_file()


# 2 -------------------------------------------------------------------------

def _cleanup(context_id: int) -> None:
    with SessionLocal() as db:
        from sqlalchemy import text
        run_ids = [r[0] for r in db.execute(
            text("SELECT id FROM maskeleme_calismalari WHERE baglam_id=:c"), {"c": context_id}).all()]
        db.execute(text("DELETE FROM ogrenilen_bulgu_kararlari WHERE baglam_id=:c"), {"c": context_id})
        for run_id in run_ids:
            for table in ("denetim_kaydi", "gozden_gecirme_kuyrugu", "denetim_uyarilari", "islem_yer_tutucu_sayaclari"):
                db.execute(text(f"DELETE FROM {table} WHERE calisma_id=:r"), {"r": run_id})
        db.execute(text("DELETE FROM deger_eslemeleri WHERE baglam_id=:c"), {"c": context_id})
        db.execute(text("DELETE FROM maskeleme_calismalari WHERE baglam_id=:c"), {"c": context_id})
        db.execute(text("DELETE FROM maskeleme_baglamlari WHERE id=:c"), {"c": context_id})
        db.commit()


@pytest.mark.parametrize("action", ["dismiss", "mask"])
def test_sqlite_write_lock_is_not_held_during_final_llm_audit(tmp_path, monkeypatch, action):
    with SessionLocal() as db:
        context, _, warning = _make(db, tmp_path, "owner = 'KisiGizli'\n", values=["KisiGizli"])
        db.commit()
        context_id, warning_id = context.id, warning.id

    other_writer = {}

    async def fake(content, _settings):
        # LLM "yanit verirken" baska bir surec/istek veritabanina yazabilmeli.
        conn = sqlite3.connect(settings.database.resolved_path, timeout=0.5)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.rollback()
            other_writer["ok"] = True
        except sqlite3.OperationalError as exc:
            other_writer["ok"] = False
            other_writer["error"] = str(exc)
        finally:
            conn.close()
        return AuditVerdict(risky=False)

    monkeypatch.setattr(service_module, "audit_masked_text", fake)
    try:
        with SessionLocal() as db:
            asyncio.run(getattr(AuditWarningService(db), action)(warning_id))
            db.commit()
        assert other_writer == {"ok": True}
    finally:
        _cleanup(context_id)


# 3 -------------------------------------------------------------------------

def test_release_is_undone_when_the_commit_fails(db_session, tmp_path, monkeypatch):
    _, run, warning = _make(db_session, tmp_path, "owner = 'KisiGizli'\n", values=["KisiGizli"])
    output = tmp_path / "output"
    output.mkdir()
    write_manifest(output, run.context_id, {}, complete=False, job_id=run.id)
    _audit_returns(monkeypatch)

    @event.listens_for(db_session, "before_commit")
    def fail_release_commit(session):
        if any(isinstance(obj, AuditWarning) and obj.status == "dismissed" for obj in session.identity_map.values()):
            raise RuntimeError("database is locked")

    with pytest.raises(RuntimeError):
        asyncio.run(AuditWarningService(db_session).dismiss(warning.id))
        db_session.commit()  # eski davranista commit istek sonunda yapilirdi
    db_session.rollback()
    event.remove(db_session, "before_commit", fail_release_commit)

    assert not (output / "src" / "config.py").exists()
    assert read_manifest(output, run.context_id)["files"] == {}


# 4 -------------------------------------------------------------------------

def test_concurrent_releases_keep_both_manifest_entries(db_session, tmp_path, monkeypatch):
    _, run, first = _make(db_session, tmp_path, "a = 1\n", file_path="a.py")
    second = AuditWarning(run_id=run.id, file_path="b.py", masked_content="b = 2\n", encoding="utf-8",
                          reasoning="-", audit_failed=False)
    db_session.add(second)
    db_session.flush()
    output = tmp_path / "output"
    output.mkdir()
    write_manifest(output, run.context_id, {}, complete=False, job_id=run.id)
    (output / "a.py").write_text("a = 1\n")
    (output / "b.py").write_text("b = 2\n")

    service = AuditWarningService(db_session)
    monkeypatch.setattr(service, "_run_reverse_map", lambda _run: {}, raising=False)
    monkeypatch.setattr(service, "_run_mapping_rows", lambda _run: [])
    monkeypatch.setattr(service_module, "_register_release_undo", lambda db, undo: None, raising=False)

    # Ilk serbest birakma manifest'i okuduktan sonra, ikincisi bitene kadar
    # (en fazla 1 sn) bekler: kilit yoksa ikisi de ayni eski manifest'i okur.
    first_read = threading.Event()
    second_done = threading.Event()
    real_read = service_module.read_manifest

    def slow_read(root, context_id):
        manifest = real_read(root, context_id)
        if threading.current_thread().name == "first":
            first_read.set()
            second_done.wait(timeout=1)
        return manifest

    monkeypatch.setattr(service_module, "read_manifest", slow_read)

    def release(warning):
        service._add_to_manifest(run, output.resolve(), (output / warning.file_path).resolve(), warning)
        if warning is second:
            second_done.set()

    first_thread = threading.Thread(target=release, args=(first,), name="first")
    second_thread = threading.Thread(target=release, args=(second,), name="second")
    first_thread.start()
    assert first_read.wait(timeout=5)
    second_thread.start()
    first_thread.join(timeout=5)
    second_thread.join(timeout=5)

    assert set(read_manifest(output, run.context_id)["files"]) == {"a.py", "b.py"}


# 5 -------------------------------------------------------------------------

def test_source_with_preexisting_syntax_error_can_be_released(db_session, tmp_path, monkeypatch):
    # Kaynak dosya da ayni hataya sahip (JSON'da yorum): export bu dosyayi geciriyordu.
    content = '// servis ayari\n{"owner": "KisiGizli"}\n'
    _, _, warning = _make(db_session, tmp_path, content, values=["KisiGizli"], file_path="config.json")
    monkeypatch.setattr(settings.validation, "syntax_failure_action", "block")
    _audit_returns(monkeypatch, "KisiGizli")

    released = asyncio.run(AuditWarningService(db_session).dismiss(warning.id))

    assert released.status == "dismissed"
    assert (tmp_path / "output" / "config.json").read_text() == content


# 6 -------------------------------------------------------------------------

def test_dismissed_quote_covers_a_shorter_quote_from_the_final_audit(db_session, tmp_path, monkeypatch):
    _, _, warning = _make(db_session, tmp_path, "# sahibi Hakan Yilmaz\n", values=["Hakan Yilmaz"])
    _audit_returns(monkeypatch, "Hakan")  # model bu kez alintiyi kisaltti

    released = asyncio.run(AuditWarningService(db_session).dismiss(warning.id))

    assert released.status == "dismissed"


def test_shorter_quote_elsewhere_in_the_file_is_still_a_finding(db_session, tmp_path, monkeypatch):
    content = "# sahibi Hakan Yilmaz\n# yedek: Hakan\n"
    _, _, warning = _make(db_session, tmp_path, content, values=["Hakan Yilmaz"])
    _audit_returns(monkeypatch, "Hakan")

    with pytest.raises(ValueError, match="bastırılmamış risk"):
        asyncio.run(AuditWarningService(db_session).dismiss(warning.id))
    assert warning.status == "pending"


@pytest.mark.parametrize(("value", "text", "suppressed", "expected"), [
    ("Hakan", "sahibi Hakan Yilmaz", ["Hakan Yilmaz"], True),
    ("hakan", "sahibi HAKAN Yilmaz", ["Hakan Yilmaz"], True),
    ("Hakan", "Hakan Yilmaz ve Hakan", ["Hakan Yilmaz"], False),
    ("Hakan Yilmaz Bey", "Hakan Yilmaz Bey", ["Hakan Yilmaz"], False),
    ("Hakan", "yok", ["Hakan Yilmaz"], False),
    ("", "Hakan", ["Hakan"], False),
])
def test_covered_by_values(value, text, suppressed, expected):
    assert covered_by_values(value, text, suppressed) is expected


# 7 -------------------------------------------------------------------------

def test_failed_dismiss_persists_no_suppression_and_no_file(db_session, tmp_path, monkeypatch):
    context, _, warning = _make(db_session, tmp_path, "owner = 'KisiGizli'\n", values=["KisiGizli"])

    async def unverifiable(content, _settings):
        return AuditVerdict(risky=True, findings=[])

    monkeypatch.setattr(service_module, "audit_masked_text", unverifiable)
    with pytest.raises(ValueError, match="doğrulanabilir ifade"):
        asyncio.run(AuditWarningService(db_session).dismiss(warning.id))

    assert warning.status == "pending"
    assert not db_session.scalars(select(LearnedDecision).where(LearnedDecision.context_id == context.id)).all()
    assert not (tmp_path / "output" / "src" / "config.py").exists()


# 8 -------------------------------------------------------------------------

_HOLD_LOCK_SCRIPT = """
import sys, time
from pathlib import Path
from app.services.integrity_manifest import manifest_lock
with manifest_lock(Path(sys.argv[1])):
    Path(sys.argv[2]).write_text("held")
    time.sleep(1.0)
"""


def test_manifest_lock_excludes_another_process(tmp_path):
    import subprocess
    import sys
    import time
    from pathlib import Path

    from app.services.integrity_manifest import manifest_lock, manifest_lock_path

    target = tmp_path / "output"
    target.mkdir()
    held = tmp_path / "held.flag"
    root = Path(__file__).resolve().parents[1]
    worker = subprocess.Popen([sys.executable, "-c", _HOLD_LOCK_SCRIPT, str(target), str(held)], cwd=root)
    try:
        deadline = time.monotonic() + 30
        while not held.exists():
            assert worker.poll() is None, "kilit tutan surec erken kapandi"
            assert time.monotonic() < deadline
            time.sleep(0.02)
        started = time.monotonic()
        with manifest_lock(target):
            waited = time.monotonic() - started
    finally:
        worker.wait(timeout=30)
    assert worker.returncode == 0
    assert waited >= 0.5, waited
    # Kilit dosyasi indirilen ciktiya girmez.
    assert manifest_lock_path(target).parent == tmp_path
    assert list(target.iterdir()) == []


def test_manifest_lock_times_out_instead_of_hanging(tmp_path):
    import subprocess
    import sys
    import time
    from pathlib import Path

    from app.services.integrity_manifest import manifest_lock

    target = tmp_path / "output"
    target.mkdir()
    held = tmp_path / "held.flag"
    root = Path(__file__).resolve().parents[1]
    worker = subprocess.Popen([sys.executable, "-c", _HOLD_LOCK_SCRIPT, str(target), str(held)], cwd=root)
    try:
        while not held.exists():
            assert worker.poll() is None
            time.sleep(0.02)
        with pytest.raises(ValueError, match="kilitli"):
            with manifest_lock(target, timeout=0.1):
                pass
    finally:
        worker.wait(timeout=30)


# 9 -------------------------------------------------------------------------

def test_term_quarantine_shows_recorded_clear_value_when_dictionary_changed(db_session):
    from app.services.audit_warning_details import describe_audit_warning

    warning = AuditWarning(
        run_id=1, file_path="app.py", masked_content="x = 1\nowner = 'Hakan Yilmaz'\n", encoding="utf-8",
        reasoning=("Kurumsal terim kontrolü: 1 açık eşleşme kaldı. Dosya çıktı klasörüne alınmadı.\n"
                   "Satır 2, sütun 10: kisi_adi (kurumsal_terim_hash); açık değer='Hakan Yilmaz'"),
        audit_failed=False,
    )
    detail = describe_audit_warning(warning, db_session)
    assert detail["evidence"][0]["found_value"] == "Hakan Yilmaz"
    assert "⟦Hakan Yilmaz⟧" in detail["evidence"][0]["excerpt"]


# 10 ------------------------------------------------------------------------
# Inceleme kararlari (onay/ret/dosyayi maskele) sonrasi son denetim de yazma
# kilidini tutmaz; maskeleme hatasi karari geri almaya devam eder.

def _make_review_hold(db, tmp_path, values):
    from app.db.models import ReviewQueue
    content = "\n".join(f"key{i} = '{value}'" for i, value in enumerate(values)) + "\n"
    context, run, warning = _make(db, tmp_path, content, reasoning="INCELEME_GEREKLI: karar bekliyor")
    items = []
    for value in values:
        item = ReviewQueue(run_id=run.id, file_path=warning.file_path, found_value=value,
                           entity_type="SECRET", confidence_level="orta", reason="risk")
        db.add(item)
        items.append(item)
    db.flush()
    return context, run, warning, items


@pytest.mark.parametrize("action", ["approve", "reject", "mask_file"])
def test_review_decision_does_not_hold_write_lock_during_final_audit(tmp_path, monkeypatch, action):
    from app.services.review_service import ReviewService

    with SessionLocal() as db:
        context, _, warning, items = _make_review_hold(db, tmp_path, ["GizliDeger1"])
        db.commit()
        context_id, warning_id, item_id = context.id, warning.id, items[0].id

    other_writer = {}

    async def fake(content, _settings):
        conn = sqlite3.connect(settings.database.resolved_path, timeout=0.5)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.rollback()
            other_writer["ok"] = True
        except sqlite3.OperationalError as exc:
            other_writer["ok"] = False
            other_writer["error"] = str(exc)
        finally:
            conn.close()
        return AuditVerdict(risky=False)

    monkeypatch.setattr(service_module, "audit_masked_text", fake)
    try:
        with SessionLocal() as db:
            getattr(ReviewService(db), action)(item_id)
            db.commit()
            warning = db.get(AuditWarning, warning_id)
            assert warning.status == "dismissed"
        assert other_writer == {"ok": True}
        released = (tmp_path / "output" / "src" / "config.py").read_text()
        assert ("GizliDeger1" in released) is (action == "reject")
    finally:
        _cleanup(context_id)


def test_review_masking_error_still_rolls_back_the_decision(tmp_path, monkeypatch):
    from app.db.models import ReviewQueue
    from app.services import review_masking
    from app.services.review_service import ReviewService

    with SessionLocal() as db:
        context, _, _, items = _make_review_hold(db, tmp_path, ["GizliDeger1"])
        db.commit()
        context_id, item_id = context.id, items[0].id

    def broken(*args, **kwargs):
        raise ValueError("Otomatik maskeleme geri dönüş doğrulamasından geçemedi")

    monkeypatch.setattr(review_masking, "mask_review_values", broken)
    try:
        with SessionLocal() as db:
            with pytest.raises(ValueError, match="geri dönüş"):
                ReviewService(db).approve(item_id)
            db.rollback()
        with SessionLocal() as db:
            assert db.get(ReviewQueue, item_id).status == "pending"
    finally:
        _cleanup(context_id)


# 11 ------------------------------------------------------------------------
# Ayni icerik, ayni karar: kayitli denetim sonucu yeniden kullanilir.

def _enable_llm(monkeypatch):
    monkeypatch.setattr(settings.vllm, "enabled", True)
    monkeypatch.setattr(settings.vllm, "host", "http://fake")
    monkeypatch.setattr(settings.vllm, "model", "fake-model")


def _counting_audit(monkeypatch, *quotes):
    calls = {"n": 0}

    async def fake(content, _settings):
        calls["n"] += 1
        findings = [AuditFinding("ad", quote) for quote in quotes if quote in content]
        return AuditVerdict(risky=bool(findings), findings=findings)

    monkeypatch.setattr(service_module, "audit_masked_text", fake)
    return calls


def _record(content, file_path, *quotes):
    from app.services.audit_reviewer import audit_record_key, encode_audit_record
    verdict = AuditVerdict(risky=bool(quotes), findings=[AuditFinding("ad", q) for q in quotes])
    return encode_audit_record(verdict, audit_record_key(content, file_path, settings.vllm))


def test_dismiss_reuses_recorded_verdict_for_identical_content(db_session, tmp_path, monkeypatch):
    _enable_llm(monkeypatch)
    content = "# sahibi KisiGizli\n# ekip Takim\n"
    _, _, warning = _make(db_session, tmp_path, content, values=["KisiGizli"])
    warning.audit_record = _record(content, warning.file_path, "KisiGizli")
    # Model yeniden sorulsaydi bu kez baska bir sey bulacakti.
    calls = _counting_audit(monkeypatch, "Takim")

    released = asyncio.run(AuditWarningService(db_session).dismiss(warning.id))

    assert released.status == "dismissed"
    assert calls["n"] == 0


def test_changed_content_is_audited_again(db_session, tmp_path, monkeypatch):
    _enable_llm(monkeypatch)
    content = "owner = 'KisiGizli'\n"
    _, _, warning = _make(db_session, tmp_path, content, values=["KisiGizli"])
    warning.audit_record = _record(content, warning.file_path, "KisiGizli")
    calls = _counting_audit(monkeypatch)

    # mask() icerigi degistirir: kayit o icerige ait degil, model cagrilir.
    asyncio.run(AuditWarningService(db_session).mask(warning.id))

    assert calls["n"] == 1


def test_failed_attempt_is_repeatable_with_the_same_verdict(db_session, tmp_path, monkeypatch):
    _enable_llm(monkeypatch)
    content = "# sahibi KisiGizli\n# yedek Ahmet\n"
    _, _, warning = _make(db_session, tmp_path, content, values=["KisiGizli"])
    calls = _counting_audit(monkeypatch, "Ahmet")

    with pytest.raises(ValueError, match="bastırılmamış risk"):
        asyncio.run(AuditWarningService(db_session).dismiss(warning.id))
    # Model artik "temiz" dese bile ayni icerik ayni karari alir.
    _counting_audit(monkeypatch)
    with pytest.raises(ValueError, match="bastırılmamış risk"):
        asyncio.run(AuditWarningService(db_session).dismiss(warning.id))
    assert calls["n"] == 1


def test_audit_record_is_bound_to_content_and_settings(monkeypatch):
    from app.services.audit_reviewer import audit_record_key, decode_audit_record, encode_audit_record
    _enable_llm(monkeypatch)
    verdict = AuditVerdict(risky=True, findings=[AuditFinding("ad", "KisiGizli")])
    key = audit_record_key("a = 'KisiGizli'", "src/a.py", settings.vllm)
    record = encode_audit_record(verdict, key)

    assert "KisiGizli" not in record  # sifreli
    assert decode_audit_record(record, key) == verdict
    assert decode_audit_record(record, audit_record_key("a = 'Baska'", "src/a.py", settings.vllm)) is None
    assert decode_audit_record(record, audit_record_key("a = 'KisiGizli'", "src/b.yaml", settings.vllm)) is None
    monkeypatch.setattr(settings.vllm, "model", "baska-model")
    assert decode_audit_record(record, audit_record_key("a = 'KisiGizli'", "src/a.py", settings.vllm)) is None
    assert decode_audit_record("bozuk-kayit", key) is None
    # Modeli hic cagirmayan "temiz" sonuc kaydedilmez: denetimin yerini tutmaz.
    assert encode_audit_record(AuditVerdict(risky=False, audited=False), key) is None


def test_export_records_the_verdict_and_dismiss_reuses_it(tmp_path, monkeypatch):
    from tests.test_review_rate_and_release import _cleanup as cleanup_project, _fake_llm, _project
    from tests.test_exporter_failure_handling import _run_export
    from app.services import exporter as exporter_module
    from app.services.audit_reviewer import audit_record_key, decode_audit_record

    project = _project()
    try:
        source = tmp_path / "source"
        source.mkdir()
        (source / "notes.txt").write_text("sahibi Hakan\nYilmaz\n", encoding="utf-8")
        target = tmp_path / "target"

        # Cok satirli alinti otomatik duzeltilmez: dosya karantinaya duser.
        def audit(_chunk):
            return {"risk_var": True, "bulgular": [{"aciklama": "kisi adi", "ilgili_bolum": "Hakan\nYilmaz"}]}

        monkeypatch.setattr(exporter_module.settings.presidio, "use_builtin_recognizers", False)
        calls = _fake_llm(monkeypatch, detections=[], audit=audit)
        report = _run_export(source, target, project)
        assert report.outcomes[0].final_state == "SECURITY_QUARANTINE"
        audits_during_export = calls["audit"]

        with SessionLocal() as db:
            warning = db.scalars(select(AuditWarning).where(AuditWarning.run_id == report.run_id)).one()
            key = audit_record_key(warning.masked_content, warning.file_path, settings.vllm)
            recorded = decode_audit_record(warning.audit_record, key)
            assert [f.ilgili_bolum for f in recorded.findings] == ["Hakan\nYilmaz"]
            asyncio.run(AuditWarningService(db).dismiss(warning.id))
            db.commit()
        assert calls["audit"] == audits_during_export  # model yeniden cagrilmadi
        assert (target / "notes.txt").is_file()
    finally:
        cleanup_project(project)
