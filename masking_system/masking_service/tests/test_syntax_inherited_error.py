"""Kaynakta zaten var olan sozdizimi hatasinin maskelemeye yuklenmemesi.

"Kaynakta ayni hata var mi?" kontrolu satir/sutun iceren hata mesajlarini
birebir karsilastiriyordu. Ayni satirda hatadan once bir deger maskelenince
sutun kayiyor ve dogru maskelenmis dosya failed_syntax_validation ile
dusuyordu. Maskelemenin GERCEKTEN bozdugu dosyalar hala hata dondurmeli.
"""

from __future__ import annotations

import pytest

from app.services.syntax_validator import _same_error, _structural_skeleton, inspect_masked_syntax

_PLACEHOLDER = "IP_ADDRESS_0001"


@pytest.mark.parametrize("relative_path,source,value", [
    ("app.py", 'x = "10.20.30.40"; y = (1,\n', "10.20.30.40"),
    ("tsconfig.json", '{"host": "10.1.2.3", "port": 8080,}\n', "10.1.2.3"),
    ("tsconfig.json", '{\n  // yorum\n  "host": "10.1.2.3", "port": 1,\n}\n', "10.1.2.3"),
    ("lib.rs", "const IP: &str = \"10.20.30.40\"; struct S<'a> { v: &'a str }\n", "10.20.30.40"),
    ("app.ts", 'const ip = "10.20.30.40"; f(1,\n', "10.20.30.40"),
    ("pyproject.toml", 'host = "10.1.2.3"; port = 1\n', "10.1.2.3"),
])
def test_inherited_error_with_shifted_column_is_not_blamed_on_masking(relative_path, source, value):
    masked = source.replace(value, _PLACEHOLDER)

    result = inspect_masked_syntax(relative_path, masked, source)

    assert result.error is None
    assert any("Kaynak dosya zaten" in notice for notice in result.warnings)


@pytest.mark.parametrize("relative_path,source,masked", [
    # Maskeleme degerin kapanis tirnagini yuttu.
    ("Main.java", 'String ip = "10.20.30.40";\n', 'String ip = "IP_ADDRESS_0001;\n'),
    ("app.py", 'ip = "10.20.30.40"\n', 'ip = "IP_ADDRESS_0001\n'),
    ("config.json", '{"host": "10.1.2.3"}\n', '{"host": "IP_ADDRESS_0001}\n'),
])
def test_masking_that_really_breaks_syntax_still_fails(relative_path, source, masked):
    assert inspect_masked_syntax(relative_path, masked, source).error is not None


@pytest.mark.parametrize("relative_path,source,masked", [
    # Kaynak zaten bozuk (kapanmamis parantez); maskeleme AYRICA bir tirnak sildi.
    ("Main.java", 'String ip = "10.20.30.40"; f(1,\n', 'String ip = "IP_ADDRESS_0001; f(1,\n'),
    ("app.py", 'ip = "10.20.30.40"; y = (1,\n', 'ip = "IP_ADDRESS_0001; y = (1,\n'),
    ("app.ts", 'const ip = "10.20.30.40"; f(1,\n', 'const ip = "IP_ADDRESS_0001; f(1,\n'),
    ("tsconfig.json", '{"host": "10.1.2.3", "port": 8080,}\n', '{"host": "IP_ADDRESS_0001, "port": 8080,}\n'),
    ("pyproject.toml", 'host = "10.1.2.3"\nport = [1,\n', 'host = "IP_ADDRESS_0001\nport = [1,\n'),
])
def test_broken_source_does_not_excuse_new_breakage(relative_path, source, masked):
    assert inspect_masked_syntax(relative_path, masked, source).error is not None


def test_same_error_ignores_column_only():
    assert _same_error("Python sozdizimi hatasi (satir 3, sutun 8)", "Python sozdizimi hatasi (satir 3, sutun 20)")
    assert _same_error("TS sozdizimi hatasi (satir 1, byte sutunu 4)", "TS sozdizimi hatasi (satir 1, byte sutunu 9)")
    # Farkli satir veya farkli tur: ayni hata DEGIL.
    assert not _same_error("Python sozdizimi hatasi (satir 3, sutun 8)", "Python sozdizimi hatasi (satir 4, sutun 8)")
    assert not _same_error(
        "Sozdizimi hatasi: dengesiz parantez: beklenmeyen '}' (satir 1, sutun 5)",
        "Sozdizimi hatasi: kapatilmamis string literal (\") - baslangic: satir 1, sutun 5",
    )
    assert not _same_error(None, "x")


def test_skeleton_ignores_masked_value_but_sees_structural_change():
    source = "let a = \"10.0.0.1\"; // yorum 'x\nf(&'a str)\n"
    same = source.replace("10.0.0.1", _PLACEHOLDER)
    quote_lost = source.replace('10.0.0.1"', _PLACEHOLDER)
    comment_lost = source.replace("// yorum", "/ yorum")

    assert _structural_skeleton("rs", source) == _structural_skeleton("rs", same)
    assert _structural_skeleton("rs", source) != _structural_skeleton("rs", quote_lost)
    assert _structural_skeleton("rs", source) != _structural_skeleton("rs", comment_lost)
