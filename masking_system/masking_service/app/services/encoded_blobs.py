"""Metin dosyalarina gomulu ikili veri (base64/hex resim, ikon, font) tespiti.

Ornek: Windows Forms .resx dosyalarindaki `<value>` icindeki base64 bitmap/
ikon verisi. Bu bloklarda LLM'in bulabilecegi bir kisi/kurum adi yoktur, ama
metnin buyuk kismini olusturduklari icin LLM'e onlarca parca halinde gider,
yuksek entropili metin cikti token butcesini kesilmeye (LLMTruncatedError)
surukler ve tek dosya dakikalar surer.

Guvenlik ilkesi: yalnizca GERCEKTEN ikili veri oldugu kanitlanan bloklar
secilir. Aday blok cozulur (base64 ya da hex); cozulen icerik okunabilir
metinse (orn. base64 ile gizlenmis bir config/parola) blok SECILMEZ ve LLM'e
aynen gider. Uzun identifier/yol listeleri, ikili veriye benzemeyen karakter
dagilimlari nedeniyle elenir. Bu modul yalnizca LLM girdisini daraltir:
Katman 1/2 (sozluk/regex/Presidio) bloklari yine tarar ve ciktiya yazilan
metin degismez.

Saf fonksiyonlar - DB/ag erisimi yok.
"""

from __future__ import annotations

import base64
import binascii
import re

# Aday: en az 40 karakterlik bir satir ve ardindan gelen, yalnizca base64/hex
# karakterlerinden olusan satirlar (girinti serbest: resx, PEM, e-posta).
# Blogun gercek siniri _block_end ile sabit satir genisligine gore daraltilir.
_CANDIDATE_RE = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}(?:[ \t]*\r?\n[ \t]*[A-Za-z0-9+/]+={0,2})*")
_LINE_RE = re.compile(r"[A-Za-z0-9+/]+={0,2}")
_HEX_RE = re.compile(r"[0-9A-Fa-f]+")
_WHITESPACE_RE = re.compile(r"\s+")

# Cozulen icerigin bu orani okunabilir metinse blok "kodlanmis metin" sayilir.
_PRINTABLE_TEXT_RATIO = 0.9
# NUL bayt orani: gercek resim/ikon verisinin (hizalama, bos pikseller) izi.
_BINARY_NUL_RATIO = 0.05
# Rastgele base64'te beklenen karakter dagilimi (rakam ~%16, buyuk/kucuk ~%40).
_MIN_DIGIT_RATIO = 0.07
_MIN_CASE_RATIO = 0.15

ENCODED_BLOB_CATEGORY = "KODLANMIS_IKILI_VERI"


# Siniflandirma icin cozer; bastaki tam 4'lu gruplar yeterlidir.
def _decode_base64(compact: str) -> bytes | None:
    body = compact.rstrip("=")
    try:
        return base64.b64decode(body[: len(body) // 4 * 4], validate=True)
    except binascii.Error:
        return None


def _is_readable_text(data: bytes) -> bool:
    try:
        decoded = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    printable = sum(1 for char in decoded if char.isprintable() or char in "\t\r\n")
    return printable >= _PRINTABLE_TEXT_RATIO * max(1, len(decoded))


def _looks_like_random_base64(compact: str) -> bool:
    length = len(compact)
    digits = sum(char.isdigit() for char in compact)
    upper = sum(char.isupper() for char in compact)
    lower = sum(char.islower() for char in compact)
    return (
        digits >= _MIN_DIGIT_RATIO * length
        and upper >= _MIN_CASE_RATIO * length
        and lower >= _MIN_CASE_RATIO * length
    )


def is_encoded_binary(blob: str) -> bool:
    """Blok cozulebilen, metin olmayan ikili veri mi?"""
    compact = _WHITESPACE_RE.sub("", blob)
    if len(compact) % 2 == 0 and _HEX_RE.fullmatch(compact):
        # Hex dokum: identifier'lar hex disi harf icerdigi icin buraya dusmez.
        return not _is_readable_text(bytes.fromhex(compact))
    data = _decode_base64(compact)
    if not data or _is_readable_text(data):
        return False
    if data.count(0) >= _BINARY_NUL_RATIO * len(data):
        return True
    return _looks_like_random_base64(compact)


# Kodlayicilar (resx, PEM, MIME) sabit genislikte satir yazar; yalnizca son
# satir kisa olabilir ve o da "=" dolgusuyla bitmelidir. Boylece bloktan
# hemen sonraki satirdaki bir kelime/identifier (orn. ic proje adi) bloga
# katilip LLM'den gizlenmez. Donus: blogun metindeki bitis ofseti.
def _block_end(text: str, start: int, end: int) -> int:
    lines = list(_LINE_RE.finditer(text, start, end))
    width = len(lines[0].group())
    block_end = lines[0].end()
    for line in lines[1:]:
        length = len(line.group())
        if length == width:
            block_end = line.end()
            continue
        if length < width and line.group().endswith("="):
            block_end = line.end()
        break
    return block_end


# Bloklari bosluga cevirir (satir sonlari korunur): ofsetler degismeden
# analizciye (Presidio) ikili verisiz metin verilir. `spans` sirali ve
# cakismasiz olmalidir (find_encoded_blobs ciktisi).
def blank_spans(text: str, spans: list[tuple[int, int]]) -> str:
    if not spans:
        return text
    parts: list[str] = []
    cursor = 0
    for start, end in spans:
        parts.append(text[cursor:start])
        parts.append(re.sub(r"[^\r\n]", " ", text[start:end]))
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


def find_encoded_blobs(text: str, min_chars: int) -> list[tuple[int, int]]:
    """`min_chars` ve ustu uzunluktaki gomulu ikili veri bloklarinin araliklari."""
    if min_chars <= 0 or len(text) < min_chars:
        return []
    blobs = []
    position = 0
    # Arama blogun gercek sonundan surer: adayin blok disinda kalan satirlari
    # (orn. bir identifier) yeni bir blogun baslangici olabilir.
    while (candidate := _CANDIDATE_RE.search(text, position)) is not None:
        start = candidate.start()
        end = _block_end(text, start, candidate.end())
        if end - start >= min_chars and is_encoded_binary(text[start:end]):
            blobs.append((start, end))
        position = end
    return blobs
