"""Background final passes for obsolete audit evidence.

Claims live in SQLite, so polling, multiple users and worker restarts cannot
start the same pass twice. Each file runs in a worker that owns its Session,
async loop and HTTP pool. Model admission and the four-file limit are shared
across workers. Cancellation drains the worker before clearing its claim.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import re
from pathlib import Path
from time import time
from typing import Callable
from uuid import uuid4

from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import ReviewAlreadyProcessedError
from app.db.models import AuditWarning
from app.db.session import SessionLocal
from app.repository.audit_warning_repository import SqlAlchemyAuditWarningRepository
from app.services.audit_reviewer import AuditFinding, AuditFindingVerifier, AuditVerdict, audit_record_key, decode_audit_record
from app.services.audit_warning_service import AuditWarningService
from app.services.failed_checks import FailedCheck
from app.services.file_classifier import is_lock_filename
from app.services.llm_admission import admission_pool
from app.services.session_worker import SessionWorker

logger = logging.getLogger("uvicorn.error.llm")
_QUOTE_RE = re.compile(r"\(ilgili bolum: '(.*?)'\)", re.DOTALL)
_RETRY_DELAY_SECONDS = 300
_MAX_CONCURRENT_FILES = 4
_MIN_FILE_BUDGET_SECONDS = 60.0
_MAX_FILE_BUDGET_SECONDS = 600.0
_LEASE_GRACE_SECONDS = 30


@dataclass(frozen=True)
class RevalidationClaim:
    warning_id: int
    token: str
    key: str
    deadline: float


@dataclass(frozen=True)
class RevalidationBatch:
    claims: tuple[RevalidationClaim, ...]
    active_ids: tuple[int, ...]


class PendingAuditRevalidator:
    def __init__(self, session_factory: Callable[[], Session] = SessionLocal) -> None:
        self._sessions = session_factory
        self._worker = SessionWorker(session_factory)
        self._concurrency = min(_MAX_CONCURRENT_FILES, settings.vllm.max_concurrent_requests)
        self._batch_size = min(settings.vllm.file_batch_size, self._concurrency)
        request_budget = settings.vllm.timeout_seconds * (settings.vllm.transient_retries + 1) * 2
        self._timeout = max(_MIN_FILE_BUDGET_SECONDS, min(_MAX_FILE_BUDGET_SECONDS, request_budget))

    @staticmethod
    def _obsolete_evidence(warning: AuditWarning) -> bool:
        if warning.audit_failed or warning.failed_check not in {None, FailedCheck.LLM_DENETIMI}:
            return False
        if warning.encoding == "java-class-v1" or is_lock_filename(Path(warning.file_path).name):
            return False
        if warning.reasoning.startswith("INCELEME_GEREKLI:"):
            return False
        quotes = _QUOTE_RE.findall(warning.reasoning)
        if not quotes:
            return False
        verifier = AuditFindingVerifier(warning.masked_content, warning.file_path)
        return not any(verifier.resolve(quote) for quote in quotes)

    def schedule(
        self, *, sicil_no: str, project_name: str | None = None, branch_name: str | None = None,
    ) -> RevalidationBatch:
        if not settings.vllm.enabled:
            return RevalidationBatch((), ())
        now = time()
        claims: list[RevalidationClaim] = []
        with self._sessions() as db:
            rows = SqlAlchemyAuditWarningRepository(db).list_pending_for_identity(
                project_name=project_name, sicil_no=sicil_no, branch_name=branch_name,
            )
            active = [row.id for row in rows if row.revalidation_token and (row.revalidation_after or 0) > now]
            active_set = set(active)
            capacity = max(0, self._batch_size - len(active))
            for row in rows:
                if len(claims) >= capacity:
                    break
                if row.id in active_set:
                    continue
                key = audit_record_key(row.masked_content, row.file_path, settings.vllm)
                if row.revalidation_key == key and (row.revalidation_after or 0) > now:
                    continue
                cached = decode_audit_record(row.audit_record, key)
                if cached is not None and cached.risky:
                    continue
                if not self._obsolete_evidence(row):
                    continue
                token = uuid4().hex
                updated = db.scalar(update(AuditWarning).where(
                    AuditWarning.id == row.id,
                    AuditWarning.status == "pending",
                    AuditWarning.audit_failed.is_(False),
                    AuditWarning.masked_content == row.masked_content,
                    AuditWarning.reasoning == row.reasoning,
                    or_(
                        (AuditWarning.revalidation_token.is_(None)) & or_(
                            AuditWarning.revalidation_key.is_(None),
                            AuditWarning.revalidation_key != key,
                            AuditWarning.revalidation_after.is_(None),
                            AuditWarning.revalidation_after <= now,
                        ),
                        AuditWarning.revalidation_after <= now,
                    ),
                ).values(
                    revalidation_key=key, revalidation_token=token,
                    revalidation_after=now + self._timeout + _LEASE_GRACE_SECONDS,
                ).returning(AuditWarning.id))
                if updated is not None:
                    claims.append(RevalidationClaim(updated, token, key, now + self._timeout))
            db.commit()
        return RevalidationBatch(tuple(claims), tuple(active + [c.warning_id for c in claims]))

    async def run(self, claims: tuple[RevalidationClaim, ...]) -> None:
        gate = admission_pool("pending-audit-files", _MAX_CONCURRENT_FILES)

        async def process(claim: RevalidationClaim) -> None:
            findings: tuple[AuditFinding, ...] = ()
            try:
                # Queue time is part of the lease budget; waiting tasks cannot
                # start a model call after their ownership has expired.
                async with asyncio.timeout(max(0.0, claim.deadline - time())):
                    async with gate.lease():
                        findings = await self._run_one(claim)
            except TimeoutError:
                logger.info("audit_revalidation warning_id=%d outcome=held error_type=TimeoutError", claim.warning_id)
            finally:
                try:
                    await asyncio.to_thread(self._finish, claim, findings)
                except Exception as exc:
                    logger.error("audit_revalidation warning_id=%d outcome=cleanup_error error_type=%s", claim.warning_id, type(exc).__name__)

        # Cancellation drains all worker-owned sessions and HTTP pools before
        # the background task exits.
        tasks = [asyncio.create_task(process(claim)) for claim in claims]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_one(self, claim: RevalidationClaim) -> tuple[AuditFinding, ...]:
        async def revalidate(db: Session) -> tuple[AuditFinding, ...]:
            service = AuditWarningService(db)
            try:
                await service.revalidate(
                    claim.warning_id, revalidation_token=claim.token,
                )
            except (ValueError, ReviewAlreadyProcessedError, TimeoutError) as exc:
                db.rollback()
                logger.info("audit_revalidation warning_id=%d outcome=held error_type=%s", claim.warning_id, type(exc).__name__)
            except Exception as exc:
                db.rollback()
                logger.error("audit_revalidation warning_id=%d outcome=error error_type=%s", claim.warning_id, type(exc).__name__)
            return service.unresolved_audit_findings

        return await self._worker.run(revalidate, deadline=claim.deadline)

    def _finish(self, claim: RevalidationClaim, findings: tuple[AuditFinding, ...] = ()) -> None:
        with self._sessions() as db:
            warning = db.get(AuditWarning, claim.warning_id)
            if warning is None or warning.revalidation_token != claim.token:
                return
            values = {"revalidation_token": None, "revalidation_after": time() + _RETRY_DELAY_SECONDS}
            # A new substantive finding replaces the obsolete display evidence.
            # Further polling leaves that finding for its normal decision flow.
            if warning.status == "pending":
                verdict = decode_audit_record(warning.audit_record, claim.key)
                candidates = list(findings) or (verdict.findings if verdict is not None and verdict.risky else [])
                verified, _ = AuditFindingVerifier(warning.masked_content, warning.file_path).verify(candidates)
                if verified:
                    values.update(reasoning=AuditVerdict(risky=True, findings=verified).reasoning_text(),
                                  failed_check=FailedCheck.LLM_DENETIMI)
            db.execute(update(AuditWarning).where(
                AuditWarning.id == claim.warning_id,
                AuditWarning.revalidation_token == claim.token,
                AuditWarning.status == warning.status,
                AuditWarning.masked_content == warning.masked_content,
                AuditWarning.audit_record == warning.audit_record,
            ).values(**values))
            db.commit()
