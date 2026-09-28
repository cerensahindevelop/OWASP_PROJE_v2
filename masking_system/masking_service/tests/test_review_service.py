"""ReviewService icin karakterizasyon testleri - refactor oncesi guvenlik agi
(Asama 2 / Adim 0). Mevcut davranisi sabitler; henuz "olmasi gereken"
davranisi tanimlamaz, sadece referans noktasi olusturur.

`db_session` fixture'i (bkz. conftest.py) transaction-rollback ile izole
edildigi icin bu testler gercek veritabaninda kalici hicbir iz birakmaz.
"""

from __future__ import annotations

import pytest

from app.core.exceptions import ReviewAlreadyProcessedError
from app.db.models import LearnedDecision, MaskingContext, MaskingRun, ReviewQueue, ValueMapping
from app.services.review_service import ReviewService


def _make_context(db_session, suffix: str) -> MaskingContext:
    context = MaskingContext(
        project_name=f"pytest-review-{suffix}",
        sicil_no="P-REVIEW-0001",
        branch_name="pytest-branch",
    )
    db_session.add(context)
    db_session.flush()
    return context


def _make_run(db_session, context: MaskingContext) -> MaskingRun:
    run = MaskingRun(
        context_id=context.id,
        operation_type="mask",
        source_path="/tmp/review-src",
        target_path="/tmp/review-dst",
        initiated_by="P-REVIEW-0001",
        status="in_progress",
    )
    db_session.add(run)
    db_session.flush()
    return run


def _make_review(db_session, run: MaskingRun, **overrides) -> ReviewQueue:
    defaults = dict(
        run_id=run.id,
        file_path="src/app.py",
        found_value="Ahmet Yilmaz",
        entity_type="PERSON",
        confidence_level="orta",
        reason="Muhtemel kisi adi",
    )
    defaults.update(overrides)
    item = ReviewQueue(**defaults)
    db_session.add(item)
    db_session.flush()
    return item


def test_approve_creates_a_permanent_mapping_and_marks_approved(db_session):
    context = _make_context(db_session, "approve")
    run = _make_run(db_session, context)
    review = _make_review(db_session, run)

    svc = ReviewService(db_session)
    approved = svc.approve(review.id)

    assert approved.status == "approved"

    mappings = db_session.query(ValueMapping).filter_by(context_id=context.id).all()
    assert len(mappings) == 1
    assert mappings[0].placeholder_value.startswith("mask_personel_")
    learned = db_session.query(LearnedDecision).filter_by(context_id=context.id).one()
    assert learned.decision_type == "sensitive"
    assert learned.scope_key == "*"


def test_approve_is_idempotent_reuses_same_mapping_on_second_call(db_session):
    """Ayni context icin ayni found_value'ya sahip iki farkli review kaydinin
    onaylanmasi, get_or_create_mapping'in dedup'i sayesinde AYNI placeholder'a
    (tek mapping/tek sayac) cozulmeli - iki ayri mapping OLUSMAMALI."""
    context = _make_context(db_session, "approve-dup")
    run = _make_run(db_session, context)
    review_a = _make_review(db_session, run, file_path="a.py")
    review_b = _make_review(db_session, run, file_path="b.py")

    svc = ReviewService(db_session)
    svc.approve(review_a.id)
    svc.approve(review_b.id)

    mappings = db_session.query(ValueMapping).filter_by(context_id=context.id).all()
    assert len(mappings) == 1


def test_reject_marks_rejected_and_creates_no_mapping(db_session):
    context = _make_context(db_session, "reject")
    run = _make_run(db_session, context)
    review = _make_review(db_session, run)

    svc = ReviewService(db_session)
    rejected = svc.reject(review.id)

    assert rejected.status == "rejected"
    assert db_session.query(ValueMapping).filter_by(context_id=context.id).count() == 0
    learned = db_session.query(LearnedDecision).filter_by(context_id=context.id).one()
    assert learned.decision_type == "suppression"
    assert learned.scope_key == "src|.py"


def test_ignore_marks_ignored_and_creates_no_mapping(db_session):
    context = _make_context(db_session, "ignore")
    run = _make_run(db_session, context)
    review = _make_review(db_session, run)

    svc = ReviewService(db_session)
    ignored = svc.ignore(review.id)

    assert ignored.status == "ignored"
    assert db_session.query(ValueMapping).filter_by(context_id=context.id).count() == 0


def test_approve_already_processed_review_raises(db_session):
    context = _make_context(db_session, "double-approve")
    run = _make_run(db_session, context)
    review = _make_review(db_session, run)

    svc = ReviewService(db_session)
    svc.approve(review.id)

    with pytest.raises(ReviewAlreadyProcessedError):
        svc.approve(review.id)


def test_approve_without_found_value_does_not_create_mapping(db_session):
    """found_value=None olan bir review kaydi (orn. LLM tarama-hatasi satiri,
    bkz. mapping_service._enqueue_llm_error_review) None deger maskelemeye
    calisilmadan onaylanabilmeli."""
    context = _make_context(db_session, "no-value")
    run = _make_run(db_session, context)
    review = _make_review(db_session, run, found_value=None, entity_type="LLM_TARAMA_HATASI")

    svc = ReviewService(db_session)
    approved = svc.approve(review.id)

    assert approved.status == "approved"
    assert db_session.query(ValueMapping).filter_by(context_id=context.id).count() == 0
