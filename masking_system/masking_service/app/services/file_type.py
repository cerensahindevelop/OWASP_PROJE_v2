"""Content-based text/binary classification - deliberately NOT extension
based, so a script with no extension or a mislabeled ".txt" that's actually
a zip file are both classified correctly.

Heuristic mirrors what git/most diff tools use: a chunk containing a NUL
byte, or with too high a ratio of non-printable bytes, is binary. On top of
that we ask charset-normalizer to confirm a plausible text encoding - a
file can pass the printable-ratio check yet still not be valid text in any
common encoding (e.g. a corrupted stream), which we don't want to blindly
mask.

BOM/wide-encoding handling (bkz. _detect_bom_encoding/_WIDE_ENCODINGS): bir
UTF-16/UTF-32 metin dosyasi, ASCII karakterlerin her ikinci/dorduncu
byte'inin \\x00 olmasi nedeniyle YUKARIDAKI "NUL byte -> binary" testini
HER ZAMAN tetikler - bu, eskiden bu encoding'lerdeki HER metin dosyasinin
(BOM'lu ya da BOM'suz) hic taranmadan, sessizce binary olarak kopyalanmasina
yol aciyordu (gercek dosyalarla dogrulanmis bug). Duzeltme iki asamali:
(1) taniniyorsa BOM imzasi (UTF-8/16/32) NUL kontrolunden ONCE, kesin bir
sinyal olarak kontrol edilir; (2) BOM yoksa ama ornekte NUL byte varsa,
NUL'u "binary" saymadan ONCE charset_normalizer'in bunun GERCEKTEN BOM'suz
bir genis-encoding (utf_16_le/be, utf_32_le/be) oldugunu yapisal olarak
(alternan NUL deseni) dogrulayip dogrulamadigina bakilir - gercek binary
veride (ELF/PNG/random byte) bu yapisal desen olusmadigindan yanlis-pozitif
vermez (bkz. tests/test_file_type.py).
"""

from __future__ import annotations

from pathlib import Path
import codecs

from app.services import bom_codecs  # Register persistent, endian-specific BOM codecs.

# charset_normalizer: printable-ratio kontrolunu gecen bir byte orneginin
# gercekten hangi metin encoding'iyle (utf-8, latin-1 vb.) uyumlu oldugunu
# tahmin etmek icin kullanilir - saf regex/heuristic'ten daha guvenilir.
from charset_normalizer import from_bytes

PEEK_SIZE = 8192
_NONTEXT_RATIO_THRESHOLD = 0.30

# Tespit/peek basarisiz oldugunda denenecek Turkce eski kodlamalar.
# app/core/config.py EncodingSettings (LEGACY_TEXT_ENCODINGS) varsayilaniyla
# AYNI olmali (bkz. tests/test_legacy_encoding.py).
DEFAULT_LEGACY_TEXT_ENCODINGS: tuple[str, ...] = ("cp1254", "iso-8859-9")

# charset_normalizer cp1254 metni siklikla cp1250/cp1252/cp1257/cp850 olarak
# tahmin eder: ayni baytlar s-cedilla yerine t-cedilla, noktasiz i yerine
# y-acute olarak cozulur ve Turkce sozluk terimleri/isimler eslesmez (sizinti
# riski). Bu harfler cp1254'e OZGU bayt degerlerine denk gelir; tek baytlik
# bir Latin tahmininde bu baytlar varsa dosya buyuk olasilikla cp1254'tur.
_TURKISH_PREFERRED_ENCODING = "cp1254"
_TURKISH_SPECIFIC_LETTERS = frozenset("şğıİŞĞ")

