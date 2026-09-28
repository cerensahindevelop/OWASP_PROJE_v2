from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_request_db
from app.api.schemas import (
    CorporateTermActivateIn,
    CorporateTermCreateIn,
    CorporateTermOut,
    TermUploadPreviewOut,
    TermUploadResultOut,
)
from app.services.term_upload import (
    activate_corporate_term,
    add_single_corporate_term,
    commit_term_upload,
    delete_corporate_term,
    list_corporate_terms,
    preview_term_upload,
)

router = APIRouter(prefix="/term-upload", tags=["term-upload"])


@router.post("/terms", response_model=CorporateTermOut, status_code=201)
def create_corporate_term(
    payload: CorporateTermCreateIn,
    db: Session = Depends(get_request_db),
) -> CorporateTermOut:
    return CorporateTermOut.model_validate(
        add_single_corporate_term(
            db,
            term=payload.term,
            title=payload.title,
            confirmed_sensitive=payload.confirmed_sensitive,
        )
    )


@router.get("/terms", response_model=list[CorporateTermOut])
def get_corporate_terms(
    include_deleted: bool = False,
    db: Session = Depends(get_request_db),
) -> list[CorporateTermOut]:
    return [
        CorporateTermOut.model_validate(item)
        for item in list_corporate_terms(db, include_deleted=include_deleted)
    ]


@router.delete("/terms/{term_id}", response_model=CorporateTermOut)
def remove_corporate_term(
    term_id: int,
    db: Session = Depends(get_request_db),
) -> CorporateTermOut:
    return CorporateTermOut.model_validate(delete_corporate_term(db, term_id))


@router.patch("/terms/{term_id}/activate", response_model=CorporateTermOut)
def activate_term(
    term_id: int,
    payload: CorporateTermActivateIn,
    db: Session = Depends(get_request_db),
) -> CorporateTermOut:
    return CorporateTermOut.model_validate(
        activate_corporate_term(
            db,
            term_id,
            confirmed_sensitive=payload.confirmed_sensitive,
        )
    )


# Yuklenen terim dosyasini onizler - hicbir satir DB'ye yazilmaz (sunucuda taslak tutulmaz).
@router.post("/preview", response_model=TermUploadPreviewOut)
async def preview_upload(
    file: UploadFile = File(...), category: str = Form(...), db: Session = Depends(get_request_db)
) -> TermUploadPreviewOut:
    content = await file.read()
    preview = preview_term_upload(db, filename=file.filename or "", content=content, category=category)
    return TermUploadPreviewOut(
        filename=preview.filename,
        category=preview.category,
        total_found=preview.total_found,
        new_valid=preview.new_valid,
        new_suspicious=[{"term": i.term, "reason": i.reason} for i in preview.new_suspicious],
        already_registered=preview.already_registered,
        rejected=[{"term": i.term, "reason": i.reason} for i in preview.rejected],
        new_count=preview.new_count,
        existing_count=preview.existing_count,
        suspicious_count=preview.suspicious_count,
        rejected_count=preview.rejected_count,
    )


# Yuklenen terim dosyasini kalici olarak kurumsal terim sozlugune ekler.
@router.post("/commit", response_model=TermUploadResultOut)
async def commit_upload(
    file: UploadFile = File(...), category: str = Form(...), db: Session = Depends(get_request_db)
) -> TermUploadResultOut:
    content = await file.read()
    result = commit_term_upload(db, filename=file.filename or "", content=content, category=category)
    return TermUploadResultOut.model_validate(result)
