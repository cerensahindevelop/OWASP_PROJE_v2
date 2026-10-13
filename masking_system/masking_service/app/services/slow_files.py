"""Taramayi uzun surdurecek buyuk metin dosyalarinin export ONCESI tespiti.

Olcum (2026-10-13, 11,9 MB / 58.934 satirlik bir CSV): sozluk/regex katmani
~14 dk, Presidio ~30 dk surdu; bulgu sayisi boyutla birlikte artar ve cakisma
cozumu bulgu sayisiyla karesel buyudugu icin ayni dosya saatler surer. Bu
sure boyunca ayni gruptaki diger dosyalar da bekler. Sure yalnizca boyuta
degil bulgu yogunluguna da baglidir, ama buyuk metin dosyasi tarama
baslamadan gorulebilen en guvenilir isarettir. Karar kullanicinindir: uyari
gosterilir, tarama engellenmez.

Hangi dosyalarin taranacagi exporter ile AYNI kurallarla belirlenir: haric
tutma desenleri ve yerlesik yoksayilan klasorler (bkz. scanner.iter_project_files),
opak ikili/arsiv dosyalari ve SCAN_MAX_FILE_MB'yi asanlar (bkz.
file_pipeline.read_scanned_file) hic taranmaz; icerigi metin olmayanlar da
(bkz. file_type.classify_bytes). Lock dosyalari LLM'siz de olsa sozluk ve
Presidio'dan gecer, bu yuzden aday sayilir.

Yukleme modunda dosyalar ikinci kez gonderilmez: arayuz yalnizca yol/boyut/
metin-mi bilgisinden olusan bir liste yollar (bkz. uploads.upload_manifest).
Rapora ve API yanitina icerik degil yalnizca yol ve boyut yazilir.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.services.exclude_engine import ExcludeSpec, matches_exclude
from app.services.file_classifier import ignored_directory_reason, is_archive_filename, is_opaque_binary_filename
from app.services.file_type import peek_classify
from app.services.scanner import iter_project_files


# Tahmin icin bir dosya hakkinda bilinmesi gerekenler (icerik degil).
@dataclass(frozen=True)
class FileEntry:
    path: str  # proje kokune gore goreli, '/' ayracli
    size_bytes: int
    is_text: bool


@dataclass(frozen=True)
class SlowFile:
    path: str
    size_bytes: int


# iter_project_files'in tek bir goreli yol icin verecegi karar: ust klasorlerden
# biri ya da dosyanin kendisi bir haric tutma desenine/yerlesik yoksayilan
# klasore uyuyorsa dosya taranmaz.
def is_excluded(relative_path: PurePosixPath, exclude_specs: list[ExcludeSpec]) -> bool:
    parts = relative_path.parts
    if any(matches_exclude(part, exclude_specs, "directory") for part in parts[:-1]):
        return True
    return bool(matches_exclude(parts[-1], exclude_specs, "file") or ignored_directory_reason(Path(*parts)))


# Uyari esigini asan ve gercekten metin olarak taranacak dosyalari, en buyukten
# kucuge dondurur. warn_bytes <= 0 ise uyari kapalidir.
def find_slow_files(
    entries: list[FileEntry], exclude_specs: list[ExcludeSpec], *, warn_bytes: int, max_bytes: int,
) -> list[SlowFile]:
    if warn_bytes <= 0:
        return []
    slow = []
    for entry in entries:
        relative_path = PurePosixPath(entry.path.replace("\\", "/").lstrip("/"))
        if not relative_path.parts or not entry.is_text:
            continue
        if not warn_bytes <= entry.size_bytes <= max_bytes:
            continue
        if is_opaque_binary_filename(relative_path.name) or is_archive_filename(relative_path.name):
            continue
        if is_excluded(relative_path, exclude_specs):
            continue
        slow.append(SlowFile(relative_path.as_posix(), entry.size_bytes))
    return sorted(slow, key=lambda item: (-item.size_bytes, item.path))


# Sunucudaki bir klasor icin FileEntry listesi. Icerige yalnizca esigi asan
# dosyalarda (ilk PEEK_SIZE bayt) bakilir; digerleri zaten uyari almaz.
def directory_entries(root: Path, exclude_specs: list[ExcludeSpec], *, warn_bytes: int) -> list[FileEntry]:
    entries = []
    for scanned in iter_project_files(root, exclude_specs, prune_ignored=True):
        if scanned.excluded_by is not None or scanned.is_symlink or not scanned.absolute_path.is_file():
            continue
        size = scanned.absolute_path.stat().st_size
        is_text = warn_bytes > 0 and size >= warn_bytes and peek_classify(scanned.absolute_path)[0]
        entries.append(FileEntry(scanned.relative_path.as_posix(), size, is_text))
    return entries
