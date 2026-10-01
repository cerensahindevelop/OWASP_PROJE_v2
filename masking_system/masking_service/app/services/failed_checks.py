"""Bir dosyayi ciktidan alikoyan (karantina/onay/dogrulama hatasi) kontrollerin
tek kaynagi.

FileOutcome.failed_check, AuditWarning.failed_check (DB: basarisiz_kontrol) ve
otomatik duzeltme kayitlari (AuditLog `auto_remediation=failed check=...`) bu
kodlari kullanir; serbest string yazilmaz. Deger metni DB'de saklandigi icin
mevcut bir kodun metni DEGISTIRILMEZ, yalnizca yeni kod eklenir.
"""

from __future__ import annotations

from enum import StrEnum


class FailedCheck(StrEnum):
    # --- Dosyayi ciktidan alikoyan kontroller (FileOutcome / AuditWarning) ---
    OKUMA = "okuma"
    BOYUT = "boyut"
    KODLAMA = "kodlama"
    ARSIV = "arsiv"
    TESPIT_KATMANI = "tespit_katmani"
    KODLANMIS_VERI = "kodlanmis_veri"
    LLM_TESPIT = "llm_tespit"
    INCELEME = "inceleme"
    ACIK_TERIM = "acik_terim"
    LLM_DENETIMI_TAMAMLANAMADI = "llm_denetimi_tamamlanamadi"
    LLM_DENETIMI = "llm_denetimi"
    SOZDIZIMI = "sozdizimi"
    YAZMA = "yazma"
    GERI_DONUS = "geri_donus"
    MASKELEME_HATASI = "maskeleme_hatasi"
    FAZ_D_COKMESI = "faz_d_cokmesi"
    TUTARLILIK = "tutarlilik"
    SONLANDIRMA = "sonlandirma"
    LOCK_BULGU = "lock_bulgu"
    LOCK_SON_DOGRULAMA = "lock_son_dogrulama"
    LOCK_KOPYALAMA = "lock_kopyalama"
    # --- Yalnizca lock dosyasi ic registry URL maskeleme basarisizlik nedeni ---
    AYRISTIRMA = "ayristirma"
    # --- Yalnizca otomatik duzeltme basarisizlik nedenleri ---
    KURAL = "kural"
    MASKELEME = "maskeleme"
    DARALTMA = "daraltma"
    COK_SATIRLI_ALINTI = "cok_satirli_alinti"
    UZUN_ALINTI = "uzun_alinti"
    BEKLENMEYEN = "beklenmeyen"


# Kodun kullaniciya gosterilen Turkce aciklamasi (rapor kirilimi ve insan
# onayi gerekcesi). Yeni kod eklenirse buraya da eklenmelidir (bkz.
# tests/test_failed_check_persistence.py).
FAILED_CHECK_LABELS: dict[FailedCheck, str] = {
    FailedCheck.OKUMA: "dosya okunamadı",
    FailedCheck.BOYUT: "boyut sınırı aşıldı",
    FailedCheck.KODLAMA: "metin kodlaması çözülemedi",
    FailedCheck.ARSIV: "arşiv dosyası taranamadı",
    FailedCheck.TESPIT_KATMANI: "tespit katmanı hata verdi",
    FailedCheck.KODLANMIS_VERI: "kodlanmış metinde hassas veri",
    FailedCheck.LLM_TESPIT: "LLM tespiti tamamlanamadı",
    FailedCheck.INCELEME: "düşük/orta güvenli AI bulgusu onay bekliyor",
    FailedCheck.ACIK_TERIM: "açık terim kontrolü",
    FailedCheck.LLM_DENETIMI_TAMAMLANAMADI: "LLM denetimi tamamlanamadı",
    FailedCheck.LLM_DENETIMI: "LLM denetimi",
    FailedCheck.SOZDIZIMI: "sözdizimi doğrulaması",
    FailedCheck.YAZMA: "çıktı yazma",
    FailedCheck.GERI_DONUS: "geri dönüş doğrulaması",
    FailedCheck.MASKELEME_HATASI: "eşleme/veritabanı yazma hatası",
    FailedCheck.FAZ_D_COKMESI: "denetim/sözdizimi/yazma aşaması hata verdi",
    FailedCheck.TUTARLILIK: "tutarlılık kontrolü",
    FailedCheck.SONLANDIRMA: "izin/bütünlük kaydı",
    FailedCheck.LOCK_BULGU: "lock dosyasında hassas olabilecek içerik",
    FailedCheck.LOCK_SON_DOGRULAMA: "lock dosyası son doğrulaması",
    FailedCheck.LOCK_KOPYALAMA: "lock dosyası kopyalanamadı",
    FailedCheck.AYRISTIRMA: "dosya biçimi (parser) doğrulaması",
    FailedCheck.KURAL: "sözlük kuralı bulunamadı",
    FailedCheck.MASKELEME: "değerlerin güvenli sınırla maskelenmesi",
    FailedCheck.DARALTMA: "alıntının bir kısmı açık kalacaktı",
    FailedCheck.COK_SATIRLI_ALINTI: "alıntı birden fazla satıra yayılıyor",
    FailedCheck.UZUN_ALINTI: "alıntı izin verilen uzunluktan uzun",
    FailedCheck.BEKLENMEYEN: "beklenmeyen",
}

# Kod kaydi olmayan eski sonuclar/uyarilar rapor kiriliminda bu anahtarla sayilir.
UNKNOWN_FAILED_CHECK = "bilinmiyor"


def failed_check_label(code: str | None) -> str:
    try:
        return FAILED_CHECK_LABELS[FailedCheck(code)]
    except ValueError:
        return UNKNOWN_FAILED_CHECK
