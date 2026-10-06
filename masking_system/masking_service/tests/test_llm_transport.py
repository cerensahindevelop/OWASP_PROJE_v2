"""HTTP reuse, operation ownership, loop isolation and cancellation regressions."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app.services import audit_reviewer, llm_recognizer, llm_runtime, llm_transport
from app.services.llm_transport import LLMHttpTransport, llm_http_scope


def settings(**overrides):
    values = dict(enabled=True, host="http://llm.test", model="test", timeout_seconds=2,
                  max_file_chars=1200, chunk_overlap_chars=100, max_tokens=512,
                  max_concurrent_requests=2, seed=42, api_key=None, transient_retries=0)
    values.update(overrides)
    return SimpleNamespace(**values)


def reply():
    content = json.dumps({"bulgular": [], "risk_var": False})
    return {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}


@pytest.fixture
def mock_clients(monkeypatch):
    original = httpx.AsyncClient
    clients = []

    def install(handler):
        def create(**kwargs):
            client = original(transport=httpx.MockTransport(handler), **kwargs)
            clients.append(client)
            return client
        monkeypatch.setattr(llm_transport.httpx, "AsyncClient", create)
        return clients

    return install


def test_detection_and_audit_chunks_share_one_operation_pool(mock_clients):
    requests = []
    async def handler(request):
        requests.append(request)
        await asyncio.sleep(0)
        return httpx.Response(200, json=reply())
    clients = mock_clients(handler)

    async def run():
        async with llm_http_scope() as outer:
            async with llm_http_scope() as inner:
                assert inner is outer
                assert await llm_recognizer.find_llm_detections("example\n" * 500, [], settings()) == []
            assert not clients[0].is_closed
            verdict = await audit_reviewer.audit_masked_text("example\n" * 500, settings())
            assert not verdict.risky
            assert len(clients) == 1
        assert clients[0].is_closed
    asyncio.run(run())
    assert len(requests) > 4


def test_independent_operations_close_their_own_pools(mock_clients):
    async def handler(request):
        await asyncio.sleep(0)
        return httpx.Response(200, json=reply())
    clients = mock_clients(handler)

    async def run():
        await asyncio.gather(*(
            llm_recognizer.find_llm_detections("example\n" * 500, [], settings())
            for _ in range(2)
        ))
    asyncio.run(run())
    assert len(clients) == 2
    assert all(client.is_closed for client in clients)
    # Another asyncio.run must never reuse sockets attached to the closed loop.
    asyncio.run(run())
    assert len(clients) == 4
    assert all(client.is_closed for client in clients)


def test_copied_context_in_worker_loop_gets_separate_pool(mock_clients):
    clients = mock_clients(lambda request: httpx.Response(200, json=reply()))

    async def child(parent):
        async with llm_http_scope() as transport:
            assert transport is not parent
            with pytest.raises(RuntimeError, match="another event loop"):
                await parent.post_completion("http://llm.test", 2, {})
            await llm_recognizer.call_vllm("http://llm.test", 2, {})

    async def run():
        async with llm_http_scope() as parent:
            await llm_recognizer.call_vllm("http://llm.test", 2, {})
            await asyncio.to_thread(lambda: asyncio.run(child(parent)))
            assert not clients[0].is_closed
            assert clients[1].is_closed
            await llm_recognizer.call_vllm("http://llm.test", 2, {})
        assert all(client.is_closed for client in clients)
    asyncio.run(run())
    assert len(clients) == 2


def test_request_credentials_and_timeouts_do_not_become_client_defaults(mock_clients):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=reply())
    clients = mock_clients(handler)

    async def run():
        async with llm_http_scope():
            await llm_recognizer.call_vllm("http://llm.test/", 3, {}, "first-key")
            await llm_recognizer.call_vllm("http://llm.test", 7, {})
            await llm_recognizer.call_vllm("http://other.test", 5, {}, "other-key")
    asyncio.run(run())
    assert len(clients) == 1
    assert [r.headers.get("Authorization") for r in requests] == ["Bearer first-key", None, "Bearer other-key"]
    assert [r.extensions["timeout"]["read"] for r in requests] == [3, 7, 5]
    assert all(r.url.path == "/v1/chat/completions" for r in requests)


@pytest.mark.parametrize("enabled,text", [(False, "example"), (True, ""), (True, " \n\t")])
def test_disabled_or_empty_scans_never_create_client(monkeypatch, enabled, text):
    def forbidden():
        raise AssertionError("No HTTP client needed")
    monkeypatch.setattr(LLMHttpTransport, "_create_client", staticmethod(forbidden))

    async def run():
        assert await llm_recognizer.find_llm_detections(text, [], settings(enabled=enabled)) == []
        assert not (await audit_reviewer.audit_masked_text(text, settings(enabled=enabled))).risky
    asyncio.run(run())


def test_export_owns_one_pool_across_files_detection_and_audit(mock_clients, monkeypatch, tmp_path, db_session):
    from app.core.config import VLLMSettings
    from app.services import exporter
    from app.services.llm_detector import LLMDetector

    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=reply())
    clients = mock_clients(handler)
    config = VLLMSettings(_env_file=None, enabled=True, host="http://llm.test", model="test",
                          max_concurrent_requests=1, file_batch_size=1, api_key=None)
    monkeypatch.setattr(exporter.settings, "vllm", config)

    class LLMOnlyOrchestrator:
        async def scan(self, text, metadata=None):
            return await LLMDetector(config).detect(text, metadata)

    monkeypatch.setattr(exporter, "build_orchestrator", lambda *a, **kw: LLMOnlyOrchestrator())
    source = tmp_path / "source"
    source.mkdir()
    for name in ("first.txt", "second.txt"):
        (source / name).write_text("Public example text.\n")
    report = asyncio.run(exporter.export_project(
        db_session, source_path=str(source), target_path=str(tmp_path / "output"),
        project_name="transport-test", sicil_no="TEST-POOL", branch_name="main", initiated_by="test",
    ))
    assert report.files_scanned == 2
    assert not report.has_quarantined_files
    assert len(requests) == 4  # Two detections and two audits across separate batches.
    assert len(clients) == 1 and clients[0].is_closed


def test_retry_reuses_client_and_preserves_admission(mock_clients, monkeypatch):
    attempts = 0
    def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(503 if attempts == 1 else 200, json=reply())
    clients = mock_clients(handler)
    monkeypatch.setattr(llm_runtime, "_RETRY_BASE_DELAY_SECONDS", 0)
    assert asyncio.run(llm_recognizer.find_llm_detections(
        "example", [], settings(transient_retries=1),
    )) == []
    assert attempts == 2
    assert len(clients) == 1 and clients[0].is_closed


def test_failure_closes_pool_and_restores_context(mock_clients):
    clients = mock_clients(lambda request: httpx.Response(503, json={}))

    async def run():
        with pytest.raises(llm_recognizer.LLMRecognitionError):
            await llm_recognizer.find_llm_detections("example", [], settings())
        assert clients[0].is_closed
        async with llm_http_scope() as transport:
            assert transport.available_on_current_loop()
        with pytest.raises(RuntimeError, match="closed"):
            await transport.post_completion("http://llm.test", 2, {})
        await transport.aclose()  # Idempotent cleanup.
    asyncio.run(run())


def test_cancelled_scan_drains_chunks_before_closing_pool(mock_clients):
    entered = asyncio.Event()
    active = 0
    async def handler(request):
        nonlocal active
        active += 1
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            active -= 1
    clients = mock_clients(handler)

    async def run():
        task = asyncio.create_task(llm_recognizer.find_llm_detections("example\n" * 500, [], settings()))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert active == 0
        assert len(clients) == 1 and clients[0].is_closed
    asyncio.run(run())


def test_cancellation_during_cleanup_waits_for_socket_close():
    async def run():
        closing = asyncio.Event()
        release = asyncio.Event()
        closed = asyncio.Event()

        class SlowClosingClient:
            async def post(self, *args, **kwargs):
                return httpx.Response(200)

            async def aclose(self):
                closing.set()
                await release.wait()
                closed.set()

        transport = LLMHttpTransport(client_factory=SlowClosingClient)
        await transport.post_completion("http://llm.test", 2, {})
        task = asyncio.create_task(transport.aclose())
        await closing.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set()
    asyncio.run(run())


def test_detection_and_audit_reuse_real_http_connection(monkeypatch):
    """Prove TCP reuse with a local HTTP/1.1 server, without a live model."""
    original = httpx.AsyncClient
    monkeypatch.setattr(llm_transport.httpx, "AsyncClient", lambda **kw: original(trust_env=False, **kw))
    connections = 0
    requests = 0

    async def run():
        handlers = []
        async def serve(reader, writer):
            nonlocal connections, requests
            connections += 1
            handlers.append(asyncio.current_task())
            try:
                while True:
                    headers = await reader.readuntil(b"\r\n\r\n")
                    length = next(int(line.split(b":", 1)[1]) for line in headers.split(b"\r\n")
                                  if line.lower().startswith(b"content-length:"))
                    await reader.readexactly(length)
                    requests += 1
                    body = json.dumps(reply()).encode()
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                                 + str(len(body)).encode() + b"\r\n\r\n" + body)
                    await writer.drain()
            except asyncio.IncompleteReadError:
                pass
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        async with server:
            port = server.sockets[0].getsockname()[1]
            config = settings(host=f"http://127.0.0.1:{port}", max_concurrent_requests=1)
            async with llm_http_scope():
                await llm_recognizer.find_llm_detections("example\n" * 500, [], config)
                await audit_reviewer.audit_masked_text("example\n" * 500, config)
            await asyncio.gather(*handlers)

    async def bounded():
        await asyncio.wait_for(run(), timeout=10)
    asyncio.run(bounded())
    assert requests > 4
    assert connections == 1
