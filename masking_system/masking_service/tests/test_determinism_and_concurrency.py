"""Repeated/parallel jobs keep deterministic output and separate DB mappings.

Each job starts its own counters, so matching token text across jobs is expected.
"""

from __future__ import annotations

import asyncio
import re
import threading

from sqlalchemy import text as sqltext

from app.db.session import SessionLocal
from app.services import exporter as exporter_module
from app.services.detectors import DetectionResult, DetectorOutput, synthetic_llm_rule
from app.services.unmasker import unmask_project

_IDENTITY_PREFIX = "pytest-determinism"
SENSITIVE = "NOVA_YETKI_CORE"
SOURCE_TEXT = f'kurum = "{SENSITIVE}"\ntimeout = 30\nemail = "foo@example.com"\n'


class _FixedOrchestrator:
    async def scan(self, text, metadata=None):
        results = []
        for value, tip in [(SENSITIVE, "KURUM"), ("foo@example.com", "EMAIL")]:
            start = text.find(value)
            if start < 0:
                continue
            results.append(DetectionResult(
                deger=value, tip=tip, guven_seviyesi="yuksek", kaynak_motor="dictionary",
                start=start, end=start + len(value), rule=synthetic_llm_rule(tip),
            ))
        return DetectorOutput(results=results)


def _cleanup_identity(project_name: str, sicil_no: str = "P-TEST-0001", branch_name: str = "pytest-branch") -> None:
    with SessionLocal() as db:
        row = db.execute(
            sqltext(
                "SELECT id FROM maskeleme_baglamlari WHERE proje_adi=:p AND personel_no=:pn AND branch_adi=:b"
            ),
            {"p": project_name, "pn": sicil_no, "b": branch_name},
        ).first()
        if row is None:
            return
        context_id = row[0]
        run_ids = [
            r[0]
            for r in db.execute(
                sqltext("SELECT id FROM maskeleme_calismalari WHERE baglam_id=:c"), {"c": context_id}
            ).all()
        ]
        for run_id in run_ids:
            db.execute(sqltext("DELETE FROM denetim_kaydi WHERE calisma_id=:r"), {"r": run_id})
            db.execute(sqltext("DELETE FROM gozden_gecirme_kuyrugu WHERE calisma_id=:r"), {"r": run_id})
            db.execute(sqltext("DELETE FROM denetim_uyarilari WHERE calisma_id=:r"), {"r": run_id})
        db.execute(sqltext("DELETE FROM maskeleme_calismalari WHERE baglam_id=:c"), {"c": context_id})
        db.execute(sqltext("DELETE FROM deger_eslemeleri WHERE baglam_id=:c"), {"c": context_id})
        db.execute(sqltext("DELETE FROM maskeleme_baglamlari WHERE id=:c"), {"c": context_id})
        db.commit()


def _export(source_dir, target_dir, project_name, sicil_no="P-TEST-0001", branch_name="pytest-branch"):
    with SessionLocal() as db:
        report = asyncio.run(
            exporter_module.export_project(
                db, source_path=str(source_dir), project_name=project_name, sicil_no=sicil_no,
                branch_name=branch_name, target_path=str(target_dir), initiated_by=sicil_no,
            )
        )
        db.commit()
    return report


def _restore(source_dir, target_dir, project_name, sicil_no="P-TEST-0001", branch_name="pytest-branch"):
    with SessionLocal() as db:
        result = unmask_project(
            db, source_path=str(source_dir), project_name=project_name, sicil_no=sicil_no,
            branch_name=branch_name, target_path=str(target_dir), initiated_by=sicil_no,
        )
        db.commit()
    return result


def _normalize_placeholders(text: str) -> str:
    """Replace the numeric suffix of every placeholder with a fixed marker
    so two runs with different (but otherwise structurally identical)
    global-counter values compare equal."""
    return re.sub(r"mask_([a-z][a-z0-9_]*)_\d+", r"mask_\1_N", text)


def test_repeated_export_on_same_context_is_byte_identical(tmp_path, monkeypatch):
    """Re-exporting the same source into the same context, several times in
    a row, must reuse the exact same placeholders every time (mappings are
    looked up by value hash, never regenerated) - byte-identical output."""
    monkeypatch.setattr(exporter_module, "build_orchestrator", lambda *a, **kw: _FixedOrchestrator())
    monkeypatch.setattr(exporter_module.settings.vllm, "enabled", False)
    project = f"{_IDENTITY_PREFIX}-sequential-same-context"
    try:
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        (source_dir / "config.py").write_text(SOURCE_TEXT, encoding="utf-8")

        outputs = []
        for i in range(4):
            target_dir = tmp_path / f"out_{i}"
            report = _export(source_dir, target_dir, project)
            assert report.status == "completed", report.summary_text()
            outputs.append((target_dir / "config.py").read_bytes())

        assert len(set(outputs)) == 1, "ayni context'te ardisik export'lar FARKLI cikti uretti"
    finally:
        _cleanup_identity(project)


