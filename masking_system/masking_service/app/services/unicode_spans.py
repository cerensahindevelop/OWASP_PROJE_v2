"""NFKC/casefold search text with offsets into the unchanged source text."""
from __future__ import annotations

from collections.abc import Sequence
import unicodedata


def normalized_view(text: str) -> tuple[str, Sequence[int], Sequence[int]]:
    """Return normalized text and the source span of each normalized character.

    Keep a starter and its combining marks together so composed/decomposed
    spellings compare equally. Adjacent starters may also compose (e.g.
    Hangul Jamo), including after compatibility decomposition. Only split
    where normalization cannot interact across the boundary. Casefold runs
    after NFKC, exactly as in learned-decision lookup/hash normalization.

    A search can hit only part of an expanded character (e.g. s within ß).
    Callers must normalize the recovered source slice and compare it with
    their needle before accepting it.
    """
    if text.isascii():
        return text.casefold(), range(len(text)), range(1, len(text) + 1)

    parts: list[str] = []
    starts: list[int] = []
    ends: list[int] = []

    def append_segment(start: int, end: int, normalized: str) -> None:
        if normalized == text[start:end]:
            # Unchanged combining sequences need no coarse cluster mapping:
            # preserve existing literal matches before a noncomposing mark.
            for offset in range(start, end):
                folded = text[offset].casefold()
                parts.append(folded)
                starts.extend([offset] * len(folded))
                ends.extend([offset + 1] * len(folded))
        else:
            folded = normalized.casefold()
            parts.append(folded)
            starts.extend([start] * len(folded))
            ends.extend([end] * len(folded))

    segment_start = 0
    for index in range(1, len(text)):
        char = text[index]
        # A compatibility character can decompose to a leading combining
        # mark even when its own canonical combining class is zero.
        if unicodedata.combining(unicodedata.normalize("NFKD", char)[0]):
            continue
        segment = unicodedata.normalize("NFKC", text[segment_start:index])
        following = unicodedata.normalize("NFKC", char)
        boundary = segment[-1] + following[0]
        if unicodedata.normalize("NFC", boundary) != boundary:
            continue
        append_segment(segment_start, index, segment)
        segment_start = index
    append_segment(segment_start, len(text), unicodedata.normalize("NFKC", text[segment_start:]))
    return "".join(parts), starts, ends
