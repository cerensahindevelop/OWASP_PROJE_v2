"""Ciktiya alinmayan bir dosyanin kullaniciya gosterilen sebebi ve yapilacak isi.

Tek kaynak: inceleme ekrani (audit_warning_details) ve maskeleme sonuc ekrani
(webapp/export_page) ayni dosya icin ayni sebebi soyler. Sebep, kayitli
kontrol kodundan (failed_checks.FailedCheck) secilir; ayni kod birden fazla
durumda kullaniliyorsa (ör. TUTARLILIK) gerekce metnindeki alt sebebe bakilir.
Mesajlar dosya icerigi ya da hassas deger tasimaz.
"""

from __future__ import annotations

import re

from app.services.audit_action_labels import ACCEPT_SYNTAX_ACTION_LABEL
from app.services.failed_checks import FailedCheck

_RERUN = "Projeyi yeniden dışa aktarın; tekrar ederse sistem kayıtlarını inceleyin."
_REPORT = (
    "Projeyi yeniden dışa aktarın; tekrar ederse dosya adını ve satırı sistem sorumlusuna bildirin."
)
_PLACEHOLDER_HINT = (
    "Projeyi yeniden çalıştırmak genellikle sonucu değiştirmez. Dosyada `mask_` ile başlayan, yer tutucuya "
    "benzeyen hazır bir metin varsa onu değiştirip yeniden dışa aktarın; yoksa dosyayı sistem sorumlusuna bildirin."
)
_LOCK_HINT = (
    "Projeyi yeniden çalıştırmak sonucu değiştirmez. Hassas içeriği kaynakta düzeltip lock dosyasını yeniden "
    "üretin ya da lock dosyasını projeden çıkarın."
)
_LLM_HINT = "LLM sunucusunun çalıştığını kontrol edip projeyi yeniden dışa aktarın."
_TARGET_HINT = "Hedef klasörde yer ve yazma izni olduğunu kontrol edip projeyi yeniden dışa aktarın."
_REVIEW_HINT = "Uyarılar ekranında bulunan değeri inceleyip karar verin."
# Sozdizimi hatasi deterministiktir: ayni dosya ayni bulguyla yine bozulur.
_SYNTAX_HINT = (
    "Projeyi yeniden dışa aktarmak sonucu değiştirmez; aynı maskeleme tekrar oluşur. Konumda gösterilen "
    "değerin maskelenmesi yanlışsa ya da dosyayı bu haliyle kullanabilecekseniz uyarılar ekranında "
    f"“{ACCEPT_SYNTAX_ACTION_LABEL}” seçin (diğer güvenlik kontrolleri yine yapılır). Değilse dosya adını "
    "ve satırı sistem sorumlusuna bildirin."
)

Explanation = tuple[str, str]

