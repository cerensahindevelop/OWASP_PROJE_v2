"""Ortak identifier ayristiricisi (generic filtre, yol/icerik olcumu, Faz 3)."""

from __future__ import annotations

import pytest

from app.services.identifier_parts import contains_term, normalized_parts, split_identifier


@pytest.mark.parametrize("text, parts", [
    ("tcKimlikNo", ["tc", "Kimlik", "No"]),
    ("getTcKimlikNo", ["get", "Tc", "Kimlik", "No"]),
    ("TC_KIMLIK_NO", ["TC", "KIMLIK", "NO"]),
    ("tc-kimlik", ["tc", "kimlik"]),
    ("tckimlik", ["tckimlik"]),
    ("UserService", ["User", "Service"]),
    ("HTTPClient", ["HTTP", "Client"]),
    ("parseJSON", ["parse", "JSON"]),
    ("cnry-db01", ["cnry", "db", "01"]),
    ("PoseidonGatewayClient.java", ["Poseidon", "Gateway", "Client", "java"]),
    ("MüşteriİşlemServisi", ["Müşteri", "İşlem", "Servisi"]),
    ("__init__", ["init"]),
    ("", []),
    ("_-.", []),
])
def test_split_identifier(text, parts):
    assert [p.text for p in split_identifier(text)] == parts


def test_offsets_point_into_source():
    text = "getTcKimlikNo"
    for part in split_identifier(text):
        assert text[part.start:part.end] == part.text


def test_normalized_parts_casefold():
    assert normalized_parts("TC_KIMLIK_NO") == ["tc", "kimlik", "no"]


@pytest.mark.parametrize("text, term, expected", [
    ("getTcKimlikNo", "tckimlik", True),
    ("TC_KIMLIK_NO", "tcKimlik", True),
    ("tc-kimlik", "tckimlik", True),
    ("tcpPort", "tc", False),
    ("etc", "tc", False),
    ("com/acme/karayel/poseidon/PoseidonGatewayClient.java", "Poseidon", True),
    ("src/main/java/MusteriService.java", "Poseidon", False),
    ("hakan_yilmaz.txt", "Hakan Yilmaz", True),
    ("x", "", False),
])
def test_contains_term_only_on_part_boundaries(text, term, expected):
    assert contains_term(text, term) is expected
