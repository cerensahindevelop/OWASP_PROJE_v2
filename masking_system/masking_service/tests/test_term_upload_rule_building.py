"""Kurumsal terim sozlugu ozelligi / Adim 4: kural uretimi + kategori
dogrulama testleri.
"""

from __future__ import annotations

import re

import pytest

from app.core.crypto import decrypt_value
from app.db.models import FilterRule
from app.services.rule_engine import _compile_flags
from app.services.term_upload import (
    TermUploadValidationError,
    build_filter_rule,
    normalize_category,
    placeholder_prefix_for_category,
    rule_name_for_term,
    validate_category_choice,
)


# --------------------------------------------------------------------------
# normalize_category / placeholder_prefix_for_category
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Proje Kodu", "proje_kodu"),
        ("proje-kodu", "proje_kodu"),
        ("PROJE_KODU", "proje_kodu"),
        ("  proje   kodu  ", "proje_kodu"),
    ],
)
def test_normalize_category_produces_canonical_form(raw, expected):
    assert normalize_category(raw) == expected


def test_normalize_category_rejects_empty():
    with pytest.raises(TermUploadValidationError):
        normalize_category("   ")


def test_placeholder_prefix_matches_convention():
    assert placeholder_prefix_for_category("proje_kodu") == "mask_kurumsal_ifade"


# --------------------------------------------------------------------------
# rule_name_for_term - deterministik idempotency temeli
# --------------------------------------------------------------------------


def test_rule_name_is_deterministic_for_same_term_and_category():
    a = rule_name_for_term("proje_kodu", "Atlas")
    b = rule_name_for_term("proje_kodu", "Atlas")
    assert a == b


def test_rule_name_is_case_insensitive():
    """Case-insensitive eslesme (regex_flags='i') kullandigimiz icin
    'Atlas'/'ATLAS'/'atlas' AYNI rule_name'e cozulmeli - aksi halde ayni
    terimin farkli yazimlari yanlislikla ayri kurallar olarak eklenirdi."""
    assert rule_name_for_term("proje_kodu", "Atlas") == rule_name_for_term("proje_kodu", "ATLAS")
    assert rule_name_for_term("proje_kodu", "Atlas") == rule_name_for_term("proje_kodu", "atlas")


def test_rule_name_differs_by_category():
    a = rule_name_for_term("proje_kodu", "Atlas")
    b = rule_name_for_term("sunucu_adi", "Atlas")
    assert a != b


def test_rule_name_fits_under_column_length_limit():
    long_term = "x" * 300
    name = rule_name_for_term("proje_kodu", long_term)
    assert len(name) <= 100


# --------------------------------------------------------------------------
# build_filter_rule
# --------------------------------------------------------------------------


def test_build_filter_rule_ok_status_is_active_and_encrypted_correctly():
    rule = build_filter_rule(term="Atlas", category="proje_kodu", status="ok", priority=500)

    assert rule.rule_name == rule_name_for_term("proje_kodu", "Atlas")
    assert rule.category == "proje_kodu"
    assert rule.source_layer == "katman1"
    assert rule.pattern_type == "regex"
    assert rule.regex_flags == "i"
    assert rule.is_pattern_encrypted is True
    assert rule.placeholder_prefix == "mask_kurumsal_ifade"
    assert rule.priority == 500
    assert rule.is_active is True

    decrypted = decrypt_value(rule.regex_pattern)
    compiled = re.compile(decrypted, _compile_flags(rule.regex_flags))
    assert compiled.search("Atlas") is not None
    assert compiled.search("atlantis") is None  # ilgisiz bir kelimenin ici yanlislikla eslesmemeli


def test_build_filter_rule_suspicious_status_is_inactive():
    rule = build_filter_rule(term="data", category="proje_kodu", status="suspicious", priority=500)
    assert rule.is_active is False


# --------------------------------------------------------------------------
# camelCase/ayrac-bitisik eslesme - "\b" tek basina bunu YAKALAYAMAZDI (iki
# harf arasinda asla sinir saymaz), bu yuzden orn. "girisYapanSicil" icindeki
# "Sicil" hic maskelenmezdi (sessiz false-negative). bkz. term_upload.py
# _CAMEL_TRANSITION dokstringi.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "term, text, should_match",
    [
        ("sicil", "girisYapanSicil", True),   # camelCase sonek
        ("sicil", "sicilNumarasi", True),     # camelCase onek
        ("sicil", "sicil_no", True),          # alt cizgi ayracli
        ("sicil", "sicil-no", True),          # tire ayracli
        ("sicil", "SICIL", True),             # tek basina, buyuk harf
        # free_right_continuation=True: sagda kucuk harfli HERHANGI bir devam
        # (orn. "-im", "-i" gibi ekler) da sinir sayilir, "sicilim" gibi
        # ilgisiz bir kelimenin ici de dahil - bkz. rule_engine.py
        # compound_aware_boundary_pattern dokstringi. Terimler elle
        # onaylandigi icin bu risk BILEREK kabul edildi (kullanici talebi).
        ("sicil", "sicilim", True),
        ("sicil", "kisisicili", False),        # sol sinir hala sikidir (sadece sag gevsetildi) - eslesmemeli
        ("sicil", "sicil01", True),           # rakam sonek
        ("sicil", "01sicil", True),           # rakam onek
        ("atlas", "APIAtlas", True),          # kisaltma->Kelime sinirinin
        ("sicil", "sicilİşlemi", True),       # Turkce buyuk harf (İ) gecisi
    ],
)
def test_build_filter_rule_matches_camelcase_and_separator_adjacent_terms(term, text, should_match):
    rule = build_filter_rule(term=term, category="proje_kodu", status="ok", priority=500)
    decrypted = decrypt_value(rule.regex_pattern)
    compiled = re.compile(decrypted, _compile_flags(rule.regex_flags))
    assert (compiled.search(text) is not None) == should_match


def test_build_filter_rule_regex_pattern_is_never_plaintext_in_the_object():
    """regex_deseni her zaman sifreli olmali - terimin kendisi cikti
    nesnesinde duz metin olarak GORUNMEMELI (encrypt_value'nun donusu
    Fernet token'idir, orijinal terimi icermez)."""
    rule = build_filter_rule(term="GizliProjeKoduXYZ", category="proje_kodu", status="ok", priority=500)
    assert "GizliProjeKoduXYZ" not in rule.regex_pattern


# --------------------------------------------------------------------------
# validate_category_choice (DB'ye karsi)
# --------------------------------------------------------------------------


def test_validate_category_choice_allows_fresh_category(db_session):
    result = validate_category_choice(db_session, "pytest-yepyeni-kategori")
    assert result == "pytest_yepyeni_kategori"


def test_validate_category_choice_rejects_collision_with_seed_category(db_session):
    with pytest.raises(TermUploadValidationError):
        validate_category_choice(db_session, "project_name")


def test_validate_category_choice_allows_reupload_into_own_category(db_session):
    existing = FilterRule(
        rule_name="kurumsal_terim_pytest_kategorisi_aaaaaaaaaaaa",
        category="pytest_kategorisi",
        source_layer="katman1",
        pattern_type="regex",
        regex_pattern="sifreli-ornek",
        regex_flags="i",
        is_pattern_encrypted=True,
        placeholder_prefix="mask_pytest_kategorisi",
        is_active=True,
    )
    db_session.add(existing)
    db_session.flush()

    result = validate_category_choice(db_session, "Pytest Kategorisi")
    assert result == "pytest_kategorisi"
