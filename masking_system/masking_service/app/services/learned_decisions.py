"""Context-scoped learned sensitive values and narrow false-positive suppressions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_value, encrypt_value, hash_value
from app.db.models import LearnedDecision
from app.services.detectors import DetectionResult, DetectorOutput, placeholder_prefix_for_type
from app.services.rule_engine import PLACEHOLDER_RE, RuleSpec
from app.services.unicode_spans import normalized_view


def normalize_value(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def security_scope(file_path: str) -> str:
    path = PurePosixPath(file_path.replace("\\", "/"))
    top = path.parts[0].casefold() if len(path.parts) > 1 else "."
    return f"{top}|{path.suffix.casefold() or '<none>'}"


def remember_decision(
    db: Session, *, context_id: int, decision_type: str, value: str,
    entity_type: str, file_path: str, source_review_id: int | None,
) -> LearnedDecision:
    normalized = normalize_value(value)
    scope = "*" if decision_type == "sensitive" else security_scope(file_path)
    value_digest = hash_value(context_id, normalized)
    existing = db.scalar(select(LearnedDecision).where(
        LearnedDecision.context_id == context_id,
        LearnedDecision.decision_type == decision_type,
        LearnedDecision.value_hash == value_digest,
        LearnedDecision.entity_type == entity_type,
        LearnedDecision.scope_key == scope,
    ))
    if existing is not None:
        existing.is_active = True
        return existing
    item = LearnedDecision(
        context_id=context_id, decision_type=decision_type,
        value_encrypted=encrypt_value(value), value_hash=value_digest,
        entity_type=entity_type, scope_key=scope,
        source_review_id=source_review_id, is_active=True,
    )
    db.add(item)
    db.flush()
    return item


@dataclass(frozen=True)
class DecisionEntry:
    id: int
    value: str
    entity_type: str
    scope_key: str


class LearnedDecisionPolicy:
    def __init__(self, sensitive: list[DecisionEntry], suppressions: list[DecisionEntry]) -> None:
        self.sensitive = sensitive
        self.suppressions = suppressions

    @classmethod
    def load(cls, db: Session, context_id: int) -> "LearnedDecisionPolicy":
        rows = db.scalars(select(LearnedDecision).where(
            LearnedDecision.context_id == context_id, LearnedDecision.is_active.is_(True)
        )).all()
        entries = [DecisionEntry(r.id, decrypt_value(r.value_encrypted), r.entity_type, r.scope_key) for r in rows]
        return cls(
            [e for e, r in zip(entries, rows) if r.decision_type == "sensitive"],
            [e for e, r in zip(entries, rows) if r.decision_type == "suppression"],
        )

    def is_suppressed(self, result: DetectionResult, file_path: str) -> DecisionEntry | None:
        value = normalize_value(result.deger)
        scope = security_scope(file_path)
        return next((entry for entry in self.suppressions if
            entry.scope_key == scope and entry.entity_type == result.tip
            and normalize_value(entry.value) == value), None)

    def suppression_for_value(self, value: str, file_path: str) -> DecisionEntry | None:
        """Match a final-audit excerpt to any narrow suppression in this file scope."""
        normalized = normalize_value(value)
        scope = security_scope(file_path)
        return next((entry for entry in self.suppressions if
            entry.scope_key == scope and normalize_value(entry.value) == normalized), None)


class LearnedSensitiveDetector:
    name = "dictionary"

    def __init__(self, entries: list[DecisionEntry]) -> None:
        self.entries = entries

    async def detect(self, content: str, metadata: dict | None = None) -> DetectorOutput:
        protected = [match.span() for match in PLACEHOLDER_RE.finditer(content)]
        results: list[DetectionResult] = []
        folded = normalize_value(content)
        source_view = None
        for entry in self.entries:
            needle = normalize_value(entry.value)
            start = 0
            while needle and (index := folded.find(needle, start)) >= 0:
                normalized_end = index + len(needle)
                start = index + 1
                # Most files contain no learned value. Allocate the offset
                # map only on a hit, and reuse it for every entry in this file.
                if source_view is None:
                    source_view = normalized_view(content)
                source_start = source_view[1][index]
                source_end = source_view[2][normalized_end - 1]
                value = content[source_start:source_end]
                # Never match part of a Unicode expansion (e.g. "s" inside
                # ß -> "ss") or use normalized offsets against source text.
                if normalize_value(value) != needle:
                    continue
                if any(a < source_end and source_start < b for a, b in protected):
                    continue
                rule = RuleSpec(
                    id=None, rule_name=f"learned_sensitive_{entry.id}", category=entry.entity_type,
                    pattern_type="regex", regex_pattern=None, regex_flags=None,
                    placeholder_prefix=placeholder_prefix_for_type(entry.entity_type),
                    priority=10,
                )
                results.append(DetectionResult(
                    deger=value, tip=entry.entity_type, guven_seviyesi="yuksek",
                    kaynak_motor=self.name, gerekce=f"learned_rule_id={entry.id}",
                    start=source_start, end=source_end, rule=rule,
                ))
        return DetectorOutput(results=results)
