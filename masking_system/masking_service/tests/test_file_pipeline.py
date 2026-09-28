"""app/services/file_pipeline.py (Asama 2 / Adim 1c'de exporter.py ve
unmasker.py'den ortaklastirilan dosya on-isleme adimi) icin birim testler.

DB'siz, saf bir modul - gercek dosya sistemi (tmp_path) disinda hicbir
bagimlilik yok.
"""

from __future__ import annotations

from pathlib import Path

from app.services.exclude_engine import ExcludeSpec
from app.services.file_pipeline import ReadStatus, read_scanned_file
from app.services.scanner import ScannedFile


def _scanned(path: Path, root: Path, *, is_symlink: bool = False, excluded_by=None) -> ScannedFile:
    return ScannedFile(
        absolute_path=path,
        relative_path=path.relative_to(root),
        is_symlink=is_symlink,
        excluded_by=excluded_by,
    )


def test_excluded_file_short_circuits_without_touching_disk(tmp_path):
    src = tmp_path / "src.env"
    src.write_text("SECRET=1", encoding="utf-8")
    dest = tmp_path / "dst" / "src.env"
    spec = ExcludeSpec(id=1, pattern_name="env", glob_pattern="*.env", applies_to="file")

    outcome = read_scanned_file(_scanned(src, tmp_path, excluded_by=spec), dest, max_inline_size=1024)

    assert outcome.status == ReadStatus.EXCLUDED
    assert not dest.exists()


