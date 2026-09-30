"""LLM girdisini daraltan ve bulgu kalitesini koruyan iyilestirmeler:

- Katman 1 bulgulari LLM'e gecici yer tutucuyla gider, ofsetler orijinale doner.
- LLM degeri kelime ortasinda eslenmez; cok kisa deger otomatik maskelenmez.
- Sistem promptunun sonuna dosya baglami eklenir.
- Denetim chunk'lari es zamanli ve deterministik taranir.
- VLLM_PROFILE yalnizca acikca verilmemis alanlari doldurur.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.core.config import VLLMSettings
from app.services import audit_reviewer, llm_recognizer
from app.services.detectors import DetectionResult
from app.services.llm_detector import LLMDetector
from app.services.llm_input_view import RedactedView, build_redacted_view
from app.services.llm_recognizer import LLMRecognitionError, load_llm_prompt


def _settings(**overrides):
    values = dict(
        enabled=True, host="http://llm-efficiency.test", model="test", api_key=None,
        timeout_seconds=1, max_file_chars=6000, chunk_overlap_chars=500, max_tokens=512,
        max_concurrent_requests=4, seed=42, redact_known_findings=True, min_auto_mask_chars=3,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _detection_response(*findings):
    items = [
        {"bulunan_deger": value, "tip": "PROJE_KOD_ADI", "guven_seviyesi": confidence, "gerekce": "ic ad"}
        for value, confidence in findings
    ]
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"bulgular": items})}}]}


def _audit_response(*quotes):
    items = [{"aciklama": "acik deger", "ilgili_bolum": quote} for quote in quotes]
    content = json.dumps({"risk_var": bool(items), "bulgular": items})
    return {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}


def _dictionary_result(text: str, value: str) -> DetectionResult:
    start = text.index(value)
    return DetectionResult(
        deger=value, tip="IP", guven_seviyesi="yuksek", kaynak_motor="dictionary",
        start=start, end=start + len(value),
    )


# --- RedactedView -----------------------------------------------------------

def test_redacted_view_maps_offsets_back_and_reuses_tokens():
    text = "host=10.0.0.5 owner=Poseidon backup=10.0.0.5 end"
    first = text.index("10.0.0.5")
    second = text.rindex("10.0.0.5")
    view = build_redacted_view(text, [(first, first + 8, "IP"), (second, second + 8, "IP")])

    assert view.text == "host=mask_ip_1 owner=Poseidon backup=mask_ip_1 end"
    view_start = view.text.index("Poseidon")
    assert view.to_original(view_start, view_start + 8) == (text.index("Poseidon"), text.index("Poseidon") + 8)
    end_start = view.text.index("end")
    assert view.to_original(end_start, end_start + 3) == (text.index("end"), text.index("end") + 3)
    # Gecici yer tutucuyla cakisan aralik orijinale eslenemez.
    assert view.to_original(view.text.index("mask_ip_1"), view.text.index("mask_ip_1") + 4) is None


def test_redacted_view_merges_overlaps_and_protects_placeholders():
    text = "abc SECRET-VALUE xyz"
    view = build_redacted_view(text, [(4, 10, "SECRET"), (7, 16, "SECRET")])
    assert view.text == "abc mask_secret_1 xyz"
    converted = view.to_view_spans([(0, 3)])
    assert (0, 3) in converted
    assert (4, 4 + len("mask_secret_1")) in converted


def test_identity_view_when_nothing_is_known():
    view = build_redacted_view("no findings", [])
    assert view == RedactedView.identity("no findings")
    assert view.to_original(3, 5) == (3, 5)


# --- LLMDetector: redaction end-to-end ---------------------------------------

def test_known_dictionary_values_are_hidden_from_llm_and_offsets_are_original(monkeypatch):
    text = "server = 10.20.30.40  # sahibi ekip Poseidon\n"
    sent = []

    async def fake(host, timeout, payload, api_key=None):
        sent.append(payload["messages"][1]["content"])
        return _detection_response(("Poseidon", "yuksek"), ("mask_ip_1", "yuksek"))

    monkeypatch.setattr(llm_recognizer, "call_vllm", fake)
    metadata = {"prior_results": (_dictionary_result(text, "10.20.30.40"),)}
    out = asyncio.run(LLMDetector(_settings()).detect(text, metadata))

    assert "10.20.30.40" not in sent[0]
    assert "mask_ip_1" in sent[0]
    assert not out.errors
    assert [(r.deger, r.start, r.end) for r in out.results] == [
        ("Poseidon", text.index("Poseidon"), text.index("Poseidon") + len("Poseidon"))
    ]
    assert text[out.results[0].start:out.results[0].end] == "Poseidon"


def test_probabilistic_prior_results_are_not_hidden(monkeypatch):
    text = "contact Ayse Yilmaz today"
    sent = []

    async def fake(host, timeout, payload, api_key=None):
        sent.append(payload["messages"][1]["content"])
        return _detection_response()

    monkeypatch.setattr(llm_recognizer, "call_vllm", fake)
    presidio = DetectionResult(
        deger="Ayse Yilmaz", tip="PERSON", guven_seviyesi="orta", kaynak_motor="katman2_presidio",
        start=8, end=19,
    )
    asyncio.run(LLMDetector(_settings()).detect(text, {"prior_results": (presidio,)}))
    assert sent == [text]


def test_redaction_can_be_disabled(monkeypatch):
    text = "server = 10.20.30.40"
    sent = []

    async def fake(host, timeout, payload, api_key=None):
        sent.append(payload["messages"][1]["content"])
        return _detection_response()

    monkeypatch.setattr(llm_recognizer, "call_vllm", fake)
    metadata = {"prior_results": (_dictionary_result(text, "10.20.30.40"),)}
    asyncio.run(LLMDetector(_settings(redact_known_findings=False)).detect(text, metadata))
    assert sent == [text]


# --- Bulgu kalitesi ----------------------------------------------------------

def _detect(monkeypatch, text, *findings, **overrides):
    async def fake(host, timeout, payload, api_key=None):
        return _detection_response(*findings)

    monkeypatch.setattr(llm_recognizer, "call_vllm", fake)
    out = asyncio.run(LLMDetector(_settings(**overrides)).detect(text))
    assert not out.errors
    return out.results


def test_value_inside_another_word_is_not_matched(monkeypatch):
    results = _detect(monkeypatch, "Alignment ok; owner Ali\n", ("Ali", "yuksek"))
    assert [(r.deger, r.start) for r in results] == [("Ali", len("Alignment ok; owner "))]


@pytest.mark.parametrize("text,value", [
    ("class PoseidonGatewayClient {}", "Poseidon"),
    ("new HTTPPoseidonClient()", "Poseidon"),
    ("atlas-billing-core", "billing"),
    ("ATLAS_API_KEY", "ATLAS"),
    ("atlas2prod", "atlas"),
])
def test_identifier_parts_on_real_boundaries_are_matched(monkeypatch, text, value):
    results = _detect(monkeypatch, text, (value, "yuksek"))
    assert [r.deger for r in results] == [value]


def test_short_values_are_downgraded_to_low_confidence(monkeypatch):
    results = _detect(monkeypatch, "id: QX\nowner: Hakan\n", ("QX", "yuksek"), ("Hakan", "yuksek"))
    assert {r.deger: r.guven_seviyesi for r in results} == {"QX": "dusuk", "Hakan": "yuksek"}


def test_min_length_rule_can_be_disabled(monkeypatch):
    results = _detect(monkeypatch, "id: QX\n", ("QX", "yuksek"), min_auto_mask_chars=0)
    assert [r.guven_seviyesi for r in results] == ["yuksek"]


# --- Dosya baglami -----------------------------------------------------------

def test_file_name_is_appended_to_system_prompt_without_directories(monkeypatch):
    systems = []

    async def fake(host, timeout, payload, api_key=None):
        systems.append(payload["messages"][0]["content"])
        return _detection_response()

    monkeypatch.setattr(llm_recognizer, "call_vllm", fake)
    metadata = {"file_path": "gizli-proje/config/application.yml"}
    asyncio.run(LLMDetector(_settings()).detect("spring: {}", metadata))

    assert systems[0].startswith(load_llm_prompt())
    assert "ad='application.yml' uzanti=yml" in systems[0]
    assert "gizli-proje" not in systems[0]


# --- Denetim eszamanliligi ---------------------------------------------------

def test_audit_chunks_run_concurrently_and_keep_chunk_order(monkeypatch):
    content = "ALFA " + "x" * 5895 + "\n" + "BETA " + "x" * 5895 + "\n" + "GAMA " + "x" * 5895
    active = peak = 0

    async def fake(host, timeout, payload, api_key=None):
        nonlocal active, peak
        chunk = payload["messages"][1]["content"]
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05 if "ALFA" in chunk else 0.01)
        active -= 1
        return _audit_response(*(v for v in ("ALFA", "BETA", "GAMA") if v in chunk))

    monkeypatch.setattr(audit_reviewer, "call_vllm", fake)
    verdict = asyncio.run(audit_reviewer.audit_masked_text(content, _settings(host="http://audit-par.test")))

    assert peak >= 3
    assert verdict.risky
    assert [f.ilgili_bolum for f in verdict.findings] == ["ALFA", "BETA", "GAMA"]


def test_audit_chunk_failure_cancels_siblings_and_raises(monkeypatch):
    content = ("y" * 5999 + "\n") * 4
    finished = []

    async def fake(host, timeout, payload, api_key=None):
        if not finished:
            finished.append("first")
            raise LLMRecognitionError("boom")
        await asyncio.sleep(0.2)
        finished.append("late")
        return _audit_response()

    monkeypatch.setattr(audit_reviewer, "call_vllm", fake)
    with pytest.raises(LLMRecognitionError):
        asyncio.run(audit_reviewer.audit_masked_text(content, _settings(host="http://audit-fail.test")))
    assert "late" not in finished


# --- Profiller ---------------------------------------------------------------

def test_profile_fills_only_unset_fields(monkeypatch):
    monkeypatch.setenv("VLLM_PROFILE", "vllm-intra")
    monkeypatch.setenv("VLLM_MAX_TOKENS", "999")
    for name in ("VLLM_MAX_CONCURRENT_REQUESTS", "VLLM_DISABLE_THINKING", "VLLM_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    settings = VLLMSettings(_env_file=None)
    assert settings.max_concurrent_requests == 4
    assert settings.disable_thinking is True
    assert settings.max_tokens == 999


def test_unknown_profile_fails_fast(monkeypatch):
    monkeypatch.setenv("VLLM_PROFILE", "turbo")
    with pytest.raises(ValueError, match="VLLM_PROFILE"):
        VLLMSettings(_env_file=None)
