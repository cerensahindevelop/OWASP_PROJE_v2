"""Operation-scoped HTTP connection reuse for LLM detection and audit.

An export owns one lazy transport; nested scans and their child tasks reuse it.
Standalone scans own a shorter scope. No client survives its owning operation
or crosses event loops (exports also run in threads with their own loops).
Admission/retries remain in llm_runtime; response validation stays in callers.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar

import httpx


class LLMHttpTransport:
    """Own a bounded, lazy HTTP pool on one event loop.

    The application admission gate controls model concurrency. The pool also
    bounds open/idle sockets and retains idle connections between detection
    and audit. Authorization and timeouts are supplied per request, never
    installed as mutable client defaults.
    """

    def __init__(self, client_factory: Callable[[], httpx.AsyncClient] | None = None) -> None:
        self._loop = asyncio.get_running_loop()
        self._client_factory = client_factory or self._create_client
        self._client: httpx.AsyncClient | None = None
        self._closed = False

    @staticmethod
    def _create_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(limits=httpx.Limits(
            max_connections=100, max_keepalive_connections=20, keepalive_expiry=30.0,
        ))

    def available_on_current_loop(self) -> bool:
        return not self._closed and self._loop is asyncio.get_running_loop()

    async def post_completion(
        self, host: str, timeout_seconds: float, payload: dict, api_key: str | None = None,
    ) -> httpx.Response:
        if not self.available_on_current_loop():
            raise RuntimeError("LLM HTTP transport is closed or belongs to another event loop")
        if self._client is None:
            # No await before assignment: concurrent chunks create only one client.
            self._client = self._client_factory()
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        return await self._client.post(
            f"{host.rstrip('/')}/v1/chat/completions",
            json=payload, headers=headers, timeout=timeout_seconds,
        )

    async def aclose(self) -> None:
        if self._closed:
            return
        if self._loop is not asyncio.get_running_loop():
            raise RuntimeError("LLM HTTP transport must close on its owning event loop")
        self._closed = True
        if self._client is not None:
            closing = asyncio.create_task(self._client.aclose())
            try:
                await asyncio.shield(closing)
            except asyncio.CancelledError:
                # Finish releasing sockets even if cancellation arrives during cleanup.
                await closing
                raise


_transport: ContextVar[LLMHttpTransport | None] = ContextVar("llm_http_transport", default=None)


@asynccontextmanager
async def llm_http_scope() -> AsyncIterator[LLMHttpTransport]:
    """Reuse a parent scope, otherwise own and close a transport.

    Also usable as an async function decorator. ContextVar propagation lets
    gathered child tasks share the pool without a process-global singleton.
    A copied context in another thread/loop receives a separate transport.
    Owners must await their child tasks before leaving the scope.
    """
    current = _transport.get()
    if current is not None and current.available_on_current_loop():
        yield current
        return

    transport = LLMHttpTransport()
    token = _transport.set(transport)
    try:
        yield transport
    finally:
        _transport.reset(token)
        await transport.aclose()
