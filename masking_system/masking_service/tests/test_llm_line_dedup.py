"""Tekrarli buyuk dosyalarda LLM'e her farkli satir yalnizca bir kez gider.

Bulunan degerler metnin tamamindaki her gecisinde maskelenir; kucuk ya da
tekrarsiz dosyalarda LLM'e giden metin degismez.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app.services.audit_reviewer import audit_masked_text
from app.services.llm_recognizer import find_llm_detections
from app.services.text_chunking import dedupe_for_llm, dedupe_repeated_lines


def _settings(**overrides):
    defaults = dict(
        enabled=True, host="http://localhost:8000", model="test-model", api_key=None,
        timeout_seconds=5.0, max_file_chars=2_000, chunk_overlap_chars=100, seed=42,
        redact_known_findings=False,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _chat_response(content: dict) -> dict:
    return {"choices": [{"message": {"content": json.dumps(content)}, "finish_reason": "stop"}]}


def _repetitive_log(repeats: int = 200) -> str:
    lines = []
    for i in range(repeats):
        lines.append("INFO gateway baglanti kuruldu host=atlas-billing-core port=8443\n")
        lines.append(f"DEBUG istek no={i}\n")
    lines.append("WARN sorumlu: Ayse Yilmaz\n")
    return "".join(lines)


def test_dedupe_keeps_first_occurrence_and_short_lines():
    text = "uzun satir bir\n}\nuzun satir bir\n}\nuzun satir iki"
    assert dedupe_repeated_lines(text) == "uzun satir bir\n}\n}\nuzun satir iki"


def test_dedupe_for_llm_leaves_small_or_unique_text_alone():
    assert dedupe_for_llm("tekrar eden satir\n" * 10, 2_000) is None  # tek parcaya sigiyor
    unique = "".join(f"benzersiz satir numarasi {i}\n" for i in range(500))
    assert dedupe_for_llm(unique, 2_000) is None  # tekrar yok


def test_detection_scans_each_line_once_and_masks_every_occurrence(monkeypatch):
    text = _repetitive_log()
    payloads = []

    async def _fake_call(host, timeout, payload, api_key=None):
        payloads.append(payload)
        chunk = payload["messages"][-1]["content"]
        findings = [
            {"bulunan_deger": value, "tip": tip, "guven_seviyesi": "yuksek", "gerekce": "test"}
            for value, tip in (("atlas-billing-core", "IC_SERVIS_ADI"), ("Ayse Yilmaz", "PERSON"))
            if value in chunk
        ]
        return _chat_response({"bulgular": findings})

    monkeypatch.setattr("app.services.llm_recognizer.call_vllm", _fake_call)
    first = text.index("atlas-billing-core")
    consumed = [(first, first + len("atlas-billing-core"))]  # ornek: zaten maskelenmis bir gecis

    detections = asyncio.run(find_llm_detections(text, consumed, _settings()))

    sent = sum(len(p["messages"][-1]["content"]) for p in payloads)
    assert sent < len(text) / 3  # tekrar eden 200 satir bir kez gonderildi
    service_spans = [(d.start, d.end) for d in detections if d.deger == "atlas-billing-core"]
    assert len(service_spans) == text.count("atlas-billing-core") - 1  # korunan gecis haric hepsi
    assert consumed[0] not in service_spans
    assert all(text[s:e] == "atlas-billing-core" for s, e in service_spans)
    person = [d for d in detections if d.deger == "Ayse Yilmaz"]
    assert len(person) == 1 and text[person[0].start:person[0].end] == "Ayse Yilmaz"


def test_audit_scans_each_line_once(monkeypatch):
    text = _repetitive_log().replace("Ayse Yilmaz", "mask_personel_1")
    payloads = []

    async def _fake_call(host, timeout, payload, api_key=None):
        payloads.append(payload)
        chunk = payload["messages"][-1]["content"]
        quote = "host=atlas-billing-core"
        if quote in chunk:
            return _chat_response({"risk_var": True, "bulgular": [
                {"ilgili_bolum": quote, "aciklama": "ic servis adi acik"}]})
        return _chat_response({"risk_var": False, "bulgular": []})

    monkeypatch.setattr("app.services.audit_reviewer.call_vllm", _fake_call)

    verdict = asyncio.run(audit_masked_text(text, _settings(), file_path="app.log", blob_min_chars=0))

    assert sum(len(p["messages"][-1]["content"]) for p in payloads) < len(text) / 3
    assert verdict.risky is True
    assert [f.ilgili_bolum for f in verdict.findings] == ["atlas-billing-core"]


def _timestamped_log(count: int = 400) -> str:
    lines = []
    for i in range(count):
        lines.append(f"2026-10-05 08:{(i // 60) % 60:02d}:{i % 60:02d} INFO deploy PRJ-ALFA-{i % 13} tamam port={8000 + i % 7}\n")
        lines.append(f"2026-10-05 08:{(i // 60) % 60:02d}:{i % 60:02d} DEBUG kuyruk boyu={i * 3}\n")
    return "".join(lines)


def test_member_spans_map_digit_variants_by_position():
    from app.services.text_chunking import dedupe_lines

    text = "08:00:01 deploy PRJ-ALFA-7 ok\n08:00:02 deploy PRJ-ALFA-12 ok\n"
    deduped = dedupe_lines(text, normalize_digits=True)
    assert deduped.text == "08:00:01 deploy PRJ-ALFA-7 ok\n"
    start = deduped.text.index("PRJ-ALFA-7")
    spans = deduped.member_spans(text, start, start + len("PRJ-ALFA-7"))
    assert [text[s:e] for s, e in spans] == ["PRJ-ALFA-7", "PRJ-ALFA-12"]


def test_detection_ignores_digit_only_differences_and_masks_each_variant(monkeypatch):
    text = _timestamped_log()
    payloads = []

    async def _fake_call(host, timeout, payload, api_key=None):
        payloads.append(payload)
        chunk = payload["messages"][-1]["content"]
        found = []
        for line in chunk.splitlines():
            if "PRJ-ALFA-" in line:
                code = line.split("deploy ")[1].split(" ")[0]
                found.append({"bulunan_deger": code, "tip": "PROJE_KOD_ADI", "guven_seviyesi": "yuksek", "gerekce": "t"})
                break
        return _chat_response({"bulgular": found})

    monkeypatch.setattr("app.services.llm_recognizer.call_vllm", _fake_call)

    detections = asyncio.run(find_llm_detections(text, [], _settings()))

    assert len(payloads) == 1  # 800 satir, yalnizca rakamlari farkli -> tek parca
    masked = {text[d.start:d.end] for d in detections}
    assert masked == {f"PRJ-ALFA-{n}" for n in range(13)}  # her varyant, konumundan
    assert not any(text[d.start:d.end].startswith(("port=", "80")) for d in detections)  # sayilar genellenmez


def test_audit_scans_digit_variants_once_and_reports_each_variant(monkeypatch):
    text = _timestamped_log()
    sent = []

    async def _fake_call(host, timeout, payload, api_key=None):
        chunk = payload["messages"][-1]["content"]
        sent.append(chunk)
        line = next((line for line in chunk.splitlines() if "PRJ-ALFA-" in line), None)
        if line is None:
            return _chat_response({"risk_var": False, "bulgular": []})
        code = line.split("deploy ")[1].split(" ")[0]
        return _chat_response({"risk_var": True, "bulgular": [{"ilgili_bolum": code, "aciklama": "kod adi acik"}]})

    monkeypatch.setattr("app.services.audit_reviewer.call_vllm", _fake_call)
    verdict = asyncio.run(audit_masked_text(text, _settings(), file_path="app.log", blob_min_chars=0))

    assert len(sent) == 1  # 800 satir, yalnizca rakamlari farkli -> tek parca
    # Model tek bir varyant gordu; atlanan satirlardaki karsiliklar da bulgu oldu.
    assert {f.ilgili_bolum for f in verdict.findings} == {f"PRJ-ALFA-{n}" for n in range(13)}
