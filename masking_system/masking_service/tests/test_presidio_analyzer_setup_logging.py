"""Asama 2 / Adim 1a: presidio_detector.py'deki sessiz `except Exception`
bloklarina eklenen loglamanin, davranisi (None'a dusup fallback'e gecmeyi)
BOZMADIGINI ve gercekten bir uyari log'u urettigini dogrular.

Gercek spaCy/Presidio kurulumuna bagimli olmamak icin ilgili sinif/fonksiyon
dogrudan monkeypatch ile bozuluyor - boylece test, spaCy modeli kurulu olsun
ya da olmasin deterministik calisir.
"""

from __future__ import annotations

import logging

import pytest

from app.services.presidio_detector import PresidioDetector


def test_email_validation_uses_packaged_suffixes_without_network(monkeypatch):
    import requests
    def deny_network(*args, **kwargs):
        raise AssertionError("email validation attempted network access")
    monkeypatch.setattr(requests.Session, "get", deny_network)
    detector = PresidioDetector([])
    recognizer = next(item for item in detector._analyzer.registry.recognizers
                      if item.name == "OfflineEmailRecognizer")
    assert recognizer.validate_result("person@example.com")


def test_analyzer_setup_failure_is_logged_and_falls_back_to_none(monkeypatch, caplog):
    def _boom(*args, **kwargs):
        raise RuntimeError("simulated Presidio setup failure")

    monkeypatch.setattr("app.services.presidio_detector.AnalyzerEngine", _boom)

    with caplog.at_level(logging.WARNING, logger="app.services.presidio_detector"):
        detector = PresidioDetector([])

    assert detector._analyzer is None
    assert any("Presidio analyzer kurulumu basarisiz" in record.message for record in caplog.records)


def test_spacy_model_load_failure_is_logged_and_falls_back_to_no_nlp_engine(monkeypatch, caplog):
    if not hasattr(__import__("presidio_analyzer"), "AnalyzerEngine"):
        pytest.skip("presidio-analyzer kurulu degil")

    class _BoomProvider:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("simulated spaCy model load failure")

    monkeypatch.setattr("presidio_analyzer.nlp_engine.NlpEngineProvider", _BoomProvider)

    with caplog.at_level(logging.WARNING, logger="app.services.presidio_detector"):
        detector = PresidioDetector([], spacy_model="nonexistent-model-xyz")

    # Ust seviye kurulum yine de basarili olmali (nlp_engine=None ile) -
    # bu davranis degisikligi degil, sadece artik loglaniyor.
    assert detector._analyzer is not None
    assert any("spaCy NLP modeli yuklenemedi" in record.message for record in caplog.records)
