"""Real parser regressions: balanced-but-invalid syntax, type and fallback contracts."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services import syntax_parsers
from app.services.syntax_validator import inspect_masked_syntax, validate_masked_syntax, validation_mode


@pytest.mark.parametrize("suffix,source,masked", [
    ("ts", "const value: number = 42;", "const value: number = ;"),
    ("tsx", "const x = <A title='x' />;", "const x = <A><B></A>;"),
    ("js", "const x = 42;", "const x = ;"),
    ("jsx", "const x = <A />;", "const x = <A><B></A>;"),
    ("xml", "<root><item /></root>", "<root><item></root>"),
    ("sql", "SELECT x FROM t WHERE x = 42", "SELECT x FROM t WHERE x ="),
])
def test_balanced_but_invalid_edits_are_rejected(suffix, source, masked):
    result = inspect_masked_syntax(f"file.{suffix}", masked, source)
    assert result.mode == "parser-based"
    assert result.error is not None
    assert suffix.upper() in result.error
    assert not result.warnings


@pytest.mark.parametrize("suffix,text", [
    ("ts", 'import { Missing } from "not-installed"; const x: Missing = mask_unknown_1;'),
    ("ts", 'const f = <T>(x: T): T => x;'),
    ("tsx", 'const f = <T,>(x: T) => <Missing>{x as string}</Missing>;'),
    ("js", 'const re = /["{}]/g; const s = `a ${`b ${value}`} c`;'),
    ("jsx", 'const el = <Unknown title="mask_value_1">{missing}</Unknown>;'),
    ("xml", '<?xml version="1.0"?><r xmlns:x="urn:test"><x:v><![CDATA[<secret>]]></x:v></r>'),
    ("sql", "SELECT 'it''s a (value)' /* don't count ( */ FROM t; -- ' comment"),
])
def test_valid_syntax_needs_no_import_resolution_or_execution(suffix, text):
    assert inspect_masked_syntax(f"a.{suffix}", text, text).error is None


def test_typescript_and_tsx_use_distinct_grammars():
    source = "const x = <number>value;"
    assert validate_masked_syntax("a.ts", source) is None
    assert validate_masked_syntax("a.tsx", source) is not None


def test_parser_reuse_is_thread_local():
    def run(index):
        first = syntax_parsers._script_parser("ts")
        assert syntax_parsers._script_parser("ts") is first
        result = inspect_masked_syntax("a.ts", f"const value{index}: number = ;")
        assert result.error is not None
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(run, range(100)))


def test_missing_script_parser_reports_lexical_fallback(monkeypatch):
    def missing(_suffix):
        raise ImportError("private installation path")
    monkeypatch.setattr(syntax_parsers, "_script_parser", missing)
    diagnostics = []
    assert validate_masked_syntax("a.ts", "const x = 1;", diagnostics=diagnostics) is None
    assert "bracket/quote" in diagnostics[0]
    assert "private" not in str(diagnostics)
    assert validate_masked_syntax("a.ts", "const x = (1;", "const x = 1;") is not None


@pytest.mark.parametrize("dialect,text", [
    ("postgres", "SELECT x::text FROM t WHERE x = 1"),
    ("tsql", "SELECT TOP 1 [value] FROM [table]"),
    ("mysql", "SELECT `value` FROM `table` LIMIT 1"),
    ("oracle", "SELECT NVL(value, 0) FROM dual"),
])
def test_sql_dialect_is_explicit(dialect, text):
    result = inspect_masked_syntax("a.sql", text, text, sql_dialect=dialect)
    assert result.error is None
    assert result.mode == "parser-based"
    assert not result.warnings


def test_unknown_sql_dialect_is_a_visible_fallback():
    result = inspect_masked_syntax("a.sql", "SELECT 1", "SELECT 2", sql_dialect="unknown-dialect")
    assert result.error is None
    assert result.mode == "bracket/quote-based"
    assert result.warnings


def test_sql_command_fallback_never_claims_parser_success_or_logs_source(caplog):
    text = "CREATE PROC confidential_source AS BEGIN SELECT 1 END"
    result = inspect_masked_syntax("a.sql", text, text)
    assert result.error is None
    assert result.mode == "bracket/quote-based"
    assert result.warnings
    assert "confidential_source" not in caplog.text


def test_unsupported_sql_source_still_checks_new_bracket_breakage():
    original = "CREATE PROC confidential_source AS BEGIN SELECT 1 END"
    result = inspect_masked_syntax("a.sql", original + "(", original)
    assert result.error is not None
    assert result.warnings


@pytest.mark.parametrize("declaration", [
    '<!DOCTYPE r [<!ENTITY secret SYSTEM "file:///never-read-secret">]><r>&secret;</r>',
    '<!DOCTYPE r [<!ENTITY secret SYSTEM "http://example.invalid/secret">]><r>&secret;</r>',
    '<!DOCTYPE r [<!ENTITY secret "private-value">]><r>&secret;</r>',
])
def test_xml_entities_are_never_expanded(declaration):
    result = inspect_masked_syntax("a.xml", declaration)
    assert result.error is not None
    assert "private-value" not in result.error
    assert "never-read" not in result.error


def test_xml_external_doctype_does_not_fetch(monkeypatch):
    import socket
    def fail(*args, **kwargs):
        pytest.fail("validator tried network access")
    monkeypatch.setattr(socket, "create_connection", fail)
    assert validate_masked_syntax("a.xml", '<!DOCTYPE r SYSTEM "http://example.invalid/dtd"><r />') is None


@pytest.mark.parametrize("before,after,types", [
    (42, "42", "number -> string"), (True, 1, "boolean -> number"),
    (None, "null", "null -> string"), ("x", False, "string -> boolean"),
    ([1], "x", "array -> string"), ({"x": 1}, [], "object -> array"),
])
def test_json_types_survive_masked_key_names(before, after, types):
    original = json.dumps({"secret-key": [before]})
    masked = json.dumps({"mask_key_1": [after]})
    error = validate_masked_syntax("a.json", masked, original)
    assert types in error
    assert "secret-key" not in error


def test_json_same_types_and_masked_keys_pass():
    before = '{"secret": [42, true, null, "secret", {"child": 1}]}'
    after = '{"mask_key_1": [3.14, false, null, "mask_secret_1", {"mask_key_2": 2}]}'
    assert validate_masked_syntax("a.json", after, before) is None


def test_duplicate_json_keys_do_not_hide_type_change():
    assert validate_masked_syntax("a.json", '{"x":"42","x":1}', '{"x":42,"x":1}') is not None


@pytest.mark.parametrize("original,masked", [("[1,2]", "[1]"), ('{"x":1}', "{}")])
def test_json_shape_changes_are_rejected(original, masked):
    assert "oge sayisi" in validate_masked_syntax("a.json", masked, original)


def test_invalid_json_source_is_explicitly_not_type_checked():
    result = inspect_masked_syntax("a.json", '{"x":"42"}', '{"x":')
    assert result.error is None
    assert result.warnings
    assert "Kaynak" in result.warnings[0]


@pytest.mark.parametrize("suffix", ["ts", "tsx", "js", "jsx", "xml", "sql"])
def test_preexisting_syntax_error_remains_compatible(suffix):
    result = inspect_masked_syntax(f"a.{suffix}", "(((", "(((")
    assert result.error is None
    assert result.warnings


@pytest.mark.parametrize("path", ["a.css", "a.scss", "a.html", "a.vue", "a.svelte", "a.md", "a.txt", "a.ini", "a.conf", "a.properties", ".env", "Dockerfile", "NOTICE"])
def test_structural_only_files_do_not_gain_a_parser(path):
    assert validation_mode(path) == "structural-only"
    result = inspect_masked_syntax(path, "{don't parse", "plain text")
    assert result.error is None
    assert any("parser yok" in warning for warning in result.warnings)


def test_other_languages_keep_legacy_bracket_route():
    assert validation_mode("a.java") == "bracket/quote-based"
    assert validate_masked_syntax("a.java", "class A {", "class A {}") is not None
