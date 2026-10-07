"""spaCy NLP motoru surec basina bir kez yuklenir ve calismalar arasinda paylasilir.

Her export calismasi kendi kural setiyle yeni bir PresidioDetector kurar. spaCy
modeli (en_core_web_lg) her seferinde yeniden yuklenirse eszamanli is basina
yuzlerce MB bellek ve saniyeler suren kurulum harcanir. Paylasilan model surec
boyunca yasadigi icin analizler sozluge kalici kayit birakmamalidir.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.services import presidio_detector
from app.services.presidio_detector import PresidioDetector

pytest.importorskip("presidio_analyzer")


def test_detectors_share_one_nlp_engine_and_its_lock():
    first = PresidioDetector([])
    second = PresidioDetector([])

    if first._analyzer is None or second._analyzer is None:
        pytest.skip("Presidio analyzer kurulamadi")
    assert first._analyzer is not second._analyzer  # kural seti calismaya ozel kalir
    assert first._analyzer.nlp_engine is second._analyzer.nlp_engine
    # spaCy Language eszamanli cagri icin guvenli degil: kilit de paylasilmali.
    assert first._analyze_lock is second._analyze_lock


def test_failed_spacy_load_is_not_cached(monkeypatch):
    class _BoomProvider:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("simulated spaCy model load failure")

    monkeypatch.setattr("presidio_analyzer.nlp_engine.NlpEngineProvider", _BoomProvider)

    PresidioDetector([], spacy_model="nonexistent-model-xyz")

    assert ("en", "nonexistent-model-xyz") not in presidio_detector._NLP_ENGINES


def test_shared_model_vocabulary_does_not_grow_across_analyses():
    detector = PresidioDetector([])
    if detector._analyzer is None or detector._analyzer.nlp_engine is None:
        pytest.skip("spaCy modeli yuklenemedi")
    strings = detector._analyzer.nlp_engine.nlp[detector.language].vocab.strings

    async def scan(count: int):
        for _ in range(count):
            unique = " ".join(uuid.uuid4().hex for _ in range(30))
            await detector.detect(f"John Smith wrote {unique} from London, person@example.com\n")

    asyncio.run(scan(1))
    baseline = len(strings)
    asyncio.run(scan(10))

    assert len(strings) == baseline
