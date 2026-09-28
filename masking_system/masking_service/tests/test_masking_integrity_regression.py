"""Lossless edits and lexical boundaries across the reported file formats."""

import asyncio
import ast
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.services.detectors import DetectionResult, DetectorOutput, placeholder_prefix_for_type, synthetic_llm_rule
from app.services.mapping_service import DetectionOutcome, MaskingRunContext, apply_detections, detect_matches
from app.services.roundtrip_validator import verify_round_trip
from app.services.rule_engine import Match, PLACEHOLDER_RE, reverse_text
from app.services.string_literal_index import StringLiteralIndex
from app.services.syntax_validator import validate_masked_syntax
from app.services.token_boundary_validator import TokenBoundaryValidator


def hit(text, value, *, reported=None):
    start = text.index(value)
    return DetectionResult(
        deger=value if reported is None else reported, tip="SECRET",
        guven_seviyesi="yuksek", kaynak_motor="dictionary",
        start=start, end=start + len(value), rule=synthetic_llm_rule("SECRET"),
    )


@pytest.mark.parametrize("path,text", [
    ("a.ts", 'const value = "MiXeD";'),
    ("a.tsx", 'const el = <div title="MiXeD" />;'),
    ("a.json", '{"value": "MiXeD"}'),
    ("a.css", '.a { content: "MiXeD"; }'),
    ("a.md", 'Value: MiXeD'),
    ("a.xml", '<value>MiXeD</value>'),
])
def test_detector_spelling_never_overrides_exact_source(path, text):
    class Orchestrator:
        async def scan(self, text, metadata=None):
            return DetectorOutput(results=[hit(text, "MiXeD", reported="mixed")])

    outcome = asyncio.run(detect_matches(Orchestrator(), text, {"file_path": path}))
    assert len(outcome.matches) == 1
    match = outcome.matches[0]
    masked = text[:match.start] + "mask_secret_1" + text[match.end:]
    assert match.original_value == "MiXeD"
    assert verify_round_trip(text, masked, {"mask_secret_1": match.original_value}).ok
    assert validate_masked_syntax(path, masked, original_text=text) is None


@pytest.mark.parametrize("word", ["cléSecret", "变量Secret", "δοκιμήSecret"])
def test_unicode_identifier_boundary_agrees_with_unmask(word):
    text = f"{word} = 1"
    accepted, rejected = TokenBoundaryValidator().validate(text, [hit(text, "Secret")])
    assert not rejected
    result = accepted[0]
    assert result.deger == word
    masked = text[:result.start] + "mask_secret_1" + text[result.end:]
    assert verify_round_trip(text, masked, {"mask_secret_1": result.deger}).ok


def test_multiline_literal_with_inner_quotes_keeps_delimiters():
    text = 'value = """prefix\ninner "secret" suffix\nend"""\n'
    accepted, rejected = TokenBoundaryValidator().validate(text, [hit(text, "secret")], file_path="a.py")
    assert not rejected
    result = accepted[0]
    assert result.deger == 'prefix\ninner "secret" suffix\nend'
    masked = text[:result.start] + "mask_secret_1" + text[result.end:]
    ast.parse(masked)
    assert verify_round_trip(text, masked, {"mask_secret_1": result.deger}).ok


def test_jsx_text_apostrophe_does_not_corrupt_surrounding_markup():
    """A contraction/possessive apostrophe in JSX text ("admin'in") used to
    be misread as a real string delimiter, falsely pairing with a LATER,
    real quote on the same line and swallowing everything between them - if
    a detection then landed inside that false span, the whole stretch
    (including real JSX/code) was replaced by one placeholder, breaking
    parse. The email here must mask in place, leaving the surrounding
    markup and the unrelated real string untouched."""
    text = (
        "import React from 'react';\n\n"
        "export function UserCard() {\n"
        "  return (\n"
        "    <div className=\"card\">\n"
        "      <p>Musterinin admin'in onayiyla, sifre 'gizli-deger' iken "
        "e-posta foo@example.com paylasildi.</p>\n"
        "    </div>\n"
        "  );\n"
        "}\n"
    )
    accepted, rejected = TokenBoundaryValidator().validate(
        text, [hit(text, "foo@example.com")], file_path="a.tsx"
    )
    assert not rejected
    result = accepted[0]
    assert result.deger == "foo@example.com"
    masked = text[: result.start] + "mask_email_1" + text[result.end :]
    assert "'gizli-deger'" in masked
    assert "<div className=\"card\">" in masked
    assert verify_round_trip(text, masked, {"mask_email_1": result.deger}).ok
    assert validate_masked_syntax("a.tsx", masked, original_text=text) is None


