"""Metin dosyalarina gomulu ikili veri (resim, ikon, font, derlenmis kaynak) tespiti.

Bu bloklarda LLM'in/Presidio'nun bulabilecegi bir kisi/kurum adi yoktur, ama
metnin buyuk kismini olusturduklari icin LLM'e onlarca parca halinde gider,
yuksek entropili metin cikti token butcesini kesilmeye (LLMTruncatedError)
surukler ve tek dosya dakikalar surer. Taninan bicimler (dosya turunden
bagimsiz, icerige gore):

- Satir satir base64/hex: .resx `<value>`, PEM govdesi, MIME ekleri.
- Tirnakli/birlestirilmis base64: `"iVBOR..." +` (C#/Java/JS sabitleri),
  JSON dizileri, .ipynb gorsel ciktilari; base64url (`-_`) dahil.
- Tek satirlik uzun base64: data URI (HTML/CSS/SVG/Markdown).
- Bayt dizisi literal'leri: `0x89, 0x50, ...` (C/C#/xxd), `137, 80, ...`
  (Java/C# byte[]), `\\x89\\x50...` (Python/C kacis dizileri).

Guvenlik ilkesi: yalnizca GERCEKTEN ikili veri oldugu kanitlanan bloklar
secilir. Aday blok cozulur; cozulen icerik okunabilir metinse (orn. base64 ya
da bayt dizisiyle gizlenmis bir config/parola) blok SECILMEZ ve LLM'e aynen
gider. Uzun identifier/yol listeleri ikili veriye benzemeyen karakter
dagilimlari nedeniyle elenir; bir blogun hemen yanindaki satir bloga katilmaz.
Bu modul yalnizca LLM/Presidio girdisini daraltir: Katman 1 (sozluk/regex)
bloklari yine tarar ve ciktiya yazilan metin degismez.

Saf fonksiyonlar - DB/ag erisimi yok.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass

from app.services.entropy import shannon_entropy

ENCODED_BLOB_CATEGORY = "KODLANMIS_IKILI_VERI"

# Bir satirdaki base64/base64url/hex kosusu (en az 40 karakter).
_RUN_RE = re.compile(r"[A-Za-z0-9+/_-]{40,}={0,2}")
# Cok satirli blokta kosu disinda yalnizca girinti, tirnak ve birlestirme
# isaretleri (`"...." +`, `'....',`, `@"...."`) bulunabilir.
_WRAPPER_CHARS = frozenset(" \t\r\"'`+,;()[]\\@")
_MAX_WRAPPER_CHARS = 16
# JSON/JS string icindeki satir sonu kacislari (.ipynb: "iVBOR...\n",).
_LITERAL_ESCAPE_RE = re.compile(r"\\[nrt]")
# Blogun kisa son satiri: yalnizca "=" dolgusuyla biten kisa kosu.
_TAIL_RE = re.compile(r"[ \t\"'`@(\[]*([A-Za-z0-9+/_-]{1,39}={1,2})")
_HEX_RE = re.compile(r"[0-9A-Fa-f]+")
_WHITESPACE_RE = re.compile(r"\s+")
# Bayt dizisi literal'leri: 0x.. listesi, ondalik dizi ve \x.. kacislari.
# Ondalik dizi yalnizca dizi literal'i icinde (`{`, `[`, `(` sonrasi) aranir;
# CSV satirlari gibi sayisal veri bayt dizisi sayilmaz.
def _byte_list_patterns(min_items: int) -> tuple[re.Pattern, re.Pattern, re.Pattern]:
    repeat = min_items - 1
    return (
        re.compile(rf"(?:0[xX][0-9A-Fa-f]{{1,2}}\s*,\s*){{{repeat},}}0[xX][0-9A-Fa-f]{{1,2}}"),
        re.compile(rf"[{{\[(]\s*((?:-?\d{{1,3}}\s*,\s*){{{repeat},}}-?\d{{1,3}})(?![\w.])"),
        re.compile(rf"(?:\\x[0-9A-Fa-f]{{2}}){{{min_items},}}"),
    )


# Ikili veri bloklari icin en az 64 bayt; kodlanmis metin (parola vb.) icin 8.
_BLOB_BYTE_LIST_PATTERNS = _byte_list_patterns(64)
_TEXT_BYTE_LIST_PATTERNS = _byte_list_patterns(8)
# Kodlanmis metin adayi: tek basina duran kisa base64/hex kosusu (12-39 karakter,
# orn. `c2E6UGFzc3cwcmQ=` = "sa:Passw0rd"; daha uzunlari satir gruplariyla ele alinir).
_SHORT_RUN_RE = re.compile(r"(?<![A-Za-z0-9+/_=-])[A-Za-z0-9+/_-]{12,39}={0,2}(?![A-Za-z0-9+/_=-])")
_HEX_BYTE_RE = re.compile(r"0[xX]([0-9A-Fa-f]{1,2})")
_DECIMAL_RE = re.compile(r"-?\d{1,3}")

# Cozulen icerigin bu orani okunabilir metinse blok "kodlanmis metin" sayilir.
_PRINTABLE_TEXT_RATIO = 0.9
# NUL bayt orani: gercek resim/ikon verisinin (hizalama, bos pikseller) izi.
_BINARY_NUL_RATIO = 0.05
# Rastgele base64'te beklenen karakter dagilimi (rakam ~%16, buyuk/kucuk ~%40).
_MIN_DIGIT_RATIO = 0.07
_MIN_CASE_RATIO = 0.15


@dataclass(frozen=True)
class _Run:
    start: int
    end: int
    value: str
    continues: bool  # kosudan once yalnizca girinti/tirnak var: bir onceki satirin devami olabilir
    opens: bool      # kosudan sonra yalnizca tirnak/birlestirme var: sonraki satirda surebilir


# --- Siniflandirma -----------------------------------------------------------

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


# Dolgusuz ya da kesik base64'u da cozer (tek artik karakter atilir).
def _decode_base64(compact: str) -> bytes | None:
    body = compact.rstrip("=")
    if len(body) % 4 == 1:
        body = body[:-1]
    body += "=" * (-len(body) % 4)
    try:
        if "-" in body or "_" in body:
            if "+" in body or "/" in body:
                return None  # iki alfabe karisik: base64 degil
            return base64.urlsafe_b64decode(body)
        return base64.b64decode(body, validate=True)
    except (binascii.Error, ValueError):
        return None


def _is_binary_bytes(data: bytes) -> bool:
    return bool(data) and not _is_readable_text(data)


def is_encoded_binary(blob: str) -> bool:
    """Base64/hex blok cozulebilen, metin olmayan ikili veri mi?"""
    compact = _WHITESPACE_RE.sub("", blob)
    if len(compact) % 2 == 0 and _HEX_RE.fullmatch(compact):
        # Hex dokum: identifier'lar hex disi harf icerdigi icin buraya dusmez.
        return _is_binary_bytes(bytes.fromhex(compact))
    data = _decode_base64(compact)
    if not _is_binary_bytes(data or b""):
        return False
    if data.count(0) >= _BINARY_NUL_RATIO * len(data):
        return True
    return _looks_like_random_base64(compact)


# --- Base64/hex kosulari -----------------------------------------------------

def _runs(text: str) -> list[_Run]:
    runs: list[_Run] = []
    for line in re.finditer(r"[^\n]*", text):
        line_text = line.group()
        if len(line_text) < 40:
            continue
        for match in _RUN_RE.finditer(line_text):
            runs.append(_Run(
                line.start() + match.start(), line.start() + match.end(), match.group(),
                continues=_is_wrapper(line_text[: match.start()]),
                opens=_is_wrapper(line_text[match.end():]),
            ))
    return runs


def _is_wrapper(residue: str) -> bool:
    residue = _LITERAL_ESCAPE_RE.sub("", residue)
    return len(residue) <= _MAX_WRAPPER_CHARS and set(residue) <= _WRAPPER_CHARS


# Grubun hemen alt satirinda "=" ile biten kisa son satir (40 karakterden
# kisa oldugu icin kosu sayilmayan `qkvXd19ta/8=</value>` gibi) varsa onu doner.
def _tail_run(text: str, last: _Run, width: int) -> _Run | None:
    if not last.opens:
        return None
    newline = text.find("\n", last.end)
    if newline < 0:
        return None
    match = _TAIL_RE.match(text, newline + 1)
    if match is None or len(match.group(1)) >= width:
        return None
    return _Run(match.start(1), match.end(1), match.group(1), continues=True, opens=False)


def _consecutive_lines(text: str, previous: _Run, current: _Run) -> bool:
    return text.count("\n", previous.end, current.start) == 1


# Kodlayicilar (resx, PEM, MIME, string sabitleri) sabit genislikte satir
# yazar; yalnizca son satir kisa olabilir ve o da "=" dolgusuyla bitmelidir.
# Boylece bloktan hemen sonraki satirdaki bir kelime/identifier (orn. ic proje
# adi) bloga katilip LLM'den gizlenmez.
def _group_runs(text: str, runs: list[_Run]) -> list[list[_Run]]:
    groups: list[list[_Run]] = []
    index = 0
    while index < len(runs):
        group = [runs[index]]
        index += 1
        width = len(group[0].value)
        while (
            index < len(runs) and group[-1].opens and runs[index].continues
            and _consecutive_lines(text, group[-1], runs[index])
        ):
            length = len(runs[index].value)
            if length == width:
                group.append(runs[index])
                index += 1
                continue
            if length < width and runs[index].value.endswith("="):
                group.append(runs[index])
                index += 1
            break
        if not group[-1].value.endswith("="):
            tail = _tail_run(text, group[-1], width)
            if tail is not None and (index >= len(runs) or runs[index].start > tail.start):
                group.append(tail)
        groups.append(group)
    return groups


def _base64_blobs(text: str, min_chars: int) -> list[tuple[int, int]]:
    blobs = []
    for group in _group_runs(text, _runs(text)):
        encoded = "".join(run.value for run in group)
        if len(encoded) >= min_chars and is_encoded_binary(encoded):
            blobs.append((group[0].start, group[-1].end))
    return blobs


# --- Bayt dizisi literal'leri ------------------------------------------------

# (baslangic, bitis, cozulmus baytlar) - cozulemeyen literal atlanir.
def _byte_lists(text: str, patterns: tuple[re.Pattern, re.Pattern, re.Pattern]):
    hex_list, decimal_list, escaped = patterns
    for pattern, parse in (
        (hex_list, lambda s: bytes(int(h, 16) for h in _HEX_BYTE_RE.findall(s))),
        (decimal_list, _decimal_bytes),
        (escaped, lambda s: bytes.fromhex(s.replace("\\x", ""))),
    ):
        for match in pattern.finditer(text):
            group = match.lastindex or 0
            payload = parse(match.group(group))
            if payload is not None:
                yield (*match.span(group), payload)


def _byte_list_blobs(text: str, min_chars: int) -> list[tuple[int, int]]:
    return [
        (start, end) for start, end, payload in _byte_lists(text, _BLOB_BYTE_LIST_PATTERNS)
        if end - start >= min_chars and _is_binary_bytes(payload)
    ]


# Ondalik liste yalnizca tum degerler bayt araligindaysa (-128..255) bayt dizisidir.
def _decimal_bytes(literal: str) -> bytes | None:
    values = [int(value) for value in _DECIMAL_RE.findall(literal)]
    if not all(-128 <= value <= 255 for value in values):
        return None
    return bytes(value & 0xFF for value in values)


# --- Genel arayuz ------------------------------------------------------------

def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def find_encoded_blobs(text: str, min_chars: int) -> list[tuple[int, int]]:
    """`min_chars` ve ustu uzunluktaki gomulu ikili veri bloklarinin sirali,
    cakismasiz araliklari."""
    if min_chars <= 0 or len(text) < min_chars:
        return []
    return _merge(_base64_blobs(text, min_chars) + _byte_list_blobs(text, min_chars))


# --- Kodlanmis metin (gizlenmis parola, connection string) ------------------

_MIN_DECODED_TEXT_CHARS = 6
_MIN_DECODED_LETTERS = 4
_MIN_ASCII_RATIO = 0.8


@dataclass(frozen=True)
class EncodedTextBlock:
    """Cozuldugunde okunabilir metin veren kodlanmis blok (base64/hex/bayt dizisi)."""

    start: int
    end: int
    kind: str
    decoded: str


# Kodlanmis metin adayi: TAMAMI yazdirilabilir olmali (ikili veri olcutu
# _is_readable_text'ten daha siki - kisa kosularda rastlantisal cozumleri eler).
def _decoded_text(data: bytes | None) -> str | None:
    if not data or not _is_readable_text(data):
        return None
    decoded = data.decode("utf-8")
    if not all(char.isprintable() or char in "\t\r\n" for char in decoded):
        return None
    if len(decoded) < _MIN_DECODED_TEXT_CHARS or sum(char.isalpha() for char in decoded) < _MIN_DECODED_LETTERS:
        return None
    if sum(char.isascii() for char in decoded) < _MIN_ASCII_RATIO * len(decoded):
        return None
    return decoded


def _decode_run(compact: str) -> tuple[str, bytes | None]:
    if len(compact) % 2 == 0 and _HEX_RE.fullmatch(compact):
        return "hex", bytes.fromhex(compact)
    return "base64", _decode_base64(compact)


def find_encoded_text_blocks(text: str) -> list[EncodedTextBlock]:
    """Cozuldugunde okunabilir metin veren kodlanmis bloklar.

    Bu bloklar find_encoded_blobs tarafindan bilerek gizlenmez; icerikleri
    metin katmanlarinca gorulemez (`Password=` base64'te `UGFzc3dvcmQ9` olur).
    Cagiran (EncodedTextDetector) cozulmus metni ayrica tarar. Cozulmus metin
    asla loglanmamalidir.
    """
    blocks: list[EncodedTextBlock] = []
    for group in _group_runs(text, _runs(text)):
        kind, data = _decode_run(_WHITESPACE_RE.sub("", "".join(run.value for run in group)))
        if (decoded := _decoded_text(data)) is not None:
            blocks.append(EncodedTextBlock(group[0].start, group[-1].end, kind, decoded))
    for match in _SHORT_RUN_RE.finditer(text):
        kind, data = _decode_run(match.group())
        if (decoded := _decoded_text(data)) is not None:
            blocks.append(EncodedTextBlock(match.start(), match.end(), kind, decoded))
    for start, end, payload in _byte_lists(text, _TEXT_BYTE_LIST_PATTERNS):
        if (decoded := _decoded_text(payload)) is not None:
            blocks.append(EncodedTextBlock(start, end, "bayt_dizisi", decoded))
    return sorted(blocks, key=lambda block: block.start)


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


# --- Erken uyari -------------------------------------------------------------

_SUSPICIOUS_MIN_LINE_CHARS = 200
_SUSPICIOUS_ALPHABET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=_-")
_SUSPICIOUS_ALPHABET_RATIO = 0.95
_SUSPICIOUS_MIN_ENTROPY = 4.5


def count_unrecognized_encoded_lines(text: str) -> int:
    """Gizlenmemis ama kodlanmis veriye benzeyen uzun satir sayisi.

    find_encoded_blobs'un tanimadigi yeni bir bicimin (ya da okunabilir metne
    cozuldugu icin bilincli olarak gizlenmeyen base64'un) erken uyarisidir:
    bu satirlar LLM'e gider ve dosyanin taranmasini yavaslatir. Icerik degil,
    yalnizca sayi raporlanir.
    """
    count = 0
    for line in text.splitlines():
        stripped = line.strip()
        if len(stripped) < _SUSPICIOUS_MIN_LINE_CHARS:
            continue
        in_alphabet = sum(char in _SUSPICIOUS_ALPHABET for char in stripped)
        if (
            in_alphabet >= _SUSPICIOUS_ALPHABET_RATIO * len(stripped)
            and shannon_entropy(stripped) >= _SUSPICIOUS_MIN_ENTROPY
        ):
            count += 1
    return count
