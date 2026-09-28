"""Streamlit ekranlari arasinda paylasilan kucuk yardimcilar: teknik
hatalarin Turkce/anlasilir mesaja cevrilmesi, ortak stil ve rozet renkleri.
Is mantigi burada YOK - ayri surecte calisan FastAPI backend'ine
(app/webapp/api_client.py) atilan HTTP cagrilarinin ince bir sunum katmani.
"""

from __future__ import annotations

# streamlit: tum ekranlarin ortak sunum bilesenleri (hata kutusu, CSS
# enjeksiyonu, baslik) icin kullanilir.
import streamlit as st

# friendly_error: bu sayfalarin kendi (backend'e hic gitmeyen, orn.
# beklenmeyen bir Python hatasi) exception'larini kullaniciya gosterilecek
# Turkce mesaja cevirir - ApiError disindaki hallerde fallback olarak kullanilir.
from app.core.error_translation import friendly_error
from app.webapp.api_client import ApiError

OPERATION_LABELS = {"mask": "Dışarı Çıkar", "unmask": "Geri Al"}

STATUS_LABELS = {
    "completed": "Başarılı",
    "completed_with_warnings": "Uyarılı tamamlandı",
    "in_progress": "Devam ediyor",
    "failed": "Başarısız",
}
STATUS_COLORS = {
    "completed": "green",
    "completed_with_warnings": "orange",
    "in_progress": "blue",
    "failed": "red",
}

CONFIDENCE_LABELS = {"yuksek": "Yüksek", "orta": "Orta", "dusuk": "Düşük"}
CONFIDENCE_COLORS = {"yuksek": "red", "orta": "orange", "dusuk": "gray"}


def error_next_step(status_code: int, message: str) -> str:
    """HTTP/uygulama hata turune gore kullaniciya uygulanabilir sonraki adim verir."""

    lowered = message.casefold()
    if status_code == 0 and "zaman aşımına" in lowered:
        return (
            "Bu hata işlemin sunucuda durduğunu göstermez. Yeniden başlatmadan önce Geçmiş İşlemler "
            "ekranından durumunu kontrol edin; teknik ayrıntıdaki hata türü ve süreyle backend loglarını karşılaştırın."
        )
    if status_code == 0:
        return "Backend servisinin çalıştığını ve WEB_API_BASE_URL ayarını kontrol edin; ardından tekrar deneyin."
    if status_code == 404:
        if "çıkt" in lowered or "indir" in lowered:
            return "Geçmiş İşlemler ekranından çalışma durumunu kontrol edin. Çıktı karantinadaysa inceleyin; geçici çıktı silindiyse projeyi yeniden dışa aktarın."
        return "Seçilen kaydın hâlâ mevcut olduğunu kontrol edip ekranı yenileyin."
    if status_code == 409:
        return "Aynı kayıt başka bir işlem tarafından değiştirilmiş olabilir. Ekranı yenileyip güncel durumla tekrar deneyin."
    if status_code in {400, 422}:
        return "Girilen alanları ve dosya/yol seçimini kontrol edip isteği yeniden gönderin."
    if status_code in {401, 403}:
        return "Aktif kimliğin ve bu işlem için gerekli erişim yetkisinin doğru olduğunu kontrol edin."
    if status_code >= 500:
        return "İşlem kaydı numarasını not edin, backend loglarını kontrol edin ve veri değiştirmeden önce hatayı yeniden üretin."
    return "Ekranı yenileyip işlemi tekrar deneyin; sorun sürerse işlem kaydıyla birlikte teknik izi inceleyin."


# Yakalanan bir hatayi kullaniciya ozet mesaj + acilir teknik detay olarak gosterir.
def show_error(exc: Exception) -> None:
    if isinstance(exc, ApiError):
        message, detail = exc.message, exc.detail
        status_code = exc.status_code
    else:
        message, detail = friendly_error(exc)
        status_code = 500
    st.error(message)
    if detail:
        with st.expander("Hata ayrıntıları ve çözüm önerisi"):
            st.markdown(f"**Ne oldu?** {detail}")
            if status_code:
                st.markdown(f"**Teknik durum:** HTTP {status_code}")
            else:
                st.markdown("**Teknik durum:** Backend isteği tamamlanamadı (HTTP yanıtı alınamadı)")
            st.markdown(f"**Ne yapabilirsiniz?** {error_next_step(status_code, message)}")


# Uygulama genelinde kullanilan kucuk CSS duzeltmelerini (sidebar buton
# boyutu, yardim metni rengi) sayfaya enjekte eder; app.run() acilista bir kez cagirir.
def inject_base_style() -> None:
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"] .stButton button { font-size: 0.85rem; }
        .osw-help { color: var(--text-color-light, #6b7280); font-size: 0.95rem; margin-bottom: 1rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )


# Her ekranin ustunde ayni gorunumde baslik + aciklama satiri gostermek
# icin ortak yardimci.
def page_intro(title: str, description: str) -> None:
    st.title(title)
    st.markdown(f"<div class='osw-help'>{description}</div>", unsafe_allow_html=True)
