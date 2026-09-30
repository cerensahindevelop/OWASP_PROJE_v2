"""Main export/mask pipeline: walks a project folder, masks every text
file's sensitive content through the FAZ 1 rule engine, and writes the
result to a target folder. The original project folder is only ever read,
never modified.

Re-export policy (confirmed with product owner): exporting again with the
same (project_name, sicil_no, branch_name) OVERWRITES the target
directory's previous contents. Every export is a new job (run): placeholder
counters and value mappings are scoped to that job (mapping_version=2), so
the same value gets the same placeholder in every file of ONE export, but a
new export may assign different placeholders. The job id is recorded in the
signed integrity manifest and selects the mappings on unmask.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, field, replace
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

# sqlalchemy: DB sorgulari, eszamanli export'lari yakalamak icin
# IntegrityError ve run/context islemleri icin Session.
from sqlalchemy import select, text as sql_text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

# Asagidaki app.* importlari, export pipeline'inin butun alt katmanlarini
# (ayarlar/sifreleme/DB modelleri, dosya siniflandirma/okuma/tur tespiti,
# detection orchestrator, denetim/round-trip/sozdizimi dogrulama, tarayici
# ve mapping servisi) tek bir akista birbirine baglar.
from app.core.config import settings
from app.core.crypto import decrypt_value
from app.core.exceptions import ExportInProgressError
from app.db.models import AuditLog, AuditWarning, MaskingContext, MaskingRun, ValueMapping
from app.services.audit_reviewer import AuditVerdict, audit_masked_text
from app.services.consistency_masking import (
    SensitiveValueRegistry,
    apply_consistency_replacements,
    find_consistency_occurrences,
)
from app.services.exclude_admin import load_active_exclude_specs
from app.services.export_report_formatter import _RULE_DISPLAY_NAMES, format_export_report
from app.services.file_classifier import ignored_directory_reason, is_opaque_binary_filename
from app.services.file_pipeline import ReadStatus, describe_file_error, read_scanned_file
from app.services.file_type import peek_classify, write_text_preserving_encoding
from app.services.java_classfile import JAVA_CLASS_ENCODING, CLASS_COVERAGE, ClassFormatError, parse_class
from app.services.detectors import DetectionOrchestrator, synthetic_llm_rule
from app.services.llm_recognizer import LLMRecognitionError
from app.services.llm_runtime import llm_file_context
from app.services.learned_decisions import LearnedDecisionPolicy
from app.services.lockfile_policy import plan_lockfile, structured_format
from app.services.roundtrip_validator import text_digest, verify_round_trip, verify_round_trip_digest
from app.services.rule_engine import JSON_NUMERIC_PLACEHOLDER_RE, PLACEHOLDER_RE, Match, reverse_text
from app.services.output_publication import OutputPublication, publish_run
from app.services.integrity_manifest import MANIFEST_NAME, write_manifest, file_digest, source_tag
from app.services.path_placeholders import PathPlaceholderResolver, UnsafeUnmaskPathError
from app.services.term_upload import find_leaked_terms
from app.services.mapping_service import (
    DetectionOutcome,
    MappingCache,
    MaskingRunContext,
    apply_detections,
    build_orchestrator,
    detect_matches,
    get_or_create_mapping,
    get_or_create_context,
    load_active_presidio_rules,
    load_active_rules,
    load_file_category_restrictions,
    mapping_scope_for_run,
    mask_relative_path,
)
from app.services.review_masking import NarrowedValueError, mask_known_values
from app.services.scanner import iter_project_files
from app.services.syntax_validator import validate_masked_syntax

DEFAULT_MAX_INLINE_SIZE = 50 * 1024 * 1024  # 50MB, per product owner's own example
DEFAULT_ENABLE_PATH_MASKING = True

_REPORT_BUCKETS = {
    "masked": "files_masked",
    "copied_text_no_match": "files_copied_text_no_match",
    "copied_binary": "files_copied_binary",
    "skipped_unsupported": "files_skipped_unsupported",
    "copied_undecodable": "files_copied_undecodable",
    "skipped_symlink": "files_skipped_symlink",
    "skipped_too_large": "files_skipped_too_large",
    "excluded": "files_excluded",
    "error": "files_errored",
    "quarantined_pending_audit": "files_quarantined_pending_audit",
    "failed_syntax_validation": "files_failed_syntax_validation",
    "failed_round_trip_validation": "files_failed_round_trip_validation",
    "failed_consistency_validation": "files_failed_consistency_validation",
    "failed_finalization": "files_failed_finalization",
    # ARCHIVE: proje ici arsiv, hic acilmadan quarantine/review'e gonderildi.
    "archive_unsupported": "files_archive_unsupported",
    # SCAN_ONLY (bagimlilik lock/integrity dosyalari): tarandi, bulgu
    # yoktu, byte-identical kopyalandi - "masked"den KASITLI olarak ayri.
    "scan_only_clean": "files_scan_only_clean",
    # SCAN_ONLY: tarandi, hassas olabilecek bulgu vardi - butunlugu
    # bozmamak icin maskelenmedi, ciktiya alinmadi, quarantine'e gonderildi.
    "scan_only_sensitive": "files_scan_only_sensitive",
    # SCAN_ONLY: tespit katmani guvenilir bir sonuc uretemedi (crash/LLM
    # hatasi) - "taranamadi != temiz", bu da quarantine gerektirir.
    "failed_detection": "files_failed_detection",
}


# Export oncesi yol/guvenlik kontrolleri basarisiz oldugunda firlatilan hata
# (orn. hedef kaynagin icinde, ya da kaynak hic yok).
class ExportValidationError(ValueError):
    pass


_PROTECTED_TARGETS = {Path("/"), Path.home()}


# Tek bir dosyanin export sirasinda basina ne geldigini (maskelendi mi,
# oldugu gibi mi kopyalandi, hata mi aldi) tutan sonuc kaydi.
@dataclass
class FileOutcome:
    relative_path: str
    status: str
    match_count: int = 0
    rule_breakdown: dict[str, int] = field(default_factory=dict)
    error: str | None = None
    final_state: str | None = None
    # Dosyayi bloklayan son kontrolun kisa kodu (orn. "acik_terim",
    # "sozdizimi"); otomatik duzeltme basarisizliginin gerekcesi icin.
    failed_check: str | None = None

    def __post_init__(self) -> None:
        if self.final_state is not None:
            return
        if self.status in {"masked", "copied_text_no_match", "scan_only_clean"}:
            self.final_state = "READY"
        elif self.status in {"excluded", "skipped_symlink", "skipped_unsupported"}:
            self.final_state = "SKIPPED"
        elif self.status in {"quarantined_pending_audit", "archive_unsupported", "scan_only_sensitive"}:
            self.final_state = "SECURITY_QUARANTINE"
        else:
            self.final_state = "VALIDATION_FAILED"


# Bir export (maskeleme) calismasinin tum ozetini tutan rapor nesnesi -
# kac dosya tarandi, kac tanesi maskelendi, kural bazinda dagilim vb.
@dataclass
class ExportReport:
    run_id: int
    context_id: int
    project_name: str
    sicil_no: str
    branch_name: str
    source_path: str
    target_path: str
    started_at: datetime
    completed_at: datetime | None = None
    target_overwritten: bool = False
    files_scanned: int = 0
    files_masked: int = 0
    files_copied_text_no_match: int = 0
    files_copied_binary: int = 0
    files_skipped_unsupported: int = 0
    files_copied_undecodable: int = 0
    files_skipped_symlink: int = 0
    files_skipped_too_large: int = 0
    files_excluded: int = 0
    files_errored: int = 0
    files_quarantined_pending_audit: int = 0
    files_failed_syntax_validation: int = 0
    files_failed_round_trip_validation: int = 0
    files_failed_consistency_validation: int = 0
    # bkz. mark_finalization_failed dokstringi: dosya butun dogrulamalardan
    # (syntax/round-trip/consistency/audit) BASARIYLA gecmisken, en SONDAKI
    # chmod/manifest-kaydi adiminda (izin/dijital-imza metaverisi yazimi)
    # beklenmeyen bir hata olustugunda kullanilir.
    files_failed_finalization: int = 0
    # ARCHIVE / SCAN_ONLY (bkz. _REPORT_BUCKETS): "taranamadi != temiz"
    # ilkesini rapor duzeyinde de gorunur tutan ayri sayaclar.
    files_archive_unsupported: int = 0
    files_scan_only_clean: int = 0
    files_scan_only_sensitive: int = 0
    files_failed_detection: int = 0
    files_ready: int = 0
    files_review_required: int = 0
    files_security_quarantine: int = 0
    files_validation_failed: int = 0
    total_matches: int = 0
    matches_by_rule: dict[str, int] = field(default_factory=dict)
    outcomes: list[FileOutcome] = field(default_factory=list)
    status: str = "in_progress"
    # Bu calisma boyunca dusuk-kapasiteli (fallback) modda calisan detector
    # katmanlarinin adlari (bkz. app/services/presidio_detector.py
    # PresidioDetector.is_degraded) - bos degilse, tespit kapsaminin bu
    # calisma icin EKSIK olabilecegi anlamina gelir; "basarili" statusu
    # bunu sessizce gizlememeli (bkz. has_degraded_detectors).
    degraded_detectors: list[str] = field(default_factory=list)
    validation_warnings: list[str] = field(default_factory=list)
    # True ise Katman 3 (LLM) bu calisma icin baslangictan itibaren BILEREK
    # kapaliydi (VLLM_ENABLED=false) - bir kurulum HATASI degil, operatorun
    # ayarladigi bir konfigurasyon. Bu yuzden degraded_detectors'tan (ve
    # final_status'u "completed_with_warnings"a cevirmekten) BILEREK AYRI
    # tutulur - aksi halde LLM'i hic kullanmayan her kurulumda HER export
    # sonsuza dek "uyarili" gorunur ve gercek uyarilar bu gurultude kaybolur.
    # Yine de kullaniciya HER ZAMAN acikca gosterilir (bkz.
    # export_report_formatter.py) - "Katman 3 hic calismadi" bilgisi
    # sessizce kaybolmamali, sadece basari/basarisizlik hukmunu degistirmemeli.
    llm_disabled: bool = False

    # Tek bir dosyanin sonucunu (FileOutcome) rapor toplamlarina isler.
    def record(self, outcome: FileOutcome) -> None:
        self.files_scanned += 1
        self.outcomes.append(outcome)
        self.total_matches += outcome.match_count
        for rule_name, count in outcome.rule_breakdown.items():
            self.matches_by_rule[rule_name] = self.matches_by_rule.get(rule_name, 0) + count

        bucket = _REPORT_BUCKETS[outcome.status]
        setattr(self, bucket, getattr(self, bucket) + 1)
        state_bucket = {
            "READY": "files_ready",
            "REVIEW_REQUIRED": "files_review_required",
            "SECURITY_QUARANTINE": "files_security_quarantine",
            "VALIDATION_FAILED": "files_validation_failed",
        }.get(outcome.final_state)
        if state_bucket:
            setattr(self, state_bucket, getattr(self, state_bucket) + 1)

    def add_consistency_matches(self, outcome: FileOutcome, rule_breakdown: dict[str, int]) -> None:
        """Merge second-pass matches without counting the file twice."""
        added = sum(rule_breakdown.values())
        if not added:
            return
        if outcome.status == "copied_text_no_match":
            self.files_copied_text_no_match -= 1
            self.files_masked += 1
            outcome.status = "masked"
        outcome.match_count += added
        self.total_matches += added
        for rule_name, count in rule_breakdown.items():
            outcome.rule_breakdown[rule_name] = outcome.rule_breakdown.get(rule_name, 0) + count
            self.matches_by_rule[rule_name] = self.matches_by_rule.get(rule_name, 0) + count

    def mark_consistency_failed(self, outcome: FileOutcome, error: str) -> None:
        """Remove a leaking/unverifiable file from successful output counts."""
        if outcome.status == "failed_consistency_validation":
            return
        old_bucket = _REPORT_BUCKETS[outcome.status]
        setattr(self, old_bucket, getattr(self, old_bucket) - 1)
        if outcome.final_state == "READY":
            self.files_ready -= 1
        elif outcome.final_state == "SECURITY_QUARANTINE":
            self.files_security_quarantine -= 1
        elif outcome.final_state == "REVIEW_REQUIRED":
            self.files_review_required -= 1
        self.files_failed_consistency_validation += 1
        self.files_validation_failed += 1
        outcome.status = "failed_consistency_validation"
        outcome.final_state = "VALIDATION_FAILED"
        outcome.error = error

    def mark_finalization_failed(self, outcome: FileOutcome, error: str) -> None:
        """A file passed every content check (syntax/round-trip/consistency/
        audit) but chmod/manifest finalization itself failed unexpectedly -
        remove it from the successful counts, same bookkeeping shape as
        mark_consistency_failed (the caller separately removes the file
        itself from the published output)."""
        if outcome.status == "failed_finalization":
            return
        old_bucket = _REPORT_BUCKETS[outcome.status]
        setattr(self, old_bucket, getattr(self, old_bucket) - 1)
        if outcome.final_state == "READY":
            self.files_ready -= 1
        elif outcome.final_state == "SECURITY_QUARANTINE":
            self.files_security_quarantine -= 1
        elif outcome.final_state == "REVIEW_REQUIRED":
            self.files_review_required -= 1
        self.files_failed_finalization += 1
        self.files_validation_failed += 1
        outcome.status = "failed_finalization"
        outcome.final_state = "VALIDATION_FAILED"
        outcome.error = error

    def mark_scan_only_verification_result(
        self, outcome: FileOutcome, *, status: str, final_state: str, error: str
    ) -> None:
        """FINAL SCAN_ONLY CONSISTENCY VERIFICATION bir scan_only_clean
        sonucunu geri aldiginda (registry hit ya da dogrulama basarisiz) ayni
        muhasebe seklini kullanir (bkz. mark_consistency_failed/
        mark_finalization_failed) - SADECE hala scan_only_clean olan bir
        outcome'u degistirir (idempotent guvenlik agi)."""
        if outcome.status not in ("scan_only_clean", "masked"):
            return
        old_bucket = _REPORT_BUCKETS[outcome.status]
        setattr(self, old_bucket, getattr(self, old_bucket) - 1)
        if outcome.final_state == "READY":
            self.files_ready -= 1
        new_bucket = _REPORT_BUCKETS[status]
        setattr(self, new_bucket, getattr(self, new_bucket) + 1)
        if final_state == "SECURITY_QUARANTINE":
            self.files_security_quarantine += 1
        elif final_state == "VALIDATION_FAILED":
            self.files_validation_failed += 1
        outcome.status = status
        outcome.final_state = final_state
        outcome.error = error

    # Ikincil risk denetiminden gecemeyen (ya da denetlenemeyen) dosya var mi.
    @property
    def has_quarantined_files(self) -> bool:
        return self.files_quarantined_pending_audit > 0

    # Maskeleme sonrasi sozdizimi dogrulamasindan gecemeyen (ve bu yuzden
    # basarisiz_dosyalar/'a tasinan) dosya var mi.
    @property
    def has_failed_syntax_validation(self) -> bool:
        return self.files_failed_syntax_validation > 0

    # Maskeleme sonrasi round-trip (geri-cozum orijinaliyle uyusmuyor)
    # dogrulamasindan gecemeyen (ve bu yuzden basarisiz_dosyalar/'a
    # tasinan) dosya var mi.
    @property
    def has_failed_round_trip_validation(self) -> bool:
        return self.files_failed_round_trip_validation > 0

    @property
    def has_failed_consistency_validation(self) -> bool:
        return self.files_failed_consistency_validation > 0

    @property
    def has_failed_finalization(self) -> bool:
        return self.files_failed_finalization > 0

    # Bu calisma, kapasitesi dusmus (fallback moda gecmis) bir detector
    # katmaniyla mi tamamlandi (bkz. degraded_detectors alani)?
    @property
    def has_degraded_detectors(self) -> bool:
        return bool(self.degraded_detectors)

    @property
    def has_unverifiable_files(self) -> bool:
        return bool(
            self.files_errored or self.files_skipped_too_large
            or self.files_copied_binary or self.files_copied_undecodable
            or self.files_skipped_unsupported
            or self.files_excluded or self.files_skipped_symlink
            or self.files_archive_unsupported or self.files_scan_only_sensitive
            or self.files_failed_detection
        )

    # Raporu insan tarafindan okunabilir, Turkce ozet metnine cevirir (CLI
    # ciktisi). Gercek metin uretimi app/services/export_report_formatter.py
    # icinde (bkz. Asama 2 / Adim 3a - SRP: bu sinif veri/toplama, formatter
    # sunum sorumlulugunu tasir). Cagiranlar (cli.py, app/ui.py, webapp/*)
    # hicbir degisiklik gerektirmez.
    def summary_text(self) -> str:
        return format_export_report(self)


# `child` yolunun `parent` yolunun altinda olup olmadigini kontrol eder.
def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


