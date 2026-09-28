"""Async event-loop boundary regression tests for POST /export(/upload).

export_project'in kendisi `async def` olsa da pipeline'inin ezici cogunlugu
(dosya I/O, siniflandirma, regex/Presidio tespiti, DB yazma, sozdizimi/
round-trip dogrulama, consistency taramasi, finalize) senkron/CPU-bound
calisir - bkz. app/services/exporter.py modul dokstring'i. Bu yuzden
app/api/routers/export.py, export_project'i `run_in_threadpool` ile kendi
ozel event loop'unda (asyncio.run) ayri bir thread'de calistirir: tek
FastAPI event loop'u boylece tek bir uzun export boyunca BLOKLANMAZ.

Bu dosya o siniri, sadece "kod async gorunuyor" degil "event loop
GERCEKTEN serbest kaliyor" seviyesinde dogrular - milisaniye esigine degil
(kirilgan), bir senkronizasyon sayacina (heartbeat tick count) dayanan
deterministik bir yontemle."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy import text as sqltext

from app.api.main import app
from app.db.models import MaskingRun
from app.db.session import SessionLocal
from app.services import exporter as exporter_module
from app.services.detectors import DetectorOutput
from app.services.mapping_service import get_or_create_context
from app.webapp import uploads

_IDENTITY_PREFIX = "pytest-export-event-loop"
_SICIL_NO = "P-TEST-0001"
_BRANCH = "pytest-branch"
_BLOCK_SECONDS = 0.4


def _cleanup_identity(project_name: str) -> None:
    with SessionLocal() as db:
        row = db.execute(
            sqltext(
                "SELECT id FROM maskeleme_baglamlari WHERE proje_adi=:p AND personel_no=:pn AND branch_adi=:b"
            ),
            {"p": project_name, "pn": _SICIL_NO, "b": _BRANCH},
        ).first()
        if row is None:
            return
        context_id = row[0]
        run_ids = [
            r[0]
            for r in db.execute(
                sqltext("SELECT id FROM maskeleme_calismalari WHERE baglam_id=:c"), {"c": context_id}
            ).all()
        ]
        for run_id in run_ids:
            db.execute(sqltext("DELETE FROM denetim_kaydi WHERE calisma_id=:r"), {"r": run_id})
            db.execute(sqltext("DELETE FROM gozden_gecirme_kuyrugu WHERE calisma_id=:r"), {"r": run_id})
            db.execute(sqltext("DELETE FROM denetim_uyarilari WHERE calisma_id=:r"), {"r": run_id})
        db.execute(sqltext("DELETE FROM maskeleme_calismalari WHERE baglam_id=:c"), {"c": context_id})
        db.execute(sqltext("DELETE FROM deger_eslemeleri WHERE baglam_id=:c"), {"c": context_id})
        db.execute(sqltext("DELETE FROM maskeleme_baglamlari WHERE id=:c"), {"c": context_id})
        db.commit()


@pytest.fixture
def cleanup():
    created: list[str] = []
    yield created
    for project_name in created:
        _cleanup_identity(project_name)


class _BlockingFakeOrchestrator:
    """Faz A/C tespitinin gercek Presidio/regex katmanlariyla AYNI desen -
    `async def` ama icinde gercek `await` YOK, senkron/CPU-bound
    `time.sleep` ile davranisini deterministik olarak taklit eder (bkz.
    presidio_detector.py::PresidioDetector.detect ve
    detectors.py::RuleBasedDetector.detect)."""

    def __init__(self, block_seconds: float) -> None:
        self._block_seconds = block_seconds

    async def scan(self, text, metadata=None):
        time.sleep(self._block_seconds)
        return DetectorOutput()


async def _post_export_upload(client: httpx.AsyncClient, project_name: str):
    return await client.post(
        "/export/upload",
        data={
            "project_name": project_name, "sicil_no": _SICIL_NO,
            "branch_name": _BRANCH, "initiated_by": _SICIL_NO,
            "is_directory_upload": "false",
        },
        files={"files": ("sample.txt", b"Ordinary project documentation.\n", "text/plain")},
    )


def test_export_upload_does_not_freeze_the_event_loop_for_other_async_work(cleanup, tmp_path, monkeypatch):
    """Test A+B: export_project icinde CPU-bound/bloklayici bir tespit
    katmani (gercek Presidio/regex'in taklidi) calisirken, AYNI event
    loop'ta paralel yasayan baska bir es zamanli is (heartbeat) ilerlemeye
    devam edebilmeli. Eski davranista (export_project dogrudan await
    edildiginde) heartbeat, export'un TUM bloklayici suresi boyunca
    tamamen durur (tick_count ~ 0) - run_in_threadpool siniri sayesinde
    artik durmamali."""
    project = f"{_IDENTITY_PREFIX}-heartbeat"
    cleanup.append(project)

    monkeypatch.setattr(
        exporter_module, "build_orchestrator", lambda *a, **kw: _BlockingFakeOrchestrator(_BLOCK_SECONDS)
    )
    monkeypatch.setattr(uploads, "UPLOADS_OUTPUT_ROOT", tmp_path / "uploads_output")

    async def _scenario():
        tick_count = 0
        stop = asyncio.Event()

        async def _heartbeat() -> None:
            nonlocal tick_count
            while not stop.is_set():
                tick_count += 1
                await asyncio.sleep(0.01)

        heartbeat_task = asyncio.create_task(_heartbeat())
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://export-test"
            ) as client:
                response = await _post_export_upload(client, project)
        finally:
            stop.set()
            await heartbeat_task
        return response, tick_count

    response, tick_count = asyncio.run(_scenario())

    assert response.status_code == 200, response.text
    # Bloklayici sure (_BLOCK_SECONDS=0.4s) boyunca event loop serbestse,
    # 0.01s araliklarla atan heartbeat bu pencerede onlarca kez tick atmis
    # olmali. Bloke olsaydi tick_count ~0-1 kalirdi - deterministik,
    # kirilgan bir ms esigine DEGIL bol paylı bir tick-sayisina dayanir.
    assert tick_count >= 15, (
        f"event loop export sirasinda bloke gorunuyor (heartbeat sadece {tick_count} kez tick atti, "
        f"beklenen >= 15)"
    )


def test_two_concurrent_export_requests_isolate_mappings_and_one_failure_does_not_affect_the_other(
    cleanup, tmp_path, monkeypatch
):
    """Test C+D: iki bagimsiz /export/upload istegi AYNI ANDA (asyncio.gather)
    tetiklenir. Biri (project_conflict) DB'deki 'zaten devam eden run'
    kismi UNIQUE index'i yuzunden kesin BASARISIZ olacak sekilde onceden
    hazirlanir (bkz. exporter.py::_ensure_no_in_progress_run /
    tests/test_concurrent_export_guard.py ile ayni ilke) - bu, gercek bir
    concurrent export basarisizligini deterministik olarak tetikler. Diger
    (project_ok) TAMAMEN bagimsiz bir mapping/context'e sahip olmali ve bu
    basarisizliktan ETKILENMEDEN basariyla tamamlanmali (job/mapping izolasyonu)."""
    project_ok = f"{_IDENTITY_PREFIX}-ok"
    project_conflict = f"{_IDENTITY_PREFIX}-conflict"
    cleanup.append(project_ok)
    cleanup.append(project_conflict)

    monkeypatch.setattr(exporter_module, "build_orchestrator", lambda *a, **kw: _BlockingFakeOrchestrator(0.05))
    monkeypatch.setattr(uploads, "UPLOADS_OUTPUT_ROOT", tmp_path / "uploads_output")

    with SessionLocal() as db:
        context = get_or_create_context(db, project_conflict, _SICIL_NO, _BRANCH)
        db.add(
            MaskingRun(
                context_id=context.id,
                operation_type="mask",
                source_path="/already-running",
                initiated_by=_SICIL_NO,
                status="in_progress",
            )
        )
        db.commit()
        conflict_context_id = context.id

    async def _scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://export-test"
        ) as client:
            return await asyncio.gather(
                _post_export_upload(client, project_ok),
                _post_export_upload(client, project_conflict),
            )

    response_ok, response_conflict = asyncio.run(_scenario())

    assert response_ok.status_code == 200, response_ok.text
    assert response_ok.json()["report"]["files_scanned"] == 1

    # ExportInProgressError -> 409 (bkz. app/api/errors.py eslemesi).
    assert response_conflict.status_code == 409, response_conflict.text

    # project_ok'un kendi calismasi, project_conflict'in reddiyle
    # ILGISIZ, kendi run'ini tam olarak tamamlamis olmali.
    with SessionLocal() as db:
        ok_context = get_or_create_context(db, project_ok, _SICIL_NO, _BRANCH)
        ok_runs = db.scalars(
            select(MaskingRun).where(MaskingRun.context_id == ok_context.id)
        ).all()
        assert len(ok_runs) == 1
        assert ok_runs[0].status in {"completed", "completed_with_warnings"}

        # project_conflict tarafinda, onceden var olan tek satirin disinda
        # YENI bir run KALICI olarak eklenmemis olmali (reddedilen deneme iz birakmaz).
        conflict_runs = db.scalars(
            select(MaskingRun).where(MaskingRun.context_id == conflict_context_id)
        ).all()
        assert len(conflict_runs) == 1
        assert conflict_runs[0].source_path == "/already-running"
