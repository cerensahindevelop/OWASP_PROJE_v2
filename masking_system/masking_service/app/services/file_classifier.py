"""File eligibility policies shared by scanning, export and LLM detection.

Excluded directories and unsupported formats never enter LLM detection.
Supported text files use the same detector stack regardless of their contents.
Lock files retain their scan-only policy; archives remain unsupported.
"""
from __future__ import annotations

from pathlib import Path


_IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "target",
    "coverage",
    ".next",
    ".nuxt",
    ".turbo",
    ".venv",
    "venv",
}

_IGNORED_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".svgz",
    ".pdf",
    ".jar",
    ".war",
    ".pyc",
    ".so",
    ".dll",
    ".dylib",
    ".exe",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
}

# ARCHIVE policy: project-internal archives are neither masked nor blindly
# ignored - they are quarantined for review (bkz. file_pipeline.py
# read_scanned_file). Recursive extraction/repack is explicitly out of
# scope for this version.
_ARCHIVE_SUFFIXES = {".zip", ".tar", ".gz", ".tgz", ".rar", ".7z"}

# SCAN_ONLY policy: gercek bagimlilik lock/integrity dosyalari metin olarak
# tam taranir ama ASLA maskelenmez/yeniden yazilmaz - bulgu yoksa byte-
# identical kopyalanir, bulgu varsa quarantine'e gonderilir (bkz.
# file_pipeline.py read_scanned_file, exporter.py _finalize_scan_only).
_LOCK_FILENAMES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "pipfile.lock",
}
_LOCK_SUFFIXES = {".lock"}

def ignored_directory_reason(relative_path: Path) -> str | None:
    """Also excludes a .git worktree pointer file before reading its content."""
    ignored = {part.lower() for part in relative_path.parts} & _IGNORED_DIRS
    return f"ignored directory: {sorted(ignored)[0]}" if ignored else None


def should_ignore_path(relative_path: Path) -> tuple[bool, str | None]:
    reason = ignored_directory_reason(relative_path)
    if reason:
        return True, reason
    suffix = relative_path.suffix.lower()
    if suffix in _IGNORED_SUFFIXES:
        return True, f"ignored suffix: {suffix}"
    return False, None


def is_archive_filename(name: str) -> bool:
    return Path(name).suffix.lower() in _ARCHIVE_SUFFIXES


def is_lock_filename(name: str) -> bool:
    lowered = name.lower()
    return lowered in _LOCK_FILENAMES or Path(lowered).suffix in _LOCK_SUFFIXES


def is_opaque_binary_filename(name: str) -> bool:
    return Path(name).suffix.lower() in _IGNORED_SUFFIXES
