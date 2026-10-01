"""Identifier'lari parcalarina ayiran ortak, saf ve deterministik yardimci.

camelCase, PascalCase, snake_case, SCREAMING_SNAKE, kebab-case ve bunlarin
karisimlarini destekler; kisaltma sinirini (`HTTPClient` -> HTTP + Client) ve
harf/rakam gecisini (`db01` -> db + 01) parca siniri sayar. Harf/rakam
olmayan her karakter (`_`, `-`, `.`, `/`, bosluk) ayractir ve parcaya girmez.
Unicode harfleri (Turkce dahil) str.isupper/islower ile siniflanir.

Tek ayristirici budur: generic bilesik ad filtresi (term_classifier),
yol/icerik uyusmazligi olcumu ve Faz 3'un parca bazli maskelemesi ayni
fonksiyonlari kullanir.
"""

from __future__ import annotations

from typing import NamedTuple


class IdentifierPart(NamedTuple):
    text: str
    start: int
    end: int


def _kind(ch: str) -> str:
    if ch.isdigit():
        return "digit"
    if ch.isupper():
        return "upper"
    if ch.isalpha():
        # Buyuk/kucuk harf ayrimi olmayan harfler de kucuk harf gibi davranir.
        return "lower"
    return "sep"


def split_identifier(text: str) -> list[IdentifierPart]:
    """Metni identifier parcalarina ayirir; ofsetler `text` icindedir."""
    parts: list[IdentifierPart] = []
    start = None
    for i, ch in enumerate(text):
        kind = _kind(ch)
        if kind == "sep":
            if start is not None:
                parts.append(IdentifierPart(text[start:i], start, i))
                start = None
            continue
        if start is None:
            start = i
            continue
        prev = _kind(text[i - 1])
        boundary = (
            (prev == "digit") != (kind == "digit")
            or (prev == "lower" and kind == "upper")
            # Kisaltma sonu: "HTTPClient" -> "HTTP" | "Client" (C, kucuk harften once).
            or (prev == "upper" and kind == "upper" and i + 1 < len(text) and _kind(text[i + 1]) == "lower")
        )
        if boundary:
            parts.append(IdentifierPart(text[start:i], start, i))
            start = i
    if start is not None:
        parts.append(IdentifierPart(text[start:], start, len(text)))
    return parts


def normalize_part(text: str) -> str:
    """Karsilastirma bicimi: casefold, harf/rakam disi karakterler atilir."""
    return "".join(ch for ch in text.casefold() if ch.isalnum())


def normalized_parts(text: str) -> list[str]:
    return [normalize_part(part.text) for part in split_identifier(text)]


def contains_term(text: str, term: str) -> bool:
    """`term`, `text` icinde YALNIZCA parca sinirlarinda geciyor mu?

    Terim normalize edilip (kucuk harf, ayracsiz) ardisik parcalarin
    birlesimiyle karsilastirilir: `tckimlik` -> `getTcKimlikNo` icinde gecer,
    `tc` -> `tcpPort` ya da `etc` icinde gecmez.
    """
    target = normalize_part(term)
    if not target:
        return False
    parts = normalized_parts(text)
    for i in range(len(parts)):
        joined = ""
        for part in parts[i:]:
            joined += part
            if joined == target:
                return True
            if len(joined) >= len(target) or not target.startswith(joined):
                break
    return False
