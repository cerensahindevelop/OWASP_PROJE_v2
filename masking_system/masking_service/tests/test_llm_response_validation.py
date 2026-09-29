"""Malformed model output: broken response structure fails closed; a single broken
finding is repaired or dropped and counted (Asama 8). Diagnostics stay content-free."""
import asyncio
import json
import logging
from types import SimpleNamespace

import pytest

from app.services import llm_recognizer
from app.services.detectors import DetectionOrchestrator, DetectorRegistry
from app.services.llm_detector import LLMDetector


def settings():
    return SimpleNamespace(enabled=True, host="http://llm.test", model="test",
                           timeout_seconds=1, max_file_chars=6000,
                           max_concurrent_requests=4)


def finding(**changes):
    return dict(bulunan_deger="PRIVATE_VALUE", tip="TEST",
                guven_seviyesi="yuksek", gerekce="PRIVATE_REASON") | changes


def response(findings):
    return {"choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps({"bulgular": findings})}}]}


def orchestrator():
    registry = DetectorRegistry()
    registry.register(LLMDetector(settings()))
    return DetectionOrchestrator(registry)


def broken_structure():
    return {"choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps({"bulgular": "PRIVATE_VALUE"})}}]}


@pytest.mark.parametrize("bad", [[], ["PRIVATE_CONFIDENCE"], {"PRIVATE_KEY": "PRIVATE_VALUE"},
                                 None, True, 42, "PRIVATE_ENUM"])
def test_invalid_confidence_is_repaired_not_crash_and_not_logged(monkeypatch, caplog, bad):
    # Asama 8: degeri metinde dogrulanan bulgu tum parcayi dusurmez;
    # orta/KURUMSAL_TANIMLAYICI ile kabul edilir, deger loga dusmez.
    async def fake(*args):
        return response([finding(), finding(guven_seviyesi=bad)])

    monkeypatch.setattr(llm_recognizer, "call_vllm", fake)
    with caplog.at_level(logging.INFO, logger="uvicorn.error.llm"):
        output = asyncio.run(orchestrator().scan("PRIVATE_VALUE", {"file_path": "test.txt"}))
    assert output.errors == [] and output.crashes == []
    # Ayni span/tip icin yuksek guvenli gecerli bulgu kazanir (chunk birlestirme).
    assert [(r.tip, r.guven_seviyesi) for r in output.results] == [("KURUMSAL_TANIMLAYICI", "yuksek")]
    assert output.notices == ["llm_bulgu_semasi_bozuk onarilan=1 atilan=0"]
    assert "PRIVATE" not in "".join(output.notices) + caplog.text
    assert "completed=1" in caplog.text


def test_broken_structure_is_explicit_failure_not_crash_or_success(monkeypatch, caplog):
    async def fake(*args):
        return broken_structure()

    monkeypatch.setattr(llm_recognizer, "call_vllm", fake)
    with caplog.at_level(logging.INFO, logger="uvicorn.error.llm"):
        output = asyncio.run(orchestrator().scan("PRIVATE_VALUE", {"file_path": "test.txt"}))
    assert output.results == [] and output.crashes == []
    assert len(output.errors) == 1
    assert "VALIDATION_FAILED" in output.errors[0]
    assert "PRIVATE" not in output.errors[0] + caplog.text
    assert "error_stage=parse" in caplog.text
    assert "completed=0" in caplog.text


@pytest.mark.parametrize("field", ["bulunan_deger", "tip", "gerekce", "guven_seviyesi"])
@pytest.mark.parametrize("mode", ["missing", "wrong_type"])
def test_required_finding_fields_are_checked(field, mode):
    item = finding()
    if mode == "missing":
        del item[field]
    else:
        item[field] = {"PRIVATE_KEY": "PRIVATE_VALUE"}
    stats = llm_recognizer.FindingRepairStats()
    detections = llm_recognizer.parse_and_verify_detections(
        response([item]), "PRIVATE_VALUE", [], repair_stats=stats)
    if field == "bulunan_deger":
        # Dogrulanacak deger yok: yalnizca bu bulgu atilir ve sayilir.
        assert detections == [] and (stats.repaired, stats.dropped) == (0, 1)
    else:
        assert [(d.deger, d.tip, d.guven_seviyesi) for d in detections] == [
            ("PRIVATE_VALUE", "KURUMSAL_TANIMLAYICI", "orta")]
        assert (stats.repaired, stats.dropped) == (1, 0)


@pytest.mark.parametrize("item", [None, 1, "PRIVATE_VALUE", []])
def test_invalid_finding_is_counted_not_silently_dropped(item):
    stats = llm_recognizer.FindingRepairStats()
    detections = llm_recognizer.parse_and_verify_detections(
        response([item, finding()]), "PRIVATE_VALUE", [], repair_stats=stats)
    assert [d.deger for d in detections] == ["PRIVATE_VALUE"]
    assert (stats.repaired, stats.dropped) == (0, 1)


def test_bad_later_chunk_discards_previous_results_and_does_not_retry(monkeypatch):
    calls = []

    async def fake(*args):
        calls.append(1)
        return response([finding()]) if len(calls) == 1 else broken_structure()

    monkeypatch.setattr(llm_recognizer, "call_vllm", fake)
    output = asyncio.run(orchestrator().scan("PRIVATE_VALUE" + "x" * 14000))
    assert len(calls) == 2
    assert output.results == [] and not output.crashes
    assert len(output.errors) == 1 and "bulgular" in output.errors[0]


def test_252_file_batch_isolates_128_invalid_responses(monkeypatch):
    async def fake(host, timeout, payload, api_key):
        await asyncio.sleep(0)
        text = payload["messages"][1]["content"]
        return broken_structure() if text.startswith("invalid") else response([finding()])

    monkeypatch.setattr(llm_recognizer, "call_vllm", fake)

    async def run():
        scanner = orchestrator()
        return await asyncio.gather(*(scanner.scan(
            ("invalid" if i < 128 else "valid") + " PRIVATE_VALUE",
            {"file_path": f"file_{i}.txt"},
        ) for i in range(252)))

    outputs = asyncio.run(run())
    assert sum(bool(out.errors) for out in outputs) == 128
    assert sum(bool(out.results) for out in outputs) == 124
    assert not any(out.crashes for out in outputs)


def test_unexpected_crash_logs_source_location_without_content_or_exception_text(caplog):
    class BrokenDetector:
        name = "llm"

        async def detect(self, content, metadata=None):
            raise TypeError("PRIVATE_EXCEPTION " + content)

    registry = DetectorRegistry()
    registry.register(BrokenDetector())
    with caplog.at_level(logging.ERROR, logger="uvicorn.error.detectors"):
        output = asyncio.run(DetectionOrchestrator(registry).scan(
            "PRIVATE_CONTENT", {"file_path": "test.txt"}))
    assert len(output.crashes) == 1 and "TypeError" in output.crashes[0]
    assert "detector_crash" in caplog.text
    assert "test_llm_response_validation.py:" in caplog.text
    assert "detect" in caplog.text and "test.txt" in caplog.text
    assert "PRIVATE" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