# Latin DISI tahminler (big5, johab, cp1006, cp1251...) icin daha siki kosul.
# Olcum: gercek Cince/Korece/Rusca metin cp1254 ile cozulunce 6-12 Turkce'ye
# ozgu harf verebiliyor, ama ASCII-disi karakterlerin en fazla ~%30'u Turk
# alfabesinden; gercek Turkce metinde bu oran %100. Bu yuzden hem sayi hem
# oran sart: tek bir harfe bakarak karar verilmez.
_TURKISH_ALPHABET_NON_ASCII = frozenset("şğıİŞĞçöüÇÖÜâîûÂÎÛ")
_MIN_TURKISH_LETTERS_FOR_NON_LATIN = 3
_MIN_TURKISH_ALPHABET_RATIO_FOR_NON_LATIN = 0.8

# Asla cp1254'e cevrilmeyen, zaten Turkce olan DOS/Mac kodlamalari. utf-8
# ailesi ve BOM'lu (-sig) codec'ler de hic cevrilmez (bkz. prefer_turkish_encoding).
_NEVER_OVERRIDDEN_ENCODINGS = frozenset({"cp857", "mac-turkish"})

# En uzun/en spesifik once: UTF-32 BOM'lari, UTF-16 BOM'larinin (\xff\xfe /
# \xfe\xff) bir uzantisi oldugundan ONCE kontrol edilmeli, yoksa bir UTF-32
# dosyasi yanlislikla UTF-16 sanilir. -sig codec'leri BOM'u decode'da
# atar, encode'da AYNI byte sirasiyla ekler; host'un byte sirasini kullanmaz.
_BOM_SIGNATURES: list[tuple[bytes, str]] = [
    (b"\x00\x00\xfe\xff", "utf-32-be-sig"),
    (b"\xff\xfe\x00\x00", "utf-32-le-sig"),
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe", "utf-16-le-sig"),
    (b"\xfe\xff", "utf-16-be-sig"),
]

# charset_normalizer'in bir BOM'suz ornekte tespit edebilecegi "genis"
# (wide) encoding adlari - bunlar disinda bir sonuc, NUL-agirlikli bir
# ornegi asla binary-degil sayacak sekilde KABUL EDILMEZ (bkz. modul
# dokstring'i, 2. adim).
_WIDE_ENCODINGS = {"utf_16", "utf_16_le", "utf_16_be", "utf_32", "utf_32_le", "utf_32_be"}


# Bir byte orneginin taniniyorsa (BOM imzasiyla) hangi encoding'i isaret ettigini dondurur.
def _detect_bom_encoding(sample: bytes) -> str | None:
    for signature, encoding in _BOM_SIGNATURES:
        if sample.startswith(signature):
            return encoding
    return None


# Tek baytlik Latin kodlamalari (codecs.lookup(...).name bicimiyle). cp1251/
# cp1253/cp1255/cp1256 ve iso8859-5/6/7/8/11 Latin DEGIL (Kiril, Yunan,
# Ibrani, Arap, Tay) - bunlarda Turkce tercihi anlamsizdir. mac/hp/DOS Latin
# varyantlari da dahil: charset_normalizer kisa cp1254 dosyalari icin
# mac_latin2/hp_roman8 da tahmin edebiliyor (olculdu). Zaten Turkce olan
# cp857/mac-turkish kasitli olarak YOK - gercek bir DOS/Mac Turkce dosyayi
# cp1254'e cevirmek yanlis olur.
_SINGLE_BYTE_LATIN_ENCODINGS = frozenset({
    "cp1250", "cp1252", "cp1254", "cp1257", "cp1258",
    "cp437", "cp850", "cp852", "cp858",
    "iso8859-1", "iso8859-2", "iso8859-3", "iso8859-4", "iso8859-9",
    "iso8859-10", "iso8859-13", "iso8859-14", "iso8859-15", "iso8859-16",
    "mac-roman", "mac-latin2", "mac-iceland", "mac-croatian", "mac-romanian", "hp-roman8",
})


# Kodlama adinin tek baytlik bir Latin kodlamasi olup olmadigini soyler.
def _is_single_byte_latin(encoding: str) -> bool:
    try:
        return codecs.lookup(encoding).name in _SINGLE_BYTE_LATIN_ENCODINGS
    except LookupError:
        return False


