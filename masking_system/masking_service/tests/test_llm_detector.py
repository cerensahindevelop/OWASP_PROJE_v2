"""LLMDetector / find_llm_detections (Katman 3 tespit) icin karakterizasyon
testleri - refactor oncesi guvenlik agi (Asama 2 / Adim 0).

Gercek bir vLLM cagrisi yapilmaz; `call_vllm`, kullanildigi modul
(app.services.llm_recognizer) uzerinden monkeypatch ile degistirilir. DB'ye
erisim yok, bu yuzden db_session fixture'ina gerek yok.

`call_vllm` (ve dolayisiyla `LLMDetector.detect`) async oldugu icin
(bkz. app/services/llm_recognizer.py modul dokstring'i - vLLM coklu-istek
gerekcesi) monkeypatch'lenen sahte fonksiyonlar da `async def` olmali,
`detector.detect(...)` cagrilari da `asyncio.run(...)` ile sarmalanmali.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app.services.llm_detector import LLMDetector
from app.services.llm_recognizer import LLMRecognitionError
from app.services.log_refs import log_file_label


def _settings(**overrides):
    defaults = dict(
        enabled=True,
        host="http://localhost:8000",
        model="test-model",
        api_key=None,
        timeout_seconds=5.0,
        max_file_chars=20_000,
        seed=42,
        max_concurrent_requests=4,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _chat_response(bulgular):
    return {"choices": [{"message": {"content": json.dumps({"bulgular": bulgular})}}]}


def test_disabled_returns_empty_output_without_calling_vllm(monkeypatch):
    async def _boom(*a, **k):
        raise AssertionError("VLLM_ENABLED=false iken call_vllm hic cagrilmamali")

    monkeypatch.setattr("app.services.llm_recognizer.call_vllm", _boom)

    detector = LLMDetector(_settings(enabled=False))
    output = asyncio.run(detector.detect("herhangi bir metin", metadata={"file_path": "a.py"}))

    assert output.results == []
    assert output.errors == []


def test_metadata_enable_llm_false_short_circuits(monkeypatch):
    async def _boom(*a, **k):
        raise AssertionError("enable_llm=False iken call_vllm hic cagrilmamali")

    monkeypatch.setattr("app.services.llm_recognizer.call_vllm", _boom)

    detector = LLMDetector(_settings(enabled=True))
    output = asyncio.run(detector.detect("metin", metadata={"enable_llm": False}))

    assert output.results == []


def test_real_value_found_in_text_is_reported(monkeypatch):
    text = "Musteri adi: Ahmet Yilmaz, telefon: 555"
    response = _chat_response(
        [{"bulunan_deger": "Ahmet Yilmaz", "tip": "PERSON", "guven_seviyesi": "yuksek", "gerekce": "kisi adi"}]
    )

    async def _fake_call(*a, **k):
        return response

    monkeypatch.setattr("app.services.llm_recognizer.call_vllm", _fake_call)

    detector = LLMDetector(_settings())
    output = asyncio.run(detector.detect(text))

    assert len(output.results) == 1
    result = output.results[0]
    assert result.deger == "Ahmet Yilmaz"
    assert result.tip == "PERSON"
    assert result.start == text.index("Ahmet Yilmaz")


def test_hallucinated_value_not_present_in_text_is_silently_dropped(monkeypatch):
    """llm_recognizer.py modul dokstring'indeki guvenlik ilkesi: LLM'in
    bildirdigi HER deger metinde birebir bulunmali - bulunamayan deger
    sessizce atilir, asla bir bulgu olarak yansitilmaz."""
    text = "Bu metinde hicbir hassas veri yok."
    response = _chat_response(
        [{"bulunan_deger": "Ahmet Yilmaz", "tip": "PERSON", "guven_seviyesi": "yuksek", "gerekce": "uydurma"}]
    )

    async def _fake_call(*a, **k):
        return response

    monkeypatch.setattr("app.services.llm_recognizer.call_vllm", _fake_call)

    detector = LLMDetector(_settings())
    output = asyncio.run(detector.detect(text))

    assert output.results == []


def test_value_overlapping_a_consumed_span_is_skipped(monkeypatch):
    text = "IP: 10.0.0.5 (dahili sunucu)"
    response = _chat_response(
        [{"bulunan_deger": "10.0.0.5", "tip": "IP_ADDRESS_LLM", "guven_seviyesi": "orta", "gerekce": "ip gibi gorunuyor"}]
    )

    async def _fake_call(*a, **k):
        return response

    monkeypatch.setattr("app.services.llm_recognizer.call_vllm", _fake_call)

    start = text.index("10.0.0.5")
    already_consumed = [(start, start + len("10.0.0.5"))]
    detector = LLMDetector(_settings())
    output = asyncio.run(detector.detect(text, metadata={"consumed_spans": already_consumed}))

    assert output.results == []


def test_extra_instructions_are_included_in_vllm_system_prompt(monkeypatch):
    """pattern_type='llm' FilterRule satirlarinin description'i artik
    gercekten vLLM'e giden system prompt'a ekleniyor (bkz.
    mapping_service.build_orchestrator + llm_recognizer._augment_prompt) -
    eskiden bu kurallar DB'de durur ama LLMDetector'a hic iletilmezdi."""
    captured_payloads = []

    async def _capture(host, timeout_seconds, payload, api_key=None):
        captured_payloads.append(payload)
        return _chat_response([])

    monkeypatch.setattr("app.services.llm_recognizer.call_vllm", _capture)

    instruction = "Kurum ici servis kod adlarina (orn. PROJE-X, ATLAS-CORE) dikkat et."
    detector = LLMDetector(_settings(), extra_instructions=[instruction])
    asyncio.run(detector.detect("herhangi bir metin"))

    assert len(captured_payloads) == 1
    system_message = captured_payloads[0]["messages"][0]["content"]
    assert instruction in system_message


def test_no_extra_instructions_leaves_base_prompt_unchanged(monkeypatch):
    captured_payloads = []

    async def _capture(host, timeout_seconds, payload, api_key=None):
        captured_payloads.append(payload)
        return _chat_response([])

    monkeypatch.setattr("app.services.llm_recognizer.call_vllm", _capture)

    from app.services.llm_recognizer import load_llm_prompt

    detector = LLMDetector(_settings())  # extra_instructions=None (varsayilan)
    asyncio.run(detector.detect("herhangi bir metin"))

    assert captured_payloads[0]["messages"][0]["content"] == load_llm_prompt()


def test_vllm_failure_is_reported_as_error_not_raised(monkeypatch):
    async def _raise(*a, **k):
        raise LLMRecognitionError("vLLM istegi basarisiz (simulated)")

    monkeypatch.setattr("app.services.llm_recognizer.call_vllm", _raise)

    detector = LLMDetector(_settings())

    async def detect():
        # Faz 2a (kural 7): hata metni rapora gider; kaynak yol degil etiket yazilir.
        with log_file_label("mask/a.py#0123456789ab"):
            return await detector.detect("herhangi bir metin", metadata={"file_path": "a.py"})

    output = asyncio.run(detect())

    assert output.results == []
    assert len(output.errors) == 1
    assert "(dosya=mask/a.py#0123456789ab)" in output.errors[0]
    assert "dosya=a.py" not in output.errors[0]
