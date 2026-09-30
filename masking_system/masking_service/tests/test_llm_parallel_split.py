"""Parallel chunk scanning and split-and-rescan of truncated (finish_reason=length) chunks."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app.services import llm_recognizer
from app.services.llm_detector import LLMDetector


def settings(**kw):
    values = dict(enabled=True, host='http://llm-split.test', model='test', timeout_seconds=1,
                  max_file_chars=6000, chunk_overlap_chars=500, max_tokens=512,
                  max_concurrent_requests=4, seed=42, api_key=None)
    values.update(kw)
    return SimpleNamespace(**values)


def response(findings=None, finish='stop'):
    return {'choices': [{'finish_reason': finish,
                         'message': {'content': json.dumps({'bulgular': findings or []})}}]}


def finding(value):
    return {'bulunan_deger': value, 'tip': 'KOD', 'guven_seviyesi': 'yuksek', 'gerekce': 'kod'}


def lines(n, width=60):
    return ''.join('x' * (width - 1) + '\n' for _ in range(n))


def test_chunks_are_requested_concurrently_and_output_is_deterministic(monkeypatch):
    # 4 chunk; chunk'lar ters sirada tamamlanir, sonuc yine offset sirasinda olmali.
    # Degerlerden sonraki bosluk: LLM degeri kelime ortasinda eslenmez (ALFAxxx).
    content = 'ALFA ' + 'x' * 5899 + 'BETA ' + 'x' * 5899 + 'GAMA ' + 'x' * 5899 + 'DELTA'
    active = peak = 0

    async def fake(host, timeout, payload, api_key=None):
        nonlocal active, peak
        chunk = payload['messages'][1]['content']
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05 if 'ALFA' in chunk else 0.01)
        active -= 1
        return response([finding(v) for v in ('ALFA', 'BETA', 'GAMA', 'DELTA') if v in chunk])

    monkeypatch.setattr(llm_recognizer, 'call_vllm', fake)
    out = asyncio.run(LLMDetector(settings()).detect(content))
    assert not out.errors
    assert peak == 4
    assert [(r.deger, r.start) for r in out.results] == [
        ('ALFA', 0), ('BETA', 5904), ('GAMA', 11808), ('DELTA', 17712)]


def test_admission_gate_still_limits_parallel_chunks(monkeypatch):
    active = peak = 0

    async def fake(*args):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return response()

    monkeypatch.setattr(llm_recognizer, 'call_vllm', fake)
    out = asyncio.run(LLMDetector(settings(host='http://llm-gate2.test', max_concurrent_requests=2))
                      .detect('x' * 30000))
    assert not out.errors
    assert peak == 2


def test_truncated_chunk_is_split_and_rescanned_with_correct_offsets(monkeypatch):
    body = lines(50)  # 3000 karakter, tek chunk
    content = 'HEAD_KOD ' + body[1:] + 'TAIL_KOD'
    sent = []

    async def fake(host, timeout, payload, api_key=None):
        chunk = payload['messages'][1]['content']
        sent.append(chunk)
        if chunk == content:
            return response(finish='length')
        return response([finding(v) for v in ('HEAD_KOD', 'TAIL_KOD') if v in chunk])

    monkeypatch.setattr(llm_recognizer, 'call_vllm', fake)
    out = asyncio.run(LLMDetector(settings()).detect(content))
    assert not out.errors
    assert len(sent) == 3
    left, right = sent[1], sent[2]
    # Satir sonundan bolunur, iki parca ortusur ve birlikte tum metni kapsar.
    assert left.endswith('\n') and content.startswith(left)
    right_start = content.index(right)
    assert content[right_start:] == right
    assert right_start < len(left)
    assert [(r.deger, r.start, r.end) for r in out.results] == [
        ('HEAD_KOD', 0, 8), ('TAIL_KOD', len(content) - 8, len(content))]


def test_split_only_rescans_the_truncated_chunk(monkeypatch):
    content = lines(100) + lines(100)  # 12000 karakter -> 3 chunk
    sent = []

    async def fake(host, timeout, payload, api_key=None):
        chunk = payload['messages'][1]['content']
        sent.append(chunk)
        first = len(sent) == 1
        return response(finish='length' if first else 'stop')

    monkeypatch.setattr(llm_recognizer, 'call_vllm', fake)
    out = asyncio.run(LLMDetector(settings(max_concurrent_requests=1, host='http://llm-one.test'))
                      .detect(content))
    assert not out.errors
    chunks = llm_recognizer.chunk_text(content, 6000, 500)
    # 3 orijinal istek + bolunen ilk chunk'in 2 parcasi; basarili chunk'lar tekrar gitmez.
    assert len(sent) == len(chunks) + 2
    assert sent.count(chunks[1][1]) == 1 and sent.count(chunks[2][1]) == 1


def test_still_truncated_at_depth_limit_raises(monkeypatch):
    sent = []

    async def fake(host, timeout, payload, api_key=None):
        sent.append(payload['messages'][1]['content'])
        return response(finish='length')

    monkeypatch.setattr(llm_recognizer, 'call_vllm', fake)
    with pytest.raises(llm_recognizer.LLMTruncatedError, match='bolme_derinligi=3'):
        asyncio.run(llm_recognizer.find_llm_detections(lines(200), [], settings(max_file_chars=12000)))
    # 12000 -> 6000 (derinlik 1) -> 3000 (derinlik 2) -> 1500 (derinlik 3) hala kesik -> hata.
    assert [len(s) for s in sent] == [12000, 6000, 3000, 1500]
    assert isinstance(llm_recognizer.LLMTruncatedError('x'), llm_recognizer.LLMRecognitionError)


def test_small_truncated_chunk_is_not_split(monkeypatch):
    calls = []

    async def fake(*args):
        calls.append(1)
        return response(finish='length')

    monkeypatch.setattr(llm_recognizer, 'call_vllm', fake)
    # 1500 karakter < 2 * _MIN_SPLIT_CHARS (800): bolunemez.
    out = asyncio.run(LLMDetector(settings()).detect('ATLAS ' * 250, {'file_path': 'a.txt'}))
    assert out.results == [] and 'VALIDATION_FAILED' in out.errors[0]
    assert len(calls) == 1


def test_one_failing_chunk_fails_whole_file_without_partial_results(monkeypatch):
    content = 'ATLAS' + 'x' * 20000

    async def fake(host, timeout, payload, api_key=None):
        chunk = payload['messages'][1]['content']
        if 'ATLAS' not in chunk and chunk.endswith('x' * 100) and len(chunk) < 6000:
            raise llm_recognizer.LLMRecognitionError('son chunk basarisiz')
        await asyncio.sleep(0.01)
        return response([finding('ATLAS')] if 'ATLAS' in chunk else [])

    monkeypatch.setattr(llm_recognizer, 'call_vllm', fake)
    out = asyncio.run(LLMDetector(settings()).detect(content, {'file_path': 'big.txt'}))
    assert out.results == []
    assert len(out.errors) == 1 and 'son chunk basarisiz' in out.errors[0]


def test_non_length_incomplete_finish_is_not_split(monkeypatch):
    calls = []

    async def fake(*args):
        calls.append(1)
        return response(finish='content_filter')

    monkeypatch.setattr(llm_recognizer, 'call_vllm', fake)
    with pytest.raises(llm_recognizer.LLMRecognitionError) as info:
        asyncio.run(llm_recognizer.find_llm_detections(lines(100), [], settings()))
    assert not isinstance(info.value, llm_recognizer.LLMTruncatedError)
    assert len(calls) == 1
