"""Read-only queries over masking_runs / audit_log, joined with
masking_contexts so callers can ask identity-shaped questions ("when was
this project last exported?") without knowing the internal context_id.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import AuditLog, AuditWarning, MaskingContext, MaskingRun
from app.services.log_refs import file_ref


# Bir sicilin daha once calistigi proje/branch ciftleri, en son kullanilan
# once: maskeleme formundaki oneriler ve liste filtreleri icin.
def list_project_branches(db: Session, sicil_no: str) -> list[tuple[str, str]]:
    last_used = func.max(MaskingRun.started_at)
    rows = db.execute(
        select(MaskingContext.project_name, MaskingContext.branch_name)
        .outerjoin(MaskingRun, MaskingRun.context_id == MaskingContext.id)
        .where(MaskingContext.sicil_no == sicil_no)
        .group_by(MaskingContext.id)
        .order_by(last_used.desc().nulls_last(), MaskingContext.id.desc())
    ).all()
    return [(project, branch) for project, branch in rows]


# Liste kayitlarinin (inceleme, denetim uyarisi) hangi proje/branch'e ait
# oldugunu tek sorguda bulur: run_id -> (proje, branch).
def run_project_branch(db: Session, run_ids) -> dict[int, tuple[str, str]]:
    ids = {run_id for run_id in run_ids if run_id is not None}
    if not ids:
        return {}
    rows = db.execute(
        select(MaskingRun.id, MaskingContext.project_name, MaskingContext.branch_name)
        .join(MaskingContext, MaskingRun.context_id == MaskingContext.id)
        .where(MaskingRun.id.in_(ids))
    ).all()
    return {run_id: (project, branch) for run_id, project, branch in rows}


# Bir masking_run kaydini, ait oldugu context bilgileriyle (proje/sicil/
# branch) birlestirip rapor/CLI ciktisina hazir hale getiren ozet nesne.
@dataclass(frozen=True)
class RunSummary:
    run_id: int
    operation_type: str
    status: str
    project_name: str
    sicil_no: str
    branch_name: str
    source_path: str
    target_path: str | None
    initiated_by: str
    started_at: datetime
    completed_at: datetime | None
    files_scanned: int | None = None
    match_count: int | None = None

    # RunSummary'yi tek satirlik, insan tarafindan okunabilir metne cevirir.
    def format(self) -> str:
        return (
            f"run_id={self.run_id} [{self.operation_type}] durum={self.status} | "
            f"proje={self.project_name} sicil={self.sicil_no} branch={self.branch_name} | "
            f"baslangic={self.started_at} bitis={self.completed_at or '-'} | "
            f"yapan={self.initiated_by} | kaynak={self.source_path} hedef={self.target_path or '-'} | "
            f"dosya={self.files_scanned if self.files_scanned is not None else '-'} "
            f"bulgu={self.match_count if self.match_count is not None else '-'}"
        )


# masking_runs ile masking_contexts'i birlestiren temel sorguyu kurar
# (diger tum sorgu fonksiyonlari bunun uzerine filtre ekler).
def _run_summary_query():
    return select(
        MaskingRun.id,
        MaskingRun.operation_type,
        MaskingRun.status,
        MaskingContext.project_name,
        MaskingContext.sicil_no,
        MaskingContext.branch_name,
        MaskingRun.source_path,
        MaskingRun.target_path,
        MaskingRun.initiated_by,
        MaskingRun.started_at,
        MaskingRun.completed_at,
        MaskingRun.files_scanned,
        MaskingRun.match_count,
    ).join(MaskingContext, MaskingRun.context_id == MaskingContext.id)


# Proje adi/sicil no/branch adi/islem tipi filtrelerini bir sorguya (varsa) ekler.
def _apply_identity_filters(stmt, *, project_name, sicil_no, branch_name, operation_type):
    if project_name:
        stmt = stmt.where(MaskingContext.project_name == project_name)
    if sicil_no:
        stmt = stmt.where(MaskingContext.sicil_no == sicil_no)
    if branch_name:
        stmt = stmt.where(MaskingContext.branch_name == branch_name)
    if operation_type:
        stmt = stmt.where(MaskingRun.operation_type == operation_type)
    return stmt


# "Bu proje en son ne zaman export/import edildi?" sorusunu cevaplar -
# filtrelere uyan en son (en yeni) run kaydini dondurur, yoksa None.
def get_latest_run(
    db: Session,
    *,
    project_name: str,
    sicil_no: str | None = None,
    branch_name: str | None = None,
    operation_type: str | None = None,
) -> RunSummary | None:
    stmt = _apply_identity_filters(
        _run_summary_query(),
        project_name=project_name,
        sicil_no=sicil_no,
        branch_name=branch_name,
        operation_type=operation_type,
    )
    # started_at ties are possible (e.g. two runs whose CURRENT_TIMESTAMP
    # resolves to the same second - SQLite datetime columns only have
    # second-level resolution here), so break ties with the monotonically
    # increasing id to reliably pick whichever run was actually inserted last.
    stmt = stmt.order_by(MaskingRun.started_at.desc(), MaskingRun.id.desc()).limit(1)
    row = db.execute(stmt).first()
    return RunSummary(*row) if row else None


# Filtrelere uyan tum run kayitlarini (en yeniden en eskiye) listeler.
def list_runs(
    db: Session,
    *,
    project_name: str | None = None,
    sicil_no: str | None = None,
    branch_name: str | None = None,
    operation_type: str | None = None,
    limit: int = 50,
) -> list[RunSummary]:
    stmt = _apply_identity_filters(
        _run_summary_query(),
        project_name=project_name,
        sicil_no=sicil_no,
        branch_name=branch_name,
        operation_type=operation_type,
    )
    stmt = stmt.order_by(MaskingRun.started_at.desc(), MaskingRun.id.desc()).limit(limit)
    rows = db.execute(stmt).all()
    return [RunSummary(*row) for row in rows]


# Bir run_id'ye ait tum dosya bazli audit log kayitlarini kronolojik sirayla dondurur.
def get_run_audit_entries(db: Session, run_id: int) -> list[AuditLog]:
    return list(
        db.scalars(select(AuditLog).where(AuditLog.run_id == run_id).order_by(AuditLog.id)).all()
    )


# Log/rapordaki "maskeli yol#kimlik" etiketinin kimlik kismini, o calismanin
# DB kayitlarindaki (AuditLog/AuditWarning) kaynak yola esler. Kimlik job
# anahtarli HMAC oldugu icin yalnizca sunucuda, ayni anahtarla cozulebilir.
def find_file_by_ref(db: Session, run_id: int, ref: str) -> list[str]:
    run = db.get(MaskingRun, run_id)
    if run is None:
        return []
    ref = ref.strip().lstrip("#").lower()
    paths = set(db.scalars(select(AuditLog.file_path).where(AuditLog.run_id == run_id)).all())
    paths |= set(db.scalars(select(AuditWarning.file_path).where(AuditWarning.run_id == run_id)).all())
    return sorted(path for path in paths if path and file_ref(run.context_id, run_id, path) == ref)