# cp1254 ile cozulmus metin, Latin DISI bir tahmini gecersiz kilacak kadar
# acikca Turkce mi: yeterli sayida Turkce'ye ozgu harf VE ASCII-disi
# karakterlerin buyuk cogunlugu Turk alfabesinden.
def _is_clearly_turkish(decoded: str) -> bool:
    non_ascii = [char for char in decoded if ord(char) > 127]
    if not non_ascii:
        return False
    specific = sum(char in _TURKISH_SPECIFIC_LETTERS for char in non_ascii)
    alphabet = sum(char in _TURKISH_ALPHABET_NON_ASCII for char in non_ascii)
    return (specific >= _MIN_TURKISH_LETTERS_FOR_NON_LATIN
            and alphabet / len(non_ascii) >= _MIN_TURKISH_ALPHABET_RATIO_FOR_NON_LATIN)


# Tahmin edilen kodlama yerine cp1254'u tercih etmeli mi karar verir:
# - tek baytlik Latin tahmin: cp1254'te Turkce harfe denk gelen bayt yeterli
#   (bu kodlamalar yalnizca bu baytlarda ayrisir);
# - Latin DISI tahmin (CJK, Kiril, Arap...): yalnizca metin acikca Turkceyse
#   (bkz. _is_clearly_turkish). Turkce dosyayi Cince sanmak sizinti demektir;
#   Cince dosyayi Turkce okumak ise baytlari bozmaz (bayt esitligi korunur);
# - utf-8, BOM'lu codec'ler, NUL iceren UTF-16/32 ve zaten Turkce kodlamalar: asla.
# Baytlar cp1254'te cozulemiyorsa (tanimsiz 0x81/0x8D/... baytlari) tahmin korunur.
def prefer_turkish_encoding(data: bytes, encoding: str | None) -> str | None:
    if not encoding:
        return encoding
    try:
        # bom_codecs adlari alt cizgiyle kaydeder (utf_16_le_sig): normalize et.
        name = codecs.lookup(encoding).name.replace("_", "-")
    except LookupError:
        return encoding
    if (name == _TURKISH_PREFERRED_ENCODING or name in _NEVER_OVERRIDDEN_ENCODINGS
            or name.startswith("utf-8") or name.endswith("-sig")):
        return encoding
    # BOM'suz UTF-16/32 kaynak kod ASCII karakterleri yuzunden HER ZAMAN NUL
    # bayti icerir. NUL'suz bir ornekte bu tahmin guvenilmez (olculdu:
    # charset_normalizer bazi cp1254 metinleri utf_16_le/be sanıyor); o zaman
    # diger Latin-disi tahminler gibi siki kurala tabidir.
    if name.startswith(("utf-16", "utf-32")) and b"\x00" in data:
        return encoding
    try:
        decoded = data.decode(_TURKISH_PREFERRED_ENCODING, errors="strict")
    except UnicodeDecodeError:
        return encoding
    if _is_single_byte_latin(encoding):
        turkish = bool(_TURKISH_SPECIFIC_LETTERS.intersection(decoded))
    else:
        turkish = _is_clearly_turkish(decoded)
    return _TURKISH_PREFERRED_ENCODING if turkish else encoding


# Tum dosya baytlari uzerinden charset_normalizer tahmini (Turkce tercihiyle).
# Pahalidir; yalnizca ucuz adaylar basarisiz olduktan sonra cagrilir.
def guess_full_text_encoding(data: bytes) -> str | None:
    best = from_bytes(data).best()
    if best is None or not _looks_like_text(str(best)):
        return None
    return prefer_turkish_encoding(data, best.encoding)


