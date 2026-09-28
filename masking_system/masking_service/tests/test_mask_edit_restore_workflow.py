"""End-to-end MASK -> USER EDIT -> RESTORE regression.

Restore is NOT a rollback: only placeholders the system itself created may
turn back into real values. Anything the user typed into the masked project
AFTER export - new lines, changed values, new statements - must survive
unmask byte-for-byte, because unmask only ever touches recognized
placeholder tokens (see rule_engine.reverse_text) and never re-derives or
replaces anything else in the file.
"""

from __future__ import annotations

import asyncio
import re

from sqlalchemy import text as sqltext

from app.db.session import SessionLocal
from app.services import exporter as exporter_module
from app.services.detectors import DetectionResult, DetectorOutput, synthetic_llm_rule
from app.services.unmasker import unmask_project

_IDENTITY_PREFIX = "pytest-mask-edit-restore"
SENSITIVE = "NOVA_YETKI_CORE"


class _ValueOnlyOrchestrator:
    """Detect only the exact literal SENSITIVE substring, wherever it sits -
    mirrors a real dictionary/parametric rule hit without needing live DB
    rule content, so the test stays focused on the mask/edit/restore cycle."""

    async def scan(self, text, metadata=None):
        start = text.find(SENSITIVE)
        if start < 0:
            return DetectorOutput()
        hit = DetectionResult(
            deger=SENSITIVE, tip="KURUM", guven_seviyesi="yuksek", kaynak_motor="dictionary",
            start=start, end=start + len(SENSITIVE), rule=synthetic_llm_rule("KURUM"),
        )
        return DetectorOutput(results=[hit])