_MESSAGES: dict[str, Explanation] = {
    FailedCheck.ARSIV: (
        "Desteklenmeyen format: arşiv dosyası (.zip, .tar, .gz, .rar, .7z) açılıp taranamıyor; "
        "içeriği doğrulanamadığı için çıktıya alınmadı.",
        "Projeyi yeniden çalıştırmak sonucu değiştirmez. Arşivin içindeki dosyalar gerekiyorsa arşivi açıp "
        "proje klasörüne ekleyin, gerekmiyorsa arşivi projeden çıkarın.",
    ),
    FailedCheck.LOCK_BULGU: (
        "Lock dosyası: hassas olabilecek içerik var (ör. iç sunucu ya da registry adresi). Lock dosyaları "
        "bozulmasın diye maskelenmez; bu yüzden çıktıya alınmadı.",
        _LOCK_HINT,
    ),
    FailedCheck.LOCK_SON_DOGRULAMA: (
        "Lock dosyası: projenin başka dosyalarında maskelenen bir değer bu dosyada geçiyor. Lock dosyaları "
        "maskelenmediği için çıktıya alınmadı.",
        _LOCK_HINT,
    ),
    FailedCheck.LOCK_KOPYALAMA: ("Yazma hatası: lock dosyası çıktı klasörüne kopyalanamadı.", _TARGET_HINT),
    FailedCheck.OKUMA: (
        "Okuma hatası: dosya okunamadı.",
        "Dosyanın açılabildiğini ve okuma izni olduğunu kontrol edip projeyi yeniden dışa aktarın.",
    ),
    FailedCheck.BOYUT: (
        "Boyut sınırı: dosya izin verilen boyutu aştığı için taranamadı.",
        "Dosyayı bölün ya da projeden çıkarın; sonra projeyi yeniden dışa aktarın.",
    ),
    FailedCheck.KODLAMA: (
        "Metin kodlaması: dosyanın karakter kodlaması çözülemedi; içerik taranamadı.",
        "Dosyayı UTF-8 olarak kaydedip projeyi yeniden dışa aktarın.",
    ),
    FailedCheck.TESPIT_KATMANI: ("Tarama hatası: tarama katmanlarından biri hata verdi; dosya taranamadı.", _RERUN),
    FailedCheck.KODLANMIS_VERI: (
        "Kodlanmış veri: base64 ya da hex ile kodlanmış bir metnin içinde hassas bilgi (ör. parola, IP adresi) "
        "bulundu. Sistem kodlanmış bloğun içini maskeleyemediği için dosya çıktıya alınmadı.",
        "Kodlanmış değeri kaynakta kaldırıp projeyi yeniden dışa aktarın. Değer gerçekten hassas değilse "
        "uyarılar ekranında “Sorun yok, çıktıya ekle” seçin.",
    ),
    FailedCheck.LLM_TESPIT: (
        "Yapay zekâ: tarama tamamlanamadı; dosyanın güvenliği doğrulanamadı.", _LLM_HINT,
    ),
    FailedCheck.LLM_DENETIMI_TAMAMLANAMADI: (
        "Yapay zekâ: maskeleme sonrası denetim tamamlanamadı; dosyanın güvenliği doğrulanamadı.", _LLM_HINT,
    ),
    FailedCheck.LLM_DENETIMI: (
        "Açık kalan bilgi: maskeleme sonrası yapay zekâ denetimi dosyada gizlenmemiş görünen bir bilgi buldu.",
        _REVIEW_HINT,
    ),
    FailedCheck.INCELEME: (
        "Karar bekliyor: yapay zekâ bu dosyada emin olamadığı bulgular buldu.",
        "Onay bekleyenler ekranında bulguları onaylayın ya da reddedin; kararlar bitince dosya otomatik "
        "yeniden doğrulanır.",
    ),
    FailedCheck.ACIK_TERIM: (
        "Açık kalan kurumsal terim: sözlükteki bir terim maskeli çıktıda açık kaldı.",
        "Maskeleme kurallarını kontrol edip projeyi yeniden dışa aktarın.",
    ),
    FailedCheck.SOZDIZIMI: ("Sözdizimi: maskeleme dosyanın yapısını bozdu.", _SYNTAX_HINT),
    FailedCheck.YAZMA: ("Yazma hatası: maskelenmiş dosya çıktı klasörüne yazılamadı.", _TARGET_HINT),
    FailedCheck.GERI_DONUS: (
        "Geri dönüş: maskeli dosya asıl haline birebir çevrilemedi.", _PLACEHOLDER_HINT,
    ),
    FailedCheck.TUTARLILIK: ("Tutarlılık: son tutarlılık kontrolü tamamlanamadı.", _RERUN),
    FailedCheck.MASKELEME_HATASI: ("Veritabanı hatası: maskeleme eşlemesi kaydedilemedi.", _RERUN),
    FailedCheck.FAZ_D_COKMESI: (
        "Beklenmeyen hata: denetim, sözdizimi ya da yazma aşamasında dosya işlenemedi.", _RERUN,
    ),
    FailedCheck.SONLANDIRMA: ("Kayıt hatası: dosyanın izin ya da bütünlük kaydı tamamlanamadı.", _RERUN),
}

