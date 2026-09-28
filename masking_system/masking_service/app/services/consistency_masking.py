"""Job-scope sensitive-value consistency masking.

The normal detector stack remains the authority: this module never invents
new sensitive values.  It only remembers values that produced a real mapping
during the first pass, then finds equivalent occurrences that another file or
context caused the detector stack to miss.

Matching uses NFKC + Unicode case-folding for controlled case/Unicode
equivalence, but replacements always keep the exact source spelling.  The
caller therefore creates an exact ValueMapping for every newly found variant,
which preserves byte/text round-trip semantics during unmask.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import unicodedata

from app.db.models import ValueMapping
from app.services.rule_engine import JSON_BARE_INTEGER_RE, Match, PLACEHOLDER_RE, RuleSpec
from app.services.token_boundary_validator import TokenBoundaryValidator
from app.services.string_literal_index import StringLiteralIndex

_EXACT_BOUNDARY_VALIDATOR = TokenBoundaryValidator()


def normalize_sensitive_value(value: str) -> str:
    """Return the registry/search key without losing the stored original."""
    return unicodedata.normalize("NFKC", value).casefold()


@dataclass
class SensitiveValueEntry:
    normalized_value: str
    original_value: str
    entity_type: str
    source_detectors: set[str] = field(default_factory=set)
    rule: RuleSpec | None = None


class SensitiveValueRegistry:
    """Values positively masked by this run, grouped by canonical form."""

    def __init__(self) -> None:
        self._entries: dict[str, SensitiveValueEntry] = {}

    def __len__(self) -> int:
        return len(self._entries)

    def entries(self) -> list[SensitiveValueEntry]:
        # Longest first makes overlap resolution deterministic and ensures a
        # full value beats a shorter sensitive value contained within it.
        return sorted(self._entries.values(), key=lambda item: len(item.normalized_value), reverse=True)

    def add_successful_matches(self, matches: list[Match], mappings: list[ValueMapping]) -> None:
        """Register only detections that actually produced/reused a mapping."""
        if len(matches) != len(mappings):
            raise ValueError("successful match/mapping count mismatch while building consistency registry")

        for match, mapping in zip(matches, mappings):
            # Mapping storage is occurrence-exact. The normalized key groups
            # controlled search variants only; it must never become the value
            # restored by unmask.
            original_value = mapping.original_value_plain or match.original_value
            normalized = normalize_sensitive_value(original_value)
            if not normalized or PLACEHOLDER_RE.fullmatch(original_value):
                continue

            entity_type = match.entity_type or match.rule.category
            source = match.source_detector or "unknown"
            existing = self._entries.get(normalized)
            if existing is None:
                self._entries[normalized] = SensitiveValueEntry(
                    normalized_value=normalized,
                    original_value=original_value,
                    entity_type=entity_type,
                    source_detectors={source},
                    rule=match.rule,
                )
            else:
                existing.source_detectors.add(source)


@dataclass(frozen=True)
class ConsistencyOccurrence:
    entry: SensitiveValueEntry
    start: int
    end: int
    original_value: str
    # True ise bu bulgu bir JSON dosyasinda tirnaksiz bir sayi konumunda -
    # cagiran (exporter.py _run_consistency_pass) bunu get_or_create_mapping'e
    # numeric=True olarak iletmeli (bkz. rule_engine.py
    # JSON_NUMERIC_PLACEHOLDER_RE dokstringi - harf-tabanli bir placeholder
    # bu konuma yazilirsa gecersiz JSON uretir).
    numeric: bool = False


def _is_word_char(char: str) -> bool:
    # Unicode-aware by design: Turkish and other non-ASCII letters must not
    # create a fake boundary merely because an ASCII class cannot see them.
    return char == "_" or char.isalnum()


def _has_safe_boundaries(text: str, start: int, end: int, value: str) -> bool:
    """Reject word/identifier substring matches such as ABC in ABCService."""
    if value and _is_word_char(value[0]) and start > 0 and _is_word_char(text[start - 1]):
        return False
    if value and _is_word_char(value[-1]) and end < len(text) and _is_word_char(text[end]):
        return False
    return True


def _normalized_view(text: str) -> tuple[str, list[int], list[int]]:
    """Normalize text while retaining normalized-offset -> source spans.

    A base code point and its following combining marks are treated as one
    cluster.  This makes composed/decomposed forms (e.g. Ş vs S + mark)
    comparable while still allowing an exact slice from the original text.
    """
    normalized_parts: list[str] = []
    source_starts: list[int] = []
    source_ends: list[int] = []
    index = 0
    while index < len(text):
        cluster_start = index
        index += 1
        while index < len(text) and unicodedata.combining(text[index]):
            index += 1
        cluster = text[cluster_start:index]
        normalized_cluster = normalize_sensitive_value(cluster)
        normalized_parts.append(normalized_cluster)
        source_starts.extend([cluster_start] * len(normalized_cluster))
        source_ends.extend([index] * len(normalized_cluster))
    return "".join(normalized_parts), source_starts, source_ends


def _overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(not (end <= other_start or start >= other_end) for other_start, other_end in spans)


def find_consistency_occurrences(text: str, registry: SensitiveValueRegistry, *, file_path: str = "") -> list[ConsistencyOccurrence]:
    """Find safe, non-overlapping, non-placeholder occurrences in text."""
    if not text or len(registry) == 0:
        return []

    normalized_text, source_starts, source_ends = _normalized_view(text)
    if not normalized_text:
        return []

    consumed = [(match.start(), match.end()) for match in PLACEHOLDER_RE.finditer(text)]
    accepted: list[ConsistencyOccurrence] = []
    string_index = StringLiteralIndex(text, file_path)
    is_json_file = Path(file_path).suffix.lower().lstrip(".") == "json"

    for entry in registry.entries():
        needle = entry.normalized_value
        if not needle:
            continue
        search_from = 0
        while True:
            normalized_start = normalized_text.find(needle, search_from)
            if normalized_start < 0:
                break
            normalized_end = normalized_start + len(needle)
            search_from = normalized_start + 1

            start = source_starts[normalized_start]
            end = source_ends[normalized_end - 1]
            source_value = text[start:end]
            span = (start, end)
            if normalize_sensitive_value(source_value) != needle:
                continue
            if _overlaps(span, consumed):
                continue
            if not _has_safe_boundaries(text, start, end, source_value):
                continue
            # Registry entries are already proven-sensitive values from a
            # successful first-pass replacement.  A complete token component
            # remains safe to replace next to language-neutral separators
            # such as ``.`` or ``(``; substring protection above still blocks
            # cases such as ABC inside ABCService.
            if not _EXACT_BOUNDARY_VALIDATOR.is_exact_span_allowed(
                text,
                start,
                end,
                allow_bare_code_expression=True,
                string_index=string_index,
            ):
                continue

            numeric = bool(
                is_json_file
                and string_index.enclosing(start, end) is None
                and JSON_BARE_INTEGER_RE.fullmatch(source_value)
            )
            accepted.append(
                ConsistencyOccurrence(
                    entry=entry, start=start, end=end, original_value=source_value, numeric=numeric
                )
            )
            consumed.append(span)

    accepted.sort(key=lambda occurrence: occurrence.start)
    return accepted


def apply_consistency_replacements(
    text: str, replacements: list[tuple[ConsistencyOccurrence, str]]
) -> str:
    """Apply pre-resolved occurrence -> placeholder replacements in one pass."""
    segments: list[str] = []
    cursor = 0
    for occurrence, placeholder in sorted(replacements, key=lambda item: item[0].start):
        if occurrence.start < cursor:
            raise ValueError("overlapping consistency replacements")
        segments.append(text[cursor : occurrence.start])
        segments.append(placeholder)
        cursor = occurrence.end
    segments.append(text[cursor:])
    return "".join(segments)
