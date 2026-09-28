from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models import AuditLog
from app.services import exporter
from app.services.audit_reviewer import AuditVerdict
from app.services.detectors import DetectorOutput, synthetic_llm_rule
from app.services.mapping_service import get_or_create_context, get_or_create_mapping, mask_relative_path
from app.services.path_placeholders import PathPlaceholderResolver, UnsafeUnmaskPathError
from app.services.unmasker import unmask_project


@pytest.mark.parametrize("name,expected", [
    ("mask_deneme_316Service.java", "OrionService.java"),
    ("mask_deneme_316TalepDto.java", "OrionTalepDto.java"),
    ("mask_deneme_316Kart.tsx", "OrionKart.tsx"),
    ("premask_deneme_316_service.ts", "preOrion_service.ts"),
    ("Önmask_deneme_316Şube", "ÖnOrionŞube"),
    ("mask_deneme_316.java", "Orion.java"),
])
def test_compound_names(name, expected):
    result, count, unresolved = PathPlaceholderResolver({"mask_deneme_316": "Orion"}).reverse(Path(name))
    assert result == Path(expected)
    assert count == 1 and unresolved == []


def test_exact_counter_and_missing_compound_token():
    resolver = PathPlaceholderResolver({"mask_deneme_31": "Wrong", "mask_deneme_316": "Right"})
    assert resolver.reverse(Path("mask_deneme_316Service.java")) == (Path("RightService.java"), 1, [])
    unknown = Path("mask_deneme_3160Service.java")
    assert resolver.reverse(unknown) == (unknown, 0, ["mask_deneme_3160"])


def test_restored_values_are_not_recursively_decoded():
    resolver = PathPlaceholderResolver({"mask_deneme_1": "mask_deneme_2", "mask_deneme_2": "Wrong"})
    assert resolver.reverse(Path("mask_deneme_1Service.java"))[0] == Path("mask_deneme_2Service.java")


@pytest.mark.parametrize("original", ["../outside", "..\\outside", "C:outside", "bad\0name"])
def test_compound_path_still_rejects_unsafe_values(original):
    with pytest.raises(UnsafeUnmaskPathError):
        PathPlaceholderResolver({"mask_deneme_316": original}).reverse(Path("mask_deneme_316Service.java"))


def test_compound_path_masking_and_unmask_preserve_tree_and_bytes(db_session, tmp_path):
    identity = ("path-compound-regression", "P-PATH", "main")
    context = get_or_create_context(db_session, *identity)
    rule = replace(synthetic_llm_rule("DENEME"), rule_name="kurumsal_terim_path_regression",
                   pattern_type="regex", regex_pattern="Orion", regex_flags=None)
    files = {
        "OrionModule/OrionService.java": b"class Example {}\r\n",
        "src/OrionKart.tsx": b"export const x = <div />;\n",
        "src/Orion_service.ts": b"export const x = 1;\n",
        "src/OrionAsset.png": b"\x89PNG\x00\xff\x00\x01",
    }
    masked = tmp_path / "masked"
    for name, content in files.items():
        path, mappings = mask_relative_path(db_session, context, Path(name), {}, [rule])
        assert mappings and "mask_kurumsal_ifade_" in str(path)
        dest = masked / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
    restored = tmp_path / "restored"
    report = unmask_project(db_session, source_path=str(masked), target_path=str(restored),
                            project_name=identity[0], sicil_no=identity[1], branch_name=identity[2], initiated_by="test")
    assert report.status == "completed_with_warnings"  # Legacy fixture has no integrity manifest.
    assert any("Butunluk kaydi yok" in notice for notice in report.validation_warnings)
    assert report.total_placeholders_resolved == 5
    assert {p.relative_to(restored).as_posix(): p.read_bytes() for p in restored.rglob("*") if p.is_file()} == files


