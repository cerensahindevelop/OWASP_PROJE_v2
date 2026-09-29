from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.db.models import AuditLog, LearnedDecision, MaskingContext, MaskingRun, ReviewQueue
from app.services.detectors import DetectionResult, DetectorOutput
from app.services.exporter import export_project
from app.services.learned_decisions import LearnedDecisionPolicy, remember_decision
from app.services.llm_detector import LLMDetector
from app.services.llm_recognizer import LLMRecognitionError
from app.services.review_service import ReviewService


def _context_run(db_session, tmp_path, suffix="learned"):
    context = MaskingContext(project_name=f"pytest-{suffix}", sicil_no="P-LEARN", branch_name="main")
    db_session.add(context)
    db_session.flush()
    run = MaskingRun(
        context_id=context.id, operation_type="mask", source_path=str(tmp_path / "old"),
        target_path=str(tmp_path / "old-out"), initiated_by="P-LEARN", status="completed",
    )
    db_session.add(run)
    db_session.flush()
    return context, run


def test_approved_finding_is_automatically_masked_on_next_export(db_session, tmp_path):
    context, run = _context_run(db_session, tmp_path, "learn-next")
    review = ReviewQueue(
        run_id=run.id, file_path="src/a.py", line_number=1, found_value="ApolloSecretName",
        entity_type="INTERNAL_NAME", confidence_level="orta", reason="possible internal name",
    )
    db_session.add(review)
    db_session.flush()
    ReviewService(db_session).approve(review.id)

    source = tmp_path / "src"
    source.mkdir()
    (source / "a.py").write_text("name = 'ApolloSecretName'\n", encoding="utf-8")
    target = tmp_path / "out"
    report = asyncio.run(export_project(
        db_session, source_path=str(source), target_path=str(target),
        project_name=context.project_name, sicil_no=context.sicil_no,
        branch_name=context.branch_name, initiated_by=context.sicil_no,
    ))
    assert report.files_masked == 1
    output = (target / "a.py").read_text(encoding="utf-8")
    assert "ApolloSecretName" not in output
    assert "mask_internal_name_" in output


def test_suppression_is_limited_to_context_entity_and_file_scope(db_session, tmp_path):
    context, _run = _context_run(db_session, tmp_path, "scope")
    decision = remember_decision(
        db_session, context_id=context.id, decision_type="suppression", value="Apollo",
        entity_type="PERSON", file_path="src/a.py", source_review_id=None,
    )
    policy = LearnedDecisionPolicy.load(db_session, context.id)
    finding = DetectionResult("Apollo", "PERSON", "orta", "llm")
    assert policy.is_suppressed(finding, "src/b.py").id == decision.id
    assert policy.is_suppressed(finding, "tests/b.py") is None
    assert policy.is_suppressed(finding, "src/b.sql") is None
    assert policy.is_suppressed(DetectionResult("Apollo", "ORGANIZATION", "orta", "llm"), "src/b.py") is None


def test_review_required_file_is_withheld_then_revalidated_after_approval(db_session, tmp_path, monkeypatch):
    from app.services import exporter
    from app.services.audit_reviewer import AuditVerdict

    class UncertainAI:
        async def scan(self, text, metadata=None):
            value = "ApolloCandidate"
            start = text.index(value)
            return DetectorOutput(results=[DetectionResult(
                value, "INTERNAL_NAME", "dusuk", "llm", "possible internal name",
                start, start + len(value),
            )])

    async def clean_audit(*args, **kwargs):
        return AuditVerdict(risky=False)

    # Dusuk guvenli bulgular varsayilan olarak onaya gonderilmez; bu test
    # onay akisini (VLLM_LOW_CONFIDENCE_ACTION=review) dogrular.
    monkeypatch.setattr(exporter.settings.vllm, "low_confidence_action", "review")
    monkeypatch.setattr(exporter, "build_orchestrator", lambda *args, **kwargs: UncertainAI())
    monkeypatch.setattr(exporter, "audit_masked_text", clean_audit)
    source = tmp_path / "hold-src"
    source.mkdir()
    (source / "a.py").write_text("name = 'ApolloCandidate'\n", encoding="utf-8")
    target = tmp_path / "hold-out"
    report = asyncio.run(export_project(
        db_session, source_path=str(source), target_path=str(target),
        project_name="pytest-hold", sicil_no="P-HOLD", branch_name="main", initiated_by="P-HOLD",
    ))
    assert not (target / "a.py").exists()
    review = db_session.query(ReviewQueue).filter_by(run_id=report.run_id).one()
    ReviewService(db_session).approve(review.id)
    output = (target / "a.py").read_text(encoding="utf-8")
    assert "ApolloCandidate" not in output
    assert "mask_internal_name_" in output
    trail = [row.detail for row in db_session.query(AuditLog).filter_by(run_id=report.run_id).all()]
    assert any("user_decision=approve" in detail for detail in trail)
    assert any("revalidation=passed final_output=written" in detail for detail in trail)


