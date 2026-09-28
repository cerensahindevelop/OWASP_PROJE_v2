from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services.presidio_detector import PresidioDetector


class _FakeAnalyzer:
    def __init__(self) -> None:
        self.seen_lengths: list[int] = []

    def analyze(self, *, text: str, language: str, entities=None, nlp_artifacts=None):
        self.seen_lengths.append(len(text))
        marker = "secret@example.com"
        if marker not in text:
            return []
        start = text.index(marker)
        return [
            SimpleNamespace(
                entity_type="EMAIL_ADDRESS",
                start=start,
                end=start + len(marker),
                score=0.91,
                recognition_metadata={"fake": True},
            )
        ]


def test_presidio_analyzer_receives_large_text_in_chunks_with_global_offsets():
    max_chars = 100
    content = ("a" * 95) + "\nsecret@example.com\n" + ("b" * 150)
    fake = _FakeAnalyzer()
    detector = PresidioDetector([], max_analyzer_chars=max_chars, chunk_overlap_chars=20)
    detector._analyzer = fake

    results = asyncio.run(detector.detect(content)).results

    assert fake.seen_lengths
    assert max(fake.seen_lengths) <= max_chars
    assert len(results) == 1
    assert results[0].start == content.index("secret@example.com")
    assert results[0].end == results[0].start + len("secret@example.com")