def test_multi_segment_corporate_term_does_not_block_export(db_session, tmp_path, monkeypatch):
    """Regression: a corporate term whose text is itself a multi-segment
    file path (e.g. a hardcoded credential path like
    "/opt/kurum/gizli/anahtar") can never be masked by mask_relative_path()
    - it scans one path COMPONENT at a time, and no single component can
    ever contain a '/'. If the project's own folder layout happens to
    reproduce that term's text as nested directories, the old path
    leak-check (find_leaked_terms without exclude_path_spanning) raised
    ExportValidationError and made the export permanently unfixable for the
    user. It must now succeed - the term stays fully enforced for file
    CONTENT scanning (see test_term_upload_leak_check.py), just not for a
    check it could structurally never pass."""
    from app.services.term_upload import commit_term_upload

    commit_term_upload(
        db_session, filename="terms.txt", content=b"zephyrqx/novacrit/omegalabs\n",
        category="pytest_path_multiseg",
    )

    source = tmp_path / "src" / "zephyrqx" / "novacrit" / "omegalabs"
    source.mkdir(parents=True)
    (source / "config.py").write_text("value = 1\n", encoding="utf-8")
    target = tmp_path / "target"

    class _NoOpOrchestrator:
        async def scan(self, text, metadata=None):
            return DetectorOutput()

    monkeypatch.setattr(exporter, "build_orchestrator", lambda *a, **kw: _NoOpOrchestrator())
    monkeypatch.setattr(exporter.settings.vllm, "enabled", False)

    report = asyncio.run(exporter.export_project(
        db_session, source_path=str(tmp_path / "src"), project_name="path-multiseg-term-regression",
        sicil_no="P-PATH", branch_name="main", target_path=str(target), initiated_by="test",
    ))

    assert report.status == "completed"
    assert (target / "zephyrqx" / "novacrit" / "omegalabs" / "config.py").exists()


def test_path_leak_error_names_the_offending_file(db_session, tmp_path, monkeypatch):
    """When the path leak-check genuinely fires (here: path masking is off,
    so a corporate term stays open in the file's own path), the error must
    say WHICH file, not just that something somewhere failed."""
    from app.services.term_upload import commit_term_upload
    from app.services.exporter import ExportValidationError

    commit_term_upload(
        db_session, filename="terms.txt", content=b"zephyrqx\n",
        category="pytest_path_single",
    )

    source = tmp_path / "src" / "zephyrqx"
    source.mkdir(parents=True)
    (source / "config.py").write_text("value = 1\n", encoding="utf-8")
    target = tmp_path / "target"

    class _NoOpOrchestrator:
        async def scan(self, text, metadata=None):
            return DetectorOutput()

    monkeypatch.setattr(exporter, "build_orchestrator", lambda *a, **kw: _NoOpOrchestrator())
    monkeypatch.setattr(exporter.settings.vllm, "enabled", False)

    with pytest.raises(ExportValidationError) as excinfo:
        asyncio.run(exporter.export_project(
            db_session, source_path=str(tmp_path / "src"), project_name="path-leak-message-regression",
            sicil_no="P-PATH", branch_name="main", target_path=str(target), initiated_by="test",
            enable_path_masking=False,
        ))

    message = str(excinfo.value)
    assert "config.py" in message
    assert "zephyrqx" in message


def test_unresolved_path_is_reported_even_when_content_is_clean(db_session, tmp_path):
    identity = ("path-missing-regression", "P-PATH", "main")
    get_or_create_context(db_session, *identity)
    source = tmp_path / "masked"
    source.mkdir()
    name = "mask_deneme_999Service.java"
    (source / name).write_text("class Example {}\n")
    target = tmp_path / "restored"
    report = unmask_project(db_session, source_path=str(source), target_path=str(target),
                            project_name=identity[0], sicil_no=identity[1], branch_name=identity[2], initiated_by="test")
    assert report.status == "completed_with_warnings"
    assert report.unresolved_by_placeholder == {"mask_deneme_999": 1}
    assert report.files_unresolved_only == 1
    assert (target / name).read_bytes() == (source / name).read_bytes()
    db_session.flush()
    assert db_session.scalars(select(AuditLog).where(AuditLog.run_id == report.run_id, AuditLog.action == "error")).all()


def test_unmask_path_collision_preserves_existing_output(db_session, tmp_path):
    identity = ("path-collision-regression", "P-PATH", "main")
    context = get_or_create_context(db_session, *identity)
    mapping, _ = get_or_create_mapping(db_session, context.id, synthetic_llm_rule("DENEME"), "Orion")
    source = tmp_path / "masked"
    source.mkdir()
    (source / f"{mapping.placeholder_value}Service.java").write_text("first")
    (source / "OrionService.java").write_text("second")
    target = tmp_path / "restored"
    target.mkdir()
    sentinel = target / "keep.txt"
    sentinel.write_text("keep")
    with pytest.raises(UnsafeUnmaskPathError, match="cakismasi"):
        unmask_project(db_session, source_path=str(source), target_path=str(target),
                       project_name=identity[0], sicil_no=identity[1], branch_name=identity[2], initiated_by="test")
    assert sentinel.read_text() == "keep"


