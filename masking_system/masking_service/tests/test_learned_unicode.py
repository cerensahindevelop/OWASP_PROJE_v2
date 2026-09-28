"""Unicode-sensitive learned decisions must mask and restore exact source spans."""
from __future__ import annotations

import asyncio

import pytest

from app.db.models import MaskingContext, MaskingRun, ReviewQueue
from app.services.exporter import export_project
from app.services.learned_decisions import DecisionEntry, LearnedSensitiveDetector
from app.services.review_service import ReviewService
from app.services.unmasker import unmask_project


def _detect(value, text):
    detector = LearnedSensitiveDetector([DecisionEntry(1, value, "INTERNAL_NAME", "*")])
    return asyncio.run(detector.detect(text)).results


@pytest.mark.parametrize("prefix,suffix", [
    ("", ""), ("İstanbul: ", ""), ("", " İstanbul"),
    ("İ ", " e\u0301"),  # Equal total lengths can still hide shifted offsets.
    ("Straße ﬃ S\u0327 ", " İ"),
])
def test_source_offsets_survive_unrelated_unicode(prefix, suffix):
    value = "ApolloSecretName"
    text = prefix + value + " / " + value + suffix
    results = _detect(value, text)
    assert [(r.start, r.end, r.deger) for r in results] == [
        (len(prefix), len(prefix) + len(value), value),
        (len(prefix) + len(value) + 3, len(prefix) + 2 * len(value) + 3, value),
    ]


@pytest.mark.parametrize("learned,source", [
    ("STRASSE", "Straße"), ("office", "oﬃce"),
    ("İstanbul", "I\u0307stanbul"), ("Şirket", "S\u0327irket"),
    ("Apollo", "Ａｐｏｌｌｏ"), ("가", "\u1100\u1161"),
    ("가", "\u3131\u314f"),
])
def test_detection_returns_exact_source_spelling(learned, source):
    results = _detect(learned, "İ: " + source + "!")
    assert [(r.start, r.end, r.deger) for r in results] == [(3, 3 + len(source), source)]


@pytest.mark.parametrize("learned,source", [("i", "İ"), ("s", "ß"), ("f", "ﬃ")])
def test_partial_unicode_expansion_is_not_a_match(learned, source):
    assert _detect(learned, source) == []


def test_placeholders_remain_protected_after_offset_shift():
    text = "İ mask_apollo_1 Apollo APOLLO_TEST_2"
    results = _detect("Apollo", text)
    start = text.index("Apollo")
    assert [(r.start, r.end, r.deger) for r in results] == [(start, start + 6, "Apollo")]


def test_overlapping_candidates_are_preserved_for_overlap_resolver():
    results = _detect("aba", "İ ababa")
    assert [(r.start, r.end) for r in results] == [(2, 5), (4, 7)]


@pytest.mark.parametrize("value,text", [("", "İ"), ("Apollo", ""), ("Apollo", "İ other")])
def test_empty_or_absent_values_have_no_matches(value, text):
    assert _detect(value, text) == []


@pytest.mark.parametrize("value,source_value,comment", [
    ("ApolloSecretName", "ApolloSecretName", ""),
    ("ApolloSecretName", "ApolloSecretName", "# İstanbul\r\n"),
    ("ŞirketSecretName", "S\u0327irketSecretName", "# İstanbul\r\n"),
    ("STRASSESecretName", "StraßeSecretName", "# İstanbul\r\n"),
])
def test_review_approval_export_and_restore_unicode(
    db_session, tmp_path, monkeypatch, value, source_value, comment,
):
    from app.core.config import settings

    monkeypatch.setattr(settings.vllm, "enabled", False)
    context = MaskingContext(project_name=f"unicode-{tmp_path.name}", sicil_no="P-UNICODE", branch_name="main")
    db_session.add(context)
    db_session.flush()
    run = MaskingRun(
        context_id=context.id, operation_type="mask", source_path=str(tmp_path / "old"),
        target_path=str(tmp_path / "old-out"), initiated_by=context.sicil_no, status="completed",
    )
    db_session.add(run)
    db_session.flush()
    review = ReviewQueue(
        run_id=run.id, file_path="a.py", line_number=1, found_value=value,
        entity_type="INTERNAL_NAME", confidence_level="orta", reason="internal name",
    )
    db_session.add(review)
    db_session.flush()
    ReviewService(db_session).approve(review.id)

    source = tmp_path / "source"
    source.mkdir()
    original = (comment + f"name = '{source_value}'\r\n" + comment).encode("utf-8")
    (source / "a.py").write_bytes(original)
    target = tmp_path / "masked"
    report = asyncio.run(export_project(
        db_session, source_path=str(source), target_path=str(target),
        project_name=context.project_name, sicil_no=context.sicil_no,
        branch_name=context.branch_name, initiated_by=context.sicil_no,
    ))
    assert report.files_masked == 1
    output = (target / "a.py").read_text(encoding="utf-8")
    assert source_value not in output
    assert "mask_internal_name_" in output
    restored = tmp_path / "restored"
    unmask_project(
        db_session, source_path=str(target), target_path=str(restored),
        project_name=context.project_name, sicil_no=context.sicil_no,
        branch_name=context.branch_name, initiated_by=context.sicil_no,
    )
    assert (restored / "a.py").read_bytes() == original


def test_literal_before_noncomposing_mark_keeps_existing_match():
    results = _detect("A", "a\u0338")
    assert [(r.start, r.end, r.deger) for r in results] == [(0, 1, "a")]
