"""VLLM_AUDIT_UNCHANGED_FILES=false: hicbir katmanin degistirmedigi dosyalar
ikinci LLM denetimine gonderilmez; degisen ya da hatali dosyalar her zaman gider."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.services import exporter
from app.services.audit_reviewer import AuditVerdict


def _masked(text: str, masked_text: str, **kwargs) -> exporter._MaskedFile:
    prep = exporter._FilePrep(scanned=None, dest_path=Path("out.txt"), rel="src/a.txt", text=text)
    return exporter._MaskedFile(prep=prep, masked_text=masked_text, rule_breakdown={}, match_count=0, **kwargs)


@pytest.fixture()
def audit_calls(monkeypatch):
    calls = []

    async def fake_audit(masked_text, vllm_settings):
        calls.append(masked_text)
        return AuditVerdict(risky=False)

    monkeypatch.setattr(exporter, "audit_masked_text", fake_audit)
    return calls


def test_unchanged_file_is_audited_by_default(monkeypatch, audit_calls):
    monkeypatch.setattr(exporter.settings.vllm, "audit_unchanged_files", True)
    asyncio.run(exporter._audit_masked_file(_masked("clean", "clean")))
    assert audit_calls == ["clean"]


@pytest.mark.parametrize("masked_file,expected_calls", [
    (_masked("clean", "clean"), []),
    (_masked("ip 10.0.0.1", "ip mask_ip_1"), ["ip mask_ip_1"]),
    (_masked("clean", "clean", llm_errors=["timeout"]), ["clean"]),
    (_masked("clean", "clean", detector_crashes=["presidio"]), ["clean"]),
])
def test_skip_only_applies_to_untouched_successful_files(monkeypatch, audit_calls, masked_file, expected_calls):
    monkeypatch.setattr(exporter.settings.vllm, "audit_unchanged_files", False)
    verdict = asyncio.run(exporter._audit_masked_file(masked_file))
    assert audit_calls == expected_calls
    assert isinstance(verdict, AuditVerdict) and not verdict.risky
