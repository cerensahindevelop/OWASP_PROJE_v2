"""Asama 8: Presidio/spaCy analizi olay dongusunu bloke etmemeli.

Ayni olay dongusunde eszamanli LLM istekleri beklerken CPU-bound Presidio
analizi donguyu kilitlerse, httpx/asyncio.wait_for zamanlayicilari sahte
zaman asimi uretir. Analiz ayri bir thread'de calismali; ayni detector
ornegi paylasildigi icin analyzer cagrilari birbirine karismamali.
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

from app.services.presidio_detector import PresidioDetector

MARKER = "secret@example.com"


class _SlowAnalyzer:
    """Gercek spaCy yerine gecen, CPU-bound isi time.sleep ile taklit eden analyzer."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.thread_ids: list[int] = []
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def analyze(self, *, text: str, language: str, entities=None, nlp_artifacts=None):
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            self.thread_ids.append(threading.get_ident())
            time.sleep(self.delay)
            if MARKER not in text:
                return []
            start = text.index(MARKER)
            return [SimpleNamespace(entity_type="EMAIL_ADDRESS", start=start, end=start + len(MARKER),
                                    score=0.91, recognition_metadata={})]
        finally:
            with self._lock:
                self.active -= 1


def _detector(monkeypatch, analyzer) -> PresidioDetector:
    # Gercek spaCy modelini yuklemeden: analyzer'i dogrudan sahtesiyle degistir.
    monkeypatch.setattr(PresidioDetector, "_build_analyzer", lambda self: None)
    detector = PresidioDetector([])
    detector._analyzer = analyzer
    return detector


def test_presidio_analysis_does_not_block_event_loop(monkeypatch):
    analyzer = _SlowAnalyzer(delay=0.3)
    detector = _detector(monkeypatch, analyzer)

    async def run():
        ticks = 0
        stop = asyncio.Event()

        async def ticker():
            nonlocal ticks
            while not stop.is_set():
                ticks += 1
                await asyncio.sleep(0.01)

        tick_task = asyncio.create_task(ticker())
        await asyncio.sleep(0)
        output = await detector.detect(f"mail: {MARKER}\n")
        stop.set()
        await tick_task
        return output, ticks

    output, ticks = asyncio.run(run())

    assert [result.deger for result in output.results] == [MARKER]
    assert analyzer.thread_ids and all(ident != threading.get_ident() for ident in analyzer.thread_ids)
    # 0.3 sn'lik analiz boyunca dongu calismaya devam etmeli (bloke olsaydi ~1 tik).
    assert ticks >= 10


def test_shared_detector_serializes_analyzer_calls_across_threads(monkeypatch):
    analyzer = _SlowAnalyzer(delay=0.05)
    detector = _detector(monkeypatch, analyzer)

    async def run():
        return await asyncio.gather(*(
            detector.detect(f"file {index}: {MARKER}\n" if index % 2 else f"file {index}\n")
            for index in range(6)
        ))

    outputs = asyncio.run(run())

    assert analyzer.max_active == 1
    for index, output in enumerate(outputs):
        expected = 1 if index % 2 else 0
        assert len(output.results) == expected