# Ayni kodun farkli durumlari: gerekce metninde aranan ifade -> aciklama.
# Sira onemlidir; ilk eslesen kullanilir.
_ROUND_TRIP_VARIANTS: list[tuple[tuple[str, ...], Explanation]] = [
    (("karsilik bulunamadi", "çözülemiyor"), (
        "Geri dönüş: yer tutucunun eşleme kaydı bulunamadı; dosya asıl haline çevrilemedi.", _REPORT,
    )),
    (("harf fark",), (
        "Geri dönüş: özgün büyük/küçük harf biçimi korunmadı; dosya asıl haline birebir çevrilemedi.", _REPORT,
    )),
]
_CONSISTENCY_VARIANTS: list[tuple[tuple[str, ...], Explanation]] = [
    (("acik canonical occurrence",), (
        "Tutarlılık: projenin başka dosyalarında maskelenen bir değer bu dosyada açık kaldı.", _REPORT,
    )),
    (("round-trip",), (
        "Tutarlılık: maskeli dosya asıl haline birebir çevrilemedi.", _PLACEHOLDER_HINT,
    )),
    (("sozdizimini bozdu",), (
        "Tutarlılık: başka dosyalardaki değerleri bu dosyada da maskelemek dosyanın yapısını bozdu.", _REPORT,
    )),
    (("dosya boyutu",), (
        "Tutarlılık: dosya, tutarlılık taraması için izin verilen boyutu aşıyor.",
        "Dosyayı bölün ya da projeden çıkarın; sonra projeyi yeniden dışa aktarın.",
    )),
    (("encoding",), (
        "Tutarlılık: maskeli dosyanın karakter kodlaması çözülemedi.", _REPORT,
    )),
]
_VARIANTS = {
    FailedCheck.GERI_DONUS: _ROUND_TRIP_VARIANTS,
    FailedCheck.TUTARLILIK: _CONSISTENCY_VARIANTS,
}

# describe_file_error() "hata=<Sinif>" ve "yol_uzunlugu=<n>" yazar.
_OS_ERROR_RE = re.compile(r"hata=(\w+)")
_PATH_LENGTH_RE = re.compile(r"yol_uzunlugu=(\d+)")
_OS_ERROR_CAUSES = {
    "PermissionError": "erişim izni yok",
    "FileNotFoundError": "dosya ya da klasör bulunamadı (tarama sırasında silinmiş ya da taşınmış olabilir)",
    "IsADirectoryError": "beklenen dosyanın yerinde bir klasör var",
    "NotADirectoryError": "yoldaki bir klasörün yerinde dosya var",
    "FileExistsError": "hedefte aynı adlı bir dosya zaten var",
}
_WINDOWS_MAX_PATH = 260
_PARSER_RE = re.compile(r"\b(Python|JSON|YAML|TOML|XML|SQL|TypeScript|JavaScript) sozdizimi")
_FILE_ERROR_CHECKS = frozenset({
    FailedCheck.OKUMA, FailedCheck.YAZMA, FailedCheck.LOCK_KOPYALAMA, FailedCheck.TUTARLILIK,
})


def _file_error_cause(reason: str) -> str | None:
    error = _OS_ERROR_RE.search(reason)
    if error is None:
        return None
    length = _PATH_LENGTH_RE.search(reason)
    if length and int(length.group(1)) >= _WINDOWS_MAX_PATH and error.group(1) in {"OSError", "FileNotFoundError"}:
        return f"dosya yolu Windows sınırını ({_WINDOWS_MAX_PATH} karakter) aşıyor olabilir"
    return _OS_ERROR_CAUSES.get(error.group(1), f"işletim sistemi hatası ({error.group(1)})")


def explain_failure(check: str | None, reason: str | None) -> Explanation | None:
    """(sebep, ne yapmali) dondurur; kod bilinmiyorsa None."""
    if not check:
        return None
    reason = reason or ""
    lowered = reason.casefold()
    for needles, explanation in _VARIANTS.get(check, []):
        if any(needle in lowered for needle in needles):
            return explanation
    base = _MESSAGES.get(check)
    if base is None:
        return None
    summary, next_step = base
    if check in _FILE_ERROR_CHECKS and (cause := _file_error_cause(reason)):
        category = "Okuma hatası" if check == FailedCheck.OKUMA or "okuma" in lowered else "Yazma hatası"
        return f"{category}: {cause}.", next_step
    if check == FailedCheck.SOZDIZIMI and (parser := _PARSER_RE.search(reason)):
        return f"Sözdizimi: maskeleme sonrası dosya {parser.group(1)} kurallarına uymuyor.", next_step
    return summary, next_step


def explained_checks() -> frozenset[str]:
    return frozenset(_MESSAGES)
