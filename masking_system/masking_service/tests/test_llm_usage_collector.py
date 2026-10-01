"""Export raporundaki dosya basina LLM kullanimi Faz A (tespit), Faz C (denetim)
ve Faz E (otomatik duzeltme yeniden denetimi) taramalarinin TAMAMINI kapsamali.

Toplayici contextvar ile tasinir; contextvar asyncio gorevlerine kopyalanir
ama ThreadPoolExecutor.submit/run_in_executor'a kendiliginden gecmez. API
export'u ayri bir thread'de asyncio.run ile calistirdigi icin test ayni
akisi hem dogrudan hem bir executor thread'inden calistirir.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services import audit_reviewer, llm_recognizer, llm_runtime
from app.services import exporter as exporter_module
from app.services.detectors import DetectionOrchestrator, DetectorRegistry
from app.services.llm_detector import LLMDetector
from tests.test_exporter_failure_handling import _cleanup_identity, _run_export

_IDENTITY_PREFIX = "pytest-llm-usage"
_LEAK = "Hakan Yilmaz"


def _response(content: dict) -> dict:
    return {
        "choices": [{"message": {"content": json.dumps(content)}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2},
    }


async def _fake_vllm(host, timeout_seconds, payload, api_key=None):
    text = payload["messages"][-1]["content"]
    if payload["response_format"]["json_schema"]["name"] == "bulgular_semasi":
        return _response({"bulgular": []})
    # Denetim: maskelenmemis kisi adi hala aciksa alintilar. Faz E'deki
    # yeniden denetim maskelenmis metni gorur ve temiz doner.
    if _LEAK in text:
        return _response({"risk_var": True, "bulgular": [{"aciklama": "kisi adi", "ilgili_bolum": _LEAK}]})
    return _response({"risk_var": False, "bulgular": []})


@pytest.mark.parametrize("runner", ["direct", "executor_thread"])
def test_collector_records_detection_audit_and_remediation_reaudit(tmp_path, monkeypatch, runner):
    project = f"{_IDENTITY_PREFIX}-{runner}"
    vllm = exporter_module.settings.vllm
    monkeypatch.setattr(vllm, "enabled", True)
    monkeypatch.setattr(vllm, "host", "http://stub-llm.invalid")
    monkeypatch.setattr(vllm, "model", "stub")
    monkeypatch.setattr(llm_recognizer, "call_vllm", _fake_vllm)
    monkeypatch.setattr(audit_reviewer, "call_vllm", _fake_vllm)

    def _llm_only_orchestrator(*args, **kwargs):
        registry = DetectorRegistry()
        registry.register(LLMDetector(vllm))
        return DetectionOrchestrator(registry)

    monkeypatch.setattr(exporter_module, "build_orchestrator", _llm_only_orchestrator)
    source = tmp_path / "src"
    source.mkdir()
    (source / "notes.txt").write_text(f"sorumlu: {_LEAK}\n", encoding="utf-8")
    (source / "clean.txt").write_text("value = 1\n", encoding="utf-8")
    try:
        if runner == "direct":
            report = _run_export(source, tmp_path / "target", project)
        else:
            with ThreadPoolExecutor(max_workers=1) as pool:
                report = pool.submit(_run_export, source, tmp_path / "target", project).result()

        usage = report.llm_usage_by_file
        assert set(usage) == {"notes.txt", "clean.txt"}, usage
        notes = usage["notes.txt"]
        # Faz A tespit + Faz C denetim + Faz E yeniden denetim.
        assert (notes.detection_scans, notes.audit_scans, notes.requests) == (1, 2, 3)
        assert (notes.prompt_tokens, notes.completion_tokens) == (30, 6)
        clean = usage["clean.txt"]
        assert (clean.detection_scans, clean.audit_scans, clean.requests) == (1, 1, 2)
        assert report.blocked_by_check == {}
        assert (tmp_path / "target" / "notes.txt").read_text(encoding="utf-8").count(_LEAK) == 0

        summary = report.llm_usage_summary
        assert (summary["files"], summary["requests"], summary["failed_scans"]) == (2, 5, 0)
        assert summary["scans_per_file"] == 2.5
        assert "LLM kullanimi: 2 dosya, 5 istek" in report.summary_text()
        # Export bitince toplayici baglamdan kaldirilir.
        assert llm_runtime._usage_collector.get() is None
    finally:
        _cleanup_identity(project)


def test_scans_outside_an_export_are_not_recorded():
    collector = llm_runtime.LLMUsageCollector()
    with llm_runtime.LLMScanMetrics("audit", 1, "x.txt"):
        pass
    assert collector.by_file == {}
    token = llm_runtime.start_llm_usage(collector)
    try:
        with llm_runtime.LLMScanMetrics("audit", 1, "x.txt"):
            pass
    finally:
        llm_runtime.stop_llm_usage(token)
    assert collector.by_file["x.txt"].audit_scans == 1


def test_percentile_is_nearest_rank():
    assert llm_runtime.percentile([], 95) == 0.0
    assert llm_runtime.percentile([3.0, 1.0, 2.0], 50) == 2.0
    assert llm_runtime.percentile([float(i) for i in range(1, 21)], 95) == 19.0
