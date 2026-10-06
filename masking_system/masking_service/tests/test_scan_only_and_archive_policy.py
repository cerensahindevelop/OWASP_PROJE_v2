"""Regression suite for the SCAN_ONLY / ARCHIVE / BINARY_UNSCANNED file
policy (bkz. app/services/file_classifier.py, file_pipeline.py, exporter.py).

Core invariant under test everywhere in this file: "Taranamadı ≠ Temiz" -
a lock file, archive or binary/office document must never be reported as
"clean"/"masked", and must never silently leak into the masked output
directory without either being verified byte-identical (SCAN_ONLY clean)
or explicitly quarantined for human review.
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest
from sqlalchemy import select

from app.api.routers.export import _pending_and_quarantined_counts
from app.db.models import AuditWarning
from app.services import exporter
from app.services.detectors import DetectionResult, DetectorOutput
from app.services.integrity_manifest import read_manifest
from app.services.rule_engine import RuleSpec

_CLEAN_STATUSES = {"masked", "copied_text_no_match", "scan_only_clean"}


def _rule(name: str = "pytest_secret_rule") -> RuleSpec:
    return RuleSpec(
        id=1, rule_name=name, category="secret", pattern_type="regex",
        regex_pattern=None, regex_flags=None, placeholder_prefix="SECRET", priority=1,
    )


@pytest.fixture()
def export_files(tmp_path, db_session, monkeypatch):
    """Same shape as tests/test_unsupported_export.py's fixture: a fully
    fake, DB-independent orchestrator so these tests exercise ONLY the file
    classification/routing policy, not the real detector stack."""

    class EmptyDetector:
        async def scan(self, text, metadata=None):
            return DetectorOutput()

    monkeypatch.setattr(exporter, "build_orchestrator", lambda *a, **kw: EmptyDetector())
    monkeypatch.setattr(exporter.settings.vllm, "enabled", False)

    def run(files: dict[str, bytes], *, orchestrator=None, **options):
        if orchestrator is not None:
            monkeypatch.setattr(exporter, "build_orchestrator", lambda *a, **kw: orchestrator)
        source = tmp_path / "source"
        target = tmp_path / "output"
        source.mkdir()
        for name, content in files.items():
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        report = asyncio.run(exporter.export_project(
            db_session, source_path=str(source), target_path=str(target),
            project_name=tmp_path.name, sicil_no="TEST-SCANONLY", branch_name="test",
            initiated_by="TEST-SCANONLY", enable_path_masking=False, **options,
        ))
        return report, source, target

    return run


def _outcome(report, relative_path: str):
    return next(o for o in report.outcomes if o.relative_path == relative_path)


# ---------------------------------------------------------------------------
# 1. Clean lock file -> byte-identical output + scan_only_clean
# ---------------------------------------------------------------------------

def test_clean_lock_file_is_copied_byte_identical_and_reported_scan_only_clean(export_files, db_session):
    content = b'{"name": "demo", "lockfileVersion": 3, "packages": {}}\n'
    report, source, target = export_files({"package-lock.json": content, "good.py": b"value = 1\n"})

    outcome = _outcome(report, "package-lock.json")
    assert outcome.status == "scan_only_clean"
    assert outcome.final_state == "READY"
    assert (target / "package-lock.json").read_bytes() == content
    assert report.files_scan_only_clean == 1
    assert report.files_scan_only_sensitive == 0
    assert report.status == "completed"
    assert not db_session.scalars(select(AuditWarning).where(AuditWarning.run_id == report.run_id)).all()

    manifest = read_manifest(target, report.context_id)
    assert "package-lock.json" in manifest["files"]


# ---------------------------------------------------------------------------
# 2. Sensitive lock file -> never reaches output, goes to review/quarantine
# ---------------------------------------------------------------------------

def test_sensitive_lock_file_is_quarantined_not_masked_not_copied(export_files, db_session):
    secret_token = "npm_supersecrettoken1234567890abcdef"
    content = f'{{"registry": "https://npm.internal/", "_authToken": "{secret_token}"}}\n'.encode()

    class SecretDetector:
        async def scan(self, text, metadata=None):
            if secret_token not in text:
                return DetectorOutput()
            start = text.index(secret_token)
            return DetectorOutput(results=[
                DetectionResult(
                    deger=secret_token, tip="GENERIC_SECRET", guven_seviyesi="yuksek",
                    kaynak_motor="rule", start=start, end=start + len(secret_token), rule=_rule(),
                )
            ])

    report, source, target = export_files(
        {"package-lock.json": content, "good.py": b"value = 1\n"}, orchestrator=SecretDetector(),
    )

    outcome = _outcome(report, "package-lock.json")
    assert outcome.status == "scan_only_sensitive"
    assert outcome.final_state == "SECURITY_QUARANTINE"
    assert secret_token not in outcome.error  # raw value never echoed
    assert not (target / "package-lock.json").exists()
    assert report.files_scan_only_sensitive == 1
    assert report.status == "completed_with_warnings"
    # The sibling file is untouched by the lock file's quarantine.
    assert _outcome(report, "good.py").status in _CLEAN_STATUSES

    warnings = db_session.scalars(select(AuditWarning).where(AuditWarning.run_id == report.run_id)).all()
    assert len(warnings) == 1
    assert warnings[0].audit_failed is True
    assert warnings[0].masked_content == ""
    assert secret_token not in warnings[0].reasoning

    _, quarantined_count, validation_failed_count = _pending_and_quarantined_counts(
        db_session, project_name=report.project_name, sicil_no=report.sicil_no,
        branch_name=report.branch_name, run_id=report.run_id,
    )
    assert validation_failed_count == 1


# ---------------------------------------------------------------------------
# 3. All real dependency lock file names route to SCAN_ONLY
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock", "Cargo.lock",
])
def test_all_real_lock_filenames_are_scan_only(export_files, name):
    report, source, target = export_files({name: b"clean lock content\n"})
    outcome = _outcome(report, name)
    assert outcome.status == "scan_only_clean"
    assert (target / name).read_bytes() == b"clean lock content\n"


# ---------------------------------------------------------------------------
# 4. SCAN_ONLY detector exception -> not clean, isolated to that file
# ---------------------------------------------------------------------------

def test_scan_only_detector_crash_is_not_treated_as_clean_and_stays_isolated(export_files, db_session):
    class CrashingDetector:
        async def scan(self, text, metadata=None):
            if (metadata or {}).get("file_path") == "package-lock.json":
                raise RuntimeError("simulated detector crash")
            return DetectorOutput()

    report, source, target = export_files(
        {"package-lock.json": b'{"ok": true}\n', "good.py": b"value = 1\n"}, orchestrator=CrashingDetector(),
    )

    outcome = _outcome(report, "package-lock.json")
    assert outcome.status == "failed_detection"
    assert outcome.final_state == "VALIDATION_FAILED"
    assert not (target / "package-lock.json").exists()
    assert report.files_failed_detection == 1
    assert report.status == "completed_with_warnings"

    # The crash on the lock file must not affect the other, healthy file.
    good_outcome = _outcome(report, "good.py")
    assert good_outcome.status in _CLEAN_STATUSES
    assert (target / "good.py").exists()

    warnings = db_session.scalars(select(AuditWarning).where(AuditWarning.run_id == report.run_id)).all()
    assert len(warnings) == 1
    assert warnings[0].file_path == "package-lock.json"
    assert warnings[0].audit_failed is True


# ---------------------------------------------------------------------------
# 6-10. Binary/office/pdf/image formats: never "clean", never masked
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["lib.jar", "doc.pdf", "report.docx", "sheet.xlsx", "photo.png"])
def test_binary_and_office_formats_are_unscanned_not_clean(export_files, name):
    payload = b"\x00\x01any bytes, content is irrelevant - extension decides\xff\xfe"
    report, source, target = export_files({name: payload})

    outcome = _outcome(report, name)
    assert outcome.status not in _CLEAN_STATUSES
    assert outcome.status == "skipped_unsupported"
    assert outcome.final_state == "SKIPPED"
    assert not (target / name).exists()
    assert (source / name).read_bytes() == payload  # source untouched
    assert report.status == "completed"


def test_office_document_with_plain_ascii_bytes_still_not_masked(export_files):
    """Guards against relying on content-sniffing alone (bkz. file_pipeline.py
    is_opaque_binary_filename short-circuit): even fully-printable ASCII
    content in a .docx must stay out of the masking pipeline."""
    report, source, target = export_files({"report.docx": b"plain ascii text, no null bytes at all"})
    outcome = _outcome(report, "report.docx")
    assert outcome.status == "skipped_unsupported"
    assert not (target / "report.docx").exists()


# ---------------------------------------------------------------------------
# 11-12. Archives inside the project: archive_unsupported, quarantined
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["bundle.zip", "release.tar", "release.tar.gz", "release.tgz", "archive.rar", "archive.7z"])
def test_project_internal_archives_are_quarantined_not_clean(export_files, db_session, name):
    payload = b"PK\x03\x04 pretend-archive-bytes"
    report, source, target = export_files({name: payload, "good.py": b"value = 1\n"})

    outcome = _outcome(report, name)
    assert outcome.status == "archive_unsupported"
    assert outcome.final_state == "SECURITY_QUARANTINE"
    assert outcome.status not in _CLEAN_STATUSES
    assert not (target / name).exists()
    assert (source / name).read_bytes() == payload
    assert report.files_archive_unsupported == 1
    assert report.status == "completed_with_warnings"

    warnings = db_session.scalars(select(AuditWarning).where(AuditWarning.run_id == report.run_id)).all()
    assert len(warnings) == 1
    assert warnings[0].audit_failed is True

    # Sibling file is unaffected.
    assert _outcome(report, "good.py").status in _CLEAN_STATUSES


# ---------------------------------------------------------------------------
# 13. Real source/text files keep the existing MASK behaviour
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,content", [
    ("Main.java", b"class Main {}\n"),
    ("app.py", b"value = 1\n"),
    ("index.ts", b"export const x = 1;\n"),
    ("component.tsx", b"export const C = () => null;\n"),
    ("config.json", b'{"a": 1}\n'),
    ("schema.sql", b"CREATE TABLE t (id INT);\n"),
    ("notes.txt", b"just some notes\n"),
])
def test_real_source_and_text_files_keep_normal_mask_flow(export_files, name, content):
    report, source, target = export_files({name: content})
    outcome = _outcome(report, name)
    assert outcome.status in {"masked", "copied_text_no_match"}
    assert outcome.final_state == "READY"
    assert (target / name).read_bytes() == content
    # Some extensions (.ts/.tsx/.sql) fall back to a bracket/quote syntax
    # check when the real parser dependency isn't installed, which adds a
    # validation_warning independent of masking - not a regression from
    # this policy change, so only the per-file outcome is asserted strictly.
    assert report.status in {"completed", "completed_with_warnings"}
    assert report.files_scan_only_clean == report.files_scan_only_sensitive == 0
    assert report.files_archive_unsupported == report.files_skipped_unsupported == 0
    assert report.files_excluded == 0


# ---------------------------------------------------------------------------
# 14. Real generated/cache/dependency dirs stay IGNORE (excluded)
# ---------------------------------------------------------------------------

def test_generated_and_vendor_dirs_remain_excluded(export_files):
    report, source, target = export_files({
        "node_modules/pkg/index.js": b"module.exports = {};\n",
        ".git/config": b"[core]\n",
        "target/classes/Output.txt": b"build output\n",
        "good.py": b"value = 1\n",
    })

    # Built-in exclusions prune whole directories even without DB patterns.
    for path in ("node_modules", "target"):
        outcome = _outcome(report, path)
        assert outcome.status == "excluded"
        assert outcome.final_state == "SKIPPED"
        assert outcome.status not in _CLEAN_STATUSES
        assert not (target / path).exists()

    # .git IS a seeded default DB exclude pattern (directory-level, see
    # alembic seed 9f21a6b8e4c3 "git_directory") - the whole subtree is pruned during the
    # scan itself, so only the directory gets one outcome, not each file
    # under it (bkz. scanner.py module docstring).
    git_dir_outcome = _outcome(report, ".git")
    assert git_dir_outcome.status == "excluded"
    assert not (target / ".git").exists()
    assert not (target / ".git" / "config").exists()

    assert _outcome(report, "good.py").status in _CLEAN_STATUSES


# ---------------------------------------------------------------------------
# 15. Cross-cutting: no unscanned/unsupported/excluded outcome is ever "clean"
# ---------------------------------------------------------------------------

def test_no_unscanned_or_excluded_outcome_is_ever_reported_clean(export_files):
    report, source, target = export_files({
        "package-lock.json": b'{"ok": true}\n',
        "lib.jar": b"\x00binary",
        "bundle.zip": b"PK\x03\x04fake",
        "node_modules/pkg/index.js": b"module.exports = {};\n",
        "good.py": b"value = 1\n",
    })

    never_clean_statuses = {
        "skipped_unsupported", "archive_unsupported", "excluded",
        "scan_only_sensitive", "failed_detection", "copied_undecodable",
        "skipped_too_large", "error",
    }
    for outcome in report.outcomes:
        if outcome.status in never_clean_statuses:
            assert outcome.status not in _CLEAN_STATUSES, outcome.relative_path
            assert outcome.final_state != "READY", outcome.relative_path

    assert _outcome(report, "package-lock.json").status == "scan_only_clean"
    assert _outcome(report, "good.py").status in _CLEAN_STATUSES
    assert report.status == "completed_with_warnings"


# ===========================================================================
# FINAL SCAN_ONLY CONSISTENCY VERIFICATION (read-only, post-registry)
# ===========================================================================
#
# The tests above only exercise PRIMARY detection on the lock file's own
# content. These tests cover the second, read-only pass that catches a value
# the primary detector missed in the lock file but that was independently
# confirmed sensitive somewhere else in the same run (bkz.
# exporter.py _run_scan_only_final_verification).


def _selective_detector(secret: str, *, blind_for: set[str] = frozenset()):
    """Fake orchestrator: flags `secret` in any file's content EXCEPT the
    paths in `blind_for` - simulates a primary-detection gap for exactly
    those files, the scenario the final verification pass exists for."""

    class SelectiveDetector:
        async def scan(self, text, metadata=None):
            path = (metadata or {}).get("file_path", "")
            if path in blind_for or secret not in text:
                return DetectorOutput()
            start = text.index(secret)
            return DetectorOutput(results=[
                DetectionResult(
                    deger=secret, tip="INTERNAL_HOST", guven_seviyesi="yuksek",
                    kaynak_motor="rule", start=start, end=start + len(secret), rule=_rule(),
                )
            ])

    return SelectiveDetector()


# --- A: cross-file discovery ------------------------------------------------

def test_final_verification_catches_value_primary_detection_missed_in_lock_file(export_files, db_session):
    secret = "NEXUS_INTERNAL_X"
    lock_content = f'{{"registry": "https://nexus.internal/{secret}/repo"}}\n'.encode()
    java_content = f'String token = "{secret}";\n'.encode()

    report, source, target = export_files(
        {"package-lock.json": lock_content, "Config.java": java_content},
        orchestrator=_selective_detector(secret, blind_for={"package-lock.json"}),
    )

    lock_outcome = _outcome(report, "package-lock.json")
    assert lock_outcome.status == "scan_only_sensitive"
    assert lock_outcome.final_state == "SECURITY_QUARANTINE"
    assert secret not in lock_outcome.error  # raw value never echoed
    assert not (target / "package-lock.json").exists()
    assert report.files_scan_only_sensitive == 1
    assert report.status == "completed_with_warnings"

    # The file that DID trigger primary detection was masked normally -
    # final verification must not touch/duplicate that work.
    java_outcome = _outcome(report, "Config.java")
    assert java_outcome.status == "masked"
    assert secret not in (target / "Config.java").read_text(encoding="utf-8")

    warnings = db_session.scalars(select(AuditWarning).where(AuditWarning.run_id == report.run_id)).all()
    lock_warnings = [w for w in warnings if w.file_path == "package-lock.json"]
    assert len(lock_warnings) == 1
    assert lock_warnings[0].audit_failed is True
    assert lock_warnings[0].masked_content == ""
    assert secret not in lock_warnings[0].reasoning

    _, _, validation_failed_count = _pending_and_quarantined_counts(
        db_session, project_name=report.project_name, sicil_no=report.sicil_no,
        branch_name=report.branch_name, run_id=report.run_id,
    )
    assert validation_failed_count == 1


# --- B: genuinely clean lock, registry non-empty but unrelated -------------

def test_final_verification_leaves_clean_lock_untouched_when_registry_has_other_values(export_files):
    other_secret = "OTHER_SECRET_VALUE_1"
    lock_content = b'{"name": "demo", "lockfileVersion": 3}\n'
    java_content = f'String token = "{other_secret}";\n'.encode()

    report, source, target = export_files(
        {"package-lock.json": lock_content, "Config.java": java_content},
        orchestrator=_selective_detector(other_secret),
    )

    # Sanity: the registry really is non-empty for this run.
    assert _outcome(report, "Config.java").status == "masked"

    lock_outcome = _outcome(report, "package-lock.json")
    assert lock_outcome.status == "scan_only_clean"
    assert lock_outcome.final_state == "READY"
    assert (target / "package-lock.json").read_bytes() == lock_content
    assert report.files_scan_only_sensitive == 0


# --- C: multiple lock files, same missed value ------------------------------

def test_final_verification_catches_value_across_multiple_lock_files(export_files):
    secret = "NEXUS_INTERNAL_X"
    lock_files = {
        "package-lock.json": f'{{"token": "{secret}"}}\n'.encode(),
        "yarn.lock": f"# token {secret}\n".encode(),
        "poetry.lock": f'password = "{secret}"\n'.encode(),
    }
    files = dict(lock_files)
    files["Config.java"] = f'String token = "{secret}";\n'.encode()

    report, source, target = export_files(
        files, orchestrator=_selective_detector(secret, blind_for=set(lock_files)),
    )

    for name in lock_files:
        outcome = _outcome(report, name)
        assert outcome.status == "scan_only_sensitive", name
        assert outcome.final_state == "SECURITY_QUARANTINE", name
        assert not (target / name).exists(), name

    assert report.files_scan_only_sensitive == 3
    assert _outcome(report, "Config.java").status == "masked"


# --- D: lookalike identifiers must not cause a false-positive quarantine ---

def test_lookalike_values_in_lock_file_do_not_trigger_false_positive_quarantine(export_files):
    real_secret = "REAL_SECRET_VALUE_1"
    # All of these merely LOOK sensitive; none of them is the registered
    # canonical value, so find_consistency_occurrences must reject them all.
    lock_content = b'{"comment": "MAX_LOGIN_TEST_3 USER_TEST_12 UserService localhost"}\n'
    java_content = f'String token = "{real_secret}";\n'.encode()

    report, source, target = export_files(
        {"package-lock.json": lock_content, "Config.java": java_content},
        orchestrator=_selective_detector(real_secret),
    )

    assert _outcome(report, "Config.java").status == "masked"  # registry really is populated
    outcome = _outcome(report, "package-lock.json")
    assert outcome.status == "scan_only_clean"
    assert (target / "package-lock.json").read_bytes() == lock_content


# --- E: failure isolation ---------------------------------------------------

def test_final_verification_crash_is_isolated_to_the_offending_file(export_files, db_session, monkeypatch):
    secret = "NEXUS_INTERNAL_X"
    java_content = f'String token = "{secret}";\n'.encode()
    real_find = exporter.find_consistency_occurrences

    def _crash_for_package_lock(text, registry, *, file_path=""):
        if file_path == "package-lock.json":
            raise RuntimeError("simulated final verification crash")
        return real_find(text, registry, file_path=file_path)

    monkeypatch.setattr(exporter, "find_consistency_occurrences", _crash_for_package_lock)

    report, source, target = export_files(
        {
            "package-lock.json": b'{"ok": true}\n',
            "yarn.lock": b"# nothing sensitive here\n",
            "Config.java": java_content,
        },
        orchestrator=_selective_detector(secret, blind_for={"package-lock.json", "yarn.lock"}),
    )

    crashed = _outcome(report, "package-lock.json")
    assert crashed.status == "failed_detection"
    assert crashed.final_state == "VALIDATION_FAILED"
    assert crashed.status not in _CLEAN_STATUSES
    assert not (target / "package-lock.json").exists()

    # The other lock file's own verification must complete normally.
    healthy_lock = _outcome(report, "yarn.lock")
    assert healthy_lock.status == "scan_only_clean"
    assert (target / "yarn.lock").exists()

    # And the ordinary masked file is entirely unaffected.
    java_outcome = _outcome(report, "Config.java")
    assert java_outcome.status == "masked"
    assert (target / "Config.java").exists()

    warnings = db_session.scalars(select(AuditWarning).where(AuditWarning.run_id == report.run_id)).all()
    crash_warnings = [w for w in warnings if w.file_path == "package-lock.json"]
    assert len(crash_warnings) == 1
    assert crash_warnings[0].audit_failed is True
    assert report.files_failed_detection == 1
    assert report.status == "completed_with_warnings"


# --- F: existing MASK/BINARY/ARCHIVE behavior stays unaffected -------------

def test_final_verification_does_not_disturb_mask_binary_archive_categories(export_files):
    secret = "NEXUS_INTERNAL_X"
    files = {
        "Main.java": f'String token = "{secret}";\n'.encode(),
        "app.py": b"value = 1\n",
        "config.json": b'{"a": 1}\n',
        "lib.jar": b"\x00binary",
        "bundle.zip": b"PK\x03\x04fake",
        "package-lock.json": b'{"ok": true}\n',
    }

    report, source, target = export_files(files, orchestrator=_selective_detector(secret))

    assert _outcome(report, "Main.java").status == "masked"
    assert _outcome(report, "app.py").status in {"masked", "copied_text_no_match"}
    assert _outcome(report, "config.json").status in {"masked", "copied_text_no_match"}
    assert _outcome(report, "lib.jar").status == "skipped_unsupported"
    assert _outcome(report, "bundle.zip").status == "archive_unsupported"
    assert _outcome(report, "package-lock.json").status == "scan_only_clean"

    assert (target / "package-lock.json").exists()
    assert not (target / "lib.jar").exists()
    assert not (target / "bundle.zip").exists()


# ===========================================================================
# Byte-identical guarantee (direct, dedicated assertions)
# ===========================================================================

def test_scan_only_clean_output_is_byte_identical_including_bom_crlf_and_whitespace(export_files):
    """Encoding (UTF-8 BOM), newline style (CRLF) and trailing whitespace
    must all survive untouched - scan_only_clean copies raw bytes, it never
    goes through the decode/encode round-trip that a masked file does."""
    tricky = (
        "﻿{\r\n"
        '  "name": "demo",\r\n'
        '  \t"note": "trailing spaces   ",   \r\n'
        "}\n"
    ).encode("utf-8")

    report, source, target = export_files({"package-lock.json": tricky})

    outcome = _outcome(report, "package-lock.json")
    assert outcome.status == "scan_only_clean"
    output_bytes = (target / "package-lock.json").read_bytes()
    assert output_bytes == tricky
    assert hashlib.sha256(output_bytes).hexdigest() == hashlib.sha256(tricky).hexdigest()


def test_scan_only_sensitive_leaves_no_output_file_at_all(export_files):
    secret = "NEXUS_INTERNAL_X"
    content = f'{{"authToken": "{secret}"}}\n'.encode()

    report, source, target = export_files(
        {"package-lock.json": content}, orchestrator=_selective_detector(secret),
    )

    outcome = _outcome(report, "package-lock.json")
    assert outcome.status == "scan_only_sensitive"
    assert not (target / "package-lock.json").exists()
    assert list(target.rglob("*")) == [] or "package-lock.json" not in {p.name for p in target.rglob("*")}
