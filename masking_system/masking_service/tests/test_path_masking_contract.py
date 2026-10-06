"""Contract shared by path masking, independent leak detection and restore."""

import asyncio
from pathlib import Path

import pytest

from app.core.crypto import decrypt_value, encrypt_value
from app.services import exporter
from app.services.detectors import DetectorOutput
from app.services.mapping_service import get_or_create_context, load_active_rules, mask_relative_path
from app.services.path_placeholders import PathPlaceholderResolver
from app.services.term_upload import build_filter_rule, find_leaked_terms


@pytest.fixture
def path_rule(db_session):
    rule = build_filter_rule(term="Zephyrqx", category="pytest_path_contract", status="ok", priority=1)
    db_session.add(rule)
    db_session.flush()
    return rule


@pytest.mark.parametrize("source", [
    "ZephyrqxService.java", "zephyrqxService.tsx", "ÖnZephyrqxService.cs",
    "Zephyrqx123.java", "Zephyrqx٣Service.java", "Zephyrqx000Service.java",
    "Zephyrqx123Novacrit456Service.java", "ZephyrqxNovacrit.java",
    "Zephyrqx_module/NovacritService.py", "Zephyrqx123/Novacrit456/asset.png",
    "Zephyrqx/ZephyrqxService.java", "Zephyrqxdan.txt",
    "önZephyrqx_şema/Novacrit_Çizim.xml",
    # All-caps neighbours have no boundary in the source; the token's digit
    # counter creates one only after the first term is replaced.
    "ZEPHYRQXNOVACRITService.java", "NOVACRITZEPHYRQX.java",
    "ZEPHYRQXNOVACRITZEPHYRQXService.java", "ZEPHYRQXNOVACRIT123Service.java",
    "ZEPHYRQXNOVACRIT/ZEPHYRQXNOVACRITImpl.java",
])
def test_paths_mask_check_and_restore_with_the_same_contract(db_session, path_rule, source):
    for term in ("Novacrit", "kurumsal", "ifade"):
        db_session.add(build_filter_rule(term=term, category="pytest_path_contract", status="ok", priority=1))
    db_session.flush()
    context = get_or_create_context(db_session, "path-contract", "P-CONTRACT", "main")
    original = Path(source)
    masked, mappings = mask_relative_path(db_session, context, original, {}, load_active_rules(db_session))
    assert mappings and masked != original
    values = {m.placeholder_value: decrypt_value(m.original_value_encrypted) for m in mappings}
    assert find_leaked_terms(
        db_session, masked.as_posix(), exclude_path_spanning=True, path_placeholders=values,
    ) == []
    assert PathPlaceholderResolver(values).reverse(masked) == (original, len(mappings), [])


@pytest.mark.parametrize("separator", ["/", "\\"])
def test_path_rules_use_component_boundaries(db_session, path_rule, separator):
    path_rule.regex_pattern = encrypt_value(r"^Zephyrqx$")
    db_session.flush()
    leaks = find_leaked_terms(
        db_session, separator.join(["src", "Zephyrqx", "file.py"]),
        exclude_path_spanning=True, path_placeholders=[],
    )
    assert [(leak.rule_name, leak.column_number) for leak in leaks] == [(path_rule.rule_name, 5)]


def test_multisegment_legacy_rule_needs_no_plain_term_metadata(db_session):
    rule = build_filter_rule(
        term="Zephyrqx/Novacrit", category="pytest_path_contract", status="ok", priority=1,
    )
    rule.corporate_term_encrypted = None  # Legacy rows can lack this field.
    db_session.add(rule)
    db_session.flush()
    text = "Zephyrqx/Novacrit/config.py"
    assert find_leaked_terms(db_session, text)
    assert find_leaked_terms(db_session, text, exclude_path_spanning=True, path_placeholders=[]) == []


@pytest.mark.parametrize("encrypted", [True, False])
@pytest.mark.parametrize("prefix", ["kurumsal_terim_", "kurumsal_alias_"])
def test_leak_check_supports_all_corporate_rule_storage_formats(db_session, path_rule, encrypted, prefix):
    path_rule.rule_name = prefix + "pytest_contract"
    path_rule.is_pattern_encrypted = encrypted
    if not encrypted:
        path_rule.regex_pattern = decrypt_value(path_rule.regex_pattern)
    db_session.flush()
    for options in ({}, {"exclude_path_spanning": True, "path_placeholders": []}):
        leaked = find_leaked_terms(db_session, "ZephyrqxService.java", **options)
        assert [term.rule_name for term in leaked] == [path_rule.rule_name]


@pytest.mark.parametrize("name", ["kurumsalXterimXnot_a_dictionary_rule", "KURUMSAL_TERIM_other"])
def test_similar_rule_name_is_not_a_corporate_rule(db_session, path_rule, name):
    # SQL LIKE treats '_' as a wildcard unless the prefix is escaped.
    path_rule.rule_name = name
    db_session.flush()
    assert find_leaked_terms(db_session, "ZephyrqxService.java") == []


