"""LLM'e gonderilen metnin "on-maskelenmis" gorunumu (redacted view).

Katman 1'in (sozluk/regex - kesin, belirlenimli) zaten buldugu degerler LLM'e
acik haliyle gonderilirse model onlari JSON ciktisinda yeniden listeler:
cikti token'lari bosa harcanir, max_tokens'ta kesilme/bolme turlari artar ve
modelin dikkati asil isinden (serbest formatli, kuruma ozgu degerler) kayar.

Bu modul, bu araliklari metinden cikarip yerlerine `mask_<tur>_<n>` bicimli
gecici yer tutucular koyar (LLM promptu bu bicimi "zaten maskelenmis" diye
tanir) ve LLM bulgularinin gorunum koordinatlarini orijinal metne geri esler.

Guvenlik ilkesi: gorunum YALNIZCA LLM'e gonderilen metni etkiler; ciktiya
yazilan maskeleme her zaman orijinal metin ve orijinal ofsetler uzerinden
yapilir. Gecici yer tutucuyla cakisan bir LLM bulgusu orijinal metne
eslenemez ve atilir (o aralik zaten Katman 1 tarafindan maskelenir).
"""

from __future__ import annotations

import logging
import re
from bisect import bisect_right
from dataclasses import dataclass

from app.services.encoded_blobs import ENCODED_BLOB_CATEGORY, count_unrecognized_encoded_lines

logger = logging.getLogger("uvicorn.error.llm")

_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class _Region:
    view_start: int
    view_end: int
    orig_start: int
    orig_end: int


@dataclass(frozen=True)
class RedactedView:
    """LLM'e gonderilen metin ve orijinal metne geri esleme bilgisi."""

    text: str
    regions: tuple[_Region, ...] = ()

    @classmethod
    def identity(cls, text: str) -> "RedactedView":
        return cls(text=text)

    @property
    def is_identity(self) -> bool:
        return not self.regions

    @property
    def hidden_chars(self) -> int:
        return sum(region.orig_end - region.orig_start for region in self.regions)

    def to_original(self, start: int, end: int) -> tuple[int, int] | None:
        """Gorunumdeki [start, end) araligini orijinal metne esler.

        Aralik bir gecici yer tutucuyla cakisiyorsa None doner.
        """
        if self.is_identity:
            return start, end
        shift = 0
        for region in self.regions:
            if region.view_start >= end:
                break
            if region.view_end > start:
                return None
            shift = region.orig_end - region.view_end
        return start + shift, end + shift

    def to_view_spans(self, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
        """Orijinal koordinatlardaki korumali araliklari gorunume tasir.

        Gecici yer tutucularin kendileri de korumali araliga eklenir, boylece
        LLM bir yer tutucuyu bulgu olarak dondurse bile eslesme atilir.
        """
        if self.is_identity:
            return list(spans)
        starts = [region.orig_start for region in self.regions]
        converted = [(self._view_pos(starts, start), self._view_pos(starts, end)) for start, end in spans]
        converted.extend((region.view_start, region.view_end) for region in self.regions)
        return converted

    def _view_pos(self, starts: list[int], pos: int) -> int:
        index = bisect_right(starts, pos) - 1
        if index < 0:
            return pos
        region = self.regions[index]
        if pos < region.orig_end:
            # Yer tutucunun icine duser: yer tutucu sinirina sabitlenir.
            return region.view_start if pos == region.orig_start else region.view_end
        return pos - (region.orig_end - region.view_end)


def _merge(spans: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    merged: list[tuple[int, int, str]] = []
    for start, end, category in sorted(spans):
        if merged and start < merged[-1][1]:
            previous_start, previous_end, previous_category = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end), previous_category)
        else:
            merged.append((start, end, category))
    return merged


def _placeholder_prefix(category: str) -> str:
    slug = _SLUG_RE.sub("_", category.lower()).strip("_")
    if not slug or not slug[0].isalpha():
        slug = "deger"
    return f"mask_{slug}"


