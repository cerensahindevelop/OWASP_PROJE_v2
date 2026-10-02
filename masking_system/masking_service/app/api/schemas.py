"""FastAPI backend'in HTTP sinirindaki Pydantic request/response modelleri.

Servis katmani (app/services/*) dataclass/ORM nesneleri dondurur; bunlarin
HICBIRI dogrudan HTTP yanitina yazilmaz - burada tanimli modeller, sadece
disariya acilmasi gereken alanlari secerek bir kopyasini uretir (orn.
FilterRule.regex_pattern - sifreli olabilen ham desen - CLI'nin kural-listele
ciktisinda da gosterilmedigi icin burada da disariya acilmaz).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class IdentityMixin(BaseModel):
    project_name: str
    sicil_no: str
    branch_name: str


# --------------------------------------------------------------------------
# Kurallar (filtre_kurallari)
# --------------------------------------------------------------------------


class RuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rule_name: str
    category: str
    source_layer: str
    pattern_type: str
    placeholder_prefix: str
    entity_type: str | None
    confidence_score: float
    is_allow_list: bool
    priority: int
    is_active: bool
    description: str | None


class RuleCreateIn(BaseModel):
    rule_name: str
    placeholder_format: str
    pattern_type: str = "regex"
    regex_pattern: str | None = None
    regex_flags: str | None = None
    validator_name: str | None = None
    category: str | None = None
    priority: int | None = None
    description: str | None = None
    is_active: bool = True
    source_layer: str = "katman1"
    entity_type: str | None = None
    confidence_score: float = 0.85
    is_allow_list: bool = False


class RuleActiveIn(BaseModel):
    is_active: bool


# --------------------------------------------------------------------------
# Gecmis / audit (masking_runs, denetim_kaydi)
# --------------------------------------------------------------------------


class RunSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    file_path: str
    action: str
    detail: str | None
    created_at: datetime


# --------------------------------------------------------------------------
# Onay kuyrugu (gozden_gecirme_kuyrugu)
# --------------------------------------------------------------------------


class FileMaskResultOut(BaseModel):
    run_id: int
    file_path: str
    written: bool
    message: str


class ReviewQueueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int | None
    file_path: str
    line_number: int | None
    found_value: str | None
    entity_type: str
    confidence_level: str
    reason: str | None
    surrounding_context: str | None
    status: str
    created_at: datetime


# --------------------------------------------------------------------------
# Denetim uyarilari (denetim_uyarilari)
# --------------------------------------------------------------------------


class AuditEvidenceOut(BaseModel):
    line: int | None = None
    column: int | None = None
    found_value: str = ""
    excerpt: str = ""
    label: str = ""


class AuditWarningOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    file_path: str
    reasoning: str
    audit_failed: bool
    status: str
    created_at: datetime
    summary: str = ""
    location: str = ""
    next_step: str = ""
    evidence: list[AuditEvidenceOut] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Kurumsal terim sozlugu
# --------------------------------------------------------------------------


class TermPreviewItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    term: str
    reason: str | None = None


class TermUploadPreviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    filename: str
    category: str
    total_found: int
    new_valid: list[str]
    new_suspicious: list[TermPreviewItemOut]
    already_registered: list[str]
    rejected: list[TermPreviewItemOut]
    new_count: int
    existing_count: int
    suspicious_count: int
    rejected_count: int


class TermUploadResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    filename: str
    category: str
    added_count: int
    skipped_count: int
    rejected_count: int
    suspicious_added_count: int


class CorporateTermOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rule_name: str
    term: str
    category: str
    placeholder_prefix: str
    is_active: bool
    inactive_reason: str | None = None
    mapping_count: int
    deleted_at: datetime | None = None


class CorporateTermCreateIn(BaseModel):
    term: str
    title: str
    confirmed_sensitive: bool


class CorporateTermActivateIn(BaseModel):
    confirmed_sensitive: bool


# --------------------------------------------------------------------------
# Export (mask)
# --------------------------------------------------------------------------


class FileOutcomeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    relative_path: str
    status: str
    match_count: int = 0
    rule_breakdown: dict[str, int] = {}
    error: str | None = None
    final_state: str | None = None
    failed_check: str | None = None


class ExportReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: int
    context_id: int
    project_name: str
    sicil_no: str
    branch_name: str
    source_path: str
    target_path: str
    started_at: datetime
    completed_at: datetime | None
    target_overwritten: bool
    files_scanned: int
    files_masked: int
    files_copied_text_no_match: int
    files_copied_binary: int
    files_skipped_unsupported: int = 0
    files_copied_undecodable: int
    files_skipped_symlink: int
    files_skipped_too_large: int
    files_excluded: int
    files_errored: int
    files_quarantined_pending_audit: int
    files_failed_syntax_validation: int
    files_failed_round_trip_validation: int
    files_failed_consistency_validation: int
    files_failed_finalization: int = 0
    files_archive_unsupported: int = 0
    files_scan_only_clean: int = 0
    files_scan_only_sensitive: int = 0
    files_failed_detection: int = 0
    files_ready: int = 0
    files_review_required: int = 0
    files_security_quarantine: int = 0
    files_validation_failed: int = 0
    total_matches: int
    matches_by_rule: dict[str, int]
    outcomes: list[FileOutcomeOut]
    status: str
    validation_warnings: list[str] = []
    degraded_detectors: list[str] = []
    blocked_by_check: dict[str, int] = {}
    llm_usage_summary: dict[str, float] = {}


class ExportPathRequest(IdentityMixin):
    source_path: str
    target_path: str
    initiated_by: str


class ExportResultOut(BaseModel):
    report: ExportReportOut
    pending_count: int
    quarantined_count: int
    validation_failed_count: int = 0
    # Yukleme modunda doldurulur - GET /export/outputs/{output_token}/download
    # bu token'i kullanir (bkz. app/webapp/uploads.py::uploaded_output_dir). Yol
    # modunda None: cikti zaten dogrudan sunucudaki target_path'e yazildi,
    # ayrica indirilecek bir sey yok.
    output_token: str | None = None


class ExportJobStartOut(BaseModel):
    job_id: str


class ExportJobOut(BaseModel):
    job_id: str
    status: str
    processed: int = 0
    total: int = 0
    current_file: str | None = None
    result: ExportResultOut | None = None
    error_message: str | None = None
    error_detail: str | None = None
    error_status: int | None = None


# --------------------------------------------------------------------------
# Unmask
# --------------------------------------------------------------------------


class IdentityMismatchSuggestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_name: str
    sicil_no: str
    branch_name: str
    matched_count: int
    unresolved_count: int
    match_ratio: float


class FileUnmaskOutcomeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    validation_warnings: list[str] = []

    relative_path: str
    status: str
    placeholders_found: int = 0
    placeholders_resolved: int = 0
    unresolved_placeholders: list[str] = []
    error: str | None = None


class UnmaskReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    validation_warnings: list[str] = []
    job_id: int | None = None

    run_id: int
    context_id: int
    project_name: str
    sicil_no: str
    branch_name: str
    source_path: str
    target_path: str
    started_at: datetime
    completed_at: datetime | None
    target_overwritten: bool
    mappings_loaded: int
    files_scanned: int
    files_reversed: int
    files_unresolved_only: int
    files_copied_no_placeholders: int
    files_copied_binary: int
    files_copied_undecodable: int
    files_skipped_symlink: int
    files_skipped_too_large: int
    files_excluded: int
    files_errored: int
    total_placeholders_found: int
    total_placeholders_resolved: int
    total_placeholders_unresolved: int
    unresolved_by_placeholder: dict[str, int]
    outcomes: list[FileUnmaskOutcomeOut]
    status: str
    identity_mismatch_suggestion: IdentityMismatchSuggestionOut | None
    # UnmaskReport'ta bir @property (total_placeholders_unresolved > 0) -
    # from_attributes=True bunu da normal bir alan gibi getattr ile okur;
    # webapp/import_page.py bu degeri dogrudan kullandigi icin (bkz. plan
    # dosyasi) burada acikca bir alan olarak tasinir.
    has_unresolved_placeholders: bool


class UnmaskPathRequest(IdentityMixin):
    job_id: int | None = Field(default=None, ge=1)
    source_path: str
    target_path: str
    initiated_by: str


class UnmaskPathResultOut(BaseModel):
    report: UnmaskReportOut


# Upload-modu unmask TEK istekte hesaplanip indirilebilir bayt dizisini
# tabanindaki JSON govdeye gomer (base64) - export'un aksine, gercek/cozulmus
# hassas veri icerdigi icin diskte kalici bir "sonra indir" token'i/klasoru
# BILEREK yok (bkz. plan dosyasindaki kullanici karari).
class UnmaskUploadResultOut(BaseModel):
    report: UnmaskReportOut
    download_base64: str | None
    download_filename: str | None