# Kaynak ve hedef klasor yollarinin guvenli olup olmadigini dogrular
# (birbirinin icinde olmamali, tehlikeli sistem klasorleri olmamali).
def _validate_paths(source: Path, target: Path) -> None:
    if not source.is_dir():
        raise ExportValidationError(f"kaynak dizin bulunamadi: {source}")
    if target in _PROTECTED_TARGETS:
        raise ExportValidationError(f"hedef dizin olarak kullanilamaz: {target}")
    if target.exists() and not target.is_dir():
        raise ExportValidationError(f"hedef yol bir dizin degil: {target}")
    if target == source:
        raise ExportValidationError("hedef dizin, kaynak proje dizini ile ayni olamaz")
    if _is_relative_to(target, source):
        raise ExportValidationError("hedef dizin, kaynak proje dizininin icinde olamaz")
    if _is_relative_to(source, target):
        raise ExportValidationError(
            "kaynak dizin, hedef dizinin icinde olamaz (uzerine yazma kaynak projeyi silebilir)"
        )


# Ayni context icin devam eden bir export var mi diye erken/hizli kontrol eder
# (asil kesin koruma DB'deki UNIQUE index - bkz. export_project'teki except bloklari).
def _ensure_no_in_progress_run(db: Session, context_id: int) -> None:
    existing = db.scalar(
        select(MaskingRun.id)
        .where(
            MaskingRun.context_id == context_id,
            MaskingRun.operation_type == "mask",
            MaskingRun.status == "in_progress",
        )
        .limit(1)
    )
    if existing is not None:
        raise ExportInProgressError(
            f"bu proje/sicil/branch icin zaten devam eden export var (run_id={existing}). "
            "Uygulama bu islem sirasinda kapandiysa `python -m app.cli recover-output --hedef <hedef> "
            "--application-stopped` ile islemi kapatin."
        )


# Bir dosyanin isleminde beklenmeyen bir istisna cikip DB SAVEPOINT'i geri
# alindiginda, o dosya sirasinda PAYLASILAN bellek-ici onbelleklere (MappingCache,
# consistency reverse_map) yazilmis olabilecek girdileri de geri almak icin
# kullanilir. Bu KRITIK: bir ValueMapping SQLAlchemy nesnesi, satiri SAVEPOINT
# ile geri alinsa DAHI bellekte "gecerliymis gibi" okunabilir kalir (rollback
# nesneyi otomatik expire ETMEZ) - onbellek geri alinmazsa, SONRAKI bir dosya
# ayni degeri tekrar gordugunde bu "hayalet" mapping'i yeniden kullanip DB'de
# hic karsiligi olmayan bir placeholder uretir (sessizce geri-donusumsuz cikti).
def _snapshot_dict(mapping: dict) -> dict:
    return dict(mapping)


def _restore_dict(mapping: dict, snapshot: dict) -> None:
    mapping.clear()
    mapping.update(snapshot)


# ValueMapping listesinden rapor icin kural-bazinda dagilim sozlugu uretir.
def _rule_breakdown(mappings, rule_names_by_id: dict[int, str]) -> dict[str, int]:
    breakdown: dict[str, int] = {}
    for mapping in mappings:
        rule_name = "llm_dynamic" if mapping.rule_id is None else rule_names_by_id.get(mapping.rule_id, f"rule_id={mapping.rule_id}")
        breakdown[rule_name] = breakdown.get(rule_name, 0) + 1
    return breakdown


# Ikincil (post-mask) denetimde risk bulunan ya da denetim basarisiz olan
# bir dosya icin DB'ye bir AuditWarning kaydi ekler - insan onayi bu kayit uzerinden yapilir.
def _create_audit_warning(
    db: Session, *, run_id: int, file_path: str, masked_content: str, encoding: str | None, reasoning: str,
    audit_failed: bool, output_path: str | None = None,
) -> None:
    db.add(
        AuditWarning(
            run_id=run_id,
            file_path=file_path,
            masked_content=masked_content,
            encoding=encoding,
            reasoning=reasoning,
            audit_failed=audit_failed,
            output_path=output_path,
        )
    )


# Sozdizimi dogrulamasindan gecemeyen bir dosyayi hedef klasor YERINE bu
# klasore yazar - "basarisiz_dosyalar_<run_id>" hedefin YANINDA (icinde
# DEGIL) durur, boylece export ciktisiyla asla karismaz. Goreli klasor
# yapisi korunur (ayni relative_path altinda).
def _write_to_failed_files_dir(failed_dir: Path, relative_path: Path, masked_text: str, encoding: str | None) -> None:
    dest = failed_dir / relative_path
    if encoding == JAVA_CLASS_ENCODING:
        dest = dest.with_name(dest.name + ".json")
        encoding = "utf-8"
    dest.parent.mkdir(parents=True, exist_ok=True)
    write_text_preserving_encoding(dest, masked_text, encoding)


def _failed_rel(prep: "_FilePrep") -> Path:
    # basarisiz_dosyalar_<run_id>/ klasoru de maskeli yol yapisini kullanir;
    # kaynak yol proje/kurum adini acik icerebilir.
    return Path(prep.masked_rel) if prep.masked_rel else prep.scanned.relative_path


