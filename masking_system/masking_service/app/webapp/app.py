"""Streamlit uygulamasinin ana montaj noktasi: kimlik kapisini uygular,
kimlik girildikten sonra sidebar navigasyonunu ve dort ana ekrani kurar."""

from __future__ import annotations

import streamlit as st

from app.webapp import export_page, history_page, import_page, review_page, term_upload_page
from app.webapp.common import inject_base_style
from app.webapp.upload_queue import install_upload_queue
from app.webapp.identity import has_identity, render_identity_badge, render_identity_gate


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
        for page in pages:
            st.page_link(page, width="stretch")

    navigation.run()
