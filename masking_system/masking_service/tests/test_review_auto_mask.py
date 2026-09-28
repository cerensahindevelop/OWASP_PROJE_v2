"""Human-triggered automatic masking must retain files, mappings and exact restore."""
import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.crypto import decrypt_value
from app.core.exceptions import ReviewAlreadyProcessedError
from app.db.models import AuditWarning, MaskingContext, MaskingRun, ReviewQueue, ValueMapping
from app.services.audit_reviewer import AuditVerdict
from app.services.audit_warning_service import AuditWarningService
from app.services.review_service import ReviewService
from app.services.rule_engine import reverse_text


def make_file(db, tmp_path, text, *, audit=False, file_path=".env", values=("ApolloPrivate", "OrionPrivate")):
    context = MaskingContext(project_name="pytest-auto-mask", sicil_no="T", branch_name="test")
    db.add(context)
    db.flush()
    run = MaskingRun(context_id=context.id, operation_type="mask", source_path=str(tmp_path / "source"),
                     target_path=str(tmp_path / "output"), initiated_by="T", status="completed_with_warnings",
                     mapping_version=2)
    db.add(run)
    db.flush()
    reason = " | ".join(f"Risk (ilgili bolum: '{value}')" for value in values) if audit else "INCELEME_GEREKLI: risk"
    warning = AuditWarning(run_id=run.id, file_path=file_path, masked_content=text,
                           encoding="utf-8", reasoning=reason, audit_failed=False)
    db.add(warning)
    reviews = []
    if not audit:
        for value in values:
            item = ReviewQueue(run_id=run.id, file_path=file_path, found_value=value,
                               entity_type="SECRET", confidence_level="orta", reason="risk")
            db.add(item)
            reviews.append(item)
    db.flush()
    return run, warning, reviews


def mapping_values(db, run):
    return {m.placeholder_value: decrypt_value(m.original_value_encrypted) for m in db.scalars(
        select(ValueMapping).where(ValueMapping.run_id == run.id)).all()}


def test_auto_mask_file_preserves_env_format_and_saves_restore_mappings(db_session, tmp_path):
    text = 'SECRET="ApolloPrivate"\r\nTEAM=OrionPrivate\r\nPORT=8080\r\n'
    run, warning, reviews = make_file(db_session, tmp_path, text)
    result = ReviewService(db_session).mask_file(reviews[0].id)
    assert result["written"]
    assert warning.status == "dismissed"
    output = (tmp_path / "output" / ".env").read_bytes().decode()
    assert "ApolloPrivate" not in output and "OrionPrivate" not in output
    assert 'SECRET="mask_secret_' in output and 'TEAM=mask_secret_' in output
    assert "PORT=8080\r\n" in output
    assert reverse_text(output, mapping_values(db_session, run))[0] == text
    assert all(review.status == "approved" for review in reviews)
    with pytest.raises(ReviewAlreadyProcessedError):
        ReviewService(db_session).mask_file(reviews[0].id)


def test_automatic_edit_does_not_approve_other_files(db_session, tmp_path):
    run, warning, reviews = make_file(db_session, tmp_path, "ApolloPrivate OrionPrivate")
    other = ReviewQueue(run_id=run.id, file_path="other.env", found_value="OtherPrivate",
                        entity_type="SECRET", confidence_level="orta")
    db_session.add(other)
    db_session.flush()
    ReviewService(db_session).mask_file(reviews[0].id)
    assert other.status == "pending"
    assert not (tmp_path / "output" / "other.env").exists()


def test_approved_numeric_json_uses_numeric_mapping(db_session, tmp_path):
    import json
    text = '{"id": 987654321}'
    run, warning, reviews = make_file(db_session, tmp_path, text, file_path="config.json", values=("987654321",))
    assert ReviewService(db_session).mask_file(reviews[0].id)["written"]
    output = (tmp_path / "output" / "config.json").read_text()
    assert isinstance(json.loads(output)["id"], int)
    assert reverse_text(output, mapping_values(db_session, run))[0] == text


def test_audit_auto_masks_all_evidence_including_beyond_ui_limit(db_session, tmp_path):
    values = tuple(f"PrivateValue{i}" for i in range(8))
    text = "\n".join(f"KEY{i}={value}" for i, value in enumerate(values))
    run, warning, _ = make_file(db_session, tmp_path, text, audit=True, values=values)
    asyncio.run(AuditWarningService(db_session).mask(warning.id))
    output = (tmp_path / "output" / ".env").read_text()
    assert all(value not in output for value in values)
    assert len(mapping_values(db_session, run)) == 8
    assert reverse_text(output, mapping_values(db_session, run))[0] == text


def test_audit_failure_rolls_back_masking_and_keeps_file_pending(db_session, tmp_path, monkeypatch):
    run, warning, _ = make_file(db_session, tmp_path, "SECRET=ApolloPrivate", audit=True, values=("ApolloPrivate",))
    async def failed(*args):
        return AuditVerdict(risky=True, findings=[])
    monkeypatch.setattr("app.services.audit_warning_service.audit_masked_text", failed)
    with pytest.raises(ValueError, match="çıktıya eklenmedi"):
        asyncio.run(AuditWarningService(db_session).mask(warning.id))
    assert warning.status == "pending"
    assert warning.masked_content == "SECRET=ApolloPrivate"
    assert not mapping_values(db_session, run)
    assert not (tmp_path / "output" / ".env").exists()


