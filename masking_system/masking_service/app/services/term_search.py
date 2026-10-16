"""Kurumsal terim aramasi icin metnin arama gorunumleri.

Sozluk desenleri metni karakter karakter okur: ekranda ayni gorunen ama
farkli kodlanmis yazimlar ("\\u005AetaLedger", "&#90;etaLedger", araya sifir
genislikli bosluk girmis "Zeta\\u200bLedger", Kiril "а" ile "Zetа") eslesmez.
Bu modul metnin YALNIZCA ARAMA icin kullanilan bir kopyasini uretir ve
kopyadaki her karakterin kaynakta hangi araliktan geldigini tutar: bulunan
eslesme kaynaktaki aralik olarak maskelenir, metnin kendisi degismez ve geri
donus (unmask) ayni kalir.

Iki gorunum:

- search_view: kacis kodlari (\\uXXXX, HTML entity) cozulur, gorunmez
  karakterler atilir, benzer harfler Latin karsiligina ve uyumluluk
  karakterleri (tam genislik, ayrik aksan) NFKC'ye cevrilir. Harf buyuklugu
  korunur: terim desenleri camelCase gecislerini okur.
- compact_view: arama gorunumunun ustune harf/rakam disindaki her sey atilir
  ve harfler katlanir ("Zeta.Ledger", "Ahmet,Yilmaz", satira bolunmus yazim).
  Son kontrolun ayractan bagimsiz kaba aramasi icindir; maskelenmis yer
  tutucular arasinda kopru kurulmaz.

Saf fonksiyonlar - DB erisimi yok.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, replace
from html.entities import html5

from app.services.identifier_parts import split_identifier
from app.services.rule_engine import Match, _CompiledRule, find_matches_compiled
from app.services.unicode_spans import normalized_view

# Metinde gorunmeyen ama harfler arasina girince eslesmeyi bozan karakterler:
# yumusak tire, sifir genislikli bosluk/birlestirici, word joiner, gorunmez
# matematik operatorleri, BOM. Word ve web'den kopyalanan metinde kendiliginden
# bulunabilirler.
_INVISIBLE = "­᠎​‌‍⁠⁡⁢⁣⁤﻿"

# Latin harfiyle ayni gorunen Kiril/Yunan harfleri (UTS #39 confusables'in
# Latin alfabesine birebir oturan alt kumesi). NFKC bunlari birlestirmez.
_HOMOGLYPHS = {
    # Kiril
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P",
    "С": "C", "Т": "T", "У": "Y", "Х": "X", "І": "I", "Ј": "J", "Ѕ": "S",
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i",
    "ј": "j", "ѕ": "s", "һ": "h", "ԁ": "d", "ԛ": "q", "ԝ": "w",
    # Yunan
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M",
    "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X", "ο": "o",
}

_ESCAPE_RE = re.compile(
    r"\\u(?P<u>[0-9A-Fa-f]{4})"
    r"|&#(?P<dec>[0-9]{1,7});"
    r"|&#[xX](?P<hex>[0-9A-Fa-f]{1,6});"
    r"|&(?P<name>[A-Za-z][A-Za-z0-9]{1,31});"
    rf"|(?P<char>[{_INVISIBLE}{''.join(_HOMOGLYPHS)}])"
)

# Turkce harflerin aksansiz karsiliklari - sozluk desenlerinin
# (rule_engine.diacritic_tolerant_escape) "s"<->"ş" esitligiyle ayni.
# casefold("İ") = "i" + U+0307 (ust nokta); nokta atilir.
_TR_FOLD = str.maketrans({"ı": "i", "ş": "s", "ğ": "g", "ç": "c", "ö": "o", "ü": "u", "̇": None})
_NON_ALNUM_RE = re.compile(r"[\W_]+")
_BARRIER = "\x00"


@dataclass(frozen=True)
class TextView:
    """Arama kopyasi ve her karakterinin kaynaktaki [start, end) araligi."""

    text: str
    starts: Sequence[int]
    ends: Sequence[int]

    def source_span(self, start: int, end: int) -> tuple[int, int]:
        return self.starts[start], self.ends[end - 1]


def _identity_view(text: str) -> TextView:
    return TextView(text, range(len(text)), range(1, len(text) + 1))


def _decoded(match: re.Match) -> str | None:
    """Bir kacis kodunun/karakterin arama karsiligi; cozulemiyorsa None."""
    if match["char"]:
        return _HOMOGLYPHS.get(match["char"], "")
    if match["name"]:
        value = html5.get(match["name"] + ";")
    else:
        code = int(match["u"] or match["dec"] or match["hex"], 16 if (match["u"] or match["hex"]) else 10)
        value = chr(code) if 0 < code <= 0x10FFFF and not 0xD800 <= code <= 0xDFFF else None
    if value is None:
        return None
    return "".join(_HOMOGLYPHS.get(ch, ch) for ch in value if ch not in _INVISIBLE)


def _decode(text: str) -> TextView | None:
    parts: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    cursor = 0

    def copy_until(end: int) -> None:
        parts.append(text[cursor:end])
        starts.extend(range(cursor, end))
        ends.extend(range(cursor + 1, end + 1))

    for match in _ESCAPE_RE.finditer(text):
        replacement = _decoded(match)
        if replacement is None:
            continue
        copy_until(match.start())
        parts.append(replacement)
        starts.extend([match.start()] * len(replacement))
        ends.extend([match.end()] * len(replacement))
        cursor = match.end()
    if not parts:
        return None
    copy_until(len(text))
    return TextView("".join(parts), starts, ends)


def search_view(text: str) -> TextView | None:
    """Arama kopyasi; metinde cozulecek/normallestirilecek bir sey yoksa None.

    Turkce dahil siradan metin (NFKC'de, kacis kodu ve gorunmez karakter
    icermeyen) hizli yoldan None doner: ek tarama yapilmaz.
    """
    decoded = _decode(text)
    base = decoded.text if decoded is not None else text
    if unicodedata.is_normalized("NFKC", base):
        return decoded
    normalized, starts, ends = normalized_view(base, casefold=False)
    if decoded is None:
        return TextView(normalized, starts, ends)
    return TextView(
        normalized,
        [decoded.starts[index] for index in starts],
        [decoded.ends[index - 1] for index in ends],
    )


def find_search_view_matches(
    compiled_rules: Sequence[_CompiledRule], text: str, known: Sequence[Match],
) -> list[Match]:
    """Kurallari arama kopyasinda calistirip kaynakta henuz bulunmamis
    eslesmeleri kaynak araligiyla dondurur (deger = kaynaktaki ham metin)."""
    if not compiled_rules:
        return []
    view = search_view(text)
    if view is None:
        return []
    occupied = [(match.start, match.end) for match in known]
    found: list[Match] = []
    for match in find_matches_compiled(list(compiled_rules), view.text)[0]:
        if is_mixed_case_glued(view.text, match.start, match.end):
            continue
        start, end = view.source_span(match.start, match.end)
        if any(start < other_end and other_start < end for other_start, other_end in occupied):
            continue
        occupied.append((start, end))
        found.append(replace(match, original_value=text[start:end], start=start, end=end))
    return found


def fold_letters(text: str) -> str:
    return text.casefold().translate(_TR_FOLD)


def compact_text(text: str) -> str:
    """Harf/rakam disi her seyi atilmis, katlanmis metin (esleme tutmaz)."""
    view = search_view(text)
    return _NON_ALNUM_RE.sub("", fold_letters(view.text if view is not None else text))


def compact_view(text: str, barriers: Sequence[tuple[int, int]] = ()) -> TextView:
    """compact_text'in kaynak eslemeli hali. barriers (orn. yer tutucular)
    tek bir engel karakterine donusur: iki yanindaki harfler birlesmez."""
    view = search_view(text) or _identity_view(text)
    blocked = sorted(barriers)
    parts: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    next_barrier = 0
    for index, char in enumerate(view.text):
        start, end = view.starts[index], view.ends[index]
        while next_barrier < len(blocked) and blocked[next_barrier][1] <= start:
            next_barrier += 1
        if next_barrier < len(blocked) and blocked[next_barrier][0] < end:
            if parts and parts[-1] != _BARRIER:
                parts.append(_BARRIER)
                starts.append(start)
                ends.append(end)
            continue
        for folded in fold_letters(char):
            if folded.isalnum():
                parts.append(folded)
                starts.append(start)
                ends.append(end)
    return TextView("".join(parts), starts, ends)


def find_compact(view: TextView, needle: str) -> list[tuple[int, int]]:
    """needle'in compact gorunumdeki ortusmeyen gecislerinin kaynak araliklari."""
    spans: list[tuple[int, int]] = []
    index = view.text.find(needle) if needle else -1
    while index >= 0:
        spans.append(view.source_span(index, index + len(needle)))
        index = view.text.find(needle, index + len(needle))
    return spans


