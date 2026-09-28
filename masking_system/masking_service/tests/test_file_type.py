"""app/services/file_type.py icin birim testler - ozellikle encoding
tespitindeki kok neden duzeltmesi: BOM'lu/BOM'suz UTF-16/UTF-32 metin
dosyalari eskiden (NUL byte iceriyor diye) binary sanilip HIC
taranmadan/maskelenmeden kopyalaniyordu. Bu dosya hem duzeltmenin
calistigini hem de gercek binary icerigin YANLIS-POZITIF "metin"
sayilmadigini (regresyon koruyucusu) dogrular.
"""

from __future__ import annotations

import os
import pytest

from app.services.file_type import classify_bytes, write_text_preserving_encoding


@pytest.mark.parametrize("text", ["şğİıöüç" * 20, "中文变量与文档" * 20, "العربية" * 20, "😀" * 20])
def test_unicode_heavy_utf8_is_text(text):
    assert classify_bytes(text.encode("utf-8")) == (True, "utf-8")


@pytest.mark.parametrize("encoding,bom", [
    ("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff"),
    ("utf-32-le", b"\xff\xfe\x00\x00"), ("utf-32-be", b"\x00\x00\xfe\xff"),
])
def test_bom_byte_order_survives_real_read_write(tmp_path, encoding, bom):
    original = bom + "şğİ 中文\r\nvalue = 1\n".encode(encoding)
    is_text, detected = classify_bytes(original)
    assert is_text
    text = original.decode(detected)
    assert not text.startswith("\ufeff")
    destination = tmp_path / "roundtrip"
    write_text_preserving_encoding(destination, text, detected)
    assert destination.read_bytes() == original


def test_utf8_peek_ending_mid_character_is_not_misclassified():
    raw = b"a" * 8191 + "ş".encode("utf-8")
    assert classify_bytes(raw[:8192]) == (True, "utf-8")


# Windows'a ozgu regresyon: write_text_preserving_encoding newline="" GECMEDEN
# path.write_text() cagirirsa, Windows'ta (os.linesep == "\r\n") varsayilan
# evrensel satir-sonu YAZMA cevirisi metindeki HER "\n"i (zaten var olan bir
# "\r\n" CRLF'in kendi parcasi dahil) tekrar "\r\n"ye cevirir - "\r\n" ->
# "\r\r\n" olur, satir basina fazladan bir bayt eklenir. Bu, export sonrasi
# roundtrip_validator.py'nin SHA-256/uzunluk karsilastirmasini SESSIZCE
# bozar (gercek intranet/Windows dagitiminda goruldu). Bu platformdan
# BAGIMSIZ calisir: write_text_preserving_encoding'in os.linesep'e gore
# HICBIR karakteri degistirmemesi, byte-birebir yazmasi gerektigini dogrudan
# dogrular (gercek os.linesep'in "\n" oldugu Linux'ta bu regresyon hic
# tetiklenmezdi - test bunu os.linesep'e bakmadan garanti eder).
def test_write_text_preserving_encoding_does_not_mangle_crlf_line_endings(tmp_path):
    text = "line1\r\nline2\r\nline3\r\n"
    dest = tmp_path / "crlf.txt"
    write_text_preserving_encoding(dest, text, "utf-8")
    assert dest.read_bytes() == text.encode("utf-8")


def test_write_text_preserving_encoding_does_not_mangle_bare_lf_line_endings(tmp_path):
    text = "line1\nline2\nline3\n"
    dest = tmp_path / "lf.txt"
    write_text_preserving_encoding(dest, text, "utf-8")
    assert dest.read_bytes() == text.encode("utf-8")


# Yukaridaki iki test, os.linesep zaten "\n" olan Linux CI'da write_text'in
# varsayilan (newline=None) evrensel satir-sonu cevirisi "\n" -> "\n" (no-op)
# oldugu icin bu platformda kirilmayi YAKALAYAMAZ - hata SADECE os.linesep'in
# "\r\n" oldugu Windows'ta ortaya cikar. Platformdan tamamen bagimsiz,
# dogrudan bir regresyon koruyucusu icin write_text'in fiilen newline=""
# ile cagrildigini (herhangi bir cevirinin ISTEMLI olarak KAPALI oldugunu)
# dogrudan dogruluyoruz.
def test_write_text_preserving_encoding_passes_newline_empty_to_disable_translation(tmp_path, monkeypatch):
    from pathlib import Path

    calls = []
    original_write_text = Path.write_text

    def spy_write_text(self, data, encoding=None, errors=None, newline=None):
        calls.append(newline)
        return original_write_text(self, data, encoding=encoding, errors=errors, newline=newline)

    monkeypatch.setattr(Path, "write_text", spy_write_text)
    write_text_preserving_encoding(tmp_path / "out.txt", "irrelevant", "utf-8")
    assert calls == [""]


