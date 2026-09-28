"""Hata katmanı ve türü korunmalı; istek içeriği tanı mesajına sızmamalı."""
import asyncio

import httpx
import pytest

from app.services import llm_recognizer
from app.webapp import api_client
from app.webapp.common import error_next_step


@pytest.mark.parametrize('error_type', [
    httpx.ReadTimeout, httpx.ConnectTimeout, httpx.WriteTimeout, httpx.PoolTimeout,
    httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError,
])
def test_backend_transport_failures_preserve_type_without_retry_or_secrets(monkeypatch, error_type):
    requests = []
    def handler(request):
        requests.append(request)
        raise error_type('PRIVATE_EXCEPTION_CONTENT', request=request)
    with httpx.Client(transport=httpx.MockTransport(handler), base_url='http://test.invalid') as client:
        monkeypatch.setattr(api_client, '_get_client', lambda: client)
        with pytest.raises(api_client.ApiError) as caught:
            api_client._request('POST', '/export/upload', content=b'PRIVATE_FILE_CONTENT')
    exc = caught.value
    assert len(requests) == 1  # A timed-out export must not be automatically resubmitted.
    assert exc.status_code == 0
    assert 'katman=arayuz_backend' in exc.detail
    assert f'hata={error_type.__name__}' in exc.detail
    assert 'gecen_saniye=' in exc.detail
    assert 'PRIVATE' not in str(exc) + exc.detail
    if issubclass(error_type, httpx.TimeoutException):
        assert 'zaman aşımına' in exc.message
        assert 'Geçmiş İşlemler' in error_next_step(0, exc.message)


@pytest.mark.parametrize('error_type', [httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError])
def test_llm_empty_network_errors_keep_layer_and_class(monkeypatch, error_type):
    async def handler(request):
        raise error_type('', request=request)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(llm_recognizer.httpx, 'AsyncClient', lambda: real_client(transport=httpx.MockTransport(handler)))
    with pytest.raises(llm_recognizer.LLMRecognitionError) as caught:
        asyncio.run(llm_recognizer.call_vllm('http://test.invalid', 60, {'messages': ['PRIVATE_PROMPT']}, 'PRIVATE_KEY'))
    detail = str(caught.value)
    assert 'katman=backend_llm' in detail
    assert f'hata={error_type.__name__}' in detail
    assert 'timeout_ayari_saniye=60' in detail
    assert 'PRIVATE' not in detail
    assert 'test.invalid' not in detail


@pytest.mark.parametrize('status', [401, 429, 503])
def test_llm_http_errors_preserve_status_without_response_body(monkeypatch, status):
    async def handler(request):
        return httpx.Response(status, text='PRIVATE_RESPONSE', request=request)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(llm_recognizer.httpx, 'AsyncClient', lambda: real_client(transport=httpx.MockTransport(handler)))
    with pytest.raises(llm_recognizer.LLMRecognitionError) as caught:
        asyncio.run(llm_recognizer.call_vllm('http://test.invalid', 60, {}))
    assert f'HTTP={status}' in str(caught.value)
    assert 'HTTPStatusError' in str(caught.value)
    assert 'PRIVATE' not in str(caught.value)


def test_llm_invalid_json_is_distinct_from_network_failure(monkeypatch):
    async def handler(request):
        return httpx.Response(200, text='PRIVATE_NON_JSON', request=request)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(llm_recognizer.httpx, 'AsyncClient', lambda: real_client(transport=httpx.MockTransport(handler)))
    with pytest.raises(llm_recognizer.LLMRecognitionError) as caught:
        asyncio.run(llm_recognizer.call_vllm('http://test.invalid', 60, {}))
    assert 'JSONDecodeError' in str(caught.value)
    assert 'PRIVATE' not in str(caught.value)


def test_successful_llm_response_unchanged(monkeypatch):
    expected = {'choices': [{'message': {'content': '{"bulgular": []}'}}]}
    async def handler(request):
        return httpx.Response(200, json=expected, request=request)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(llm_recognizer.httpx, 'AsyncClient', lambda: real_client(transport=httpx.MockTransport(handler)))
    assert asyncio.run(llm_recognizer.call_vllm('http://test.invalid', 60, {})) == expected
