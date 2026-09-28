"""Kurumsal terim sozlugu ozelligi / Adim 1: sema testleri.

Migration'in upgrade/downgrade round-trip'i ve FilterRule.is_pattern_encrypted
alaninin temel CRUD davranisi.
"""

from __future__ import annotations

import subprocess
import sys

from app.db.models import FilterRule


def test_filter_rule_with_encrypted_pattern_flag_persists(db_session):
    rule = FilterRule(
        rule_name="kurumsal_terim_test_deadbeefcafe",
        category="proje_kodu",
        source_layer="katman1",
        pattern_type="regex",
        regex_pattern="sifreli-goruntu-metni",
        regex_flags="i",
        is_pattern_encrypted=True,
        placeholder_prefix="mask_proje_kodu",
        is_active=True,
    )
    db_session.add(rule)
    db_session.flush()

    fresh = db_session.get(FilterRule, rule.id)
    assert fresh.is_pattern_encrypted is True


def test_filter_rule_default_is_pattern_encrypted_false(db_session):
    rule = FilterRule(
        rule_name="test_plain_rule_xyz",
        category="test_category",
        source_layer="katman1",
        pattern_type="regex",
        regex_pattern=r"\bxyz\b",
        placeholder_prefix="mask_xyz",
    )
    db_session.add(rule)
    db_session.flush()

    fresh = db_session.get(FilterRule, rule.id)
    assert fresh.is_pattern_encrypted is False


def test_migration_upgrade_downgrade_round_trip():
    """alembic downgrade -1 + upgrade head'in hatasiz calistigini dogrular.
    Gercek subprocess ile - ayni anda acik bir db_session ile ayni
    connection'i paylasmadigindan emin olmak icin (DDL, ayri bir
    baglantida calismali)."""
    down = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "-1"],
        capture_output=True, text=True,
    )
    try:
        assert down.returncode == 0, down.stderr
    finally:
        up = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            capture_output=True, text=True,
        )
        assert up.returncode == 0, up.stderr
