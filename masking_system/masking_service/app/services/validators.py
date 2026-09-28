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


# validator_name -> dogrulama fonksiyonu. Yeni bir checksum'li format
# eklemek icin buraya yeni bir giris eklemek yeterli.
VALIDATORS: dict[str, Callable[[str], bool]] = {
    "tc_kimlik_no": validate_tc_kimlik_no,
}