@pytest.mark.parametrize("name", ["service_test_3.txt", "MAX_LOGIN_TEST_3.java"])
def test_unchanged_source_token_lookalikes_do_not_block_export(db_session, monkeypatch, tmp_path, name):
    class CleanDetector:
        async def scan(self, text, metadata=None):
            return DetectorOutput()

    monkeypatch.setattr(exporter, "build_orchestrator", lambda *a, **kw: CleanDetector())
    monkeypatch.setattr(exporter.settings.vllm, "enabled", False)
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    (source / name).write_bytes(b"class Example {}\n")
    report = asyncio.run(exporter.export_project(
        db_session, source_path=str(source), target_path=str(target),
        project_name="source-lookalike", sicil_no="P-CONTRACT", branch_name="main", initiated_by="test",
    ))
    assert report.status == "completed"
    assert (target / name).read_bytes() == (source / name).read_bytes()


def test_digit_extension_is_resolved_before_overlaps(db_session, path_rule):
    db_session.add(build_filter_rule(term="123", category="pytest_path_contract", status="ok", priority=1))
    db_session.flush()
    context = get_or_create_context(db_session, "digit-overlap", "P-CONTRACT", "main")
    original = Path("Zephyrqx123Service.java")
    masked, mappings = mask_relative_path(db_session, context, original, {}, load_active_rules(db_session))
    assert len(mappings) == 1
    values = {m.placeholder_value: decrypt_value(m.original_value_encrypted) for m in mappings}
    # Yol terimi, icerikteki gibi tam token'a genisler: kodda
    # `class Zephyrqx123Service` ile ayni yer tutucuyu alir.
    assert list(values.values()) == ["Zephyrqx123Service"]
    assert masked.suffix == ".java"
    assert PathPlaceholderResolver(values).reverse(masked) == (original, 1, [])


def test_genuine_leak_beside_known_token_still_preserves_existing_target(
    db_session, path_rule, monkeypatch, tmp_path,
):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "Zephyrqx.java").write_text("class Example {}\n")
    (target / "keep.txt").write_text("existing output")
    real_mask = exporter.mask_relative_path

    def leave_open_term(*args, **kwargs):
        masked, mappings = real_mask(*args, **kwargs)
        return masked.with_name(masked.stem + "Zephyrqx.java"), mappings

    monkeypatch.setattr(exporter, "mask_relative_path", leave_open_term)
    monkeypatch.setattr(exporter, "build_orchestrator", lambda *a, **kw: object())
    with pytest.raises(exporter.ExportValidationError, match="yol maskeleme son kontrolu") as error:
        asyncio.run(exporter.export_project(
            db_session, source_path=str(source), target_path=str(target),
            project_name="leak-contract", sicil_no="P-CONTRACT", branch_name="main", initiated_by="test",
        ))
    assert "konumlar(satir:sutun)=" in str(error.value)
    assert (target / "keep.txt").read_text() == "existing output"
    assert list(target.iterdir()) == [target / "keep.txt"]


def test_adjacent_all_caps_terms_in_path_are_masked_separately(db_session, monkeypatch, tmp_path):
    # Regresyon: VEGA maskelenince "mask_x_7OMEGAService" olusuyor, sayac
    # rakami OMEGA'nin soluna kaynakta olmayan bir sinir ekliyordu ve export
    # "yol maskeleme son kontrolu basarisiz" ile duruyordu.
    class CleanDetector:
        async def scan(self, text, metadata=None):
            return DetectorOutput()

    for term in ("VEGA", "OMEGA"):
        db_session.add(build_filter_rule(term=term, category="pytest_path_contract", status="ok", priority=1))
    db_session.flush()
    monkeypatch.setattr(exporter, "build_orchestrator", lambda *a, **kw: CleanDetector())
    monkeypatch.setattr(exporter.settings.vllm, "enabled", False)
    source, target = tmp_path / "source", tmp_path / "target"
    relative = Path("src/main/java/tr/gov/integration/VEGAOMEGAService.java")
    (source / relative).parent.mkdir(parents=True)
    (source / relative).write_bytes(b"class Example {}\n")

    report = asyncio.run(exporter.export_project(
        db_session, source_path=str(source), target_path=str(target),
        project_name="adjacent-terms", sicil_no="P-CONTRACT", branch_name="main", initiated_by="test",
    ))

    assert report.status == "completed"
    [written] = [path.relative_to(target) for path in target.rglob("*.java")]
    assert written.parent == relative.parent
    assert "VEGA" not in written.name.upper() and "OMEGA" not in written.name.upper()
    # Dosya adi, koddaki `VEGAOMEGAService` identifier'i gibi tek token olarak maskelenir.
    assert "Service" not in written.name and written.suffix == ".java"
