"""Maskeleme SONRASI, dosya hedef klasore yazilmadan ONCE calisan otomatik
round-trip dogrulamasi: maskelenmis metni, o an icin olusturulmus/bulunmus
mapping'lerle (ayni unmask_project'in kullanacagi ayni reverse_text()
fonksiyonuyla) GERI COZUP orijinal metinle karsilastirir.

Amac: "restore edilen cikti orijinal dosyayla birebir ayni olmali" hedefini
export ANINDA, gercek bir unmask calismasi beklemeden dogrulamak - eger
bir detector/overlap-cozumu/replacement hatasi restore'u imkansiz ya da
kayipli hale getiriyorsa, bunu SESSIZCE gecmek yerine dosyayi
karantinaya alip ayrintili raporlamak (bkz. exporter.py entegrasyonu).

Guvenlik notu: hem `original_text` hem de reverse_text() ile geri
cozulmus `reconstructed_text` GERCEK/HAM hassas veri icerir (sifrelenmemis).
Bu modul bu ikisini KARSILASTIRIR ama uyusmazlik raporunda ASLA bu
metinlerin bir alt-dizisini/parcasini yazdirmaz - sadece konum/uzunluk gibi
meta-veriyi (bkz. syntax_validator.py'deki ayni ilke).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib

# reverse_text: gercek unmask_project() ile AYNI geri-cozum fonksiyonu -
# round-trip dogrulamasinin "gercekci" olmasini saglar (bkz. modul dokstringi).
from app.services.rule_engine import reverse_text


# Bir round-trip dogrulamasinin sonucunu (basarili mi, tam mi, yalnizca
# harf-buyuklugu farkli mi, cozulemeyen placeholder var mi) tasiyan sonuc.
# `case_normalized_only=True` yalniz tani bilgisidir; exact olmayan hicbir
# geri donus `ok=True` kabul edilmez.
@dataclass(frozen=True)
class RoundTripResult:
    ok: bool
    exact_match: bool
    case_normalized_only: bool
    unresolved_placeholders: list[str] = field(default_factory=list)
    detail: str | None = None


# Iki metnin ilk farklilastigi karakter indeksini bulur - hata mesajinda
# HAM icerik yerine sadece bu konum bilgisini raporlamak icin kullanilir.
def _first_difference_index(a: str, b: str) -> int:
    shorter = min(len(a), len(b))
    for i in range(shorter):
        if a[i] != b[i]:
            return i
    return shorter


# Duz karakter ofsetini (orijinal/geri-cozulmus metin uzerinde, ikisi de bu
# noktaya kadar OZDES oldugundan hangisine gore hesaplandigi fark etmez)
# 1-tabanli (satir, sutun) ciftine cevirir - kullanicinin KENDI orijinal
# kaynak dosyasinda o noktayi bulabilmesi icin. Sadece meta-veri (satir/
# sutun sayisi) uretir, HICBIR icerik/alt-dizi dondurmez (bkz. modul
# dokstringindeki guvenlik notu).
def _line_column(text: str, index: int) -> tuple[int, int]:
    line = text.count("\n", 0, index) + 1
    last_newline = text.rfind("\n", 0, index)
    column = index - last_newline
    return line, column


def text_digest(text: str) -> str:
    """Constant-size source reference for a run with many large files."""
    return hashlib.sha256(text.encode("utf-8", errors="surrogatepass")).hexdigest()


def verify_round_trip_digest(
    original_digest: str | None, original_length: int | None,
    masked_text: str, placeholder_map: dict[str, str],
) -> RoundTripResult:
    """Check real unmask against the immutable pre-mask source reference.

Unlike a temporary identity map, this also handles tokens reused across
multiple masking passes without retaining every original file in memory.
"""
    if original_digest is None or original_length is None:
        return RoundTripResult(False, False, False, detail="kaynak metin butunluk referansi bulunamadi")
    reconstructed, _, unresolved = reverse_text(masked_text, placeholder_map)
    # Source equality is the contract. Unknown token-shaped source literals
    # are not missing mappings if actual reverse_text restores every source
    # character. A newly introduced unresolved token still changes the digest
    # and must fail below; no spelling-based exception is needed.
    if len(reconstructed) == original_length and text_digest(reconstructed) == original_digest:
        return RoundTripResult(True, True, False)
    if unresolved:
        tokens = sorted(set(unresolved))
        locations = []
        for token in tokens[:20]:
            pos = masked_text.find(token)
            if pos >= 0:
                line, column = _line_column(masked_text, pos)
                locations.append(f"{token} (satir {line}, sutun {column})")
            else:
                locations.append(token)
        detail = f"{len(unresolved)} placeholder icin mapping bulunamadi: " + ", ".join(locations)
        if len(tokens) > 20:
            detail += f", ... ve {len(tokens) - 20} tane daha"
        return RoundTripResult(False, False, False, tokens, detail=detail)
    return RoundTripResult(False, False, False, detail=(
        "final geri cozum kaynak metnin SHA-256/uzunluk referansiyla uyusmuyor: "
        f"orijinal uzunluk={original_length}, geri cozulmus uzunluk={len(reconstructed)}"
    ))


# Maskelenmis metni (gercek unmask ile ayni fonksiyonla) geri cozup orijinaliyle karsilastirir.
def verify_round_trip(original_text: str, masked_text: str, placeholder_map: dict[str, str]) -> RoundTripResult:
    reconstructed, _resolved, unresolved = reverse_text(masked_text, placeholder_map)

    # Check the full source before interpreting lexical candidates as errors.
    # A source constant such as service_test_1 needs no reverse mapping.
    if reconstructed == original_text:
        return RoundTripResult(ok=True, exact_match=True, case_normalized_only=False)

    if unresolved:
        tokens = sorted(set(unresolved))
        # Token metninin kendisi (orn. "mask_email_5") guvenle gosterilebilir -
        # gercek hassas deger degil, sadece placeholder adi. Kullanicinin
        # dosyada TAM OLARAK nereye bakacagini bulabilmesi icin ilk gectigi
        # (satir, sutun) konumu da eklenir.
        locations = []
        for token in tokens[:20]:
            pos = masked_text.find(token)
            if pos >= 0:
                line, column = _line_column(masked_text, pos)
                locations.append(f"{token} (satir {line}, sutun {column})")
            else:
                locations.append(token)
        detail = f"{len(unresolved)} placeholder icin az once olusturulan mapping'de karsilik bulunamadi: " + ", ".join(locations)
        if len(tokens) > 20:
            detail += f", ... ve {len(tokens) - 20} tane daha"
        return RoundTripResult(
            ok=False,
            exact_match=False,
            case_normalized_only=False,
            unresolved_placeholders=tokens,
            detail=detail,
        )

    if reconstructed.casefold() == original_text.casefold():
        return RoundTripResult(
            ok=False,
            exact_match=False,
            case_normalized_only=True,
            detail="geri cozum orijinalle yalnizca buyuk/kucuk harf farkiyla eslesiyor; "
            "birebir geri donus sozlesmesi ihlal edildi",
        )

    diff_at = _first_difference_index(original_text, reconstructed)
    diff_line, diff_column = _line_column(original_text, diff_at)
    return RoundTripResult(
        ok=False,
        exact_match=False,
        case_normalized_only=False,
        detail=(
            f"geri cozulmus metin orijinaliyle uyusmuyor: ilk fark orijinal dosyada "
            f"satir {diff_line}, sutun {diff_column} konumunda, "
            f"orijinal uzunluk={len(original_text)}, geri cozulmus uzunluk={len(reconstructed)}"
        ),
    )
