"""audit_reviewer.py (Katman 3'ten bagimsiz, maskeleme SONRASI adversarial
denetim) icin karakterizasyon testleri - refactor oncesi guvenlik agi
(Asama 2 / Adim 0).

Gercek bir vLLM cagrisi yapilmaz; `call_vllm` monkeypatch ile
degistirilir. DB'ye erisim yok, bu yuzden db_session fixture'ina gerek yok.

`call_vllm` (ve dolayisiyla `audit_masked_text`) async oldugu icin (bkz.
app/services/llm_recognizer.py modul dokstring'i - vLLM coklu-istek
gerekcesi) monkeypatch'lenen sahte fonksiyonlar da `async def` olmali,
`audit_masked_text(...)` cagrilari da `asyncio.run(...)` ile sarmalanmali.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services.audit_reviewer import audit_masked_text, parse_audit_response
from app.services.llm_recognizer import LLMRecognitionError


def _settings(**overrides):
    defaults = dict(
        enabled=True,
        host="http://localhost:8000",
        model="test-model",
        api_key=None,
        timeout_seconds=5.0,
        max_file_chars=20_000,
        seed=42,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _chat_response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def test_disabled_vllm_returns_not_risky_without_calling_vllm(monkeypatch):
    async def _boom(*args, **kwargs):
        raise AssertionError("VLLM_ENABLED=false iken call_vllm hic cagrilmamali")

    monkeypatch.setattr("app.services.audit_reviewer.call_vllm", _boom)

    verdict = asyncio.run(audit_masked_text("herhangi bir maskelenmis metin", _settings(enabled=False)))

    assert verdict.risky is False
    assert verdict.findings == []


def test_risky_verdict_is_parsed_with_findings(monkeypatch):
    raw_response = _chat_response(
        '{"risk_var": true, "bulgular": ['
        '{"aciklama": "Yorum hala IP iceriyor", "ilgili_bolum": "# 10.0.0.5 sunucusu"}'
        "]}"
    )

    async def _fake_call(*a, **k):
        return raw_response

    monkeypatch.setattr("app.services.audit_reviewer.call_vllm", _fake_call)

    verdict = asyncio.run(audit_masked_text("maskelenmis metin", _settings()))

    assert verdict.risky is True
    assert len(verdict.findings) == 1
    assert "IP" in verdict.reasoning_text()


def test_not_risky_verdict_has_no_findings(monkeypatch):
    raw_response = _chat_response('{"risk_var": false, "bulgular": []}')

    async def _fake_call(*a, **k):
        return raw_response

    monkeypatch.setattr("app.services.audit_reviewer.call_vllm", _fake_call)

    verdict = asyncio.run(audit_masked_text("maskelenmis metin", _settings()))

    assert verdict.risky is False
    assert verdict.findings == []


def test_malformed_json_raises_llm_recognition_error(monkeypatch):
    raw_response = _chat_response("bu gecerli bir JSON degil")

    async def _fake_call(*a, **k):
        return raw_response

    monkeypatch.setattr("app.services.audit_reviewer.call_vllm", _fake_call)

    with pytest.raises(LLMRecognitionError):
        asyncio.run(audit_masked_text("maskelenmis metin", _settings()))


def test_missing_required_field_raises_llm_recognition_error():
    with pytest.raises(LLMRecognitionError):
        parse_audit_response(_chat_response('{"bulgular": []}'))


def test_vllm_transport_failure_propagates_not_swallowed(monkeypatch):
    """audit_masked_text, basarisiz/ulasilamayan bir vLLM cagrisini SESSIZCE
    'risk yok' olarak yorumlamaz - hatayi yutmadan yukari firlatir; fail-safe
    karantina karari cagiran tarafin (exporter._finalize_file) sorumlulugundadir."""

    async def _raise(*args, **kwargs):
        raise LLMRecognitionError("vLLM istegi basarisiz (simulated)")

    monkeypatch.setattr("app.services.audit_reviewer.call_vllm", _raise)

    with pytest.raises(LLMRecognitionError):
        asyncio.run(audit_masked_text("maskelenmis metin", _settings()))


def test_oversized_text_is_fully_chunked(monkeypatch):
    calls = []
    async def fake(host, timeout, payload, api_key=None):
        calls.append(payload["messages"][1]["content"])
        return _chat_response('{"risk_var": false, "bulgular": []}')
    monkeypatch.setattr("app.services.audit_reviewer.call_vllm", fake)
    text = "bu metin on karakterden kesinlikle daha uzun"
    verdict = asyncio.run(audit_masked_text(text, _settings(max_file_chars=10)))
    assert verdict.risky is False
    assert len(calls) > 1
    assert calls[-1].endswith("uzun")
    assert all(len(chunk) <= 10 for chunk in calls)


@pytest.mark.parametrize("disable_thinking", [False, True])
def test_disable_thinking_controls_chat_template_kwargs(monkeypatch, disable_thinking):
    # Qwen3 thinking modu max_tokens'i <think> ile tuketip yaniti kestiginden
    # VLLM_DISABLE_THINKING hem denetim hem tespit istegine yansimali.
    from app.services.llm_recognizer import find_llm_detections

    payloads = []

    async def _fake_call(host, timeout, payload, api_key=None):
        payloads.append(payload)
        if "denetim_semasi" in str(payload["response_format"]):
            return _chat_response('{"risk_var": false, "bulgular": []}')
        return _chat_response('{"bulgular": []}')

    monkeypatch.setattr("app.services.audit_reviewer.call_vllm", _fake_call)
    monkeypatch.setattr("app.services.llm_recognizer.call_vllm", _fake_call)
    settings = _settings(disable_thinking=disable_thinking)

    asyncio.run(audit_masked_text("maskelenmis metin", settings))
    asyncio.run(find_llm_detections("ham metin", [], settings))

    assert len(payloads) == 2
    for payload in payloads:
        if disable_thinking:
            assert payload["chat_template_kwargs"] == {"enable_thinking": False}
        else:
            assert "chat_template_kwargs" not in payload
