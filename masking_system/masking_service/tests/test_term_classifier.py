"""Kurumsal terim sozlugu ozelligi / Adim 3: terim siniflandirma testleri."""

from __future__ import annotations

import pytest

from app.services.term_classifier import classify_term

# --------------------------------------------------------------------------
# ok - gercek kurumsal terimler
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "term",
    ["Poseidon", "Atlas42", "IcServisA", "Şirket-DB-01", "OdemeSistemi", "PROJE-KODU-7781"],
)
def test_realistic_corporate_terms_are_ok(term):
    result = classify_term(term)
    assert result.status == "ok"
    assert result.reason is None


# --------------------------------------------------------------------------
# rejected - Python anahtar kelime / stdlib modul cakismasi
# --------------------------------------------------------------------------


@pytest.mark.parametrize("term", ["import", "class", "def", "True", "False", "None", "return", "match", "case"])
def test_python_keywords_are_rejected(term):
    result = classify_term(term)
    assert result.status == "rejected"
    assert "anahtar kelime" in result.reason


@pytest.mark.parametrize("term", ["IMPORT", "Import", "TRUE", "Class", "MATCH"])
def test_python_keywords_are_rejected_case_insensitively(term):
    """regex_flags='i' ile eslesecegi icin buyuk/kucuk harf farki
    onemli degil - IMPORT da gercek `import` anahtar kelimesiyle cakisir."""
    result = classify_term(term)
    assert result.status == "rejected"


@pytest.mark.parametrize("term", ["os", "sys", "json", "re", "OS", "Json", "asyncio", "sqlite3"])
def test_stdlib_module_names_are_rejected(term):
    result = classify_term(term)
    assert result.status == "rejected"
    assert "standart kutuphane" in result.reason


def test_empty_term_is_rejected():
    result = classify_term("   ")
    assert result.status == "rejected"
    assert result.reason == "bos terim"


# --------------------------------------------------------------------------
# suspicious - tehlikeli ama reddedilmeyen (varsayilan pasif eklenecek)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("term", ["a", "ab", "X", "12"])
def test_very_short_terms_are_suspicious(term):
    result = classify_term(term)
    assert result.status == "suspicious"
    assert "kisa" in result.reason


@pytest.mark.parametrize("term", ["123", "007", "999999"])
def test_purely_numeric_terms_are_suspicious(term):
    result = classify_term(term)
    assert result.status == "suspicious"
    assert "sayisal" in result.reason


@pytest.mark.parametrize("term", ["data", "test", "user", "main", "admin", "DATA", "Test", "config"])
def test_common_english_words_are_suspicious(term):
    result = classify_term(term)
    assert result.status == "suspicious"
    assert "yaygin" in result.reason


@pytest.mark.parametrize("term", ["veri", "kullanici", "ana", "deneme", "sistem", "Veri", "KULLANICI"])
def test_common_turkish_words_are_suspicious(term):
    result = classify_term(term)
    assert result.status == "suspicious"
    assert "yaygin" in result.reason


def test_classification_is_deterministic_for_same_term():
    a = classify_term("Poseidon")
    b = classify_term("Poseidon")
    assert a == b
