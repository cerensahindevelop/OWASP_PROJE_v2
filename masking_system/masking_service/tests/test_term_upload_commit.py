"""Kurumsal terim sozlugu ozelligi / Adim 6: atomik commit testleri."""

from __future__ import annotations

import re

import pytest
from sqlalchemy import select

from app.core.crypto import decrypt_value
from app.db.models import FilterRule
from app.services.rule_engine import _compile_flags
from app.services.mapping_service import get_or_create_context, get_or_create_mapping
from app.repository.filter_rule_repository import SqlAlchemyFiltreKuraliRepository
from app.services.rule_admin import RuleValidationError, set_rule_active
from app.services.term_upload import (
    TermUploadValidationError,
    activate_corporate_term,
    add_single_corporate_term,
    commit_term_upload,
    delete_corporate_term,
    list_corporate_terms,
    rule_name_for_term,
)


def _rule_row(db_session, category: str, term: str) -> FilterRule | None:
    rule_name = rule_name_for_term(category, term)
    return db_session.scalar(select(FilterRule).where(FilterRule.rule_name == rule_name))


def test_commit_writes_all_new_terms_with_correct_counts(db_session):
    content = b"Atlas\nPoseidon\nZeus\n"
    result = commit_term_upload(db_session, filename="liste.txt", content=content, category="pytest_commit_kat1")

    assert result.added_count == 3
    assert result.skipped_count == 0
    assert result.rejected_count == 0
    assert result.suspicious_added_count == 0

    for term in ("Atlas", "Poseidon", "Zeus"):
        row = _rule_row(db_session, "pytest_commit_kat1", term)
        assert row is not None
        assert row.is_active is True
        assert decrypt_value(row.corporate_term_encrypted) == term
        compiled = re.compile(decrypt_value(row.regex_pattern), _compile_flags(row.regex_flags))
        assert compiled.search(term) is not None


def test_commit_marks_suspicious_terms_inactive(db_session):
    content = b"data\nAtlas\n"
    result = commit_term_upload(db_session, filename="liste.txt", content=content, category="pytest_commit_kat2")

    assert result.added_count == 2
    assert result.suspicious_added_count == 1

    suspicious_row = _rule_row(db_session, "pytest_commit_kat2", "data")
    assert suspicious_row.is_active is False
    ok_row = _rule_row(db_session, "pytest_commit_kat2", "Atlas")
    assert ok_row.is_active is True

    listed = next(
        item
        for item in list_corporate_terms(db_session)
        if item.id == suspicious_row.id
    )
    assert listed.inactive_reason
    assert "yaygin" in listed.inactive_reason


def test_inactive_corporate_term_requires_explicit_confirmation_then_activates(db_session):
    commit_term_upload(
        db_session,
        filename="liste.txt",
        content=b"data\n",
        category="pytest_activation_flow",
    )
    inactive = next(
        item
        for item in list_corporate_terms(db_session)
        if item.category == "pytest_activation_flow"
    )
    assert inactive.is_active is False
    assert inactive.inactive_reason

    with pytest.raises(TermUploadValidationError, match="onaylamalısınız"):
        activate_corporate_term(
            db_session,
            inactive.id,
            confirmed_sensitive=False,
        )

    activated = activate_corporate_term(
        db_session,
        inactive.id,
        confirmed_sensitive=True,
    )

    assert activated.is_active is True
    assert activated.inactive_reason is None
    assert db_session.get(FilterRule, inactive.id).is_active is True


def test_commit_excludes_rejected_terms_entirely(db_session):
    content = b"import\nAtlas\n"
    result = commit_term_upload(db_session, filename="liste.txt", content=content, category="pytest_commit_kat3")

    assert result.added_count == 1
    assert result.rejected_count == 1
    assert _rule_row(db_session, "pytest_commit_kat3", "import") is None


def test_commit_is_idempotent_second_upload_only_skips(db_session):
    content = b"Atlas\nPoseidon\n"
    first = commit_term_upload(db_session, filename="liste.txt", content=content, category="pytest_commit_kat4")
    assert first.added_count == 2
    assert first.skipped_count == 0

    second = commit_term_upload(db_session, filename="liste.txt", content=content, category="pytest_commit_kat4")
    assert second.added_count == 0
    assert second.skipped_count == 2

    all_rows = db_session.scalars(
        select(FilterRule).where(FilterRule.category == "pytest_commit_kat4")
    ).all()
    assert len(all_rows) == 2  # tekrar yuklemede DUPLICATE satir olusmadi


def test_commit_partial_overlap_only_adds_the_new_ones(db_session):
    commit_term_upload(db_session, filename="ilk.txt", content=b"Atlas\n", category="pytest_commit_kat5")
    second = commit_term_upload(
        db_session, filename="ikinci.txt", content=b"Atlas\nPoseidon\n", category="pytest_commit_kat5",
    )
    assert second.added_count == 1
    assert second.skipped_count == 1


def test_commit_does_not_call_db_commit_itself(db_session):
    commit_term_upload(db_session, filename="liste.txt", content=b"Atlas\n", category="pytest_commit_kat7")
    # Hala ayni (rollback fixture'inin yonettigi) transaction icindeyiz -
    # fonksiyon kendi commit'ini atmadiysa bu, db_session'in disaridan
    # commit edilmedigi surece hicbir sekilde KALICI olmadigi anlamina
    # gelir; conftest.py'nin rollback'i test sonunda temizleyecek.
    assert db_session.in_transaction()


