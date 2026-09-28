"""Offline diagnosis of detection failures with requests=0.

Run with the backend's Python from masking_service. No LLM requests are sent.
Only synthetic text is scanned; active LLM instructions are read from SQLite
in read-only mode. No exception messages, prompts or rule values are printed.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import closing
import hashlib
import inspect
import json
from pathlib import Path
import sqlite3
import sys
from traceback import walk_tb
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SAMPLE = 'SERVICE_NAME = "synthetic-preflight-service"\n'


def failure(stage, exc):
    frames = " > ".join(
        f"{Path(frame.f_code.co_filename).name}:{line}:{frame.f_code.co_name}"
        for frame, line in walk_tb(exc.__traceback__)
    )
    print(f"FAIL stage={stage} error_type={type(exc).__name__} frames={frames}")


def read_instructions(database_path):
    # mode=ro cannot create a missing database or update the existing one.
    uri = Path(database_path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        rows = db.execute(
            "SELECT aciklama FROM filtre_kurallari "
            "WHERE aktif_mi = 1 AND desen_tipi = 'llm' "
            "AND kaynak_katman IN ('katman1', 'llm') "
            "AND kurumsal_terim_silinme_tarihi IS NULL ORDER BY oncelik"
        ).fetchall()
    return [row[0] for row in rows if row[0]]


def module_info(module):
    path = Path(module.__file__).resolve()
    print(f"module={module.__name__} path={path} sha256={hashlib.sha256(path.read_bytes()).hexdigest()}")


async def check_paths(settings, instructions):
    from app.services import audit_reviewer, llm_recognizer

    calls = 0

    async def fake_call(host, timeout_seconds, payload, api_key=None):
        nonlocal calls
        # Exercise JSON serializability too, without printing the payload.
        json.dumps(payload)
        calls += 1
        body = {"bulgular": []}
        if payload["response_format"]["json_schema"]["name"] == "denetim_semasi":
            body["risk_var"] = False
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(body)}}]}

    def no_network(*args, **kwargs):
        raise RuntimeError("Offline check forbids network access")

    failures = 0
    with patch.object(llm_recognizer, "call_vllm", fake_call), \
         patch.object(audit_reviewer, "call_vllm", fake_call), \
         patch("socket.socket.connect", no_network), \
         patch("socket.create_connection", no_network), \
         patch("socket.getaddrinfo", no_network):
        cases = [
            ("detection_without_rules", lambda: llm_recognizer.find_llm_detections(
                SAMPLE, [], settings, {"file_path": "preflight.txt"}, [])),
            ("audit", lambda: audit_reviewer.audit_masked_text(SAMPLE, settings)),
        ]
        if instructions is not None:
            cases.insert(1, ("detection_with_db_rules", lambda: llm_recognizer.find_llm_detections(
                SAMPLE, [], settings, {"file_path": "preflight.txt"}, instructions)))
        for stage, operation in cases:
            before = calls
            try:
                await operation()
                if calls == before:
                    raise RuntimeError("No synthetic HTTP boundary reached")
                print(f"PASS stage={stage} stub_calls={calls - before}")
            except Exception as exc:
                failures += 1
                failure(stage, exc)
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-db", action="store_true", help="Only check code/settings, not DB instructions")
    args = parser.parse_args(argv)
    print(f"python={sys.version.split()[0]} executable={sys.executable}")
    print("mode=offline network=blocked scanned_text=synthetic")
    try:
        from app.core import config
        from app.services import audit_reviewer, llm_recognizer, llm_runtime

        for module in (llm_recognizer, llm_runtime, audit_reviewer):
            module_info(module)
        for fn in (llm_recognizer.build_detection_request, llm_recognizer.find_llm_detections,
                   llm_runtime.LLMScanMetrics.request):
            parameters = ",".join(inspect.signature(fn).parameters)
            print(f"function={fn.__qualname__} parameters={parameters}")
        settings = config.VLLMSettings()
        if not settings.enabled:
            print("FAIL stage=settings reason=VLLM_ENABLED_false; use the backend environment")
            return 1
        for key in ("max_concurrent_requests", "max_file_chars", "max_tokens", "disable_thinking"):
            value = getattr(settings, key, None)
            print(f"setting={key} type={type(value).__name__}")
    except Exception as exc:
        failure("imports_or_settings", exc)
        return 1

    instructions = None
    failures = 0
    if args.skip_db:
        print("SKIP stage=db_rules scope=code_and_settings_only")
    else:
        try:
            instructions = read_instructions(config.DatabaseSettings().resolved_path)
            print(f"db_rules=count:{len(instructions)} types:{','.join(sorted({type(v).__name__ for v in instructions}))}")
        except Exception as exc:
            failure("db_rules", exc)
            failures += 1
    failures += asyncio.run(check_paths(settings, instructions))
    print("RESULT=" + ("OFFLINE_OK" if not failures else "OFFLINE_FAILED"))
    print("This checks a fresh process; it does not validate a running backend or real model responses.")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
