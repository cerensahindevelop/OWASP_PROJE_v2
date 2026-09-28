"""SqlAlchemyFiltreKuraliRepository icin testler - ozellikle Adim 4'te
eklenen desen_sifreli_mi decrypt entegrasyonu.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import InvalidToken

from app.core.crypto import encrypt_value
from app.db.models import FilterRule
from app.repository.filter_rule_repository import SqlAlchemyFiltreKuraliRepository


def _add_rule(db_session, **overrides) -> FilterRule:
    defaults = dict(
        rule_name="pytest_repo_rule",
        category="pytest_kategori",
        source_layer="katman1",
        pattern_type="regex",
        regex_pattern=r"\bplaintext\b",
        regex_flags=None,
        is_pattern_encrypted=False,
        placeholder_prefix="mask_pytest_repo",
        is_active=True,
    )
    defaults.update(overrides)
    rule = FilterRule(**defaults)
    db_session.add(rule)
    db_session.flush()
    return rule


def test_plaintext_rule_is_returned_unchanged(db_session):
    _add_rule(db_session, rule_name="pytest_plain_rule")

    rules = SqlAlchemyFiltreKuraliRepository(db_session).list_active_detection_rules()
    matching = [r for r in rules if r.rule_name == "pytest_plain_rule"]
    assert len(matching) == 1
    assert matching[0].regex_pattern == r"\bplaintext\b"


def test_encrypted_rule_is_decrypted_transparently(db_session):
    real_pattern = r"\bAtlasProjesi\b"
    _add_rule(
        db_session,
        rule_name="pytest_encrypted_rule",
        regex_pattern=encrypt_value(real_pattern),
        regex_flags="i",
        is_pattern_encrypted=True,
    )

    rules = SqlAlchemyFiltreKuraliRepository(db_session).list_active_detection_rules()
    matching = [r for r in rules if r.rule_name == "pytest_encrypted_rule"]
    assert len(matching) == 1
    assert matching[0].regex_pattern == real_pattern


def test_corrupted_encrypted_rule_raises_loudly_not_silently_skipped(db_session):
    """Decrypt basarisiz olursa (bozuk veri/anahtar rotasyonu) bu sessizce
    atlanmamali - run'i durduran acik bir hata firlatilmali. Sessiz atlama,
    o kuralin korumasi gereken kurumsal terimi maskelemeden birakirdi."""
    _add_rule(
        db_session,
        rule_name="pytest_corrupted_rule",
        regex_pattern="bu-gecerli-bir-fernet-token-degil",
        is_pattern_encrypted=True,
    )

    with pytest.raises(InvalidToken):
        SqlAlchemyFiltreKuraliRepository(db_session).list_active_detection_rules()


def test_inactive_encrypted_rule_is_not_loaded_at_all(db_session):
    """is_active=False (orn. supheli terim) kurallar hic listeye
    girmemeli - bu durumda decrypt bile denenmemeli."""
    _add_rule(
        db_session,
        rule_name="pytest_inactive_encrypted_rule",
        regex_pattern="bu-gecerli-bir-fernet-token-degil-ama-onemli-degil",
        is_pattern_encrypted=True,
        is_active=False,
    )

    rules = SqlAlchemyFiltreKuraliRepository(db_session).list_active_detection_rules()
    names = [r.rule_name for r in rules]
    assert "pytest_inactive_encrypted_rule" not in names
