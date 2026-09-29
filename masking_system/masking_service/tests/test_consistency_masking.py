"""Job-scope canonical sensitive-value consistency pass regressions."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import unicodedata

import pytest

from app.services import exporter as exporter_module
from app.services.audit_reviewer import AuditVerdict
from app.services.detectors import DetectionResult, DetectorOutput, synthetic_llm_rule
from app.services.unmasker import unmask_project

SENSITIVE = "ORION PERSONEL PLATFORMU"
SHORT_SENSITIVE = "ORION"


class _SeedOnlyOrchestrator:
    """Simulate a detector that confirms values in one file and misses all others."""

    def __init__(self, values: list[str], seed_file: str = "Seed.java") -> None:
        self.values = values
        self.seed_file = seed_file

    async def scan(self, text: str, metadata=None) -> DetectorOutput:
        if (metadata or {}).get("file_path") != self.seed_file:
            return DetectorOutput()
        results = []
        for value in self.values:
            start = text.find(value)
            if start < 0:
                continue
            results.append(
                DetectionResult(
                    deger=value,
                    tip="KURUMSAL_DEGER",
                    guven_seviyesi="yuksek",
                    kaynak_motor="dictionary",
                    gerekce="seed-only test detector",
                    start=start,
                    end=start + len(value),
                    rule=synthetic_llm_rule("KURUMSAL_DEGER"),
                )
            )
        return DetectorOutput(results=results)


def _write(path: Path, text: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding=encoding)


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _run_export_and_restore(
    db_session, monkeypatch, tmp_path, files, *, seed_values=None, max_inline_size=None, seed_file="Seed.java"
):
    source = tmp_path / "source"
    for relative_path, text, encoding in files:
        _write(source / relative_path, text, encoding)

    monkeypatch.setattr(
        exporter_module,
        "build_orchestrator",
        lambda *args, **kwargs: _SeedOnlyOrchestrator(seed_values or [SENSITIVE], seed_file=seed_file),
    )

    async def _clean_audit(masked_text, vllm_settings):
        return AuditVerdict(risky=False)

    monkeypatch.setattr(exporter_module, "audit_masked_text", _clean_audit)
    monkeypatch.setattr(exporter_module, "find_leaked_terms", lambda db, text, **kwargs: [])
    monkeypatch.setattr(exporter_module.settings.vllm, "enabled", False)

    target = tmp_path / "masked"
    identity = (f"consistency-{tmp_path.name}", "P-CONSISTENCY-1", "main")
    export_kwargs = {}
    if max_inline_size is not None:
        export_kwargs["max_inline_size"] = max_inline_size
    report = asyncio.run(
        exporter_module.export_project(
            db_session,
            source_path=str(source),
            project_name=identity[0],
            sicil_no=identity[1],
            branch_name=identity[2],
            target_path=str(target),
            initiated_by=identity[1],
            **export_kwargs,
        )
    )

    restored = tmp_path / "restored"
    unmask_report = unmask_project(
        db_session,
        source_path=str(target),
        project_name=identity[0],
        sicil_no=identity[1],
        branch_name=identity[2],
        target_path=str(restored),
        initiated_by=identity[1],
    )
    return source, target, restored, report, unmask_report


@pytest.mark.parametrize("prefix,value", [("mask_kurumsal_deger", "ELENA"), ("mask_kurumsal_deger", SENSITIVE)])
def test_first_and_second_pass_share_placeholder_in_same_file(db_session, monkeypatch, tmp_path, prefix, value):
    content = f"Owner: {value}\n" + "\n".join(f"src/{value}/file_{index}.ts" for index in range(8)) + "\n"
    files = [("FILE_LIST.txt", content, "utf-8")]
    source, target, restored, report, unmask_report = _run_export_and_restore(
        db_session, monkeypatch, tmp_path, files, seed_values=[value], seed_file="FILE_LIST.txt",
    )
    assert report.files_failed_consistency_validation == 0, report.summary_text()
    assert report.total_matches == 9
    assert (target / "FILE_LIST.txt").read_text().count(prefix) == 9
    assert not unmask_report.has_unresolved_placeholders
    assert _tree_bytes(restored) == _tree_bytes(source)


def test_numeric_placeholder_can_be_reused_across_passes(db_session, monkeypatch, tmp_path):
    files = [("Seed.json", '{"staff": [12345, 12345]}', "utf-8")]
    source, target, restored, report, _ = _run_export_and_restore(
        db_session, monkeypatch, tmp_path, files, seed_values=["12345"], seed_file="Seed.json",
    )
    assert report.files_failed_consistency_validation == 0, report.summary_text()
    staff = json.loads((target / "Seed.json").read_text())["staff"]
    assert isinstance(staff[0], int) and staff[0] == staff[1] and staff[0] != 12345
    assert _tree_bytes(restored) == _tree_bytes(source)


@pytest.mark.parametrize(
    ("secondary_name", "secondary_text"),
    [
        ("Other.java", f'final String project = "{SENSITIVE}";\n'),
        ("README.md", f"# Project\n\nOwner: {SENSITIVE}\n"),
        ("config.xml", f"<config><project>{SENSITIVE}</project></config>\n"),
        ("config.json", json.dumps({"project": SENSITIVE}) + "\n"),
    ],
)
def test_consistency_masks_same_value_across_java_markdown_xml_json(
    db_session, monkeypatch, tmp_path, secondary_name, secondary_text
):
    files = [
        ("Seed.java", f'final String project = "{SENSITIVE}";\n', "utf-8"),
        (secondary_name, secondary_text, "utf-8"),
    ]
    source, target, restored, report, unmask_report = _run_export_and_restore(
        db_session, monkeypatch, tmp_path, files
    )

    assert SENSITIVE not in (target / secondary_name).read_text(encoding="utf-8")
    assert "mask_kurumsal_deger_" in (target / secondary_name).read_text(encoding="utf-8")
    assert report.files_failed_consistency_validation == 0
    assert report.status == "completed_with_warnings"  # Seed.java has lexical validation only.
    assert any("bracket/quote" in warning for warning in report.validation_warnings)
    assert unmask_report.status == "completed"
    assert _tree_bytes(restored) == _tree_bytes(source)


def test_consistency_masks_utf8_and_utf16_occurrences(db_session, monkeypatch, tmp_path):
    files = [
        ("Seed.java", f'final String project = "{SENSITIVE}";\n', "utf-8"),
        ("wide.xml", f"<project>{SENSITIVE}</project>\n", "utf-16"),
    ]
    source, target, restored, report, _unmask_report = _run_export_and_restore(
        db_session, monkeypatch, tmp_path, files
    )

    assert SENSITIVE not in (target / "wide.xml").read_text(encoding="utf-16")
    assert report.files_failed_consistency_validation == 0
    assert _tree_bytes(restored) == _tree_bytes(source)


def test_consistency_masks_comment_and_string_occurrences(db_session, monkeypatch, tmp_path):
    files = [
        ("Seed.java", f'final String project = "{SENSITIVE}";\n', "utf-8"),
        (
            "CommentAndString.java",
            f"// deployment owner: {SENSITIVE}\nfinal String owner = \"{SENSITIVE}\";\n",
            "utf-8",
        ),
    ]
    source, target, restored, report, _unmask_report = _run_export_and_restore(
        db_session, monkeypatch, tmp_path, files
    )

    masked = (target / "CommentAndString.java").read_text(encoding="utf-8")
    assert SENSITIVE not in masked
    assert masked.count("mask_kurumsal_deger_") == 2
    assert report.total_matches == 3
    assert _tree_bytes(restored) == _tree_bytes(source)


def test_consistency_masks_same_value_in_twenty_files(db_session, monkeypatch, tmp_path):
    files = [("Seed.java", f'final String project = "{SENSITIVE}";\n', "utf-8")]
    files.extend((f"config/file_{index}.properties", f"project.name={SENSITIVE}\n", "utf-8") for index in range(20))
    source, target, restored, report, _unmask_report = _run_export_and_restore(
        db_session, monkeypatch, tmp_path, files
    )

    for index in range(20):
        assert SENSITIVE not in (target / f"config/file_{index}.properties").read_text(encoding="utf-8")
    assert report.total_matches == 21
    assert _tree_bytes(restored) == _tree_bytes(source)


def test_consistency_overlap_prefers_longest_sensitive_value(db_session, monkeypatch, tmp_path):
    files = [
        (
            "Seed.java",
            f'final String full = "{SENSITIVE}";\nfinal String shortName = "{SHORT_SENSITIVE}";\n',
            "utf-8",
        ),
        ("README.md", f"Project: {SENSITIVE}\n", "utf-8"),
    ]
    source, target, restored, report, _unmask_report = _run_export_and_restore(
        db_session, monkeypatch, tmp_path, files, seed_values=[SENSITIVE, SHORT_SENSITIVE]
    )

    masked = (target / "README.md").read_text(encoding="utf-8")
    assert masked.count("mask_") == 1
    assert SENSITIVE not in masked
    assert report.files_failed_consistency_validation == 0
    assert _tree_bytes(restored) == _tree_bytes(source)


def test_consistency_does_not_mask_identifier_substrings(db_session, monkeypatch, tmp_path):
    files = [
        ("Seed.java", f'final String project = "{SHORT_SENSITIVE}";\n', "utf-8"),
        ("Service.java", f"class {SHORT_SENSITIVE}Service {{ String owner = \"{SHORT_SENSITIVE}\"; }}\n", "utf-8"),
    ]
    source, target, restored, report, _unmask_report = _run_export_and_restore(
        db_session, monkeypatch, tmp_path, files, seed_values=[SHORT_SENSITIVE]
    )

    masked = (target / "Service.java").read_text(encoding="utf-8")
    assert f"class {SHORT_SENSITIVE}Service" in masked
    assert f'owner = "{SHORT_SENSITIVE}"' not in masked
    assert report.files_failed_consistency_validation == 0
    assert _tree_bytes(restored) == _tree_bytes(source)


def test_consistency_masks_complete_qualified_identifier_component_in_unknown_text_format(
    db_session, monkeypatch, tmp_path
):
    files = [
        ("Seed.java", f'final String project = "{SHORT_SENSITIVE}";\n', "utf-8"),
        ("definitions/query.future_format", f"namespace.{SHORT_SENSITIVE}()\n", "utf-8"),
    ]
    source, target, restored, report, _unmask_report = _run_export_and_restore(
        db_session, monkeypatch, tmp_path, files, seed_values=[SHORT_SENSITIVE]
    )

    masked = (target / "definitions/query.future_format").read_text(encoding="utf-8")
    assert f"namespace.{SHORT_SENSITIVE}()" not in masked
    assert "namespace.mask_kurumsal_deger_" in masked
    assert masked.rstrip().endswith("()")
    assert report.files_failed_consistency_validation == 0
    assert _tree_bytes(restored) == _tree_bytes(source)


def test_consistency_case_variation_has_exact_restore(db_session, monkeypatch, tmp_path):
    lower = SENSITIVE.lower()
    files = [
        ("Seed.java", f'final String project = "{SENSITIVE}";\n', "utf-8"),
        ("README.md", f"project: {lower}\n", "utf-8"),
    ]
    source, target, restored, report, _unmask_report = _run_export_and_restore(
        db_session, monkeypatch, tmp_path, files
    )

    assert lower not in (target / "README.md").read_text(encoding="utf-8")
    assert report.files_failed_consistency_validation == 0
    assert _tree_bytes(restored) == _tree_bytes(source)


def test_consistency_unicode_normalization_variation_has_exact_restore(db_session, monkeypatch, tmp_path):
    canonical = "ŞİRKET CAFÉ"
    decomposed = unicodedata.normalize("NFD", canonical)
    assert canonical != decomposed
    files = [
        ("Seed.java", f'final String project = "{canonical}";\n', "utf-8"),
        ("README.md", f"project: {decomposed}\n", "utf-8"),
    ]
    source, target, restored, report, _unmask_report = _run_export_and_restore(
        db_session, monkeypatch, tmp_path, files, seed_values=[canonical]
    )

    masked = (target / "README.md").read_text(encoding="utf-8")
    assert decomposed not in masked
    assert report.files_failed_consistency_validation == 0
    assert _tree_bytes(restored) == _tree_bytes(source)


def test_consistency_pass_ignores_lookalike_placeholder_identifier(db_session, monkeypatch, tmp_path):
    """A file touched by the SECOND (cross-file consistency) pass can also
    legitimately contain an identifier that merely looks like our own
    placeholder grammar (e.g. a constant named MAX_LOGIN_TEST_3). Both the
    consistency round-trip check and the final safety scan must treat it as
    pass-through, not as an unresolved/open canonical occurrence."""
    files = [
        ("Seed.java", f'final String project = "{SENSITIVE}";\n', "utf-8"),
        (
            "Config.java",
            f'int MAX_LOGIN_TEST_3 = 5;\nString owner = "{SENSITIVE}";\n',
            "utf-8",
        ),
    ]
    source, target, restored, report, _unmask_report = _run_export_and_restore(
        db_session, monkeypatch, tmp_path, files
    )

    masked = (target / "Config.java").read_text(encoding="utf-8")
    assert "MAX_LOGIN_TEST_3" in masked
    assert SENSITIVE not in masked


def test_final_safety_scan_failure_reports_location_not_value(db_session, monkeypatch, tmp_path):
    """When the final safety scan genuinely finds a canonical value still
    open, the failure reason must tell the user WHERE to look (line, column,
    entity type) - never the raw sensitive value itself."""
    from app.services import consistency_masking as consistency_masking_module

    files = [("Seed.java", f'final String project = "{SENSITIVE}";\n', "utf-8")]
    real_find = consistency_masking_module.find_consistency_occurrences
    calls = {"n": 0}

    def _fake_find(text, registry, *, file_path=""):
        calls["n"] += 1
        # First call (main replace loop): report nothing, so the file goes
        # straight to the final scan unmodified. Second call (final safety
        # scan): simulate a genuinely still-open occurrence.
        if calls["n"] < 2:
            return []
        entry = next(iter(registry.entries()), None)
        if entry is None:
            return []
        return [consistency_masking_module.ConsistencyOccurrence(
            entry=entry, start=0, end=1, original_value=text[0:1],
        )]

    monkeypatch.setattr(exporter_module, "find_consistency_occurrences", _fake_find)

    source, target, restored, report, _unmask_report = _run_export_and_restore(
        db_session, monkeypatch, tmp_path, files
    )

    assert report.files_failed_consistency_validation == 1, report.summary_text()
    outcome = next(o for o in report.outcomes if o.status == "failed_consistency_validation")
    assert "satir" in outcome.error
    assert "sutun" in outcome.error
    assert SENSITIVE not in outcome.error


def test_unverifiable_text_file_is_not_reported_as_success(db_session, monkeypatch, tmp_path):
    large_text = f"project={SENSITIVE}\n" + ("padding=" + "x" * 256)
    files = [
        ("Seed.java", f'final String p = "{SENSITIVE}";\n', "utf-8"),
        ("large.properties", large_text, "utf-8"),
    ]
    _source, target, _restored, report, _unmask_report = _run_export_and_restore(
        db_session,
        monkeypatch,
        tmp_path,
        files,
        max_inline_size=64,
    )

    assert report.status == "completed_with_warnings"
    assert report.files_skipped_too_large == 1
    assert not (target / "large.properties").exists()


def test_consistency_pass_reads_output_with_written_encoding(db_session, monkeypatch, tmp_path):
    # charset_normalizer'in tahmini maskelemeden sonra kayabiliyor (ornegin
    # cp1250 -> cp1257; hangi yone kayacagi rastgele placeholder'a bagli).
    # Kaymayi deterministik yapmak icin tutarlilik adiminin yeniden tahminini
    # sabitliyoruz: dosya YAZILDIGI kodlamayla okunmazsa round-trip bozulur.
    monkeypatch.setattr(exporter_module, "peek_classify", lambda path: (True, "cp1257"))
    legacy = (
        "// Müşteri kaydı işlemleri, ağ bağlantısı\n"
        f'public class Other {{\n    String p = "{SENSITIVE}";\n}}\n'
    )
    files = [
        ("Seed.java", f'final String project = "{SENSITIVE}";\n', "utf-8"),
        ("Other.java", legacy, "cp1254"),
    ]
    source, target, restored, report, _unmask_report = _run_export_and_restore(
        db_session, monkeypatch, tmp_path, files
    )

    assert report.files_failed_consistency_validation == 0, report.summary_text()
    assert SENSITIVE.encode("cp1254") not in (target / "Other.java").read_bytes()
    assert _tree_bytes(restored) == _tree_bytes(source)


def _first_occurrence_only(monkeypatch, target_file: str) -> dict[str, int]:
    """Her cagrida hedef dosya icin yalnizca ILK acik gecisi dondur.

    Bir degistirme turunun, sonraki bir gecisi ancak kendinden sonra
    gorunur kildigi durumu (orn. degisen string/yorum baglami) taklit eder.
    """
    from app.services import consistency_masking as consistency_masking_module

    real_find = consistency_masking_module.find_consistency_occurrences
    calls = {"n": 0}

    def _fake_find(text, registry, *, file_path=""):
        found = real_find(text, registry, file_path=file_path)
        if file_path != target_file:
            return found
        calls["n"] += 1
        return found[:1]

    monkeypatch.setattr(exporter_module, "find_consistency_occurrences", _fake_find)
    return calls


def test_consistency_pass_repeats_replacement_until_no_open_occurrence(db_session, monkeypatch, tmp_path):
    files = [
        ("Seed.java", f'final String project = "{SENSITIVE}";\n', "utf-8"),
        ("notes.txt", "\n".join(f"{index}: {SENSITIVE}" for index in range(3)) + "\n", "utf-8"),
    ]
    calls = _first_occurrence_only(monkeypatch, "notes.txt")

    source, target, restored, report, unmask_report = _run_export_and_restore(
        db_session, monkeypatch, tmp_path, files
    )

    assert report.files_failed_consistency_validation == 0, report.summary_text()
    masked = (target / "notes.txt").read_text(encoding="utf-8")
    assert SENSITIVE not in masked
    assert masked.count("mask_kurumsal_deger") == 3
    # 3 degistirme turu + final guvenlik taramasi.
    assert calls["n"] == 4
    assert not unmask_report.has_unresolved_placeholders
    assert _tree_bytes(restored) == _tree_bytes(source)


def test_consistency_pass_gives_up_after_three_rounds_and_final_scan_blocks(db_session, monkeypatch, tmp_path):
    files = [
        ("Seed.java", f'final String project = "{SENSITIVE}";\n', "utf-8"),
        ("notes.txt", "\n".join(f"{index}: {SENSITIVE}" for index in range(4)) + "\n", "utf-8"),
    ]
    calls = _first_occurrence_only(monkeypatch, "notes.txt")

    _source, target, _restored, report, _unmask_report = _run_export_and_restore(
        db_session, monkeypatch, tmp_path, files
    )

    assert report.files_failed_consistency_validation == 1, report.summary_text()
    outcome = next(o for o in report.outcomes if o.status == "failed_consistency_validation")
    assert outcome.relative_path == "notes.txt"
    assert "final safety scan 1 acik canonical occurrence" in outcome.error
    assert SENSITIVE not in outcome.error
    assert not (target / "notes.txt").exists()
    assert calls["n"] == 4
