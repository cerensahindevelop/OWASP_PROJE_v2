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

from app.services.identifier_parts import normalized_parts

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


# Python disindaki yaygin dillerin (JS/TS, Java/Kotlin/C#, SQL, shell, YAML)
# anahtar kelimeleri ve kod/config'te her yerde gecen genel tokenlar. LLM bir
# bunlardan birini "kuruma ozgu" diye isaretlerse deger maskelenmez: tek bir
# `default`/`export` bulgusu tutarlilik gecisiyle projedeki TUM dosyalara
# yayilip sozdizimini bozuyordu.
_CODE_KEYWORDS = {
    # JS/TS
    "abstract", "any", "as", "async", "await", "boolean", "break", "case", "catch", "class", "const",
    "constructor", "continue", "debugger", "declare", "default", "delete", "do", "else", "enum", "export",
    "extends", "false", "finally", "for", "from", "function", "get", "if", "implements", "import", "in",
    "infer", "instanceof", "interface", "is", "keyof", "let", "module", "namespace", "never", "new", "null",
    "number", "object", "of", "package", "private", "protected", "public", "readonly", "require", "return",
    "satisfies", "set", "static", "string", "super", "switch", "symbol", "this", "throw", "true", "try",
    "type", "typeof", "undefined", "unique", "unknown", "var", "void", "while", "with", "yield",
    "use client", "use server", "use strict", "props", "children", "state", "props", "react", "next",
    # Java/Kotlin/C#
    "byte", "char", "double", "final", "float", "int", "long", "native", "short", "synchronized",
    "throws", "transient", "volatile", "val", "fun", "override", "internal", "sealed", "data", "object",
    "void", "using", "var", "record",
    # SQL
    "select", "insert", "update", "delete", "where", "join", "left", "right", "inner", "outer", "group",
    "order", "by", "having", "limit", "offset", "table", "create", "drop", "alter", "index", "primary",
    "foreign", "references", "values", "into", "null", "not", "and", "or", "database", "schema", "grant",
    # shell / devops / config
    "echo", "export", "then", "fi", "done", "esac", "local", "source", "sudo", "bash", "sh", "env",
    "true", "false", "yes", "no", "on", "off", "localhost", "latest", "stable", "production", "staging",
    "development", "dev", "prod", "debug", "info", "warn", "warning", "error", "http", "https", "api",
    "version", "name", "image", "volumes", "services", "ports", "environment", "labels", "networks",
}
_CODE_KEYWORDS_FOLDED = {word.casefold() for word in _CODE_KEYWORDS}


def is_generic_code_token(value: str) -> bool:
    """True if `value` is too generic to be a sensitive/corporate identifier.

    Python anahtar kelimeleri/stdlib modulleri, diger dillerin anahtar
    kelimeleri, yaygin genel kelimeler, 3 karakterden kisa ya da salt sayisal
    degerler. Salt sayisal degerler (orn. sicil no) deterministik kurallarla
    yakalanir; bu fonksiyon LLM/Presidio gibi olasiliksal kaynaklar icindir.
    """
    normalized = (value or "").strip().strip("\"'`=:;,.-")
    folded = normalized.casefold()
    if folded in _CODE_KEYWORDS_FOLDED:
        return True
    if normalized.isdigit():
        # Kisa sayilar (port, surum, sayac) geneldir; uzun sayilar (sicil,
        # kimlik, hesap no) ayirt edicidir.
        return len(normalized) < 6
    return classify_term(normalized).status != "ok"


