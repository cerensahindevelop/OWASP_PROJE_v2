from __future__ import annotations

import asyncio
from time import time

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_request_db
from app.api.schemas import AuditRevalidationOut, AuditWarningOut, SicilScope
from app.services.audit_warning_service import AuditWarningService
from app.services.audit_warning_details import describe_audit_warning
from app.services.pending_audit_revalidator import PendingAuditRevalidator
from app.services.reporting import run_project_branch

router = APIRouter(prefix="/audit-warnings", tags=["audit-warnings"])


@router.post("/revalidate-pending", response_model=AuditRevalidationOut, status_code=202)
def revalidate_pending(payload: SicilScope, background_tasks: BackgroundTasks) -> AuditRevalidationOut:
    revalidator = PendingAuditRevalidator()
    batch = revalidator.schedule(**payload.model_dump())
    if batch.claims:
        background_tasks.add_task(revalidator.run, batch.claims)
    return AuditRevalidationOut(scheduled=len(batch.claims), active_ids=list(batch.active_ids))


def _present(item, db: Session, labels: dict[int, tuple[str, str]] | None = None) -> AuditWarningOut:
    base = AuditWarningOut.model_validate(item)
    running = bool(item.status == "pending" and item.revalidation_token and (item.revalidation_after or 0) > time())
    details = {"summary": "Dosya otomatik yeniden doğrulanıyor.", "evidence": []} if running else describe_audit_warning(item, db)
    project, branch = (labels or {}).get(item.run_id, (None, None))
    return AuditWarningOut.model_validate({
        **base.model_dump(), **details, "revalidating": running,
        "project_name": project, "branch_name": branch,
    })


# Bir kimlige ait, henuz karara baglanmamis denetim uyarilarini listeler.
@router.get("", response_model=list[AuditWarningOut])
def get_pending_audit_warnings(
    sicil_no: str, project_name: str | None = None, branch_name: str | None = None,
    db: Session = Depends(get_request_db),
) -> list[AuditWarningOut]:
    items = AuditWarningService(db).list_pending_for_identity(
        project_name=project_name, sicil_no=sicil_no, branch_name=branch_name
    )
    labels = run_project_branch(db, (i.run_id for i in items))
    return [_present(i, db, labels) for i in items]


# Bir calismaya ait tum denetim uyarilarini listeler.
@router.get("/by-run/{run_id}", response_model=list[AuditWarningOut])
def get_audit_warnings_for_run(run_id: int, db: Session = Depends(get_request_db)) -> list[AuditWarningOut]:
    items = AuditWarningService(db).list_for_run(run_id)
    return [_present(i, db) for i in items]


# Riskin gercek oldugunu onaylar - dosya karantinada kalir.
@router.post("/{warning_id}/confirm", response_model=AuditWarningOut)
def confirm_audit_warning(warning_id: int, db: Session = Depends(get_request_db)) -> AuditWarningOut:
    item = AuditWarningService(db).confirm(warning_id)
    return _present(item, db)


# Bulguyu yanlis alarm sayar, dosyayi hedef klasore serbest birakir.
@router.post("/{warning_id}/dismiss", response_model=AuditWarningOut)
def dismiss_audit_warning(warning_id: int, db: Session = Depends(get_request_db)) -> AuditWarningOut:
    # DB, offline validation, file/manifest writes and presentation all stay
    # in the request worker. Model I/O remains asynchronous on its own loop.
    item = asyncio.run(AuditWarningService(db).dismiss(warning_id))
    return _present(item, db)


@router.post("/{warning_id}/mask", response_model=AuditWarningOut)
def mask_audit_warning(warning_id: int, db: Session = Depends(get_request_db)) -> AuditWarningOut:
    item = asyncio.run(AuditWarningService(db).mask(warning_id))
    return _present(item, db)
