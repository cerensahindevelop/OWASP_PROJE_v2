"""Windows MAX_PATH (260 karakter) siniri icin genisletilmis-uzunluk yollar.

Windows, LongPathsEnabled kayit defteri ayari acik degilse 259 karakterden
uzun yollarda dosya islemlerini reddeder. Cikti once gecici bir staging
klasorune (".<hedef>.masking-XXXXXXXX/output/", hedeften ~25 karakter uzun)
yazildigi icin, hedefte sinirin altinda kalacak dosyalar staging'de sinira
takilabiliyordu. "\\\\?\\" onekli (extended-length) yol bu siniri yonetici
yetkisi veya kayit defteri degisikligi gerektirmeden kaldirir.

Onek yalnizca dosya islemi yapilan KOK yollara eklenir; altindaki yollar
kokten turetildigi icin oneki tasir. Kullaniciya gosterilen metinlerde
display_path ile onek kaldirilir. Windows disinda her iki fonksiyon da no-op.
"""

from __future__ import annotations

import os
from pathlib import Path

_EXTENDED_PREFIX = "\\\\?\\"
_UNC_EXTENDED_PREFIX = "\\\\?\\UNC\\"


# Mutlak bir Windows yolunu extended-length bicimine cevirir (saf metin islemi).
def to_extended(text: str) -> str:
    if text.startswith(_EXTENDED_PREFIX):
        return text
    if text.startswith("\\\\"):
        return _UNC_EXTENDED_PREFIX + text[2:]
    return _EXTENDED_PREFIX + text


# Windows'ta yolu mutlak + normalize edip "\\?\" onekiyle dondurur. Onekli
# yolda Windows ".." ve "/" normalizasyonu yapmaz; abspath bunu onceden yapar.
def extended_length_path(path: Path | str) -> Path:
    if os.name != "nt":
        return Path(path)
    return Path(to_extended(os.path.abspath(path)))


# Hata mesajlari/arayuz icin oneki kaldirir.
def display_path(path: Path | str) -> str:
    text = str(path)
    if text.startswith(_UNC_EXTENDED_PREFIX):
        return "\\\\" + text[len(_UNC_EXTENDED_PREFIX):]
    if text.startswith(_EXTENDED_PREFIX):
        return text[len(_EXTENDED_PREFIX):]
    return text
