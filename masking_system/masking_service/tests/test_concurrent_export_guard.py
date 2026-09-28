"""Asama 2 / Adim 2: _ensure_no_in_progress_run'daki check-then-act (TOCTOU)
yarisi icin regresyon testleri.

Gercek OS thread'leriyle bu yarisi uretmeye calismak (GIL zamanlamasi +
mask_text()'in kendi _lock_context() kilidiyle etkilesip sahte bir DB
deadlock'una yol acmasi nedeniyle) guvenilir/deterministik degildi. Bunun
yerine, planin da onerdigi gibi, iki ayri SessionLocal'i TEK thread icinde
elle ic ice geçirerek (manually interleaved) TOCTOU penceresini
deterministik olarak aciyoruz - bir transaction'in commit edilmemis
satirlarinin diger acik transaction'a gorunmemesi (temel ACID izolasyon
ozelligi, motordan bagimsiz) sayesinde bu, gercek bir yarisi birebir
temsil eder, ama rastgele zamanlamaya bagli degildir.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy import text as sqltext
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import ExportInProgressError
from app.db.models import MaskingContext, MaskingRun
from app.db.session import SessionLocal
from app.services import exporter
from app.services.exporter import _ensure_no_in_progress_run, export_project
from app.services.mapping_service import get_or_create_context

_PROJECT_NAME = "pytest-concurrent-export-guard"


def _cleanup() -> None:
    """test_full_regression.py'nin _cleanup_identity'siyle ayni desen: bagli
    tum tablolari (denetim_kaydi/gozden_gecirme_kuyrugu/denetim_uyarilari ->
    deger_eslemeleri -> maskeleme_calismalari -> baglam) FK sirasina uygun
    sekilde siler."""
    with SessionLocal() as db:
        row = db.execute(
            sqltext("SELECT id FROM maskeleme_baglamlari WHERE proje_adi = :p"),
            {"p": _PROJECT_NAME},
        ).first()
        if row is None:
            return
        context_id = row[0]
        run_ids = [
            r[0]
            for r in db.execute(
                sqltext("SELECT id FROM maskeleme_calismalari WHERE baglam_id = :c"), {"c": context_id}
            ).all()
        ]
        for run_id in run_ids:
            db.execute(sqltext("DELETE FROM denetim_kaydi WHERE calisma_id = :r"), {"r": run_id})
            db.execute(sqltext("DELETE FROM gozden_gecirme_kuyrugu WHERE calisma_id = :r"), {"r": run_id})
            db.execute(sqltext("DELETE FROM denetim_uyarilari WHERE calisma_id = :r"), {"r": run_id})
        db.execute(sqltext("DELETE FROM maskeleme_calismalari WHERE baglam_id = :c"), {"c": context_id})
        db.execute(sqltext("DELETE FROM deger_eslemeleri WHERE baglam_id = :c"), {"c": context_id})
        db.execute(sqltext("DELETE FROM maskeleme_baglamlari WHERE id = :c"), {"c": context_id})
        db.commit()


@pytest.fixture(autouse=True)
def cleanup():
    _cleanup()
    yield
    _cleanup()


def test_partial_unique_index_rejects_a_second_concurrent_in_progress_mask_run():
    """TOCTOU penceresini elle acik tutuyoruz: A ve B, ikisi de
    _ensure_no_in_progress_run'dan (henuz hicbiri MaskingRun eklemedigi
    icin) BASARIYLA gecer - bu satirlar tam da yarisin kendisidir. Sonra
    ikisi de kendi MaskingRun satirini eklemeye calisir; SADECE biri
    basarili olmali, digeri veritabani seviyesinde
    (uq_masking_runs_context_in_progress_mask kismi UNIQUE index'i, bkz.
    migration 9a08e7b65228) reddedilmeli."""
    with SessionLocal() as setup_db:
        context = get_or_create_context(setup_db, _PROJECT_NAME, "P-RACE", "main")
        context_id = context.id
        setup_db.commit()

    session_a = SessionLocal()
    session_b = SessionLocal()
    try:
        _ensure_no_in_progress_run(session_a, context_id)
        _ensure_no_in_progress_run(session_b, context_id)

        run_a = MaskingRun(
            context_id=context_id, operation_type="mask", source_path="/a",
            initiated_by="P-RACE", status="in_progress",
        )
        session_a.add(run_a)
        session_a.flush()
        session_a.commit()

        run_b = MaskingRun(
            context_id=context_id, operation_type="mask", source_path="/b",
            initiated_by="P-RACE", status="in_progress",
        )
        session_b.add(run_b)
        with pytest.raises(IntegrityError):
            session_b.flush()
        session_b.rollback()
    finally:
        session_a.close()
        session_b.close()

    with SessionLocal() as check_db:
        rows = check_db.scalars(
            select(MaskingRun).where(
                MaskingRun.context_id == context_id,
                MaskingRun.operation_type == "mask",
                MaskingRun.status == "in_progress",
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].source_path == "/a"


def test_export_project_converts_the_missed_race_into_export_in_progress_error(tmp_path, monkeypatch):
    """_ensure_no_in_progress_run'in kendisi TOCTOU'ya acik oldugu icin,
    onu "yaris penceresini kacirdi" durumunu simule etmek uzere bilerek
    no-op'a cevirip export_project()'in yine de guvenli kaldigini
    dogruluyoruz: DB'deki unique index, uygulama katmanindaki (kacirilmis)
    on-kontrolden BAGIMSIZ olarak devreye girmeli ve export_project() bunu
    ham IntegrityError olarak degil, ExportInProgressError olarak
    yukari firlatmali."""
    source = tmp_path / "src"
    source.mkdir()
    (source / "f.txt").write_text("hello world, no secrets here\n", encoding="utf-8")

    with SessionLocal() as setup_db:
        context = get_or_create_context(setup_db, _PROJECT_NAME, "P-RACE", "main")
        already_running = MaskingRun(
            context_id=context.id, operation_type="mask", source_path="/already-running",
            initiated_by="P-RACE", status="in_progress",
        )
        setup_db.add(already_running)
        setup_db.commit()
        context_id = context.id

    monkeypatch.setattr(exporter, "_ensure_no_in_progress_run", lambda db, ctx_id: None)

    db = SessionLocal()
    try:
        with pytest.raises(ExportInProgressError):
            asyncio.run(
                export_project(
                    db,
                    source_path=str(source),
                    project_name=_PROJECT_NAME,
                    sicil_no="P-RACE",
                    branch_name="main",
                    target_path=str(tmp_path / "dst"),
                    initiated_by="P-RACE",
                )
            )
    finally:
        db.close()

    with SessionLocal() as check_db:
        rows = check_db.scalars(
            select(MaskingRun).where(
                MaskingRun.context_id == context_id,
                MaskingRun.operation_type == "mask",
                MaskingRun.status == "in_progress",
            )
        ).all()
        # Reddedilen deneme (source_path='dst' hedefli olan) DB'de KALICI iz
        # birakmamali - sadece ONCEDEN var olan tek satir kalmali.
        assert len(rows) == 1
        assert rows[0].source_path == "/already-running"
