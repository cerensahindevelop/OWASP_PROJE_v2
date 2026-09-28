from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_request_db
from app.api.schemas import ReviewQueueOut, FileMaskResultOut
from app.services.review_service import ReviewService

router = APIRouter(prefix="/reviews", tags=["review"])


# Bir kimlige ait, henuz karara baglanmamis inceleme kayitlarini listeler.
@router.get("", response_model=list[ReviewQueueOut])
def get_pending_reviews(
    project_name: str, sicil_no: str, branch_name: str, db: Session = Depends(get_request_db)
) -> list[ReviewQueueOut]:
    items = ReviewService(db).list_pending_for_identity(
        project_name=project_name, sicil_no=sicil_no, branch_name=branch_name
    )
    return [ReviewQueueOut.model_validate(i) for i in items]


# Bir calismaya ait tum inceleme kayitlarini listeler.
@router.get("/by-run/{run_id}", response_model=list[ReviewQueueOut])
def get_reviews_for_run(run_id: int, db: Session = Depends(get_request_db)) -> list[ReviewQueueOut]:
    items = ReviewService(db).list_for_run(run_id)
    return [ReviewQueueOut.model_validate(i) for i in items]


# Bulguyu onaylar ve degeri kalici olarak bir placeholder'a baglar.
@router.post("/{review_id}/approve", response_model=ReviewQueueOut)
def approve_review(review_id: int, db: Session = Depends(get_request_db)) -> ReviewQueueOut:
    item = ReviewService(db).approve(review_id)
    return ReviewQueueOut.model_validate(item)


# Bulguyu reddeder - deger hicbir zaman placeholder'a baglanmaz.
@router.post("/{review_id}/reject", response_model=ReviewQueueOut)
def reject_review(review_id: int, db: Session = Depends(get_request_db)) -> ReviewQueueOut:
    item = ReviewService(db).reject(review_id)
    return ReviewQueueOut.model_validate(item)


@router.post("/{review_id}/mask-file", response_model=FileMaskResultOut)
def mask_review_file(review_id: int, db: Session = Depends(get_request_db)) -> FileMaskResultOut:
    return FileMaskResultOut.model_validate(ReviewService(db).mask_file(review_id))
