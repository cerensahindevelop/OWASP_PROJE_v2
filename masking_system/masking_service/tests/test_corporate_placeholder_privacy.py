"""Project titles stay private in new exports; historical tokens still restore."""

from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.crypto import decrypt_value, encrypt_value, hash_value
from app.db.models import ValueMapping
from app.services.mapping_service import (
    get_or_create_context,
    get_or_create_mapping,
    load_active_rules,
    mask_relative_path,
)
from app.services.rule_engine import reverse_text
from app.services.term_upload import build_filter_rule, list_corporate_terms


@pytest.mark.parametrize("title", ["poseidon", "orion_personel_platformu", "2fa_projesi"])
def test_title_never_enters_new_rule_placeholder(db_session, title):
    rule = build_filter_rule(term="ZetaLedger", category=title, status="ok", priority=1)
    db_session.add(rule)
    db_session.flush()
    assert rule.placeholder_prefix == "mask_kurumsal_ifade"
    spec = next(r for r in load_active_rules(db_session) if r.id == rule.id)
    context = get_or_create_context(db_session, title, "P-PRIVACY", "main")
    mapping, created = get_or_create_mapping(db_session, context.id, spec, "ZetaLedger")
    assert created
    assert mapping.placeholder_value.startswith("mask_kurumsal_ifade_")
    assert title not in mapping.placeholder_value


@pytest.mark.parametrize("old_prefix", ["mask_poseidon", "mask_kurumsal_ifade_poseidon"])
@pytest.mark.parametrize("other_detector", [False, True])
def test_saved_rules_and_mappings_stop_reusing_titles_without_breaking_restore(
    db_session, old_prefix, other_detector,
):
    rule = build_filter_rule(term="ZetaLedger", category="poseidon", status="ok", priority=1)
    rule.placeholder_prefix = old_prefix  # Simulate a rule saved before the fix.
    db_session.add(rule)
    db_session.flush()
    context = get_or_create_context(db_session, "poseidon", "P-LEGACY", "main")
    original = "ZetaLedger"
    original_hash = hash_value(context.id, original)
    old_token = f"{old_prefix}_42"
    legacy = ValueMapping(
        context_id=context.id, rule_id=rule.id,
        original_value_hash=original_hash, original_value_plain=original,
        original_value_encrypted=encrypt_value(original), placeholder_value=old_token,
    )
    db_session.add(legacy)
    db_session.flush()

    spec = next(r for r in load_active_rules(db_session) if r.id == rule.id)
    assert spec.placeholder_prefix == "mask_kurumsal_ifade"
    record = next(r for r in list_corporate_terms(db_session) if r.id == rule.id)
    assert record.placeholder_prefix == "mask_kurumsal_ifade"
    if other_detector:
        spec = replace(spec, id=None, rule_name="llm:organization", placeholder_prefix="mask_organization")

    cache = {}
    current, created = get_or_create_mapping(db_session, context.id, spec, original, cache)
    assert created and current.id != legacy.id
    assert current.placeholder_value.startswith("mask_kurumsal_ifade_")
    assert "poseidon" not in current.placeholder_value
    assert legacy.placeholder_value == old_token
    assert current.original_value_hash == original_hash
    assert get_or_create_mapping(db_session, context.id, spec, original, cache) == (current, False)
    assert get_or_create_mapping(db_session, context.id, spec, original) == (current, False)

    # The public path uses the same neutral mapping as content, even with an
    # old configured prefix passed directly instead of through the repository.
    corporate_spec = next(r for r in load_active_rules(db_session) if r.id == rule.id)
    corporate_spec = replace(corporate_spec, placeholder_prefix=old_prefix)
    path, mappings = mask_relative_path(
        db_session, context, Path("ZetaLedger/config.txt"), {}, rules=[corporate_spec],
    )
    assert str(path) == f"{current.placeholder_value}/config.txt"
    assert mappings[0].id == current.id
    rows = db_session.scalars(select(ValueMapping).where(ValueMapping.context_id == context.id)).all()
    reverse_map = {row.placeholder_value: decrypt_value(row.original_value_encrypted) for row in rows}
    for token in (old_token, current.placeholder_value):
        restored, count, unresolved = reverse_text(token, reverse_map)
        assert (restored, count, unresolved) == (original, 1, [])
