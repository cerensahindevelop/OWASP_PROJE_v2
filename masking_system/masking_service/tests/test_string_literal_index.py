"""Regressions for the lexical string/template-literal scanner.

Both bugs below were found while investigating real masking runs that
corrupted TSX output: a naive quote scanner cannot tell a contraction/
possessive apostrophe in JSX text ("admin'in", "don't") apart from a real
string delimiter, and a backtick template literal was indexed as one
opaque span, so a match anywhere inside it caused the WHOLE literal -
including live ``${...}`` expressions - to be replaced by one placeholder.
"""

from __future__ import annotations

from app.services.string_literal_index import StringLiteralIndex


def test_apostrophe_in_jsx_text_does_not_open_a_false_string():
    text = (
        "<p>Kullanicinin admin'in onayi bekleniyor; unvan 'Acme Corp' "
        "olarak gecer, e-posta foo@example.com burada</p>\n"
    )
    index = StringLiteralIndex(text, "a.tsx")
    contents = [text[s[0] : s[1]] for s in index.spans]
    assert contents == ["Acme Corp"]


def test_apostrophe_false_pair_does_not_swallow_real_jsx_markup():
    text = (
        "import React from 'react';\n\n"
        "export function UserCard() {\n"
        "  return (\n"
        "    <div className=\"card\">\n"
        "      <p>Bu admin'in gizli sifresi 's3cr3t-Key-99' olarak degisti.</p>\n"
        "      <button onClick={() => alert('tamam')}>Kapat</button>\n"
        "    </div>\n"
        "  );\n"
        "}\n"
    )
    index = StringLiteralIndex(text, "a.tsx")
    contents = [text[s[0] : s[1]] for s in index.spans]
    assert contents == ["react", "card", "s3cr3t-Key-99", "tamam"]


def test_python_string_prefixes_still_recognized_as_real_strings():
    for text, expected in [
        ("x = r'raw\\path'", "raw\\path"),
        ("x = rb'raw bytes'", "raw bytes"),
        ("x = f'value {y}'", "value {y}"),
    ]:
        index = StringLiteralIndex(text, "a.py")
        assert len(index.spans) == 1
        start, end, _, _ = index.spans[0]
        assert text[start:end] == expected


def test_ordinary_code_strings_are_unaffected():
    assert [t for t in _spans("const x = 'hello';", "ts")] == ["hello"]
    assert [t for t in _spans("SELECT 'it''s secret';", "sql")] == ["it''s secret"]
    assert [t for t in _spans("<Foo bar='baz'/>", "tsx")] == ["baz"]


def _spans(text: str, suffix: str) -> list[str]:
    index = StringLiteralIndex(text, f"a.{suffix}")
    return [text[s[0] : s[1]] for s in index.spans]


def test_template_literal_interpolation_is_not_string_content():
    text = "const msg = `Contact: ${name} at foo@example.com, id=${id}`;"
    index = StringLiteralIndex(text, "a.tsx")
    contents = [text[s[0] : s[1]] for s in index.spans]
    assert contents == ["Contact: ", " at foo@example.com, id="]
    email_start = text.index("foo@example.com")
    email_end = email_start + len("foo@example.com")
    bounds = index.enclosing(email_start, email_end)
    assert bounds is not None
    content_start, content_end, _, _ = bounds
    # The matched value's enclosing "string" must be just the static run it
    # sits in, never the whole template literal (which would also delete the
    # live ${name}/${id} expressions when replaced).
    assert text[content_start:content_end] == " at foo@example.com, id="


def test_nested_template_literal_interpolation_is_segmented():
    text = "const x = `outer ${`inner ${secretVal} done`} tail`;"
    index = StringLiteralIndex(text, "a.ts")
    contents = [text[s[0] : s[1]] for s in index.spans]
    assert contents == ["outer ", "inner ", " done", " tail"]


def test_optional_chaining_inside_interpolation_is_preserved():
    text = "const msg = `User: ${user?.profile?.email} contact foo@example.com`;"
    index = StringLiteralIndex(text, "a.tsx")
    contents = [text[s[0] : s[1]] for s in index.spans]
    assert contents == ["User: ", " contact foo@example.com"]


def test_nullish_coalescing_inside_interpolation_is_preserved():
    text = 'const msg = `Contact: ${email ?? "default@example.com"} real foo@example.com`;'
    index = StringLiteralIndex(text, "a.tsx")
    contents = [text[s[0] : s[1]] for s in index.spans]
    assert contents == ["Contact: ", " real foo@example.com"]
    # The nested string literal inside ${...} keeps its own quotes/content
    # verbatim - it is genuinely untouched code, not part of either static run.
    assert '"default@example.com"' not in "".join(contents)


def test_function_and_map_calls_inside_interpolation_are_preserved():
    text = "const msg = `Users: ${users.map(u => u.name).join(', ')} email foo@example.com`;"
    index = StringLiteralIndex(text, "a.tsx")
    contents = [text[s[0] : s[1]] for s in index.spans]
    assert contents == ["Users: ", " email foo@example.com"]


def test_object_literal_inside_interpolation_does_not_confuse_brace_depth():
    text = "const msg = `Data: ${JSON.stringify({a:1})} mail foo@example.com`;"
    index = StringLiteralIndex(text, "a.tsx")
    contents = [text[s[0] : s[1]] for s in index.spans]
    assert contents == ["Data: ", " mail foo@example.com"]


def test_go_backtick_raw_string_keeps_single_span_no_interpolation():
    text = "x := `raw ${not interpolated} text`"
    index = StringLiteralIndex(text, "a.go")
    contents = [text[s[0] : s[1]] for s in index.spans]
    assert contents == ["raw ${not interpolated} text"]
