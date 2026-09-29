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


@pytest.mark.parametrize("encoding", ["utf-8", "utf_16_le", "cp1251", "cp857", "mac_turkish", "big5"])
def test_non_latin_or_multibyte_guess_is_left_alone(encoding):
    assert prefer_turkish_encoding(_TURKISH.encode("cp1254"), encoding) == encoding


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
