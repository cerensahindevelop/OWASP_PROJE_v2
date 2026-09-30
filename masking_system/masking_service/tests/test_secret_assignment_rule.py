"""generic_secret_assignment (migration c4f8a2d6e1b9): kisa parolalar ve ek
anahtar kelimeler yakalanir; ortam degiskeni referanslari, yer tutucular ve
kod ifadeleri parola sayilmaz. Kural, migrate edilmis DB'den okunur."""
from __future__ import annotations

import pytest

from app.services.mapping_service import load_active_rules
from app.services.rule_engine import find_matches
from app.services.validators import validate_secret_value


@pytest.fixture()
def secret_rule(db_session):
    [rule] = [rule for rule in load_active_rules(db_session) if rule.rule_name == "generic_secret_assignment"]
    assert rule.validator_name == "secret_value"
    return rule


def _values(rule, text: str) -> list[str]:
    matches, _ = find_matches([rule], text)
    return [match.original_value for match in matches]


@pytest.mark.parametrize("text,value", [
    ('password = "Sup3rGizli!"\n', '"Sup3rGizli!"'),
    ('db_password = "abc123"\n', '"abc123"'),
    ("DB_PASSWORD=Sup3rGizli!\n", "Sup3rGizli!"),
    ('{"Password": "Sup3rGizli!"}\n', '"Sup3rGizli!"'),
    ('"Server=sql01;User Id=sa;Password=Sup3rGizli!;"', "Sup3rGizli!"),
    ('"Server=sql01;Uid=sa;Pwd=abc123;"', "abc123"),
    ("pwd=Sup3rGizli!\n", "Sup3rGizli!"),
    ("sifre=Sup3rGizli!\n", "Sup3rGizli!"),
    ("kullanici_sifresi: 'Gizli123'\n", "'Gizli123'"),
    ("smtp_pass = Mail2024!\n", "Mail2024!"),
    ("passphrase: 'correct horse battery'\n", "'correct horse battery'"),
    ("client-secret: SYNTHETIC-CLIENT-SECRET-9842\n", "SYNTHETIC-CLIENT-SECRET-9842"),
    ("spring.datasource.password=Db$ecret1\n", "Db$ecret1"),
])
def test_short_and_varied_passwords_are_caught(secret_rule, text, value):
    assert _values(secret_rule, text) == [value]


@pytest.mark.parametrize("text", [
    "password = ${DB_PASSWORD}\n",
    "DB_PASSWORD=$DB_PASS\n",
    'password: "%PASSWORD%"\n',
    "password: {{ vault.db_password }}\n",
    'password = "changeme"\n',
    "password = null\n",
    'token = "{token}"\n',
    "password = request_password\n",
    "secret_key = s3_connection\n",
    "token = tokenizer\n",
    "token: Optional[str] = None\n",
    "invalidation_token: CacheInvalidationToken\n",
    "token:\n    entities = []\n",
    "token_count = 100000\n",
    "password_min_length = 123456\n",
    "token_url = https://login.example.com/oauth\n",
    "password_file = /etc/app/passwd\n",
    "Token: '#dddddd'\n",
    'password = "******"\n',
    "passport_number = U12345678\n",
    "bypass = enabled1\n",
])
def test_references_placeholders_and_code_are_not_passwords(secret_rule, text):
    assert _values(secret_rule, text) == []


def test_validator_keeps_dollar_prefixed_real_password():
    assert validate_secret_value("$ecret123") is True
    assert validate_secret_value("$DB_PASS") is False
