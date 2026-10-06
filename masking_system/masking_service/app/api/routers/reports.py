from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_request_db
from app.api.schemas import AuditLogOut, ProjectBranchOut, RunSummaryOut
from app.services import reporting

router = APIRouter(tags=["reports"])


# Filtrelere uyan calismalari (en yeniden en eskiye) listeler.
@router.get("/runs", response_model=list[RunSummaryOut])
def get_runs(
    project_name: str | None = None,
    sicil_no: str | None = None,
    branch_name: str | None = None,
    operation_type: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_request_db),
) -> list[RunSummaryOut]:
    runs = reporting.list_runs(
        db,
        project_name=project_name,
        sicil_no=sicil_no,
        branch_name=branch_name,
        operation_type=operation_type,
        limit=limit,
    )
    return [RunSummaryOut.model_validate(r) for r in runs]


# Bir projenin en son calismasini dondurur (yoksa None).
@router.get("/runs/latest", response_model=RunSummaryOut | None)
def get_latest_run(
    project_name: str,
    sicil_no: str | None = None,
    branch_name: str | None = None,
    operation_type: str | None = None,
    db: Session = Depends(get_request_db),
) -> RunSummaryOut | None:
    run = reporting.get_latest_run(
        db, project_name=project_name, sicil_no=sicil_no, branch_name=branch_name, operation_type=operation_type
    )
    return RunSummaryOut.model_validate(run) if run else None


# Bir calismanin dosya bazli audit log kayitlarini dondurur.
@router.get("/runs/{run_id}/audit", response_model=list[AuditLogOut])
def get_run_audit(run_id: int, db: Session = Depends(get_request_db)) -> list[AuditLogOut]:
    entries = reporting.get_run_audit_entries(db, run_id)
    return [AuditLogOut.model_validate(e) for e in entries]


# Bir sicilin daha once calistigi proje/branch ciftleri (en son kullanilan once).
@router.get("/identities/project-branches", response_model=list[ProjectBranchOut])
def get_project_branches(sicil_no: str, db: Session = Depends(get_request_db)) -> list[ProjectBranchOut]:
    return [
        ProjectBranchOut(project_name=project, branch_name=branch)
        for project, branch in reporting.list_project_branches(db, sicil_no)
    ]
