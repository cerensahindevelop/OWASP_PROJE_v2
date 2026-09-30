"""Gomulu ikili veri (base64/hex resim, ikon) LLM girdisinden cikarilir;
okunabilir metne cozulen base64, identifier/yol listeleri ve bloga bitisik
satirlar LLM'e gitmeye devam eder."""
from __future__ import annotations

import asyncio
import base64
import json
import random
import textwrap
from types import SimpleNamespace

import pytest

from app.services import audit_reviewer, llm_recognizer
from app.services.detectors import DetectionOrchestrator, DetectorRegistry
from app.services.encoded_blobs import blank_spans, find_encoded_blobs, is_encoded_binary
from app.services.llm_detector import LLMDetector
from app.services.presidio_detector import PresidioDetector

_RNG = random.Random(1234)


def _random_bytes(count: int) -> bytes:
    return bytes(_RNG.getrandbits(8) for _ in range(count))


def _wrapped_base64(data: bytes, indent: str = "        ") -> str:
    return f"\n{indent}".join(textwrap.wrap(base64.b64encode(data).decode(), 80))


def _resx(*blobs: bytes, label: str = "Musteri: Hakan Yilmaz") -> str:
    entries = "".join(
        f'  <data name="image{index}" type="System.Drawing.Bitmap" '
        f'mimetype="application/x-microsoft.net.object.bytearray.base64">\n'
        f"    <value>\n        {_wrapped_base64(blob)}\n</value>\n  </data>\n"
        for index, blob in enumerate(blobs)
    )
    return f'<root>\n{entries}  <data name="label1.Text"><value>{label}</value></data>\n</root>\n'


PNG = b"\x89PNG\r\n\x1a\n" + _random_bytes(3000)
ICON = (b"\x00" * 600 + _random_bytes(200)) * 3


def _settings(**overrides):
    values = dict(
        enabled=True, host="http://llm-blobs.test", model="test", api_key=None, timeout_seconds=1,
        max_file_chars=6000, chunk_overlap_chars=500, max_tokens=512, max_concurrent_requests=4,
        seed=42,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


# --- Tespit kurallari --------------------------------------------------------

def test_resx_images_and_icons_are_detected():
    text = _resx(PNG, ICON)
    blobs = find_encoded_blobs(text, 512)
    assert len(blobs) == 2
    remaining = text
    for start, end in reversed(blobs):
        remaining = remaining[:start] + remaining[end:]
    assert "Musteri: Hakan Yilmaz" in remaining
    assert len(remaining) < 700


@pytest.mark.parametrize("text", [
    "\n".join(f"src/main/java/com/acmecorp/project{i}/service/CustomerServiceImpl" for i in range(30)),
    "\n".join(["SomeVeryLongIdentifierNameForTestAbc", "AnotherVeryLongIdentifierName2Value"] * 30),
    _wrapped_base64(b"password=Sup3r; user=ali; host=db.acme.local\n" * 40),
    (b"select * from musteriler where sicil = 42;" * 40).hex(),
], ids=["paths", "identifiers", "base64-encoded-text", "hex-encoded-text"])
def test_text_like_content_is_never_hidden(text):
    assert find_encoded_blobs(text, 512) == []


def test_hex_dump_of_binary_is_detected():
    assert find_encoded_blobs(_random_bytes(600).hex(), 512) == [(0, 1200)]


def test_line_after_blob_is_not_swallowed():
    text = "<value>\n" + _wrapped_base64(_random_bytes(3000), indent="") + "\nAcmeCorpInternalProjectName\n"
    [(start, end)] = find_encoded_blobs(text, 512)
    assert text[end:] == "\nAcmeCorpInternalProjectName\n"
    assert is_encoded_binary(text[start:end])


def test_short_blobs_and_disabled_setting_are_ignored():
    blob = base64.b64encode(_random_bytes(300)).decode()  # 400 karakter
    assert find_encoded_blobs(blob, 512) == []
    assert find_encoded_blobs(_resx(PNG), 0) == []


# --- LLM entegrasyonu --------------------------------------------------------

def test_detection_does_not_send_blobs_and_keeps_original_offsets(monkeypatch):
    text = _resx(PNG, ICON)
    sent = []

    async def fake(host, timeout, payload, api_key=None):
        sent.append(payload["messages"][1]["content"])
        items = [{"bulunan_deger": "Hakan Yilmaz", "tip": "PERSON", "guven_seviyesi": "yuksek", "gerekce": "ad"}]
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"bulgular": items})}}]}

    monkeypatch.setattr(llm_recognizer, "call_vllm", fake)
    registry = DetectorRegistry()
    registry.register(LLMDetector(_settings()))
    orchestrator = DetectionOrchestrator(registry, encoded_blob_min_chars=512)
    out = asyncio.run(orchestrator.scan(text, {"file_path": "Forms/FaturalarForm.resx"}))

    assert not out.errors
    assert len(sent) == 1  # eskiden ~10 KB base64 -> 2+ parca
    assert "mask_kodlanmis_ikili_veri_1" in sent[0] and "mask_kodlanmis_ikili_veri_2" in sent[0]
    assert base64.b64encode(PNG).decode()[:80] not in sent[0]
    [result] = out.results
    assert text[result.start:result.end] == "Hakan Yilmaz"


def test_audit_does_not_send_blobs_but_still_verifies_leaks(monkeypatch):
    masked = _resx(PNG, label="Musteri: Hakan Yilmaz")
    sent = []

    async def fake(host, timeout, payload, api_key=None):
        sent.append(payload["messages"][1]["content"])
        content = json.dumps({"risk_var": True, "bulgular": [
            {"aciklama": "maskelenmemis kisi adi", "ilgili_bolum": "Hakan Yilmaz"},
        ]})
        return {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}

    monkeypatch.setattr(audit_reviewer, "call_vllm", fake)
    verdict = asyncio.run(audit_reviewer.audit_masked_text(
        masked, _settings(host="http://audit-blobs.test"), blob_min_chars=512,
    ))

    assert len(sent) == 1 and len(sent[0]) < 600
    assert verdict.risky
    assert [finding.ilgili_bolum for finding in verdict.findings] == ["Hakan Yilmaz"]


def test_blank_spans_keeps_offsets_and_line_breaks():
    assert blank_spans("ab\ncdXY\nZ", [(1, 5)]) == "a \n  XY\nZ"


def test_presidio_does_not_report_findings_inside_blobs():
    text = _resx(PNG, label="Contact John Smith at john.smith@example.org")
    detector = PresidioDetector([])
    blobs = find_encoded_blobs(text, 512)
    out = asyncio.run(detector.detect(text, {"file_path": "Form.resx", "encoded_blob_spans": blobs}))
    [(blob_start, blob_end)] = blobs
    assert out.results
    assert all(r.end <= blob_start or r.start >= blob_end for r in out.results)
    assert any(text[r.start:r.end] == "john.smith@example.org" for r in out.results)
