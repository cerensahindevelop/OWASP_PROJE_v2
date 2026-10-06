from __future__ import annotations

from typing import Protocol

# Sorgu/guncelleme insasi (select, update) ve DB session tipi icin cekirdek
# SQLAlchemy bilesenleri.
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.exceptions import ReviewAlreadyProcessedError
from app.db.models import MaskingContext, MaskingRun, ReviewQueue


# ReviewQueue (gozden_gecirme_kuyrugu) tablosuna erisimin soyut sozlesmesi.
class ReviewQueueRepository(Protocol):
    # Bekleyen bir inceleme kaydini onay/red/yoksayma durumuna gecirir.
    def transition_pending(self, review_id: int, *, status: str) -> ReviewQueue:
        ...

    def transition_equivalent_pending(self, review_id: int, *, status: str) -> tuple[ReviewQueue, int]:
        ...

    # Verilen kimlige ait, henuz karara baglanmamis inceleme kayitlarini listeler.
    def list_pending_for_identity(
        self, *, sicil_no: str, project_name: str | None = None, branch_name: str | None = None
    ) -> list[ReviewQueue]:
        ...

    # Bir calismaya (run) ait tum inceleme kayitlarini listeler.
    def list_for_run(self, run_id: int) -> list[ReviewQueue]:
        ...


# ReviewQueueRepository sozlesmesinin SQLAlchemy implementasyonu.
class SqlAlchemyReviewQueueRepository:
    # Cagiran taraftan gelen aktif DB session'ini saklar.
    def __init__(self, db: Session) -> None:
        self.db = db

    # Aktif kimlige ait, henuz karara baglanmamis (pending) bulgulari eskiden yeniye siralar.
    def list_pending_for_identity(
        self, *, sicil_no: str, project_name: str | None = None, branch_name: str | None = None
    ) -> list[ReviewQueue]:
        stmt = (
            select(ReviewQueue)
            .join(MaskingRun, ReviewQueue.run_id == MaskingRun.id)
            .join(MaskingContext, MaskingRun.context_id == MaskingContext.id)
            .where(
                ReviewQueue.status == "pending",
                # Yarim kalan/basarisiz bir export'un kayitlari karar
                # bekleyenler arasinda gosterilmez: hedef klasor o islem
                # icin hic yayimlanmadi, serbest birakma yanlis yere yazardi.
                MaskingRun.status.in_(("completed", "completed_with_warnings")),
                MaskingContext.sicil_no == sicil_no,
            )
            .order_by(ReviewQueue.created_at)
        )
        # Kullanici sicille tanimlanir; proje/branch yalnizca istege bagli filtredir.
        if project_name:
            stmt = stmt.where(MaskingContext.project_name == project_name)
        if branch_name:
            stmt = stmt.where(MaskingContext.branch_name == branch_name)
        return list(self.db.scalars(stmt).all())

    # Bir run_id'ye ait TUM (durum farketmeksizin) review kayitlarini
    # dondurur - Gecmis Islemler detay ekraninda kullanilir.
    def list_for_run(self, run_id: int) -> list[ReviewQueue]:
        stmt = select(ReviewQueue).where(ReviewQueue.run_id == run_id).order_by(ReviewQueue.created_at)
        return list(self.db.scalars(stmt).all())

    # Bekleyen (durum='pending') bir inceleme kaydini atomik UPDATE ile
    # approved/rejected/ignored durumuna gecirir; zaten islenmisse
    # ReviewAlreadyProcessedError firlatir.
    def transition_pending(self, review_id: int, *, status: str) -> ReviewQueue:
        if status not in {"approved", "rejected", "ignored"}:
            raise ValueError(f"unsupported review status: {status}")

        stmt = (
            update(ReviewQueue)
            .where(ReviewQueue.id == review_id, ReviewQueue.status == "pending")
            .values(status=status)
            .returning(ReviewQueue.id)
        )
        updated_id = self.db.scalar(stmt)
        if updated_id is None:
            raise ReviewAlreadyProcessedError(f"review_queue id={review_id} zaten islenmis veya yok")

        row = self.db.get(ReviewQueue, updated_id)
        if row is None:
            raise ReviewAlreadyProcessedError(f"review_queue id={review_id} bulunamadi")
        return row

    def transition_equivalent_pending(self, review_id: int, *, status: str) -> tuple[ReviewQueue, int]:
        """The selected row claims the decision before updating its exact peers.

        Both updates share a transaction. SQLite serializes conflicting writers;
        another decision cannot partially approve/reject the same group.
        Missing values and runless rows never form a decision group.
        """
        row = self.transition_pending(review_id, status=status)
        if row.run_id is None or not row.found_value:
            return row, 1
        peers = list(self.db.scalars(
            update(ReviewQueue).where(
                ReviewQueue.run_id == row.run_id,
                ReviewQueue.file_path == row.file_path,
                ReviewQueue.found_value == row.found_value,
                ReviewQueue.entity_type == row.entity_type,
                ReviewQueue.status == "pending",
            ).values(status=status).returning(ReviewQueue.id)
        ))
        return row, 1 + len(peers)
