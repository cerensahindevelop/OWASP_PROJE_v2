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
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.crypto import decrypt_value
from app.db.models import AuditLog, AuditWarning, FilterRule, MaskingContext, MaskingRun, ValueMapping
from app.services.consistency_masking import SensitiveValueRegistry, find_consistency_occurrences
from app.services.integrity_manifest import read_manifest, write_manifest, file_digest, source_tag
from app.services.mapping_service import load_active_rules, mapping_scope_for_run, mask_relative_path
from app.services.roundtrip_validator import text_digest
from app.services.rule_engine import reverse_text
from app.repository.audit_warning_repository import SqlAlchemyAuditWarningRepository
from app.services.audit_reviewer import AuditVerdict, audit_masked_text
from app.services.llm_runtime import llm_file_context
from app.services.audit_warning_details import describe_audit_warning
from app.services.file_type import write_text_preserving_encoding
from app.services.learned_decisions import LearnedDecisionPolicy, remember_decision
from app.services.llm_recognizer import LLMRecognitionError
from app.services.rule_engine import PLACEHOLDER_RE
from app.services.syntax_validator import validate_masked_syntax
from app.services.term_upload import find_leaked_terms


class AuditWarningService:
    # Repository'yi DB session'i ile kurar.
    def __init__(self, db: Session) -> None:
        self.db = db
        self.audit_warnings = SqlAlchemyAuditWarningRepository(db)

    # Verilen kimlige ait, henuz karara baglanmamis denetim uyarilarini listeler.
    def list_pending_for_identity(
        self, *, project_name: str, sicil_no: str, branch_name: str
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
    async def dismiss(self, warning_id: int) -> AuditWarning:
        """Learn a narrow suppression and release only after a complete final pass."""
        warning = self.db.get(AuditWarning, warning_id)
        if warning is None or warning.status != "pending":
            from app.core.exceptions import ReviewAlreadyProcessedError
            raise ReviewAlreadyProcessedError(f"denetim_uyarilari id={warning_id} zaten islenmis veya yok")
        if warning.audit_failed:
            raise ValueError("Teknik doğrulama hatası kullanıcı kararıyla geçilemez; proje yeniden çalıştırılmalıdır")
        run = self.db.get(MaskingRun, warning.run_id)
        if run is None:
            raise ValueError("İlgili export işlemi bulunamadı")

        details = describe_audit_warning(warning, self.db)
        learned_ids: list[int] = []
        for evidence in details.get("evidence", []):
            value = str(evidence.get("found_value", "")).strip()
            if not value:
                continue
            learned = remember_decision(
                self.db, context_id=run.context_id, decision_type="suppression",
                value=value, entity_type="POST_MASK_AUDIT", file_path=warning.file_path,
                source_review_id=None,
            )
            learned_ids.append(learned.id)
        self.db.add(AuditLog(
            run_id=run.id, file_path=warning.file_path, action="skipped",
            detail=(f"warning_id={warning.id} ai_result=risky user_decision=false_alarm "
                    f"suppression_rule_ids={learned_ids or 'none'} revalidation=started"),
        ))

        error = self._apply_run_consistency(warning, run)
        if error is None:
            error = await self._final_pass(warning, run)
        if error is not None:
            self.db.add(AuditLog(
                run_id=run.id, file_path=warning.file_path, action="error",
                detail=f"revalidation=failed final_output=blocked reason={error}",
            ))
            self.db.commit()
            raise ValueError(f"Dosya yeniden doğrulamadan geçemedi ve çıktıya eklenmedi: {error}")

        warning = self.audit_warnings.transition_pending(warning_id, status="dismissed")
        self._release_to_target(warning)
        self.db.add(AuditLog(
            run_id=run.id, file_path=warning.file_path, action="skipped",
            detail="revalidation=passed final_output=written",
        ))
        return warning

    async def mask(self, warning_id: int) -> AuditWarning:
        """Automatically mask verified audit evidence and release after validation."""
        from app.core.exceptions import ReviewAlreadyProcessedError
        from app.services.file_classifier import is_lock_filename
        from app.services.review_masking import mask_review_values

        warning = self.db.get(AuditWarning, warning_id)
        if warning is None or warning.status != "pending":
            raise ReviewAlreadyProcessedError("Bu dosya kararı zaten işlenmiş veya bulunamadı.")
        if warning.audit_failed:
            raise ValueError("Teknik doğrulama hatası otomatik düzenlemeyle geçilemez; projeyi yeniden tarayın.")
        if warning.encoding == "java-class-v1" or is_lock_filename(Path(warning.file_path).name):
            raise ValueError("Bu dosya türü inceleme ekranından düzenlenemez; orijinal projeyi yeniden tarayın.")
        if warning.reasoning.startswith("INCELEME_GEREKLI:"):
            raise ValueError("Önce dosyanın bekleyen bulguları için Düzenle işlemini kullanın.")
        run = self.db.get(MaskingRun, warning.run_id)
        if run is None:
            raise ValueError("İlgili maskeleme işlemi bulunamadı.")
        details = describe_audit_warning(warning, self.db, evidence_limit=None)
        values = [(item["found_value"], "POST_MASK_AUDIT") for item in details["evidence"]
                  if item.get("found_value")]
        if not values:
            raise ValueError("Denetim dosyada birebir bulunan bir ifade belirtmedi; otomatik maskeleme yapılamadı.")
        with self.db.begin_nested():
            warning.masked_content = mask_review_values(
                self.db, run, warning.masked_content, warning.file_path, values,
            )
            self.db.flush()
            error = self._apply_run_consistency(warning, run)
            if error is None:
                error = await self._final_pass(warning, run)
            if error is not None:
                raise ValueError(f"Otomatik maskeleme sonrası dosya çıktıya eklenmedi: {error}")
            warning = self.audit_warnings.transition_pending(warning_id, status="dismissed")
            self._release_to_target(warning)
            self.db.add(AuditLog(
                run_id=run.id, file_path=warning.file_path, action="replaced",
                detail="user_decision=automatic_mask revalidation=passed final_state=READY final_output=written",
            ))
        return warning

    async def _final_pass(self, warning: AuditWarning, run: MaskingRun) -> str | None:
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
        if find_consistency_occurrences(
            warning.masked_content, self._run_registry(run), file_path=warning.file_path
        ):
            return "tutarlılık kontrolünde bu işlemde maskelenen bir değer dosyada açık kaldı"
        if find_leaked_terms(self.db, warning.masked_content):
            return "kurumsal terim son kontrolünde açık değer kaldı"
        syntax_error = validate_masked_syntax(
            warning.file_path, warning.masked_content, original_text=None,
            sql_dialect=settings.validation.sql_dialect,
        )
        if syntax_error:
            return f"sözdizimi doğrulaması başarısız: {syntax_error}"

        known = set(self.db.scalars(select(ValueMapping.placeholder_value).where(
            ValueMapping.context_id == run.context_id,
            ValueMapping.run_id == mapping_scope_for_run(self.db, run.id),
        )).all())
        unresolved = sorted({m.group(0) for m in PLACEHOLDER_RE.finditer(warning.masked_content)} - known)
        if unresolved:
            return f"geri dönüş doğrulaması başarısız; {len(unresolved)} yer tutucu çözülemiyor"

        try:
            with llm_file_context(warning.file_path):
                verdict = await audit_masked_text(warning.masked_content, settings.vllm)
        except LLMRecognitionError as exc:
            return f"AI güvenlik doğrulaması tamamlanamadı: {exc}"

        policy = LearnedDecisionPolicy.load(self.db, run.context_id)
        remaining = []
        for finding in verdict.findings if verdict.risky else []:
            if policy.suppression_for_value(finding.ilgili_bolum, warning.file_path) is None:
                remaining.append(finding)
        if verdict.risky and not verdict.findings:
            return "final AI denetimi risk bildirdi ancak doğrulanabilir ifade belirtmedi"
        if verdict.risky and remaining:
            return f"final AI denetimi {len(remaining)} bastırılmamış risk buldu"
        return None

    async def finalize_review_hold(self, warning: AuditWarning) -> None:
        """Apply completed review decisions, then release through the same final pass."""
        run = self.db.get(MaskingRun, warning.run_id)
        if run is None:
            return
        from app.db.models import ReviewQueue

        approved = self.db.scalars(select(ReviewQueue).where(
            ReviewQueue.run_id == run.id, ReviewQueue.file_path == warning.file_path,
            ReviewQueue.status == "approved", ReviewQueue.found_value.is_not(None),
        )).all()
        if approved:
            from app.services.review_masking import mask_review_values
            warning.masked_content = mask_review_values(
                self.db, run, warning.masked_content, warning.file_path,
                [(review.found_value, review.entity_type) for review in approved],
            )

        error = self._apply_run_consistency(warning, run)
        if error is None:
            error = await self._final_pass(warning, run)
        if error is not None:
            warning.audit_failed = True
            warning.reasoning = f"İnceleme kararları sonrası doğrulama başarısız: {error}"
            self.db.add(AuditLog(
                run_id=run.id, file_path=warning.file_path, action="error",
                detail=f"review_decisions=complete revalidation=failed final_output=blocked reason={error}",
            ))
            return
        warning.status = "dismissed"
        self._release_to_target(warning)
        self.db.add(AuditLog(
            run_id=run.id, file_path=warning.file_path, action="skipped",
            detail="review_decisions=complete revalidation=passed final_output=written",
        ))

    def _run_mapping_rows(self, run: MaskingRun) -> list[ValueMapping]:
        return list(self.db.scalars(select(ValueMapping).where(
            ValueMapping.context_id == run.context_id,
            ValueMapping.run_id == mapping_scope_for_run(self.db, run.id),
        )).all())

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

    def _apply_run_consistency(self, warning: AuditWarning, run: MaskingRun) -> str | None:
        """Bu islemde baska dosyalarda maskelenmis ama bu dosyada acik kalmis
        degerleri, export'taki tutarlilik gecisiyle ayni esleme sozlesmesiyle maskeler."""
        occurrences = find_consistency_occurrences(
            warning.masked_content, self._run_registry(run), file_path=warning.file_path
        )
        if not occurrences:
            return None
        from app.services.review_masking import mask_review_values
        try:
            warning.masked_content = mask_review_values(
                self.db, run, warning.masked_content, warning.file_path,
                [(item.original_value, item.entry.entity_type) for item in occurrences],
            )
        except ValueError as exc:
            return f"tutarlılık maskelemesi uygulanamadı: {exc}"
        self.db.add(AuditLog(
            run_id=run.id, file_path=warning.file_path, action="replaced",
            detail=f"source=consistency_on_release occurrences={len(occurrences)}",
        ))
        self.db.flush()
        return None

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
        runtime_params = {
            "project_name": context.project_name,
            "sicil_no": context.sicil_no,
            "branch_name": context.branch_name,
        }
        masked, _ = mask_relative_path(
            self.db, context, Path(warning.file_path), runtime_params,
            load_active_rules(self.db), run_id=run.id,
        )
        return masked

    # Karantinadaki maskelenmis icerigi run'in hedef klasorune, MASKELI yola yazar
    # ve imzali butunluk kaydina ekler.
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
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        write_text_preserving_encoding(dest_path, warning.masked_content, warning.encoding)
        self._add_to_manifest(run, target, dest_path, warning)

    def _add_to_manifest(self, run: MaskingRun, target: Path, dest_path: Path, warning: AuditWarning) -> None:
        manifest = read_manifest(target, run.context_id)
        if manifest is None:
            return  # Eski/manifestsiz cikti: unmask zaten "kanit yok" yolunu kullanir.
        reverse_map = {
            row.placeholder_value: row.original_value_plain or decrypt_value(row.original_value_encrypted)
            for row in self._run_mapping_rows(run)
        }
        original_text, _resolved, _unresolved = reverse_text(warning.masked_content, reverse_map)
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
        files = dict(manifest["files"])
        files[dest_path.relative_to(target).as_posix()] = {
            "encoding": warning.encoding or "utf-8",
            "masked_sha256": file_digest(dest_path),
            "source_tag": source_tag(run.context_id, text_digest(original_text)),
            "mode": mode,
        }
        complete = run.files_scanned is not None and len(files) >= run.files_scanned
        write_manifest(target, run.context_id, files, complete=complete, job_id=manifest.get("job_id"))
