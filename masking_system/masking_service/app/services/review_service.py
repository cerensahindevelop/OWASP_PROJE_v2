from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditLog, AuditWarning, MaskingRun, ReviewQueue
# SqlAlchemyReviewQueueRepository: review_queue tablosuna erisim katmani.
from app.repository.review_queue_repository import SqlAlchemyReviewQueueRepository
# synthetic_llm_rule: onaylanan LLM bulgusu icin kalici bir RuleSpec uretir
# (DB'de gercek bir kural karsiligi olmayan LLM tespitlerini de eslestirebilmek icin).
from app.services.detectors import synthetic_llm_rule
# get_or_create_mapping: onaylanan degeri kalici bir ValueMapping/placeholder'a baglar.
from app.services.mapping_service import get_or_create_mapping
from app.services.learned_decisions import remember_decision


# Gozden gecirme (review) kuyrugundaki dusuk/orta guvenli LLM bulgularini
# listeleme, onaylama, reddetme ve yok sayma islemlerini yoneten servis.
class ReviewService:
    # DB oturumunu ve review_queue repository'sini saklar.
    def __init__(self, db: Session) -> None:
        self.db = db
        self.review_queue = SqlAlchemyReviewQueueRepository(db)

    # Verilen proje/sicil/branch kimligine ait bekleyen (pending) review kayitlarini listeler.
    def list_pending_for_identity(
        self, *, project_name: str, sicil_no: str, branch_name: str
    ) -> list[ReviewQueue]:
        return self.review_queue.list_pending_for_identity(
            project_name=project_name, sicil_no=sicil_no, branch_name=branch_name
        )

    # Belirli bir run_id'ye ait tum review kayitlarini listeler.
    def list_for_run(self, run_id: int) -> list[ReviewQueue]:
        return self.review_queue.list_for_run(run_id)

    # Bulguyu onaylar, degeri kalici bir placeholder'a baglar ve dosyanin
    # kalan kararlari bittiyse yeniden dogrulama akisini tetikler.
    def approve(self, review_id: int, *, finalize: bool = True) -> ReviewQueue:
        item = self.review_queue.transition_pending(review_id, status="approved")
        if item.run_id is not None and item.found_value:
            run = self.db.get(MaskingRun, item.run_id)
            if run is not None:
                rule = synthetic_llm_rule(item.entity_type)
                mapping, created = get_or_create_mapping(self.db, run.context_id, rule, item.found_value, run_id=run.id)
                learned = remember_decision(
                    self.db, context_id=run.context_id, decision_type="sensitive",
                    value=item.found_value, entity_type=item.entity_type,
                    file_path=item.file_path, source_review_id=item.id,
                )
                self.db.add(AuditLog(
                    run_id=item.run_id, file_path=item.file_path, action="matched",
                    detail=(f"review_id={item.id} user_decision=approve finding={item.entity_type} "
                            f"ai_confidence={item.confidence_level} learned_rule_id={learned.id}"),
                ))
                if created:
                    self.db.add(
                        AuditLog(
                            run_id=item.run_id,
                            file_path=item.file_path,
                            action="replaced",
                            detail=f"gozden gecirme onayi ile eslendi: placeholder={mapping.placeholder_value}",
                        )
                    )
        if finalize:
            self._finalize_file_when_complete(item)
        return item

    def mask_file(self, review_id: int) -> dict:
        """One user action approves and masks all pending findings in this file."""
        from app.core.exceptions import ReviewAlreadyProcessedError
        item = self.db.get(ReviewQueue, review_id)
        if item is None or item.status != "pending":
            raise ReviewAlreadyProcessedError("Bu bulgu zaten işlenmiş veya bulunamadı.")
        if item.run_id is None:
            raise ValueError("Bulgunun bağlı olduğu maskeleme işlemi bulunamadı.")
        hold = self.db.scalar(select(AuditWarning).where(
            AuditWarning.run_id == item.run_id, AuditWarning.file_path == item.file_path,
            AuditWarning.status == "pending", AuditWarning.audit_failed.is_(False),
            AuditWarning.reasoning.like("INCELEME_GEREKLI:%"),
        ))
        if hold is None:
            raise ValueError("Dosyanın düzenlenebilir inceleme kaydı yok; projeyi yeniden tarayın.")
        ids = list(self.db.scalars(select(ReviewQueue.id).where(
            ReviewQueue.run_id == item.run_id, ReviewQueue.file_path == item.file_path,
            ReviewQueue.status == "pending",
        )).all())
        with self.db.begin_nested():
            for pending_id in ids:
                self.approve(pending_id, finalize=False)
        # Son dogrulama savepoint disinda bir kez calisir: kararlari commit edip
        # LLM denetimini yazma kilidi tutmadan yapar (bkz. finalize_review_hold).
        self._finalize_file_when_complete(item)
        self.db.refresh(hold)
        written = hold.status == "dismissed"
        return {"run_id": item.run_id, "file_path": item.file_path, "written": written,
                "message": ("Riskli ifadeler sistem tarafından maskelendi, eşlemeler kaydedildi ve dosya çıktıya eklendi."
                            if written else "İfadeler maskelendi ve kaydedildi; dosya son doğrulamadan geçemedi. " + hold.reasoning)}

    # Bulguyu reddeder ve yalnizca ayni context+varlik tipi+dosya kapsami icin suppression ogrenilir.
    def reject(self, review_id: int) -> ReviewQueue:
        item = self.review_queue.transition_pending(review_id, status="rejected")
        if item.run_id is not None and item.found_value:
            run = self.db.get(MaskingRun, item.run_id)
            if run is not None:
                learned = remember_decision(
                    self.db, context_id=run.context_id, decision_type="suppression",
                    value=item.found_value, entity_type=item.entity_type,
                    file_path=item.file_path, source_review_id=item.id,
                )
                self.db.add(AuditLog(
                    run_id=item.run_id, file_path=item.file_path, action="skipped",
                    detail=(f"review_id={item.id} user_decision=reject finding={item.entity_type} "
                            f"ai_confidence={item.confidence_level} suppression_rule_id={learned.id}"),
                ))
        self._finalize_file_when_complete(item)
        return item

    def _finalize_file_when_complete(self, item: ReviewQueue) -> None:
        if item.run_id is None:
            return
        self.db.flush()
        pending = self.db.scalar(select(ReviewQueue.id).where(
            ReviewQueue.run_id == item.run_id, ReviewQueue.file_path == item.file_path,
            ReviewQueue.status == "pending",
        ).limit(1))
        if pending is not None:
            return
        hold = self.db.scalar(select(AuditWarning).where(
            AuditWarning.run_id == item.run_id, AuditWarning.file_path == item.file_path,
            AuditWarning.status == "pending",
            AuditWarning.reasoning.like("INCELEME_GEREKLI:%"),
        ))
        if hold is None:
            return
        from app.services.audit_warning_service import AuditWarningService
        asyncio.run(AuditWarningService(self.db).finalize_review_hold(hold))
        self.db.flush()

    # Bulguyu yok sayar (ne onaylanir ne reddedilir, durumu 'ignored' olur).
    def ignore(self, review_id: int) -> ReviewQueue:
        return self.review_queue.transition_pending(review_id, status="ignored")
