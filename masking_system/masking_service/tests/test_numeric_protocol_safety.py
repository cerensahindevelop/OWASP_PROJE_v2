"""Protocol regressions independent of detectors and programming languages."""
import pytest

from app.core.crypto import encrypt_value, hash_value
from app.db.models import ValueMapping
from app.services.detectors import synthetic_llm_rule
from app.services.mapping_service import get_or_create_context, get_or_create_mapping
from app.services.rule_engine import JSON_NUMERIC_PLACEHOLDER_RE, make_json_numeric_placeholder, reverse_text
from app.services.unmasker import load_context_mappings


def test_numeric_tokens_are_unique_across_rules_and_contexts(db_session):
    tokens = []
    for project in ("numeric-a", "numeric-b"):
        context = get_or_create_context(db_session, project, "test", "main")
        for category, value in (("ALPHA", "1234567"), ("BETA", "7654321")):
            mapping, _ = get_or_create_mapping(db_session, context.id, synthetic_llm_rule(category), value, numeric=True)
            tokens.append(mapping.placeholder_value)
            assert int(mapping.placeholder_value) <= 2**53 - 1
            assert str(int(float(mapping.placeholder_value))) == mapping.placeholder_value
    assert len(set(tokens)) == 4


def test_legacy_collision_is_left_unresolved_for_both_contexts(db_session):
    token = "9111990000000001"
    for project, value in (("legacy-a", "1234567"), ("legacy-b", "7654321")):
        context = get_or_create_context(db_session, project, "test", "main")
        db_session.add(ValueMapping(context_id=context.id, rule_id=None,
            original_value_encrypted=encrypt_value(value), original_value_plain=value,
            original_value_hash=hash_value(context.id, value), placeholder_value=token))
    db_session.flush()
    for project in ("legacy-a", "legacy-b"):
        _, mappings = load_context_mappings(db_session, project, "test", "main")
        assert reverse_text(token, mappings) == (token, 0, [token])


def test_legacy_numeric_mapping_is_readable_but_not_reused(db_session):
    context = get_or_create_context(db_session, "legacy-only", "test", "main")
    value, token = "1234567", "9111990000000001"
    db_session.add(ValueMapping(context_id=context.id, rule_id=None,
        original_value_encrypted=encrypt_value(value), original_value_plain=value,
        original_value_hash=hash_value(context.id, f"\x00json-numeric\x00{value}"), placeholder_value=token))
    db_session.flush()
    mapping, created = get_or_create_mapping(db_session, context.id, synthetic_llm_rule("ID"), value, numeric=True)
    assert created and mapping.placeholder_value != token
    _, mappings = load_context_mappings(db_session, "legacy-only", "test", "main")
    assert reverse_text(token, mappings) == (value, 1, [])


@pytest.mark.parametrize("counter", [1, 2, 999999999])
def test_numeric_protocol_boundaries(counter):
    token = make_json_numeric_placeholder(counter)
    assert JSON_NUMERIC_PLACEHOLDER_RE.fullmatch(token)
    assert int(token) == int(float(token))


@pytest.mark.parametrize("counter", [0, -1, 1000000000])
def test_exhausted_counter_never_creates_an_unreadable_token(counter):
    with pytest.raises(ValueError):
        make_json_numeric_placeholder(counter)
