"""Ikincil risk (post-mask adversarial denetim) kayitlari icin insan
karar servisi. review_service.py'daki ReviewService ile KASITLI olarak
ayri: buradaki "onayla/reddet" anlami TERSTIR -

  - confirm(): riskin GERCEK oldugunu dogrular -> dosya kasitli olarak
    karantinada KALIR (hedef klasore hic yazilmaz). Bu proje icin export
    tekrarlanana ya da maskeleme kurallari duzeltilene kadar boyle kalir.
  - mask(): dosyada birebir dogrulanan riskli ifadeleri otomatik maskeler,
    DB eslemelerini kaydeder; final kontroller gecerse dosyayi yayinlar.
  - dismiss(): dar kapsamli suppression karari olusturur; sozluk, syntax,
    placeholder/restore ve AI final denetimini yeniden calistirir. Yalnizca
    hepsi gecerse maskelenmis icerigi hedef klasore yazar.

dismiss()/mask() uc asamada calisir:
  A. Aday icerik bir SAVEPOINT icinde hazirlanir (maskeleme, tutarlilik,
     LLM'siz kontroller), savepoint geri alinir ve transaction commit edilir:
     SQLite yazma kilidi LLM cagrisi boyunca TUTULMAZ.
  B. LLM final denetimi aday icerik uzerinde, DB'ye yazmadan calisir.
  C. Kisa bir yazma transaction'inda aday yeniden (deterministik) uretilir,
     durum atomik olarak gecirilir, kararlar kaydedilir, dosya yazilir ve
     commit edilir. Commit basarisiz olursa yazilan dosya ve manifest kaydi
     geri alinir (bkz. _register_release_undo) - "dosya disarida ama DB
     'karantinada' diyor" durumu olusmaz. Basarisiz dogrulama hicbir karari
     (suppression, esleme) kalici yapmaz; yalnizca denetim kaydi yazilir.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Callable, Sequence
from time import time

from sqlalchemy import event, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.crypto import decrypt_value
from app.db.models import AuditLog, AuditWarning, FilterRule, MaskingContext, MaskingRun, ValueMapping
from app.services.consistency_masking import SensitiveValueRegistry, find_consistency_occurrences
from app.core.exceptions import ReviewAlreadyProcessedError
from app.services.integrity_manifest import manifest_lock, read_manifest, write_manifest, file_digest, source_tag
from app.services.mapping_service import load_active_rules, mapping_scope_for_run, mask_relative_path
from app.services.roundtrip_validator import text_digest
from app.services.rule_engine import reverse_text
from app.repository.audit_warning_repository import SqlAlchemyAuditWarningRepository
from app.services.audit_reviewer import (
    AuditFinding, audit_masked_text, audit_record_key, decode_audit_record, encode_audit_record,
)
from app.services.llm_transport import llm_http_scope
from app.services.llm_runtime import llm_file_context
from app.services.runtime_params import build_runtime_params
from app.services.log_refs import file_label, file_ref, log_file_label
from app.services.audit_warning_details import describe_audit_warning
from app.services.file_type import write_text_preserving_encoding
from app.services.learned_decisions import LearnedDecisionPolicy, covered_by_values, remember_decision
from app.services.llm_recognizer import LLMRecognitionError
from app.services.rule_engine import PLACEHOLDER_RE
from app.services.syntax_validator import validate_masked_syntax
from app.services.term_upload import find_leaked_terms




logger = logging.getLogger("uvicorn.error.llm")

_PENDING_RELEASES_KEY = "audit_warning_pending_release_undos"
# Asama B/C arasinda es zamanli yer tutucu tahsisi aday icerigi degistirirse
# en fazla bu kadar kez yeniden denetlenir.
_MAX_CANDIDATE_ATTEMPTS = 3


def _forget_releases(session: Session) -> None:
    session.info.get(_PENDING_RELEASES_KEY, []).clear()


def _undo_uncommitted_releases(session: Session, transaction) -> None:
    # after_commit (yalnizca en distaki commit'te) listeyi zaten bosaltir; en
    # distaki transaction commit'siz biterse (rollback, basarisiz commit,
    # close) serbest birakilan dosyalar ve manifest kayitlari geri alinir.
    if transaction.parent is not None:
        return
    _undo_pending_releases(session)


def _undo_pending_releases(session: Session) -> None:
    pending = session.info.get(_PENDING_RELEASES_KEY)
    while pending:
        pending.pop()()


def _register_release_undo(db: Session, undo: Callable[[], None]) -> None:
    pending = db.info.get(_PENDING_RELEASES_KEY)
    if pending is None:
        pending = db.info[_PENDING_RELEASES_KEY] = []
        event.listen(db, "after_commit", _forget_releases)
        event.listen(db, "after_transaction_end", _undo_uncommitted_releases)
    pending.append(undo)


@dataclass(frozen=True)
class _Candidate:
    """Serbest birakilacak icerigin hazirlanmis hali (asama A/C)."""
    content: str
    consistency_count: int
    error: str | None


class AuditWarningService:
    # Repository'yi DB session'i ile kurar.
    def __init__(self, db: Session) -> None:
        self.db = db
        self.audit_warnings = SqlAlchemyAuditWarningRepository(db)
        # Son _ai_check'te modelden alinan sonucun sifreli kaydi (yeniden kullanimda None).
        self._fresh_audit_record: str | None = None
        self._unresolved_findings: tuple[AuditFinding, ...] = ()

    @property
    def unresolved_audit_findings(self) -> tuple[AuditFinding, ...]:
        return self._unresolved_findings

    # Verilen kimlige ait, henuz karara baglanmamis denetim uyarilarini listeler.
    def list_pending_for_identity(
        self, *, sicil_no: str, project_name: str | None = None, branch_name: str | None = None
    ) -> list[AuditWarning]:
        return self.audit_warnings.list_pending_for_identity(
            project_name=project_name, sicil_no=sicil_no, branch_name=branch_name
        )

    # Bir calismaya ait tum denetim uyarilarini listeler.
    def list_for_run(self, run_id: int) -> list[AuditWarning]:
        return self.audit_warnings.list_for_run(run_id)

    # Riskin gercek oldugunu onaylar - dosya karantinada kalir, hedefe yazilmaz.
    def confirm(self, warning_id: int) -> AuditWarning:
        warning = self.audit_warnings.transition_pending(warning_id, status="confirmed")
        self.db.add(AuditLog(
            run_id=warning.run_id, file_path=warning.file_path, action="skipped",
            detail=(f"warning_id={warning.id} ai_result=risky user_decision=risk_confirmed "
                    "revalidation=not_applicable final_output=blocked"),
        ))
        return warning

    # Bulguyu yanlis alarm sayar; dar suppression ve FINAL PASS basariliysa serbest birakir.
    @llm_http_scope()
    async def dismiss(self, warning_id: int) -> AuditWarning:
        """Learn a narrow suppression and release only after a complete final pass."""
        warning = self._pending_warning(warning_id)
        if warning.audit_failed:
            raise ValueError("Teknik doğrulama hatası kullanıcı kararıyla geçilemez; proje yeniden çalıştırılmalıdır")
        run = self.db.get(MaskingRun, warning.run_id)
        if run is None:
            raise ValueError("İlgili export işlemi bulunamadı")
        # Kanitlarin TAMAMI (ekran siniri yok): bastirilmayan bir alinti final
        # denetimde yeniden bulunur ve dosya hicbir zaman cikamaz.
        suppressions = self._evidence_values(warning)
        return await self._release_with_revalidation(
            warning, run, mask_values=[], suppressions=suppressions,
            failure_message="Dosya yeniden doğrulamadan geçemedi ve çıktıya eklenmedi",
            decision_detail="user_decision=false_alarm",
        )

    @llm_http_scope()
    async def mask(self, warning_id: int) -> AuditWarning:
        """Automatically mask verified audit evidence and release after validation."""
        from app.services.file_classifier import is_lock_filename

        warning = self._pending_warning(warning_id, message="Bu dosya kararı zaten işlenmiş veya bulunamadı.")
        if warning.audit_failed:
            raise ValueError("Teknik doğrulama hatası otomatik düzenlemeyle geçilemez; projeyi yeniden tarayın.")
        if warning.encoding == "java-class-v1" or is_lock_filename(Path(warning.file_path).name):
            raise ValueError("Bu dosya türü inceleme ekranından düzenlenemez; orijinal projeyi yeniden tarayın.")
        if warning.reasoning.startswith("INCELEME_GEREKLI:"):
            raise ValueError("Önce dosyanın bekleyen bulguları için Düzenle işlemini kullanın.")
        run = self.db.get(MaskingRun, warning.run_id)
        if run is None:
            raise ValueError("İlgili maskeleme işlemi bulunamadı.")
        values = [(value, "POST_MASK_AUDIT") for value in self._evidence_values(warning)]
        if not values:
            raise ValueError("Denetim dosyada birebir bulunan bir ifade belirtmedi; otomatik maskeleme yapılamadı.")
        return await self._release_with_revalidation(
            warning, run, mask_values=values, suppressions=[],
            failure_message="Otomatik maskeleme sonrası dosya çıktıya eklenmedi",
            decision_detail="user_decision=automatic_mask",
        )

    @llm_http_scope()
    async def finalize_review_hold(self, warning: AuditWarning) -> None:
        """Apply completed review decisions, then release through the same final pass.

        Uc asama (bkz. modul dokumani). Asama A cagiranin (ReviewService)
        transaction'inda calisir: onaylanan degerler maskelenemezse hata yukari
        tasinir ve inceleme karari da geri alinir. Aksi halde kararlar commit
        edilir; LLM final denetimi yazma kilidi tutulmadan calisir.
        """
        run = self.db.get(MaskingRun, warning.run_id)
        if run is None:
            return
        from app.db.models import ReviewQueue

        warning_id = warning.id
        approved = [
            (review.found_value, review.entity_type)
            for review in self.db.scalars(select(ReviewQueue).where(
                ReviewQueue.run_id == run.id, ReviewQueue.file_path == warning.file_path,
                ReviewQueue.status == "approved", ReviewQueue.found_value.is_not(None),
            )).all()
        ]

        # A
        savepoint = self.db.begin_nested()
        try:
            candidate = self._prepare_candidate(warning, run, approved, raise_mask_errors=True)
        finally:
            savepoint.rollback()
        self.db.commit()

        for _attempt in range(_MAX_CANDIDATE_ATTEMPTS):
            # B
            error = candidate.error
            if error is None:
                error = await self._ai_check(candidate.content, warning, run)
            # C
            savepoint = self.db.begin_nested()
            try:
                again = self._prepare_candidate(warning, run, approved)
                if error is None and again.error is None and again.content != candidate.content:
                    # Es zamanli bir serbest birakma ayni islemde yer tutucu aldi:
                    # yeni icerik yeniden denetlenir.
                    savepoint.rollback()
                    self.db.commit()
                    candidate = again
                    continue
                self._apply_review_outcome(warning_id, run, again, error or again.error)
                savepoint.commit()
            except BaseException:
                savepoint.rollback()
                _undo_pending_releases(self.db)
                raise
            self._commit_or_undo()
            return

        savepoint = self.db.begin_nested()
        try:
            again = self._prepare_candidate(warning, run, approved)
            self._apply_review_outcome(
                warning_id, run, again, "eşlemeler doğrulama sırasında tekrar tekrar değişti; projeyi yeniden tarayın",
            )
            savepoint.commit()
        except BaseException:
            savepoint.rollback()
            raise
        self._commit_or_undo()

    def _apply_review_outcome(self, warning_id: int, run: MaskingRun, candidate: _Candidate, error: str | None) -> None:
        warning = self.db.get(AuditWarning, warning_id)
        self.db.refresh(warning)
        if warning.status != "pending":
            return  # Es zamanli baska bir karar dosyayi zaten sonuclandirdi.
        # Onaylanan maskelemeler dogrulama basarisiz olsa da karantina kopyasina islenir.
        warning.masked_content = candidate.content
        self._log_consistency(warning, run, candidate.consistency_count)
        if error is not None:
            warning.audit_failed = True
            warning.reasoning = f"İnceleme kararları sonrası doğrulama başarısız: {error}"
            if self._fresh_audit_record:
                warning.audit_record = self._fresh_audit_record
            self.db.add(AuditLog(
                run_id=run.id, file_path=warning.file_path, action="error",
                detail=f"review_decisions=complete revalidation=failed final_output=blocked reason={error}",
            ))
            return
        warning = self.audit_warnings.transition_pending(warning_id, status="dismissed")
        self._release_to_target(warning)
        self.db.add(AuditLog(
            run_id=run.id, file_path=warning.file_path, action="skipped",
            detail="review_decisions=complete revalidation=passed final_output=written",
        ))

    def _commit_or_undo(self) -> None:
        # Commit basarisiz olursa after_transaction_end dosyayi ve manifest kaydini geri alir.
        try:
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    # Geriye uyumluluk: LLM'siz kontroller + LLM final denetimi, mevcut icerik uzerinde.
    async def _final_pass(self, warning: AuditWarning, run: MaskingRun) -> str | None:
        error = self._offline_checks(warning.masked_content, warning, run)
        if error is None:
            error = await self._ai_check(warning.masked_content, warning, run)
        return error

    # ------------------------------------------------------------------
    # Uc asamali serbest birakma (bkz. modul dokumani)
    # ------------------------------------------------------------------

    async def revalidate(self, warning_id: int, *, revalidation_token: str) -> AuditWarning:
        """Automatic final pass without learning or suppressing any finding."""
        warning = self._pending_warning(warning_id)
        if (warning.audit_failed or warning.revalidation_token != revalidation_token
                or (warning.revalidation_after or 0) <= time()):
            raise ReviewAlreadyProcessedError("Otomatik doğrulama kaydı başka bir işlem tarafından alındı.")
        run = self.db.get(MaskingRun, warning.run_id)
        if run is None or run.status not in {"completed", "completed_with_warnings"}:
            raise ValueError("Tamamlanmamış export yeniden doğrulanamaz.")
        return await self._release_with_revalidation(
            warning, run, mask_values=[], suppressions=[],
            failure_message="Otomatik doğrulama dosyayı serbest bırakamadı",
            decision_detail="automatic_revalidation=true",
            revalidation_token=revalidation_token,
        )

    async def _release_with_revalidation(
        self, warning: AuditWarning, run: MaskingRun, *,
        mask_values: list[tuple[str, str]], suppressions: list[str],
        failure_message: str, decision_detail: str, revalidation_token: str | None = None,
    ) -> AuditWarning:
        warning_id = warning.id
        release_location = (run.target_path, warning.file_path, warning.output_path, warning.encoding)

        # A: aday icerik; hicbir yazma kalici degil, kilit LLM'den once birakilir.
        savepoint = self.db.begin_nested()
        try:
            candidate = self._prepare_candidate(warning, run, mask_values)
        finally:
            savepoint.rollback()
        self.db.commit()

        # B: LLM final denetimi - DB'ye yazmaz.
        error = candidate.error
        if error is None:
            error = await self._ai_check(candidate.content, warning, run, extra_suppressions=suppressions)
        if error is not None:
            self._record_failure(warning, run, decision_detail, error, revalidation_token=revalidation_token)
            raise ValueError(f"{failure_message}: {error}")

        # C: kisa yazma transaction'i.
        savepoint = self.db.begin_nested()
        try:
            self.db.refresh(warning)
            self.db.refresh(run)
            if (run.target_path, warning.file_path, warning.output_path, warning.encoding) != release_location:
                raise _StaleCandidate("çıktı konumu doğrulama sırasında değişti; işlemi tekrarlayın")
            again = self._prepare_candidate(warning, run, mask_values)
            if again.error is not None or again.content != candidate.content:
                raise _StaleCandidate(again.error or "dosya ya da eşlemeler doğrulama sırasında değişti; işlemi tekrarlayın")
            warning = self.audit_warnings.transition_pending(
                warning_id, status="dismissed", revalidation_token=revalidation_token,
            )
            warning.masked_content = again.content
            self._log_consistency(warning, run, again.consistency_count)
            learned_ids = [
                remember_decision(
                    self.db, context_id=run.context_id, decision_type="suppression",
                    value=value, entity_type="POST_MASK_AUDIT", file_path=warning.file_path,
                    source_review_id=None,
                ).id
                for value in suppressions
            ]
            self._release_to_target(warning)
            suppression_detail = f" suppression_rule_ids={learned_ids}" if suppressions else ""
            self.db.add(AuditLog(
                run_id=run.id, file_path=warning.file_path,
                action="replaced" if mask_values else "skipped",
                detail=(f"warning_id={warning_id} ai_result={'clean' if revalidation_token else 'risky'} {decision_detail}{suppression_detail} "
                        "revalidation=passed final_state=READY final_output=written"),
            ))
            savepoint.commit()
        except _StaleCandidate as exc:
            savepoint.rollback()
            self._record_failure(warning, run, decision_detail, str(exc), revalidation_token=revalidation_token)
            raise ValueError(f"{failure_message}: {exc}") from None
        except BaseException:
            savepoint.rollback()
            # Asama A'da commit edildigi icin bekleyen geri almalarin tamami bu denemeye ait.
            _undo_pending_releases(self.db)
            raise
        self._commit_or_undo()
        return warning

    def _pending_warning(self, warning_id: int, message: str | None = None) -> AuditWarning:
        warning = self.db.get(AuditWarning, warning_id)
        if warning is None or warning.status != "pending":
            raise ReviewAlreadyProcessedError(message or f"denetim_uyarilari id={warning_id} zaten islenmis veya yok")
        return warning

    def _evidence_values(self, warning: AuditWarning) -> list[str]:
        details = describe_audit_warning(warning, self.db, evidence_limit=None)
        values = (str(item.get("found_value", "")).strip() for item in details.get("evidence", []))
        return list(dict.fromkeys(value for value in values if value))

    def _record_failure(
        self, warning: AuditWarning, run: MaskingRun, decision_detail: str, error: str,
        *, revalidation_token: str | None = None,
    ) -> None:
        # Yalnizca denetim kaydi (ve alinan denetim sonucu) kalici olur; karar/esleme
        # yazilmaz, dosya bekler. Ayni icerikle tekrar denemek ayni sonucu alir.
        self.db.refresh(warning)
        if warning.status != "pending" or (revalidation_token is not None and warning.revalidation_token != revalidation_token):
            raise ReviewAlreadyProcessedError("Dosya doğrulama sırasında başka bir işlem tarafından sonuçlandırıldı.")
        stmt = update(AuditWarning).where(
            AuditWarning.id == warning.id, AuditWarning.status == "pending",
        ).values(audit_record=self._fresh_audit_record or warning.audit_record).returning(AuditWarning.id)
        if revalidation_token is not None:
            stmt = stmt.where(
                AuditWarning.revalidation_token == revalidation_token,
                AuditWarning.revalidation_after > time(),
            )
        if self.db.scalar(stmt) is None:
            raise ReviewAlreadyProcessedError("Dosya doğrulama sırasında başka bir işlem tarafından sonuçlandırıldı.")
        self.db.add(AuditLog(
            run_id=run.id, file_path=warning.file_path, action="error",
            detail=f"warning_id={warning.id} {decision_detail} revalidation=failed final_output=blocked reason={error}",
        ))
        self.db.commit()

    def _prepare_candidate(
        self, warning: AuditWarning, run: MaskingRun, mask_values: list[tuple[str, str]],
        *, content: str | None = None, raise_mask_errors: bool = False,
    ) -> _Candidate:
        from app.services.review_masking import mask_review_values

        content = warning.masked_content if content is None else content
        if mask_values:
            try:
                content = mask_review_values(self.db, run, content, warning.file_path, mask_values)
            except ValueError as exc:
                if raise_mask_errors:
                    raise
                return _Candidate(content, 0, str(exc))
        content, count, error = self._with_run_consistency(content, warning, run)
        if error is None:
            error = self._offline_checks(content, warning, run)
        return _Candidate(content, count, error)

    def _offline_checks(self, content: str, warning: AuditWarning, run: MaskingRun) -> str | None:
        if warning.encoding == "java-class-v1":
            return "Java class dosyası için orijinal projeyi yeniden tarayın; metin önizlemesinden bytecode oluşturulamaz"
        if not run.target_path:
            return "export hedef klasörü bulunamadı"
        target = Path(run.target_path).resolve()
        try:
            output_rel = self._output_relative_path(warning, run)
        except ValueError as exc:
            return str(exc)
        destination = (target / output_rel).resolve()
        try:
            destination.relative_to(target)
        except ValueError:
            return "karantina dosya yolu hedef klasörün dışına çıkıyor"
        if find_consistency_occurrences(content, self._run_registry(run), file_path=warning.file_path):
            return "tutarlılık kontrolünde bu işlemde maskelenen bir değer dosyada açık kaldı"
        if find_leaked_terms(self.db, content):
            return "kurumsal terim son kontrolünde açık değer kaldı"

        reverse_map = self._run_reverse_map(run)
        unresolved = sorted({m.group(0) for m in PLACEHOLDER_RE.finditer(content)} - reverse_map.keys())
        if unresolved:
            return f"geri dönüş doğrulaması başarısız; {len(unresolved)} yer tutucu çözülemiyor"

        # Export'taki gibi orijinalle karsilastirilir: kaynakta zaten bulunan
        # bir sozdizimi hatasi (orn. yorumlu JSON) maskelemenin hatasi sayilmaz.
        original_text = reverse_text(content, reverse_map)[0]
        syntax_error = validate_masked_syntax(warning.file_path, content, original_text=original_text)
        if syntax_error and settings.validation.syntax_failure_action == "warn":
            # Gizlilik kontrolleri (tutarlilik, acik terim) gecti; denetim asagida
            # yine calisir. Hata yalnizca uyari olarak kaydedilir.
            self.db.add(AuditLog(
                run_id=run.id, file_path=warning.file_path, action="skipped",
                detail=f"validation_warning; syntax_failure_action=warn; {syntax_error}",
            ))
        elif syntax_error:
            return f"sözdizimi doğrulaması başarısız: {syntax_error}"
        return None

    async def _ai_check(
        self, content: str, warning: AuditWarning, run: MaskingRun, *, extra_suppressions: Sequence[str] = (),
    ) -> str | None:
        self._fresh_audit_record = None
        self._unresolved_findings = ()
        verdict = None
        key = None
        if settings.vllm.enabled:
            # Ayni icerik, ayni karar: bu icerik ayni ayarlarla zaten denetlendiyse
            # model yeniden orneklenmez (bkz. audit_reviewer.audit_record_key).
            key = audit_record_key(content, warning.file_path, settings.vllm)
            verdict = decode_audit_record(warning.audit_record, key)
            if verdict is not None:
                logger.info("llm_audit_record_reused warning_id=%s findings=%d", warning.id, len(verdict.findings))
        if verdict is None:
            try:
                label = file_label(warning.output_path, file_ref(run.context_id, run.id, warning.file_path))
                with llm_file_context(warning.file_path), log_file_label(label):
                    verdict = await audit_masked_text(content, settings.vllm)
            except LLMRecognitionError as exc:
                return f"AI güvenlik doğrulaması tamamlanamadı: {exc}"
            if key is not None:
                self._fresh_audit_record = encode_audit_record(verdict, key)
        if verdict.risky and not verdict.findings:
            return "final AI denetimi risk bildirdi ancak doğrulanabilir ifade belirtmedi"

        policy = LearnedDecisionPolicy.load(self.db, run.context_id)
        extra = list(extra_suppressions)
        remaining = [
            finding for finding in (verdict.findings if verdict.risky else [])
            if policy.suppression_covering(finding.ilgili_bolum, warning.file_path, content) is None
            and not covered_by_values(finding.ilgili_bolum, content, extra)
        ]
        if remaining:
            self._unresolved_findings = tuple(remaining)
            return f"final AI denetimi {len(remaining)} bastırılmamış risk buldu"
        return None

    def _run_mapping_rows(self, run: MaskingRun) -> list[ValueMapping]:
        return list(self.db.scalars(select(ValueMapping).where(
            ValueMapping.context_id == run.context_id,
            ValueMapping.run_id == mapping_scope_for_run(self.db, run.id),
        )).all())

    def _run_reverse_map(self, run: MaskingRun) -> dict[str, str]:
        return {
            row.placeholder_value: row.original_value_plain or decrypt_value(row.original_value_encrypted)
            for row in self._run_mapping_rows(run)
        }

    def _run_registry(self, run: MaskingRun) -> SensitiveValueRegistry:
        """Export'un tutarlilik registry'sinin DB'deki eslemelerden kurulmus hali."""
        rule_categories = dict(self.db.execute(select(FilterRule.id, FilterRule.category)).all())
        registry = SensitiveValueRegistry()
        # Yalnizca kural kimligi olan (deterministik) eslemeler: LLM/NER
        # eslemelerinin kaynagi ve guveni DB'de tutulmadigi icin projeye
        # yayilacak otorite sayilmaz (bkz. consistency_masking kalite kapisi).
        registry.add_mapping_values([
            (row.original_value_plain or decrypt_value(row.original_value_encrypted),
             rule_categories[row.rule_id])
            for row in self._run_mapping_rows(run)
            if row.rule_id is not None and row.rule_id in rule_categories
        ])
        return registry

    def _with_run_consistency(
        self, content: str, warning: AuditWarning, run: MaskingRun,
    ) -> tuple[str, int, str | None]:
        """Bu islemde baska dosyalarda maskelenmis ama bu dosyada acik kalmis
        degerleri, export'taki tutarlilik gecisiyle ayni esleme sozlesmesiyle maskeler."""
        occurrences = find_consistency_occurrences(content, self._run_registry(run), file_path=warning.file_path)
        if not occurrences:
            return content, 0, None
        from app.services.review_masking import mask_review_values
        try:
            content = mask_review_values(
                self.db, run, content, warning.file_path,
                [(item.original_value, item.entry.entity_type) for item in occurrences],
            )
        except ValueError as exc:
            return content, 0, f"tutarlılık maskelemesi uygulanamadı: {exc}"
        self.db.flush()
        return content, len(occurrences), None

    def _log_consistency(self, warning: AuditWarning, run: MaskingRun, count: int) -> None:
        if count:
            self.db.add(AuditLog(
                run_id=run.id, file_path=warning.file_path, action="replaced",
                detail=f"source=consistency_on_release occurrences={count}",
            ))

    def _output_relative_path(self, warning: AuditWarning, run: MaskingRun) -> Path:
        """Serbest birakilan dosyanin ciktidaki MASKELI goreli yolu.

        warning.file_path kaynak yoludur (proje/kurum adini acik icerebilir);
        ciktiya asla bu yolla yazilmaz. Eski kayitlarda output_path yoksa yol,
        export'taki ayni yol maskeleyicisi ve ayni islem eslemeleriyle yeniden
        hesaplanir.
        """
        if warning.output_path:
            return Path(warning.output_path)
        context = self.db.get(MaskingContext, run.context_id)
        if context is None:
            raise ValueError("İşlem bağlamı bulunamadı; dosya çıktıya yazılamaz")
        runtime_params = build_runtime_params(context.project_name, context.sicil_no, context.branch_name)
        masked, _ = mask_relative_path(
            self.db, context, Path(warning.file_path), runtime_params,
            load_active_rules(self.db), run_id=run.id,
        )
        return masked

    # Karantinadaki maskelenmis icerigi run'in hedef klasorune, MASKELI yola yazar
    # ve imzali butunluk kaydina ekler. Transaction commit edilmezse ikisi de geri alinir.
    def _release_to_target(self, warning: AuditWarning) -> None:
        if warning.encoding == "java-class-v1":
            raise ValueError("Java class dosyası orijinal projeden yeniden taranmalıdır")
        run = self.db.get(MaskingRun, warning.run_id)
        if run is None or not run.target_path:
            raise ValueError("Export hedef klasörü bulunamadı; dosya çıktıya yazılamaz")
        target = Path(run.target_path).resolve()
        output_rel = self._output_relative_path(warning, run)
        dest_path = (target / output_rel).resolve()
        try:
            dest_path.relative_to(target)
        except ValueError as exc:
            raise ValueError("Karantina dosya yolu hedef klasörün dışına çıkıyor") from exc
        previous = dest_path.read_bytes() if dest_path.is_file() else None
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        def undo_file() -> None:
            try:
                if previous is None:
                    dest_path.unlink(missing_ok=True)
                else:
                    dest_path.write_bytes(previous)
            except OSError:
                pass

        _register_release_undo(self.db, undo_file)
        write_text_preserving_encoding(dest_path, warning.masked_content, warning.encoding)
        self._add_to_manifest(run, target, dest_path, warning)

    def _add_to_manifest(self, run: MaskingRun, target: Path, dest_path: Path, warning: AuditWarning) -> None:
        original_text, _resolved, _unresolved = reverse_text(warning.masked_content, self._run_reverse_map(run))
        mode = 0o644
        if run.source_path:
            source_file = Path(run.source_path) / warning.file_path
            try:
                mode = source_file.stat().st_mode & 0o777
            except OSError:
                pass  # Yukleme modunda gecici kaynak klasor silinmis olabilir.
        try:
            dest_path.chmod(mode)
        except OSError:
            pass
        key = dest_path.relative_to(target).as_posix()
        entry = {
            "encoding": warning.encoding or "utf-8",
            "masked_sha256": file_digest(dest_path),
            "source_tag": source_tag(run.context_id, text_digest(original_text)),
            "mode": mode,
        }
        # Oku-degistir-yaz kilit altinda: es zamanli iki serbest birakma
        # birbirinin kaydini silmez.
        with manifest_lock(target):
            manifest = read_manifest(target, run.context_id)
            if manifest is None:
                return  # Eski/manifestsiz cikti: unmask zaten "kanit yok" yolunu kullanir.
            previous_entry = manifest["files"].get(key)
            files = dict(manifest["files"])
            files[key] = entry
            complete = run.files_scanned is not None and len(files) >= run.files_scanned
            write_manifest(target, run.context_id, files, complete=complete, job_id=manifest.get("job_id"))

        def undo_manifest() -> None:
            # Anlik goruntu geri yuklenmez: arada baska bir serbest birakma
            # eklenmis olabilir. Yalnizca bu kayit geri alinir.
            try:
                with manifest_lock(target):
                    current = read_manifest(target, run.context_id)
                    if current is None or current["files"].get(key) != entry:
                        return
                    files = dict(current["files"])
                    if previous_entry is None:
                        files.pop(key)
                    else:
                        files[key] = previous_entry
                    complete = run.files_scanned is not None and len(files) >= run.files_scanned
                    write_manifest(target, run.context_id, files, complete=complete, job_id=current.get("job_id"))
            except (OSError, ValueError):
                pass

        _register_release_undo(self.db, undo_manifest)


class _StaleCandidate(Exception):
    """Asama C'de uretilen icerik, LLM'in denetledigi adayla ayni degil."""
