from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_request_db
from app.api.schemas import AuditWarningOut
from app.services.audit_warning_service import AuditWarningService
from app.services.audit_warning_details import describe_audit_warning

router = APIRouter(prefix="/audit-warnings", tags=["audit-warnings"])


def _present(item, db: Session) -> AuditWarningOut:
    base = AuditWarningOut.model_validate(item)
    return AuditWarningOut.model_validate({**base.model_dump(), **describe_audit_warning(item, db)})


# Bir kimlige ait, henuz karara baglanmamis denetim uyarilarini listeler.
@router.get("", response_model=list[AuditWarningOut])
def get_pending_audit_warnings(
    project_name: str, sicil_no: str, branch_name: str, db: Session = Depends(get_request_db)
) -> list[AuditWarningOut]:
    items = AuditWarningService(db).list_pending_for_identity(
        project_name=project_name, sicil_no=sicil_no, branch_name=branch_name
    )
    return [_present(i, db) for i in items]


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
async def dismiss_audit_warning(warning_id: int, db: Session = Depends(get_request_db)) -> AuditWarningOut:
    item = await AuditWarningService(db).dismiss(warning_id)
    return _present(item, db)


@router.post("/{warning_id}/mask", response_model=AuditWarningOut)
async def mask_audit_warning(warning_id: int, db: Session = Depends(get_request_db)) -> AuditWarningOut:
    item = await AuditWarningService(db).mask(warning_id)
    return _present(item, db)
