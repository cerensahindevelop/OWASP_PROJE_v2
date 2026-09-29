"""Asama 8: tek bir bozuk LLM bulgusu tum metin parcasini dusurmemeli.

Degeri metinde birebir gecen ama semasi bozuk (bos/yanlis tipli `tip`,
gecersiz `guven_seviyesi`, eksik `gerekce`) bulgu `orta` guven ve
KURUMSAL_TANIMLAYICI tipiyle kabul edilir; degeri dogrulanamayan bulgu
yalnizca kendisi atilir. Ust duzey yanit yapisi bozuksa parca yine hata
sayilir. Onarilan/atilan sayisi AuditLog'a acik deger olmadan yazilir.
"""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db.models import AuditLog, MaskingContext, MaskingRun
from app.services import llm_recognizer
from app.services.detectors import DetectionOrchestrator, DetectorRegistry
from app.services.llm_detector import LLMDetector
from app.services.mapping_service import MaskingRunContext, apply_detections, detect_matches

TEXT = "user=PRIVATE_VALUE host=OTHER_PRIVATE"


def settings():
    return SimpleNamespace(enabled=True, host="http://llm.test", model="test",
                           timeout_seconds=1, max_file_chars=6000,
                           max_concurrent_requests=4)


def finding(**changes):
    return dict(bulunan_deger="PRIVATE_VALUE", tip="PASSWORD",
                guven_seviyesi="yuksek", gerekce="PRIVATE_REASON") | changes


def response(findings):
    return {"choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps({"bulgular": findings})}}]}


def parse(findings, stats=None):
    return llm_recognizer.parse_and_verify_detections(response(findings), TEXT, [], repair_stats=stats)


@pytest.mark.parametrize("broken", [
    {"tip": ""},
    {"tip": None},
    {"tip": ["PASSWORD"]},
    {"guven_seviyesi": "cok_yuksek"},
    {"guven_seviyesi": ["yuksek"]},
    {"guven_seviyesi": {"a": "b"}},
    {"gerekce": None},
])
def test_broken_finding_with_verified_value_is_accepted_as_generic_orta(broken):
    item = finding() | broken
    stats = llm_recognizer.FindingRepairStats()

    detections = parse([item, finding(bulunan_deger="OTHER_PRIVATE")], stats)

    by_value = {d.deger: d for d in detections}
    assert set(by_value) == {"PRIVATE_VALUE", "OTHER_PRIVATE"}
    repaired = by_value["PRIVATE_VALUE"]
    assert repaired.tip == "KURUMSAL_TANIMLAYICI"
    assert repaired.guven_seviyesi == "orta"
    assert TEXT[repaired.start:repaired.end] == "PRIVATE_VALUE"
    assert by_value["OTHER_PRIVATE"].guven_seviyesi == "yuksek"
    assert (stats.repaired, stats.dropped) == (1, 0)


@pytest.mark.parametrize("item", [
    finding(tip="", bulunan_deger="NOT_IN_TEXT"),
    finding(bulunan_deger=""),
    finding(bulunan_deger=None),
    finding(bulunan_deger={"PRIVATE_KEY": "PRIVATE_VALUE"}),
    {"tip": "PASSWORD", "guven_seviyesi": "yuksek", "gerekce": "x"},
    None, 1, "PRIVATE_VALUE", [],
])
def test_unverifiable_broken_finding_is_dropped_alone(item):
    stats = llm_recognizer.FindingRepairStats()

    detections = parse([item, finding(bulunan_deger="OTHER_PRIVATE")], stats)

    assert [d.deger for d in detections] == ["OTHER_PRIVATE"]
    assert (stats.repaired, stats.dropped) == (0, 1)


@pytest.mark.parametrize("content", [
    "not json", json.dumps({"yanlis": []}), json.dumps({"bulgular": "PRIVATE_VALUE"}), json.dumps([1]),
])
def test_broken_response_structure_still_fails_whole_chunk(content):
    raw = {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}
    with pytest.raises(llm_recognizer.LLMRecognitionError):
        llm_recognizer.parse_and_verify_detections(raw, TEXT, [])


def test_repair_notice_reaches_detector_output_without_values(monkeypatch, caplog):
    async def fake(*args):
        return response([finding(tip=""), finding(bulunan_deger="NOT_IN_TEXT", tip=None),
                         finding(bulunan_deger="OTHER_PRIVATE")])

    monkeypatch.setattr(llm_recognizer, "call_vllm", fake)
    registry = DetectorRegistry()
    registry.register(LLMDetector(settings()))
    with caplog.at_level(logging.INFO):
        output = asyncio.run(DetectionOrchestrator(registry).scan(TEXT, {"file_path": "app.properties"}))

    assert not output.errors and not output.crashes
    assert sorted(d.deger for d in output.results) == ["OTHER_PRIVATE", "PRIVATE_VALUE"]
    assert len(output.notices) == 1
    assert "onarilan=1" in output.notices[0] and "atilan=1" in output.notices[0]
    assert "PRIVATE" not in output.notices[0] + caplog.text


def test_repair_counts_are_written_to_audit_log(db_session, monkeypatch, tmp_path):
    async def fake(*args):
        return response([finding(guven_seviyesi=["yuksek"]), finding(bulunan_deger="NOT_IN_TEXT", tip="")])

    monkeypatch.setattr(llm_recognizer, "call_vllm", fake)
    context = MaskingContext(project_name="pytest-llm-repair", sicil_no="T", branch_name="test")
    db_session.add(context)
    db_session.flush()
    run = MaskingRun(context_id=context.id, operation_type="mask", source_path=str(tmp_path),
                     initiated_by="T", status="in_progress")
    db_session.add(run)
    db_session.flush()
    registry = DetectorRegistry()
    registry.register(LLMDetector(settings()))

    outcome = asyncio.run(detect_matches(DetectionOrchestrator(registry), TEXT, {"file_path": "app.properties"}))
    masked, _mappings = apply_detections(
        db_session, MaskingRunContext(context=context, run_id=run.id), TEXT, outcome, file_path="app.properties",
    )

    assert "PRIVATE_VALUE" not in masked
    details = db_session.scalars(select(AuditLog.detail).where(AuditLog.run_id == run.id)).all()
    notices = [d for d in details if d and d.startswith("llm_bulgu_semasi_bozuk")]
    assert len(notices) == 1
    assert "onarilan=1" in notices[0] and "atilan=1" in notices[0]
    assert not any("PRIVATE" in (d or "") for d in details)
