from __future__ import annotations

from typing import Protocol
from time import time

# Sorgu/guncelleme insasi (select, update) ve DB session tipi icin cekirdek
# SQLAlchemy bilesenleri.
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.exceptions import ReviewAlreadyProcessedError
from app.db.models import AuditWarning, MaskingContext, MaskingRun


# AuditWarning (denetim_uyarilari) tablosuna erisimin soyut sozlesmesi -
# servis katmani somut SQLAlchemy implementasyonuna degil bu arayuze baglidir.
class AuditWarningRepository(Protocol):
    # Bekleyen bir denetim uyarisini onay/red durumuna gecirir.
    def transition_pending(
        self, warning_id: int, *, status: str, revalidation_token: str | None = None,
    ) -> AuditWarning:
        ...

    # Verilen kimlige (proje/sicil/branch) ait, henuz karara baglanmamis uyarilari listeler.
    def list_pending_for_identity(
        self, *, sicil_no: str, project_name: str | None = None, branch_name: str | None = None
    ) -> list[AuditWarning]:
        ...

    # Bir calismaya (run) ait tum denetim uyarilarini listeler.
    def list_for_run(self, run_id: int) -> list[AuditWarning]:
        ...


# AuditWarningRepository sozlesmesinin SQLAlchemy ile calisan somut implementasyonu.
class SqlAlchemyAuditWarningRepository:
    # Cagiran taraftan (FastAPI dependency/CLI) gelen aktif DB session'ini saklar.
    def __init__(self, db: Session) -> None:
        self.db = db

    # Aktif kimlige ait, henuz karara baglanmamis (durum='pending') ikincil
    # risk kayitlarini, en eski once olacak sekilde dondurur.
    def list_pending_for_identity(
        self, *, sicil_no: str, project_name: str | None = None, branch_name: str | None = None
    ) -> list[AuditWarning]:
        stmt = (
            select(AuditWarning)
            .join(MaskingRun, AuditWarning.run_id == MaskingRun.id)
            .join(MaskingContext, MaskingRun.context_id == MaskingContext.id)
            .where(
                AuditWarning.status == "pending",
                # Yarim kalan/basarisiz bir export'un kayitlari karar
                # bekleyenler arasinda gosterilmez: hedef klasor o islem
                # icin hic yayimlanmadi, serbest birakma yanlis yere yazardi.
                MaskingRun.status.in_(("completed", "completed_with_warnings")),
                MaskingContext.sicil_no == sicil_no,
            )
            .order_by(AuditWarning.created_at)
        )
        # Kullanici sicille tanimlanir; proje/branch yalnizca istege bagli filtredir.
        if project_name:
            stmt = stmt.where(MaskingContext.project_name == project_name)
        if branch_name:
            stmt = stmt.where(MaskingContext.branch_name == branch_name)
        return list(self.db.scalars(stmt).all())

    # Bir run_id'ye ait TUM (durum farketmeksizin) ikincil risk kayitlarini
    # dondurur - Gecmis Islemler detay ekraninda kullanilir.
    def list_for_run(self, run_id: int) -> list[AuditWarning]:
        stmt = select(AuditWarning).where(AuditWarning.run_id == run_id).order_by(AuditWarning.created_at)
        return list(self.db.scalars(stmt).all())

    # Bekleyen (durum='pending') bir uyariyi atomik UPDATE ile confirmed/dismissed
    # durumuna gecirir; kayit zaten islenmisse ReviewAlreadyProcessedError firlatir.
    def transition_pending(
        self, warning_id: int, *, status: str, revalidation_token: str | None = None,
    ) -> AuditWarning:
        if status not in {"confirmed", "dismissed"}:
            raise ValueError(f"unsupported audit warning status: {status}")

        stmt = (
            update(AuditWarning)
            .where(AuditWarning.id == warning_id, AuditWarning.status == "pending")
            .values(status=status)
            .returning(AuditWarning.id)
        )
        if revalidation_token is not None:
            stmt = stmt.where(
                AuditWarning.revalidation_token == revalidation_token,
                AuditWarning.revalidation_after > time(),
            )
        updated_id = self.db.scalar(stmt)
        if updated_id is None:
            raise ReviewAlreadyProcessedError(f"denetim_uyarilari id={warning_id} zaten islenmis veya yok")

        row = self.db.get(AuditWarning, updated_id)
        if row is None:
            raise ReviewAlreadyProcessedError(f"denetim_uyarilari id={warning_id} bulunamadi")
        return row