def test_template_literal_interpolation_survives_masking():
    """A sensitive value inside a template literal's static text must mask
    without deleting neighboring ${...} expressions - those are live code,
    not string content, and silently erasing them used to still pass
    round-trip/syntax checks while corrupting program behavior."""
    text = "const msg = `Contact: ${name} at foo@example.com, id=${id}`;"
    accepted, rejected = TokenBoundaryValidator().validate(
        text, [hit(text, "foo@example.com")], file_path="a.tsx"
    )
    assert not rejected
    result = accepted[0]
    # The whole static run around the match is replaced (existing "no partial
    # string replacement" policy) - but never the ${...} expressions either
    # side of it, which is the invariant this regression protects.
    assert result.deger == " at foo@example.com, id="
    masked = text[: result.start] + "mask_email_1" + text[result.end :]
    assert masked == "const msg = `Contact: ${name}mask_email_1${id}`;"
    assert verify_round_trip(text, masked, {"mask_email_1": result.deger}).ok
    assert validate_masked_syntax("a.tsx", masked, original_text=text) is None


@pytest.mark.parametrize("text", [
    "const msg = `User: ${user?.profile?.email} contact foo@example.com`;",
    'const msg = `Contact: ${email ?? "default@example.com"} real foo@example.com`;',
    "const msg = `Users: ${users.map(u => u.name).join(', ')} email foo@example.com`;",
    "const msg = `Data: ${JSON.stringify({a:1})} mail foo@example.com`;",
    "const msg = `outer ${`inner ${fn(1,2)} val`} mail foo@example.com`;",
    'const msg = `Contact: ${fallback ?? "foo@example.com"} tail`;',
])
def test_modern_js_template_expressions_survive_masking(text):
    """Optional chaining, nullish coalescing, function/map calls, object
    literals, nested template literals and a nested string literal inside
    an interpolation must all keep working as live code after a sensitive
    value elsewhere is masked - valid source -> mask -> valid source."""
    accepted, rejected = TokenBoundaryValidator().validate(
        text, [hit(text, "foo@example.com")], file_path="a.tsx"
    )
    assert not rejected
    result = accepted[0]
    masked = text[: result.start] + "mask_email_1" + text[result.end :]
    assert "foo@example.com" not in masked
    assert verify_round_trip(text, masked, {"mask_email_1": result.deger}).ok
    assert validate_masked_syntax("a.tsx", masked, original_text=text) is None


def test_comment_quotes_do_not_consume_real_string():
    text = '/* odd " quote */ const key = "secret";'
    accepted, rejected = TokenBoundaryValidator().validate(text, [hit(text, "secret")], file_path="a.ts")
    assert not rejected
    assert accepted[0].deger == "secret"


def test_template_quotes_and_brackets_do_not_trigger_false_syntax_errors():
    text = 'const x = `literal " and ( bracket`;\n'
    accepted, rejected = TokenBoundaryValidator().validate(text, [hit(text, "literal")], file_path="a.ts")
    assert not rejected
    result = accepted[0]
    assert result.deger == 'literal " and ( bracket'
    assert validate_masked_syntax("a.ts", text) is None
    assert validate_masked_syntax("a.ts", text + "}") is not None