def test_llm_detector_fails_closed_without_resubmitting_file(monkeypatch):
    attempts = 0

    async def flaky(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise LLMRecognitionError("temporary")
        return []

    monkeypatch.setattr("app.services.llm_detector.find_llm_detections", flaky)
    detector = LLMDetector(SimpleNamespace())
    result = asyncio.run(detector.detect("text", {"file_path": "a.py"}))
    assert attempts == 1
    assert result.results == []
    assert len(result.errors) == 1
    assert "VALIDATION_FAILED" in result.errors[0]


def test_llm_failure_is_a_technical_audit_event_not_a_review_finding(db_session, tmp_path):
    context, run = _context_run(db_session, tmp_path, "technical")
    db_session.add(AuditLog(
        run_id=run.id, file_path="src/a.py", action="error",
        detail="LLM taramasi 3 denemede tamamlanamadi; dosya VALIDATION_FAILED",
    ))
    db_session.flush()
    assert db_session.query(ReviewQueue).filter_by(run_id=run.id).count() == 0
    assert "VALIDATION_FAILED" in db_session.query(AuditLog).filter_by(run_id=run.id).one().detail


def _export_with_risky_audit(db_session, tmp_path, monkeypatch, suffix, quotes):
    from app.services import exporter
    from app.services.audit_reviewer import AuditFinding, AuditVerdict

    async def risky_audit(*args, **kwargs):
        return AuditVerdict(risky=True, findings=[
            AuditFinding(aciklama="olasi ic isim", ilgili_bolum=quote) for quote in quotes])

    monkeypatch.setattr(exporter, "audit_masked_text", risky_audit)
    source = tmp_path / f"{suffix}-src"
    (source / "src").mkdir(parents=True)
    (source / "src" / "a.py").write_text("note = 'HarmlessWord'\nother = 'OtherWord'\n", encoding="utf-8")
    target = tmp_path / f"{suffix}-out"
    report = asyncio.run(export_project(
        db_session, source_path=str(source), target_path=str(target),
        project_name=f"pytest-{suffix}", sicil_no="P-LEARN", branch_name="main", initiated_by="P-LEARN",
    ))
    return report, target / "src" / "a.py"


def test_export_applies_learned_suppression_to_audit_findings(db_session, tmp_path, monkeypatch):
    from app.db.models import AuditWarning

    context, _run = _context_run(db_session, tmp_path, "sup-export")
    remember_decision(
        db_session, context_id=context.id, decision_type="suppression", value="HarmlessWord",
        entity_type="INTERNAL_NAME", file_path="src/other.py", source_review_id=None,
    )
    db_session.flush()
    report, output = _export_with_risky_audit(db_session, tmp_path, monkeypatch, "sup-export", ["HarmlessWord"])

    # Kullanicinin "hassas degil" dedigi deger ayni kapsamda tekrar onaya dusmez.
    assert output.exists()
    outcome = next(o for o in report.outcomes if o.relative_path == "src/a.py")
    assert outcome.final_state == "READY"
    assert db_session.query(AuditWarning).filter_by(run_id=report.run_id).count() == 0
    trail = [row.detail for row in db_session.query(AuditLog).filter_by(run_id=report.run_id).all()]
    assert any("suppressed_findings=1" in detail for detail in trail)
    # Bastirilan degerin kendisi loglanmaz.
    assert not any("HarmlessWord" in (detail or "") for detail in trail)


def test_export_keeps_unsuppressed_audit_findings_quarantined(db_session, tmp_path, monkeypatch):
    from app.db.models import AuditWarning

    context, _run = _context_run(db_session, tmp_path, "sup-partial")
    remember_decision(
        db_session, context_id=context.id, decision_type="suppression", value="HarmlessWord",
        entity_type="INTERNAL_NAME", file_path="src/other.py", source_review_id=None,
    )
    db_session.flush()
    report, output = _export_with_risky_audit(
        db_session, tmp_path, monkeypatch, "sup-partial", ["HarmlessWord", "OtherWord"])

    assert not output.exists()
    warning = db_session.query(AuditWarning).filter_by(run_id=report.run_id).one()
    # Kalan bulgu gerekcede gorunur, bastirilan bulgu gorunmez.
    assert "OtherWord" in warning.reasoning and "HarmlessWord" not in warning.reasoning


def test_export_suppression_does_not_cross_file_scope(db_session, tmp_path, monkeypatch):
    context, _run = _context_run(db_session, tmp_path, "sup-scope")
    remember_decision(
        db_session, context_id=context.id, decision_type="suppression", value="HarmlessWord",
        entity_type="INTERNAL_NAME", file_path="tests/other.py", source_review_id=None,
    )
    db_session.flush()
    _report, output = _export_with_risky_audit(db_session, tmp_path, monkeypatch, "sup-scope", ["HarmlessWord"])
    assert not output.exists()


def _context(db_session, project, sicil, branch):
    context = MaskingContext(project_name=project, sicil_no=sicil, branch_name=branch)
    db_session.add(context)
    db_session.flush()
    return context


def _remember(db_session, context, decision_type, value="ApolloInternal"):
    return remember_decision(
        db_session, context_id=context.id, decision_type=decision_type, value=value,
        entity_type="INTERNAL_NAME", file_path="src/a.py", source_review_id=None,
    )


def test_sensitive_decisions_apply_project_wide(db_session):
    author = _context(db_session, "pytest-scope-p", "S-1", "main")
    other_dev = _context(db_session, "pytest-scope-p", "S-2", "feature")
    other_project = _context(db_session, "pytest-scope-q", "S-1", "main")
    _remember(db_session, author, "sensitive")

    assert [e.value for e in LearnedDecisionPolicy.load(db_session, other_dev.id).sensitive] == ["ApolloInternal"]
    assert LearnedDecisionPolicy.load(db_session, other_project.id).sensitive == []


def test_same_sensitive_value_from_two_contexts_is_one_entry(db_session):
    first = _context(db_session, "pytest-scope-dup", "S-1", "main")
    second = _context(db_session, "pytest-scope-dup", "S-2", "main")
    _remember(db_session, first, "sensitive")
    _remember(db_session, second, "sensitive")

    assert len(LearnedDecisionPolicy.load(db_session, first.id).sensitive) == 1


def test_not_sensitive_decisions_follow_only_the_author_across_branches(db_session):
    author_main = _context(db_session, "pytest-scope-s", "S-1", "main")
    author_feature = _context(db_session, "pytest-scope-s", "S-1", "feature")
    other_dev = _context(db_session, "pytest-scope-s", "S-2", "main")
    other_project = _context(db_session, "pytest-scope-t", "S-1", "main")
    _remember(db_session, author_main, "suppression")

    finding = DetectionResult("ApolloInternal", "INTERNAL_NAME", "orta", "llm")
    assert LearnedDecisionPolicy.load(db_session, author_feature.id).is_suppressed(finding, "src/b.py")
    # Baska bir gelistirici ya da baska proje bu karardan etkilenmez.
    assert LearnedDecisionPolicy.load(db_session, other_dev.id).is_suppressed(finding, "src/b.py") is None
    assert LearnedDecisionPolicy.load(db_session, other_project.id).is_suppressed(finding, "src/b.py") is None


def test_inactive_decisions_are_not_shared(db_session):
    author = _context(db_session, "pytest-scope-i", "S-1", "main")
    other_dev = _context(db_session, "pytest-scope-i", "S-2", "main")
    _remember(db_session, author, "sensitive").is_active = False
    db_session.flush()

    assert LearnedDecisionPolicy.load(db_session, other_dev.id).sensitive == []


def test_other_developers_sensitive_decision_is_masked_in_export(db_session, tmp_path):
    author = _context(db_session, "pytest-scope-export", "S-1", "main")
    _remember(db_session, author, "sensitive", value="ApolloSecretName")

    source = tmp_path / "scope-src"
    source.mkdir()
    (source / "a.py").write_text("name = 'ApolloSecretName'\n", encoding="utf-8")
    target = tmp_path / "scope-out"
    asyncio.run(export_project(
        db_session, source_path=str(source), target_path=str(target),
        project_name="pytest-scope-export", sicil_no="S-2", branch_name="dev", initiated_by="S-2",
    ))
    assert "ApolloSecretName" not in (target / "a.py").read_text(encoding="utf-8")