def _read_consistency_target(
    path: Path, max_inline_size: int, *, preferred_encoding: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Read an exported text file for consistency scan.

    Returns ``(text, encoding, error)``. Binary files return all ``None``;
    text that cannot be safely scanned returns an error and must not remain
    in a supposedly safe export.

    preferred_encoding: dosyanin YAZILDIGI kodlama (output_file.encoding).
    Kodlama yeniden tahmin edilirse kayabilir (cp1254 bir dosya once cp1258,
    maskelemeden sonra cp1250 tahmin edilebilir); farkli cozulen metin
    round-trip dogrulamasini bozar ve dogru maskelenmis dosya duser. Bu
    yuzden aday sirasi: preferred -> tespit edilen -> utf-8. Bilinen bir
    metin kodlamasiyla yazilmis dosya, tahmin "binary" dese bile taranir.
    """
    try:
        size = path.stat().st_size
        if path.suffix.lower() == ".class":
            if size > max_inline_size:
                return None, JAVA_CLASS_ENCODING, "Java class dosyası tutarlılık boyut sınırını aşıyor"
            return parse_class(path.read_bytes()).text, JAVA_CLASS_ENCODING, None
        is_text, detected_encoding = peek_classify(path)
    except (OSError, ClassFormatError) as exc:
        return None, None, f"consistency taramasi: {describe_file_error('okuma', path, exc)}"
    if not is_text and not preferred_encoding:
        return None, None, None
    encoding = preferred_encoding or detected_encoding
    if size > max_inline_size:
        return None, encoding, (
            f"consistency taramasi icin dosya boyutu {size} bayt, izin verilen "
            f"{max_inline_size} bayt esigini asiyor"
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, encoding, f"consistency taramasi: {describe_file_error('okuma', path, exc)}"

    tried: set[str] = set()
    for candidate in (preferred_encoding, detected_encoding, "utf-8"):
        if not candidate or candidate in tried:
            continue
        tried.add(candidate)
        try:
            return raw.decode(candidate, errors="strict"), candidate, None
        except (UnicodeDecodeError, LookupError):
            continue
    return None, encoding, "consistency taramasi metin dosyasinin encoding'ini cozemedi"


def _remove_failed_consistency_output(
    output_file: _OutputFile,
    failed_dir: Path,
    *,
    text: str | None,
    encoding: str | None,
) -> None:
    """Keep a failed artifact for review but remove it from safe output."""
    relative = output_file.masked_relative_path or output_file.source_relative_path
    failed_path = failed_dir / relative
    failed_path.parent.mkdir(parents=True, exist_ok=True)
    if text is not None:
        _write_to_failed_files_dir(failed_dir, relative, text, encoding)
    elif output_file.path.is_file():
        shutil.copy2(output_file.path, failed_path)
    if output_file.path.is_file() or output_file.path.is_symlink():
        output_file.path.unlink()


def _mark_consistency_failure(
    db: Session,
    run_id: int,
    report: ExportReport,
    output_file: _OutputFile,
    failed_dir: Path,
    error: str,
    *,
    text: str | None,
    encoding: str | None,
) -> None:
    _remove_failed_consistency_output(output_file, failed_dir, text=text, encoding=encoding)
    report.mark_consistency_failed(output_file.outcome, error)
    db.add(
        AuditLog(
            run_id=run_id,
            file_path=output_file.outcome.relative_path,
            action="error",
            detail=f"final consistency safety scan basarisiz; dosya disa aktarilmadi: {error}",
        )
    )
    _create_audit_warning(
        db, run_id=run_id, file_path=output_file.outcome.relative_path,
        masked_content=text or "", encoding=encoding, reasoning=error, audit_failed=True,
        output_path=(output_file.masked_relative_path.as_posix() if output_file.masked_relative_path else None),
    )


def _validate_and_log_syntax(
    db: Session, run_id: int, relative_path: str, masked_text: str,
    original_text: str, validation_warnings: list[str] | None = None,
) -> str | None:
    if Path(relative_path).suffix.lower() == ".class":
        # Binary reconstruction separately validates the class structure.
        # This view is JSON containing decoded constant-pool strings.
        relative_path = str(Path(relative_path).with_suffix(".json"))
    diagnostics: list[str] = []
    error = validate_masked_syntax(
        relative_path, masked_text, original_text=original_text,
        sql_dialect=settings.validation.sql_dialect, diagnostics=diagnostics,
    )
    for notice in diagnostics:
        db.add(AuditLog(run_id=run_id, file_path=relative_path, action="skipped", detail=f"validation_warning; {notice}"))
        if validation_warnings is not None:
            entry = f"{relative_path}: {notice}"
            if entry not in validation_warnings:
                validation_warnings.append(entry)
    return error


# SYNTAX_FAILURE_ACTION=warn: sozdizimi hatasi dosyayi bloklamaz, rapora ve
# AuditLog'a uyari olarak duser. Hata metni yalnizca tur + satir/sutun icerir.
def _record_syntax_warning(
    db: Session, run_id: int, relative_path: str, syntax_error: str, validation_warnings: list[str] | None,
) -> None:
    db.add(AuditLog(
        run_id=run_id, file_path=relative_path, action="skipped",
        detail=f"validation_warning; syntax_failure_action=warn; {syntax_error}",
    ))
    if validation_warnings is not None:
        entry = f"{relative_path}: sozdizimi hatasi uyariyla ciktiya alindi: {syntax_error}"
        if entry not in validation_warnings:
            validation_warnings.append(entry)


def _register_passthrough_placeholders(reverse_map: dict[str, str], text: str) -> None:
    """Any placeholder-SHAPED token already in `text` that has no real DB
    mapping must round-trip as itself, not as "unresolved". It is either a
    genuinely pre-existing placeholder from an earlier run (never rewritten
    here) or - just as common in real source (e.g. `MAX_LOGIN_TEST_3`,
    matching the legacy `PREFIX_TEST_N` grammar) - an ordinary identifier
    that merely happens to look like one. Both cases must reverse to
    themselves unchanged; see _apply_masking's identical handling of
    already_masked_spans for the same invariant on the first pass.
    """
    for match in PLACEHOLDER_RE.finditer(text):
        token = match.group(0)
        reverse_map.setdefault(token, token)


# Bulgu konum(lar)ini ("satir X, sutun Y (TUR)") bicimlendirir - acik deger
# ASLA yazdirilmaz. _run_consistency_pass'in final safety scan'i ile
# _run_scan_only_final_verification arasinda PAYLASILIR (ayni ilke, tekrarsiz).
# NOT: metin BIREBIR eski final-safety-scan cikisiyla ayni (ASCII "satir"/
# "sutun") - tests/test_consistency_masking.py bu tam alt-diziyi doguluyor,
# degistirilmemeli.
def _consistency_occurrence_locations(text: str, occurrences: list) -> str:
    locations = "; ".join(
        f"satir {text.count(chr(10), 0, occ.start) + 1}, sutun "
        f"{occ.start - text.rfind(chr(10), 0, occ.start)} ({occ.entry.entity_type})"
        for occ in occurrences[:20]
    )
    if len(occurrences) > 20:
        locations += f"; ... ve {len(occurrences) - 20} tane daha"
    return locations


_CONSISTENCY_MAX_ROUNDS = 3


# Bir tutarlilik turunun bulgularini esleme + AuditLog kayitlariyla
# placeholder'a cevirir; `mappings`/`reverse_map` turlar boyunca birikir.
def _replace_consistency_occurrences(
    db: Session,
    run_ctx: MaskingRunContext,
    output_file: _OutputFile,
    text: str,
    occurrences: list,
    reverse_map: dict[str, str],
    mappings: list,
) -> str:
    replacements = []
    for occurrence in occurrences:
        rule = occurrence.entry.rule
        if rule is None:  # Defensive; registry only accepts real successful Match objects.
            raise ValueError("consistency registry entry has no source rule")
        mapping, _created = get_or_create_mapping(
            db,
            run_ctx.context.id,
            rule,
            occurrence.original_value,
            cache=run_ctx.mapping_cache.mappings if run_ctx.mapping_cache is not None else None,
            numeric=occurrence.numeric,
            run_id=run_ctx.run_id,
        )
        replacements.append((occurrence, mapping.placeholder_value))
        mappings.append(mapping)
        if mapping.placeholder_value not in reverse_map:
            reverse_map[mapping.placeholder_value] = decrypt_value(mapping.original_value_encrypted)
        db.add(
            AuditLog(
                run_id=run_ctx.run_id,
                file_path=output_file.outcome.relative_path,
                action="matched",
                detail=(
                    "source=consistency entity_type="
                    f"{occurrence.entry.entity_type} detectors="
                    f"{','.join(sorted(occurrence.entry.source_detectors))}"
                ),
            )
        )
        db.add(
            AuditLog(
                run_id=run_ctx.run_id,
                file_path=output_file.outcome.relative_path,
                action="replaced",
                detail=f"source=consistency placeholder={mapping.placeholder_value}",
            )
        )
    return apply_consistency_replacements(text, replacements)


def _run_consistency_pass(
    db: Session,
    run_ctx: MaskingRunContext,
    registry: SensitiveValueRegistry,
    output_files: list[_OutputFile],
    report: ExportReport,
    failed_dir: Path,
    rule_names_by_id: dict[int, str],
    max_inline_size: int,
) -> None:
    """Mask cross-file misses, then enforce that no canonical value remains."""
    if len(registry) == 0:
        return

    # Real unmask mappings cover old and new occurrences of a shared token.
    # Decrypt each mapping once rather than once per occurrence or file.
    used_mappings = db.execute(
        select(ValueMapping.placeholder_value, ValueMapping.original_value_encrypted)
        .where(ValueMapping.context_id == run_ctx.context.id, ValueMapping.run_id == run_ctx.run_id)
    )
    reverse_map = {placeholder: decrypt_value(encrypted) for placeholder, encrypted in used_mappings}
    scannable: list[tuple[_OutputFile, str, str | None]] = []
    for output_file in output_files:
        text, encoding, read_error = _read_consistency_target(
            output_file.path, max_inline_size, preferred_encoding=output_file.encoding,
        )
        if read_error is not None:
            _mark_consistency_failure(
                db,
                run_ctx.run_id,
                report,
                output_file,
                failed_dir,
                read_error,
                text=text,
                encoding=encoding,
            )
            continue
        if text is None:  # Confirmed binary: no text occurrence to inspect.
            continue

        # SAVEPOINT + onbellek anlik goruntusu: bkz. _snapshot_dict/_restore_dict
        # dokstringi - Faz B'deki AYNI ilke burada da gecerli. Beklenen
        # (istisna OLMAYAN) sonuclar - eslesme yok/round-trip basarisiz/
        # sozdizimi bozuk - normal `continue` ile devam eder, savepoint
        # sessizce serbest kalir. SADECE gercek bir istisnada geri alinir.
        reverse_map_snapshot = _snapshot_dict(reverse_map)
        cache_snapshot = (
            _snapshot_dict(run_ctx.mapping_cache.mappings) if run_ctx.mapping_cache is not None else None
        )
        try:
            with db.begin_nested():
                # Bir tur, sonraki bir gecisi ancak kendisinden sonra gorunur
                # kilabilir (orn. degisen string/yorum baglami). Acik gecis
                # kalmayana kadar en fazla _CONSISTENCY_MAX_ROUNDS tur
                # degistirilir; hala kalan varsa asagidaki final safety scan
                # dosyayi eskisi gibi dusurur.
                masked_text = text
                mappings = []
                for _round in range(_CONSISTENCY_MAX_ROUNDS):
                    occurrences = find_consistency_occurrences(
                        masked_text, registry, file_path=output_file.outcome.relative_path
                    )
                    if not occurrences:
                        break
                    masked_text = _replace_consistency_occurrences(
                        db, run_ctx, output_file, masked_text, occurrences, reverse_map, mappings,
                    )
                if not mappings:
                    scannable.append((output_file, text, encoding))
                    continue
                _register_passthrough_placeholders(reverse_map, masked_text)
                round_trip = verify_round_trip_digest(
                    output_file.original_digest, output_file.original_length, masked_text, reverse_map,
                )
                if not round_trip.ok:
                    _mark_consistency_failure(
                        db,
                        run_ctx.run_id,
                        report,
                        output_file,
                        failed_dir,
                        f"consistency round-trip dogrulamasi basarisiz: {round_trip.detail}",
                        text=masked_text,
                        encoding=encoding,
                    )
                    continue

                syntax_error = _validate_and_log_syntax(
                    db, run_ctx.run_id, output_file.outcome.relative_path,
                    masked_text, text, report.validation_warnings,
                )
                if (syntax_error is not None and settings.validation.syntax_failure_action == "warn"
                        and output_file.original_binary_digest is None):
                    _record_syntax_warning(
                        db, run_ctx.run_id, output_file.outcome.relative_path, syntax_error,
                        report.validation_warnings,
                    )
                    syntax_error = None
                if syntax_error is not None:
                    _mark_consistency_failure(
                        db,
                        run_ctx.run_id,
                        report,
                        output_file,
                        failed_dir,
                        f"consistency maskesi sozdizimini bozdu: {syntax_error}",
                        text=masked_text,
                        encoding=encoding,
                    )
                    continue

                try:
                    used_encoding = write_text_preserving_encoding(output_file.path, masked_text, encoding)
                except (OSError, ClassFormatError) as exc:
                    _mark_consistency_failure(
                        db,
                        run_ctx.run_id,
                        report,
                        output_file,
                        failed_dir,
                        f"consistency ciktisi: {describe_file_error('yazma', output_file.path, exc)}",
                        text=masked_text,
                        encoding=encoding,
                    )
                    continue
                report.add_consistency_matches(output_file.outcome, _rule_breakdown(mappings, rule_names_by_id))
                scannable.append((output_file, masked_text, used_encoding))
        except Exception as exc:
            _restore_dict(reverse_map, reverse_map_snapshot)
            if run_ctx.mapping_cache is not None:
                _restore_dict(run_ctx.mapping_cache.mappings, cache_snapshot)
            # Never log exc's message: bkz. modulun ustundeki ayni ilke.
            _mark_consistency_failure(
                db,
                run_ctx.run_id,
                report,
                output_file,
                failed_dir,
                f"consistency gecisinde beklenmeyen bir hata olustu ({type(exc).__name__})",
                text=None,
                encoding=output_file.encoding,
            )

    # FINAL SAFETY SCAN: the run may only keep files for which the canonical
    # registry has zero valid open occurrences after consistency masking.
    # Per-file SAVEPOINT + isolation - same principle as the loop above: one
    # file's unexpected exception here must not lose every OTHER file that
    # already correctly cleared this final scan.
    for output_file, text, encoding in scannable:
        try:
            with db.begin_nested():
                _register_passthrough_placeholders(reverse_map, text)
                round_trip = verify_round_trip_digest(
                    output_file.original_digest, output_file.original_length, text, reverse_map,
                )
                if not round_trip.ok:
                    _mark_consistency_failure(
                        db, run_ctx.run_id, report, output_file, failed_dir,
                        f"final consistency round-trip dogrulamasi basarisiz: {round_trip.detail}",
                        text=text, encoding=encoding,
                    )
                    continue
                remaining = find_consistency_occurrences(
                    text, registry, file_path=output_file.outcome.relative_path
                )
                if remaining:
                    # Konum + tur bilgisi guvenle gosterilebilir (kullanici
                    # dosyada NEREYE bakacagini bulabilsin diye) - ama asla
                    # acik degerin kendisi degil (bkz. modul dokstringindeki
                    # "hicbir zaman ham icerik/alt-dizi yazdirma" ilkesi).
                    locations = _consistency_occurrence_locations(text, remaining)
                    _mark_consistency_failure(
                        db,
                        run_ctx.run_id,
                        report,
                        output_file,
                        failed_dir,
                        f"final safety scan {len(remaining)} acik canonical occurrence buldu: {locations}",
                        text=text,
                        encoding=encoding,
                    )
        except Exception as exc:
            # Never log exc's message: bkz. modulun ustundeki ayni ilke.
            _mark_consistency_failure(
                db,
                run_ctx.run_id,
                report,
                output_file,
                failed_dir,
                f"final consistency taramasinda beklenmeyen bir hata olustu ({type(exc).__name__})",
                text=None,
                encoding=output_file.encoding,
            )


# Bir coroutine'i, ayni anda en fazla N tanesi calisacak sekilde semafor altinda calistirir.
async def _bounded(coro, semaphore: asyncio.Semaphore):
    async with semaphore:
        return await coro


# Faz A'ya hazirlanmis bir dosya grubu (batch): kaynak dosyalar, hazirlik
# sonuclari ve Faz D'de rapor/audit log icin saklanan yol maskeleme sonuclari.
@dataclass
class _PreparedBatch:
    files: list
    preps: list["_FilePrep"]
    masked_paths: list[Path]
    path_mappings: list[list]


# Yol maskeleme + Faz A hazirligi (okuma/siniflandirma) - hizli I/O, sirali.
def _prepare_batch(
    db: Session, run_id: int, files: list, path_plans: dict, output_target: Path, max_inline_size: int,
) -> _PreparedBatch:
    batch = _PreparedBatch(files=files, preps=[], masked_paths=[], path_mappings=[])
    for scanned in files:
        masked_relative_path, path_mappings = path_plans[scanned.relative_path]
        prep = _prepare_file(db, run_id, scanned, output_target / masked_relative_path, max_inline_size)
        prep.masked_rel = masked_relative_path.as_posix()
        batch.preps.append(prep)
        batch.masked_paths.append(masked_relative_path)
        batch.path_mappings.append(path_mappings)
    return batch


# Faz A (es zamanli, DB'ye dokunmaz): erken-cikis yapmamis dosyalarin tespiti.
# Donus: preps indeksi -> DetectionOutcome.
async def _detect_batch(
    orchestrator: DetectionOrchestrator, preps: list["_FilePrep"], semaphore: asyncio.Semaphore,
) -> dict[int, DetectionOutcome]:
    ready_indices = [i for i, prep in enumerate(preps) if prep.outcome is None]
    outcomes = await asyncio.gather(
        *(_bounded(_detect_for_prep(orchestrator, preps[i]), semaphore) for i in ready_indices)
    )
    return dict(zip(ready_indices, outcomes))


# Batch'in tespitini arka planda baslatir; sonuc Faz B'den hemen once beklenir.
def _start_detection(
    orchestrator: DetectionOrchestrator, batch: _PreparedBatch, semaphore: asyncio.Semaphore,
) -> asyncio.Future:
    return asyncio.ensure_future(_detect_batch(orchestrator, batch.preps, semaphore))


# Onceden baslatilmis (prefetch) bir tespit gorevini iptal edip bitmesini bekler.
async def _cancel_detection(task: asyncio.Future | None) -> None:
    if task is None or task.done():
        return
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


# Faz A hazirligi bitmis, tespite (detect_matches) hazir HALE GELMIS ya da
# erken-cikis yapmis (outcome doluysa) bir dosyanin durumu.
@dataclass
class _FilePrep:
    scanned: object
    dest_path: Path
    rel: str
    text: str | None = None
    encoding: str | None = None
    outcome: FileOutcome | None = None
    class_document: object | None = None
    # "mask" (default): normal detect->mask->write flow. "scan_only":
    # dependency lock/integrity file - goes through the SAME Faz A
    # detection, but Faz B never masks/writes it (bkz. _finalize_scan_only).
    mode: str = "mask"
    # Ciktidaki MASKELENMIS goreli yol (posix). Karantinadan serbest birakma
    # bu yola yazar - `rel` kaynak yoludur ve proje/kurum adini acik icerebilir.
    masked_rel: str | None = None


# A supported text file prepared for post-mask LLM audit and final validation.
@dataclass
class _MaskedFile:
    prep: _FilePrep
    masked_text: str
    rule_breakdown: dict[str, int]
    match_count: int
    # Bu dosya icin Katman 3 (LLM) tespitinin basarisiz oldugu hata(lar) -
    # bkz. _finalize_file: bu katman calismadan dosyanin tam kapsamli
    # tarandigi garanti edilemeyecegi icin (regex/Presidio bir seyi
    # yakalamis olsa bile) dosya otomatik karantinaya alinir.
    llm_errors: list[str] = field(default_factory=list)
    review_results: list[object] = field(default_factory=list)
    # Bir detector katmaninin (Katman 1/2/3, herhangi biri) bu dosya icin
    # BEKLENMEYEN bir hatayla coktugu durum(lar) - bkz. detectors.py
    # DetectorOutput.crashes dokstringi. llm_errors'tan KASITLI olarak AYRI:
    # settings.vllm.enabled=False iken bile (Katman 3 zaten kapaliyken)
    # Katman 1/2'nin coktugu bir dosya guvenlik geregi karantinaya alinmali -
    # bu quarantine, LLM'in acik/kapali olma durumuna BAGLI DEGILDIR.
    detector_crashes: list[str] = field(default_factory=list)
    # Kodlanmis (base64/hex/bayt dizisi) metnin icinde bulunan hassas veri -
    # bkz. encoded_text_detector. Deger icermez; dosya karantinaya alinir.
    encoded_leaks: list[str] = field(default_factory=list)


# Degeri kesin bilinen sizintilar (acik kalan sozluk terimi, metinde birebir
# dogrulanmis denetim alintisi) icin _finalize_file'in insan onayi yerine
# dondurdugu otomatik duzeltme talebi.
@dataclass
class _RemediationRequest:
    leaked_terms: list = field(default_factory=list)
    findings: list = field(default_factory=list)


# Otomatik duzeltilmis, LLM denetiminin tekrar calismasini bekleyen dosya.
# `original`/`original_audit`: duzeltme basarisiz olursa insan onayina
# dusecek duzeltme-oncesi hal. matches/mappings yalnizca dosya READY
# olursa tutarlilik registry'sine eklenir.
@dataclass
class _PendingRemediation:
    original: _MaskedFile
    original_audit: object
    current: _MaskedFile
    rounds: int
    matches: list = field(default_factory=list)
    mappings: list = field(default_factory=list)


@dataclass
class _OutputFile:
    """A file currently present in the exported project copy."""

    path: Path
    source_relative_path: Path
    outcome: FileOutcome
    masked_relative_path: Path | None = None
    encoding: str | None = None
    original_digest: str | None = None
    original_length: int | None = None
    original_binary_digest: str | None = None
    # Lock dosyasi (SCAN_ONLY): tutarlilik adimi YENIDEN YAZMAZ, yalnizca
    # salt-okunur final dogrulamadan gecer (host'u maskelenmis olsa bile).
    scan_only: bool = False


# Dosyayi okur, exclude/symlink/boyut/binary/encoding kontrollerinden gecirir ve siniflandirir.
def _prepare_file(
    db: Session,
    run_id: int,
    scanned,
    dest_path: Path,
    max_inline_size: int,
) -> _FilePrep:
    rel = str(scanned.relative_path)
    ignored_reason = ignored_directory_reason(scanned.relative_path)
    if ignored_reason:
        db.add(AuditLog(run_id=run_id, file_path=rel, action="skipped", detail=ignored_reason))
        return _FilePrep(scanned, dest_path, rel, outcome=FileOutcome(rel, status="excluded"))

    read_outcome = read_scanned_file(
        scanned, dest_path, max_inline_size, copy_unscannable=False,
        legacy_encodings=settings.encoding.legacy_text_encoding_list,
    )

    if read_outcome.status == ReadStatus.EXCLUDED:
        db.add(
            AuditLog(
                run_id=run_id,
                file_path=rel,
                action="skipped",
                detail=f"guvenlik politikasi geregi haric tutuldu (desen: {scanned.excluded_by.pattern_name} "
                f"'{scanned.excluded_by.glob_pattern}'), hedefe kopyalanmadi",
            )
        )
        return _FilePrep(scanned, dest_path, rel, outcome=FileOutcome(rel, status="excluded"))

    if read_outcome.status == ReadStatus.SKIPPED_SYMLINK:
        db.add(AuditLog(run_id=run_id, file_path=rel, action="skipped", detail="symbolic link, not followed/copied"))
        return _FilePrep(scanned, dest_path, rel, outcome=FileOutcome(rel, status="skipped_symlink"))

    if read_outcome.status == ReadStatus.ERROR:
        db.add(AuditLog(run_id=run_id, file_path=rel, action="error", detail=read_outcome.error))
        _create_audit_warning(
            db, run_id=run_id, file_path=rel, masked_content="", encoding=None,
            reasoning=f"Dosya güvenlik doğrulamasına hazırlanamadı: {read_outcome.error}", audit_failed=True,
        )
        return _FilePrep(scanned, dest_path, rel, outcome=FileOutcome(rel, status="error", error=read_outcome.error))

    if read_outcome.status == ReadStatus.SKIPPED_TOO_LARGE:
        db.add(
            AuditLog(
                run_id=run_id,
                file_path=rel,
                action="skipped",
                detail=f"{read_outcome.size} bayt > {max_inline_size} bayt esigi; dogrulanamadi ve ciktiya alinmadi",
            )
        )
        _create_audit_warning(
            db, run_id=run_id, file_path=rel, masked_content="", encoding=None,
            reasoning=f"Dosya boyut sınırını aştığı için güvenlik doğrulaması tamamlanamadı ({read_outcome.size} bayt)",
            audit_failed=True,
        )
        return _FilePrep(scanned, dest_path, rel, outcome=FileOutcome(rel, status="skipped_too_large"))

    if read_outcome.status == ReadStatus.COPIED_BINARY:
        # The reader also serves unmask, where COPIED_BINARY is a legacy
        # status. Export uses copy_unscannable=False: no bytes were copied.
        # Unsupported content is a coverage exclusion, not a failed masking
        # operation. Keep it visible and out of both output and release queues.
        if is_opaque_binary_filename(scanned.relative_path.name):
            # Known opaque type (.jar etc.): decided by extension before the
            # size check, so a large one is never reported as a failed check.
            reason = (
                f"Desteklenmeyen dosya türü ({scanned.relative_path.suffix.lower()}); "
                "Dosya taranmadı ve çıktıya alınmadı."
            )
        else:
            reason = (
                "İçerik metin olarak tanınamadı; bu içerik türünde maskeleme desteklenmiyor. "
                "Dosya taranmadı ve çıktıya alınmadı."
            )
        db.add(
            AuditLog(
                run_id=run_id, file_path=rel, action="skipped",
                detail=f"final_state=SKIPPED final_output=blocked; skipped_unsupported; {reason}",
            )
        )
        return _FilePrep(
            scanned, dest_path, rel,
            outcome=FileOutcome(rel, status="skipped_unsupported", error=reason),
        )

    if read_outcome.status == ReadStatus.COPIED_UNDECODABLE:
        db.add(
            AuditLog(
                run_id=run_id,
                file_path=rel,
                action="skipped",
                detail=f"tespit edilen encoding ({read_outcome.detected_encoding}) ile decode edilemedi; çıktıya alınmadı",
            )
        )
        _create_audit_warning(
            db, run_id=run_id, file_path=rel, masked_content="", encoding=read_outcome.detected_encoding,
            reasoning="Metin kodlaması çözülemediği için güvenlik doğrulaması tamamlanamadı", audit_failed=True,
        )
        return _FilePrep(scanned, dest_path, rel, outcome=FileOutcome(rel, status="copied_undecodable"))

    if read_outcome.status == ReadStatus.ARCHIVE_UNSUPPORTED:
        # ARCHIVE: recursive extraction/repack is out of scope for this
        # version - never opened, never silently ignored either. "Taranamadi
        # != temiz": quarantined for human review, same mechanism as any
        # other post-mask risk (bkz. _create_audit_warning).
        reason = (
            "Arşiv/paket formatı bu sürümde açılıp taranmıyor (recursive extraction "
            "desteklenmiyor); içeriği doğrulanamadığından çıktıya alınmadı ve incelemeye gönderildi."
        )
        db.add(
            AuditLog(
                run_id=run_id, file_path=rel, action="skipped",
                detail=f"final_state=SECURITY_QUARANTINE final_output=blocked; archive_unsupported; {reason}",
            )
        )
        _create_audit_warning(
            db, run_id=run_id, file_path=rel, masked_content="", encoding=None,
            reasoning=reason, audit_failed=True,
        )
        return _FilePrep(
            scanned, dest_path, rel,
            outcome=FileOutcome(rel, status="archive_unsupported", error=reason, final_state="SECURITY_QUARANTINE"),
        )

    mode = "scan_only" if read_outcome.status == ReadStatus.SCAN_ONLY_TEXT_READY else "mask"
    if read_outcome.encoding_fallback and run_id is not None:
        # Yalnizca kodlama adi yazilir; icerik/bayt asla (bkz. modul ilkesi).
        db.add(AuditLog(
            run_id=run_id, file_path=rel, action="skipped",
            detail=(
                f"validation_warning; encoding_fallback encoding={read_outcome.encoding}; "
                "metin kodlamasi tespit edilemedi, baytlar birebir korunarak okundu"
            ),
        ))
    if run_id is not None:
        db.add(AuditLog(
            run_id=run_id, file_path=rel, action="skipped",
            detail=f"scan_policy mode={mode} presidio=True llm={settings.vllm.enabled and mode != 'scan_only'}",
        ))
    return _FilePrep(
        scanned, dest_path, rel, text=read_outcome.text, encoding=read_outcome.encoding,
        class_document=read_outcome.class_document, mode=mode,
    )


# Hazirlanmis bir dosya icin tespiti (detect_matches) calistirir.
async def _detect_for_prep(orchestrator: DetectionOrchestrator, prep: _FilePrep) -> DetectionOutcome:
    metadata = {
        "enable_presidio": True,
        # Lock dosyalarinda LLM calismaz: icerik paket adi/surum/ozetlerinden
        # olusur, LLM bulgulari bu dosyalarda neredeyse hep yanlis-pozitifti.
        "enable_llm": prep.mode != "scan_only",
        "file_path": str(Path(prep.rel).with_suffix(".json")) if prep.class_document else prep.rel,
    }
    try:
        return await detect_matches(orchestrator, prep.text, metadata)
    except Exception as exc:
        # Defense in depth: DetectionOrchestrator.scan already isolates a
        # single detector's crash from the others, but the post-processing
        # around it (overlap resolution, boundary validation) is pure code
        # over untrusted-shaped detector output and could, in principle,
        # still raise. Faz A runs every prepared file through asyncio.gather
        # - one unhandled exception here would cancel and lose every OTHER
        # file in the batch, not just this one. Never log exc's message:
        # it ran over real file content.
        return DetectionOutcome(
            matches=[], review_results=[], llm_errors=[], already_masked_spans=[],
            overlap_conflicts=[], boundary_rejections=[],
            detector_crashes=[
                f"tespit sonrasi isleme beklenmeyen bir hatayla durdu ({type(exc).__name__}); "
                "bu dosya icin tarama kapsami guvenilir sayilamaz"
            ],
        )


# Tespit sonucunu DB'ye uygular, round-trip dogrulamasindan gecirir. Basarili
# olursa post-mask denetime hazir bir _MaskedFile, degilse bir FileOutcome doner.
def _apply_masking(
    db: Session,
    run_ctx: MaskingRunContext,
    prep: _FilePrep,
    outcome: DetectionOutcome,
    rule_names_by_id: dict[int, str],
    failed_dir: Path,
    consistency_registry: SensitiveValueRegistry,
) -> _MaskedFile | FileOutcome:
    masked_text, mappings = apply_detections(db, run_ctx, prep.text, outcome, file_path=prep.rel)

    if not mappings:
        # No mapping means no round-trip replacement to check. Every supported
        # text file still receives LLM audit and final validation before publication.
        return _MaskedFile(
            prep=prep, masked_text=masked_text, rule_breakdown={}, match_count=0,
            llm_errors=list(outcome.llm_errors), review_results=list(outcome.review_results),
            detector_crashes=list(outcome.detector_crashes), encoded_leaks=list(outcome.encoded_leaks),
        )

    rule_breakdown = _rule_breakdown(mappings, rule_names_by_id)

    # OTOMATIK ROUND-TRIP DOGRULAMASI - ATLANAMAZ: az once olusturulan/
    # bulunan mapping'lerle masked_text'i (gercek unmask_project()'in
    # kullanacagi AYNI reverse_text() ile) geri cozup orijinal metinle
    # birebir karsilastirir. Bir detector/overlap-cozumu/replacement hatasi
    # restore'u imkansiz/kayipli hale getiriyorsa bu dosya SESSIZCE
    # gecilmez - hedefe HIC YAZILMAZ, basarisiz_dosyalar/'a tasinir.
    placeholder_map = {m.placeholder_value: decrypt_value(m.original_value_encrypted) for m in mappings}
    # already_masked_spans (bkz. rule_engine.PLACEHOLDER_RE) bu turdan HIC
    # DOKUNULMADI - kaynakta OLDUGU GIBI kaldilar (gercekten onceden
    # maskelenmis bir deger OLABILIR ya da yalnizca "mask_x_1"/"X_TEST_1"
    # bicimine TESADUFEN benzeyen sıradan bir tanimlayici OLABILIR, orn.
    # "MAX_LOGIN_TEST_3"). Ikisinde de bu tur icin round-trip beklentisi
    # AYNI: reverse_text() bu metni degistirmeden geri vermeli (kimlik/
    # identity). Bu girdiler olmadan reverse_text() onlari "cozulemeyen
    # placeholder" sanip HICBIR SEY BOZULMAMISKEN dosyayi yanlislikla
    # basarisiz_dosyalar/'a tasirdi (bkz. tests/test_exporter_failure_handling.py).
    for start, end in outcome.already_masked_spans:
        token = prep.text[start:end]
        placeholder_map.setdefault(token, token)
    round_trip = verify_round_trip(prep.text, masked_text, placeholder_map)
    if not round_trip.ok:
        _write_to_failed_files_dir(failed_dir, _failed_rel(prep), masked_text, prep.encoding)
        db.add(
            AuditLog(
                run_id=run_ctx.run_id, file_path=prep.rel, action="round_trip_hatasi",
                detail=f"final_state=VALIDATION_FAILED final_output=blocked; Round-trip dogrulamasi basarisiz, disa aktarilmadi: {round_trip.detail}",
            )
        )
        _create_audit_warning(
            db, run_id=run_ctx.run_id, file_path=prep.rel, masked_content=masked_text,
            encoding=prep.encoding, output_path=prep.masked_rel, reasoning=f"Round-trip doğrulaması başarısız: {round_trip.detail}",
            audit_failed=True,
        )
        return FileOutcome(
            prep.rel, status="failed_round_trip_validation", match_count=len(mappings),
            rule_breakdown=rule_breakdown, error=round_trip.detail,
        )

    # Only detections that survived overlap/boundary resolution, produced a
    # real mapping and passed round-trip become second-pass authorities.
    consistency_registry.add_successful_matches(outcome.matches, mappings)

    return _MaskedFile(
        prep=prep, masked_text=masked_text, rule_breakdown=rule_breakdown, match_count=len(mappings),
        llm_errors=list(outcome.llm_errors), review_results=list(outcome.review_results),
        detector_crashes=list(outcome.detector_crashes), encoded_leaks=list(outcome.encoded_leaks),
    )


# SCAN_ONLY bulgularini, denetim uyarisi metnine gomulecek satir/sutun/tur
# ozetine cevirir - _run_consistency_pass'in final safety scan'iyla AYNI
# ilke: acik deger ASLA yazdirilmaz, sadece NEREDE/NE TURDE bulundugu.
def _scan_only_finding_summary(text: str, outcome: DetectionOutcome) -> str:
    locations: list[str] = []
    for match in outcome.matches[:20]:
        line = text.count("\n", 0, match.start) + 1
        col = match.start - text.rfind("\n", 0, match.start)
        entity = match.entity_type or (match.rule.rule_name if match.rule else "bilinmeyen")
        locations.append(f"satır {line}, sütun {col} ({entity})")
    for result in outcome.review_results[:20]:
        if result.start is not None:
            line = text.count("\n", 0, result.start) + 1
            col = result.start - text.rfind("\n", 0, result.start)
            locations.append(f"satır {line}, sütun {col} ({result.tip}, düşük/orta güvenli - inceleme gerektirir)")
    total = len(outcome.matches) + len(outcome.review_results)
    summary = "; ".join(locations)
    if total > len(locations):
        summary += f"; ... ve {total - len(locations)} tane daha"
    return summary


# Kodlanmis metin sizintisinin kullaniciya gosterilen gerekcesi (deger icermez).
def _encoded_leak_reason(leaks: list[str]) -> str:
    return (
        "Kodlanmış (base64/hex/bayt dizisi) bir değerin içinde hassas veri bulundu; değer kodlanmış "
        "blok içinde otomatik maskelenemediği için dosya çıktıya alınmadı. Değeri kaynakta kaldırıp "
        "yeniden tarayın ya da gerçekten hassas değilse 'Yanlış Alarm' ile serbest bırakın: "
        + "; ".join(leaks)
    )


# SCAN_ONLY (bagimlilik lock/integrity dosyalari, bkz. _prepare_file): Faz
# A'nin tespit sonucuna gore KARAR verir - hicbir durumda maskelemez/yeniden
# yazmaz (dosya butunlugu/imza gecerliligi bozulmasin diye). Bulgu yoksa
# kaynak byte'lari HIC DOKUNULMADAN hedefe kopyalanir (masking/consistency
# pipeline'i tamamen atlanir); bulgu varsa ya da tespit katmani guvenilir
# bir sonuc uretemediyse dosya hic yazilmaz - "taranamadi != temiz" ilkesi
# geregi ikisi de review/quarantine'e (AuditWarning) gonderilir.
def _finalize_scan_only(
    db: Session, run_ctx: MaskingRunContext, prep: "_FilePrep", outcome: DetectionOutcome,
) -> FileOutcome:
    run_id = run_ctx.run_id
    if outcome.detector_crashes or (outcome.llm_errors and settings.vllm.enabled):
        reasons = list(outcome.detector_crashes) + list(outcome.llm_errors)
        reason = (
            "Bağımlılık/lock dosyası için tespit katmanı güvenilir bir sonuç üretemedi - "
            "'taranamadı' hiçbir zaman 'temiz' sayılmaz, güvenlik gereği incelemeye alındı: "
            + "; ".join(reasons)
        )
        db.add(
            AuditLog(
                run_id=run_id, file_path=prep.rel, action="error",
                detail="final_state=VALIDATION_FAILED final_output=blocked; scan_only tespiti tamamlanamadi",
            )
        )
        _create_audit_warning(
            db, run_id=run_id, file_path=prep.rel, masked_content="",
            encoding=prep.encoding, output_path=prep.masked_rel, reasoning=reason, audit_failed=True,
        )
        return FileOutcome(prep.rel, status="failed_detection", error=reason, final_state="VALIDATION_FAILED")

    if outcome.encoded_leaks:
        reason = _encoded_leak_reason(outcome.encoded_leaks)
        db.add(AuditLog(
            run_id=run_id, file_path=prep.rel, action="skipped",
            detail="final_state=SECURITY_QUARANTINE final_output=blocked; kodlanmis metinde hassas veri",
        ))
        _create_audit_warning(
            db, run_id=run_id, file_path=prep.rel, masked_content=prep.text or "",
            encoding=prep.encoding, output_path=prep.masked_rel, reasoning=reason, audit_failed=False,
        )
        return FileOutcome(prep.rel, status="scan_only_sensitive", error=reason, final_state="SECURITY_QUARANTINE")

    # Paket ozetleri ve public registry URL'leri izin listesindedir; sozluk
    # bulgulari haric (bkz. lockfile_policy). Kalan tum bulgular ic registry
    # URL'lerindeyse bu URL'ler maskelenir, aksi halde dosya eskisi gibi onaya gider.
    findings = [
        (m.start, m.end, m.source_detector == "dictionary") for m in outcome.matches
    ] + [
        (r.start, r.end, r.kaynak_motor == "dictionary") for r in outcome.review_results
        if r.start is not None and r.end is not None
    ]
    unlocated = len(outcome.review_results) - (len(findings) - len(outcome.matches))
    plan = plan_lockfile(prep.text, prep.rel, findings, settings.lockfile.public_registry_host_list)
    if plan.allowlisted:
        db.add(AuditLog(
            run_id=run_id, file_path=prep.rel, action="skipped",
            detail=f"scan_only allowlisted_findings={plan.allowlisted} remaining_findings={len(plan.remaining)}",
        ))
    remediation_note = ""
    if plan.url_spans and not unlocated:
        result = _mask_lockfile_urls(db, run_ctx, prep, plan.url_spans)
        if isinstance(result, FileOutcome):
            return result
        remediation_note = (
            f" (iç registry URL maskelemesi denendi, şu kontrolde başarısız oldu: "
            f"{_LOCKFILE_CHECK_LABELS.get(result, result)})"
        )

    if plan.remaining or unlocated:
        reason = (
            "Bağımlılık/lock dosyasında hassas olabilecek içerik bulundu; bütünlüğünü/imza "
            "geçerliliğini bozmamak için otomatik maskelenmedi, çıktıya alınmadı: "
            + _scan_only_finding_summary(prep.text, outcome)
            + remediation_note
        )
        db.add(
            AuditLog(
                run_id=run_id, file_path=prep.rel, action="skipped",
                detail="final_state=SECURITY_QUARANTINE final_output=blocked; scan_only_sensitive",
            )
        )
        _create_audit_warning(
            db, run_id=run_id, file_path=prep.rel, masked_content="",
            encoding=prep.encoding, output_path=prep.masked_rel, reasoning=reason, audit_failed=True,
        )
        return FileOutcome(prep.rel, status="scan_only_sensitive", error=reason, final_state="SECURITY_QUARANTINE")

    try:
        shutil.copy2(prep.scanned.absolute_path, prep.dest_path)
    except OSError as exc:
        reason = f"Byte-identical {describe_file_error('kopyalama', prep.dest_path, exc)}"
        db.add(AuditLog(run_id=run_id, file_path=prep.rel, action="error", detail=reason))
        _create_audit_warning(
            db, run_id=run_id, file_path=prep.rel, masked_content="",
            encoding=prep.encoding, output_path=prep.masked_rel, reasoning=reason, audit_failed=True,
        )
        return FileOutcome(prep.rel, status="error", error=reason)

    db.add(
        AuditLog(
            run_id=run_id, file_path=prep.rel, action="skipped",
            detail="final_state=READY final_output=written; scan_only_clean; byte-identical kopyalandi",
        )
    )
    return FileOutcome(prep.rel, status="scan_only_clean", final_state="READY")


_LOCKFILE_CHECK_LABELS = {
    "geri_donus": "geri dönüş doğrulaması",
    "acik_terim": "açık terim kontrolü",
    "ayristirma": "dosya biçimi (parser) doğrulaması",
}


# Lock dosyasindaki ic registry URL'lerini maskeler, ardindan geri donus +
# acik terim + parser (JSON/YAML/TOML) kontrollerini calistirir ve dosyayi
# yazar. Kendi SAVEPOINT'inde calisir: bir kontrol basarisizsa eslemeler ve
# onbellek geri alinir, basarisiz kontrolun kodu doner (caller onaya gonderir).
def _mask_lockfile_urls(
    db: Session, run_ctx: MaskingRunContext, prep: "_FilePrep", url_spans: list[tuple[int, int]],
) -> "FileOutcome | str":
    rule = replace(
        synthetic_llm_rule("INTERNAL_REGISTRY_URL"),
        rule_name="lockfile:internal_registry_url", description="Lock dosyasindaki ic registry URL'si.",
    )
    matches = [
        Match(rule=rule, original_value=prep.text[start:end], start=start, end=end,
              entity_type=rule.category, source_detector="lockfile")
        for start, end in sorted(set(url_spans))
    ]
    detection = DetectionOutcome(matches=matches, review_results=[], llm_errors=[], already_masked_spans=[],
                                 overlap_conflicts=[], boundary_rejections=[])
    cache = run_ctx.mapping_cache.mappings if run_ctx.mapping_cache is not None else None
    cache_snapshot = _snapshot_dict(cache) if cache is not None else None
    savepoint = db.begin_nested()
    try:
        masked_text, mappings = apply_detections(db, run_ctx, prep.text, detection, file_path=prep.rel)
        failure = None
        placeholder_map = {m.placeholder_value: decrypt_value(m.original_value_encrypted) for m in mappings}
        _register_passthrough_placeholders(placeholder_map, prep.text)
        if not verify_round_trip(prep.text, masked_text, placeholder_map).ok:
            failure = "geri_donus"
        elif find_leaked_terms(db, masked_text):
            failure = "acik_terim"
        elif validate_masked_syntax(f"lockfile.{structured_format(prep.rel)}", masked_text, original_text=None):
            failure = "ayristirma"
        if failure is None:
            write_text_preserving_encoding(prep.dest_path, masked_text, prep.encoding)
    except BaseException:
        savepoint.rollback()
        if cache is not None:
            _restore_dict(cache, cache_snapshot)
        raise
    if failure is not None:
        savepoint.rollback()
        if cache is not None:
            _restore_dict(cache, cache_snapshot)
        db.add(AuditLog(
            run_id=run_ctx.run_id, file_path=prep.rel, action="skipped",
            detail=f"lockfile_url_masking=failed check={failure}",
        ))
        return failure
    savepoint.commit()
    # Degerler degil, yalnizca sayilar loglanir.
    db.add(AuditLog(
        run_id=run_ctx.run_id, file_path=prep.rel, action="replaced",
        detail=f"final_state=READY final_output=written; scan_only lockfile_internal_urls_masked="
        f"{len(matches)}",
    ))
    return FileOutcome(
        prep.rel, status="masked", match_count=len(mappings),
        rule_breakdown={rule.rule_name: len(mappings)},
    )


# scan_only_clean bir outcome'u, FINAL SCAN_ONLY CONSISTENCY VERIFICATION'in
# bulgusuna gore geri alir: ciktidaki byte'lar SILINIR (asla degistirilmez/
# yeniden yazilmaz - bkz. modul basindaki SCAN_ONLY ilkesi), placeholder/
# mapping OLUSTURULMAZ, mevcut AuditWarning/AuditLog mekanizmasi kullanilir.
def _quarantine_scan_only_after_verification(
    db: Session, run_id: int, report: ExportReport, output_file: "_OutputFile",
    *, status: str, final_state: str, reason: str,
) -> None:
    rel = output_file.outcome.relative_path
    if output_file.path.is_file() or output_file.path.is_symlink():
        try:
            output_file.path.unlink()
        except OSError:
            pass
    db.add(
        AuditLog(
            run_id=run_id, file_path=rel,
            action="error" if final_state == "VALIDATION_FAILED" else "skipped",
            detail=f"final_state={final_state} final_output=blocked; {status}; final scan_only verification",
        )
    )
    _create_audit_warning(
        db, run_id=run_id, file_path=rel, masked_content="",
        encoding=output_file.encoding, reasoning=reason, audit_failed=True,
    )
    report.mark_scan_only_verification_result(output_file.outcome, status=status, final_state=final_state, error=reason)


# FINAL SCAN_ONLY CONSISTENCY VERIFICATION: Sensitive Value Registry TUM
# batch'ler bittikten sonra tamamen olustuktan sonra calisir - scan_only_clean
# (bagimlilik lock) dosyalarini, BASKA dosyalarda dogrulanmis hassas
# degerlere karsi SALT-OKUNUR sekilde tekrar tarar. find_consistency_
# occurrences() ile AYNI fonksiyon/boundary/canonicalization semantigi
# kullanilir (_run_consistency_pass'in kullandigi fonksiyonun BIREBIR
# AYNISI) - substring/identifier yanlis-pozitiflerine karsi ikinci bir kural
# seti icat edilmez. KRITIK: bu asama HICBIR ZAMAN maskeleme/replacement
# yapmaz - sadece dogrular. Bulgu (ya da okuma/dogrulama hatasi) varsa dosya
# ciktidan cikarilir (icerigi DEGISTIRILMEDEN), review/quarantine icin
# AuditWarning birakilir. Per-file SAVEPOINT + izolasyon: bir dosyadaki
# beklenmeyen hata SADECE onu etkiler, diger scan_only dosyalarini ya da
# calismanin geri kalanini coktermez.
def _run_scan_only_final_verification(
    db: Session,
    run_ctx: MaskingRunContext,
    registry: SensitiveValueRegistry,
    scan_only_output_files: list["_OutputFile"],
    report: ExportReport,
    max_inline_size: int,
) -> None:
    if len(registry) == 0 or not scan_only_output_files:
        return
    run_id = run_ctx.run_id
    for output_file in scan_only_output_files:
        rel = output_file.outcome.relative_path
        try:
            with db.begin_nested():
                text, encoding, read_error = _read_consistency_target(
                    output_file.path, max_inline_size, preferred_encoding=output_file.encoding,
                )
                if read_error is not None:
                    reason = (
                        "Final SCAN_ONLY doğrulaması dosyayı yeniden okuyamadı - 'taranamadı' hiçbir "
                        f"zaman 'temiz' sayılmaz, güvenlik gereği incelemeye alındı: {read_error}"
                    )
                    _quarantine_scan_only_after_verification(
                        db, run_id, report, output_file,
                        status="failed_detection", final_state="VALIDATION_FAILED", reason=reason,
                    )
                    continue
                if text is None:  # Confirmed binary: nothing left to verify.
                    continue

                occurrences = find_consistency_occurrences(text, registry, file_path=rel)
                if not occurrences:
                    db.add(
                        AuditLog(
                            run_id=run_id, file_path=rel, action="skipped",
                            detail="final_state=READY final_output=written; scan_only final verification: registry temiz",
                        )
                    )
                    continue

                reason = (
                    "Final SCAN_ONLY doğrulaması: bu bağımlılık/lock dosyasında, PROJENİN BAŞKA "
                    "dosyalarında doğrulanmış hassas değer(ler) bulundu; bütünlüğünü/imza geçerliliğini "
                    "bozmamak için maskelenmedi, çıktıdan çıkarıldı: "
                    + _consistency_occurrence_locations(text, occurrences)
                )
                _quarantine_scan_only_after_verification(
                    db, run_id, report, output_file,
                    status="scan_only_sensitive", final_state="SECURITY_QUARANTINE", reason=reason,
                )
        except Exception as exc:
            # Never log exc's message: bkz. modulun ustundeki ayni ilke.
            reason = (
                "Final SCAN_ONLY doğrulamasında beklenmeyen bir hata oluştu - güvenlik gereği "
                f"incelemeye alındı ({type(exc).__name__})"
            )
            _quarantine_scan_only_after_verification(
                db, run_id, report, output_file,
                status="failed_detection", final_state="VALIDATION_FAILED", reason=reason,
            )


# VLLM_AUDIT_UNCHANGED_FILES=false iken ikinci denetimin atlanabildigi dosya:
# hicbir katman metni degistirmedi ve LLM tespiti hatasiz tamamlandi. Boyle
# bir dosyada denetim, tespitin gordugu metnin aynisini tekrar okur.
def _audit_skippable(masked_file: _MaskedFile) -> bool:
    return (
        not settings.vllm.audit_unchanged_files
        and masked_file.masked_text == masked_file.prep.text
        and not masked_file.llm_errors
        and not masked_file.detector_crashes
        and not masked_file.encoded_leaks
    )


async def _audit_masked_file(masked_file: _MaskedFile) -> "AuditVerdict | LLMRecognitionError":
    if _audit_skippable(masked_file):
        return AuditVerdict(risky=False)
    return await _audit_one(masked_file.masked_text, masked_file.prep.rel)


# Maskelenmis bir metni post-mask LLM denetiminden gecirir; LLM hatasini deger olarak dondurur (raise etmez).
async def _audit_one(masked_text: str, file_path: str = "") -> "AuditVerdict | LLMRecognitionError":
    with llm_file_context(file_path):
        try:
            return await audit_masked_text(masked_text, settings.vllm)
        except LLMRecognitionError as exc:
            return exc
        except Exception as exc:
            # Isolate unexpected failures to this file, without logging content.
            return LLMRecognitionError(
                f"ikincil denetim beklenmeyen bir hatayla durdu ({type(exc).__name__})"
            )


# Faz D (sirali, DB+dosya yazan): post-mask audit sonucuna gore karantina
# karari verir, gecerse sozdizimi dogrulamasindan gecirir, hedefe yazar.
def _finalize_file(
    db: Session,
    run_id: int,
    masked_file: _MaskedFile,
    audit_result: "AuditVerdict | LLMRecognitionError | None",
    failed_dir: Path,
    validation_warnings: list[str] | None = None,
    decision_policy: LearnedDecisionPolicy | None = None,
    allow_remediation: bool = False,
    remediation_note: str | None = None,
    syntax_failure_action: str | None = None,
) -> "FileOutcome | _RemediationRequest":
    prep = masked_file.prep

    # Otomatik duzeltme denenip basarisiz olduysa (bkz. _fallback_after_remediation)
    # insan onayina dusen her uyarinin gerekcesine hangi kontrolde kaldigi eklenir.
    def _warn(**kwargs) -> None:
        if remediation_note:
            kwargs["reasoning"] = f"{kwargs['reasoning']}\n({remediation_note})"
        _create_audit_warning(db, **kwargs)

    # Herhangi bir detector katmani (Katman 1/2/3, hangisi olursa olsun) bu
    # dosya icin BEKLENMEYEN bir hatayla coktu mu? (bkz. detectors.py
    # DetectionOrchestrator.scan - tek bir dosyanin tespit hatasi artik TUM
    # calismayi (asyncio.gather) coktermiyor, bunun yerine buraya kadar
    # tasiniyor.) settings.vllm.enabled durumundan KASITLI olarak BAGIMSIZ:
    # Katman 1/2 LLM kapaliyken de calisir, o yuzden onlarin cokmesi HER
    # ZAMAN karantina gerektirir - llm_errors'un aksine burada bir "bilerek
    # kapali" istisnasi YOK.
    if masked_file.detector_crashes:
        reason = (
            "Bir tespit katmani bu dosya icin beklenmeyen bir hatayla durdu - kapsamin "
            "tam oldugu garanti edilemez, guvenlik geregi karantinaya alindi: "
            + "; ".join(masked_file.detector_crashes)
        )
        _warn(
            run_id=run_id, file_path=prep.rel, masked_content=masked_file.masked_text,
            encoding=prep.encoding, output_path=prep.masked_rel, reasoning=reason, audit_failed=True,
        )
        db.add(
            AuditLog(
                run_id=run_id, file_path=prep.rel, action="error",
                detail="final_state=VALIDATION_FAILED final_output=blocked; detector katmani coktu",
            )
        )
        return FileOutcome(
            prep.rel, status="quarantined_pending_audit", match_count=masked_file.match_count,
            rule_breakdown=masked_file.rule_breakdown, error=reason, final_state="VALIDATION_FAILED",
            failed_check="tespit_katmani",
        )

    # Base64/hex/bayt dizisiyle kodlanmis bir metnin icinde parola, connection
    # string, IP ya da kurum terimi bulundu (bkz. encoded_text_detector). Deger
    # kodlanmis blok icinde maskelenmez: yeniden kodlanan metin "geri alinca
    # birebir ayni dosya" garantisini karmasiklastirir. Dosya disari verilmez.
    if masked_file.encoded_leaks:
        reason = _encoded_leak_reason(masked_file.encoded_leaks)
        _warn(
            run_id=run_id, file_path=prep.rel, masked_content=masked_file.masked_text,
            encoding=prep.encoding, output_path=prep.masked_rel, reasoning=reason, audit_failed=False,
        )
        db.add(AuditLog(
            run_id=run_id, file_path=prep.rel, action="skipped",
            detail="final_state=SECURITY_QUARANTINE final_output=blocked; kodlanmis metinde hassas veri",
        ))
        return FileOutcome(
            prep.rel, status="quarantined_pending_audit", match_count=masked_file.match_count,
            rule_breakdown=masked_file.rule_breakdown, error=reason, final_state="SECURITY_QUARANTINE",
            failed_check="kodlanmis_veri",
        )

    # Katman 3 (LLM) tespiti bu dosya icin basarisiz oldu mu? (bkz.
    # _apply_masking - outcome.llm_errors buraya kadar tasindi). Bu kontrol
    # asagidaki post-mask audit sonucundan BAGIMSIZDIR ve ONA GUVENMEZ:
    # audit AYRI bir vLLM cagrisidir, tespit cagrisi basarisiz olsa bile
    # (orn. gecici bir tekil istek hatasi) audit farkli bir sonuc
    # dondurebilir - "detector basarisiz oldu ama dosya yine de basarili
    # sayildi" durumunu KESIN olarak kapatmak icin bu sinyal DOGRUDAN
    # kontrol edilir. settings.vllm.enabled=False iken (LLM katmani zaten
    # tamamen kapaliyken) bu kontrol atlanir - o zaten beklenen/yapilandirilmis bir durumdur.
    if masked_file.llm_errors and settings.vllm.enabled:
        reason = (
            "LLM tespit katmani bu dosya icin basarisiz oldu - bu katman calismadan dosyanin "
            "tam kapsamli tarandigi garanti edilemez, guvenlik geregi karantinaya alindi: "
            + "; ".join(masked_file.llm_errors)
        )
        _warn(
            run_id=run_id, file_path=prep.rel, masked_content=masked_file.masked_text,
            encoding=prep.encoding, output_path=prep.masked_rel, reasoning=reason, audit_failed=True,
        )
        db.add(
            AuditLog(
                run_id=run_id, file_path=prep.rel, action="error",
                detail="ai_result=error final_state=VALIDATION_FAILED final_output=blocked; LLM tespit katmani tamamlanamadi",
            )
        )
        return FileOutcome(
            prep.rel, status="quarantined_pending_audit", match_count=masked_file.match_count,
            rule_breakdown=masked_file.rule_breakdown, error=reason, final_state="VALIDATION_FAILED",
            failed_check="llm_tespit",
        )

    if masked_file.review_results:
        reason = (
            f"INCELEME_GEREKLI: Bu dosyada {len(masked_file.review_results)} düşük/orta güvenli AI "
            "bulgusu kullanıcı kararı bekliyor. Kararlar tamamlanınca dosya otomatik yeniden doğrulanacaktır."
        )
        _warn(
            run_id=run_id, file_path=prep.rel, masked_content=masked_file.masked_text,
            encoding=prep.encoding, output_path=prep.masked_rel, reasoning=reason, audit_failed=False,
        )
        db.add(AuditLog(
            run_id=run_id, file_path=prep.rel, action="skipped",
            detail="final_state=REVIEW_REQUIRED final_output=blocked",
        ))
        return FileOutcome(
            prep.rel, status="quarantined_pending_audit", match_count=masked_file.match_count,
            rule_breakdown=masked_file.rule_breakdown, error=reason, final_state="REVIEW_REQUIRED",
            failed_check="inceleme",
        )

    if audit_result is None:
        audit_result = LLMRecognitionError("Gerekli LLM denetim sonucu eksik")

    # Kullanicinin ayni kapsamda "hassas degil" dedigi degerler (ogrenilmis
    # suppression) denetim bulgularindan dusulur - serbest birakma adimindaki
    # kuralin aynisi (audit_warning_service._final_pass). Alintisiz "risk var"
    # karari bastirilamaz; bulgu kalirsa dosya yine onaya duser.
    if (isinstance(audit_result, AuditVerdict) and audit_result.risky and audit_result.findings
            and decision_policy is not None):
        remaining = [
            finding for finding in audit_result.findings
            if decision_policy.suppression_covering(
                finding.ilgili_bolum, prep.rel, masked_file.masked_text,
            ) is None
        ]
        suppressed = len(audit_result.findings) - len(remaining)
        if suppressed:
            # Degerin kendisi degil, yalnizca sayilar loglanir.
            db.add(AuditLog(
                run_id=run_id, file_path=prep.rel, action="skipped",
                detail=f"ai_result=risky learned_suppression suppressed_findings={suppressed} "
                f"remaining_findings={len(remaining)}",
            ))
            audit_result = AuditVerdict(risky=bool(remaining), findings=remaining)

    # Terim sozlugu son kontrolu: ana tespitten bagimsiz ikinci bir tarama - burada
    # hala eslesme varsa kesin sinyaldir, LLM denetimi beklenmeden dosya reddedilir.
    leaked_terms = find_leaked_terms(db, masked_file.masked_text)

    # Sizan deger kesin biliniyorsa (acik terim ya da metinde birebir
    # dogrulanmis denetim alintisi) once otomatik duzeltme denenir; caller
    # duzeltilen metni bu fonksiyondan (tum son kontrollerle) tekrar gecirir.
    # Denetim tamamlanamadiysa ya da alintisiz "risk var" dediyse duzeltme
    # denenmez - bu dosyalar her zaman insan onayina gider.
    if allow_remediation and isinstance(audit_result, AuditVerdict) and prep.class_document is None:
        findings = list(audit_result.findings) if audit_result.risky else []
        if (leaked_terms or findings) and not (audit_result.risky and not findings):
            return _RemediationRequest(leaked_terms=leaked_terms, findings=findings)

    if leaked_terms:
        # Acik deger gerekceye yazilmaz (denetim_uyarilari sifrelenmez); inceleme
        # ekrani degeri konumdan, maskelenmis icerik uzerinde canli cozer
        # (bkz. audit_warning_details.describe_audit_warning).
        leaked_summary = "\n".join(
            f"Satır {t.line_number}, sütun {t.column_number}: {t.category} ({t.rule_name})"
            for t in leaked_terms[:20]
        )
        reason = (
            f"Kurumsal terim kontrolü: {len(leaked_terms)} açık eşleşme kaldı. "
            f"Dosya çıktı klasörüne alınmadı.\n{leaked_summary}"
        )
        if len(leaked_terms) > 20:
            reason += f"\nİlk 20 eşleşme gösteriliyor; toplam {len(leaked_terms)}."
        _warn(
            run_id=run_id, file_path=prep.rel, masked_content=masked_file.masked_text,
            encoding=prep.encoding, output_path=prep.masked_rel, reasoning=reason, audit_failed=False,
        )
        db.add(
            AuditLog(
                run_id=run_id, file_path=prep.rel, action="skipped",
                detail="final_state=SECURITY_QUARANTINE final_output=blocked; terim sozlugu son kontrolunde acik eslesme kaldi",
            )
        )
        return FileOutcome(
            prep.rel, status="quarantined_pending_audit", match_count=masked_file.match_count,
            rule_breakdown=masked_file.rule_breakdown, error=reason, final_state="SECURITY_QUARANTINE",
            failed_check="acik_terim",
        )

    if isinstance(audit_result, LLMRecognitionError):
        reason = (
            "Ikincil denetim (LLM) cagrisi basarisiz oldu ya da zaman asimina ugradi, otomatik "
            f"dogrulama yapilamadi - guvenlik geregi dosya karantinaya alindi: {audit_result}"
        )
        _warn(
            run_id=run_id, file_path=prep.rel, masked_content=masked_file.masked_text,
            encoding=prep.encoding, output_path=prep.masked_rel, reasoning=reason, audit_failed=True,
        )
        db.add(
            AuditLog(
                run_id=run_id, file_path=prep.rel, action="error",
                detail="ai_result=error final_state=VALIDATION_FAILED final_output=blocked; ikincil denetim tamamlanamadi",
            )
        )
        return FileOutcome(
            prep.rel, status="quarantined_pending_audit", match_count=masked_file.match_count,
            rule_breakdown=masked_file.rule_breakdown, error=reason, final_state="VALIDATION_FAILED",
            failed_check="llm_denetimi_tamamlanamadi",
        )

    if audit_result.risky:
        # Ikincil risk bulundu: mapping'ler DB'de kalir ama dosya hedefe yazilmaz - insan onayi bekler.
        reason = audit_result.reasoning_text()
        _warn(
            run_id=run_id, file_path=prep.rel, masked_content=masked_file.masked_text,
            encoding=prep.encoding, output_path=prep.masked_rel, reasoning=reason, audit_failed=False,
        )
        db.add(
            AuditLog(
                run_id=run_id, file_path=prep.rel, action="skipped",
                detail="ai_result=risky ai_confidence=not_provided final_state=SECURITY_QUARANTINE final_output=blocked",
            )
        )
        return FileOutcome(
            prep.rel, status="quarantined_pending_audit", match_count=masked_file.match_count,
            rule_breakdown=masked_file.rule_breakdown, error=reason, final_state="SECURITY_QUARANTINE",
            failed_check="llm_denetimi",
        )

    # Son guvenlik agi: maskeleme sozdizimini BOZDU mu? (kaynak zaten bozuksa buradan gecer)
    syntax_error = _validate_and_log_syntax(
        db, run_id, prep.rel, masked_file.masked_text, prep.text, validation_warnings,
    )
    # warn modu: bu noktaya gelen dosya tum gizlilik kontrollerinden (acik terim,
    # LLM denetimi) gecti; sozdizimi hatasi uyariyla kaydedilip dosya yazilir.
    # Java .class her modda bloklar (bozuk sabit havuzu yeniden kurulamaz).
    if (syntax_error is not None and prep.class_document is None
            and (syntax_failure_action or settings.validation.syntax_failure_action) == "warn"):
        _record_syntax_warning(db, run_id, prep.rel, syntax_error, validation_warnings)
        syntax_error = None
    if syntax_error is not None:
        _write_to_failed_files_dir(failed_dir, _failed_rel(prep), masked_file.masked_text, prep.encoding)
        db.add(
            AuditLog(
                run_id=run_id, file_path=prep.rel, action="sozdizimi_hatasi",
                detail=f"final_state=VALIDATION_FAILED final_output=blocked; Maskeleme sozdizimini bozdu, disa aktarilmadi: {syntax_error}",
            )
        )
        _warn(
            run_id=run_id, file_path=prep.rel, masked_content=masked_file.masked_text,
            encoding=prep.encoding, output_path=prep.masked_rel, reasoning=f"Sözdizimi doğrulaması başarısız: {syntax_error}",
            audit_failed=True,
        )
        return FileOutcome(
            prep.rel, status="failed_syntax_validation", match_count=masked_file.match_count,
            rule_breakdown=masked_file.rule_breakdown, error=syntax_error,
            failed_check="sozdizimi",
        )

    if prep.class_document is not None:
        try:
            prep.class_document.rebuild(masked_file.masked_text)
        except ClassFormatError as exc:
            reason = str(exc)
            _write_to_failed_files_dir(failed_dir, _failed_rel(prep),
                                      masked_file.masked_text, prep.encoding)
            _warn(run_id=run_id, file_path=prep.rel,
                  masked_content=masked_file.masked_text, encoding=prep.encoding,
                  output_path=prep.masked_rel, reasoning=reason, audit_failed=True)
            db.add(AuditLog(run_id=run_id, file_path=prep.rel, action="error",
                            detail=f"final_state=VALIDATION_FAILED final_output=blocked; {reason}"))
            return FileOutcome(prep.rel, failed_check="sozdizimi", status="failed_syntax_validation",
                               match_count=masked_file.match_count,
                               rule_breakdown=masked_file.rule_breakdown, error=reason)
        notice = f"{prep.rel}: {CLASS_COVERAGE}"
        if validation_warnings is not None and notice not in validation_warnings:
            validation_warnings.append(notice)
        db.add(AuditLog(run_id=run_id, file_path=prep.rel, action="skipped",
                        detail=f"validation_warning; {CLASS_COVERAGE}"))

    try:
        if prep.class_document is not None:
            write_text_preserving_encoding(prep.dest_path, masked_file.masked_text, prep.encoding,
                                           class_document=prep.class_document)
        else:
            write_text_preserving_encoding(prep.dest_path, masked_file.masked_text, prep.encoding)
    except OSError as exc:
        detail = describe_file_error("yazma", prep.dest_path, exc)
        db.add(AuditLog(run_id=run_id, file_path=prep.rel, action="error", detail=detail))
        _warn(
            run_id=run_id, file_path=prep.rel, masked_content=masked_file.masked_text,
            encoding=prep.encoding, output_path=prep.masked_rel, reasoning=f"Doğrulanmış çıktı yazılamadı: {detail}", audit_failed=True,
        )
        return FileOutcome(prep.rel, failed_check="yazma", status="error", error=detail)

    # match_count==0 (hicbir sey maskelenmedi) burada tamamen gecerli bir
    # sonuctur - dosya secilen katmanlardan ve gerekli dogrulamalardan TEMIZ
    # gecti demektir (bkz. _apply_masking). Rapor semantigi korunur: bu
    # durumda "masked" degil "copied_text_no_match" olarak sayilir.
    status = "masked" if masked_file.match_count > 0 else "copied_text_no_match"
    db.add(AuditLog(
        run_id=run_id, file_path=prep.rel, action="skipped",
        detail="final_state=READY security_validation=passed final_output=written",
    ))
    return FileOutcome(
        prep.rel, status=status, match_count=masked_file.match_count, rule_breakdown=masked_file.rule_breakdown
    )


# Otomatik duzeltme sinirlari: denetim alintisi bu tiple maskelenir; en fazla
# 2 duzeltme turu (her tur bir LLM denetimi daha) - sonsuz dongu olmaz.
_AUDIT_REMEDIATION_TYPE = "DENETIM_BULGUSU"
_MAX_REMEDIATION_ROUNDS = 2
# Bundan uzun ya da alt satira gecen denetim alintisi otomatik maskelenmez,
# insan onayina gider (buyuk bir parcayi tek yer tutucuyla ortmek riskli).
_MAX_REMEDIATION_QUOTE_CHARS = 200
# Otomatik duzeltme basarisizliginda insan onayi gerekcesine yazilan kontrol adlari.
_REMEDIATION_CHECK_LABELS = {
    "kural": "sözlük kuralı bulunamadı",
    "maskeleme": "değerlerin güvenli sınırla maskelenmesi",
    "daraltma": "alıntının bir kısmı açık kalacaktı",
    "cok_satirli_alinti": "alıntı birden fazla satıra yayılıyor",
    "uzun_alinti": f"alıntı {_MAX_REMEDIATION_QUOTE_CHARS} karakterden uzun",
    "geri_donus": "geri dönüş doğrulaması",
    "sozdizimi": "sözdizimi doğrulaması",
    "acik_terim": "açık terim kontrolü",
    "llm_denetimi": "LLM denetimi",
    "llm_denetimi_tamamlanamadi": "LLM denetimi tamamlanamadı",
    "yazma": "çıktı yazma",
}


def _placeholder_reverse_map(db: Session, run_ctx: MaskingRunContext, text: str) -> dict[str, str]:
    """Reverse map for exactly the placeholders present in `text`."""
    tokens = {m.group(0) for pattern in (PLACEHOLDER_RE, JSON_NUMERIC_PLACEHOLDER_RE) for m in pattern.finditer(text)}
    reverse_map: dict[str, str] = {}
    if tokens:
        rows = db.execute(
            select(ValueMapping.placeholder_value, ValueMapping.original_value_encrypted).where(
                ValueMapping.context_id == run_ctx.context.id,
                ValueMapping.run_id == mapping_scope_for_run(db, run_ctx.run_id),
                ValueMapping.placeholder_value.in_(sorted(tokens)),
            )
        )
        reverse_map = {placeholder: decrypt_value(encrypted) for placeholder, encrypted in rows}
    _register_passthrough_placeholders(reverse_map, text)
    return reverse_map


# Talepteki degerleri maskeler ve senkron son kontrolleri (geri donus + acik
# terim + sozdizimi) calistirir. Kendi SAVEPOINT'inde calisir: basarisizlikta
# olusturulan eslemeler ve onbellek geri alinir, basarisiz kontrolun kodu
# doner. LLM denetimi ve dosyanin tamami caller'da _finalize_file ile tekrar
# denetlenir; buradaki kontroller yalnizca bosuna bir LLM cagrisini onler.
def _try_remediation(
    db: Session,
    run_ctx: MaskingRunContext,
    masked_file: _MaskedFile,
    request: _RemediationRequest,
    rules_by_name: dict[str, object],
    rule_names_by_id: dict[int, str],
) -> "tuple[_MaskedFile, list, list] | str":
    prep = masked_file.prep
    values = []
    for term in request.leaked_terms:
        rule = rules_by_name.get(term.rule_name)
        if rule is None:
            return "kural"
        values.append((term.matched_value, rule, "dictionary"))
    for finding in request.findings:
        if "\n" in finding.ilgili_bolum or "\r" in finding.ilgili_bolum:
            return "cok_satirli_alinti"
        if len(finding.ilgili_bolum) > _MAX_REMEDIATION_QUOTE_CHARS:
            return "uzun_alinti"
    audit_rule = synthetic_llm_rule(_AUDIT_REMEDIATION_TYPE)
    values.extend((finding.ilgili_bolum, audit_rule, "llm") for finding in request.findings)

    cache = run_ctx.mapping_cache.mappings if run_ctx.mapping_cache is not None else None
    cache_snapshot = _snapshot_dict(cache) if cache is not None else None
    savepoint = db.begin_nested()
    try:
        failure = None
        try:
            text, matches, mappings = mask_known_values(
                db, run_ctx, masked_file.masked_text, prep.rel, values, reject_narrowed_content=True,
            )
        except NarrowedValueError:
            # Daraltilan alintinin acik kalacak kismini yakalayacak tek sey
            # ikinci LLM denetimi olurdu; bu belirsiz kontrole birakilmaz.
            failure = "daraltma"
        except ValueError:
            failure = "maskeleme"
        if failure is None:
            reverse_map = _placeholder_reverse_map(db, run_ctx, text)
            if not verify_round_trip(prep.text, text, reverse_map).ok:
                failure = "geri_donus"
        if failure is None and find_leaked_terms(db, text):
            failure = "acik_terim"
        if failure is None and validate_masked_syntax(
            prep.rel, text, original_text=prep.text, sql_dialect=settings.validation.sql_dialect,
        ):
            failure = "sozdizimi"
    except BaseException:
        savepoint.rollback()
        if cache is not None:
            _restore_dict(cache, cache_snapshot)
        raise
    if failure is not None:
        savepoint.rollback()
        if cache is not None:
            _restore_dict(cache, cache_snapshot)
        return failure
    savepoint.commit()

    rule_breakdown = dict(masked_file.rule_breakdown)
    for rule_name, count in _rule_breakdown(mappings, rule_names_by_id).items():
        rule_breakdown[rule_name] = rule_breakdown.get(rule_name, 0) + count
    remediated = _MaskedFile(
        prep=prep, masked_text=text, rule_breakdown=rule_breakdown,
        match_count=masked_file.match_count + len(mappings),
    )
    return remediated, matches, mappings


# Otomatik duzeltme basarisiz: duzeltme-oncesi metin ve denetim sonucuyla
# mevcut davranisa (insan onayi) donulur; gerekceye hangi kontrolde
# kalindigi eklenir. Log'a yalnizca kontrol kodu yazilir.
def _fallback_after_remediation(
    db: Session,
    run_id: int,
    masked_file: _MaskedFile,
    audit_result,
    failed_dir: Path,
    validation_warnings: list[str] | None,
    decision_policy: LearnedDecisionPolicy | None,
    check: str,
) -> FileOutcome:
    db.add(AuditLog(
        run_id=run_id, file_path=masked_file.prep.rel, action="skipped",
        detail=f"auto_remediation=failed check={check}",
    ))
    label = _REMEDIATION_CHECK_LABELS.get(check, check)
    return _finalize_file(
        db, run_id, masked_file, audit_result, failed_dir, validation_warnings,
        decision_policy=decision_policy,
        remediation_note=f"otomatik düzeltme denendi, şu kontrolde başarısız oldu: {label}",
    )


# Faz D'de _finalize_file duzeltme talebi dondurdu: ilk turu dener.
# Basarida LLM denetimini bekleyen bir _PendingRemediation, aksi halde
# insan onayina dusen FileOutcome doner.
def _start_remediation(
    db: Session,
    run_ctx: MaskingRunContext,
    masked_file: _MaskedFile,
    audit_result,
    request: _RemediationRequest,
    failed_dir: Path,
    validation_warnings: list[str] | None,
    decision_policy: LearnedDecisionPolicy | None,
    rules_by_name: dict[str, object],
    rule_names_by_id: dict[int, str],
) -> "FileOutcome | _PendingRemediation":
    result = _try_remediation(db, run_ctx, masked_file, request, rules_by_name, rule_names_by_id)
    if isinstance(result, str):
        return _fallback_after_remediation(
            db, run_ctx.run_id, masked_file, audit_result, failed_dir, validation_warnings, decision_policy, result,
        )
    remediated, matches, mappings = result
    return _PendingRemediation(
        original=masked_file, original_audit=audit_result, current=remediated, rounds=1,
        matches=list(matches), mappings=list(mappings),
    )


# Faz E: duzeltilmis dosyanin yeni LLM denetimi geldi. Dosya _finalize_file'in
# TUM son kontrollerinden (acik terim + denetim + sozdizimi + yazma) tekrar
# gecer. Yeni dogrulanmis bulgu varsa ve tur siniri dolmadiysa bir tur daha
# denenir; READY olursa degerler tutarlilik registry'sine eklenir. Aksi halde
# bu denemenin DB yazmalari geri alinir ve insan onayina donulur.
def _continue_remediation(
    db: Session,
    run_ctx: MaskingRunContext,
    item: _PendingRemediation,
    audit_result,
    failed_dir: Path,
    validation_warnings: list[str] | None,
    decision_policy: LearnedDecisionPolicy | None,
    rules_by_name: dict[str, object],
    rule_names_by_id: dict[int, str],
    consistency_registry: SensitiveValueRegistry,
) -> "FileOutcome | _PendingRemediation":
    prep = item.current.prep
    cache = run_ctx.mapping_cache.mappings if run_ctx.mapping_cache is not None else None
    cache_snapshot = _snapshot_dict(cache) if cache is not None else None
    failed_path = failed_dir / _failed_rel(prep)
    had_failed_file = failed_path.exists()
    savepoint = db.begin_nested()
    try:
        outcome = _finalize_file(
            db, run_ctx.run_id, item.current, audit_result, failed_dir, validation_warnings,
            decision_policy=decision_policy, allow_remediation=item.rounds < _MAX_REMEDIATION_ROUNDS,
            # Otomatik duzeltmeden sonra cikan sozdizimi hatasi yanlis bir seyin
            # maskelendigine isaret edebilir: warn modunda da onaya duser.
            syntax_failure_action="block",
        )
        if isinstance(outcome, _RemediationRequest):
            result = _try_remediation(db, run_ctx, item.current, outcome, rules_by_name, rule_names_by_id)
            if not isinstance(result, str):
                savepoint.commit()
                remediated, matches, mappings = result
                return _PendingRemediation(
                    original=item.original, original_audit=item.original_audit, current=remediated,
                    rounds=item.rounds + 1, matches=item.matches + list(matches),
                    mappings=item.mappings + list(mappings),
                )
            check = result
        elif outcome.final_state == "READY":
            savepoint.commit()
            consistency_registry.add_successful_matches(item.matches, item.mappings)
            # Degerler degil, yalnizca sayilar loglanir.
            db.add(AuditLog(
                run_id=run_ctx.run_id, file_path=prep.rel, action="replaced",
                detail=f"auto_remediated rounds={item.rounds} masked_values={len(item.mappings)}",
            ))
            return outcome
        else:
            check = outcome.failed_check or "beklenmeyen"
    except BaseException:
        savepoint.rollback()
        if cache is not None:
            _restore_dict(cache, cache_snapshot)
        raise
    savepoint.rollback()
    if cache is not None:
        _restore_dict(cache, cache_snapshot)
    # Duzeltilmis metnin basarisiz_dosyalar/ kopyasi insan onayindaki
    # (duzeltme-oncesi) hal ile karismasin.
    if not had_failed_file and failed_path.is_file():
        failed_path.unlink()
    return _fallback_after_remediation(
        db, run_ctx.run_id, item.original, item.original_audit, failed_dir, validation_warnings,
        decision_policy, check,
    )


# _finalize_file (Faz D: audit karari + terim son kontrolu + sozdizimi +
# hedefe yazma) icinde BEKLENMEYEN bir istisna olustugunda cagrilir - bu tek
# dosyayi karantinaya alir ve DB tarafini tutarli birakir; caller'daki
# SAVEPOINT (db.begin_nested()) bu fonksiyon cagrilmadan ONCE zaten o dosya
# icin yapilan yarim/sirali DB yazmalarini geri almis olur (bkz. cagri yeri).
# Yazma adimi _finalize_file icinde SIRALAMA geregi HER ZAMAN EN SON
# calistigi icin (audit/terim/sozdizimi kontrollerinin hepsinden SONRA),
# fiziksel dosya sadece o adimdan SONRAKI (pratikte imkansiza yakin, ama
# yine de savunulan) bir cokmede diskte kalmis olabilir - byle bir durumda
# guvenilmez/yari-yazilmis dosyanin hedef pakete sessizce sizmasini onlemek
# icin acikca silinir.
def _quarantine_after_finalize_crash(
    db: Session, run_id: int, prep: "_FilePrep", exc: BaseException
) -> FileOutcome:
    # Never log exc's message: Faz D butunuyle GERCEK dosya icerigi
    # uzerinde calisir (audit/sozdizimi/yazma), mesaj bir icerik parcasini
    # yansitabilir (bkz. modulun ustundeki ayni ilke - _detect_for_prep).
    reason = (
        "Denetim/sozdizimi/yazma asamasinda (Faz D) beklenmeyen bir hata olustu - bu dosyanin "
        f"guvenli sekilde tamamlandigi garanti edilemez ({type(exc).__name__}), guvenlik geregi "
        "karantinaya alindi"
    )
    if prep.dest_path.is_file() or prep.dest_path.is_symlink():
        try:
            prep.dest_path.unlink()
        except OSError:
            pass
    db.add(
        AuditLog(
            run_id=run_id, file_path=prep.rel, action="error",
            detail="final_state=VALIDATION_FAILED final_output=blocked; Faz D (denetim/sozdizimi/yazma) coktu",
        )
    )
    _create_audit_warning(
        db, run_id=run_id, file_path=prep.rel, masked_content="",
        encoding=prep.encoding, output_path=prep.masked_rel, reasoning=reason, audit_failed=True,
    )
    return FileOutcome(
        prep.rel, status="quarantined_pending_audit", error=reason, final_state="VALIDATION_FAILED",
    )


def _acquire_write_lock(db: Session, run_id: int) -> None:
    """Start the next write transaction with a write, not a read.

    SQLite/WAL: a transaction that first reads and later writes cannot be
    upgraded if another connection committed in between (SQLITE_BUSY_SNAPSHOT,
    busy_timeout does not help). Ara commit'lerden sonra her yazma fazi bu
    no-op UPDATE ile baslar; kilit busy_timeout ile beklenerek alinir.
    """
    db.execute(sql_text("UPDATE maskeleme_calismalari SET durum = durum WHERE id = :id"), {"id": run_id})


def _mark_run_failed(db: Session, run_id: int) -> None:
    """Ara commit'lerden sonra kalici olan run'i 'failed' yapar.

    Aksi halde 'in_progress' kalan run ayni proje/sicil/branch icin yeni
    export'lari engellerdi. Bu adim basarisiz olursa asil hata gizlenmez;
    run recover-output ile kapatilabilir.
    """
    try:
        row = db.get(MaskingRun, run_id)
        if row is None or row.status != "in_progress":
            return
        row.status = "failed"
        row.completed_at = datetime.now(timezone.utc)
        db.add(AuditLog(
            run_id=run_id, file_path="", action="error",
            detail="Export yarida kesildi; cikti yayimlanmadi, onceki hedef korundu.",
        ))
        db.commit()
    except Exception:
        db.rollback()


# Export akisinin ana giris noktasi: bir proje klasorunu tarar, her dosyayi
# maskeler, hedefe kopyalar ve ozet bir rapor uretir. Dosyalar batch'ler
# halinde islenir; tespit/denetim es zamanli, DB yazma adimlari siralidir.
async def export_project(
    db: Session,
    *,
    source_path: str,
    project_name: str,
    sicil_no: str,
    branch_name: str,
    target_path: str,
    initiated_by: str,
    max_inline_size: int = DEFAULT_MAX_INLINE_SIZE,
    enable_path_masking: bool = DEFAULT_ENABLE_PATH_MASKING,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> ExportReport:
    source = Path(source_path).resolve()
    target = Path(target_path).resolve()
    _validate_paths(source, target)
    if (source / MANIFEST_NAME).exists():
        raise ExportValidationError("Kaynakta maskeleme butunluk kaydi var; yeniden maskeleme icin orijinal projeyi kullanin.")

    context = get_or_create_context(db, project_name, sicil_no, branch_name)
    _ensure_no_in_progress_run(db, context.id)

    run = MaskingRun(
        context_id=context.id,
        mapping_version=2,
        operation_type="mask",
        source_path=str(source),
        target_path=str(target),
        initiated_by=initiated_by,
        status="in_progress",
    )
    db.add(run)
    try:
        db.flush()
    except IntegrityError as exc:
        # Asil race-kapatma noktasi: DB'deki UNIQUE index bu INSERT'i reddeder -
        # ayni context icin iki eszamanli export ASLA ikisi de basarili olamaz.
        db.rollback()
        raise ExportInProgressError(
            "bu proje/sicil/branch icin zaten devam eden export var (eszamanli istek tespit edildi)"
        ) from exc
    except OperationalError as exc:
        # SQLite kilit modeli geregi bazen IntegrityError yerine "database is
        # locked" hatasi gelir - kullaniciya ayni temiz mesaj gosterilir.
        db.rollback()
        if "database is locked" in str(exc).lower():
            raise ExportInProgressError(
                "bu proje/sicil/branch icin zaten devam eden export var (eszamanli istek tespit edildi)"
            ) from exc
        raise

    report = ExportReport(
        run_id=run.id,
        context_id=context.id,
        project_name=project_name,
        sicil_no=sicil_no,
        branch_name=branch_name,
        source_path=str(source),
        target_path=str(target),
        started_at=datetime.now(timezone.utc),
    )

    failed = False
    publication = None
    next_detection: asyncio.Future | None = None
    # Ara commit'ler (asagida) sonrasi mapping onbellegindeki ORM nesneleri
    # her erisimde yeniden SELECT'lenmesin diye; cikista eski deger geri yuklenir.
    previous_expire_on_commit = db.expire_on_commit
    db.expire_on_commit = False
    try:
        active_rules = load_active_rules(db)
        rule_names_by_id = {r.id: r.rule_name for r in active_rules}
        rules_by_name = {r.rule_name: r for r in active_rules}
        exclude_specs = load_active_exclude_specs(db)
        runtime_params = {
            "project_name": project_name,
            "sicil_no": sicil_no,
            "branch_name": branch_name,
        }
        # Bir kere kurulur, tum dosyalar icin yeniden kullanilir (pahali kurulum).
        active_presidio_rules = load_active_presidio_rules(db)
        category_restrictions = load_file_category_restrictions(db)
        decision_policy = LearnedDecisionPolicy.load(db, context.id)
        orchestrator = build_orchestrator(
            active_rules, runtime_params, active_presidio_rules, category_restrictions,
            decision_policy=decision_policy,
        )
        # Bir detector katmani (bkz. PresidioDetector.is_degraded) bu
        # calisma icin dusuk-kapasiteli fallback moda gectiyse, bunu
        # SESSIZCE gecmeyiz: run-genelinde bir AuditLog kaydi eklenir ve
        # rapora yansir (bkz. asagida final_status hesaplamasi) - "detector
        # basarisiz oldu ama calisma yine de basarili sayildi" ilkesi ihlali.
        # getattr ile savunmali: test'lerdeki sahte orchestrator'larin
        # (bkz. tests/test_export_llm_concurrency.py) registry'si olmayabilir.
        _registry = getattr(orchestrator, "registry", None)
        degraded_detectors = [
            detector.name for detector in (_registry.enabled_detectors() if _registry is not None else [])
            if getattr(detector, "is_degraded", False)
        ]
        report.degraded_detectors = degraded_detectors
        if degraded_detectors:
            db.add(
                AuditLog(
                    run_id=run.id,
                    file_path="",
                    # AuditLog.action bir DB CHECK constraint'iyle (ck_audit_log_action)
                    # sabit bir kume ile sinirli - "uyari" gecerli degil, en
                    # yakin anlamli deger "error" (bu detector katmani icin
                    # gercekten bir kurulum hatasi/basarisizlik yasandi).
                    action="error",
                    detail=(
                        f"Su detector katman(lar)i bu calisma boyunca dusuk-kapasiteli (fallback) "
                        f"modda calisti, tespit kapsami eksik olabilir: {', '.join(degraded_detectors)}"
                    ),
                )
            )
        # VLLM_ENABLED=false: Katman 3 (LLM) bu calisma icin BASTAN BERI
        # bilerek kapali - find_llm_detections() her dosyada sessizce bos
        # sonuc donuyor (bkz. llm_recognizer.py). Bu bir kurulum hatasi
        # DEGIL (yukaridaki degraded_detectors'tan BILEREK ayri tutulur -
        # bkz. ExportReport.llm_disabled dokstring'i), ama kullaniciya HER
        # ZAMAN acikca gosterilmeli - operatorun VLLM'i kapattigini unutup
        # "tam tarandi" sanmasi riski gercek.
        report.llm_disabled = not settings.vllm.enabled
        if report.llm_disabled:
            db.add(
                AuditLog(
                    run_id=run.id,
                    file_path="",
                    action="skipped",
                    detail=(
                        "LLM (Katman 3) bu calisma icin KAPALI (VLLM_ENABLED=false) - sadece "
                        "Katman 1 (kural/sozluk) ve Katman 2 (Presidio) taramasi yapildi."
                    ),
                )
            )
        # Sozdizimi dogrulamasindan gecemeyen dosyalarin tasinacagi klasor (hedefin yaninda, run_id etiketli).
        failed_dir = target.parent / f"basarisiz_dosyalar_{run.id}"
        # Tekrar eden degerlerin (sirket adi, e-posta vb.) DB'ye tekrar sorgulanmamasi icin paylasilan onbellek.
        mapping_cache = MappingCache()
        consistency_registry = SensitiveValueRegistry()
        output_files: list[_OutputFile] = []
        run_ctx = MaskingRunContext(
            context=context, run_id=run.id, runtime_params=runtime_params,
            orchestrator=orchestrator, mapping_cache=mapping_cache,
        )

        # Ayni anda hazirlanan dosya sayisi. LLM HTTP istek siniri ayrica
        # llm_runtime._gate (max_concurrent_requests) ile korunur; batch'i o
        # sinira esitlemek (eskiden 1) LLM beklerken diger dosyalarin
        # kural/Presidio taramasini da durduruyordu. Faz B/D (mapping, sayac,
        # yazma) dosya sirasiyla ve sirali calistigi icin placeholder
        # numaralandirmasi batch boyutundan bagimsizdir.
        batch_size = max(1, getattr(settings.vllm, "file_batch_size", 1), settings.vllm.max_concurrent_requests)
        # Tespit (siradaki batch) ve denetim (bu batch) boru hattinda ayni
        # anda yurudugu icin ayri dosya slotlari kullanir; biri digerinin
        # slotlarini tuketip boru hattini sirali hale getirmesin. LLM istek
        # siniri ikisi icin ortak olarak llm_runtime._gate ile korunur.
        detection_slots = asyncio.Semaphore(batch_size)
        audit_slots = asyncio.Semaphore(batch_size)

        all_files = list(iter_project_files(source, exclude_specs, prune_ignored=True))
        total_files = len(all_files)
        processed = 0

        # Butun hedef yollarini TEK bir dosya yazmadan once hesapla. Iki
        # farkli kaynak yol ayni maskeli hedefe donusurse sessiz overwrite
        # geri donusu olmayan veri kaybi yaratir; export bu durumda fail-closed
        # durur. casefold anahtari, cikti daha sonra Windows/macOS gibi
        # case-insensitive bir ortamda acildiginda dogacak cakismayi da engeller.
        path_plans: dict[Path, tuple[Path, list]] = {}
        claimed_masked_paths: dict[str, Path] = {}
        for scanned in all_files:
            if enable_path_masking and scanned.excluded_by is None and not scanned.is_symlink:
                masked_relative_path, path_mappings = mask_relative_path(
                    db,
                    context,
                    scanned.relative_path,
                    runtime_params,
                    active_rules,
                    mapping_cache=mapping_cache,
                    run_id=run.id,
                )
            else:
                masked_relative_path, path_mappings = scanned.relative_path, []

            path_plans[scanned.relative_path] = (masked_relative_path, path_mappings)
            if scanned.excluded_by is not None or scanned.is_symlink:
                continue
            leaked_path_terms = find_leaked_terms(
                db, masked_relative_path.as_posix(), exclude_path_spanning=True,
                path_placeholders=(mapping.placeholder_value for mapping in path_mappings),
            )
            if leaked_path_terms:
                leaked_rules = ", ".join(sorted({term.rule_name for term in leaked_path_terms}))
                leaked_locations = ", ".join(sorted({
                    f"{term.line_number}:{term.column_number}" for term in leaked_path_terms
                }))
                raise ExportValidationError(
                    f"yol maskeleme son kontrolu basarisiz: dosya='{scanned.relative_path}', "
                    f"maskelenmis yol='{masked_relative_path}' - aktif kurumsal terim maskeli "
                    f"yolda kaldi; kurallar={leaked_rules}; "
                    f"maskeli yolda konumlar(satir:sutun)={leaked_locations}"
                )
            collision_key = masked_relative_path.as_posix().casefold()
            previous_source = claimed_masked_paths.get(collision_key)
            if previous_source is not None and previous_source != scanned.relative_path:
                raise ExportValidationError(
                    f"yol maskeleme cakismasi: dosyalar='{previous_source}' ve "
                    f"'{scanned.relative_path}' - iki farkli kaynak yolu ayni maskeli "
                    f"hedefe ('{masked_relative_path}') donusuyor"
                )
            claimed_masked_paths[collision_key] = scanned.relative_path

        # Use the same path decoder as unmask, including compound names.
        # Validate before overwriting any existing output directory.
        db.flush()
        path_reverse_map = {
            token: decrypt_value(encrypted)
            for token, encrypted in db.execute(
                select(ValueMapping.placeholder_value, ValueMapping.original_value_encrypted)
                .where(ValueMapping.context_id == context.id, ValueMapping.run_id == run.id)
            )
        }
        path_resolver = PathPlaceholderResolver(path_reverse_map)
        for scanned in all_files:
            if scanned.excluded_by is not None or scanned.is_symlink:
                continue
            masked_path, _ = path_plans[scanned.relative_path]
            try:
                restored_path, _, _unresolved = path_resolver.reverse(masked_path)
            except UnsafeUnmaskPathError as exc:
                raise ExportValidationError("dosya yolu round-trip dogrulamasi basarisiz") from exc
            # As with content round-trip, exact source equality is the
            # contract. An unchanged source name like service_test_3.txt
            # does not need a mapping just because it resembles a token.
            # Missing mappings for generated tokens still change the path
            # and fail this comparison before any output is published.
            if restored_path != scanned.relative_path:
                raise ExportValidationError(
                    f"dosya yolu round-trip dogrulamasi basarisiz: dosya='{scanned.relative_path}', "
                    f"maskelenmis yol='{masked_path}'"
                )

        # Cakisma preflight'i basarili olmadan mevcut hedefe dokunulmaz.
        publication = OutputPublication(target, run.id)
        output_target = publication.stage
        # ARA COMMIT'LER: SQLite'ta ilk yazmadan commit'e kadar tek bir yazma
        # kilidi tutulur. Tum export tek transaction olsaydi kilit LLM
        # cagrilari boyunca (saatlerce) tutulur, baska projelerin export'lari
        # ve onay/kural islemleri "database is locked" alirdi. Bu yuzden LLM
        # fazlarindan (A ve C) ONCE commit edilir. Bu noktadan sonra run
        # 'in_progress' olarak kalicidir; kurtarma gunlugu (publication lock)
        # zaten yazildigi icin surec cokerse recover-output onu 'failed' yapar.
        # Hata yolunda run asagida 'failed' olarak isaretlenir. Bitmemis bir
        # run'in inceleme/karantina kayitlari onay ekraninda listelenmez
        # (bkz. repository list_pending_for_identity).
        db.commit()

        # BORU HATTI: bir sonraki batch'in Faz A tespiti (Presidio + LLM),
        # bu batch'in Faz C denetimi ve Faz D/E'si ile ES ZAMANLI yurur;
        # boylece bir batch'in en yavas dosyasi siradaki batch'in taramasini
        # bekletmez. Faz A DB'ye dokunmaz; hazirlik (DB'ye yazan) yine sirali
        # ve yazma kilidi altinda yapilir. Placeholder numaralandirmasi sirali
        # Faz B'de verildigi icin cikti bu es zamanliliktan etkilenmez.
        batches = [all_files[start : start + batch_size] for start in range(0, total_files, batch_size)]
        prepared_next = (
            _prepare_batch(db, run.id, batches[0], path_plans, output_target, max_inline_size) if batches else None
        )
        if prepared_next is not None:
            next_detection = _start_detection(orchestrator, prepared_next, detection_slots)

        for batch_number in range(len(batches)):
            prepared, outcome_by_index = prepared_next, await next_detection
            prepared_next = next_detection = None
            batch = prepared.files
            preps = prepared.preps
            batch_masked_paths = prepared.masked_paths
            batch_path_mappings = prepared.path_mappings

            # Faz B (sirali, DB yazan): apply_detections + round-trip.
            _acquire_write_lock(db, run.id)
            batch_results: list[FileOutcome | _MaskedFile] = [None] * len(batch)  # type: ignore[list-item]
            for i, prep in enumerate(preps):
                if prep.outcome is not None:
                    batch_results[i] = prep.outcome
                    continue
                if prep.mode == "scan_only":
                    # SAVEPOINT: ayni izolasyon ilkesi asagidaki _apply_masking
                    # cagrisiyla BIREBIR ayni - bu dosyanin (bagimlilik lock
                    # dosyasi) taramasindaki BEKLENMEYEN bir hata SADECE onu
                    # etkilemeli, diger dosyalarin zaten basarili islerini
                    # coktermemeli. "taranamadi != temiz": bir crash burada da
                    # otomatik-temiz sayilmaz, guvenlik geregi karantinaya alinir.
                    cache_snapshot = (
                        _snapshot_dict(run_ctx.mapping_cache.mappings) if run_ctx.mapping_cache is not None else None
                    )
                    try:
                        with db.begin_nested():
                            batch_results[i] = _finalize_scan_only(db, run_ctx, prep, outcome_by_index[i])
                    except Exception as exc:
                        if run_ctx.mapping_cache is not None:
                            _restore_dict(run_ctx.mapping_cache.mappings, cache_snapshot)
                        reason = (
                            "Bağımlılık/lock dosyası taramasında beklenmeyen bir hata oluştu - "
                            f"güvenlik gereği karantinaya alındı ({type(exc).__name__})"
                        )
                        db.add(
                            AuditLog(
                                run_id=run.id, file_path=prep.rel, action="error",
                                detail="final_state=VALIDATION_FAILED final_output=blocked; scan_only beklenmeyen hata",
                            )
                        )
                        _create_audit_warning(
                            db, run_id=run.id, file_path=prep.rel, masked_content="",
                            encoding=prep.encoding, output_path=prep.masked_rel, reasoning=reason, audit_failed=True,
                        )
                        batch_results[i] = FileOutcome(
                            prep.rel, status="failed_detection", error=reason, final_state="VALIDATION_FAILED",
                        )
                    continue
                # SAVEPOINT: bir dosyanin mapping/DB yazma adiminda BEKLENMEYEN
                # bir istisna, bu dosyayi (ve SADECE bunu) karantinaya almali -
                # TUM calismayi (ve bu batch'teki DIGER dosyalarin zaten
                # basarili mapping/audit yazmalarini) coktermemeli. `with`
                # bloğu normal bitince (basarili DONUS - round-trip hatasi
                # DAHIL, bu bir istisna DEGIL) savepoint sessizce serbest
                # birakilir/ana transaction'a katilir; SADECE gercek bir
                # istisnada geri alinir (bkz. asagidaki except).
                # bkz. _snapshot_dict/_restore_dict dokstringi: paylasilan
                # MappingCache, bu dosyanin savepoint'i geri alinsa BILE
                # rollback-sonrasi "hayalet" bir ValueMapping nesnesini
                # SONRAKI bir dosyaya sizdirmamali.
                cache_snapshot = (
                    _snapshot_dict(run_ctx.mapping_cache.mappings) if run_ctx.mapping_cache is not None else None
                )
                try:
                    with db.begin_nested():
                        batch_results[i] = _apply_masking(
                            db,
                            run_ctx,
                            prep,
                            outcome_by_index[i],
                            rule_names_by_id,
                            failed_dir,
                            consistency_registry,
                        )
                except Exception as exc:
                    if run_ctx.mapping_cache is not None:
                        _restore_dict(run_ctx.mapping_cache.mappings, cache_snapshot)
                    # Never log exc's message: it ran over real file content
                    # and could echo a fragment of it back (bkz. modul
                    # dokstirninin guvenlik ilkesi - _detect_for_prep'teki
                    # ayni ilkeyle tutarli).
                    reason = (
                        "Mapping/veritabani yazma adiminda beklenmeyen bir hata olustu - bu dosyanin "
                        f"tam ve dogru maskelendigi garanti edilemez ({type(exc).__name__}), guvenlik "
                        "geregi karantinaya alindi"
                    )
                    db.add(
                        AuditLog(
                            run_id=run.id, file_path=prep.rel, action="error",
                            detail="final_state=VALIDATION_FAILED final_output=blocked; mapping/DB yazma hatasi",
                        )
                    )
                    _create_audit_warning(
                        db, run_id=run.id, file_path=prep.rel, masked_content="",
                        encoding=prep.encoding, output_path=prep.masked_rel, reasoning=reason, audit_failed=True,
                    )
                    batch_results[i] = FileOutcome(
                        prep.rel, status="quarantined_pending_audit", error=reason,
                        final_state="VALIDATION_FAILED",
                    )

            # Siradaki batch'in hazirligi hala yazma kilidi altindayken yapilir;
            # tespiti asagidaki Faz C/D/E ile es zamanli baslar.
            if batch_number + 1 < len(batches):
                prepared_next = _prepare_batch(
                    db, run.id, batches[batch_number + 1], path_plans, output_target, max_inline_size,
                )

            # Faz C (LLM denetimi) yazma kilidi tutulmadan calissin.
            db.commit()
            if prepared_next is not None:
                next_detection = _start_detection(orchestrator, prepared_next, detection_slots)

            # Phase C audits every supported, prepared text file, including
            # files for which the initial detectors found no matches.
            masked_indices = [i for i, r in enumerate(batch_results) if isinstance(r, _MaskedFile)]
            audit_results = await asyncio.gather(
                *(_bounded(_audit_masked_file(batch_results[i]), audit_slots) for i in masked_indices)
            )
            audit_by_index = dict(zip(masked_indices, audit_results))

            # Faz D (sirali, DB+dosya yazan): audit karari + sozdizimi + yazma.
            # Degeri kesin bilinen sizintisi olan dosyalar insan onayi yerine
            # otomatik duzeltmeye (Faz E) ayrilir.
            _acquire_write_lock(db, run.id)
            outcomes: dict[int, FileOutcome] = {}
            pending: list[tuple[int, _PendingRemediation]] = []
            for i, scanned in enumerate(batch):
                prep = preps[i]
                result = batch_results[i]
                if isinstance(result, _MaskedFile):
                    # SAVEPOINT: ayni gerekce Faz B'deki ile (bkz. yukaridaki
                    # yorum) - bir dosyanin Faz D'sindeki BEKLENMEYEN bir
                    # istisna SADECE o dosyayi (ve o dosya icin bu adimda
                    # yapilmis olabilecek yarim DB yazmalarini) etkilemeli,
                    # bu batch'teki (ya da onceki batch'lerdeki) diger
                    # dosyalarin zaten basarili olan mapping/audit/yazma
                    # islerini COKERTMEMELI. Normal donus (basarisiz
                    # FileOutcome DAHIL - bu bir istisna DEGIL) savepoint'i
                    # sessizce serbest birakir/ana transaction'a katar.
                    # Otomatik duzeltme mapping yazdigi icin onbellek de korunur.
                    cache_snapshot = (
                        _snapshot_dict(run_ctx.mapping_cache.mappings) if run_ctx.mapping_cache is not None else None
                    )
                    try:
                        with db.begin_nested():
                            outcome = _finalize_file(
                                db, run.id, result, audit_by_index[i], failed_dir, report.validation_warnings,
                                decision_policy=decision_policy, allow_remediation=True,
                            )
                            if isinstance(outcome, _RemediationRequest):
                                outcome = _start_remediation(
                                    db, run_ctx, result, audit_by_index[i], outcome, failed_dir,
                                    report.validation_warnings, decision_policy, rules_by_name, rule_names_by_id,
                                )
                    except Exception as exc:
                        if run_ctx.mapping_cache is not None:
                            _restore_dict(run_ctx.mapping_cache.mappings, cache_snapshot)
                        outcome = _quarantine_after_finalize_crash(db, run.id, prep, exc)
                    if isinstance(outcome, _PendingRemediation):
                        pending.append((i, outcome))
                        continue
                else:
                    outcome = result
                outcomes[i] = outcome

            # Faz E (duzeltme turu): duzeltilen dosyalarin LLM denetimi yazma
            # kilidi tutulmadan tekrar calisir, ardindan dosya sirali olarak
            # _finalize_file'in tum son kontrollerinden gecer. En fazla
            # _MAX_REMEDIATION_ROUNDS tur; kalan her dosya insan onayina doner.
            while pending:
                db.commit()
                reaudits = await asyncio.gather(
                    *(_bounded(_audit_one(item.current.masked_text, preps[i].rel), audit_slots) for i, item in pending)
                )
                _acquire_write_lock(db, run.id)
                next_pending: list[tuple[int, _PendingRemediation]] = []
                for (i, item), reaudit in zip(pending, reaudits):
                    cache_snapshot = (
                        _snapshot_dict(run_ctx.mapping_cache.mappings) if run_ctx.mapping_cache is not None else None
                    )
                    try:
                        with db.begin_nested():
                            outcome = _continue_remediation(
                                db, run_ctx, item, reaudit, failed_dir, report.validation_warnings,
                                decision_policy, rules_by_name, rule_names_by_id, consistency_registry,
                            )
                    except Exception as exc:
                        if run_ctx.mapping_cache is not None:
                            _restore_dict(run_ctx.mapping_cache.mappings, cache_snapshot)
                        outcome = _quarantine_after_finalize_crash(db, run.id, preps[i], exc)
                    if isinstance(outcome, _PendingRemediation):
                        next_pending.append((i, outcome))
                    else:
                        outcomes[i] = outcome
                pending = next_pending

            # Rapor/cikti kaydi dosya sirasiyla (duzeltme turundan bagimsiz, deterministik).
            for i, scanned in enumerate(batch):
                prep = preps[i]
                outcome = outcomes[i]
                path_mappings = batch_path_mappings[i]
                if path_mappings:
                    if outcome.status == "copied_text_no_match":
                        outcome.status = "masked"
                    outcome.match_count += len(path_mappings)
                    path_breakdown = _rule_breakdown(path_mappings, rule_names_by_id)
                    for rule_name, count in path_breakdown.items():
                        outcome.rule_breakdown[rule_name] = outcome.rule_breakdown.get(rule_name, 0) + count
                    db.add(
                        AuditLog(
                            run_id=run.id,
                            file_path=str(scanned.relative_path),
                            action="replaced",
                            detail=f"path masked to {batch_masked_paths[i]}",
                        )
                    )
                report.record(outcome)
                if prep.dest_path.is_file():
                    output_files.append(
                        _OutputFile(
                            path=prep.dest_path,
                            source_relative_path=scanned.relative_path,
                            outcome=outcome,
                            masked_relative_path=batch_masked_paths[i],
                            encoding=prep.encoding,
                            original_digest=text_digest(prep.text) if prep.text is not None else None,
                            original_length=len(prep.text) if prep.text is not None else None,
                            original_binary_digest=(hashlib.sha256(prep.class_document.raw).hexdigest()
                                                    if prep.class_document is not None else None),
                            scan_only=prep.mode == "scan_only",
                        )
                    )
                processed += 1
                if progress_callback is not None:
                    progress_callback(processed, total_files, str(scanned.relative_path))

            # Batch'in rapor/cikti kayitlari kalici olsun; siradaki batch'in
            # tespiti bu sirada zaten yazma kilidi tutulmadan yuruyor.
            db.commit()

        # Ilk turda dogrulanip mapping'e donusen degerleri, detector/context
        # farki yuzunden kacmis olabilecek tum proje kopyasinda uygula ve
        # ardindan sifir acik occurrence invariant'ini denetle. SCAN_ONLY
        # (scan_only_clean) dosyalari KASITLI olarak DISARIDA birakilir: bu
        # gecis bulgu varsa dosyayi YENIDEN YAZAR (replacement), tam da
        # SCAN_ONLY politikasinin yasakladigi sey ("Lock -> tara + degistirme
        # yok") - lock/integrity dosyalari byte-identical kalmayi GARANTI eder.
        _acquire_write_lock(db, run.id)
        consistency_output_files = [f for f in output_files if not f.scan_only]
        _run_consistency_pass(
            db,
            run_ctx,
            consistency_registry,
            consistency_output_files,
            report,
            failed_dir,
            rule_names_by_id,
            max_inline_size,
        )

        # FINAL SCAN_ONLY CONSISTENCY VERIFICATION: registry artik tam olarak
        # olustu (yukaridaki gecis registry'ye YENI deger EKLEMEZ, sadece
        # okur) - scan_only_clean (lock/integrity) dosyalarini, projenin
        # BASKA dosyalarinda dogrulanmis degerlere karsi salt-okunur sekilde
        # tekrar dogrular (bkz. _run_scan_only_final_verification). MASK
        # dosyalarinin aksine bulgu varsa dosya MASKELENMEZ, sadece ciktidan cikarilir.
        scan_only_output_files = [f for f in output_files if f.scan_only]
        _run_scan_only_final_verification(
            db, run_ctx, consistency_registry, scan_only_output_files, report, max_inline_size,
        )

        # Finalize (chmod + manifest kaydi): bu noktaya kadar dosya butun
        # icerik dogrulamalarindan (syntax/round-trip/consistency/audit)
        # BASARIYLA gecti - burada SADECE izin/dijital-imza metaverisi
        # yaziliyor. Yine de dosya-basina BEKLENMEYEN bir hata (orn. kaynak
        # dosyanin stat()'i, chmod, ya da sha256 hesaplamasi sirasinda bir
        # OSError/baska istisna) SADECE o dosyayi etkilemeli - digerlerinin
        # zaten basarili chmod/manifest islerini coktermemeli (bkz. Faz A/B/
        # D icin ayni ilke, yukarida). SAVEPOINT burada da mapping/DB
        # yazan bir ic-ice adim olmadigi icin cache anlik goruntusu
        # gerekmiyor, ama tutarlilik icin yine de kullaniliyor.
        class_reverse_map = {}
        if any(item.original_binary_digest is not None for item in output_files):
            class_reverse_map = {
                token: decrypt_value(encrypted)
                for token, encrypted in db.execute(
                    select(ValueMapping.placeholder_value, ValueMapping.original_value_encrypted)
                    .where(ValueMapping.context_id == context.id, ValueMapping.run_id == run.id)
                )
            }
        manifest_files = {}
        for output_file in output_files:
            if not output_file.path.is_file():
                continue
            try:
                with db.begin_nested():
                    if output_file.original_binary_digest is not None:
                        document = parse_class(output_file.path.read_bytes())
                        restored_view, _, _ = reverse_text(document.text, class_reverse_map)
                        restored_bytes = document.rebuild(restored_view)
                        if hashlib.sha256(restored_bytes).hexdigest() != output_file.original_binary_digest:
                            raise ClassFormatError("Java class bayt bazlı geri dönüş doğrulaması başarısız")
                    mode = (source / output_file.source_relative_path).stat().st_mode & 0o777
                    output_file.path.chmod(mode)
                    manifest_entry = {
                        "encoding": output_file.encoding or "utf-8",
                        "masked_sha256": file_digest(output_file.path),
                        "source_tag": source_tag(context.id, output_file.original_digest),
                        "mode": mode,
                    }
                    if output_file.original_binary_digest is not None:
                        manifest_entry["source_bytes_tag"] = source_tag(context.id, output_file.original_binary_digest)
            except Exception as exc:
                # Guvensiz/dogrulanmamis bir dosya hedef pakette KALAMAZ -
                # icerigi gecerli olsa bile, chmod/manifest kaydi eksikse
                # butunluk kaydi onu aciklayamaz. Never log exc's message:
                # bu adim gercek maskelenmis dosya baytlari uzerinde calisir.
                reason = (
                    "Sonlandirma (izin/butunluk kaydi) adiminda beklenmeyen bir hata olustu - bu "
                    f"dosyanin guvenli sekilde tamamlandigi garanti edilemez ({type(exc).__name__}), "
                    "hedef paketten kaldirildi"
                )
                if output_file.path.is_file() or output_file.path.is_symlink():
                    try:
                        output_file.path.unlink()
                    except OSError:
                        pass
                db.add(
                    AuditLog(
                        run_id=run.id, file_path=output_file.outcome.relative_path, action="error",
                        detail="final_state=VALIDATION_FAILED final_output=blocked; finalize (chmod/manifest) coktu",
                    )
                )
                _create_audit_warning(
                    db, run_id=run.id, file_path=output_file.outcome.relative_path, masked_content="",
                    encoding=output_file.encoding, reasoning=reason, audit_failed=True,
                )
                report.mark_finalization_failed(output_file.outcome, reason)
                continue
            manifest_files[output_file.path.relative_to(output_target).as_posix()] = manifest_entry
        write_manifest(output_target, context.id, manifest_files,
                       complete=len(manifest_files) == report.files_scanned, job_id=run.id)

        # Karantinaya alinan ya da dogrulamalardan (finalize DAHIL) gecemeyen
        # dosya varsa export teknik olarak basarili tamamlanmis olsa da
        # CIKTI EKSIKTIR - "Basarili" degil "Uyarili tamamlandi" olarak
        # isaretlenir (unmask'taki cozulemeyen placeholder durumuyla ayni
        # ilke). Finalize dongusunden SONRA hesaplanir ki dongude ortaya
        # cikabilecek YENI bir basarisizlik (has_failed_finalization) da
        # dogru yansitilsin.
        final_status = (
            "completed_with_warnings"
            if (
                report.has_quarantined_files
                or report.has_failed_syntax_validation
                or report.has_failed_round_trip_validation
                or report.has_failed_consistency_validation
                or report.has_failed_finalization
                or report.has_degraded_detectors
                or report.has_unverifiable_files
                or report.validation_warnings
            )
            else "completed"
        )
        run.status = final_status
        report.status = final_status
        report.completed_at = datetime.now(timezone.utc)
        run.completed_at = report.completed_at
        run.files_scanned = report.files_scanned
        run.match_count = report.total_matches
        publish_run(db, run, report, publication)
    except BaseException:
        failed = True
        report.status = "failed"
        await _cancel_detection(next_detection)
        db.rollback()
        _mark_run_failed(db, run.id)
        raise
    finally:
        db.expire_on_commit = previous_expire_on_commit
        if publication is not None:
            publication.close()
        if failed:
            report.completed_at = datetime.now(timezone.utc)

    return report