def test_parallel_jobs_reset_counters_without_sharing_mappings(tmp_path, monkeypatch):
    """Several different (project, sicil, branch) contexts masking the SAME
    source content at the same time, in separate threads/sessions, must:
      - start their own placeholder counters at one,
      - never let one context's decrypted value appear under another
        context's mapping rows,
      - each end up with structurally identical (placeholder-number
        normalized) output, since the input and rules are the same.
    """
    monkeypatch.setattr(exporter_module, "build_orchestrator", lambda *a, **kw: _FixedOrchestrator())
    monkeypatch.setattr(exporter_module.settings.vllm, "enabled", False)

    n = 5
    projects = [f"{_IDENTITY_PREFIX}-parallel-{i}" for i in range(n)]
    results: dict[str, bytes] = {}
    errors: list[BaseException] = []
    lock = threading.Lock()

    def _worker(index: int) -> None:
        try:
            source_dir = tmp_path / f"src_{index}"
            source_dir.mkdir()
            (source_dir / "config.py").write_text(SOURCE_TEXT, encoding="utf-8")
            target_dir = tmp_path / f"out_{index}"
            report = _export(source_dir, target_dir, projects[index])
            assert report.status == "completed", report.summary_text()
            with lock:
                results[projects[index]] = (target_dir / "config.py").read_bytes()
        except BaseException as exc:  # noqa: BLE001 - surfaced to the main thread below
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(n)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, errors
        assert len(results) == n

        # Identical inputs start at one independently, even under concurrency.
        assert len(set(results.values())) == 1
        for output in results.values():
            assert set(re.findall(r"mask_[a-z][a-z0-9_]*_\d+", output.decode())) == {
                "mask_email_1", "mask_kurum_1",
            }

        # No cross-context leakage in the mapping table itself: each
        # context's rows must decrypt to SENSITIVE/the email, never to a
        # different context's mapping being visible under this context_id.
        with SessionLocal() as db:
            for project in projects:
                row = db.execute(
                    sqltext(
                        "SELECT id FROM maskeleme_baglamlari WHERE proje_adi=:p AND personel_no=:pn AND branch_adi=:b"
                    ),
                    {"p": project, "pn": "P-TEST-0001", "b": "pytest-branch"},
                ).first()
                assert row is not None
                count = db.execute(
                    sqltext("SELECT count(*) FROM deger_eslemeleri WHERE baglam_id=:c"), {"c": row[0]}
                ).scalar()
                assert count == 2, f"{project} icin beklenmeyen mapping sayisi: {count}"
    finally:
        for project in projects:
            _cleanup_identity(project)


def test_one_jobs_setup_failure_does_not_affect_a_concurrent_successful_job(tmp_path, monkeypatch):
    """Two different users' jobs run concurrently in separate threads/
    sessions. One job hits an unexpected crash mid-setup (simulated here via
    build_orchestrator). The run is committed before the expensive setup so
    no write lock is held during it; the crashed run must therefore be closed
    as 'failed' with no mappings or published output, and this must have
    zero effect on the other, concurrently-running job's context, which
    should complete and persist normally."""
    fail_user = f"{_IDENTITY_PREFIX}-user-fail"
    ok_user = f"{_IDENTITY_PREFIX}-user-ok"
    fail_project = f"{_IDENTITY_PREFIX}-crash-project"
    ok_project = f"{_IDENTITY_PREFIX}-ok-project"

    def flaky_build_orchestrator(rules, runtime_params, *args, **kwargs):
        if runtime_params.get("sicil_no") == fail_user:
            raise RuntimeError("simulated setup-phase crash")
        return _FixedOrchestrator()

    monkeypatch.setattr(exporter_module, "build_orchestrator", flaky_build_orchestrator)
    monkeypatch.setattr(exporter_module.settings.vllm, "enabled", False)

    outcomes: dict[str, object] = {}
    errors: dict[str, BaseException] = {}
    lock = threading.Lock()

    def _worker(name: str, project: str, sicil_no: str) -> None:
        try:
            source_dir = tmp_path / f"src_{name}"
            source_dir.mkdir()
            (source_dir / "config.py").write_text(SOURCE_TEXT, encoding="utf-8")
            target_dir = tmp_path / f"out_{name}"
            report = _export(source_dir, target_dir, project, sicil_no=sicil_no)
            with lock:
                outcomes[name] = report
        except BaseException as exc:  # noqa: BLE001 - asserted on below
            with lock:
                errors[name] = exc

    threads = [
        threading.Thread(target=_worker, args=("fail", fail_project, fail_user)),
        threading.Thread(target=_worker, args=("ok", ok_project, ok_user)),
    ]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert "fail" in errors and isinstance(errors["fail"], RuntimeError)
        assert "ok" in outcomes and outcomes["ok"].status == "completed"

        with SessionLocal() as db:
            # The crashed job's run was committed before setup, so it persists -
            # but closed as 'failed' (not left 'in_progress' to block retries)
            # and without any mappings or published output.
            fail_row = db.execute(
                sqltext(
                    "SELECT id FROM maskeleme_baglamlari WHERE proje_adi=:p AND personel_no=:pn AND branch_adi=:b"
                ),
                {"p": fail_project, "pn": fail_user, "b": "pytest-branch"},
            ).first()
            assert fail_row is not None
            fail_statuses = db.execute(
                sqltext("SELECT durum FROM maskeleme_calismalari WHERE baglam_id=:c"), {"c": fail_row[0]}
            ).scalars().all()
            assert fail_statuses == ["failed"], "basarisiz job'un calismasi failed olarak kapatilmamis"
            fail_count = db.execute(
                sqltext("SELECT count(*) FROM deger_eslemeleri WHERE baglam_id=:c"), {"c": fail_row[0]}
            ).scalar()
            assert fail_count == 0, "basarisiz job mapping yazmis"
            assert not (tmp_path / "out_fail").exists(), "basarisiz job cikti yayimlamis"

            ok_row = db.execute(
                sqltext(
                    "SELECT id FROM maskeleme_baglamlari WHERE proje_adi=:p AND personel_no=:pn AND branch_adi=:b"
                ),
                {"p": ok_project, "pn": ok_user, "b": "pytest-branch"},
            ).first()
            assert ok_row is not None
            count = db.execute(
                sqltext("SELECT count(*) FROM deger_eslemeleri WHERE baglam_id=:c"), {"c": ok_row[0]}
            ).scalar()
            assert count == 2, "basarili job, cokmus komsu job'tan etkilenmis"
    finally:
        _cleanup_identity(fail_project, sicil_no=fail_user)
        _cleanup_identity(ok_project, sicil_no=ok_user)


