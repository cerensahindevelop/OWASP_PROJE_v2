"""Regression tests for the failure pattern seen on a real ERP export:

* 218 files dropped by the final consistency pass because (a) a replacement
  fused with the following word/digits (`mask_organization_233noprint_wrappers`,
  length mismatch on restore) and (b) a generic LLM value such as `default`
  was propagated to every file and broke TS/TSX syntax;
* small files quarantined with `finish_reason=length` without saying why.
"""
from __future__ import annotations

import json

import pytest

from app.services.consistency_masking import (
    SensitiveValueRegistry,
    apply_consistency_replacements,
    find_consistency_occurrences,
)
from app.services.detectors import synthetic_llm_rule
from app.services.llm_recognizer import LLMTruncatedError, require_complete_response
from app.services.rule_engine import Match, reverse_text
from app.services.term_classifier import is_generic_code_token


class _Mapping:
    def __init__(self, value):
        self.original_value_plain = value


def _registry(*values, source="llm", confidence="yuksek", entity_type="ORGANIZATION", rule=None):
    registry = SensitiveValueRegistry()
    matches = [
        Match(rule=rule or synthetic_llm_rule(entity_type), original_value=v, start=0, end=len(v),
              entity_type=entity_type, source_detector=source, confidence=confidence)
        for v in values
    ]
    registry.add_successful_matches(matches, [_Mapping(v) for v in values])
    return registry


def _regex_rule():
    from app.services.rule_engine import RuleSpec
    return RuleSpec(id=1, rule_name="email_address", category="email", pattern_type="regex",
                    regex_pattern=r"\S+@\S+", regex_flags=None, placeholder_prefix="mask_email", priority=10)


@pytest.mark.parametrize("text,file_path", [
    ('const args = "-of default=noprint_wrappers=1";\n', "a.js"),
    ("Surum notu: `ERP-2026` yayinda\n", "notes.md"),
    ('url = "https://opsQorvexa.example"\n', "cfg.py"),
])
def test_replacement_never_fuses_with_neighbour_text(text, file_path):
    registry = _registry("default=", "ERP-", "Qorvexa.example", source="dictionary")
    occurrences = find_consistency_occurrences(text, registry, file_path=file_path)
    masked = apply_consistency_replacements(
        text, [(occ, f"mask_organization_{i + 1}") for i, occ in enumerate(occurrences)]
    )
    reverse_map = {f"mask_organization_{i + 1}": occ.original_value for i, occ in enumerate(occurrences)}
    restored, _, unresolved = reverse_text(masked, reverse_map)
    assert restored == text and not unresolved


def test_separated_occurrences_are_still_masked():
    text = 'vendor: "Qorvexa Labs"\n# Qorvexa Labs ile anlasma\n'
    occurrences = find_consistency_occurrences(text, _registry("Qorvexa Labs"), file_path="a.yml")
    assert len(occurrences) == 2


# --- Kok neden 1: registry kalite kapisi ----------------------------------------

@pytest.mark.parametrize("value,entity_type,source,confidence", [
    ("&&", "ORGANIZATION", "katman2_presidio", "orta"),
    ("useEffect", "ORGANIZATION", "katman2_presidio", "yuksek"),
    ("useCallback", "ORGANIZATION", "katman2_presidio", "yuksek"),
    ("0", "DATE_TIME", "katman2_presidio", "orta"),
    ("2026-08-02", "DATE_TIME", "katman2_presidio", "yuksek"),
    ("z0", "US_DRIVER_LICENSE", "katman2_presidio", "orta"),
    ("Turkish", "NRP", "katman2_presidio", "orta"),
    ("Istanbul", "LOCATION", "katman2_presidio", "yuksek"),
    ("default=", "ORGANIZATION", "llm", "yuksek"),
    ("@", "EMAIL", "llm", "yuksek"),
    ("Qorvexa", "KURUM_JARGONU", "llm", "orta"),   # orta: yerelde maskelenir, yayilmaz
    ("ERP", "KURUM_JARGONU", "llm", "yuksek"),     # < 4 karakter
    ("1234", "KIMLIK_NO", "llm", "yuksek"),        # kisa salt sayi
    ("0", "DATE_TIME", "llm", "yuksek"),
    ("export", "KURUM_JARGONU", "llm", "yuksek"),  # anahtar kelime
])
def test_weak_or_malformed_values_never_enter_registry(value, entity_type, source, confidence):
    registry = _registry(value, source=source, confidence=confidence, entity_type=entity_type)
    assert len(registry) == 0