# --- Generic bilesik ad (SCAN_GENERIC_COMPOUND_FILTER, Faz 2a) ----------------
# Bilesik bir identifier'in (UserService, KayitSorguServisi, getMusteriListesi)
# TUM parcalari bu kumelerdeyse ad generic sayilir. Kume yalnizca programlama
# ekleri, fiiller ve teknik terimlerden olusur; genel isim/sifat (kara, yel,
# mavi, yildiz...) EKLENMEZ - kod adlari bu tur kelimelerden olusabilir
# (KARAYEL). Turkce ekli bicimler (servisi, listesi) ayrica yazilir;
# karsilastirma Turkce karakterleri ASCII'ye katlayarak yapilir.
_GENERIC_PARTS_EN = {
    # ekler / roller
    "controller", "repository", "repo", "impl", "manager", "handler", "factory", "dto", "dao", "entity",
    "util", "utils", "helper", "helpers", "mapper", "adapter", "request", "response", "exception", "error",
    "builder", "provider", "base", "abstract", "validator", "converter", "parser", "reader", "writer",
    "listener", "event", "job", "task", "scheduler", "filter", "interceptor", "configuration", "properties",
    "settings", "context", "session", "cache", "queue", "message", "notification", "model", "view", "page",
    "form", "component", "detail", "details", "summary", "record", "entry", "query", "command", "gateway",
    "proxy", "rest", "endpoint", "resource", "spec", "mock", "stub", "wrapper", "processor", "engine",
    "store", "registry", "facade", "bean", "vo", "id", "no", "num", "number", "count",
    "total", "by", "all", "new", "old", "info", "list", "map", "set", "get", "is", "has", "to",
    # fiiller
    "find", "save", "add", "remove", "create", "update", "delete", "fetch", "load", "read", "write", "send",
    "check", "validate", "convert", "parse", "build", "init", "handle", "process", "search", "sync",
}
_GENERIC_PARTS_TR = {
    # teknik terimler (yalin + iyelik ekli bicimler)
    "servis", "servisi", "islem", "islemi", "islemleri", "kayit", "kaydi", "kayitlari", "sorgu", "sorgusu",
    "liste", "listesi", "bilgi", "bilgisi", "bilgileri", "istek", "istegi", "yanit", "yaniti", "cevap",
    "cevabi", "hata", "hatasi", "durum", "durumu", "tipi", "turu", "kodu", "numara",
    "numarasi", "isim", "ismi", "yonetici", "yoneticisi", "yonetim", "yonetimi", "kullanici",
    "kullanicisi", "veri", "verisi", "tablo", "tablosu", "alan", "alani", "deger", "degeri", "parametre",
    "parametresi", "ayar", "ayari", "ayarlari", "kural", "kurali", "rapor", "raporu", "dosya", "dosyasi",
    "klasor", "dizin", "yolu", "adres", "adresi", "baglanti", "baglantisi", "oturum", "oturumu",
    "yetki", "yetkisi", "rolu", "grup", "grubu", "sayfa", "sayfasi", "ekran", "ekrani", "mesaj",
    "mesaji", "bildirim", "bildirimi", "olay", "olayi", "gorev", "gorevi", "kuyruk", "kuyrugu", "onbellek",
    "gecmis", "gecmisi", "tarih", "tarihi", "zaman", "sure", "suresi", "sayi", "sayisi", "sayac", "toplam",
    "adet", "miktar", "tutar", "oran", "orani", "detay", "detayi", "ozet", "ozeti", "sonuc", "sonucu",
    "cikti", "ciktisi", "girdi", "girdisi", "model", "modeli", "varlik", "depo", "deposu", "denetleyici",
    "yardimci", "arac", "araci", "istemci", "istemcisi", "sunucu", "sunucusu", "arayuz", "arayuzu", "sinif",
    "sinifi", "nesne", "nesnesi", "modul", "modulu", "paket", "paketi", "test", "testi", "ornek", "ornegi",
    "sablon", "sablonu", "tanim", "tanimi", "aciklama", "aciklamasi", "baslik", "basligi", "icerik",
    "icerigi", "metin", "metni", "anahtar", "anahtari", "kimlik", "kimligi", "sifre", "sifresi", "giris",
    "cikis", "dogrulama", "dogrulamasi", "kontrol", "kontrolu", "yapilandirma", "kategori", "kategorisi",
    # fiiller (kok ve yaygin bicimler)
    "getir", "kaydet", "guncelle", "ekle", "olustur", "gonder",
    "dogrula", "hesapla", "listele", "sorgula", "cevir", "donustur", "temizle", "baslat",
    "durdur", "calistir", "yukle", "indir", "onayla", "reddet", "kapat", "iptal", "getirme",
    "kaydetme", "silme", "guncelleme", "ekleme", "bulma", "olusturma", "okuma", "yazma", "gonderme",
}
_TR_ASCII = str.maketrans("çğıöşüâîû", "cgiosuaiu")


def _fold_tr(text: str) -> str:
    return text.casefold().replace("i̇", "i").translate(_TR_ASCII)


# Turkce parcalar en az 4 harf: kisa kokler (al, ac, ara, ata, sec, ver, kod,
# tip, ad...) kisi ve kod adlarinin parcasi olabilir. Yanlis pozitif bir onaya,
# yanlis negatif bir sizintiya mal olur; bu yuzden kisa Turkce kokler generic
# sayilmaz (mevcut _COMMON_WORDS_TR'den gelenler dahil).
_MIN_TR_PART_CHARS = 4
_GENERIC_PARTS_FOLDED = {
    _fold_tr(w) for w in _GENERIC_PARTS_EN | _COMMON_WORDS_EN | _CODE_KEYWORDS_FOLDED
} | {_fold_tr(w) for w in _GENERIC_PARTS_TR | _COMMON_WORDS_TR if len(_fold_tr(w)) >= _MIN_TR_PART_CHARS}


def _is_generic_part(part: str) -> bool:
    if part.isdigit():
        return len(part) < 6
    return _fold_tr(part) in _GENERIC_PARTS_FOLDED


def is_generic_compound(value: str) -> bool:
    """True: bosluksuz, en az iki parcali bir identifier'in TUM parcalari generic.

    `UserService`, `KayitSorguServisi` -> True; `PoseidonGatewayClient`,
    `KaraKartalServisi` -> False (en az bir parca genel kelime degil).
    Yalnizca sezgisel kaynaklarin (LLM, llm_audit, Presidio NER) bulgularina
    uygulanir; sozluk/alias/runtime terimleri bu kontrolden gecmez.
    """
    normalized = (value or "").strip()
    if not normalized or any(ch.isspace() for ch in normalized):
        return False
    parts = normalized_parts(normalized)
    return len(parts) >= 2 and all(_is_generic_part(part) for part in parts)


def is_generic_heuristic_value(value: str, *, compound: bool) -> bool:
    """Sezgisel bulgu filtresi: mevcut tek parca kontrolu + (bayrakla) bilesik ad kurali."""
    return is_generic_code_token(value) or (compound and is_generic_compound(value))
