"""SADECE export (mask) ciktisi icin indirme endpoint'i.

Unmask'in kendi indirme akisi yok (bkz. app/api/routers/unmask.py modul
docstring'i) - gercek hassas veri icerdigi icin tek istekte hesaplanip
donuluyor, kalici bir klasore/route'a hic ihtiyac duymuyor."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_request_db
from app.db.models import MaskingRun
from app.webapp.uploads import UPLOADS_OUTPUT_ROOT, zip_directory_to_bytes

router = APIRouter(tags=["downloads"])

# Yeni token'lar tam UUID4 hex (128 bit) kullanir. 16 haneli eski token'lar
# mevcut tamamlanmis export'lar indirilebilsin diye geriye donuk kabul edilir.
_OUTPUT_TOKEN_RE = re.compile(r"^(?:[0-9a-f]{16}|[0-9a-f]{32})$")


def _zip_response(target_dir: Path, *, filename: str) -> Response:
    """Verilen export klasorunu tek tip HTTP yanitina paketler."""
    zip_bytes = zip_directory_to_bytes(target_dir)
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# Yukleme-modu export ciktisini, POST /export/upload yanitinda donen opak
# token ile indirir. Bu endpoint bilerek DB kaydina bagli degildir: FastAPI'nin
# yield-dependency sonlandiricisi transaction'i response body gonderildikten
# sonra commit edebilir. UI'nin POST yanitini alir almaz actigi ikinci GET'in
# henuz commit edilmemis run satirini aramasi ara sira yanlis 404 uretiyordu.
@router.get("/export/outputs/{output_token}/download")
def download_uploaded_export_output(output_token: str) -> Response:
    if _OUTPUT_TOKEN_RE.fullmatch(output_token) is None:
        raise HTTPException(status_code=404, detail="İndirilebilir çıktı bulunamadı.")

    output_root = UPLOADS_OUTPUT_ROOT.resolve()
    target_dir = (output_root / output_token).resolve()
    try:
        target_dir.relative_to(output_root)
    except ValueError:
        raise HTTPException(status_code=404, detail="İndirilebilir çıktı bulunamadı.") from None

    if not target_dir.is_dir():
        raise HTTPException(status_code=404, detail="Çıktı klasörü artık mevcut değil.")

    return _zip_response(target_dir, filename="maskelenmis_cikti.zip")


# Bir export calismasinin maskelenmis ciktisini zip olarak indirir.
@router.get("/runs/{run_id}/download")
def download_run_output(run_id: int, db: Session = Depends(get_request_db)) -> Response:
    run = db.get(MaskingRun, run_id)
    if run is None or run.operation_type != "mask" or not run.target_path:
        raise HTTPException(status_code=404, detail="Bu çalışma için indirilebilir bir çıktı bulunamadı.")

    target_dir = Path(run.target_path)
    if not target_dir.is_dir():
        raise HTTPException(status_code=404, detail="Çıktı klasörü artık mevcut değil.")

    return _zip_response(target_dir, filename=f"maskelenmis_cikti_{run_id}.zip")
