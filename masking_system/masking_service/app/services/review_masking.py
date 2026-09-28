"""Apply human-authorized findings using the export mapping/restore contract."""
from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import select

from app.core.crypto import decrypt_value
from app.db.models import MaskingContext, ValueMapping
from app.services.detectors import DetectionResult, synthetic_llm_rule
from app.services.token_boundary_validator import TokenBoundaryValidator
from app.services.mapping_service import (
    DetectionOutcome, MaskingRunContext, apply_detections, mapping_scope_for_run,
)
from app.services.roundtrip_validator import verify_round_trip
from app.services.rule_engine import Match, PLACEHOLDER_RE, JSON_NUMERIC_PLACEHOLDER_RE, reverse_text


def _reverse_map(db, run):
    return {row.placeholder_value: decrypt_value(row.original_value_encrypted)
            for row in db.scalars(select(ValueMapping).where(
                ValueMapping.context_id == run.context_id,
                ValueMapping.run_id == mapping_scope_for_run(db, run.id),
            )).all()}


def mask_review_values(db, run, content: str, file_path: str, values: list[tuple[str, str]]) -> str:
    """Mask exact occurrences once, protect existing tokens, verify exact restoration."""
    original = reverse_text(content, _reverse_map(db, run))[0]
    protected = [m.span() for pattern in (PLACEHOLDER_RE, JSON_NUMERIC_PLACEHOLDER_RE)
                 for m in pattern.finditer(content)]
    candidates = []
    for value, entity_type in dict.fromkeys(values):
        if not value or not value.strip():
            continue
        rule = synthetic_llm_rule(entity_type)
        for found in re.finditer(re.escape(value), content):
            start, end = found.span()
            if Path(file_path).name == ".env" or Path(file_path).name.startswith(".env.") or Path(file_path).suffix == ".env":
                assignment = re.fullmatch(r"(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*=\s*(?P<value>[^\r\n]+)", value)
                if assignment:
                    start += assignment.start("value")
            if any(start < b and a < end for a, b in protected):
                continue
            candidates.append(Match(rule=rule, original_value=value, start=start, end=end))
    validated, rejections = TokenBoundaryValidator().validate(content, [
        DetectionResult(deger=content[m.start:m.end], tip=m.rule.category, guven_seviyesi="yuksek",
                        kaynak_motor="dictionary", start=m.start, end=m.end, rule=m.rule)
        for m in candidates
    ], file_path=file_path)
    if rejections:
        raise ValueError("Riskli ifade güvenli maskeleme sınırlarına ayrıştırılamadı; dosya değiştirilmedi.")
    candidates = []
    for result in validated:
        if any(result.start < b and a < result.end for a, b in protected):
            raise ValueError("Riskli ifade mevcut bir yer tutucuyla çakışıyor; dosya değiştirilmedi.")
        candidates.append(Match(rule=result.rule, original_value=result.deger, start=result.start, end=result.end))
    selected = []
    for candidate in sorted(candidates, key=lambda m: (-(m.end - m.start), m.start)):
        if not any(candidate.start < old.end and old.start < candidate.end for old in selected):
            selected.append(candidate)
    if not selected:
        raise ValueError("Maskelenecek ifade dosyada bulunamadı; dosya değiştirilmedi.")
    outcome = DetectionOutcome(matches=sorted(selected, key=lambda m: m.start), review_results=[],
                               llm_errors=[], already_masked_spans=[], overlap_conflicts=[], boundary_rejections=[])
    context = db.get(MaskingContext, run.context_id)
    masked, _ = apply_detections(db, MaskingRunContext(context=context, run_id=run.id),
                                 text=content, outcome=outcome, file_path=file_path)
    if not verify_round_trip(original, masked, _reverse_map(db, run)).ok:
        raise ValueError("Otomatik maskeleme geri dönüş doğrulamasından geçemedi; dosya çıktıya eklenmedi.")
    return masked
