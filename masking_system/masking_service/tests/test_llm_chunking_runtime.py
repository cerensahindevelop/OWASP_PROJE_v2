"""Chunk coverage, shared admission, bounded output and fail-closed regressions."""
import asyncio
import json
import logging
from types import SimpleNamespace

import httpx
import pytest

from app.services import audit_reviewer, llm_recognizer
from app.services.llm_detector import LLMDetector
from app.services.llm_runtime import LLMScanMetrics, llm_file_context


def settings(**kw):
    values = dict(enabled=True, host='http://llm.test', model='test', timeout_seconds=1,
                  max_file_chars=6000, chunk_overlap_chars=500, max_tokens=512,
                  max_concurrent_requests=1, seed=42, api_key=None)
    values.update(kw)
    return SimpleNamespace(**values)


def response(findings=None, *, audit=False, finish='stop', risky=False):
    data = {'bulgular': findings or []}
    if audit:
        data['risk_var'] = risky
    return {'choices': [{'finish_reason': finish, 'message': {'content': json.dumps(data)}}],
            'usage': {'prompt_tokens': 101, 'completion_tokens': 12}}


@pytest.mark.parametrize('text', ['', 'x'*6000, 'x'*25001, ('satir 👋 İstanbul\n'*3000)])
def test_chunks_cover_every_character_with_overlap(text):
    chunks = llm_recognizer.chunk_text(text, 6000, 500)
    end = 0
    for i, (start, chunk) in enumerate(chunks):
        assert chunk == text[start:start+len(chunk)]
        assert len(chunk) <= 6000
        assert start <= end
        if i:
            assert end - start == 500
        end = start + len(chunk)
    assert end == len(text)


def test_tail_and_boundary_findings_are_detected_once_with_global_offsets(monkeypatch):
    content = 'x'*5700 + 'ATLAS' + 'x'*15000 + 'SON_KOD'
    calls = []
    async def fake(host, timeout, payload, api_key=None):
        chunk = payload['messages'][1]['content']
        calls.append(chunk)
        findings = [{'bulunan_deger': value, 'tip': 'KOD',
                     'guven_seviyesi': 'orta' if len(calls)==1 else 'yuksek', 'gerekce': 'kod'}
                    for value in ('ATLAS', 'SON_KOD') if value in chunk]
        assert payload['max_tokens'] == 512
        return response(findings)
    monkeypatch.setattr(llm_recognizer, 'call_vllm', fake)
    out = asyncio.run(LLMDetector(settings()).detect(content))
    assert not out.errors
    assert [(r.deger, r.start) for r in out.results] == [('ATLAS', 5700), ('SON_KOD', 20705)]
    assert out.results[0].guven_seviyesi == 'yuksek'
    assert len(calls) == 4


def test_failure_on_later_chunk_discards_all_detections_without_retry(monkeypatch):
    calls = []
    async def fake(*args):
        calls.append(1)
        if len(calls) == 2:
            raise llm_recognizer.LLMRecognitionError('timeout')
        return response([{'bulunan_deger': 'ATLAS', 'tip': 'KOD', 'guven_seviyesi': 'yuksek', 'gerekce': 'kod'}])
    monkeypatch.setattr(llm_recognizer, 'call_vllm', fake)
    out = asyncio.run(LLMDetector(settings()).detect('ATLAS'+'x'*13000, {'file_path': 'big.txt'}))
    assert out.results == []
    assert len(out.errors) == 1 and 'VALIDATION_FAILED' in out.errors[0]
    assert len(calls) == 2


@pytest.mark.parametrize('phase', ['detection', 'audit'])
@pytest.mark.parametrize('finish', ['length', 'content_filter', 'tool_calls'])
def test_valid_json_with_incomplete_finish_is_rejected(phase, finish):
    raw = response(audit=phase=='audit', finish=finish)
    with pytest.raises(llm_recognizer.LLMRecognitionError):
        if phase == 'audit':
            audit_reviewer.parse_audit_response(raw)
        else:
            llm_recognizer.parse_and_verify_detections(raw, '', [])


def test_audit_checks_every_chunk_and_deduplicates_overlap(monkeypatch):
    calls = []
    async def fake(host, timeout, payload, api_key=None):
        chunk = payload['messages'][1]['content']
        calls.append(chunk)
        assert payload['max_tokens'] == 512
        return response([{'aciklama': str(len(calls)), 'ilgili_bolum': 'ATLAS'}] if 'ATLAS' in chunk else [],
                        audit=True, risky='ATLAS' in chunk)
    monkeypatch.setattr(audit_reviewer, 'call_vllm', fake)
    verdict = asyncio.run(audit_reviewer.audit_masked_text('x'*5700+'ATLAS'+'x'*16000, settings()))
    assert verdict.risky and len(verdict.findings) == 1
    assert len(calls) == 4  # Continue even after a risky verdict; no unscanned tail.


def test_audit_late_failure_cannot_return_success(monkeypatch):
    calls = []
    async def fake(*args):
        calls.append(1)
        if len(calls) == 2:
            raise llm_recognizer.LLMRecognitionError('failed')
        return response(audit=True)
    monkeypatch.setattr(audit_reviewer, 'call_vllm', fake)
    with pytest.raises(llm_recognizer.LLMRecognitionError):
        asyncio.run(audit_reviewer.audit_masked_text('x'*13000, settings()))
    assert len(calls) == 2