def test_list_and_soft_delete_keep_historical_mapping_then_allow_reupload(db_session):
    term = "Zeta Internal Ledger"
    category = "pytest_term_management"
    commit_term_upload(
        db_session,
        filename="liste.txt",
        content=f"{term}\n".encode("utf-8"),
        category=category,
    )
    listed = list_corporate_terms(db_session)
    selected = next(item for item in listed if item.category == category)
    assert selected.term == term
    assert selected.mapping_count == 0

    context = get_or_create_context(db_session, "term-management", "P-TERM-MGMT", "main")
    rule = next(
        item
        for item in SqlAlchemyFiltreKuraliRepository(db_session).list_active_detection_rules()
        if item.id == selected.id
    )
    mapping, _created = get_or_create_mapping(db_session, context.id, rule, term)

    deleted = delete_corporate_term(db_session, selected.id)
    assert deleted.term == term
    assert deleted.is_active is False
    assert deleted.mapping_count == 1
    assert all(item.id != selected.id for item in list_corporate_terms(db_session))
    deleted_rows = list_corporate_terms(db_session, include_deleted=True)
    assert any(item.id == selected.id and item.deleted_at is not None for item in deleted_rows)
    assert db_session.get(FilterRule, selected.id) is not None
    assert db_session.get(type(mapping), mapping.id) is not None
    with pytest.raises(RuleValidationError, match="Silinmis kurumsal"):
        set_rule_active(db_session, selected.rule_name, True)

    result = commit_term_upload(
        db_session,
        filename="liste.txt",
        content=f"{term}\n".encode("utf-8"),
        category=category,
    )
    assert result.added_count == 1
    restored_entry = next(item for item in list_corporate_terms(db_session) if item.id == selected.id)
    assert restored_entry.is_active is True
    assert restored_entry.mapping_count == 1


def test_single_term_add_requires_confirmation_and_activates_explicitly_confirmed_term(db_session):
    with pytest.raises(TermUploadValidationError, match="onaylamalisiniz"):
        add_single_corporate_term(
            db_session,
            term="data",
            title="pytest single term",
            confirmed_sensitive=False,
        )

    created = add_single_corporate_term(
        db_session,
        term="data",
        title="pytest single term",
        confirmed_sensitive=True,
    )
    assert created.term == "data"
    assert created.category == "pytest_single_term"
    assert created.is_active is True
    assert any(item.id == created.id for item in list_corporate_terms(db_session))


def test_single_term_add_rejects_multiple_lines(db_session):
    with pytest.raises(TermUploadValidationError, match="yalnizca bir"):
        add_single_corporate_term(
            db_session,
            term="Atlas\nPoseidon",
            title="pytest single line",
            confirmed_sensitive=True,
        )


def _rows_for_term(db_session, term: str) -> list[FilterRule]:
    return [
        row for row in db_session.scalars(
            select(FilterRule).where(FilterRule.rule_name.like("kurumsal_terim_%"))
        ).all()
        if row.corporate_term_encrypted and decrypt_value(row.corporate_term_encrypted).casefold() == term.casefold()
    ]


def test_same_term_under_another_title_is_not_inserted_again(db_session):
    first = commit_term_upload(
        db_session, filename="a.txt", content="Kxqvorn Platformu\n".encode(), category="pytest_dup_a",
    )
    assert first.added_count == 1

    second = commit_term_upload(
        db_session, filename="b.txt", content="KXQVORN PLATFORMU\nZyphrax\n".encode(), category="pytest_dup_b",
    )
    assert second.added_count == 1
    assert second.skipped_count == 1
    assert [row.category for row in _rows_for_term(db_session, "Kxqvorn Platformu")] == ["pytest_dup_a"]


def test_preview_lists_term_registered_under_another_title(db_session):
    from app.services.term_upload import preview_term_upload

    commit_term_upload(db_session, filename="a.txt", content=b"Vranoqel\n", category="pytest_dup_c")
    preview = preview_term_upload(
        db_session, filename="b.txt", content=b"vranoqel\nQuildomer\n", category="pytest_dup_d",
    )
    assert preview.already_registered == ["vranoqel"]
    assert preview.new_valid == ["Quildomer"]


def test_single_add_refuses_term_active_under_another_title(db_session):
    commit_term_upload(db_session, filename="a.txt", content=b"Brontexa\n", category="pytest_dup_e")
    with pytest.raises(TermUploadValidationError, match="pytest_dup_e"):
        add_single_corporate_term(
            db_session, term="brontexa", title="pytest dup f", confirmed_sensitive=True,
        )
    assert len(_rows_for_term(db_session, "Brontexa")) == 1


def test_single_add_activates_existing_passive_term_without_new_row(db_session):
    if _rows_for_term(db_session, "data"):
        pytest.skip("ortak DB'de 'data' zaten kayitli")
    commit_term_upload(db_session, filename="a.txt", content=b"data\n", category="pytest_dup_g")
    passive = next(row for row in _rows_for_term(db_session, "data") if row.category == "pytest_dup_g")
    before = len(_rows_for_term(db_session, "data"))
    assert passive.is_active is False

    created = add_single_corporate_term(db_session, term="data", title="pytest dup h", confirmed_sensitive=True)
    assert created.is_active is True
    assert len(_rows_for_term(db_session, "data")) == before


def test_deleted_term_can_be_added_under_another_title(db_session):
    commit_term_upload(db_session, filename="a.txt", content=b"Morvatex\n", category="pytest_dup_i")
    delete_corporate_term(db_session, _rule_row(db_session, "pytest_dup_i", "Morvatex").id)

    result = commit_term_upload(db_session, filename="b.txt", content=b"Morvatex\n", category="pytest_dup_j")
    assert result.added_count == 1
