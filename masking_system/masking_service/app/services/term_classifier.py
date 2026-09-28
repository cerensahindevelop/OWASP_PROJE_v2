"""Kurumsal terim tehlike siniflandirmasi. Saf, DB'siz - bir terimin
metnine bakip 'ok'/'suspicious'/'rejected' karari verir.

Reddedilen terimler HIC eklenmez (Python anahtar kelime/stdlib modul
adiyla cakisma - eklenirse, kural case-insensitive eslestigi icin
(bkz. app/services/term_upload.py'nin regex_flags='i' varsayilani),
`import`/`type`/`os` gibi HER Python dosyasinda gecen bir token'i
maskeler ve syntax_validator'in bile kurtaramayacagi kadar yaygin bir
kaynak kodu bozulmasina yol acar).

Supheli terimler REDDEDILMEZ, ama caller'in varsayilan olarak PASIF
(is_active=False) eklemesi beklenir - kullanici elle gozden gecirip aktif
etmedikce hicbir taramada calismaz. Bu modul kendisi DB'ye dokunmaz, sadece
siniflandirma karari uretir; is_active atamasi caller'in (term_upload.py)
sorumlulugundadir.
"""

from __future__ import annotations

import keyword
import sys
from dataclasses import dataclass

_MIN_TERM_LENGTH = 3

_COMMON_WORDS_EN = {
    "data", "test", "user", "main", "admin", "config", "app", "service",
    "value", "name", "id", "type", "item", "list", "info", "system",
    "default", "sample", "example", "temp", "file", "path", "url",
    "key", "code", "status", "error", "result", "object", "class",
    "module", "package", "server", "client", "host", "port", "root",
}

_COMMON_WORDS_TR = {
    "veri", "deneme", "kullanici", "ana", "sistem", "servis", "deger",
    "ad", "kod", "tip", "liste", "bilgi", "varsayilan", "ornek", "gecici",
    "dosya", "yol", "anahtar", "durum", "hata", "sonuc", "nesne", "sinif",
    "modul", "paket", "sunucu", "istemci", "test1", "veri1",
}

_COMMON_WORDS = {w.casefold() for w in (_COMMON_WORDS_EN | _COMMON_WORDS_TR)}

# Python anahtar kelimeleri (True/False/def/import/...) ve "soft" anahtar
# kelimeler (match/case/type/_) - hepsi case-insensitive karsilastirma
# icin casefold edilmis. Kurallarimiz case-insensitive eslestigi icin
# (regex_flags='i') "IMPORT" gibi buyuk harfli bir terim de gercek
# `import` anahtar kelimesiyle CAKISIR - bu yuzden orijinal buyuk/kucuk
# harfe degil, casefold edilmis haline bakiyoruz.
_RESERVED_NAMES_FOLDED = {kw.casefold() for kw in keyword.kwlist} | {
    kw.casefold() for kw in keyword.softkwlist
} | {name.casefold() for name in sys.stdlib_module_names}


# Bir terimin siniflandirma sonucunu (durum + varsa gerekce) tasiyan sonuc nesnesi.
@dataclass(frozen=True)
class TermClassification:
    term: str
    status: str  # 'ok' | 'suspicious' | 'rejected'
    reason: str | None = None

    # Terimin hic eklenmemesi gerekip gerekmedigini bildirir.
    @property
    def is_rejected(self) -> bool:
        return self.status == "rejected"

    # Terimin pasif (is_active=False) eklenmesi gerekip gerekmedigini bildirir.
    @property
    def is_suspicious(self) -> bool:
        return self.status == "suspicious"


# Bir terimi inceleyip 'ok' (sorunsuz), 'suspicious' (supheli - pasif
# eklenmeli) ya da 'rejected' (hic eklenmemeli) olarak siniflandirir.
def classify_term(term: str) -> TermClassification:
    normalized = term.strip()
    folded = normalized.casefold()

    if not normalized:
        return TermClassification(normalized, "rejected", "bos terim")

    if folded in _RESERVED_NAMES_FOLDED:
        return TermClassification(
            normalized, "rejected",
            f"'{normalized}' bir Python anahtar kelimesi/standart kutuphane modul adiyla cakisiyor",
        )

    if len(normalized) < _MIN_TERM_LENGTH:
        return TermClassification(normalized, "suspicious", "terim cok kisa (<3 karakter)")

    if normalized.isdigit():
        return TermClassification(normalized, "suspicious", "terim salt sayisal")

    if folded in _COMMON_WORDS:
        return TermClassification(normalized, "suspicious", f"'{normalized}' yaygin/genel bir kelime")

    return TermClassification(normalized, "ok", None)