# Bir kaba arama bulgusu tek yer tutucuyla degistirilirse araya giren yapisal
# isaretleri de yutar: CSV'de "Ahmet,Yilmaz" iki hucredir, kodda
# "Zeta" + "Ledger" iki string'dir; "# hassas\ndegerler = 1" tek yer tutucu
# olursa alt satirdaki kod yorumun icine kayar. Boyle bir deger, ayraclar
# terimin KENDI kelime sinirlarina dustugu surece parcalara ayrilir ve her
# parca ayri maskelenir ("mask_1,mask_2"). Kelimenin ortasindan bolunen
# ("hassas deger(ler") ya da kisa parcali deger bolunmez: parca dosyanin her
# yerinde maskelenecegi icin "ler" gibi bir parca yanlis olurdu. Yatay bosluk
# yapisal sayilmaz ("Z e t a L e d g e r" tek degerdir).
_SEPARATOR_RUN_RE = re.compile(r"[\W_]+")
_HORIZONTAL_SPACE = " \t\u00a0"
_MIN_PART_LETTERS = 4


def _term_words(term: str) -> list[str]:
    return [compact_text(part.text) for part in split_identifier(term)]


def structural_parts(value: str, term: str) -> list[str]:
    parts: list[str] = []
    cursor = 0
    for run in _SEPARATOR_RUN_RE.finditer(value):
        if run.group().strip(_HORIZONTAL_SPACE):
            parts.append(value[cursor:run.start()].strip(_HORIZONTAL_SPACE))
            cursor = run.end()
    parts.append(value[cursor:].strip(_HORIZONTAL_SPACE))
    if len(parts) < 2:
        return [value]
    words = _term_words(term)
    used = 0
    for part in parts:
        target, joined = compact_text(part), ""
        while used < len(words) and len(joined) < len(target):
            joined += words[used]
            used += 1
        if joined != target or sum(ch.isalpha() for ch in part) < _MIN_PART_LETTERS:
            return [value]
    return parts if used == len(words) else [value]


def is_mixed_case_glued(text: str, start: int, end: int) -> bool:
    """Soldan yapisik eslesme karisik harf buyuklugunde mi?

    Soldan yapisik yazim ("ETTIVEDA", "ettiveda") tek bicimlidir. Karisik
    buyuklukte bir eslesme camelCase bir tanimlayicinin rastgele bir dilimidir
    ("routeBySeverity" icinde "ebys" -> "eByS"); maskelenmez. camelCase ve
    kisaltma sinirlari ("ettiVeda", "ETTIVeda") yapisik sayilmaz.
    """
    if start == 0 or not text[start - 1].isalpha():
        return False
    previous, first = text[start - 1], text[start]
    if previous.islower() and first.isupper():
        return False
    if previous.isupper() and first.isupper() and start + 1 < end and text[start + 1].islower():
        return False
    letters = [ch for ch in text[start - 1:end] if ch.isalpha()]
    return any(ch.isupper() for ch in letters) and any(ch.islower() for ch in letters)
