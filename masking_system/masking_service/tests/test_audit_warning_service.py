"""AuditWarningService icin karakterizasyon testleri - refactor oncesi
guvenlik agi (Asama 2 / Adim 0).

AuditWarningService.dismiss(), Asama 1 analizinde bulunan EN riskli is
mantigi: karantinadaki maskelenmis icerigi GERCEK hedef klasore yaziyor
(bkz. audit_warning_service.py:_release_to_target). Bu dosya, herhangi bir
refactor'a dokunulmadan once bu davranisi sabitler.
"""

from __future__ import annotations

import asyncio
import pytest

from app.core.exceptions import ReviewAlreadyProcessedError
from app.db.models import AuditWarning, MaskingContext, MaskingRun
from app.services.audit_warning_service import AuditWarningService


def _make_context(db_session, suffix: str) -> MaskingContext:
    context = MaskingContext(
        project_name=f"pytest-auditwarn-{suffix}",
        sicil_no="P-AUDIT-0001",
        branch_name="pytest-branch",
    )
    db_session.add(context)
    db_session.flush()
    return context


def _make_run(db_session, context: MaskingContext, target_path: str) -> MaskingRun:
    run = MaskingRun(
        context_id=context.id,
        operation_type="mask",
        source_path="/tmp/audit-src",
        target_path=target_path,
        initiated_by="P-AUDIT-0001",
        status="completed_with_warnings",
    )
    db_session.add(run)
    db_session.flush()
    return run


def _make_warning(db_session, run: MaskingRun, **overrides) -> AuditWarning:
    defaults = dict(
        run_id=run.id,
        file_path="src/config.py",
        masked_content="SECRET = safe_value\n",
        encoding="utf-8",
        reasoning="Model olası risk bildirdi (ilgili bolum: 'safe_value')",
        audit_failed=False,
    )
    defaults.update(overrides)
    warning = AuditWarning(**defaults)
    db_session.add(warning)
    db_session.flush()
    return warning


def test_confirm_marks_confirmed_and_never_writes_to_target(db_session, tmp_path):
    context = _make_context(db_session, "confirm")
    run = _make_run(db_session, context, target_path=str(tmp_path))
    warning = _make_warning(db_session, run)

    svc = AuditWarningService(db_session)
    confirmed = svc.confirm(warning.id)

    assert confirmed.status == "confirmed"
    assert not (tmp_path / "src" / "config.py").exists()


def test_dismiss_marks_dismissed_and_releases_content_to_target(db_session, tmp_path):
    context = _make_context(db_session, "dismiss")
    run = _make_run(db_session, context, target_path=str(tmp_path))
    warning = _make_warning(db_session, run)

    svc = AuditWarningService(db_session)
    dismissed = asyncio.run(svc.dismiss(warning.id))

    assert dismissed.status == "dismissed"
    released_path = tmp_path / "src" / "config.py"
    assert released_path.exists()
    assert released_path.read_text(encoding="utf-8") == "SECRET = safe_value\n"


def test_dismiss_recreates_missing_target_directory(db_session, tmp_path):
    """Modul dokstring'i, karar verilene kadar run'in hedef klasorunun
    silinmis/tasinmis olabilecegini not ediyor - dismiss() hata vermek yerine
    dizini yeniden olusturmali."""
    target_dir = tmp_path / "already-gone"
    context = _make_context(db_session, "dismiss-recreate")
    run = _make_run(db_session, context, target_path=str(target_dir))
    warning = _make_warning(db_session, run, file_path="nested/dir/file.py")

    svc = AuditWarningService(db_session)
    asyncio.run(svc.dismiss(warning.id))

    assert (target_dir / "nested" / "dir" / "file.py").exists()


def test_dismiss_with_missing_target_path_never_marks_released(db_session):
    context = _make_context(db_session, "orphan")
    run = _make_run(db_session, context, target_path="")

    warning = _make_warning(db_session, run)

    svc = AuditWarningService(db_session)
    with pytest.raises(ValueError):
        asyncio.run(svc.dismiss(warning.id))
    assert warning.status == "pending"


def test_second_decision_on_same_warning_raises(db_session, tmp_path):
    context = _make_context(db_session, "double-decision")
    run = _make_run(db_session, context, target_path=str(tmp_path))
    warning = _make_warning(db_session, run)

    svc = AuditWarningService(db_session)
    svc.confirm(warning.id)

    with pytest.raises(ReviewAlreadyProcessedError):
        asyncio.run(svc.dismiss(warning.id))


def test_validation_failure_cannot_be_overridden_by_user(db_session, tmp_path):
    context = _make_context(db_session, "technical")
    run = _make_run(db_session, context, target_path=str(tmp_path))
    warning = _make_warning(db_session, run, audit_failed=True)
    with pytest.raises(ValueError, match="kullanıcı kararıyla geçilemez"):
        asyncio.run(AuditWarningService(db_session).dismiss(warning.id))
    assert not (tmp_path / warning.file_path).exists()