def _cleanup_identity(project_name: str) -> None:
    with SessionLocal() as db:
        row = db.execute(
            sqltext(
                "SELECT id FROM maskeleme_baglamlari WHERE proje_adi=:p AND personel_no=:pn AND branch_adi=:b"
            ),
            {"p": project_name, "pn": "P-TEST-0001", "b": "pytest-branch"},
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


def _export(source_dir, target_dir, project_name, monkeypatch):
    monkeypatch.setattr(exporter_module, "build_orchestrator", lambda *a, **kw: _ValueOnlyOrchestrator())
    monkeypatch.setattr(exporter_module.settings.vllm, "enabled", False)
    identity = (project_name, "P-TEST-0001", "pytest-branch")
    with SessionLocal() as db:
        report = asyncio.run(
            exporter_module.export_project(
                db, source_path=str(source_dir), project_name=identity[0], sicil_no=identity[1],
                branch_name=identity[2], target_path=str(target_dir), initiated_by=identity[1],
            )
        )
        db.commit()
    return report


def _restore(source_dir, target_dir, project_name):
    identity = (project_name, "P-TEST-0001", "pytest-branch")
    with SessionLocal() as db:
        report = unmask_project(
            db, source_path=str(source_dir), project_name=identity[0], sicil_no=identity[1],
            branch_name=identity[2], target_path=str(target_dir), initiated_by=identity[1],
        )
        db.commit()
    return report


def _run_case(tmp_path, monkeypatch, project, filename, original, after_user_edit, expected_restored):
    """original -mask-> (edit in place, simulating the user) -restore->.

    `after_user_edit` is what the FILE LOOKS LIKE after masking AND the
    user's own edits - it must still contain the placeholder(s) the export
    step produced (the test substitutes them in), plus whatever the user
    changed around them. Uses plain substring templating (not str.format)
    since the JS/TSX fixtures contain literal `{`/`}` from JSX expressions.
    """
    try:
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        (source_dir / filename).write_text(original, encoding="utf-8")
        masked_dir = tmp_path / "masked"
        restored_dir = tmp_path / "restored"

        report = _export(source_dir, masked_dir, project, monkeypatch)
        # Java/Go/etc. have no full parser (bracket/quote-only validation),
        # which is correctly reported as a warning, not a failure - only
        # actual validation/round-trip failures make this NOT "clean".
        assert not report.has_failed_syntax_validation, report.summary_text()
        assert not report.has_failed_round_trip_validation, report.summary_text()
        assert not report.has_quarantined_files, report.summary_text()
        masked_text = (masked_dir / filename).read_text(encoding="utf-8")
        assert SENSITIVE not in masked_text
        placeholder = re.search(r"mask_[a-z][a-z0-9_]*_\d+", masked_text).group(0)

        # Simulate the user editing the MASKED file directly.
        edited = after_user_edit.replace("{placeholder}", placeholder)
        (masked_dir / filename).write_text(edited, encoding="utf-8")

        restore_report = _restore(masked_dir, restored_dir, project)
        restored_text = (restored_dir / filename).read_text(encoding="utf-8")
        assert restored_text == expected_restored.replace("{sensitive}", SENSITIVE)
        assert restore_report.total_placeholders_unresolved == 0, restore_report.summary_text()
        return report, restore_report
    finally:
        _cleanup_identity(project)


def test_mask_edit_restore_java(tmp_path, monkeypatch):
    original = (
        f'String kurum = "{SENSITIVE}";\n'
        "int timeout = 30;\n"
    )
    after_edit = (
        'String kurum = "{placeholder}";\n'
        "int timeout = 60;\n"
        "retryPolicy.enable();\n"
    )
    expected = (
        'String kurum = "{sensitive}";\n'
        "int timeout = 60;\n"
        "retryPolicy.enable();\n"
    )
    _run_case(tmp_path, monkeypatch, f"{_IDENTITY_PREFIX}-java", "App.java", original, after_edit, expected)


def test_mask_edit_restore_python(tmp_path, monkeypatch):
    original = f'kurum = "{SENSITIVE}"\ntimeout = 30\n'
    after_edit = (
        'kurum = "{placeholder}"\n'
        "timeout = 60\n"
        "retry_policy.enable()\n"
        "# yeni bir aciklama satiri\n"
    )
    expected = (
        'kurum = "{sensitive}"\n'
        "timeout = 60\n"
        "retry_policy.enable()\n"
        "# yeni bir aciklama satiri\n"
    )
    _run_case(tmp_path, monkeypatch, f"{_IDENTITY_PREFIX}-python", "app.py", original, after_edit, expected)


def test_mask_edit_restore_javascript(tmp_path, monkeypatch):
    original = f'const kurum = "{SENSITIVE}";\nlet timeout = 30;\n'
    after_edit = (
        'const kurum = "{placeholder}";\n'
        "let timeout = 60;\n"
        "retryPolicy.enable();\n"
    )
    expected = (
        'const kurum = "{sensitive}";\n'
        "let timeout = 60;\n"
        "retryPolicy.enable();\n"
    )
    _run_case(tmp_path, monkeypatch, f"{_IDENTITY_PREFIX}-js", "app.js", original, after_edit, expected)


def test_mask_edit_restore_tsx(tmp_path, monkeypatch):
    original = (
        "export function Badge() {\n"
        f'  const kurum = "{SENSITIVE}";\n'
        "  const timeout = 30;\n"
        "  return <span title={kurum}>{timeout}</span>;\n"
        "}\n"
    )
    after_edit = (
        "export function Badge() {\n"
        '  const kurum = "{placeholder}";\n'
        "  const timeout = 60;\n"
        "  retryPolicy.enable();\n"
        "  return <span title={kurum} data-timeout={timeout}>{timeout}</span>;\n"
        "}\n"
    )
    expected = (
        "export function Badge() {\n"
        '  const kurum = "{sensitive}";\n'
        "  const timeout = 60;\n"
        "  retryPolicy.enable();\n"
        "  return <span title={kurum} data-timeout={timeout}>{timeout}</span>;\n"
        "}\n"
    )
    _run_case(tmp_path, monkeypatch, f"{_IDENTITY_PREFIX}-tsx", "Badge.tsx", original, after_edit, expected)


def test_user_added_lines_with_no_placeholders_survive_restore_untouched(tmp_path, monkeypatch):
    """A file the user creates AFTER masking (no placeholders in it at all)
    must pass through unmask completely unchanged - restore only edits
    recognized placeholder tokens, never rewrites arbitrary file content."""
    project = f"{_IDENTITY_PREFIX}-new-file"
    try:
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        (source_dir / "seed.py").write_text(f'kurum = "{SENSITIVE}"\n', encoding="utf-8")
        masked_dir = tmp_path / "masked"
        restored_dir = tmp_path / "restored"

        _export(source_dir, masked_dir, project, monkeypatch)
        user_new_file = "def helper():\n    return 42\n"
        (masked_dir / "helper.py").write_text(user_new_file, encoding="utf-8")

        _restore(masked_dir, restored_dir, project)
        assert (restored_dir / "helper.py").read_text(encoding="utf-8") == user_new_file
    finally:
        _cleanup_identity(project)
