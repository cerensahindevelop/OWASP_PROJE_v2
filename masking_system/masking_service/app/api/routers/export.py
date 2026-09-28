"""Export (mask) endpoint'leri.

Yol-modu (POST /export) ve yukleme-modu (POST /export/upload) - ikisi de
app/services/exporter.py::export_project'i AYNEN cagirir, is mantigi burada
TEKRARLANMAZ. Yukleme-modunda kalici uploaded_output_dir(token) kullanilir
(export ciktisi zaten MASKELENMIS/hassas olmayan veridir, bugunku Streamlit
davranisiyla ayni - bkz. app/webapp/uploads.py docstring'i, karantina/onay
akisi bu klasorun kalici kalmasini zaten gerektiriyordu).

export_project `async def` olsa da (LLM Katman 3 icin gercek `await`
noktalari tasir), pipeline'in geri kalani (dosya I/O, siniflandirma,
regex/Presidio tespiti, DB yazma, sozdizimi/round-trip dogrulama, consistency
taramasi, finalize) BASTAN SONA senkron/CPU-bound calisir - bkz.
app/services/exporter.py::export_project docstring'i. Bu yuzden burada
`run_in_threadpool` ile KENDI ozel event loop'unda (`asyncio.run`) ayri bir
thread'de calistirilir: tek FastAPI event loop'u, uzun surebilen bir export
boyunca BLOKLANMAZ ve ayni anda gelen diger istekler (orn. /health, baska
bir export'un baslangic DB kontrolu) ilerleyebilir - unmask.py'daki
`run_in_threadpool` kullanimiyla AYNI ilke. exporter.py'nin kendi ici
(asyncio.gather + semaphore ile sinirlanan LLM concurrency) DEGISTIRILMEDI -
sadece hangi thread'de calistigi degisti, o thread'in KENDI event loop'u
icinde davranisi birebir ayni."""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api import export_jobs
from app.api.deps import get_request_db
from app.api.errors import _status_for
from app.api.schemas import ExportJobOut, ExportJobStartOut, ExportPathRequest, ExportReportOut, ExportResultOut
from app.core.error_translation import friendly_error
from app.db.session import SessionLocal
from app.services.audit_warning_service import AuditWarningService
from app.services.exporter import export_project
from app.services.review_service import ReviewService
from app.webapp.path_guard import ensure_path_allowed
from app.webapp.uploads import cleanup_temp_dir, save_uploaded_files_to_temp_dir, uploaded_output_dir

router = APIRouter(prefix="/export", tags=["export"])


# export_project'i (bkz. modul dokstring'i) kendi ozel event loop'unda ayri
# bir thread'de calistiran senkron kopru - run_in_threadpool'a verilebilecek
# tek sey senkron bir callable oldugu icin (dogrudan bir coroutine DEGIL).
def _run_export_in_thread(db: Session, **kwargs):
    return asyncio.run(export_project(db, **kwargs))


# app/webapp/uploads.py::save_uploaded_files_to_temp_dir Streamlit'in
# UploadedFile'ini (.name/.getvalue()) bekler - FastAPI'nin UploadFile'i
# (.filename/async .read()) farkli bir shape'e sahip oldugu icin, is
# mantigini (zip-slip/path-traversal korumasi dahil) uploads.py icinde
# DEGISTIRMEDEN yeniden kullanmak icin ince bir adapter.
class _UploadedFileAdapter:
    def __init__(self, name: str, content: bytes) -> None:
        self.name = name
        self._content = content

    def getvalue(self) -> bytes:
        return self._content


# Bu run icin bekleyen inceleme/karantina kayitlarinin sayisini dondurur.
def _pending_and_quarantined_counts(
    db: Session, *, project_name: str, sicil_no: str, branch_name: str, run_id: int
) -> tuple[int, int, int]:
    pending_count = len(
        [
            item
            for item in ReviewService(db).list_pending_for_identity(
                project_name=project_name, sicil_no=sicil_no, branch_name=branch_name
            )
            if item.run_id == run_id
        ]
    )
    warning_items = [
        item for item in AuditWarningService(db).list_pending_for_identity(
                project_name=project_name, sicil_no=sicil_no, branch_name=branch_name
            )
            if item.run_id == run_id
    ]
    quarantined_count = sum(
        not item.audit_failed and not item.reasoning.startswith("INCELEME_GEREKLI:")
        for item in warning_items
    )
    validation_failed_count = sum(item.audit_failed for item in warning_items)
    return pending_count, quarantined_count, validation_failed_count


# Sunucu uzerindeki bir klasor yolunu maskeleyip disa aktarir.
@router.post("", response_model=ExportResultOut)
async def export_by_path(payload: ExportPathRequest, db: Session = Depends(get_request_db)) -> ExportResultOut:
    # WEB_ALLOWED_ROOTS disina cikan bir yol burada REDDEDILIR - path artik
    # ag uzerinden gelen guvenilmeyen bir girdi, istemci (Streamlit) tarafinda
    # ayni kontrolun tekrarlanmasina GUVENILMEZ (bkz. plan dosyasi).
    ensure_path_allowed(payload.source_path, label="Kaynak Klasör")
    ensure_path_allowed(payload.target_path, label="Hedef Klasör")

    report = await run_in_threadpool(
        _run_export_in_thread,
        db,
        source_path=payload.source_path,
        project_name=payload.project_name,
        sicil_no=payload.sicil_no,
        branch_name=payload.branch_name,
        target_path=payload.target_path,
        initiated_by=payload.initiated_by,
    )
    pending_count, quarantined_count, validation_failed_count = _pending_and_quarantined_counts(
        db,
        project_name=payload.project_name,
        sicil_no=payload.sicil_no,
        branch_name=payload.branch_name,
        run_id=report.run_id,
    )
    return ExportResultOut(
        report=ExportReportOut.model_validate(report),
        pending_count=pending_count,
        quarantined_count=quarantined_count,
        validation_failed_count=validation_failed_count,
        output_token=None,
    )


