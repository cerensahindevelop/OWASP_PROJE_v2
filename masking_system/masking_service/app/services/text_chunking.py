"""Buyuk metinleri, tek bir katmanin tek seferde islemek istemeyecegi
boyutta, tercihen satir sinirindan kesilmis, birbiriyle ortusen (overlap)
parcalara bolen ortak yardimci.

presidio_detector.py (Katman 2) ve rule_engine.py (Katman 1) TARAFINDAN
PAYLASILIR - ikisi de ayni dosya icerigini tarar ve ayni "cok buyuk dosyada
tek parca halinde calismak istemiyoruz" problemini cozer; mantigin iki
yerde ayri ayri (ve zamanla birbirinden sapmis) kopyasi olmasin diye
buraya cikarildi.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass

DEFAULT_MAX_CHUNK_CHARS = 200_000
DEFAULT_CHUNK_OVERLAP_CHARS = 2_000


# Metni max_chars buyuklugunde, biraz ortusen parcalara boler (mumkunse satir sinirindan keser).
def chunk_text(text: str, max_chars: int, overlap_chars: int) -> list[tuple[int, str]]:
    if max_chars <= 0 or len(text) <= max_chars:
        return [(0, text)]

    overlap = max(0, min(overlap_chars, max_chars // 4))
    chunks: list[tuple[int, str]] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            newline = text.rfind("\n", start, end)
            if newline > start + max_chars // 2:
                end = newline + 1
        chunks.append((start, text[start:end]))
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


# Bu uzunluktan kisa satirlar (`}`, `end`, bos satir) tekrar etse de korunur:
# kazanci yoktur ve kodun goruntusunu LLM icin bozmamak gerekir.
_MIN_DEDUP_LINE_CHARS = 8
_DIGITS_RE = re.compile(r"\d+")


@dataclass(frozen=True)
class DedupedText:
    """LLM'e gidecek tekrarsiz metin ve her satirin orijinaldeki karsiliklari.

    line_starts[i]: i. satirin tekrarsiz metindeki baslangici.
    members[i]: o satirla ayni sinifa giren orijinal satirlarin (bas, son)
    araliklari (satir sonu haric); ilk eleman satirin kendisidir.
    """

    text: str
    line_starts: tuple[int, ...]
    members: tuple[tuple[tuple[int, int], ...], ...]

    def member_spans(self, original: str, start: int, end: int) -> list[tuple[int, int]] | None:
        """Tekrarsiz metindeki [start, end) araligini sinifin her satirindaki karsiligina esler.

        Sinif uyeleri yalnizca rakam dizilerinde farklilasir; rakam disi
        parcalar ayni oldugu icin konum parca parca tasinir. Aralik satir
        sonunu asiyorsa None doner.
        """
        index = bisect_right(self.line_starts, start) - 1
        if index < 0:
            return None
        if not self.members[index]:
            # Sentezlenmis satir (tablo sablonu/basligi): karsiligi yok, cagiran
            # taraf degeri metinde birebir arar.
            return []
        line_start = self.line_starts[index]
        first_start, first_end = self.members[index][0]
        if end - line_start > first_end - first_start:
            return None
        representative = original[first_start:first_end]
        spans = []
        for member_start, member_end in self.members[index]:
            mapped = _map_offsets(representative, original[member_start:member_end],
                                  start - line_start, end - line_start)
            if mapped is not None:
                spans.append((member_start + mapped[0], member_start + mapped[1]))
        return spans


def _token_starts(tokens: list[str]) -> list[int]:
    starts, position = [], 0
    for token in tokens:
        starts.append(position)
        position += len(token)
    return starts


# Temsilci satirdaki konumu, yalnizca rakamlari farkli bir satira tasir.
# re.split(r"(\d+)") cift indekste rakam disi, tek indekste rakam parcasi verir.
# Bir rakam dizisinin ortasina dusen sinir, guvenli tarafta kalmak icin
# dizinin tamamini kapsayacak sekilde genisletilir.
def _map_offsets(representative: str, member: str, start: int, end: int) -> tuple[int, int] | None:
    if representative == member:
        return start, end
    rep_tokens, member_tokens = re.split(r"(\d+)", representative), re.split(r"(\d+)", member)
    if len(rep_tokens) != len(member_tokens):
        return None
    rep_starts, member_starts = _token_starts(rep_tokens), _token_starts(member_tokens)

    def locate(position: int, *, is_end: bool) -> int:
        for index, token_start in enumerate(rep_starts):
            token_end = token_start + len(rep_tokens[index])
            if (token_start < position <= token_end) if is_end else (token_start <= position < token_end):
                return index
        return len(rep_tokens) - 1

    start_index, end_index = locate(start, is_end=False), locate(end, is_end=True)
    if start_index % 2:
        new_start = member_starts[start_index]
    else:
        new_start = member_starts[start_index] + start - rep_starts[start_index]
    if end_index % 2:
        new_end = member_starts[end_index] + len(member_tokens[end_index])
    else:
        new_end = member_starts[end_index] + end - rep_starts[end_index]
    return (new_start, new_end) if new_start < new_end else None


def digit_key(text: str) -> str:
    """Yalnizca rakamlari farkli metinleri ayni anahtara indirger."""
    return _DIGITS_RE.sub("0", text)


def dedupe_lines(text: str, *, normalize_digits: bool = False) -> DedupedText:
    """Tekrar eden satirlarin yalnizca ilk gecisini birakir (satir sirasi korunur).

    normalize_digits=True ise yalnizca rakamlari farkli satirlar (zaman damgasi,
    sayac, istek no) da ayni sayilir.
    """
    classes: dict[str, int] = {}
    kept: list[str] = []
    line_starts: list[int] = []
    members: list[list[tuple[int, int]]] = []
    kept_length = position = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        span = (position, position + len(content))
        position += len(line)
        key = digit_key(content) if normalize_digits else content
        if len(content.strip()) >= _MIN_DEDUP_LINE_CHARS:
            if key in classes:
                members[classes[key]].append(span)
                continue
            classes[key] = len(kept)
        kept.append(line)
        line_starts.append(kept_length)
        members.append([span])
        kept_length += len(line)
    return DedupedText("".join(kept), tuple(line_starts), tuple(tuple(m) for m in members))


def dedupe_repeated_lines(text: str) -> str:
    """Birebir tekrar eden satirlarin yalnizca ilk gecisini birakir."""
    return dedupe_lines(text).text


def dedupe_for_llm(text: str, max_chunk_chars: int, *, normalize_digits: bool = False) -> DedupedText | None:
    """Buyuk ve tekrarli metin icin LLM'e gonderilecek tekrarsiz metni dondurur.

    Her farkli satir LLM'e en az bir kez gider; bulunan degerler cagiran
    tarafta metnin tamaminda aranir. En az bir LLM parcasi kazandirmiyorsa
    None doner ve metin aynen taranir (kucuk ya da tekrarsiz dosyalarda
    davranis degismez).
    """
    if max_chunk_chars <= 0 or len(text) <= max_chunk_chars:
        return None
    deduped = dedupe_lines(text, normalize_digits=normalize_digits)
    return deduped if len(text) - len(deduped.text) >= max_chunk_chars else None