def test_toml_literal_backslash_and_sql_doubled_quote():
    text = "path = 'secret\\'\n"
    index = StringLiteralIndex(text, "a.toml")
    assert index.enclosing(8, 14) == (8, 15, 7, 16)
    sql = "SELECT 'it''s secret';"
    bounds = StringLiteralIndex(sql, "a.sql").enclosing(sql.index("secret"), sql.index("secret") + 6)
    assert sql[bounds[0]:bounds[1]] == "it''s secret"


@pytest.mark.parametrize("category", ["X" * 80, "123CATEGORY", "PERSON_NAME", "秘密", "A_" * 40])
def test_generated_prefix_always_obeys_reverse_protocol(category):
    prefix = placeholder_prefix_for_type(category)
    assert len(prefix) <= 50
    token = f"{prefix}_1"
    assert PLACEHOLDER_RE.fullmatch(token)
    assert verify_round_trip("original", token, {token: "original"}).ok


def test_legacy_or_custom_prefix_is_reversed_only_when_mapping_exists():
    text = "CUSTOM_1 HTTP_200 mask_secret_2"
    restored, count, unresolved = reverse_text(text, {"CUSTOM_1": "secret"})
    assert restored == "secret HTTP_200 mask_secret_2"
    assert count == 1
    assert unresolved == ["mask_secret_2"]
    assert verify_round_trip("secret", "CUSTOM_1", {"CUSTOM_1": "secret"}).ok


def test_missing_offsets_are_rejected_before_match_conversion():
    accepted, rejected = TokenBoundaryValidator().validate("secret", [replace(hit("secret", "secret"), start=None)])
    assert not accepted
    assert len(rejected) == 1


def test_json_unquoted_number_still_fails_closed():
    assert validate_masked_syntax("a.json", '{"id": mask_id_1}', original_text='{"id": 1234}') is not None


def test_unknown_source_placeholder_requires_exact_source_reconstruction():
    # Source equality, rather than token spelling, distinguishes an original
    # literal from a genuinely new placeholder whose mapping was lost.
    original = "mask_old_1 secret"
    masked = "mask_old_1 mask_secret_2"
    assert verify_round_trip(original, masked, {"mask_secret_2": "secret"}).ok
    assert not verify_round_trip(original, masked, {}).ok
    assert not verify_round_trip(original, masked, {"mask_secret_2": "wrong"}).ok


def test_passthrough_identity_entries_never_hide_a_genuinely_lost_mapping():
    """exporter._apply_masking adds identity entries (token -> token) for
    already-masked-shaped spans that were never touched, so a coincidental
    lookalike identifier (MAX_LOGIN_TEST_3) doesn't fail the round-trip
    self-check. This must stay purely additive: a DIFFERENT, genuinely new
    placeholder that lost its real mapping (simulating an internal bug) has
    to keep failing, even in the same file as a harmless lookalike."""
    original_text = "const a = 1; // MAX_LOGIN_TEST_3 seen here\n"
    # masked_text as if a real replacement happened (mask_secret_1) AND the
    # lookalike was correctly left untouched.
    masked_text = "const a = mask_secret_1; // MAX_LOGIN_TEST_3 seen here\n"
    placeholder_map: dict[str, str] = {}  # mask_secret_1's real mapping "lost"
    lookalike = "MAX_LOGIN_TEST_3"
    placeholder_map.setdefault(lookalike, lookalike)

    result = verify_round_trip(original_text, masked_text, placeholder_map)
    assert not result.ok
    assert result.unresolved_placeholders == ["mask_secret_1"]


@pytest.mark.parametrize("spans", [[(0, 4), (3, 6)], [(-1, 2)], [(1, 20)], [(2, 2)]])
def test_invalid_edit_plan_fails_before_database_access(spans):
    outcome = DetectionOutcome(
        matches=[Match(synthetic_llm_rule("SECRET"), "secret", start, end) for start, end in spans],
        review_results=[], llm_errors=[], already_masked_spans=[], overlap_conflicts=[], boundary_rejections=[],
    )
    # None has no DB methods: getting ValueError proves no mutation was tried.
    with pytest.raises(ValueError, match="maskeleme araligi"):
        apply_detections(None, MaskingRunContext(context=SimpleNamespace(id=1)), "secret", outcome)
