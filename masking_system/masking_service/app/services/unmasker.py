"""Reverse/unmask pipeline: given a masked project folder and the
(project_name, sicil_no, branch_name) triple it was exported with,
looks up every value_mapping recorded for that context and replaces
placeholders back with their real values.

Security invariant: if the triple doesn't resolve to an existing
masking_context, this MUST fail loudly before touching the filesystem at
all - never fall back to an empty mapping table and silently produce a
copy that still contains placeholders while claiming success.

Reversibility invariant: a placeholder found in the file but with no
matching row in value_mappings (wrong context, hand-edited file, or a
false positive that merely looks like PREFIX_TEST_N) must be left exactly
as-is in the output AND surfaced in the report - never blanked out, never
guessed at, never silently dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session
from cryptography.fernet import InvalidToken

# decrypt_value: value_mappings'teki sifreli orijinal degerleri cozmek icin.
# AuditLog/MaskingContext/MaskingRun/ValueMapping: bu akisin okudugu/yazdigi
# ORM modelleri. load_active_exclude_specs: hangi dosyalarin haric tutulacagini
# belirler. exporter'dan hedef klasor hazirlama/yol dogrulama yardimcilari
# (export ile AYNI mantik, kod tekrarini onlemek icin paylasilir). ReadStatus/
# read_scanned_file: dosya okuma-siniflandirma boru hatti (symlink/binary/
# boyut/encoding kontrolleri). write_text_preserving_encoding: sonucu orijinal
# encoding'i koruyarak yazar. reverse_text: placeholder->gercek deger geri
# cozumu (roundtrip_validator.py ile PAYLASILAN AYNI fonksiyon). iter_project_files:
# kaynak klasoru deterministik tarar. IdentityMismatchAdvisor/Suggestion:
# yanlis kimlikle unmask calistirilmis olabilecegini tespit eden tani katmani.
from app.core.config import settings
from app.core.crypto import decrypt_value
from app.db.models import AuditLog, MaskingContext, MaskingRun, ValueMapping
from app.services.exclude_admin import load_active_exclude_specs
from app.services.exporter import (
    DEFAULT_MAX_INLINE_SIZE,
    _validate_paths,
)
from app.services.file_pipeline import ReadStatus, read_scanned_file
from app.services.file_type import write_text_preserving_encoding
from app.services.java_classfile import ClassFormatError
from app.services.rule_engine import reverse_text
from app.services.path_placeholders import PathPlaceholderResolver, UnsafeUnmaskPathError
from app.services.scanner import iter_project_files
from app.services.output_publication import OutputPublication, publish_run
from app.services.integrity_manifest import MANIFEST_NAME, read_manifest, file_digest, source_tag
from app.services.mapping_service import mapping_scope_for_run
from app.services.roundtrip_validator import text_digest
import hmac
from app.services.unmask_diagnostics import IdentityMismatchAdvisor, IdentityMismatchSuggestion


# proje_adi/personel_no/branch uclusune ait bir context bulunamadiginda
# (yanlis bilgi girildiginde) firlatilan guvenlik hatasi.
class ContextNotFoundError(ValueError):
    """Raised when (project_name, sicil_no, branch_name) has no
    matching masking_context - i.e. the caller supplied a wrong triple.
    Deliberately NOT a subclass of ExportValidationError: callers should be
    able to tell "bad folder paths" apart from "bad project identity"."""


# Cozulen bir path bileseninin, hedef klasorun disina cikmaya calistigi
# (path traversal) tespit edildiginde firlatilir - dosya hic yazilmaz.
# Bir path bileseninin cozulmus halini dogrular: sonuc '/' ya da '\\' iceriyorsa
# ya da '.'/'..'e esitse GUVENSIZ sayilir (path traversal koruması).
def _reverse_path_component(component: str, placeholder_map: dict[str, str]) -> str:
    return PathPlaceholderResolver(placeholder_map).reverse_component(component)[0]


# Bir dosyanin goreli yolunu, her path bilesenini AYRI AYRI cozup dogrulayarak
# geri cozer (path traversal riskini onlemek icin tum yolu tek string olarak cozmez).
def _reverse_relative_path(relative_path: Path, placeholder_map: dict[str, str]) -> Path:
    return PathPlaceholderResolver(placeholder_map).reverse(relative_path)[0]


# Tek bir dosyanin geri donusum sirasinda basina ne geldigini (kac
# placeholder bulundu/cozuldu/cozulemedi) tutan sonuc kaydi.
@dataclass
class FileUnmaskOutcome:
    relative_path: str
    status: str
    placeholders_found: int = 0
    placeholders_resolved: int = 0
    unresolved_placeholders: list[str] = field(default_factory=list)
    error: str | None = None
    validation_warnings: list[str] = field(default_factory=list)


_STATUS_DISPLAY_NAMES = {
    "completed": "Basarili",
    "completed_with_warnings": "Uyarili tamamlandi",
    "failed": "Basarisiz",
    "in_progress": "Devam ediyor",
}


# Bir unmask (geri donusum) calismasinin tum ozetini tutan rapor nesnesi.
@dataclass
class UnmaskReport:
    run_id: int
    context_id: int
    project_name: str
    sicil_no: str
    branch_name: str
    source_path: str
    target_path: str
    started_at: datetime
    job_id: int | None = None
    completed_at: datetime | None = None
    target_overwritten: bool = False
    mappings_loaded: int = 0
    files_scanned: int = 0
    files_reversed: int = 0
    files_unresolved_only: int = 0
    files_copied_no_placeholders: int = 0
    files_copied_binary: int = 0
    files_copied_undecodable: int = 0
    files_skipped_symlink: int = 0
    files_skipped_too_large: int = 0
    files_excluded: int = 0
    files_errored: int = 0
    total_placeholders_found: int = 0
    total_placeholders_resolved: int = 0
    total_placeholders_unresolved: int = 0
    unresolved_by_placeholder: dict[str, int] = field(default_factory=dict)
    outcomes: list[FileUnmaskOutcome] = field(default_factory=list)
    status: str = "in_progress"
    validation_warnings: list[str] = field(default_factory=list)
    identity_mismatch_suggestion: IdentityMismatchSuggestion | None = None

    # Tek bir dosyanin sonucunu (FileUnmaskOutcome) rapor toplamlarina isler.
    def record(self, outcome: FileUnmaskOutcome) -> None:
        self.files_scanned += 1
        self.outcomes.append(outcome)
        self.validation_warnings.extend(f"{outcome.relative_path}: {item}" for item in outcome.validation_warnings)
        self.total_placeholders_found += outcome.placeholders_found
        self.total_placeholders_resolved += outcome.placeholders_resolved
        self.total_placeholders_unresolved += len(outcome.unresolved_placeholders)
        for token in outcome.unresolved_placeholders:
            self.unresolved_by_placeholder[token] = self.unresolved_by_placeholder.get(token, 0) + 1

        bucket = {
            "reversed": "files_reversed",
            "unresolved_only": "files_unresolved_only",
            "copied_no_placeholders": "files_copied_no_placeholders",
            "copied_binary": "files_copied_binary",
            "copied_undecodable": "files_copied_undecodable",
            "skipped_symlink": "files_skipped_symlink",
            "skipped_too_large": "files_skipped_too_large",
            "excluded": "files_excluded",
            "error": "files_errored",
        }[outcome.status]
        setattr(self, bucket, getattr(self, bucket) + 1)

    # Cozulemeyen (eslesme kaydi bulunamayan) placeholder olup olmadigini bildirir.
    @property
    def has_unresolved_placeholders(self) -> bool:
        return self.total_placeholders_unresolved > 0

    # Raporu insan tarafindan okunabilir, Turkce ozet metnine cevirir (CLI
    # ciktisi). Ayni "duz cumlelik ozet + sadece dikkat gerektiren kalemler"
    # yapisi export raporuyla tutarli (bkz. ExportReport.summary_text).
    def summary_text(self) -> str:
        status_label = _STATUS_DISPLAY_NAMES.get(self.status, self.status)
        lines = [
            f"Geri donusum raporu - run_id={self.run_id} durum={status_label}",
            f"  Proje: {self.project_name} | sicil: {self.sicil_no} | Branch: {self.branch_name}",
            f"  Kaynak (maskeli): {self.source_path}",
            f"  Hedef (geri donusturulmus): {self.target_path}"
            + (" (onceki cikti uzerine yazildi)" if self.target_overwritten else ""),
            "",
        ]

        headline = (
            f"  {self.files_scanned} dosya tarandi -> "
            f"{self.files_reversed} dosyada placeholder cozulup geri donusturuldu"
        )
        if self.files_copied_no_placeholders:
            headline += f", {self.files_copied_no_placeholders} dosyada placeholder bulunamadi"
        if self.files_unresolved_only:
            headline += f", {self.files_unresolved_only} dosyada HICBIR placeholder cozulemedi"
        lines.append(headline + ".")
        lines.append(
            f"  Toplam {self.total_placeholders_found} placeholder bulundu, "
            f"{self.total_placeholders_resolved} tanesi cozuldu."
        )

        if self.unresolved_by_placeholder:
            lines.append("")
            lines.append(
                f"  !!! COZULEMEYEN {self.total_placeholders_unresolved} placeholder var "
                "(manuel inceleme gerekli) !!!"
            )
            if self.identity_mismatch_suggestion is not None:
                s = self.identity_mismatch_suggestion
                lines.append(
                    f"  >>> OLASI NEDEN: YANLIS KIMLIK. Cozulemeyen placeholder'larin "
                    f"{s.matched_count}/{s.unresolved_count} tanesi ({s.match_ratio:.0%}) "
                    f"su kimlige ait gibi gorunuyor: proje='{s.project_name}', "
                    f"sicil='{s.sicil_no}', branch='{s.branch_name}'. "
                    "Bu bilgilerle tekrar deneyin. <<<"
                )
            for token, count in sorted(self.unresolved_by_placeholder.items()):
                lines.append(f"    - {token}: {count} yerde")

        attention: list[str] = []
        if self.files_skipped_symlink:
            attention.append(f"{self.files_skipped_symlink} dosya symlink oldugu icin atlandi")
        if self.files_skipped_too_large:
            attention.append(
                f"{self.files_skipped_too_large} dosya boyut esigini astigi icin taranmadan kopyalandi"
            )
        if self.files_copied_undecodable:
            attention.append(
                f"{self.files_copied_undecodable} dosya decode edilemedi, manuel inceleme gerekli"
            )
        if self.files_excluded:
            attention.append(
                f"{self.files_excluded} dosya guvenlik politikasi geregi haric tutuldu (hic kopyalanmadi)"
            )
        if self.files_errored:
            attention.append(f"{self.files_errored} dosya hata aldi")
        if self.files_copied_binary:
            attention.append(f"{self.files_copied_binary} binary dosyanin icerigi geri cozum icin incelenemedi")
        attention.extend(self.validation_warnings)

        lines.append("")
        if attention:
            lines.append("  Dikkat edilmesi gerekenler:")
            for item in attention:
                lines.append(f"    - {item}")
        elif not self.unresolved_by_placeholder:
            lines.append("  Sorun yok: hata, atlanan symlink, boyut asimi ya da haric tutma yasanmadi.")

        return "\n".join(lines)


# Verilen uclunun context'ini bulur ve tum eslemelerini sifresi cozulmus
# (placeholder -> gercek deger) sozluk olarak dondurur. Context bulunamazsa
# ContextNotFoundError firlatir, hicbir dosyaya dokunmaz (guvenlik kapisi).
def _load_identity(
    db: Session, project_name: str, sicil_no: str, branch_name: str
) -> MaskingContext:
    context = db.scalars(
        select(MaskingContext).where(
            MaskingContext.project_name == project_name,
            MaskingContext.sicil_no == sicil_no,
            MaskingContext.branch_name == branch_name,
        )
    ).one_or_none()
    if context is None:
        raise ContextNotFoundError(
            f"Kayitli masking context bulunamadi: proje_adi='{project_name}', "
            f"personel_no='{sicil_no}', branch_adi='{branch_name}'. "
            "Bu ucluyle daha once export yapilmamis olabilir ya da bilgilerden "
            "biri yanlis girilmis olabilir - islem guvenlik nedeniyle durduruldu."
        )

    return context


def load_context_mappings(
    db: Session, project_name: str, sicil_no: str, branch_name: str,
    *, job_id: int | None = None, allow_legacy: bool = False,
) -> tuple[MaskingContext, dict[str, str]]:
    context = _load_identity(db, project_name, sicil_no, branch_name)
    scope_id = None
    if job_id is not None:
        job = db.get(MaskingRun, job_id)
        if job is None or job.context_id != context.id or job.operation_type != "mask":
            raise ValueError("Maskeleme islem kimligi secilen proje/sicil/branch ile eslesmiyor.")
        scope_id = mapping_scope_for_run(db, job_id)
    elif not allow_legacy and db.scalar(select(MaskingRun.id).where(
        MaskingRun.context_id == context.id, MaskingRun.mapping_version == 2,
        MaskingRun.operation_type == "mask",
    ).limit(1)) is not None:
        raise ValueError("Maskeleme islem kimligi eksik. Cikti paketindeki .masking-integrity.json dosyasini ekleyin veya job_id belirtin; hedef degistirilmedi.")

    rows = db.scalars(select(ValueMapping).where(
        ValueMapping.context_id == context.id, ValueMapping.run_id == scope_id,
    )).all()
    ambiguous = set()
    if scope_id is None:
        # Only legacy rows can be ambiguous across identities. Repeated tokens
        # in different jobs are expected and resolved by the authenticated job.
        context_tokens = select(ValueMapping.placeholder_value).where(
            ValueMapping.context_id == context.id, ValueMapping.run_id.is_(None),
        )
        ambiguous = set(db.scalars(select(ValueMapping.placeholder_value).where(
            ValueMapping.context_id != context.id, ValueMapping.run_id.is_(None),
            ValueMapping.placeholder_value.in_(context_tokens),
        )))
    try:
        placeholder_map = {
            row.placeholder_value: decrypt_value(row.original_value_encrypted)
            for row in rows if row.placeholder_value not in ambiguous
        }
    except InvalidToken:
        from app.services.exporter import ExportValidationError
        raise ExportValidationError("Esleme kayitlari cozulemedi: sifreleme anahtari DB ile uyusmuyor veya kayit bozuk. Dogru DB/anahtar yedegini kontrol edin; hedef degistirilmedi.") from None
    return context, placeholder_map


# Tek bir dosyayi geri donusum icin isler: symlink/boyut/binary/encoding
# kontrollerinden gecirir, metinse placeholder'lari cozup hedefe yazar.
def _process_file_reverse(
    db: Session,
    run_id: int,
    scanned,
    dest_path: Path,
    placeholder_map: dict[str, str],
    max_inline_size: int,
    integrity_record: dict | None = None,
    context_id: int | None = None,
) -> FileUnmaskOutcome:
    rel = str(scanned.relative_path)

    read_outcome = read_scanned_file(scanned, dest_path, max_inline_size,
                                   encoding_hint=integrity_record["encoding"] if integrity_record else None,
                                   legacy_encodings=settings.encoding.legacy_text_encoding_list)

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
        return FileUnmaskOutcome(rel, status="excluded")

    if read_outcome.status == ReadStatus.SKIPPED_SYMLINK:
        db.add(AuditLog(run_id=run_id, file_path=rel, action="skipped", detail="symbolic link, not followed/copied"))
        return FileUnmaskOutcome(rel, status="skipped_symlink")

    if read_outcome.status == ReadStatus.ERROR:
        db.add(AuditLog(run_id=run_id, file_path=rel, action="error", detail=read_outcome.error))
        return FileUnmaskOutcome(rel, status="error", error=read_outcome.error)

    if read_outcome.status == ReadStatus.SKIPPED_TOO_LARGE:
        db.add(
            AuditLog(
                run_id=run_id,
                file_path=rel,
                action="skipped",
                detail=f"{read_outcome.size} bayt > {max_inline_size} bayt esigi; taranmadan kopyalandi",
            )
        )
        return FileUnmaskOutcome(rel, status="skipped_too_large")

    if read_outcome.status == ReadStatus.COPIED_BINARY:
        db.add(AuditLog(run_id=run_id, file_path=rel, action="skipped", detail="binary/metin-disi icerik"))
        return FileUnmaskOutcome(rel, status="copied_binary")

    if read_outcome.status == ReadStatus.COPIED_UNDECODABLE:
        db.add(
            AuditLog(
                run_id=run_id,
                file_path=rel,
                action="skipped",
                detail=f"tespit edilen encoding ({read_outcome.detected_encoding}) ile decode edilemedi",
            )
        )
        return FileUnmaskOutcome(rel, status="copied_undecodable")

    if read_outcome.status == ReadStatus.ARCHIVE_UNSUPPORTED:
        # Arsivler export tarafinda hicbir zaman maskelenmez (bkz.
        # exporter.py ARCHIVE politikasi) - geri cozulecek bir placeholder
        # yok, read_scanned_file zaten oldugu gibi kopyaladi (copy_unscannable
        # varsayilani True). copied_binary ile AYNI bucket: caller icin
        # anlam ayni ("icerik incelenmeden gecti").
        db.add(AuditLog(run_id=run_id, file_path=rel, action="skipped",
                        detail="arşiv içeriği taranmadı/maskelenmedi, olduğu gibi kopyalandı"))
        return FileUnmaskOutcome(rel, status="copied_binary")

    # read_outcome.status in (ReadStatus.TEXT_READY, ReadStatus.SCAN_ONLY_TEXT_READY):
    # SCAN_ONLY (bagimlilik lock dosyalari) export'ta ASLA maskelenmedi, o
    # yuzden burada da hicbir PLACEHOLDER_RE eslesmesi bulunmaz -
    # reverse_text() dogal olarak kimlik (no-op) donusumu yapar, ayri bir
    # dal gerekmez.
    text = read_outcome.text
    encoding = read_outcome.encoding

    reversed_text, resolved, unresolved = reverse_text(text, placeholder_map)
    validation_warnings = []
    rebuilt_class = None
    if read_outcome.class_document is not None:
        try:
            rebuilt_class = read_outcome.class_document.rebuild(reversed_text)
        except ClassFormatError as exc:
            db.add(AuditLog(run_id=run_id, file_path=rel, action="error", detail=str(exc)))
            return FileUnmaskOutcome(rel, status="error", error=str(exc))
    if integrity_record:
        if file_digest(scanned.absolute_path) != integrity_record["masked_sha256"]:
            validation_warnings.append("Dosya export sonrasinda degismis; birebir geri donus garanti edilemez.")
        actual_tag = source_tag(context_id, text_digest(reversed_text))
        if not hmac.compare_digest(actual_tag, integrity_record["source_tag"]):
            validation_warnings.append("Geri cozulmus metin orijinal kaynak butunluk kaydiyla uyusmuyor.")
        if rebuilt_class is not None:
            import hashlib
            byte_tag = source_tag(context_id, hashlib.sha256(rebuilt_class).hexdigest())
            if not hmac.compare_digest(byte_tag, integrity_record.get("source_bytes_tag", "")):
                validation_warnings.append("Java class baytları orijinal kaynak bütünlük kaydıyla uyuşmuyor.")
        if not validation_warnings:
            # The authenticated manifest proves both unchanged masked bytes
            # and exact reconstruction of source content. Unknown lexical
            # candidates therefore belonged to the source (e.g. SERVICE_TEST_1),
            # rather than being lost mappings. Without this proof, retain all
            # unresolved diagnostics, including for legacy/no-manifest inputs.
            unresolved = []

    try:
        if read_outcome.class_document is not None:
            write_text_preserving_encoding(dest_path, reversed_text, encoding,
                                           class_document=read_outcome.class_document)
            used_encoding = encoding
        else:
            used_encoding = write_text_preserving_encoding(dest_path, reversed_text, encoding)
        if used_encoding != encoding:
            validation_warnings.append("Orijinal encoding degeri tasiyamadi; cikti UTF-8 olarak yazildi.")
        dest_path.chmod(integrity_record["mode"] if integrity_record else scanned.absolute_path.stat().st_mode & 0o777)
    except OSError as exc:
        db.add(AuditLog(run_id=run_id, file_path=rel, action="error", detail=f"yazma basarisiz: {exc}"))
        return FileUnmaskOutcome(rel, status="error", error=str(exc))

    found = resolved + len(unresolved)
    for token in unresolved:
        db.add(
            AuditLog(
                run_id=run_id,
                file_path=rel,
                action="error",
                detail=f"placeholder icin eslesme kaydi bulunamadi: {token}",
            )
        )
    if resolved:
        db.add(
            AuditLog(
                run_id=run_id,
                file_path=rel,
                action="replaced",
                detail=f"{resolved} placeholder gercek degerle degistirildi",
            )
        )

    if found == 0:
        return FileUnmaskOutcome(rel, status="copied_no_placeholders", validation_warnings=validation_warnings)
    if resolved == 0:
        return FileUnmaskOutcome(
            rel, status="unresolved_only", placeholders_found=found, unresolved_placeholders=unresolved,
            validation_warnings=validation_warnings,
        )
    return FileUnmaskOutcome(
        rel,
        status="reversed",
        placeholders_found=found,
        placeholders_resolved=resolved,
        unresolved_placeholders=unresolved,
        validation_warnings=validation_warnings,
    )


# Geri donusum (unmask) akisinin ana giris noktasi: maskelenmis bir
# klasoru tarar, placeholder'lari gercek degerlerle degistirir, hedefe
# yazar ve sonunda ozet bir rapor uretir.
def unmask_project(
    db: Session,
    *,
    source_path: str,
    project_name: str,
    sicil_no: str,
    branch_name: str,
    target_path: str,
    initiated_by: str,
    max_inline_size: int = DEFAULT_MAX_INLINE_SIZE,
    progress_callback: Callable[[int, int, str], None] | None = None,
    job_id: int | None = None,
) -> UnmaskReport:
    source = Path(source_path).resolve()
    target = Path(target_path).resolve()
    _validate_paths(source, target)

    # Security gate: resolve identity BEFORE touching the filesystem at all.
    context = _load_identity(db, project_name, sicil_no, branch_name)
    manifest = read_manifest(source, context.id)
    if manifest is not None and manifest["version"] == 2:
        if job_id is not None and job_id != manifest["job_id"]:
            raise ValueError("Secilen job_id, dosya paketinin maskeleme islem kimligiyle eslesmiyor; hedef degistirilmedi.")
        job_id = manifest["job_id"]
        job = db.get(MaskingRun, job_id)
        if job is None or job.mapping_version != 2:
            raise ValueError("Paketin maskeleme islem kaydi bulunamadi veya esleme surumu uyusmuyor.")
    elif manifest is not None and job_id is not None:
        job = db.get(MaskingRun, job_id)
        if job is None or job.mapping_version != 1:
            raise ValueError("Eski cikti paketi yeni bir maskeleme islemiyle geri alinamaz.")
    context, placeholder_map = load_context_mappings(
        db, project_name, sicil_no, branch_name, job_id=job_id,
        allow_legacy=manifest is not None and manifest["version"] == 1,
    )

    run = MaskingRun(
        context_id=context.id,
        operation_type="unmask",
        source_path=str(source),
        target_path=str(target),
        initiated_by=initiated_by,
        status="in_progress",
    )
    db.add(run)
    db.flush()

    report = UnmaskReport(
        run_id=run.id,
        context_id=context.id,
        project_name=project_name,
        sicil_no=sicil_no,
        branch_name=branch_name,
        source_path=str(source),
        target_path=str(target),
        started_at=datetime.now(timezone.utc),
        mappings_loaded=len(placeholder_map),
        job_id=job_id,
    )
    if job_id is not None:
        db.add(AuditLog(run_id=run.id, file_path="", action="matched", detail=f"source_masking_job_id={job_id}"))

    failed = False
    publication = None
    try:
        exclude_specs = load_active_exclude_specs(db)

        all_files = [item for item in iter_project_files(source, exclude_specs)
                     if item.relative_path.as_posix() != MANIFEST_NAME]
        if manifest is None:
            report.validation_warnings.append("Butunluk kaydi yok; eski veya tekil dosya geri cozumunde eksik/degistirilmis dosyalar ve encoding tamligi dogrulanamaz.")
        else:
            actual_files = {item.relative_path.as_posix() for item in all_files}
            expected_files = set(manifest["files"])
            if not manifest["complete"]:
                report.validation_warnings.append("Kaynak export eksik dosyalarla olusturulmus; cikti projenin tam kopyasi degildir.")
            if expected_files - actual_files:
                report.validation_warnings.append(f"Butunluk kaydindaki {len(expected_files - actual_files)} dosya kaynakta eksik.")
            if actual_files - expected_files:
                report.validation_warnings.append(f"{len(actual_files - expected_files)} dosya icin export butunluk kaydi yok.")
        path_resolver = PathPlaceholderResolver(placeholder_map)
        path_plans = {}
        claimed_paths: dict[str, Path] = {}
        for scanned in all_files:
            if scanned.excluded_by is not None or scanned.is_symlink:
                path_plans[scanned.relative_path] = (scanned.relative_path, 0, [])
                continue
            try:
                plan = path_resolver.reverse(scanned.relative_path)
            except UnsafeUnmaskPathError as exc:
                path_plans[scanned.relative_path] = exc
                continue
            key = plan[0].as_posix().casefold()
            if key in claimed_paths:
                raise UnsafeUnmaskPathError("geri cozum yol cakismasi: iki dosya ayni hedefe donusuyor")
            claimed_paths[key] = scanned.relative_path
            path_plans[scanned.relative_path] = plan
        publication = OutputPublication(target, run.id)
        total_files = len(all_files)
        for index, scanned in enumerate(all_files, start=1):
            rel = str(scanned.relative_path)
            plan = path_plans[scanned.relative_path]
            if isinstance(plan, UnsafeUnmaskPathError):
                exc = plan
                db.add(
                    AuditLog(
                        run_id=run.id, file_path=rel, action="error",
                        detail=f"hedef yolu guvensiz oldugu icin dosya atlandi: {exc}",
                    )
                )
                report.record(FileUnmaskOutcome(rel, status="error", error=str(exc)))
                if progress_callback is not None:
                    progress_callback(index, total_files, rel)
                continue

            restored_path, path_resolved, path_unresolved = plan
            dest_path = publication.stage / restored_path
            integrity_record = manifest["files"].get(scanned.relative_path.as_posix()) if manifest else None
            outcome = _process_file_reverse(db, run.id, scanned, dest_path, placeholder_map, max_inline_size,
                                            integrity_record=integrity_record, context_id=context.id)
            outcome.placeholders_found += path_resolved + len(path_unresolved)
            outcome.placeholders_resolved += path_resolved
            outcome.unresolved_placeholders.extend(path_unresolved)
            if path_resolved:
                db.add(AuditLog(run_id=run.id, file_path=rel, action="replaced",
                                detail=f"dosya yolunda {path_resolved} placeholder geri cozuldu"))
            for token in path_unresolved:
                db.add(AuditLog(run_id=run.id, file_path=rel, action="error",
                                detail=f"dosya yolunda placeholder icin eslesme kaydi bulunamadi: {token}"))
            if outcome.status in ("copied_no_placeholders", "unresolved_only"):
                if outcome.placeholders_resolved:
                    outcome.status = "reversed"
                elif outcome.unresolved_placeholders:
                    outcome.status = "unresolved_only"
            report.record(outcome)
            if progress_callback is not None:
                progress_callback(index, total_files, rel)

        if report.has_unresolved_placeholders and (job_id is None or mapping_scope_for_run(db, job_id) is None):
            report.identity_mismatch_suggestion = IdentityMismatchAdvisor(db).suggest(
                current_context_id=context.id,
                unresolved_tokens=list(report.unresolved_by_placeholder.keys()),
                total_found=report.total_placeholders_found,
            )

        for notice in report.validation_warnings:
            db.add(AuditLog(run_id=run.id, file_path="", action="error", detail=f"validation_warning: {notice}"))

        run.status = "completed_with_warnings" if (
            report.has_unresolved_placeholders or report.files_errored
            or report.files_skipped_too_large or report.files_copied_undecodable
            or report.files_copied_binary or report.files_excluded
            or report.files_skipped_symlink or report.validation_warnings
        ) else "completed"
        report.status = run.status
        report.completed_at = datetime.now(timezone.utc)
        run.completed_at = report.completed_at
        run.files_scanned = report.files_scanned
        run.match_count = report.total_placeholders_found
        publish_run(db, run, report, publication)
    except BaseException:
        failed = True
        report.status = "failed"
        db.rollback()
        raise
    finally:
        if publication is not None:
            publication.close()
        if failed:
            report.completed_at = datetime.now(timezone.utc)

    return report
