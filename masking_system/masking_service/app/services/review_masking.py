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
from app.services.rule_engine import Match, PLACEHOLDER_RE, JSON_NUMERIC_PLACEHOLDER_RE, RuleSpec, reverse_text


def _reverse_map(db, run):
    return {row.placeholder_value: decrypt_value(row.original_value_encrypted)
            for row in db.scalars(select(ValueMapping).where(
                ValueMapping.context_id == run.context_id,
                ValueMapping.run_id == mapping_scope_for_run(db, run.id),
            )).all()}


class NarrowedValueError(ValueError):
    """Sinir dogrulamasi degeri daraltti ve atilan kisimda harf/rakam kaldi."""


def mask_review_values(db, run, content: str, file_path: str, values: list[tuple[str, str]]) -> str:
    """Mask exact occurrences once, protect existing tokens, verify exact restoration."""
    original = reverse_text(content, _reverse_map(db, run))[0]
    context = db.get(MaskingContext, run.context_id)
    masked, _, _ = mask_known_values(
        db, MaskingRunContext(context=context, run_id=run.id), content, file_path,
        # Inceleme ekraninda bir insanin onayladigi degerler: sozluk yetkisi.
        [(value, synthetic_llm_rule(entity_type), "dictionary") for value, entity_type in dict.fromkeys(values)],
    )
    if not verify_round_trip(original, masked, _reverse_map(db, run)).ok:
        raise ValueError("Otomatik maskeleme geri dönüş doğrulamasından geçemedi; dosya çıktıya eklenmedi.")
    return masked


# Yalnizca sozluk kurali ya da insan onayi kesin sayilir; diger kaynaklar
# (denetim LLM'i dahil) yuksek guven/Katman 1 yetkisi kazanmaz.
def _confidence_for(source: str) -> str:
    return "yuksek" if source == "dictionary" else "orta"


def mask_known_values(
    db, run_ctx: MaskingRunContext, content: str, file_path: str,
    values: list[tuple[str, RuleSpec, str]],
    *,
    reject_narrowed_content: bool = False,
) -> tuple[str, list[Match], list[ValueMapping]]:
    """Degeri kesin bilinen ifadeleri (value, kural, kaynak) maskeler.

    Her gecis kendi kaynagiyla (source) TokenBoundaryValidator'dan gecer:
    yalnizca "dictionary" (sozluk kurali ya da insan onayi) identifier
    sinirina genisletme yetkisi alir. Denetim LLM'inin alintilari
    ("llm_audit") sezgisel kalir ve "yuksek" guven kazanmaz (LLM09).
    Tek bir gecis bile guvenli
    sinira oturtulamazsa ya da mevcut bir yer tutucuyla cakisirsa hicbir sey
    degistirilmez (ValueError). Donus: (maskeli metin, eslesmeler, eslemeler);
    eslesmeler ve eslemeler ayni sirada, tutarlilik registry'sine verilebilir.
    Geri donus dogrulamasi cagiranin sorumlulugundadir.

    reject_narrowed_content=True: dogrulayici bir gecisi daraltirken
    (orn. `sifre": "Abc123` -> `Abc123`) disarida kalan kisimda harf/rakam
    varsa NarrowedValueError - o kisim acik kalirdi. Yalnizca tirnak/iki
    nokta/bosluk gibi isaretler atiliyorsa daraltma kabul edilir.
    """
    protected = [m.span() for pattern in (PLACEHOLDER_RE, JSON_NUMERIC_PLACEHOLDER_RE)
                 for m in pattern.finditer(content)]
    source_by_rule: dict[str, str] = {}
    candidates = []
    for value, rule, source in dict.fromkeys(values):
        if not value or not value.strip():
            continue
        if source_by_rule.setdefault(rule.rule_name, source) != source:
            raise ValueError("Ayni kural farkli kaynaklarla verildi; dosya degistirilmedi.")
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
        DetectionResult(deger=content[m.start:m.end], tip=m.rule.category,
                        guven_seviyesi=_confidence_for(source_by_rule[m.rule.rule_name]),
                        kaynak_motor=source_by_rule[m.rule.rule_name], start=m.start, end=m.end, rule=m.rule)
        for m in candidates
    ], file_path=file_path)
    if rejections:
        raise ValueError("Riskli ifade güvenli maskeleme sınırlarına ayrıştırılamadı; dosya değiştirilmedi.")
    if reject_narrowed_content:
        for candidate in candidates:
            covered = [(max(r.start, candidate.start), min(r.end, candidate.end))
                       for r in validated if r.start < candidate.end and candidate.start < r.end]
            if any(content[pos].isalnum() for pos in range(candidate.start, candidate.end)
                   if not any(a <= pos < b for a, b in covered)):
                raise NarrowedValueError("Riskli ifadenin bir kısmı maskelenemiyor; dosya değiştirilmedi.")
    candidates = []
    for result in validated:
        if any(result.start < b and a < result.end for a, b in protected):
            raise ValueError("Riskli ifade mevcut bir yer tutucuyla çakışıyor; dosya değiştirilmedi.")
        candidates.append(Match(
            rule=result.rule, original_value=result.deger, start=result.start, end=result.end,
            entity_type=result.rule.category, source_detector=source_by_rule.get(result.rule.rule_name),
            confidence=result.guven_seviyesi,
        ))
    selected = []
    for candidate in sorted(candidates, key=lambda m: (-(m.end - m.start), m.start)):
        if not any(candidate.start < old.end and old.start < candidate.end for old in selected):
            selected.append(candidate)
    if not selected:
        raise ValueError("Maskelenecek ifade dosyada bulunamadı; dosya değiştirilmedi.")
    selected.sort(key=lambda m: m.start)
    outcome = DetectionOutcome(matches=selected, review_results=[],
                               llm_errors=[], already_masked_spans=[], overlap_conflicts=[], boundary_rejections=[])
    masked, mappings = apply_detections(db, run_ctx, text=content, outcome=outcome, file_path=file_path)
    return masked, selected, mappings