# Yuklenen dosya/klasoru maskeleyip disa aktarir (tarayicidan dosya yukleme modu).
@router.post("/upload", response_model=ExportResultOut)
async def export_upload(
    project_name: str = Form(...),
    sicil_no: str = Form(...),
    branch_name: str = Form(...),
    initiated_by: str = Form(...),
    is_directory_upload: bool = Form(False),
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_request_db),
) -> ExportResultOut:
    adapted = [_UploadedFileAdapter(f.filename or "", await f.read()) for f in files]
    source_dir = save_uploaded_files_to_temp_dir(adapted, is_directory_upload=is_directory_upload)
    # Token ayni zamanda upload ciktisinin tahmin edilemeyen indirme
    # yetenegidir; UUID4'un tamamini (128 bit) kullan, kisaltma.
    try:
        target_dir = uploaded_output_dir(uuid.uuid4().hex)
        report = await run_in_threadpool(
            _run_export_in_thread,
            db,
            source_path=str(source_dir),
            project_name=project_name,
            sicil_no=sicil_no,
            branch_name=branch_name,
            target_path=str(target_dir),
            initiated_by=initiated_by,
        )
        pending_count, quarantined_count, validation_failed_count = _pending_and_quarantined_counts(
            db, project_name=project_name, sicil_no=sicil_no, branch_name=branch_name, run_id=report.run_id
        )
    finally:
        # Yuklenen kaynak dosyalar sadece bu taramada kullanildi, artik gerek
        # yok - target_dir ise BILEREK silinmiyor (bkz. modul docstring'i).
        cleanup_temp_dir(source_dir)

    return ExportResultOut(
        report=ExportReportOut.model_validate(report),
        pending_count=pending_count,
        quarantined_count=quarantined_count,
        validation_failed_count=validation_failed_count,
        output_token=target_dir.name,
    )


# --------------------------------------------------------------------------
# Arka plan isleri: export ayri thread'de calisir, arayuz ilerlemeyi sorgular.
# Mevcut bloklayan uclar (POST /export, /export/upload) geriye donuk uyumluluk
# icin korunur.
# --------------------------------------------------------------------------


def _job_error(exc: Exception) -> tuple[str, str | None, int]:
    message, detail = friendly_error(exc)
    return message, detail, _status_for(exc)


def _export_job_work(kwargs: dict, output_token: str | None):
    def work(progress) -> dict:
        with SessionLocal() as db:
            report = asyncio.run(export_project(db, progress_callback=progress, **kwargs))
            pending_count, quarantined_count, validation_failed_count = _pending_and_quarantined_counts(
                db, project_name=kwargs["project_name"], sicil_no=kwargs["sicil_no"],
                branch_name=kwargs["branch_name"], run_id=report.run_id,
            )
            db.commit()
            return ExportResultOut(
                report=ExportReportOut.model_validate(report),
                pending_count=pending_count,
                quarantined_count=quarantined_count,
                validation_failed_count=validation_failed_count,
                output_token=output_token,
            ).model_dump(mode="json")
    return work


# Sunucu uzerindeki bir klasor icin arka planda export baslatir.
@router.post("/jobs", response_model=ExportJobStartOut, status_code=202)
def start_export_job_by_path(payload: ExportPathRequest) -> ExportJobStartOut:
    ensure_path_allowed(payload.source_path, label="Kaynak Klasör")
    ensure_path_allowed(payload.target_path, label="Hedef Klasör")
    kwargs = dict(
        source_path=payload.source_path, project_name=payload.project_name, sicil_no=payload.sicil_no,
        branch_name=payload.branch_name, target_path=payload.target_path, initiated_by=payload.initiated_by,
    )
    job = export_jobs.start_job(_export_job_work(kwargs, None), _job_error)
    return ExportJobStartOut(job_id=job.job_id)


# Yuklenen dosya/klasor icin arka planda export baslatir.
@router.post("/upload/jobs", response_model=ExportJobStartOut, status_code=202)
async def start_export_job_upload(
    project_name: str = Form(...),
    sicil_no: str = Form(...),
    branch_name: str = Form(...),
    initiated_by: str = Form(...),
    is_directory_upload: bool = Form(False),
    files: list[UploadFile] = File(...),
) -> ExportJobStartOut:
    adapted = [_UploadedFileAdapter(f.filename or "", await f.read()) for f in files]
    source_dir = save_uploaded_files_to_temp_dir(adapted, is_directory_upload=is_directory_upload)
    try:
        target_dir = uploaded_output_dir(uuid.uuid4().hex)
    except BaseException:
        cleanup_temp_dir(source_dir)
        raise
    kwargs = dict(
        source_path=str(source_dir), project_name=project_name, sicil_no=sicil_no,
        branch_name=branch_name, target_path=str(target_dir), initiated_by=initiated_by,
    )
    job = export_jobs.start_job(
        _export_job_work(kwargs, target_dir.name), _job_error,
        cleanup=lambda: cleanup_temp_dir(source_dir),
    )
    return ExportJobStartOut(job_id=job.job_id)


# Arka plan export isinin durumunu ve ilerlemesini dondurur.
@router.get("/jobs/{job_id}", response_model=ExportJobOut)
def get_export_job(job_id: str) -> ExportJobOut:
    job = export_jobs.registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="İşlem bulunamadı (servis yeniden başlatılmış olabilir).")
    return ExportJobOut.model_validate(job.snapshot())
