"""Export oncesi LLM is yuku tahmini (LLM'e hic istek gonderilmez).

Bir projenin hangi dosyalarinin LLM'e kac parca gidecegini, gomulu ikili
verinin ne kadarinin gizlenecegini ve gizlenemeyen kodlanmis-veri benzeri
satirlari dosya dosya hesaplar. Amac, .resx base64 resimleri gibi yeni bir
dosya bicimi taramayi saatlerce yavaslatmadan ONCE gorunur olmasi: kullanici
dosyayi haric tutma kuraliyla ayirabilir ya da sorun bildirebilir.

Tahmin, export ile ayni okuma/siniflandirma (read_scanned_file), ayni blok
tespiti (find_encoded_blobs) ve ayni parcalama (chunk_text) adimlarini
kullanir. Katman 1 bulgularinin gizlenmesi hesaba katilmaz; bu yuzden sonuc
ust sinirdir. Rapora icerik degil yalnizca yol ve sayilar yazilir.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.services.encoded_blobs import ENCODED_BLOB_CATEGORY, find_encoded_blobs
from app.services.file_pipeline import ReadStatus, read_scanned_file
from app.services.llm_input_view import LLMInputStats, build_redacted_view
from app.services.scanner import iter_project_files
from app.services.text_chunking import chunk_text, dedupe_for_llm

MINIFIED_LINE_CHARS = 2000


@dataclass(frozen=True)
class FileWorkload:
    path: str
    stats: LLMInputStats
    longest_line: int

    @property
    def requests(self) -> int:
        # Tespit + maskeleme sonrasi denetim; maskeli metin kaynakla ayni boyutta kabul edilir.
        return 2 * self.stats.chunks

    @property
    def hints(self) -> tuple[str, ...]:
        hints = []
        if self.stats.unrecognized_encoded_lines:
            hints.append("taninmayan kodlanmis veri")
        if self.longest_line >= MINIFIED_LINE_CHARS:
            hints.append("minified/uretilmis kod")
        if self.stats.hidden_chars:
            hints.append("gomulu ikili veri gizlenecek")
        return tuple(hints)


def estimate_text(path: str, text: str, vllm_settings, blob_min_chars: int) -> FileWorkload:
    blobs = find_encoded_blobs(text, blob_min_chars)
    view = build_redacted_view(text, [(start, end, ENCODED_BLOB_CATEGORY) for start, end in blobs])
    deduped = dedupe_for_llm(view.text, vllm_settings.max_file_chars, normalize_digits=True)
    scan_text = deduped.text if deduped is not None else view.text
    chunks = chunk_text(scan_text, vllm_settings.max_file_chars, getattr(vllm_settings, "chunk_overlap_chars", 500))
    stats = LLMInputStats()
    stats.record(text, view, len(chunks) if view.text.strip() else 0)
    longest = max((len(line) for line in view.text.splitlines()), default=0)
    return FileWorkload(path=path, stats=stats, longest_line=longest)


def estimate_project(
    root: Path, exclude_specs, vllm_settings, *, blob_min_chars: int, max_inline_size: int,
    legacy_encodings: tuple[str, ...],
) -> list[FileWorkload]:
    """LLM'e gidecek her metin dosyasinin tahmini is yuku, en agirdan hafife."""
    workloads: list[FileWorkload] = []
    with tempfile.TemporaryDirectory(prefix="llm-workload-") as scratch:
        for scanned in iter_project_files(root, exclude_specs, prune_ignored=True):
            if scanned.excluded_by is not None or scanned.is_symlink:
                continue
            outcome = read_scanned_file(
                scanned, Path(scratch) / "unused", max_inline_size, copy_unscannable=False,
                legacy_encodings=legacy_encodings,
            )
            # Lock dosyalari (SCAN_ONLY) ve metin olmayan dosyalar LLM'e gitmez.
            if outcome.status != ReadStatus.TEXT_READY or outcome.class_document is not None:
                continue
            workloads.append(estimate_text(
                scanned.relative_path.as_posix(), outcome.text, vllm_settings, blob_min_chars,
            ))
    return sorted(workloads, key=lambda workload: (-workload.requests, workload.path))
