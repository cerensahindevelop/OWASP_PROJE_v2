"""SYNTAX_FAILURE_ACTION=warn|block: tum gizlilik kontrollerinden gecmis ama
maskelemenin sozdizimini bozdugu dosya `warn` modunda uyariyla ciktiya alinir.
Varsayilan `block`. Gizlilik kontrolleri ve otomatik duzeltme her modda
bloklamaya devam eder.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from app.core.config import ValidationSettings
from app.db.models import AuditLog, AuditWarning
from app.services import exporter
from app.services.audit_reviewer import AuditFinding, AuditVerdict
from app.services.detectors import DetectorOutput

_FAKE_ERROR = "Python sozdizimi hatasi (satir 1, sutun 1)"


class _NoDetections:
    async def scan(self, text, metadata=None):
        return DetectorOutput(results=[])


def _broken_syntax(*args, **kwargs):
    # Maskelemenin sozdizimini bozdugu durumu canlandirir.
    return _FAKE_ERROR


def _export(db_session, tmp_path, files: dict[str, str], suffix: str):
    source = tmp_path / f"{suffix}-src"
    for rel, content in files.items():
        path = source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    target = tmp_path / f"{suffix}-out"
    report = asyncio.run(exporter.export_project(
        db_session, source_path=str(source), target_path=str(target),
        project_name=f"pytest-{suffix}", sicil_no="P-SYN", branch_name="main", initiated_by="P-SYN",
    ))
    return report, target


def _setup(monkeypatch, action, audit=None):
    async def clean(*args, **kwargs):
        return AuditVerdict(risky=False)

    monkeypatch.setattr(exporter.settings.validation, "syntax_failure_action", action, raising=False)
    monkeypatch.setattr(exporter, "build_orchestrator", lambda *a, **k: _NoDetections())
    monkeypatch.setattr(exporter, "audit_masked_text", audit or clean)
    monkeypatch.setattr(exporter, "validate_masked_syntax", _broken_syntax)


def test_default_is_block_and_env_names_are_accepted(monkeypatch):
    for key in ("SYNTAX_FAILURE_ACTION", "VALIDATION_SYNTAX_FAILURE_ACTION"):
        monkeypatch.delenv(key, raising=False)
    assert ValidationSettings(_env_file=None).syntax_failure_action == "block"
    monkeypatch.setenv("SYNTAX_FAILURE_ACTION", "warn")
    assert ValidationSettings(_env_file=None).syntax_failure_action == "warn"
    monkeypatch.setenv("SYNTAX_FAILURE_ACTION", "ignore")
    with pytest.raises(ValueError):
        ValidationSettings(_env_file=None)


def test_block_mode_keeps_broken_file_out(db_session, tmp_path, monkeypatch):
    _setup(monkeypatch, "block")
    report, target = _export(db_session, tmp_path, {"src/a.py": "x = 1\n"}, "syn-block")
    assert not (target / "src" / "a.py").exists()
    assert report.outcomes[0].status == "failed_syntax_validation"


def test_warn_mode_writes_file_with_visible_warning(db_session, tmp_path, monkeypatch):
    _setup(monkeypatch, "warn")
    report, target = _export(db_session, tmp_path, {"src/a.py": "x = 1\n"}, "syn-warn")

    assert (target / "src" / "a.py").read_text(encoding="utf-8") == "x = 1\n"
    assert report.outcomes[0].final_state == "READY"
    assert any("src/a.py" in w and _FAKE_ERROR in w for w in report.validation_warnings)
    assert report.status == "completed_with_warnings"
    assert db_session.query(AuditWarning).filter_by(run_id=report.run_id).count() == 0
    trail = [row.detail or "" for row in db_session.query(AuditLog).filter_by(run_id=report.run_id).all()]
    assert any("syntax_failure_action=warn" in detail for detail in trail)


def test_warn_mode_does_not_bypass_privacy_checks(db_session, tmp_path, monkeypatch):
    async def risky_without_quote(*args, **kwargs):
        return AuditVerdict(risky=True, findings=[])

    _setup(monkeypatch, "warn", audit=risky_without_quote)
    report, target = _export(db_session, tmp_path, {"src/a.py": "x = 1\n"}, "syn-privacy")
    assert not (target / "src" / "a.py").exists()
    assert report.outcomes[0].final_state == "SECURITY_QUARANTINE"


def test_warn_mode_still_sends_broken_auto_remediation_to_review(db_session, tmp_path, monkeypatch):
    # Otomatik duzeltmeden sonra cikan sozdizimi hatasi yanlis bir seyin
    # maskelendigine isaret edebilir: warn modunda bile onaya duser.
    async def risky_operator(masked_text, *args, **kwargs):
        if "x = 1" in masked_text:
            return AuditVerdict(risky=True, findings=[AuditFinding(aciklama="x", ilgili_bolum="= 1")])
        return AuditVerdict(risky=False)

    # validate_masked_syntax burada gercek: `= 1` maskelenince Python bozulur.
    monkeypatch.setattr(exporter.settings.validation, "syntax_failure_action", "warn", raising=False)
    monkeypatch.setattr(exporter, "build_orchestrator", lambda *a, **k: _NoDetections())
    monkeypatch.setattr(exporter, "audit_masked_text", risky_operator)

    report, target = _export(db_session, tmp_path, {"src/c.py": "x = 1\n"}, "syn-remediate")

    assert not (target / "src" / "c.py").exists()
    warning = db_session.query(AuditWarning).filter_by(run_id=report.run_id).one()
    assert "sözdizimi" in warning.reasoning


def test_warn_mode_in_consistency_pass(db_session, tmp_path, monkeypatch):
    from tests.test_consistency_masking import SENSITIVE, _SeedOnlyOrchestrator

    calls = []

    def broken_only_in_consistency(relative_path, masked_text, *args, **kwargs):
        # Ilk turda temiz; tutarlilik adimi Other.java'yi maskeleyince bozuk.
        if relative_path == "Other.java" and "mask_" in masked_text:
            calls.append(relative_path)
            return _FAKE_ERROR
        return None

    async def clean(*args, **kwargs):
        return AuditVerdict(risky=False)

    monkeypatch.setattr(exporter.settings.validation, "syntax_failure_action", "warn", raising=False)
    monkeypatch.setattr(exporter, "build_orchestrator", lambda *a, **k: _SeedOnlyOrchestrator([SENSITIVE]))
    monkeypatch.setattr(exporter, "audit_masked_text", clean)
    monkeypatch.setattr(exporter, "find_leaked_terms", lambda db, text, **kwargs: [])
    monkeypatch.setattr(exporter, "validate_masked_syntax", broken_only_in_consistency)

    report, target = _export(db_session, tmp_path, {
        "Seed.java": f'String s = "{SENSITIVE}";\n',
        "Other.java": f'String o = "{SENSITIVE}";\n',
    }, "syn-consistency")

    assert calls
    output = (target / "Other.java").read_text(encoding="utf-8")
    assert SENSITIVE not in output
    assert any("Other.java" in w and _FAKE_ERROR in w for w in report.validation_warnings)
    assert not report.has_failed_consistency_validation


@pytest.mark.parametrize("action, released", [("block", False), ("warn", True)])
def test_release_from_review_respects_action(db_session, tmp_path, monkeypatch, action, released):
    from app.services import audit_warning_service
    from app.services.review_service import ReviewService
    from tests.test_review_auto_mask import make_file

    monkeypatch.setattr(exporter.settings.validation, "syntax_failure_action", action, raising=False)
    monkeypatch.setattr(audit_warning_service, "validate_masked_syntax", _broken_syntax)
    _run, warning, reviews = make_file(db_session, tmp_path, "SECRET=ApolloPrivate\n")

    result = ReviewService(db_session).mask_file(reviews[0].id)
    assert result["written"] is released
    assert (tmp_path / "output" / ".env").exists() is released
    if released:
        assert "ApolloPrivate" not in (tmp_path / "output" / ".env").read_text()
    else:
        assert "sözdizimi" in warning.reasoning