def test_strong_values_enter_registry():
    assert len(_registry("7650321", entity_type="KIMLIK_NO")) == 1  # uzun kimlik no, yuksek LLM
    assert _registry("Qorvexa", confidence="yuksek").entries()[0].authoritative is False
    assert _registry("Hakan Yilmaz", source="katman2_presidio", entity_type="PERSON").entries()[0].authoritative is False
    assert _registry("ops@corp.example", source="katman2_presidio", entity_type="EMAIL_ADDRESS").entries()[0].authoritative
    # Deterministik kaynaklar kisa/sayisal olsa bile otoritedir (sicil no vb.).
    assert _registry("12", source="dictionary").entries()[0].authoritative
    assert _registry("ops@corp.example", source="unknown", rule=_regex_rule()).entries()[0].authoritative


def test_punctuation_only_value_is_rejected_even_from_rules():
    assert len(_registry("&&", source="dictionary")) == 0


# --- Kok neden 2: zayif kaynakli degerler ciplak koda yayilmaz --------------------

def test_weak_value_only_replaced_in_strings_and_comments_of_code_files():
    registry = _registry("Qorvexa", confidence="yuksek")
    code = 'import { Qorvexa } from "./x";\nconst a = "Qorvexa";\n// Qorvexa notu\nQorvexa();\n'
    occurrences = find_consistency_occurrences(code, registry, file_path="page.tsx")
    kinds = sorted(code[o.start - 1] for o in occurrences)
    assert len(occurrences) == 2 and kinds == [" ", '"']
    # Kod disi dosyada her gecis maskelenir.
    assert len(find_consistency_occurrences("Qorvexa ekibi, Qorvexa.\n", registry, file_path="notes.md")) == 2


def test_authoritative_value_is_replaced_in_bare_code():
    registry = _registry("Qorvexa", source="dictionary")
    code = "const client = Qorvexa;\n"
    assert len(find_consistency_occurrences(code, registry, file_path="a.ts")) == 1


@pytest.mark.parametrize("value,generic", [
    ("default", True), ("Default", True), ("8080", True), ("7650321", False), ("'default'", True), ("export", True), ("SELECT", True),
    ("Qorvexa", False), ("atlas-billing-core", False), ("Hakan Yilmaz", False),
])
def test_is_generic_code_token(value, generic):
    assert is_generic_code_token(value) is generic


def test_generic_llm_finding_is_not_masked_in_first_pass(monkeypatch):
    import asyncio
    from app.services.detectors import DetectionResult, DetectorOutput
    from app.services.mapping_service import detect_matches

    text = "export default function Page() { return null }\n"

    class Orchestrator:
        decision_policy = None

        async def scan(self, content, metadata=None):
            start = content.index("default")
            return DetectorOutput(results=[DetectionResult(
                "default", "ORGANIZATION", "yuksek", "llm", "x", start, start + 7)])

    outcome = asyncio.run(detect_matches(Orchestrator(), text, {"file_path": "page.tsx"}))
    assert outcome.matches == [] and outcome.review_results == []
    assert [r.deger for r in outcome.ignored_llm_results] == ["default"]


def _truncated(message):
    return {"choices": [{"finish_reason": "length", "message": message}]}


def test_truncation_reports_thinking_mode():
    with pytest.raises(LLMTruncatedError, match="DUSUNME"):
        require_complete_response(_truncated({"content": "", "reasoning_content": "Let me think..."}))
    with pytest.raises(LLMTruncatedError, match="DUSUNME"):
        require_complete_response(_truncated({"content": "<think>hmm"}))


def test_truncation_reports_repetition_loop():
    looping = '{"bulgular": [' + '{"bulunan_deger": "x", "tip": "IP"}, ' * 40
    with pytest.raises(LLMTruncatedError, match="donguye"):
        require_complete_response(_truncated({"content": looping}))


