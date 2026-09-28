"""Basit Shannon entropy hesaplamasi - Presidio'nun PERSON/ORGANIZATION/
DATE_TIME/LOCATION/NRP gibi dogal-dil (serbest metin) kategorilerinin,
rastgele gorunen (yuksek entropili) bir string'e (orn. bir API key/base64
blob) uygulanmasini engellemek icin ikinci bir savunma katmani olarak
kullanilir - dosya-tipi kisitlamasindan (bkz. file_category_restriction_
repository.py) BAGIMSIZ ve ONA EK olarak calisir: dosya turu bu
kategorilere izin verse bile (orn. .md), degerin kendisi rastgele
gorunuyorsa yine de reddedilir.

Saf (DB/dosya-siz) bir fonksiyon - trivially unit test edilebilir.
"""

from __future__ import annotations

import math
from collections import Counter

# Presidio'nun SpacyRecognizer'i uzerinden gelen, gercekten "dogal dil"
# (istatistiksel/NER tabanli, deterministik regex/checksum OLMAYAN)
# kategoriler. Bkz. presidio_detector.py - bu liste SADECE bu kategoriler
# icin entropy kontrolu uygulanmasini saglar; EMAIL_ADDRESS/IP_ADDRESS gibi
# yapisal formatlar zaten kendi regex'leriyle yeterince guvenilirdir.
NATURAL_LANGUAGE_ENTITY_TYPES = frozenset({"PERSON", "ORGANIZATION", "DATE_TIME", "LOCATION", "NRP"})


def shannon_entropy(value: str) -> float:
    """Bir string'in Shannon entropisini (bit/karakter) hesaplar. Bos
    string icin 0.0 doner."""
    if not value:
        return 0.0
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


# Degerin entropisi esigi asiyor mu (yani "rastgele/supheli" gorunuyor mu) diye bakar.
def is_high_entropy(value: str, threshold: float) -> bool:
    return shannon_entropy(value) > threshold
