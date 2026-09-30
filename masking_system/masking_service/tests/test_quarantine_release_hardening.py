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
            for table in ("denetim_kaydi", "denetim_uyarilari", "islem_yer_tutucu_sayaclari"):
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
