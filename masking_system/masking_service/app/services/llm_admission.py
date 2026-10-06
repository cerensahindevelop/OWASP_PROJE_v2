"""Host-wide admission across event loops, threads and backend processes.

Every deployment worker must use the same directory and limits. OS locks
release automatically when a process exits; lock files must not be deleted
while workers run. Separate hosts/containers require a shared lock-capable
filesystem, or a model gateway that enforces a distributed quota.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import getpass
import hashlib
import os
from pathlib import Path
import tempfile
import threading
from typing import AsyncIterator, BinaryIO

from app.core.config import settings
from app.services.file_locks import try_lock, unlock

_POLL_SECONDS = 0.025
_REGISTRY_LOCK = threading.Lock()
_POOLS: dict[tuple[str, str, int], "AdmissionPool"] = {}


@dataclass(frozen=True)
class _Lease:
    handle: BinaryIO
    local_lock: threading.Lock

    def release(self) -> None:
        try:
            try:
                unlock(self.handle)
            finally:
                self.handle.close()
        finally:
            self.local_lock.release()


class AdmissionPool:
    def __init__(self, key: str, limit: int, directory: Path) -> None:
        self.limit = max(1, limit)
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        self._directory = directory
        self._digest = digest
        self._control_path = directory / f"{digest}.capacity.lock"
        self._paths = tuple(directory / f"{digest}.{slot}.lock" for slot in range(self.limit))
        self._locks = tuple(threading.Lock() for _ in self._paths)
        self._rotation = threading.Lock()
        self._next = 0

    @staticmethod
    def _open(path: Path) -> BinaryIO:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            return os.fdopen(fd, "r+b")
        except BaseException:
            os.close(fd)
            raise

    def _configure_limit(self, control: BinaryIO) -> bool:
        control.seek(0)
        stored = control.read(32).strip()
        previous = int(stored) if stored else self.limit
        if previous < 1:
            raise ValueError("Invalid admission capacity record")
        if previous != self.limit:
            # A benchmark/restart may change the limit, but only after every
            # old lease has ended. The control lock excludes new admissions.
            probes: list[BinaryIO] = []
            try:
                for slot in range(previous):
                    handle = self._open(self._directory / f"{self._digest}.{slot}.lock")
                    probes.append(handle)
                    if not try_lock(handle):
                        return False
            finally:
                for handle in probes:
                    handle.close()  # Releases acquired OS locks, also on errors.
        if not stored or previous != self.limit:
            control.seek(0)
            control.write(str(self.limit).encode("ascii"))
            control.truncate()
            control.flush()
        return True

    def _try_acquire(self) -> _Lease | None:
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self._open(self._control_path) as control:
            if not try_lock(control):
                return None
            try:
                if not self._configure_limit(control):
                    return None
                return self._try_slot()
            finally:
                unlock(control)

    def _try_slot(self) -> _Lease | None:
        with self._rotation:
            first = self._next
            self._next = (first + 1) % self.limit
        for offset in range(self.limit):
            index = (first + offset) % self.limit
            local = self._locks[index]
            if not local.acquire(blocking=False):
                continue
            handle = None
            acquired = False
            try:
                # An unchanged inode is the identity of a cross-process slot.
                handle = self._open(self._paths[index])
                if os.name == "nt" and os.fstat(handle.fileno()).st_size == 0:
                    handle.write(b"\0")
                    handle.flush()
                acquired = try_lock(handle)
                if acquired:
                    return _Lease(handle, local)
            finally:
                if not acquired:
                    if handle is not None:
                        handle.close()
                    local.release()
        return None

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[None]:
        held = self._try_acquire()
        while held is None:
            # No blocking semaphore wait on any event loop. Cancellation while
            # queued owns no resource, and never calls the model.
            await asyncio.sleep(_POLL_SECONDS)
            held = self._try_acquire()
        try:
            yield
        finally:
            held.release()


def admission_pool(key: str, limit: int) -> AdmissionPool:
    configured = settings.vllm.admission_dir
    if configured is None:
        user = hashlib.sha256(getpass.getuser().encode("utf-8")).hexdigest()[:12]
        directory = Path(tempfile.gettempdir()) / f"masking-llm-admission-{user}"
    else:
        directory = configured.expanduser().resolve()
    identity = (str(directory), key, max(1, limit))
    with _REGISTRY_LOCK:
        pool = _POOLS.get(identity)
        if pool is None:
            pool = _POOLS[identity] = AdmissionPool(key, limit, directory)
        return pool
