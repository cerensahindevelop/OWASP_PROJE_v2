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
