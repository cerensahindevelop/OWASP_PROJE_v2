"""Turkce eski kodlamalarin (cp1254/iso-8859-9) dogru okunmasi.

Iki kok neden:
  1) charset_normalizer cp1254 dosyalari cp1250/cp850/cp1257 olarak tahmin
     ediyor; s->t-cedilla, i(noktasiz)->y-acute olunca Turkce sozluk terimleri ve
     isimler eslesmiyor (sizinti riski).
  2) Tespit yalnizca ilk PEEK_SIZE bayta bakiyor; ilk 8 KB ASCII ise "utf-8"
     deniyor, ilerideki cp1254 baytlari yuzunden dosya COPIED_UNDECODABLE
     olup ciktidan dusuyordu.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from app.core.config import EncodingSettings
from app.services import file_pipeline
from app.services.file_pipeline import ReadStatus, read_scanned_file
from app.services.file_type import DEFAULT_LEGACY_TEXT_ENCODINGS, PEEK_SIZE, classify_bytes, prefer_turkish_encoding
from app.services.scanner import ScannedFile

_TURKISH = (
    "// Müşteri kaydı: ağ bağlantısı düşürülmeyecek\n"
    "// İstanbul şubesi Şirket içi güncelleme\n"
    "public class Musteri {\n"
    '    String ad = "Müşteri Işık Ğ";\n'
    "}\n"
)


def _scanned(path: Path, root: Path) -> ScannedFile:
    return ScannedFile(absolute_path=path, relative_path=path.relative_to(root), is_symlink=False, excluded_by=None)


def _read(tmp_path: Path, name: str, raw: bytes, **kwargs):
    src = tmp_path / "src" / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(raw)
    return read_scanned_file(_scanned(src, tmp_path / "src"), tmp_path / "dst" / name, 1_000_000,
                             copy_unscannable=False, **kwargs)


def test_pure_cp1254_file_is_read_as_cp1254(tmp_path):
    raw = _TURKISH.encode("cp1254")

    outcome = _read(tmp_path, "Musteri.java", raw)

    assert outcome.status == ReadStatus.TEXT_READY
    assert outcome.encoding == "cp1254"
    assert outcome.text == _TURKISH
    assert "Müşteri" in outcome.text


def test_cp1254_tail_after_ascii_peek_is_text_ready(tmp_path):
    head = ("x = 1\n" * (10 * 1024 // 6 + 1)).encode("ascii")
    assert len(head) > PEEK_SIZE
    tail = "// Müşteri şifresi değiştirilmeyecek\n".encode("cp1254")

    outcome = _read(tmp_path, "config.py", head + tail)

    assert outcome.status == ReadStatus.TEXT_READY
    assert outcome.encoding == "cp1254"
    assert outcome.text.endswith("// Müşteri şifresi değiştirilmeyecek\n")
    assert outcome.encoding_fallback is False


def test_last_resort_latin1_preserves_bytes_and_is_flagged(tmp_path, monkeypatch):
    # Hicbir aday kodlamanin (bos legacy listesi, utf-8) ve tum-dosya
    # tahmininin cozemedigi metin: latin-1 her bayti birebir korur.
    monkeypatch.setattr(file_pipeline, "guess_full_text_encoding", lambda raw: None)
    raw = ("y = 2\n" * 2000).encode("ascii") + b"# \x81\x8d\x9d end\n"

    outcome = _read(tmp_path, "notes.py", raw, legacy_encodings=())

    assert outcome.status == ReadStatus.TEXT_READY
    assert outcome.encoding == "latin-1"
    assert outcome.encoding_fallback is True
    assert outcome.text.encode("latin-1") == raw


def test_latin1_is_not_used_for_forced_encoding_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(file_pipeline, "guess_full_text_encoding", lambda raw: None)
    raw = b"# \x81\x8d\x9d\xff end\n"

    outcome = _read(tmp_path, "notes.py", raw, legacy_encodings=(), encoding_hint="utf-8")

    assert outcome.status == ReadStatus.COPIED_UNDECODABLE


@pytest.mark.parametrize("raw", [
    b"\xef\xbb\xbfbroken utf8\xff",
    ("x = 1\n" * 2000).encode() + "// Müşteri\n".encode("utf-8") + b"\xff\n",
])
def test_broken_utf8_is_never_reread_as_legacy(tmp_path, raw):
    # Gecerli UTF-8 Turkce harfler cp1254/latin-1 ile okunursa bozulur
    # ("Müşteri" -> "MÃ¼ÅŸteri") ve terim eslesmesi kacar: blokla.
    outcome = _read(tmp_path, "broken.txt", raw)

    assert outcome.status == ReadStatus.COPIED_UNDECODABLE


def test_latin1_is_not_used_when_peek_says_binary(tmp_path):
    raw = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(range(256)) * 8

    outcome = _read(tmp_path, "image.dat", raw)

    assert outcome.status == ReadStatus.COPIED_BINARY


def test_random_bytes_stay_binary(tmp_path):
    raw = random.Random(1234).randbytes(16 * 1024)

    outcome = _read(tmp_path, "blob.dat", raw)

    assert outcome.status == ReadStatus.COPIED_BINARY


@pytest.mark.parametrize("wrong", ["cp1250", "cp1252", "cp1257", "cp1258", "cp850", "iso8859_16", "latin_1",
                                   "mac_latin2", "hp_roman8"])
def test_single_byte_latin_guess_prefers_cp1254_for_turkish_letters(wrong):
    assert prefer_turkish_encoding(_TURKISH.encode("cp1254"), wrong) == "cp1254"


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16-le-sig", "cp857", "mac_turkish"])
def test_unicode_or_turkish_guess_is_left_alone(encoding):
    assert prefer_turkish_encoding(_TURKISH.encode("cp1254"), encoding) == encoding


@pytest.mark.parametrize("encoding", ["utf_16_le", "utf_16_be", "utf_32_le"])
def test_wide_guess_without_nul_prefers_cp1254_for_turkish_text(encoding):
    # Olculdu: charset_normalizer bazi cp1254 metinleri BOM'suz utf_16 saniyor.
    assert prefer_turkish_encoding(_TURKISH.encode("cp1254"), encoding) == "cp1254"


@pytest.mark.parametrize("encoding", ["utf_16_le", "utf_16_be", "utf_32_le"])
def test_real_wide_text_keeps_its_encoding(encoding):
    assert prefer_turkish_encoding(_TURKISH.encode(encoding), encoding) == encoding


def test_latin_guess_without_turkish_letters_is_left_alone():
    # Almanca cp1252 metin: cp1254'te de ayni harflere cozulur, Turkce harf yok.
    raw = "Größe über Straße\n".encode("cp1252")
    assert prefer_turkish_encoding(raw, "cp1252") == "cp1252"


def test_classify_bytes_prefers_cp1254_for_turkish_sample():
    assert classify_bytes(_TURKISH.encode("cp1254")) == (True, "cp1254")


def test_config_default_matches_pipeline_default(monkeypatch):
    monkeypatch.delenv("LEGACY_TEXT_ENCODINGS", raising=False)
    assert EncodingSettings(_env_file=None).legacy_text_encoding_list == DEFAULT_LEGACY_TEXT_ENCODINGS


def test_config_rejects_unknown_encoding(monkeypatch):
    monkeypatch.setenv("LEGACY_TEXT_ENCODINGS", "cp1254,no-such-codec")
    with pytest.raises(ValueError):
        EncodingSettings(_env_file=None)


_TR_SENSITIVE = "ŞİRKETİÇİ GÜVENLİK PLATFORMU"


def test_export_masks_turkish_value_in_cp1254_file_and_restores(db_session, monkeypatch, tmp_path):
    from tests.test_consistency_masking import _run_export_and_restore, _tree_bytes

    legacy = f'// Müşteri kaydı\nString p = "{_TR_SENSITIVE}";\n'
    tail_only = ("a = 1\n" * 2000) + f"# {_TR_SENSITIVE} ağ şifresi\n"
    files = [
        ("Seed.java", f'final String project = "{_TR_SENSITIVE}";\n', "utf-8"),
        ("Legacy.java", legacy, "cp1254"),
        ("tail.py", tail_only, "cp1254"),
    ]
    source, target, restored, report, _ = _run_export_and_restore(
        db_session, monkeypatch, tmp_path, files, seed_values=[_TR_SENSITIVE],
    )

    assert report.files_failed_consistency_validation == 0, report.summary_text()
    for name in ("Legacy.java", "tail.py"):
        output = (target / name).read_bytes()
        assert _TR_SENSITIVE.encode("cp1254") not in output
        assert b"mask_kurumsal_deger_" in output
    assert _tree_bytes(restored) == _tree_bytes(source)


def test_export_latin1_fallback_is_audited_with_encoding_name_only(db_session, monkeypatch, tmp_path):
    from sqlalchemy import select

    from app.db.models import AuditLog
    from app.services import exporter as exporter_module
    from tests.test_consistency_masking import _run_export_and_restore, _tree_bytes

    monkeypatch.setattr(exporter_module.settings.encoding, "legacy_text_encodings", "")
    monkeypatch.setattr(file_pipeline, "guess_full_text_encoding", lambda raw: None)
    source = tmp_path / "source"
    source.mkdir()
    (source / "notes.txt").write_bytes(("b = 3\n" * 2000).encode("ascii") + b"# \x81\x8d gizli\n")

    _source, target, restored, report, _ = _run_export_and_restore(db_session, monkeypatch, tmp_path, [])

    assert (target / "notes.txt").read_bytes() == (source / "notes.txt").read_bytes()
    details = db_session.execute(
        select(AuditLog.detail).where(AuditLog.run_id == report.run_id, AuditLog.file_path == "notes.txt")
    ).scalars().all()
    fallback = [detail for detail in details if "encoding_fallback" in detail]
    assert fallback == [
        "validation_warning; encoding_fallback encoding=latin-1; "
        "metin kodlamasi tespit edilemedi, baytlar birebir korunarak okundu"
    ]
    assert _tree_bytes(restored) == _tree_bytes(source)


# --- Latin disi tahminler (CJK, Urduca vb.) ---
# Turkce bir dosyayi Cince sanmak Turkce terimlerin kacmasi (sizinti)
# demektir; Cince bir dosyayi Turkce okumak ise baytlari bozmaz. Yine de
# karar tek bir harfe dayanmaz: yeterli sayida Turkce'ye ozgu harf VE
# ASCII-disi karakterlerin buyuk cogunlugunun Turk alfabesinden olmasi gerekir.

@pytest.mark.parametrize("guess", ["big5", "big5hkscs", "johab", "cp1006", "gb2312", "cp1251"])
def test_non_latin_guess_prefers_cp1254_for_clearly_turkish_text(guess):
    assert prefer_turkish_encoding(_TURKISH.encode("cp1254"), guess) == "cp1254"


@pytest.mark.parametrize("encoding,text", [
    ("big5", "這是一個測試檔案，包含客戶資料與伺服器設定。"),
    ("johab", "이것은 고객 데이터와 서버 설정을 포함한 테스트 파일입니다."),
    ("euc_kr", "이것은 고객 데이터와 서버 설정을 포함한 테스트 파일입니다."),
    ("gb2312", "这是一个测试文件，包含客户数据和服务器配置。"),
    ("cp1251", "Это тестовый файл с данными клиентов и настройками сервера."),
    ("cp1256", "هذا ملف اختبار يحتوي على بيانات العملاء"),
])
def test_genuine_foreign_text_keeps_its_encoding(encoding, text):
    raw = ("// " + text * 3 + "\nint x = 1;\n").encode(encoding)
    assert prefer_turkish_encoding(raw, encoding) == encoding


@pytest.mark.parametrize("text", [
    "// İstanbul ŞİRKET\n",
    "// hesap çıkış şifresi\n",
])
def test_classify_bytes_short_turkish_misread_as_cjk_is_fixed(text):
    # Olculen gercek ornekler: charset_normalizer bunlari johab/big5hkscs sanıyordu.
    assert classify_bytes(text.encode("cp1254")) == (True, "cp1254")


# --- Kisa dosyalar (3'ten az Turkce'ye ozgu harf) ---
# Olcum: 1-3 kelimelik Turkce dosyalarin ~%33'u Latin disi bir kodlama
# sanilip yanlis okunuyordu. Ikinci yol (kullanici onayli): ASCII-disi
# karakterlerin TAMAMI Turk alfabesinden ve her biri ASCII harf iceren bir
# kelimenin parcasi. Tek basina duran bir harf yetmez.

@pytest.mark.parametrize("text", [
    "// Güncelleme\n", "# şirket\n", "// değer\n", "// ağ\n", "// yapılandırma\n",
    'x = "sunucu özel"\n', "Güncelleme için\n", "// düşüş\n",
])
@pytest.mark.parametrize("guess", ["cp1006", "big5", "big5hkscs", "johab", "utf_16_le", "utf_16_be"])
def test_short_turkish_word_overrides_non_latin_guess(text, guess):
    assert prefer_turkish_encoding(text.encode("cp1254"), guess) == "cp1254"


@pytest.mark.parametrize("text", [
    "// ü\n",           # kelimede ASCII harf yok
    "// Güncelleme ³\n",  # Turk alfabesi disi karakter var
    "// ağ ¹o\n",
])
def test_short_rule_needs_turkish_words_only(text):
    assert prefer_turkish_encoding(text.encode("cp1254"), "big5") == "big5"


@pytest.mark.parametrize("encoding,text", [
    ("big5", "// 修正錯誤"), ("big5", "// 客戶資料"), ("big5", "# 設定檔"), ("big5", 'x = "伺服器"'),
    ("gb2312", "// 修复错误"), ("gb2312", "# 配置文件"),
    ("euc_kr", "// 버그 수정"), ("euc_kr", "# 설정 파일"), ("johab", "// 고객 데이터"),
    ("cp1251", "// Исправить ошибку"), ("cp1251", "# Настройки"),
    ("cp1256", "// إصلاح الخطأ"), ("cp1253", "// Διόρθωση"), ("cp1255", "// תיקון באג"),
])
def test_short_genuine_foreign_comment_keeps_its_encoding(encoding, text):
    raw = (text + "\n").encode(encoding)
    assert prefer_turkish_encoding(raw, encoding) == encoding


@pytest.mark.parametrize("text", ["# şirket\n", "// değer\n", "// yapılandırma\n", "Güncelleme için\n"])
def test_classify_bytes_reads_short_turkish_file_as_cp1254(text):
    # Olculen gercek ornekler: cp1006/big5hkscs/utf_16 saniliyordu.
    raw = text.encode("cp1254")
    is_text, encoding = classify_bytes(raw)
    assert is_text and raw.decode(encoding) == text