# Bir byte ornegine bakip metin mi binary mi oldugunu (ve encoding'ini) tahmin eder.
def classify_bytes(sample: bytes) -> tuple[bool, str | None]:
    """Returns (is_text, detected_encoding). detected_encoding is None when
    is_text is False."""
    if not sample:
        return True, "utf-8"  # empty file: nothing to mask, treat as text

    bom_encoding = _detect_bom_encoding(sample)
    if bom_encoding is not None:
        return True, bom_encoding

    if b"\x00" in sample:
        # Bkz. modul dokstring'i: NUL byte tek basina "binary" anlamina
        # gelmez - BOM'suz UTF-16/UTF-32 metin de dogasi geregi NUL
        # agirliklidir. charset_normalizer'in bunu YAPISAL olarak (rastgele
        # NUL degil, tutarli alternan desen) genis-encoding olarak
        # dogrulayip dogrulamadigina bakiyoruz; dogrulamazsa (gercek
        # binary/rastgele veri) eski davranis (binary) korunur.
        best = from_bytes(sample).best()
        if best is not None and best.encoding in _WIDE_ENCODINGS:
            return True, best.encoding
        return False, None

    # Inspect decoded characters, not UTF-8 continuation bytes. A peek may
    # end halfway through a Unicode character; full-file decoding remains
    # strict in read_scanned_file.
    try:
        decoded = codecs.getincrementaldecoder("utf-8")().decode(sample, final=False)
    except UnicodeDecodeError:
        decoded = None
    if decoded is not None:
        return (_looks_like_text(decoded), "utf-8" if _looks_like_text(decoded) else None)

    best = from_bytes(sample).best()
    if best is None:
        return False, None
    if not _looks_like_text(str(best)):
        return False, None
    return True, prefer_turkish_encoding(sample, best.encoding)


def _looks_like_text(text: str) -> bool:
    if not text:
        return True
    controls = sum(not (char.isprintable() or char in "\n\r\t\b\f\x1b") for char in text)
    return controls / len(text) <= _NONTEXT_RATIO_THRESHOLD


# Bir dosyanin sadece ilk PEEK_SIZE byte'ini okuyup classify_bytes ile
# siniflandirir - cok buyuk dosyalarda bile bellek guvenlidir.
def peek_classify(path: Path) -> tuple[bool, str | None]:
    """Reads only the first PEEK_SIZE bytes of `path` - safe to call on
    arbitrarily large files."""
    with open(path, "rb") as f:
        sample = f.read(PEEK_SIZE)
    return classify_bytes(sample)


# Metni, mumkunse orijinal encoding'iyle yazar; sigmazsa UTF-8'e duser. Kullanilan encoding'i dondurur.
#
# newline="" ZORUNLU: metin zaten kaynak dosyadan HAM byte olarak okunup
# decode edilmisti (bkz. file_pipeline.py) - satir sonlari (\r\n, \n) hicbir
# donusturme gecirmeden text icinde AYNEN duruyor. write_text'in varsayilani
# (newline=None) platforma gore evrensel satir-sonu CEVIRISI yapar: Windows'ta
# os.linesep == "\r\n" oldugundan, text icindeki HER "\n" (CRLF'in kendi
# parcasi dahil) "\r\n"ye cevrilir - "\r\n" -> "\r\r\n" olur, satir basina 1
# fazladan bayt eklenir. Cok satirli bir dosyada bu, yazilan boyutu orijinal
# uzunluktan farkli hale getirir ve roundtrip_validator.py'nin SHA-256/uzunluk
# karsilastirmasini SESSIZCE bozar (Linux'ta os.linesep zaten "\n" oldugundan
# bu hata bu platformda hic tetiklenmez - testler Linux'ta calistigi icin
# yakalanmadi, sadece gercek Windows dagitiminda ortaya cikti).
def write_text_preserving_encoding(path: Path, text: str, encoding: str | None, *, class_document=None) -> str:
    from app.services.java_classfile import JAVA_CLASS_ENCODING, parse_class
    if encoding == JAVA_CLASS_ENCODING:
        document = class_document if class_document is not None else parse_class(path.read_bytes())
        data = document.rebuild(text)
        path.write_bytes(data)
        return JAVA_CLASS_ENCODING
    chosen = encoding or "utf-8"
    try:
        path.write_text(text, encoding=chosen, newline="")
        return chosen
    except UnicodeEncodeError:
        path.write_text(text, encoding="utf-8", newline="")
        return "utf-8"
