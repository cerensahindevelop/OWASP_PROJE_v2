"""Authenticated, language-independent metadata accompanying an export.

No original paths or values are stored. Source text digests are keyed to
avoid exposing hashes of guessable secrets. Edited files remain usable,
but unmask must report that source equivalence is no longer established.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile
import threading
import time

from app.core.crypto import hash_value
from app.services.file_locks import try_lock as _try_lock, unlock as _unlock

MANIFEST_NAME = ".masking-integrity.json"
MAX_MANIFEST_BYTES = 64 * 1024 * 1024

# Karantinadan serbest birakma manifest'i oku-degistir-yaz ile gunceller; ayni
# hedefe es zamanli iki serbest birakma birbirinin kaydini silmesin diye hedef
# kok dizini basina kilit. Backend birden fazla worker/surecle calisabilir:
# kilit isletim sistemi dosya kilididir (Windows: msvcrt, POSIX: fcntl) ve
# surec-ici thread'ler icin ayrica threading.Lock ile korunur.
MANIFEST_LOCK_TIMEOUT_SECONDS = 30.0
_LOCK_POLL_SECONDS = 0.05
_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.Lock] = {}

def manifest_lock_path(root: Path) -> Path:
    """Kilit dosyasi hedefin ICINDE degil YANINDA durur: indirilen ciktiya
    (zip) girmez. Silinmez - silinen kilit dosyasi yaris yaratir."""
    root = Path(root).resolve()
    return root.parent / f".{root.name}.masking-manifest.lock"


@contextmanager
def manifest_lock(root: Path, timeout: float = MANIFEST_LOCK_TIMEOUT_SECONDS):
    path = manifest_lock_path(root)
    key = os.path.normcase(str(path))
    with _LOCKS_GUARD:
        thread_lock = _LOCKS.setdefault(key, threading.Lock())
    if not thread_lock.acquire(timeout=timeout):
        raise ValueError("Dosya bütünlük kaydı kilidi alınamadı; işlemi tekrar deneyin.")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a+b") as handle:
            deadline = time.monotonic() + timeout
            while not _try_lock(handle):
                if time.monotonic() >= deadline:
                    raise ValueError("Dosya bütünlük kaydı başka bir işlem tarafından kilitli; işlemi tekrar deneyin.")
                time.sleep(_LOCK_POLL_SECONDS)
            try:
                yield
            finally:
                _unlock(handle)
    finally:
        thread_lock.release()


def source_tag(context_id: int, digest: str) -> str:
    return hash_value(context_id, "source-integrity-v1:" + digest)


def _signature(context_id: int, payload: dict) -> str:
    return hash_value(context_id, "manifest-v1:" + json.dumps(payload, sort_keys=True, separators=(",", ":")))


def write_manifest(root: Path, context_id: int, files: dict, *, complete: bool, job_id: int | None = None) -> None:
    payload = {"version": 1, "files": files, "complete": complete}
    if job_id is not None:
        payload.update(version=2, job_id=job_id)
    document = {"payload": payload, "signature": _signature(context_id, payload)}
    data = json.dumps(document, sort_keys=True, ensure_ascii=True).encode("utf-8")
    if len(data) > MAX_MANIFEST_BYTES:
        raise ValueError("Dosya butunluk kaydi boyut sinirini asti.")
    # Gecici dosya + os.replace: yarim yazilmis (imzasi bozuk) manifest kalmaz.
    fd, temp_name = tempfile.mkstemp(prefix=".masking-integrity-", suffix=".tmp", dir=root)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        _replace_with_retry(temp_name, root / MANIFEST_NAME)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def _replace_with_retry(src: str, dst: Path) -> None:
    # Windows: antivirus/OneDrive hedef dosyayi kisa sure acik tutabilir
    # (bkz. output_publication._rename_with_retry); POSIX'te ilk deneme gecer.
    for attempt in range(5):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.2 * (attempt + 1))


# Imzayi DOGRULAMADAN yalnizca paketin maskeleme islem kimligini okur:
# geri almada proje/branch bu islemden bulunur. Sonuc yetki kaniti degildir;
# imza, islemin baglamiyla read_manifest'te ayrica dogrulanir.
def peek_manifest_job_id(root: Path) -> int | None:
    path = root / MANIFEST_NAME
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_MANIFEST_BYTES:
            return None
        job_id = json.loads(path.read_bytes())["payload"].get("job_id")
    except (ValueError, KeyError, TypeError, AttributeError, OSError, RecursionError):
        return None
    return job_id if type(job_id) is int and job_id >= 1 else None


def read_manifest(root: Path, context_id: int) -> dict | None:
    path = root / MANIFEST_NAME
    if not path.exists() and not path.is_symlink():
        return None
    try:
        if path.is_symlink() or path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ValueError
        document = json.loads(path.read_bytes())
        payload = document["payload"]
        if not hmac.compare_digest(document["signature"], _signature(context_id, payload)):
            raise ValueError
        if payload["version"] not in (1, 2) or not isinstance(payload["files"], dict) or not isinstance(payload["complete"], bool):
            raise ValueError
        if payload["version"] == 2 and (type(payload.get("job_id")) is not int or payload["job_id"] < 1):
            raise ValueError
        for name, item in payload["files"].items():
            # Paths are only lookup keys; never create files from these keys.
            if not isinstance(name, str) or not isinstance(item, dict):
                raise ValueError
            if not all(isinstance(item.get(key), str) for key in ("encoding", "masked_sha256", "source_tag")):
                raise ValueError
            if item["encoding"] == "java-class-v1" and not isinstance(item.get("source_bytes_tag"), str):
                raise ValueError
            if not isinstance(item.get("mode"), int) or not 0 <= item["mode"] <= 0o777:
                raise ValueError
        return payload
    except (ValueError, KeyError, TypeError, OSError, RecursionError):
        raise ValueError("Dosya butunluk kaydi gecersiz veya secilen proje kimligiyle eslesmiyor; hedef degistirilmedi.") from None


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