def test_presence_penalty_is_sent_only_when_configured():
    from app.services.audit_reviewer import build_audit_request
    from app.services.llm_recognizer import build_detection_request

    assert "presence_penalty" not in build_detection_request("t", "m", 1)
    assert build_detection_request("t", "m", 1, presence_penalty=1.5)["presence_penalty"] == 1.5
    assert build_audit_request("t", "m", 1, presence_penalty=1.5)["presence_penalty"] == 1.5


# --- Uctan uca: gercek calismadaki desen --------------------------------------

def test_ner_noise_in_markdown_does_not_break_code_files(tmp_path, monkeypatch):
    """md'de NER `useEffect`/`&&`/`0` isaretler; tsx/js dosyalari dusmemeli."""
    import asyncio
    from app.db.session import SessionLocal
    from app.services import exporter as exporter_module
    from app.services.detectors import DetectionResult, DetectorOutput
    from tests.test_review_rate_and_release import _cleanup, _project

    class NoisyNer:
        decision_policy = None
        registry = None

        async def scan(self, content, metadata=None):
            if not str((metadata or {}).get("file_path", "")).endswith(".md"):
                return DetectorOutput()
            results = []
            for value, tip in (("useEffect", "ORGANIZATION"), ("&&", "ORGANIZATION"), ("0", "DATE_TIME")):
                start = content.find(value)
                if start >= 0:
                    results.append(DetectionResult(value, tip, "yuksek", "katman2_presidio", "ner",
                                                   start, start + len(value), synthetic_llm_rule(tip)))
            return DetectorOutput(results=results)

    monkeypatch.setattr(exporter_module, "build_orchestrator", lambda *a, **k: NoisyNer())
    monkeypatch.setattr(exporter_module.settings.vllm, "enabled", False)
    project = _project()
    source = tmp_path / "src"
    (source / "docs").mkdir(parents=True)
    (source / "docs" / "plan.md").write_text(
        "Adim 0: useEffect ile yukle && dogrula\n", encoding="utf-8")
    page = (
        '"use client";\nimport { useEffect } from "react";\n'
        "export default function Page({ v }: { v: number[] }) {\n"
        "  useEffect(() => {}, []);\n  return Array.isArray(v) && v.length > 0 ? v[0] : null;\n}\n"
    )
    (source / "page.tsx").write_text(page, encoding="utf-8")
    target = tmp_path / "out"
    try:
        with SessionLocal() as db:
            report = asyncio.run(exporter_module.export_project(
                db, source_path=str(source), project_name=project, sicil_no="P-NER-1",
                branch_name="pytest-branch", target_path=str(target), initiated_by="P-NER-1",
            ))
            db.commit()
        assert report.files_failed_consistency_validation == 0, report.summary_text()
        assert report.files_failed_syntax_validation == 0
        assert (target / "page.tsx").read_text(encoding="utf-8") == page
        # md'nin kendisinde `&&` (harf/rakam yok) maskelenmez; NER'in diger
        # bulgulari yalnizca bu dosyada kalir.
        assert "&&" in (target / "docs" / "plan.md").read_text(encoding="utf-8")
    finally:
        _cleanup(project)


def test_reasoning_effort_is_sent_only_when_configured():
    # Ollama chat_template_kwargs'i yok sayar; Qwen3.x thinking'i reasoning_effort=none kapatir.
    from app.services.audit_reviewer import build_audit_request
    from app.services.llm_recognizer import build_detection_request

    assert "reasoning_effort" not in build_detection_request("t", "m", 1)
    assert "reasoning_effort" not in build_audit_request("t", "m", 1)
    assert build_detection_request("t", "m", 1, reasoning_effort="none")["reasoning_effort"] == "none"
    assert build_audit_request("t", "m", 1, reasoning_effort="none")["reasoning_effort"] == "none"


def test_ollama_dev_profile_disables_thinking():
    from app.core.config import VLLMSettings

    settings = VLLMSettings(_env_file=None, profile="ollama-dev")
    assert settings.disable_thinking is True
    assert settings.reasoning_effort == "none"
    assert VLLMSettings(_env_file=None, profile="ollama-dev", reasoning_effort="").reasoning_effort == ""