def test_symlink_is_reported_and_not_followed(tmp_path):
    target = tmp_path / "real.txt"
    target.write_text("gercek icerik", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    dest = tmp_path / "dst" / "link.txt"

    outcome = read_scanned_file(_scanned(link, tmp_path, is_symlink=True), dest, max_inline_size=1024)

    assert outcome.status == ReadStatus.SKIPPED_SYMLINK
    assert not dest.exists()


def test_oversized_file_is_copied_as_is(tmp_path):
    src = tmp_path / "big.txt"
    src.write_text("0123456789", encoding="utf-8")
    dest = tmp_path / "dst" / "big.txt"

    outcome = read_scanned_file(_scanned(src, tmp_path), dest, max_inline_size=5)

    assert outcome.status == ReadStatus.SKIPPED_TOO_LARGE
    assert outcome.size == 10
    assert dest.read_bytes() == src.read_bytes()


def test_oversized_known_binary_type_is_classified_by_type_not_size(tmp_path):
    src = tmp_path / "huge.jar"
    src.write_bytes(b"PK\x03\x04" + b"\x00" * 100)
    dest = tmp_path / "dst" / "huge.jar"

    outcome = read_scanned_file(_scanned(src, tmp_path), dest, max_inline_size=5, copy_unscannable=False)

    assert outcome.status == ReadStatus.COPIED_BINARY
    assert outcome.size == 104
    assert not dest.exists()


def test_binary_file_is_copied_as_is(tmp_path):
    src = tmp_path / "image.bin"
    src.write_bytes(b"\x00\x01\x02binary-ish-content")
    dest = tmp_path / "dst" / "image.bin"

    outcome = read_scanned_file(_scanned(src, tmp_path), dest, max_inline_size=1024)

    assert outcome.status == ReadStatus.COPIED_BINARY
    assert dest.read_bytes() == src.read_bytes()


def test_normal_text_file_is_not_written_yet_and_returned_as_text_ready(tmp_path):
    src = tmp_path / "app.py"
    src.write_text("SECRET = 'abc'\n", encoding="utf-8")
    dest = tmp_path / "dst" / "app.py"

    outcome = read_scanned_file(_scanned(src, tmp_path), dest, max_inline_size=1024)

    assert outcome.status == ReadStatus.TEXT_READY
    assert outcome.text == "SECRET = 'abc'\n"
    assert outcome.encoding is not None
    # TEXT_READY doneninde dosya HENUZ yazilmamis olmali - yazma sorumlulugu
    # cagiran tarafta (exporter.py/unmasker.py, mask/reverse sonrasi).
    assert not dest.exists()


def test_utf16_text_file_is_scanned_not_treated_as_binary(tmp_path):
    """Kok neden regresyon testi: UTF-16 encode edilmis bir metin dosyasi
    (BOM'lu) eskiden classify_bytes'in NUL-byte kisayolu yuzunden binary
    sanilip HIC decode edilmeden/taranmadan COPIED_BINARY olarak
    kopyalaniyordu - icindeki hassas veri asla maskelenemezdi."""
    text = "SECRET_KEY = 'gizli-deger-123'\nPoseidon dahili not\n"
    src = tmp_path / "config.txt"
    src.write_bytes(text.encode("utf-16"))
    dest = tmp_path / "dst" / "config.txt"

    outcome = read_scanned_file(_scanned(src, tmp_path), dest, max_inline_size=4096)

    assert outcome.status == ReadStatus.TEXT_READY
    assert outcome.text == text


def test_utf8_bom_text_file_is_scanned_with_bom_stripped(tmp_path):
    text = "Poseidon Teknoloji A.S. - dahili belge\n"
    src = tmp_path / "notes.md"
    src.write_bytes(text.encode("utf-8-sig"))
    dest = tmp_path / "dst" / "notes.md"

    outcome = read_scanned_file(_scanned(src, tmp_path), dest, max_inline_size=4096)

    assert outcome.status == ReadStatus.TEXT_READY
    assert outcome.text == text
    assert "﻿" not in outcome.text


def test_archive_file_is_never_content_sniffed_and_copied_when_requested(tmp_path):
    src = tmp_path / "bundle.zip"
    # Deliberately plain-text bytes with a .zip name: proves the ARCHIVE
    # short-circuit fires on the extension alone, before any content check
    # could decide "this looks like text" and let it through unscanned.
    src.write_bytes(b"not actually compressed, just extension-tagged")
    dest = tmp_path / "dst" / "bundle.zip"

    outcome = read_scanned_file(_scanned(src, tmp_path), dest, max_inline_size=1024)

    assert outcome.status == ReadStatus.ARCHIVE_UNSUPPORTED
    assert outcome.text is None
    assert dest.read_bytes() == src.read_bytes()


def test_archive_file_is_not_copied_when_copy_unscannable_is_false(tmp_path):
    """Export uses copy_unscannable=False: an archive must never reach the
    output directory pre-decision (bkz. exporter.py _prepare_file, which
    quarantines it instead)."""
    src = tmp_path / "release.tar.gz"
    src.write_bytes(b"irrelevant")
    dest = tmp_path / "dst" / "release.tar.gz"

    outcome = read_scanned_file(_scanned(src, tmp_path), dest, max_inline_size=1024, copy_unscannable=False)

    assert outcome.status == ReadStatus.ARCHIVE_UNSUPPORTED
    assert not dest.exists()


def test_office_document_is_never_content_sniffed_even_if_it_looks_like_text(tmp_path):
    """Regression guard for the exact scenario the security policy calls
    out: even if classify_bytes/_looks_like_text's heuristic ever changed
    to treat this content as text, the extension-based short-circuit in
    read_scanned_file must still keep it out of the masking pipeline."""
    src = tmp_path / "report.docx"
    src.write_text("this file is plain ASCII text on purpose", encoding="utf-8")
    dest = tmp_path / "dst" / "report.docx"

    outcome = read_scanned_file(_scanned(src, tmp_path), dest, max_inline_size=1024)

    assert outcome.status == ReadStatus.COPIED_BINARY
    assert outcome.text is None
    assert dest.read_bytes() == src.read_bytes()


def test_lock_file_is_scan_only_text_ready_not_text_ready(tmp_path):
    src = tmp_path / "package-lock.json"
    src.write_text('{"name": "demo", "lockfileVersion": 3}\n', encoding="utf-8")
    dest = tmp_path / "dst" / "package-lock.json"

    outcome = read_scanned_file(_scanned(src, tmp_path), dest, max_inline_size=4096)

    assert outcome.status == ReadStatus.SCAN_ONLY_TEXT_READY
    assert outcome.text == '{"name": "demo", "lockfileVersion": 3}\n'
    # Like TEXT_READY, nothing is written yet - the caller decides.
    assert not dest.exists()


def test_stat_failure_is_reported_as_error(tmp_path):
    missing = tmp_path / "does-not-exist.txt"
    dest = tmp_path / "dst" / "does-not-exist.txt"

    outcome = read_scanned_file(_scanned(missing, tmp_path), dest, max_inline_size=1024)

    assert outcome.status == ReadStatus.ERROR
    assert "stat basarisiz" in outcome.error
    # Errno 2 teshisi: asama, basarisiz yol ve uzunlugu mesajda.
    assert f"yol={missing}," in outcome.error
    assert f"yol_uzunlugu={len(str(missing))}" in outcome.error
    assert "hata=FileNotFoundError" in outcome.error
