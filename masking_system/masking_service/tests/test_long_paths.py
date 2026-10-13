from pathlib import Path

from app.services.file_pipeline import describe_file_error
from app.services.long_paths import display_path, extended_length_path, to_extended


def test_to_extended_prefixes_drive_and_unc_paths_once():
    assert to_extended("C:\\proje\\a.txt") == "\\\\?\\C:\\proje\\a.txt"
    assert to_extended("\\\\sunucu\\pay\\a.txt") == "\\\\?\\UNC\\sunucu\\pay\\a.txt"
    assert to_extended("\\\\?\\C:\\proje") == "\\\\?\\C:\\proje"


def test_display_path_strips_extended_prefix():
    assert display_path("\\\\?\\C:\\proje\\a.txt") == "C:\\proje\\a.txt"
    assert display_path("\\\\?\\UNC\\sunucu\\pay\\a.txt") == "\\\\sunucu\\pay\\a.txt"
    assert display_path("/tmp/a.txt") == "/tmp/a.txt"


def test_extended_length_path_is_noop_off_windows(tmp_path):
    assert extended_length_path(tmp_path / "a.txt") == tmp_path / "a.txt"


def test_file_error_reports_unprefixed_path_and_its_length():
    failing = "\\\\?\\C:\\" + "d" * 250
    exc = FileNotFoundError(2, "bulunamadi", failing)
    message = describe_file_error("yazma", Path("x"), exc)
    assert "yol=C:\\" + "d" * 250 in message
    assert "yol_uzunlugu=253" in message
