"""LLM09: denetim alintilari ("llm_audit") sezgisel kaynak gruptadir.

Kayit otoritesinde en fazla 'weak' (projeye sezgisel yayilma), asla
'authoritative'; cakisma cozumunde LLM ile ayni sezgisel otorite.
"""

from __future__ import annotations

from app.services.consistency_masking import registry_authority
from app.services.detectors import DetectionResult, synthetic_llm_rule
from app.services.overlap_resolver import _AUTHORITY_RANK, _authority
from app.services.rule_engine import Match


def _match(value: str, source: str, confidence: str) -> Match:
    rule = synthetic_llm_rule("DENETIM_BULGUSU")
    return Match(rule=rule, original_value=value, start=0, end=len(value), entity_type=rule.category,
                 source_detector=source, confidence=confidence)


def test_llm_audit_is_at_most_weak_in_registry():
    for confidence in ("yuksek", "orta", "dusuk"):
        assert registry_authority("Hakan Yilmaz", "llm_audit", _match("Hakan Yilmaz", "llm_audit", confidence)) == "weak"


def test_llm_audit_generic_or_short_values_are_not_registered():
    for value in ("client", "abc", "42", "&&"):
        assert registry_authority(value, "llm_audit", _match(value, "llm_audit", "orta")) is None


def test_llm_audit_has_heuristic_overlap_authority():
    result = DetectionResult(deger="x", tip="DENETIM_BULGUSU", guven_seviyesi="yuksek", kaynak_motor="llm_audit",
                             start=0, end=1, rule=synthetic_llm_rule("DENETIM_BULGUSU"))
    assert _authority(result) == _AUTHORITY_RANK["heuristic"]
