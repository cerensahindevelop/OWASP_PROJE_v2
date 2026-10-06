"""Kullaniciyi tanimlayan sicildir: ayni kisi farkli proje/branch'lerde
calisir, geri alma yalnizca paketi maskeleyen sicille yapilir ve proje/branch
paketin islem kaydindan okunur."""

import asyncio

import pytest

from app.core.crypto import encrypt_value, hash_value
from app.db.models import MaskingRun, ValueMapping
from app.services import exporter, reporting
from app.services.integrity_manifest import MANIFEST_NAME
from app.services.mapping_service import get_or_create_context
from app.services.term_upload import build_filter_rule
from app.services.unmask_diagnostics import IdentityMismatchAdvisor
from app.services.unmasker import PackageOwnerMismatchError, unmask_project

SOURCE = "url = 'https://zephyrqx.example'\n"


@pytest.fixture
def masked_package(db_session, monkeypatch, tmp_path):
    db_session.add(build_filter_rule(term="Zephyrqx", category="pytest_sicil", status="ok", priority=1))
    db_session.flush()
    monkeypatch.setattr(exporter.settings.vllm, "enabled", False)
    source, target = tmp_path / "source", tmp_path / "masked"
    source.mkdir()
    (source / "app.py").write_text(SOURCE, encoding="utf-8")
    report = asyncio.run(exporter.export_project(
        db_session, source_path=str(source), target_path=str(target),
        project_name="proje-a", sicil_no="SCL-1", branch_name="feature-x", initiated_by="SCL-1",
    ))
    assert report.status == "completed", report.summary_text()
    assert "zephyrqx" not in (target / "app.py").read_text(encoding="utf-8").lower()
    return target, report.run_id


def test_unmask_reads_project_and_branch_from_package(db_session, masked_package, tmp_path):
    target, _job_id = masked_package
    restored = tmp_path / "restored"

    report = unmask_project(
        db_session, source_path=str(target), target_path=str(restored), sicil_no="SCL-1", initiated_by="SCL-1",
    )

    assert report.status == "completed", report.summary_text()
    assert (report.project_name, report.branch_name) == ("proje-a", "feature-x")
    assert (restored / "app.py").read_text(encoding="utf-8") == SOURCE


def test_unmask_rejects_another_sicil_without_naming_the_owner(db_session, masked_package, tmp_path):
    target, _job_id = masked_package
    restored = tmp_path / "restored"

    with pytest.raises(PackageOwnerMismatchError) as error:
        unmask_project(
            db_session, source_path=str(target), target_path=str(restored), sicil_no="SCL-2", initiated_by="SCL-2",
        )

    assert "proje-a" not in str(error.value) and "SCL-1" not in str(error.value)
    assert not restored.exists()


def test_unmask_without_manifest_needs_job_id(db_session, masked_package, tmp_path):
    target, job_id = masked_package
    (target / MANIFEST_NAME).unlink()

    with pytest.raises(PackageOwnerMismatchError, match="JOB ID"):
        unmask_project(
            db_session, source_path=str(target), target_path=str(tmp_path / "r1"), sicil_no="SCL-1", initiated_by="SCL-1",
        )
    report = unmask_project(
        db_session, source_path=str(target), target_path=str(tmp_path / "r2"), sicil_no="SCL-1",
        initiated_by="SCL-1", job_id=job_id,
    )
    assert (tmp_path / "r2" / "app.py").read_text(encoding="utf-8") == SOURCE
    assert report.total_placeholders_unresolved == 0


def test_unmask_rejects_wrong_explicit_project(db_session, masked_package, tmp_path):
    target, _job_id = masked_package
    with pytest.raises(PackageOwnerMismatchError, match="eşleşmiyor"):
        unmask_project(
            db_session, source_path=str(target), target_path=str(tmp_path / "r"), sicil_no="SCL-1",
            initiated_by="SCL-1", project_name="proje-b", branch_name="feature-x",
        )


def _run(db_session, project: str, sicil: str, branch: str) -> MaskingRun:
    context = get_or_create_context(db_session, project, sicil, branch)
    run = MaskingRun(
        context_id=context.id, operation_type="mask", source_path="s", target_path="t",
        initiated_by=sicil, status="completed",
    )
    db_session.add(run)
    db_session.flush()
    return run


def test_one_sicil_lists_all_its_projects_and_branches(db_session):
    first = _run(db_session, "proje-a", "SCL-LIST", "main")
    second = _run(db_session, "proje-a", "SCL-LIST", "develop")
    third = _run(db_session, "proje-b", "SCL-LIST", "main")
    _run(db_session, "proje-a", "SCL-OTHER", "main")

    pairs = reporting.list_project_branches(db_session, "SCL-LIST")
    assert sorted(pairs) == [("proje-a", "develop"), ("proje-a", "main"), ("proje-b", "main")]
    runs = reporting.list_runs(db_session, sicil_no="SCL-LIST", limit=10)
    assert {r.run_id for r in runs} == {first.id, second.id, third.id}
    filtered = reporting.list_runs(db_session, sicil_no="SCL-LIST", project_name="proje-a", limit=10)
    assert {r.run_id for r in filtered} == {first.id, second.id}
    assert reporting.run_project_branch(db_session, [third.id]) == {third.id: ("proje-b", "main")}


def test_identity_suggestion_never_points_to_another_sicil(db_session):
    mine = get_or_create_context(db_session, "proje-a", "SCL-ME", "main")
    others = get_or_create_context(db_session, "proje-gizli", "SCL-YOU", "main")
    tokens = [f"mask_pytest_sicil_{i}" for i in range(30)]
    for token in tokens:
        db_session.add(ValueMapping(
            context_id=others.id, run_id=None, rule_id=None,
            original_value_encrypted=encrypt_value(token), original_value_plain=token,
            original_value_hash=hash_value(others.id, token), placeholder_value=token,
        ))
    db_session.flush()

    suggestion = IdentityMismatchAdvisor(db_session).suggest(
        current_context_id=mine.id, unresolved_tokens=tokens, total_found=len(tokens),
    )
    assert suggestion is None
