"""term_search: arama kopyasi, kaynak araligi eslemesi ve kaba (compact) arama."""

from __future__ import annotations

import unicodedata

import pytest

from app.services.rule_engine import RuleSpec, compile_rules
from app.services.term_search import (
    compact_text,
    compact_view,
    find_compact,
    find_search_view_matches,
    is_mixed_case_glued,
    search_view,
)
from app.services.term_upload import corporate_term_pattern


def _corporate_rules(*terms: str):
    return compile_rules([
        RuleSpec(
            id=index, rule_name=f"kurumsal_terim_pytest_{index}", category="pytest", pattern_type="regex",
            regex_pattern=corporate_term_pattern(term), regex_flags="i",
            placeholder_prefix="mask_kurumsal_ifade", priority=500,
        )
        for index, term in enumerate(terms, 1)
    ])


@pytest.mark.parametrize("text", [
    "proje ZetaLedger hazir",
    "Gökşube ve İZMİR şubesi: çalışan sayısı",
    "if (a && b) { x = y & z; }",
    "",
])
def test_plain_text_needs_no_search_view(text):
    assert search_view(text) is None


@pytest.mark.parametrize("source, expected", [
    ('{"a": "\\u005AetaLedger"}', '{"a": "ZetaLedger"}'),
    ("&#90;etaLedger &#x5A;eta Zeta&shy;Ledger", "ZetaLedger Zeta ZetaLedger"),
    ("&ouml;zel", "özel"),
    ("Zeta​Ledger Ze⁠ta Zeta­Ledger", "ZetaLedger Zeta ZetaLedger"),
    ("Zetа Lеdger", "Zeta Ledger"),  # Kiril а ve е
    ("ＺｅｔａＬｅｄｇｅｒ", "ZetaLedger"),
    (unicodedata.normalize("NFD", "Gökşube"), "Gökşube"),
    ("&bogus; &#0; \\uZZZZ", None),  # cozulemeyen kodlar oldugu gibi kalir
])
def test_search_view_decodes_and_keeps_case(source, expected):
    view = search_view(source)
    assert (view.text if view is not None else None) == expected


def test_search_view_maps_every_character_back_to_source():
    source = "a &#90;eta​Ledger b"
    view = search_view(source)
    start = view.text.index("ZetaLedger")
    src_start, src_end = view.source_span(start, start + len("ZetaLedger"))
    assert source[src_start:src_end] == "&#90;eta​Ledger"


@pytest.mark.parametrize("text, value", [
    ('x = "\\u005AetaLedger";', "\\u005AetaLedger"),
    ("<p>&#90;etaLedger</p>", "&#90;etaLedger"),
    ("proje Zeta​Ledger hazir", "Zeta​Ledger"),
    ("proje Zetа Ledger hazir", "Zetа Ledger"),
    ("proje ＺｅｔａＬｅｄｇｅｒ hazir", "ＺｅｔａＬｅｄｇｅｒ"),
])
def test_view_matches_report_raw_source_value(text, value):
    matches = find_search_view_matches(_corporate_rules("ZetaLedger"), text, known=[])
    assert [m.original_value for m in matches] == [value]
    assert text[matches[0].start:matches[0].end] == value


def test_view_matches_skip_spans_already_found_in_source():
    rules = _corporate_rules("ZetaLedger")
    text = "ZetaLedger ve &#90;etaLedger"
    from app.services.rule_engine import find_matches_compiled
    known, _ = find_matches_compiled(rules, text)
    found = find_search_view_matches(rules, text, known)
    assert [m.original_value for m in known] == ["ZetaLedger"]
    assert [m.original_value for m in found] == ["&#90;etaLedger"]


def test_view_matches_keep_existing_placeholders_untouched():
    text = "mask_kurumsal_ifade_1 &amp; diger"
    assert find_search_view_matches(_corporate_rules("kurumsal"), text, known=[]) == []


@pytest.mark.parametrize("text", [
    "Zeta.Ledger", "Zeta\nLedger", "Z e t a L e d g e r", 'x = "Zeta" + "Ledger"', "zeta_ledger",
    "ZETA-LEDGER", "&#90;eta.Ledger",
])
def test_compact_view_finds_separated_spellings(text):
    view = compact_view(text)
    spans = find_compact(view, compact_text("ZetaLedger"))
    assert len(spans) == 1
    start, end = spans[0]
    assert compact_text(text[start:end]) == "zetaledger"


def test_compact_view_never_bridges_a_barrier():
    text = "Zeta mask_kurumsal_ifade_1 Ledger"
    barrier = (5, 26)
    assert find_compact(compact_view(text, [barrier]), "zetaledger") == []
    assert find_compact(compact_view(text), "zetamaskkurumsalifade1ledger") != []


def test_compact_text_folds_turkish_letters():
    assert compact_text("Ahmet Yılmaz") == compact_text("AHMET,YILMAZ") == "ahmetyilmaz"
    assert compact_text("İZMİR") == "izmir"


@pytest.mark.parametrize("text, term, glued_mixed", [
    ("routeBySeverity", "ebys", True),      # camelCase diliminden yapisik
    ("ETTIVEDA", "veda", False),
    ("ettiveda", "veda", False),
    ("ettiVeda", "veda", False),            # camelCase siniri yapisik degil
    ("ETTIVeda", "veda", False),            # kisaltma siniri
    ("eski veda", "veda", False),
])
def test_is_mixed_case_glued(text, term, glued_mixed):
    start = text.casefold().index(term)
    assert is_mixed_case_glued(text, start, start + len(term)) is glued_mixed
