"""Streamlit'ten AYRI calisan FastAPI backend surecinin giris noktasi.

Calistirma (masking_service/ klasorunden, streamlit_app.py ile ayni
konvansiyon):

    uvicorn api_app:app --host 127.0.0.1 --port 8001

(bkz. masking_service/api_app.py - bu modulu re-export eden ince giris dosyasi)
"""

from __future__ import annotations

from fastapi import FastAPI, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.api.deps import get_request_db
from app.api.errors import register_exception_handlers
from app.api.routers import audit_warnings, downloads, export, reports, review, rules, term_upload, unmask

# Starlette'in multipart form-data ayristiricisi, DoS korumasi icin
# istek basina VARSAYILAN 1000 dosya/1000 alan siniri uygular
# (bkz. starlette.requests.Request._get_form, FastAPI bunu File(...)/
# Form(...) parametrelerini cozerken parametresiz cagirir). Bu sistemin
# /export/upload ve /unmask/upload uclari BUTUN proje klasorlerini
# (node_modules, .git, venv dahil - kolayca binlerce dosya) tek istekte
# kabul etmek uzere tasarlandigindan, varsayilan 1000 sinirini asmak
# NORMAL bir kullanim, saldiri degil. Request.form()'un keyword-only
# varsayilanlarini surec-genelinde yukseltiyoruz (Request nesnenin
# _form onbellegi ornek-bazli oldugundan bir middleware'de erkenden
# form() cagirmak islemez - asagidaki gibi varsayilan degeri yukseltmek
# tek guvenilir yol).
_HIGHER_MULTIPART_LIMITS = {"max_files": 200_000, "max_fields": 200_000}
if Request._get_form.__kwdefaults__ is not None:
    Request._get_form.__kwdefaults__.update(_HIGHER_MULTIPART_LIMITS)
if Request.form.__kwdefaults__ is not None:
    Request.form.__kwdefaults__.update(_HIGHER_MULTIPART_LIMITS)

app = FastAPI(title="Maskeleme Sistemi API", version="1.0.0")

register_exception_handlers(app)

app.include_router(rules.router)
app.include_router(reports.router)
app.include_router(review.router)
app.include_router(audit_warnings.router)
app.include_router(term_upload.router)
app.include_router(export.router)
app.include_router(unmask.router)
app.include_router(downloads.router)


# DB baglantisini gercekten dogrulayan basit bir canlilik kontrolu -
# Streamlit tarafi (ya da bir yonetici) backend'in ayakta VE veritabanina
# erisebildigini tek bir istekle dogrulayabilsin diye.
@app.get("/health")
def health(db: Session = Depends(get_request_db)) -> dict:
    db.execute(select(1))
    return {"status": "ok"}