@pytest.mark.parametrize("kind", ["technical", "unlocated", "lock"])
def test_unverifiable_audit_file_cannot_be_auto_released(db_session, tmp_path, kind):
    run, warning, _ = make_file(db_session, tmp_path, "SECRET=ApolloPrivate", audit=True, values=("ApolloPrivate",),
                               file_path="yarn.lock" if kind == "lock" else ".env")
    if kind == "technical":
        warning.audit_failed = True
    if kind == "unlocated":
        warning.reasoning = "Risk var ama ifade yok"
    with pytest.raises(ValueError):
        asyncio.run(AuditWarningService(db_session).mask(warning.id))
    assert warning.status == "pending" and not mapping_values(db_session, run)
    assert not (tmp_path / "output" / warning.file_path).exists()


def test_overlapping_values_and_existing_placeholders_restore_exactly(db_session, tmp_path):
    from app.services.detectors import synthetic_llm_rule
    from app.services.mapping_service import get_or_create_mapping
    from app.services.review_masking import mask_review_values
    run, _, _ = make_file(db_session, tmp_path, "unused")
    mapping, _ = get_or_create_mapping(db_session, run.context_id, synthetic_llm_rule("SECRET"), "PreviousPrivate", run_id=run.id)
    content = f'OLD={mapping.placeholder_value}\nNEW=ApolloPrivate'
    output = mask_review_values(db_session, run, content, ".env", [("ApolloPrivate", "SECRET"), ("Private", "SECRET"), ("secret", "SECRET")])
    assert mapping.placeholder_value in output
    assert reverse_text(output, mapping_values(db_session, run))[0] == 'OLD=PreviousPrivate\nNEW=ApolloPrivate'


def test_review_action_discards_stale_zip_and_fetches_fresh_output(monkeypatch):
    from app.webapp import review_page, export_page
    result = {"report": SimpleNamespace(run_id=42), "download_bytes": b"old zip"}
    monkeypatch.setattr(review_page.st, "session_state", {"export_last_result": result})
    monkeypatch.setattr(review_page.api_client, "approve_review", lambda _id: None)
    monkeypatch.setattr(export_page.api_client, "download_run_output", lambda run_id: b"new zip with masked file")
    assert review_page._apply_action("approve", 1) == ("ok", "")
    assert result["download_bytes"] is None
    export_page._refresh_review_download(result)
    assert result["download_bytes"] == b"new zip with masked file"
    assert not result["download_needs_refresh"]


@pytest.mark.parametrize("line", ['SECRET=ApolloPrivate', 'SECRET="ApolloPrivate"'])
def test_env_audit_citing_whole_assignment_keeps_key(db_session, tmp_path, line):
    run, warning, _ = make_file(db_session, tmp_path, line + '\nPORT=8080\n', audit=True, values=(line,))
    asyncio.run(AuditWarningService(db_session).mask(warning.id))
    output = (tmp_path / "output" / ".env").read_text()
    assert output.startswith('SECRET=') and 'PORT=8080\n' in output
    assert "ApolloPrivate" not in output
    assert reverse_text(output, mapping_values(db_session, run))[0] == line + '\nPORT=8080\n'


def test_api_automatic_mask_returns_written_and_download_contains_file(db_session, tmp_path):
    import io
    import zipfile
    from fastapi.testclient import TestClient
    from app.api.main import app
    from app.api.deps import get_request_db

    run, warning, reviews = make_file(db_session, tmp_path, 'SECRET=ApolloPrivate\nTEAM=OrionPrivate')
    def override():
        yield db_session
    app.dependency_overrides[get_request_db] = override
    try:
        with TestClient(app) as client:
            response = client.post(f'/reviews/{reviews[0].id}/mask-file')
            assert response.status_code == 200, response.text
            assert response.json()['written'] is True
            download = client.get(f'/runs/{run.id}/download')
            assert download.status_code == 200
            with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
                output = archive.read('.env').decode()
                assert 'ApolloPrivate' not in output
                assert reverse_text(output, mapping_values(db_session, run))[0] == 'SECRET=ApolloPrivate\nTEAM=OrionPrivate'
    finally:
        app.dependency_overrides.pop(get_request_db, None)


def test_download_refresh_failure_never_reuses_old_zip(monkeypatch):
    from app.webapp import export_page
    from app.webapp.api_client import ApiError
    result = {"report": SimpleNamespace(run_id=42), "download_bytes": b"old zip", "download_needs_refresh": True}
    def fail(_id):
        raise ApiError("Bağlantı yok", None, 503)
    monkeypatch.setattr(export_page.api_client, "download_run_output", fail)
    monkeypatch.setattr(export_page, "show_error", lambda exc: None)
    export_page._refresh_review_download(result)
    assert result["download_bytes"] is None and result["download_needs_refresh"]