def test_utf8_bom_is_detected_as_text_with_bom_stripped_on_decode():
    sample = "Poseidon projesi - dahili not".encode("utf-8-sig")

    is_text, encoding = classify_bytes(sample)

    assert is_text is True
    assert sample.decode(encoding) == "Poseidon projesi - dahili not"


def test_utf16_with_bom_is_detected_as_text_not_binary():
    """Kok neden: eski classify_bytes, ilk NUL byte'ta hemen binary
    donuyordu - UTF-16'nin ASCII karakterleri icin HER ikinci byte 0x00'dir,
    yani BU testin verisi eskiden HER ZAMAN (yanlislikla) binary sayilirdi."""
    text = "SECRET_KEY = 'abc123'\nPoseidon Teknoloji A.S. dahili config dosyasi\n"
    for encoding in ("utf-16", "utf-16-le", "utf-16-be"):
        sample = text.encode(encoding)

        is_text, detected_encoding = classify_bytes(sample)

        assert is_text is True, f"{encoding} icin metin binary sanildi"
        assert sample.decode(detected_encoding) == text, encoding


def test_utf32_with_bom_is_detected_as_text_not_binary():
    text = "Atlas Core dahili servis anahtari: XYZ-999\n"
    for encoding in ("utf-32", "utf-32-le", "utf-32-be"):
        sample = text.encode(encoding)

        is_text, detected_encoding = classify_bytes(sample)

        assert is_text is True, f"{encoding} icin metin binary sanildi"
        assert sample.decode(detected_encoding) == text, encoding


def test_utf16_without_bom_is_still_detected_as_text():
    """BOM olmasa bile (bazi araclar/legacy sistemler BOM eklemez), yapisal
    NUL deseni charset_normalizer ile dogrulanip metin olarak taninmali."""
    text = "Zeus internal servisi - musteri kodu 77321"
    for encoding in ("utf-16-le", "utf-16-be"):
        sample = text.encode(encoding)

        is_text, detected_encoding = classify_bytes(sample)

        assert is_text is True, encoding
        assert sample.decode(detected_encoding) == text


def test_plain_utf8_text_still_detected_as_before():
    sample = "SECRET = 'abc'\n".encode("utf-8")

    is_text, encoding = classify_bytes(sample)

    assert is_text is True
    assert sample.decode(encoding) == "SECRET = 'abc'\n"


def test_short_control_byte_prefixed_blob_is_still_binary_not_utf16():
    """Regresyon koruyucusu: NUL-heuristigi tamamen kaldirmak yerine SADECE
    charset_normalizer'in gercekten genis-encoding (utf-16/32) yapisal
    desenini dogruladigi durumlarda devre disi birakiliyoruz - rastgele
    kontrol byte'lari + duz ASCII metin karisimi (gercek 'binary-ish'
    dosyalarda gorulen desen) hala binary sayilmali."""
    sample = b"\x00\x01\x02binary-ish-content"

    is_text, encoding = classify_bytes(sample)

    assert is_text is False
    assert encoding is None


def test_real_elf_binaries_are_not_misclassified_as_text():
    """charset_normalizer'in gercek binary veride wide-encoding'i yanlislikla
    dogrulamadigini gercek ELF dosyalariyla kanitlar (bulunamazsa atlanir)."""
    candidates = []
    for root in ("/usr/bin", "/bin"):
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root)[:100]:
            path = os.path.join(root, name)
            if os.path.isfile(path) and not os.path.islink(path):
                candidates.append(path)

    tested = 0
    for path in candidates:
        try:
            with open(path, "rb") as f:
                head = f.read(4)
                if head != b"\x7fELF":
                    continue
                f.seek(0)
                sample = f.read(8192)
        except OSError:
            continue
        is_text, _ = classify_bytes(sample)
        assert is_text is False, f"gercek ELF binary'si yanlislikla metin sayildi: {path}"
        tested += 1
        if tested >= 20:
            break

    if tested == 0:
        import pytest

        pytest.skip("test ortaminda ELF binary bulunamadi")


def test_random_binary_bytes_are_not_misclassified_as_text():
    sample = os.urandom(4096)

    is_text, _ = classify_bytes(sample)

    assert is_text is False


def test_empty_sample_is_treated_as_text():
    assert classify_bytes(b"") == (True, "utf-8")
