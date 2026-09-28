"""Kurumsal terim sozlugu ozelligi / Adim 5: onizleme testleri."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models import FilterRule
from app.services.term_upload import (
    TermUploadValidationError,
    build_filter_rule,
    preview_term_upload,
    rule_name_for_term,
)


def _register_existing_term(db_session, *, category: str, term: str) -> None:
    rule = build_filter_rule(term=term, category=category, status="ok", priority=500)
    db_session.add(rule)
    db_session.flush()


def test_preview_classifies_mixed_file_correctly(db_session):
    _register_existing_term(db_session, category="pytest_preview_kat", term="ZatenKayitli")

    content = "\n".join(["Atlas", "Poseidon", "ZatenKayitli", "data", "import", "12"]).encode("utf-8")

    preview = preview_term_upload(db_session, filename="liste.txt", content=content, category="pytest_preview_kat")

    assert preview.category == "pytest_preview_kat"
    assert preview.total_found == 6
    assert sorted(preview.new_valid) == ["Atlas", "Poseidon"]
    assert preview.already_registered == ["ZatenKayitli"]
    assert {item.term for item in preview.new_suspicious} == {"data", "12"}
    assert {item.term for item in preview.rejected} == {"import"}
    assert preview.new_count == 4  # new_valid (2) + new_suspicious (2)
    assert preview.existing_count == 1
    assert preview.suspicious_count == 2
    assert preview.rejected_count == 1


def test_preview_never_writes_to_db(db_session):
    content = b"Atlas\nPoseidon\n"
    preview_term_upload(db_session, filename="liste.txt", content=content, category="pytest_preview_nowrite")

    rule_name = rule_name_for_term("pytest_preview_nowrite", "Atlas")
    existing = db_session.scalar(select(FilterRule.id).where(FilterRule.rule_name == rule_name))
    assert existing is None


def test_preview_deduplicates_case_variants(db_session):
    content = b"Atlas\nATLAS\natlas\nPoseidon\n"
    preview = preview_term_upload(db_session, filename="liste.txt", content=content, category="pytest_preview_dedupe")

    assert preview.total_found == 2
    assert sorted(preview.new_valid) == ["Atlas", "Poseidon"]


def test_preview_rejects_colliding_category_before_parsing(db_session):
    with pytest.raises(TermUploadValidationError):
        preview_term_upload(db_session, filename="liste.txt", content=b"Atlas\n", category="project_name")


def test_preview_empty_file_returns_zero_counts(db_session):
    preview = preview_term_upload(db_session, filename="liste.txt", content=b"\n\n  \n", category="pytest_preview_empty")
    assert preview.total_found == 0
    assert preview.new_count == 0
    assert preview.existing_count == 0
    assert preview.suspicious_count == 0
    assert preview.rejected_count == 0
