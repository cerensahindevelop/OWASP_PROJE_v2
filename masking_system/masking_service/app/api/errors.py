"""Servis katmanindan gelen exception'lari HTTP status koduna esler.

app/core/error_translation.py::friendly_error() BILEREK HTTP'den bagimsiz
(saf) kalir - Turkce mesaj/detay uretimi orada tek bir yerde toplu kalsin
diye. Burasi SADECE hangi exception tipinin hangi HTTP status koduna
karsilik geldigini ekler; mesaj metni HER ZAMAN friendly_error()'dan gelir,
ikinci bir ceviri katmani YOK.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.core.error_translation import friendly_error
from app.core.exceptions import ExportInProgressError, MaskingSystemError, ReviewAlreadyProcessedError
from app.services.unmasker import ContextNotFoundError

logger = logging.getLogger(__name__)

# Exception turunden HTTP status koduna esleme - sira onemli, alt siniflar
# kendi temel sinifindan ONCE gelmeli (once yukaridan asagiya kontrol edilir).
_STATUS_BY_EXCEPTION: tuple[tuple[type[Exception], int], ...] = (
    (ContextNotFoundError, 404),
    (ExportInProgressError, 409),
    (ReviewAlreadyProcessedError, 409),
    (MaskingSystemError, 400),
    (ValueError, 400),
    (SQLAlchemyError, 503),
)


# Bir exception'in HTTP status kodunu bulur; eslesme yoksa 500 doner.
def _status_for(exc: Exception) -> int:
    for exc_type, status_code in _STATUS_BY_EXCEPTION:
        if isinstance(exc, exc_type):
            return status_code
    return 500


# Yakalanan her hatayi kullanici-dostu mesaj + dogru HTTP status koduyla JSON yanita cevirir.
async def _masking_system_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    message, detail = friendly_error(exc)
    status_code = _status_for(exc)
    if status_code == 500:
        logger.exception("Beklenmeyen hata: %s %s", request.method, request.url.path)
    return JSONResponse({"message": message, "detail": detail}, status_code=status_code)


# Bu global hata yakalayiciyi FastAPI uygulamasina kaydeder.
def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(Exception, _masking_system_exception_handler)