def test_export_rejects_nonreversible_path_before_touching_target(db_session, monkeypatch, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "Example.java").write_text("class Example {}\n")
    target = tmp_path / "target"
    target.mkdir()
    sentinel = target / "keep.txt"
    sentinel.write_text("keep")
    monkeypatch.setattr(exporter, "build_orchestrator", lambda *a, **kw: object())
    monkeypatch.setattr(exporter, "mask_relative_path", lambda *a, **kw: (Path("mask_deneme_999Service.java"), []))
    with pytest.raises(exporter.ExportValidationError, match="dosya yolu round-trip"):
        asyncio.run(exporter.export_project(db_session, source_path=str(source), target_path=str(target),
                    project_name="path-proof-regression", sicil_no="P-PATH", branch_name="main", initiated_by="test"))
    assert sentinel.read_text() == "keep"


def test_full_export_and_unmask_compound_paths(db_session, monkeypatch, tmp_path):
    class CleanDetector:
        async def scan(self, text, metadata=None):
            return DetectorOutput()

    async def clean_audit(*args, **kwargs):
        return AuditVerdict(risky=False)

    rule = replace(synthetic_llm_rule("DENEME"), rule_name="kurumsal_terim_path_regression",
                   pattern_type="regex", regex_pattern="Orion", regex_flags=None)
    monkeypatch.setattr(exporter, "build_orchestrator", lambda *a, **kw: CleanDetector())
    monkeypatch.setattr(exporter, "load_active_rules", lambda db: [rule])
    monkeypatch.setattr(exporter, "audit_masked_text", clean_audit)
    monkeypatch.setattr(exporter.settings.vllm, "enabled", False)
    source = tmp_path / "source"
    original = source / "OrionModule" / "OrionService.java"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"class Example {}\r\n")
    masked = tmp_path / "masked"
    identity = dict(project_name="path-export-regression", sicil_no="P-PATH", branch_name="main", initiated_by="test")
    report = asyncio.run(exporter.export_project(db_session, source_path=str(source), target_path=str(masked), **identity))
    assert report.status == "completed"
    assert any("mask_kurumsal_ifade_" in str(path.relative_to(masked)) for path in masked.rglob("*.java"))
    restored = tmp_path / "restored"
    result = unmask_project(db_session, source_path=str(masked), target_path=str(restored), **identity)
    assert result.status == "completed"
    assert result.total_placeholders_resolved == 2
    assert (restored / "OrionModule" / "OrionService.java").read_bytes() == original.read_bytes()


@pytest.mark.parametrize("name", [
    "YetkiServiceImpl.java", "yetkiServiceImpl.java", "ÖnYetkiServiceImpl.java",
    "Yetki123ServiceImpl.java", "Yetki٣ServiceImpl.java", "Yetki000.java",
])
def test_export_compound_path_does_not_report_generated_token_as_leak(
    db_session, monkeypatch, tmp_path, name,
):
    """A dictionary term inside our neutral token must not block all exports."""
    from app.services.term_upload import build_filter_rule

    for term in ("Yetki", "kurumsal"):
        db_session.add(build_filter_rule(
            term=term, category="pytest_compound_path_leak", status="ok", priority=1,
        ))
    db_session.flush()

    class CleanDetector:
        async def scan(self, text, metadata=None):
            return DetectorOutput()

    monkeypatch.setattr(exporter, "build_orchestrator", lambda *a, **kw: CleanDetector())
    monkeypatch.setattr(exporter.settings.vllm, "enabled", False)
    source = tmp_path / "source"
    original = source / "axioserror" / name
    original.parent.mkdir(parents=True)
    original.write_bytes(b"class Example {}\r\n")
    masked = tmp_path / "masked"
    identity = dict(project_name="compound-path-leak", sicil_no="P-PATH", branch_name="main", initiated_by="test")

    report = asyncio.run(exporter.export_project(
        db_session, source_path=str(source), target_path=str(masked), **identity,
    ))
    assert report.status == "completed"
    output = list((masked / "axioserror").glob("*.java"))
    assert len(output) == 1 and "mask_kurumsal_ifade_" in output[0].name

    restored = tmp_path / "restored"
    result = unmask_project(
        db_session, source_path=str(masked), target_path=str(restored), **identity,
    )
    assert result.status == "completed"
    assert (restored / "axioserror" / name).read_bytes() == original.read_bytes()