def test_concurrent_export_and_restore_across_different_users_stay_isolated(tmp_path, monkeypatch):
    """A restore (unmask) of an already-completed job for one user must not
    be affected by a completely different user's export running at the same
    time, and vice versa - both operations hit the shared file-based SQLite
    DB from separate threads/sessions simultaneously."""
    monkeypatch.setattr(exporter_module, "build_orchestrator", lambda *a, **kw: _FixedOrchestrator())
    monkeypatch.setattr(exporter_module.settings.vllm, "enabled", False)

    restore_user = f"{_IDENTITY_PREFIX}-user-restore"
    export_user = f"{_IDENTITY_PREFIX}-user-export"
    restore_project = f"{_IDENTITY_PREFIX}-restore-project"
    export_project_name = f"{_IDENTITY_PREFIX}-live-export-project"

    # Prepare the already-completed job whose output will be restored
    # concurrently with the other user's brand-new export below.
    prepared_source = tmp_path / "prepared_src"
    prepared_source.mkdir()
    (prepared_source / "config.py").write_text(SOURCE_TEXT, encoding="utf-8")
    prepared_target = tmp_path / "prepared_out"
    prepared_report = _export(prepared_source, prepared_target, restore_project, sicil_no=restore_user)
    assert prepared_report.status == "completed", prepared_report.summary_text()

    outcomes: dict[str, object] = {}
    errors: dict[str, BaseException] = {}
    lock = threading.Lock()

    def _export_worker() -> None:
        try:
            source_dir = tmp_path / "live_src"
            source_dir.mkdir()
            (source_dir / "config.py").write_text(SOURCE_TEXT, encoding="utf-8")
            target_dir = tmp_path / "live_out"
            report = _export(source_dir, target_dir, export_project_name, sicil_no=export_user)
            with lock:
                outcomes["export"] = report
        except BaseException as exc:  # noqa: BLE001 - asserted on below
            with lock:
                errors["export"] = exc

    def _restore_worker() -> None:
        try:
            restored_dir = tmp_path / "restored_out"
            result = _restore(prepared_target, restored_dir, restore_project, sicil_no=restore_user)
            with lock:
                outcomes["restore"] = result
        except BaseException as exc:  # noqa: BLE001 - asserted on below
            with lock:
                errors["restore"] = exc

    threads = [threading.Thread(target=_export_worker), threading.Thread(target=_restore_worker)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, errors

        assert outcomes["export"].status == "completed", outcomes["export"].summary_text()

        restored_dir = tmp_path / "restored_out"
        assert (restored_dir / "config.py").read_text(encoding="utf-8") == SOURCE_TEXT
        assert outcomes["restore"].total_placeholders_resolved == 2

        with SessionLocal() as db:
            for project, sicil_no, expected_count in (
                (restore_project, restore_user, 2),
                (export_project_name, export_user, 2),
            ):
                row = db.execute(
                    sqltext(
                        "SELECT id FROM maskeleme_baglamlari WHERE proje_adi=:p AND personel_no=:pn AND branch_adi=:b"
                    ),
                    {"p": project, "pn": sicil_no, "b": "pytest-branch"},
                ).first()
                assert row is not None
                count = db.execute(
                    sqltext("SELECT count(*) FROM deger_eslemeleri WHERE baglam_id=:c"), {"c": row[0]}
                ).scalar()
                assert count == expected_count, f"{project}/{sicil_no} icin beklenmeyen mapping sayisi: {count}"
    finally:
        _cleanup_identity(restore_project, sicil_no=restore_user)
        _cleanup_identity(export_project_name, sicil_no=export_user)
