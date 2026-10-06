"""Streamlit uygulamasinin ana montaj noktasi: kimlik kapisini uygular,
kimlik girildikten sonra sidebar navigasyonunu ve dort ana ekrani kurar."""

from __future__ import annotations

import time

import streamlit as st

from app.webapp import api_client, export_page, history_page, import_page, review_page, term_upload_page
from app.webapp.common import inject_base_style
from app.webapp.upload_queue import install_upload_queue
from app.webapp.identity import get_identity, has_identity, render_identity_badge, render_identity_gate

_PENDING_CACHE_KEY = "_nav_pending_count"
_PENDING_CACHE_SECONDS = 30


# Kenar cubugundaki "Onay Bekleyenler" yanindaki sayi: sicilin tum
# projelerinde karar bekleyen bulgu + karantinadaki dosya. Her etkilesimde
# API'yi yormamak icin kisa sure onbellekte tutulur; API'ye ulasilamazsa
# sayi gosterilmez, gezinme bozulmaz.
def _pending_count() -> int | None:
    sicil_no = get_identity()["sicil_no"]
    cached = st.session_state.get(_PENDING_CACHE_KEY)
    if cached and cached[0] == sicil_no and time.monotonic() - cached[1] < _PENDING_CACHE_SECONDS:
        return cached[2]
    try:
        # Otomatik yeniden kontrol edilen dosya henuz kullanici karari beklemez.
        warnings = [
            item for item in api_client.list_pending_audit_warnings(sicil_no=sicil_no)
            if not getattr(item, "revalidating", False)
        ]
        count = len(api_client.list_pending_reviews(sicil_no=sicil_no)) + len(warnings)
    except Exception:
        count = None
    st.session_state[_PENDING_CACHE_KEY] = (sicil_no, time.monotonic(), count)
    return count


# Streamlit uygulamasinin giris noktasi: kimlik kapisini uygular, gecilirse sidebar + ekranlari kurar.
def run() -> None:
    st.set_page_config(page_title="Maskeleme Sistemi", page_icon="🔒", layout="wide")
    inject_base_style()
    install_upload_queue()

    if not has_identity():
        # Kimlik girilmeden hicbir islem ekranina erisilemez - sidebar/nav
        # kasitli olarak hic olusturulmuyor.
        render_identity_gate()
        return

    pages = [
        st.Page(export_page.render, title="Dışarı Çıkar", icon="📤", url_path="disari-cikar"),
        st.Page(review_page.render, title="Onay Bekleyenler", icon="🕵️", url_path="onay-bekleyenler"),
        st.Page(import_page.render, title="Geri Al", icon="📥", url_path="geri-al"),
        st.Page(history_page.render, title="Geçmiş İşlemler", icon="🗂️", url_path="gecmis-islemler"),
        st.Page(term_upload_page.render, title="Kurumsal Terim Sözlüğü", icon="📚", url_path="kurumsal-terim-sozlugu"),
    ]
    # Export ekraninin "Onay Bekleyenler ekranina git" butonu icin sayfa
    # referanslarini sakla (st.switch_page bir st.Page nesnesi bekliyor).
    st.session_state["_nav_pages"] = {
        "export": pages[0],
        "review": pages[1],
        "import": pages[2],
        "history": pages[3],
        "term_upload": pages[4],
    }

    # Varsayilan navigasyon sidebar'in EN USTUNE cizilir; baslik ve kimlik
    # rozeti sayfa listesinin ustunde kalsin diye gizlenip
    # asagida st.page_link ile elle cizilir.
    navigation = st.navigation(pages, position="hidden")

    with st.sidebar:
        st.markdown("### 🔒 Maskeleme Sistemi")
        render_identity_badge()
        st.divider()
        st.caption("Sayfalar")
        pending = _pending_count()
        for page in pages:
            label = f"{page.title} ({pending})" if page is pages[1] and pending else None
            st.page_link(page, label=label, width="stretch")

    navigation.run()