def build_redacted_view(text: str, spans: list[tuple[int, int, str]]) -> RedactedView:
    """`spans` (start, end, kategori) araliklarini gecici yer tutucularla degistirir.

    Ayni deger ayni yer tutucuyu alir; boylece model degerin metindeki
    tekrarlarini hala "ayni sey" olarak gorebilir.
    """
    valid = [(start, end, category) for start, end, category in spans if 0 <= start < end <= len(text)]
    if not valid:
        return RedactedView.identity(text)

    tokens: dict[str, str] = {}
    counters: dict[str, int] = {}
    parts: list[str] = []
    regions: list[_Region] = []
    cursor = 0
    view_length = 0
    for start, end, category in _merge(valid):
        value = text[start:end]
        token = tokens.get(value)
        if token is None:
            prefix = _placeholder_prefix(category)
            counters[prefix] = counters.get(prefix, 0) + 1
            token = f"{prefix}_{counters[prefix]}"
            tokens[value] = token
        unchanged = text[cursor:start]
        parts.append(unchanged)
        view_length += len(unchanged)
        parts.append(token)
        regions.append(_Region(view_length, view_length + len(token), start, end))
        view_length += len(token)
        cursor = end
    parts.append(text[cursor:])
    return RedactedView(text="".join(parts), regions=tuple(regions))


@dataclass
class LLMInputStats:
    """Bir dosyanin LLM'e giden is yuku (yalnizca sayilar, icerik yok)."""

    chunks: int = 0
    original_chars: int = 0
    sent_chars: int = 0
    hidden_chars: int = 0
    unrecognized_encoded_lines: int = 0

    def record(self, text: str, view: RedactedView, chunks: int) -> None:
        self.chunks = chunks
        self.original_chars = len(text)
        self.sent_chars = len(view.text)
        self.hidden_chars = view.hidden_chars
        self.unrecognized_encoded_lines = count_unrecognized_encoded_lines(view.text)

    # Dosya beklenenden cok LLM istegi uretiyorsa ya da gizlenmemis kodlanmis
    # veri iceriyorsa, islem kaydina yazilacak icerik-siz uyari; yoksa None.
    def workload_notice(self, warn_chunks: int) -> str | None:
        too_many = 0 < warn_chunks <= self.chunks
        if not too_many and not self.unrecognized_encoded_lines:
            return None
        return (
            f"llm_is_yuku_yuksek parca={self.chunks} gonderilen_karakter={self.sent_chars} "
            f"gizlenen_karakter={self.hidden_chars} "
            f"taninmayan_kodlanmis_satir={self.unrecognized_encoded_lines}"
        )


def build_llm_input_view(
    text: str, vllm_settings, known_spans: list[tuple[int, int, str]] | None = None,
    blob_spans: list[tuple[int, int]] | None = None, *,
    phase: str = "detection", file_path: str | None = None,
) -> RedactedView:
    """LLM'e gidecek gorunumu kurar: Katman 1'in kesin bulgulari
    (VLLM_REDACT_KNOWN_FINDINGS) ve gomulu ikili veri bloklari
    (SCAN_ENCODED_BLOB_MIN_CHARS; orn. .resx icindeki base64 resimler)
    gecici yer tutucuyla degistirilir. Loga yalnizca sayilar yazilir.
    """
    spans: list[tuple[int, int, str]] = []
    if known_spans and getattr(vllm_settings, "redact_known_findings", False):
        spans.extend(known_spans)
    blobs = blob_spans or []
    spans.extend((start, end, ENCODED_BLOB_CATEGORY) for start, end in blobs)
    view = build_redacted_view(text, spans)
    if blobs:
        logger.info(
            "llm_input_encoded_blobs file=%r phase=%s blobs=%d hidden_chars=%d original_chars=%d sent_chars=%d",
            file_path, phase, len(blobs), view.hidden_chars, len(text), len(view.text),
        )
    return view
