"""Check source provenance against Python's whole-string Unicode normalizer."""
from itertools import product
import unicodedata

import pytest

from app.services.unicode_spans import normalized_view


@pytest.mark.parametrize("text,folded,starts,ends", [
    ("", "", [], []),
    ("ABC", "abc", [0, 1, 2], [1, 2, 3]),
    ("İß", "i\u0307ss", [0, 0, 1, 1], [1, 1, 2, 2]),
    ("S\u0327", "ş", [0], [2]),
    ("\u1100\u1161", "가", [0], [2]),
    ("\u3131\u314f", "가", [0], [2]),
    ("a\u0338", "a\u0338", [0, 1], [1, 2]),
])
def test_normalized_offsets_identify_original_characters(text, folded, starts, ends):
    actual, actual_starts, actual_ends = normalized_view(text)
    assert actual == folded
    assert list(actual_starts) == starts
    assert list(actual_ends) == ends


def test_normalization_matches_whole_string_for_interacting_unicode_sequences():
    # Include canonical ordering, compatibility decompositions, casefold
    # expansions and composition between characters of combining class zero.
    alphabet = ["A", "İ", "ß", "ﬃ", "\u0301", "\u0327", "\u0345", "\u0f73",
                "\u1100", "\u1161", "\u11a8", "\u3131", "\u314f", "\u09c7", "\u09be"]
    for chars in product(alphabet, repeat=3):
        text = "".join(chars)
        folded, starts, ends = normalized_view(text)
        assert folded == unicodedata.normalize("NFKC", text).casefold(), repr(text)
        assert len(folded) == len(starts) == len(ends)
        assert all(0 <= start < end <= len(text) for start, end in zip(starts, ends))
