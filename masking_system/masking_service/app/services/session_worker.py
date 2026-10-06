"""Run a synchronous SQLAlchemy unit of work away from the API event loop.

The worker owns the Session and its private asyncio loop. Only detached
results may leave it. Cancellation is delivered to the worker loop, and the
caller waits for rollback/close before releasing a database claim.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import threading
from time import time
from typing import TypeVar

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.db.session import SessionLocal

T = TypeVar("T")


class _Cancellation:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requested = False
        self._target: tuple[asyncio.AbstractEventLoop, asyncio.Task] | None = None

    def bind(self, loop: asyncio.AbstractEventLoop, task: asyncio.Task) -> None:
        with self._lock:
            self._target = (loop, task)
            if self._requested:
                task.cancel()

    def unbind(self) -> None:
        with self._lock:
            self._target = None

    def request(self) -> None:
        with self._lock:
            self._requested = True
            if self._target is not None:
                loop, task = self._target
                loop.call_soon_threadsafe(task.cancel)


class SessionWorker:
    def __init__(self, session_factory: Callable[[], Session] = SessionLocal) -> None:
        self._sessions = session_factory

    async def run(self, operation: Callable[[Session], Awaitable[T]], *, deadline: float | None = None) -> T:
        cancellation = _Cancellation()
        worker = asyncio.create_task(run_in_threadpool(self._run, operation, deadline, cancellation))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            cancellation.request()
            # Never clear a lease while its worker can still write/release a file.
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not worker.cancelled():
                worker.exception()  # Consume a failure encountered during cancellation.
            raise

    def _run(self, operation: Callable[[Session], Awaitable[T]], deadline: float | None, cancellation: _Cancellation) -> T:
        async def execute(db: Session) -> T:
            task = asyncio.current_task()
            assert task is not None
            cancellation.bind(asyncio.get_running_loop(), task)
            try:
                remaining = None if deadline is None else max(0.0, deadline - time())
                async with asyncio.timeout(remaining):
                    await asyncio.sleep(0)  # Deliver a cancellation queued before bind.
                    if deadline is not None and time() >= deadline:
                        raise TimeoutError("Revalidation deadline expired before execution")
                    return await operation(db)
            finally:
                cancellation.unbind()

        with self._sessions() as db:
            try:
                result = asyncio.run(execute(db))
                db.commit()
                return result
            except BaseException:
                db.rollback()
                raise
