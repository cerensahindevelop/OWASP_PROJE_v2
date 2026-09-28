"""Authenticated, language-independent metadata accompanying an export.

No original paths or values are stored. Source text digests are keyed to
avoid exposing hashes of guessable secrets. Edited files remain usable,
but unmask must report that source equivalence is no longer established.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

from app.core.crypto import hash_value

MANIFEST_NAME = ".masking-integrity.json"
MAX_MANIFEST_BYTES = 64 * 1024 * 1024


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
    (root / MANIFEST_NAME).write_bytes(data)


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
