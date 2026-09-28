"""Recursive, deterministic filesystem walk for the export/mask pipeline.

Symlinks are surfaced but never followed - the exporter decides to skip
them outright (see exporter.py) rather than silently copy whatever they
point at, which could reach outside the project folder.

Exclude patterns (ExcludeSpec, see exclude_engine.py) are applied here too:
- directory-level matches (e.g. ".git") are pruned from the walk entirely,
  the same way a .gitignore entry works - nothing under them is ever
  yielded, no per-file reporting needed for what's essentially VCS/tooling
  internals.
- file-level matches (e.g. ".env", "*.pem") ARE still yielded, but flagged
  via `excluded_by` so the caller can report exactly which file was kept
  out and why, rather than it silently vanishing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# ExcludeSpec/matches_exclude: taramada hangi dosya/klasorlerin haric
# tutulacagini (orn. .git, .env, *.pem) belirleyen kural motoru.
from app.services.exclude_engine import ExcludeSpec, matches_exclude
from app.services.file_classifier import ignored_directory_reason


# Taramada bulunan tek bir dosyayi (tam yol, kok'e gore goreli yol,
# symlink olup olmadigi, bir haric tutma desenine uyup uymadigi) temsil eder.
@dataclass(frozen=True)
class ScannedFile:
    absolute_path: Path
    relative_path: Path
    is_symlink: bool
    excluded_by: ExcludeSpec | None = None


# Bir kok klasoru recursive olarak, alfabetik/deterministik sirayla tarar;
# her dosya icin bir ScannedFile uretir (symlink'leri takip etmez, dizin
# tipi haric tutma desenlerine uyan klasorlere hic inmez).
def iter_project_files(
    root: Path, exclude_specs: list[ExcludeSpec] | None = None, *, prune_ignored: bool = False,
):
    root = Path(root).resolve()
    exclude_specs = exclude_specs or []
    def builtin_exclusion(path: Path) -> ExcludeSpec | None:
        reason = ignored_directory_reason(path.relative_to(root)) if prune_ignored else None
        return ExcludeSpec(0, reason, path.name, "both") if reason else None

    def on_error(error):
        # Permission/I/O failures must not silently remove a subtree.
        raise error
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False, onerror=on_error):
        dirnames.sort()
        current_dir = Path(dirpath)
        retained = []
        for name in dirnames:
            path = current_dir / name
            excluded = matches_exclude(name, exclude_specs, "directory") or builtin_exclusion(path)
            if excluded or path.is_symlink():
                yield ScannedFile(path, path.relative_to(root), path.is_symlink(), excluded)
            else:
                retained.append(name)
        dirnames[:] = retained
        for name in sorted(filenames):
            absolute_path = current_dir / name
            yield ScannedFile(
                absolute_path=absolute_path,
                relative_path=absolute_path.relative_to(root),
                is_symlink=absolute_path.is_symlink(),
                excluded_by=matches_exclude(name, exclude_specs, "file") or builtin_exclusion(absolute_path),
            )
