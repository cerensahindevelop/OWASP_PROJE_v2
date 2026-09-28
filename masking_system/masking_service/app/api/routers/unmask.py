"""Unmask (geri donusum) endpoint'leri.

unmask_project SENKRON bir fonksiyondur (export_project'in aksine) - burada
`run_in_threadpool` ile calistirilir, boylece uzun surebilen bir geri
donusum tek FastAPI event loop'unu (ve dolayisiyla ayni anda gelen diger
tum istekleri, orn. /health) BLOKLAMAZ.

Yukleme-modu (POST /unmask/upload) BILEREK export'tan FARKLI tasarlandi:
unmask ciktisi GERCEK, sifresi cozulmus hassas veri icerir (isim, TC no
vb.) - bu yuzden export'taki gibi kalici bir token/klasor + ayri bir
/download endpoint'i YOK (bkz. plan dosyasindaki kullanici karari). Tek
istekte hesaplanir, zip'lenip base64 olarak AYNI yanitta donulur, gecici
klasor basarili yanit donmeden ONCE silinir. Silme hatasi basarili yaniti
engeller ve yonetici incelemesi icin kayda alinir."""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_request_db
from app.api.schemas import UnmaskPathRequest, UnmaskPathResultOut, UnmaskReportOut, UnmaskUploadResultOut
from app.services.unmasker import unmask_project
from app.webapp.path_guard import ensure_path_allowed
from app.webapp.uploads import cleanup_temp_dir, is_single_plain_file_upload, save_uploaded_files_to_temp_dir, zip_directory_to_bytes

router = APIRouter(prefix="/unmask", tags=["unmask"])


# FastAPI'nin UploadFile'ini uploads.py'nin bekledigi (Streamlit-tarzi) shape'e uydurur.
class _UploadedFileAdapter:
    def __init__(self, name: str, content: bytes) -> None:
        self.name = name
        self._content = content

    def getvalue(self) -> bytes:
        return self._content


# Sunucu uzerindeki maskelenmis bir klasoru gercek degerlere geri donusturur.
@router.post("", response_model=UnmaskPathResultOut)
async def unmask_by_path(payload: UnmaskPathRequest, db: Session = Depends(get_request_db)) -> UnmaskPathResultOut:
    ensure_path_allowed(payload.source_path, label="Maskelenmiş Proje Klasörü")
    ensure_path_allowed(payload.target_path, label="Çıktı Klasörü")

    report = await run_in_threadpool(
        unmask_project,
        db,
        source_path=payload.source_path,
        project_name=payload.project_name,
        sicil_no=payload.sicil_no,
        branch_name=payload.branch_name,
        target_path=payload.target_path,
        initiated_by=payload.initiated_by,
        job_id=payload.job_id,
    )
    return UnmaskPathResultOut(report=UnmaskReportOut.model_validate(report))


# Yuklenen maskelenmis dosya/klasoru geri donusturur ve sonucu indirilebilir olarak dondurur.
@router.post("/upload", response_model=UnmaskUploadResultOut)
async def unmask_upload(
    project_name: str = Form(...),
    sicil_no: str = Form(...),
    branch_name: str = Form(...),
    initiated_by: str = Form(...),
    is_directory_upload: bool = Form(False),
    job_id: int | None = Form(None, ge=1),
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_request_db),
) -> UnmaskUploadResultOut:
    adapted = [_UploadedFileAdapter(f.filename or "", await f.read()) for f in files]
    source_dir = save_uploaded_files_to_temp_dir(adapted, is_directory_upload=is_directory_upload)
    target_dir = None

    try:
        target_dir = Path(tempfile.mkdtemp(prefix="osw_unmask_dst_"))
        report = await run_in_threadpool(
            unmask_project,
            db,
            source_path=str(source_dir),
            project_name=project_name,
            sicil_no=sicil_no,
            branch_name=branch_name,
            target_path=str(target_dir),
            initiated_by=initiated_by,
            job_id=job_id,
        )

        if is_single_plain_file_upload(adapted, is_directory_upload=is_directory_upload):
            out_path = target_dir / Path(adapted[0].name).name
            download_bytes = out_path.read_bytes() if out_path.exists() else None
            download_filename = f"geri_donusturulmus_{out_path.name}" if download_bytes is not None else None
        else:
            download_bytes = zip_directory_to_bytes(target_dir)
            download_filename = "geri_donusturulmus_cikti.zip"
    finally:
        # Gercek hassas veri iceren hem kaynak hem hedef gecici klasor,
        # basarili yanit donmeden ONCE temizlenir; hata varsa yanit durdurulur.
        cleanup_temp_dir(source_dir, target_dir)

    return UnmaskUploadResultOut(
        report=UnmaskReportOut.model_validate(report),
        download_base64=base64.b64encode(download_bytes).decode("ascii") if download_bytes is not None else None,
        download_filename=download_filename,
    )
