from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.output_publication import OutputPublication, publish_run
from app.services.output_publication import recover_output
from app.services.unmasker import unmask_project
from app.services.mapping_service import get_or_create_context


def test_staging_and_second_writer_leave_existing_tree_untouched(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "old").write_bytes(b"old output")
    publication = OutputPublication(target, 1)
    try:
        (publication.stage / "new").write_bytes(b"new output")
        with pytest.raises(ValueError, match="kilitli"):
            OutputPublication(target, 2)
        assert (target / "old").read_bytes() == b"old output"
        assert not (target / "new").exists()
    finally:
        publication.close()


def test_failed_promotion_restores_previous_tree(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.mkdir()
    (target / "old").write_text("old")
    publication = OutputPublication(target, 1)
    (publication.stage / "new").write_text("new")
    rename = Path.rename
    def fail_stage(path, destination):
        if path == publication.stage:
            raise OSError("simulated rename failure")
        return rename(path, destination)
    monkeypatch.setattr(Path, "rename", fail_stage)
    try:
        with pytest.raises(OSError):
            publication.promote()
        publication.restore()
        assert (target / "old").read_text() == "old"
    finally:
        publication.close()


@pytest.mark.parametrize("failing_commit", [1, 2])
def test_commit_failures_never_destroy_previous_output(tmp_path, failing_commit):
    target = tmp_path / "target"
    target.mkdir()
    (target / "old").write_text("old")
    publication = OutputPublication(target, 1)
    (publication.stage / "new").write_text("new")
    class DB:
        calls = 0
        def commit(self):
            self.calls += 1
            if self.calls == failing_commit:
                raise RuntimeError("simulated commit failure")
        def rollback(self): pass
        def add(self, value): pass
    try:
        with pytest.raises(RuntimeError):
            publish_run(DB(), SimpleNamespace(id=1), SimpleNamespace(status="completed"), publication)
        assert (target / "old").read_text() == "old"
        assert not (target / "new").exists()
    finally:
        publication.close()


def test_late_unmask_failure_keeps_old_target(db_session, tmp_path):
    identity = dict(project_name="publication", sicil_no="test", branch_name="main")
    get_or_create_context(db_session, *identity.values())
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir(); target.mkdir()
    (source / "new.txt").write_text("new")
    (target / "old.txt").write_text("old")
    def fail_after_first_file(*args):
        raise RuntimeError("late failure")
    with pytest.raises(RuntimeError):
        unmask_project(db_session, source_path=str(source), target_path=str(target),
                       initiated_by="test", progress_callback=fail_after_first_file, **identity)
    assert (target / "old.txt").read_text() == "old"
    assert not (target / "new.txt").exists()
    assert not list(tmp_path.glob(".target.masking*"))


@pytest.mark.parametrize("phase", ["staged", "old_moved", "promoted"])
def test_offline_recovery_at_each_rename_boundary(db_session, tmp_path, phase):
    target = tmp_path / "target"
    target.mkdir()
    (target / "old").write_text("old")
    publication = OutputPublication(target, 9999999)
    (publication.stage / "new").write_text("new")
    if phase == "old_moved":
        target.rename(publication.backup)
    elif phase == "promoted":
        publication.promote()
    with pytest.raises(ValueError, match="application-stopped"):
        recover_output(db_session, target)
    recover_output(db_session, target, application_stopped=True)
    assert (target / "old").read_text() == "old"
    assert not (target / "new").exists()
    assert not publication.lock.exists()


def test_io_errors_cannot_replace_an_existing_output(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "old").write_text("old")
    publication = OutputPublication(target, 1)
    try:
        with pytest.raises(OSError, match="eksik ciktiyla"):
            publish_run(None, None, SimpleNamespace(status="completed_with_warnings", files_errored=1), publication)
        assert (target / "old").read_text() == "old"
    finally:
        publication.close()


def test_mapping_commit_precedes_visibility_in_another_connection(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session
    from app.db.models import Base, MaskingContext, MaskingRun, ValueMapping
    engine = create_engine(f"sqlite:///{tmp_path / 'durability.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        context = MaskingContext(project_name="durable", sicil_no="test", branch_name="main")
        db.add(context)
        db.flush()
        db.add(ValueMapping(context_id=context.id, original_value_encrypted="test-cipher",
                           original_value_plain="test-value", original_value_hash="test-hash", placeholder_value="mask_test_1"))
        run = MaskingRun(context_id=context.id, operation_type="mask", source_path="source",
                         target_path=str(tmp_path / "target"), initiated_by="test", status="in_progress")
        db.add(run)
        db.flush()
        publication = OutputPublication(tmp_path / "target", run.id)
        (publication.stage / "file").write_text("mask_test_1")
        promote = publication.promote
        def observe_then_promote():
            with Session(engine) as observer:
                assert observer.scalar(select(ValueMapping.placeholder_value)) == "mask_test_1"
                assert observer.get(MaskingRun, run.id).status == "in_progress"
            assert not publication.target.exists()
            promote()
        monkeypatch.setattr(publication, "promote", observe_then_promote)
        try:
            publish_run(db, run, SimpleNamespace(status="completed"), publication)
            assert (publication.target / "file").read_text() == "mask_test_1"
        finally:
            publication.close()
    engine.dispose()
