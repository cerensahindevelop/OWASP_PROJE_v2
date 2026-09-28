from __future__ import annotations

from pathlib import Path

from app.services.file_classifier import (
    is_archive_filename,
    is_lock_filename,
    is_opaque_binary_filename,
    should_ignore_path,
)


def test_should_ignore_build_and_vendor_dirs():
    assert should_ignore_path(Path("node_modules/pkg/index.js"))[0] is True
    assert should_ignore_path(Path("dist/app.js"))[0] is True


def test_lock_files_are_scan_only_not_ignored():
    """Policy change: dependency lock files are no longer silently IGNORE'd -
    see SCAN_ONLY in file_pipeline.py/exporter.py. should_ignore_path is now
    reserved for generated/cache/dependency dirs and opaque binary formats."""
    for name in ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock", "Cargo.lock"):
        assert should_ignore_path(Path(name))[0] is False, name
        assert is_lock_filename(name) is True, name
    assert is_lock_filename("regular.json") is False


def test_archive_files_are_not_generic_ignore():
    for name in ("bundle.zip", "release.tar", "release.tar.gz", "release.tgz", "archive.rar", "archive.7z"):
        assert should_ignore_path(Path(name))[0] is False, name
        assert is_archive_filename(name) is True, name
    assert is_archive_filename("regular.py") is False


def test_opaque_binary_suffixes_still_ignored_and_now_include_office_formats():
    for name in ("report.docx", "sheet.xlsx", "deck.pptx", "legacy.doc", "legacy.xls", "photo.png", "lib.dll"):
        assert is_opaque_binary_filename(name) is True, name
    assert is_opaque_binary_filename("source.py") is False
    # .class has its own dedicated structural handling (java_classfile.py),
    # it is neither in the opaque-binary list nor content-sniffed as binary.
    assert is_opaque_binary_filename("App.class") is False


def test_git_worktree_pointer_and_generated_directories_are_excluded():
    for name in (".git", ".git/config", "repo/.git/objects/data.lock", ".hg/store", ".svn/entries",
                 "node_modules/pkg/index.js", "dist/app.js", ".venv/pyvenv.cfg"):
        assert should_ignore_path(Path(name))[0], name


def test_supported_text_paths_are_not_excluded():
    for name in ("README.md", "src/math.py", "auth/config.yaml", "settings.json", "notes.txt"):
        assert not should_ignore_path(Path(name))[0], name