def test_detection_and_audit_jobs_share_request_limit_and_log_usage(monkeypatch, caplog):
    active = peak = 0
    async def fake(host, timeout, payload, api_key=None):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(.015)
        active -= 1
        return response(audit=payload['response_format']['json_schema']['name']=='denetim_semasi')
    monkeypatch.setattr(llm_recognizer, 'call_vllm', fake)
    monkeypatch.setattr(audit_reviewer, 'call_vllm', fake)
    async def run():
        with llm_file_context('audit.txt'):
            return await asyncio.gather(
                LLMDetector(settings()).detect('PRIVATE_CONTENT', {'file_path': 'detect.txt'}),
                audit_reviewer.audit_masked_text('PRIVATE_CONTENT', settings()),
                audit_reviewer.audit_masked_text('PRIVATE_CONTENT', settings()),
            )
    with caplog.at_level(logging.INFO, logger='uvicorn.error.llm'):
        asyncio.run(run())
    assert peak == 1
    assert 'file=\'audit.txt\'' in caplog.text and 'file=\'detect.txt\'' in caplog.text
    assert 'requests=1' in caplog.text and 'prompt_tokens=101' in caplog.text
    assert 'PRIVATE_CONTENT' not in caplog.text


@pytest.mark.parametrize("limit,service_seconds", [(4, .06), (2, .11)])
def test_single_runner_queue_can_timeout_above_one(monkeypatch, limit, service_seconds):
    # Simulates Ollama Parallel:1 with a real HTTPX request coroutine and
    # a deterministic service time; scaled-down times avoid a 200s test.
    async def run(limit):
        runner = asyncio.Semaphore(1)
        real_client = httpx.AsyncClient
        async def handler(request):
            async with runner:
                await asyncio.sleep(service_seconds)
                return httpx.Response(200, json=response(), request=request)
        monkeypatch.setattr(llm_recognizer.httpx, 'AsyncClient',
                            lambda: real_client(transport=httpx.MockTransport(handler)))
        s = settings(max_concurrent_requests=limit, timeout_seconds=.15)
        results = await asyncio.gather(*(LLMDetector(s).detect('test') for _ in range(4)))
        monkeypatch.setattr(llm_recognizer.httpx, 'AsyncClient', real_client)
        return sum(bool(out.errors) for out in results)
    assert asyncio.run(run(limit)) >= 1
    assert asyncio.run(run(1)) == 0  # Local queue does not consume HTTP deadline.


def test_deadline_cancels_http_request_and_releases_gate(monkeypatch, caplog):
    real_client = httpx.AsyncClient
    async def handler(request):
        await asyncio.sleep(.1)
        return httpx.Response(200, json=response(), request=request)
    monkeypatch.setattr(llm_recognizer.httpx, 'AsyncClient',
                        lambda: real_client(transport=httpx.MockTransport(handler)))
    async def run():
        s = settings(timeout_seconds=.01)
        return await asyncio.gather(*(LLMDetector(s).detect('private') for _ in range(2)))
    with caplog.at_level(logging.INFO, logger='uvicorn.error.llm'):
        out = asyncio.run(run())
    assert all(r.errors for r in out)
    assert 'status=timeout' in caplog.text
    assert 'completed=0 requests=1' in caplog.text


def test_cancelled_scan_releases_slot():
    async def run():
        entered = asyncio.Event()
        async def slow(*args):
            entered.set()
            await asyncio.Event().wait()
        async def fast(*args):
            return response()
        async def one(caller):
            with LLMScanMetrics('detection', 1, 'cancel.txt') as metrics:
                return await metrics.request(settings(), {}, caller, lambda x: x, 1)
        task = asyncio.create_task(one(slow))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.wait_for(one(fast), .1)
    asyncio.run(run())


def test_untrusted_usage_and_finish_text_are_not_logged(monkeypatch, caplog):
    async def fake(*args):
        raw = response(finish='PRIVATE_FINISH')
        raw['usage'] = {'prompt_tokens': 'PRIVATE_INPUT', 'completion_tokens': 'PRIVATE_OUTPUT'}
        return raw
    monkeypatch.setattr(llm_recognizer, 'call_vllm', fake)
    with caplog.at_level(logging.INFO, logger='uvicorn.error.llm'):
        out = asyncio.run(LLMDetector(settings()).detect('test'))
    assert out.errors
    assert 'PRIVATE' not in caplog.text
    assert 'prompt_tokens=None' in caplog.text


def test_common_application_defaults_without_environment(monkeypatch):
    import os
    from app.core.config import VLLMSettings
    for key in list(os.environ):
        if key.startswith('VLLM_'):
            monkeypatch.delenv(key)
    configured = VLLMSettings(_env_file=None)
    assert (configured.max_concurrent_requests, configured.timeout_seconds,
            configured.max_file_chars, configured.chunk_overlap_chars,
            configured.max_tokens) == (1, 200, 6000, 500, 512)


def _clean_vllm_env(monkeypatch):
    import os
    for key in list(os.environ):
        if key.startswith('VLLM_'):
            monkeypatch.delenv(key)


@pytest.mark.parametrize('host', ['http://llm.test/v1', 'http://llm.test/v1/', 'http://llm.test/'])
def test_host_trailing_v1_and_slash_are_normalized(monkeypatch, host):
    from app.core.config import VLLMSettings
    _clean_vllm_env(monkeypatch)
    configured = VLLMSettings(_env_file=None, enabled=True, host=host, model='m')
    assert configured.host == 'http://llm.test'


@pytest.mark.parametrize('field', ['host', 'model'])
def test_enabled_rejects_change_me_template_values(monkeypatch, field):
    from pydantic import ValidationError
    from app.core.config import VLLMSettings
    _clean_vllm_env(monkeypatch)
    values = dict(host='http://llm.test', model='m')
    values[field] = 'CHANGE_ME'
    with pytest.raises(ValidationError, match='CHANGE_ME'):
        VLLMSettings(_env_file=None, enabled=True, **values)
    # Kapaliyken sablon degerleri sorun degildir; vLLM'e istek gitmez.
    VLLMSettings(_env_file=None, enabled=False, **values)
