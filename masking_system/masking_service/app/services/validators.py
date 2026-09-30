"""Adlandirilmis dogrulama fonksiyonlari - regex bir degeri YAKALADIKTAN
SONRA, gercekten o formatin kurallarina uyup uymadigini (checksum vb.)
kontrol eder. Bir FilterRule satirinin validator_name alani buradaki bir
anahtarla eslesirse, rule_engine.find_matches regex eslesmesini bu
fonksiyondan da gecirir - checksum tutmuyorsa eslesme reddedilir.

LLM'e ya da bag lamsal ipucuna gerek yok: bu formatlarin hepsinin (TC
kimlik no, IBAN, kredi karti vb.) resmi/matematiksel bir dogrulama
algoritmasi var - deterministik ve sifir maliyetli.
"""

from __future__ import annotations

import re
from typing import Callable


# TC kimlik numarasinin 11 haneli resmi checksum kuralina uyup uymadigini kontrol eder.
def validate_tc_kimlik_no(value: str) -> bool:
    digits_str = re.sub(r"\D", "", value)
    if len(digits_str) != 11 or digits_str[0] == "0":
        return False

    digits = [int(c) for c in digits_str]
    odd_sum = digits[0] + digits[2] + digits[4] + digits[6] + digits[8]
    even_sum = digits[1] + digits[3] + digits[5] + digits[7]

    check10 = (odd_sum * 7 - even_sum) % 10
    if check10 != digits[9]:
        return False

    check11 = sum(digits[:10]) % 10
    return check11 == digits[10]


# --- Parola/sir atamasi (generic_secret_assignment) -------------------------
# Kural 6+ karakterlik degerleri yakalar; kisa sinir gercek parolalari
# yakalarken asagidaki "parola olmayan" degerleri de yakalayabilir. Bunlar
# reddedilir. Bir sirri kacirmak maskelemeyi atlatir, yanlis eslesme ise
# yalnizca fazladan bir yer tutucu uretir: bu yuzden liste dar tutulur.

# Ortam degiskeni / sablon referansi: ${DB_PASS}, $DB_PASS, %PASSWORD%,
# {{ vault.pw }}, #{secret}, <%= pw %>, $(PASSWORD)
# (`$ecret123` gibi $ ile baslayan gercek parola, BUYUK_HARF degisken adi olmadigi icin reddedilmez.)
_SECRET_REFERENCE_RE = re.compile(r"^(?:\$\{|\$\(|\$[A-Z_][A-Z0-9_]*$|%[A-Za-z_][\w.]*%$|\{\{|#\{|<%)")
# Bicim/sablon yer tutuculari: {token}, %s, %(password)s, <password>
# ve renk kodu (#ddd, #d0d0d0 - stil tanimlarindaki `Token: '#ddd'`).
_FORMAT_PLACEHOLDER_RE = re.compile(r"^(?:\{[\w.]*\}|%(?:\([\w.]+\))?[sd]|<[\w-]+>|#[0-9A-Fa-f]{3,8})$")
_NON_SECRET_LITERALS = frozenset({
    "null", "none", "nil", "true", "false", "undefined", "empty",
    "changeme", "change_me", "change-me", "password", "passwd", "secret", "token",
    "your_password", "yourpassword", "your-password", "placeholder",
})
# Tirnaksiz deger yalnizca harf/alt cizgiden olusuyorsa kod ifadesidir:
# degisken (`tokenizer`, `request_password`) ya da tip bildirimi
# (`token: Optional`). Tirnaksiz gercek parola rakam ya da sembol icermelidir;
# yalnizca harften olusan tirnaksiz parola (`pwd=sunshine`) bilinen sinirdir.
# snake_case degisken adi (`s3_connection`) de rakam icerse bile kod ifadesidir.
_CODE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_]+$|^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")


def validate_secret_value(value: str) -> bool:
    quoted = len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'"
    inner = value[1:-1] if quoted else value
    stripped = inner.strip()
    if not stripped:
        return False
    if stripped.lower() in _NON_SECRET_LITERALS or len(set(stripped)) == 1:
        return False
    if _SECRET_REFERENCE_RE.match(stripped) or _FORMAT_PLACEHOLDER_RE.match(stripped):
        return False
    return quoted or not _CODE_IDENTIFIER_RE.match(stripped)


# validator_name -> dogrulama fonksiyonu. Yeni bir checksum'li format
# eklemek icin buraya yeni bir giris eklemek yeterli.
VALIDATORS: dict[str, Callable[[str], bool]] = {
    "tc_kimlik_no": validate_tc_kimlik_no,
    "secret_value": validate_secret_value,
}
