"""Ekran 5 - Kurumsal Terim Sözlüğü: bir terim listesi dosyası (.txt/.csv/
.xlsx) yükleyip mevcut maskeleme motoruna kalıcı kural olarak ekler. İnce
sunum katmanı - tüm iş mantığı ayrı süreçte çalışan FastAPI backend'inde
(app/webapp/api_client.py üzerinden HTTP ile).

Yüklenen dosya Streamlit tarafında SADECE bellekte tutulur (st.file_uploader'ın
kendi UploadedFile nesnesi + session_state'teki .getvalue() bytes'ı) - hiçbir
noktada diske yazılmaz; backend'de de bir taslak olarak ÖNBELLEĞE ALINMAZ,
"Önizle" ve "Onayla ve Yükle" adımlarının ikisinde de AYNI byte'lar backend'e
yeniden gönderilir. Hiçbir terim, kullanıcı önizlemeyi görüp "Onayla ve
Yükle"ye basmadan kaydedilmez.
"""

from __future__ import annotations

# streamlit: dosya yukleyici, metrik kutulari ve onay butonu gibi tum ekran
# bilesenleri icin.
import streamlit as st

# api_client: onizleme/onay isteklerini backend'e HTTP ile ileten fonksiyonlar.
from app.webapp import api_client
from app.webapp.api_client import ApiError
# page_intro/show_error: ortak baslik ve hata gosterimi yardimcilari.
from app.webapp.common import page_intro, show_error
# get_identity: bu ekranin da kimlik-kapisi ardinda oldugunu belirtmek icin
# (bkz. render() - ekran icerigi kimlikten bagimsiz calisir, sadece erisim kontrolu).
from app.webapp.identity import get_identity

_DRAFT_KEY = "term_upload_pending"
_RESULT_KEY = "term_upload_last_result"
_DELETE_RESULT_KEY = "term_delete_last_result"
_ACTIVATE_RESULT_KEY = "term_activate_last_result"
_SINGLE_RESULT_KEY = "term_single_add_last_result"


# "Önizle" butonuna basildiginda cagirilir: dosyayi henuz KAYDETMEDEN
# siniflandirir, sonucu (onaylanmayi bekleyen bir taslak olarak)
# session_state'e yazar.
def _run_preview(filename: str, content: bytes, category: str) -> None:
    try:
        preview = api_client.preview_term_upload(filename, content, category)
    except ApiError as exc:
        st.session_state.pop(_DRAFT_KEY, None)
        show_error(exc)
        return

    st.session_state[_DRAFT_KEY] = {
        "filename": filename,
        "content": content,
        "category": category,
        "preview": preview,
    }
    st.session_state[_RESULT_KEY] = None


# "Onayla ve Yukle" butonuna basildiginda cagirilir: bekleyen taslagi
# (varsa) DB'ye kalici olarak yazar ve taslagi temizler.
def _run_commit() -> None:
    pending = st.session_state.get(_DRAFT_KEY)
    if not pending:
        return
    try:
        result = api_client.commit_term_upload(pending["filename"], pending["content"], pending["category"])
    except ApiError as exc:
        show_error(exc)
        return

    st.session_state[_RESULT_KEY] = result
    st.session_state.pop(_DRAFT_KEY, None)


# Onizleme sonucunu (yeni/mevcut/supheli/reddedilen terim sayilari ve
# listeleri) gosterir; yeni terim varsa "Onayla ve Yukle" butonunu cizer.
def _render_preview(preview) -> None:
    st.markdown(f"### Önizleme — Başlık / Proje Adı: `{preview.category}`")
    st.caption(f"Dosyada toplam **{preview.total_found}** benzersiz terim bulundu.")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Yeni (eklenecek)", preview.new_count)
    col2.metric("Zaten kayıtlı", preview.existing_count)
    col3.metric("Şüpheli (pasif)", preview.suspicious_count)
    col4.metric("Reddedilecek", preview.rejected_count)

    if preview.new_valid:
        with st.expander(f"Yeni eklenecek terimler ({len(preview.new_valid)})"):
            st.write(", ".join(preview.new_valid))

    if preview.new_suspicious:
        with st.expander(f"⚠️ Şüpheli terimler ({len(preview.new_suspicious)}) — pasif eklenecek", expanded=True):
            st.caption("Bu terimler eklenecek ama hiçbir taramada otomatik çalışmayacak - elle gözden geçirip aktif etmeniz gerekir.")
            for item in preview.new_suspicious:
                st.write(f"- **{item.term}** — {item.reason}")

    if preview.already_registered:
        with st.expander(f"Zaten kayıtlı terimler ({len(preview.already_registered)})"):
            st.write(", ".join(preview.already_registered))

    if preview.rejected:
        with st.expander(f"❌ Reddedilen terimler ({len(preview.rejected)})", expanded=True):
            st.caption("Bu terimler hiçbir şekilde eklenmeyecek.")
            for item in preview.rejected:
                st.write(f"- **{item.term}** — {item.reason}")

    if preview.new_count == 0:
        st.info("Onaylanacak yeni bir terim yok.")
        return

    st.warning(
        f"Onaylarsanız **{preview.new_count} terim** kalıcı olarak maskeleme kuralına eklenecek "
        f"({preview.suspicious_count} tanesi şüpheli olduğu için pasif eklenecek)."
    )
    if st.button("✅ Onayla ve Yükle", type="primary", key="term_upload_confirm"):
        _run_commit()
        st.rerun()


# commit_term_upload sonrasi elde edilen kalici sonucu (eklenen/atlanan/
# reddedilen terim sayilari) ozetler.
def _render_result(result) -> None:
    st.success(
        f"Yükleme tamamlandı: **{result.added_count} terim eklendi** "
        f"({result.suspicious_added_count} tanesi şüpheli/pasif olarak), "
        f"**{result.skipped_count} terim** zaten kayıtlı olduğu için atlandı, "
        f"**{result.rejected_count} terim** reddedildi."
    )
    if result.suspicious_added_count:
        st.info(
            "Pasif eklenen ifadeler, gerekçeleriyle birlikte yukarıdaki "
            "'🔓 Aktivasyon Bekleyen Pasif İfadeler' bölümünde listelenir - "
            "oradan doğrudan aktif edebilirsiniz."
        )
    st.caption(f"Başlık / Proje Adı: `{result.category}` | Dosya: {result.filename}")


# Her pasif ifadeyi, secim/tiklama gerektirmeden DOGRUDAN kendi satirinda -
# nedeniyle ve Aktif Et butonuyla birlikte - gosterir. st.dataframe hucre
# icine buton koyamadigi icin (Streamlit sinirlamasi) bu bilerek ayri,
# tek-tek cizilen bir blok listesi olarak yapilir.
def _render_pending_activation(terms: list) -> None:
    passive_terms = [item for item in terms if not item.is_active]
    if not passive_terms:
        return

    st.caption(
        f"{len(passive_terms)} ifade şüpheli bulunduğu için pasif; taramalarda kullanılmıyor. "
        "Gerçekten kurumsal ve hassas olduğunu doğruladığınız ifadeyi aktif edin."
    )
    # Her ifade tek satir: ad + neden | onay kutusu | dugme.
    for item in passive_terms:
        with st.container(border=True):
            term_col, confirm_col, button_col = st.columns([3, 3, 1.2], vertical_alignment="center")
            term_col.markdown(
                f"**{item.term}** · {item.category}  \n"
                f":gray[Neden pasif: {item.inactive_reason or 'belirtilmemiş'}]"
            )
            confirmed = confirm_col.checkbox(
                "Hassas olduğunu ve yanlış eşleşme riskini kontrol ettim",
                key=f"inline_activate_confirm_{item.id}",
            )
            if button_col.button(
                "Aktif Et",
                key=f"inline_activate_{item.id}",
                disabled=not confirmed,
                type="primary",
                width="stretch",
            ):
                try:
                    activated = api_client.activate_corporate_term(item.id, confirmed_sensitive=True)
                except ApiError as exc:
                    show_error(exc)
                else:
                    st.session_state[_ACTIVATE_RESULT_KEY] = (
                        f"'{activated.term}' aktif edildi; bundan sonraki maskeleme işlemlerinde kullanılacak."
                    )
                    st.rerun()


def _render_registry(terms: list) -> None:
    st.caption(
        "Yalnızca sözlüğe eklenen kurumsal ifadeler listelenir; sistemin yerleşik kuralları "
        "(IP, e-posta, parola vb.) burada yer almaz."
    )
    if not terms:
        st.info("Henüz kayıtlı kurumsal ifade yok. 'İfade ekle' bölümünden ekleyebilirsiniz.")
        return

    categories = sorted({item.category for item in terms})
    filter_col, status_col = st.columns([3, 2])
    with filter_col:
        search = st.text_input(
            "İfadelerde ara",
            key="corporate_term_search",
            placeholder="Terim, başlık veya proje adı yazın",
        ).strip().casefold()
    with status_col:
        status = st.selectbox(
            "Durum",
            options=["Tümü", "Aktif", "Pasif"],
            key="corporate_term_status",
        )

    filtered = [
        item
        for item in terms
        if (not search or search in item.term.casefold() or search in item.category.casefold())
        and (status == "Tümü" or (status == "Aktif") == item.is_active)
    ]
    st.caption(
        f"Toplam {len(terms)} kurumsal ifade, {len(categories)} başlık/proje; "
        f"filtre sonucu {len(filtered)} kayıt."
    )
    if not filtered:
        st.info("Filtreye uyan kurumsal ifade yok.")
        return

    table_rows = [
        {
            "term_id": item.id,
            "İfade": item.term,
            "Başlık / Proje Adı": item.category,
            "Yer tutucu": f"{item.placeholder_prefix}_<N>",
            "Geçmiş Eşleme": item.mapping_count,
            "Durum": "AKTİF" if item.is_active else "PASİF",
            "Pasiflik Nedeni": "—" if item.is_active else (item.inactive_reason or "Neden belirtilmemiş"),
        }
        for item in filtered
    ]
    event = st.dataframe(
        table_rows,
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        column_order=[
            "İfade",
            "Başlık / Proje Adı",
            "Yer tutucu",
            "Geçmiş Eşleme",
            "Durum",
            "Pasiflik Nedeni",
        ],
    )
    if not (event and event.selection and event.selection.get("rows")):
        st.caption("Pasiflik nedenini incelemek, aktif etmek veya silmek için tablodan bir ifade seçin.")
        return

    selected = filtered[event.selection["rows"][0]]
    st.markdown(f"#### Seçili ifade: `{selected.term}`")
    st.caption(
        f"Başlık / Proje Adı: {selected.category} · Geçmiş mapping sayısı: {selected.mapping_count}. "
        "Silme, geçmiş restore kayıtlarını bozmaz."
    )
    if not selected.is_active:
        st.caption(
            "Bu ifade pasif - aktif etmek için 'Onay bekleyen' bölümünü kullanın."
        )

    confirmed = st.checkbox(
        "Bu ifadeyi silmek istediğimi onaylıyorum",
        key=f"term_delete_confirm_{selected.id}",
    )
    if st.button(
        "🗑️ Kurumsal İfadeyi Sil",
        key=f"term_delete_{selected.id}",
        disabled=not confirmed,
        type="primary",
        width="stretch",
    ):
        try:
            deleted = api_client.delete_corporate_term(selected.id)
        except ApiError as exc:
            show_error(exc)
        else:
            st.session_state[_DELETE_RESULT_KEY] = (
                f"'{deleted.term}' kurumsal ifadesi silindi. Yeni taramalarda kullanılmayacak; "
                "geçmiş restore kayıtları korundu."
            )
            st.rerun()


def _render_single_add() -> None:
    st.markdown("#### Tek ifade ekle")
    st.caption("Dosya hazırlamadan tek bir kelimeyi veya ifadeyi doğrudan ekleyebilirsiniz.")
    message = st.session_state.pop(_SINGLE_RESULT_KEY, None)
    if message:
        st.success(message)

    with st.form("single_corporate_term_form"):
        title = st.text_input(
            "Başlık / Proje Adı",
            key="single_term_title",
            placeholder="örn. Orion Personel Platformu",
            help="Yalnızca gruplama için kullanılır. Çıktıda mask_kurumsal_ifade_<sayı> kullanılır.",
        )
        term = st.text_input(
            "Kurumsal İfade",
            key="single_term_value",
            placeholder="örn. ORION PERSONEL PLATFORMU",
        )
        confirmed_sensitive = st.checkbox(
            "Bu ifadenin kurumsal ve hassas olduğunu doğruluyorum",
            key="single_term_confirmed",
        )
        submitted = st.form_submit_button("➕ İfadeyi Ekle", type="primary", width="stretch")

    if not submitted:
        return
    if not title.strip():
        st.error("Başlık / Proje Adı boş bırakılamaz.")
        return
    if not term.strip():
        st.error("Kurumsal İfade boş bırakılamaz.")
        return
    if not confirmed_sensitive:
        st.error("İfadenin kurumsal ve hassas olduğunu onaylamalısınız.")
        return

    try:
        created = api_client.create_corporate_term(
            term=term.strip(),
            title=title.strip(),
            confirmed_sensitive=True,
        )
    except ApiError as exc:
        show_error(exc)
        return

    st.session_state[_SINGLE_RESULT_KEY] = (
        f"'{created.term}' kurumsal ifadesi '{created.category}' başlığı altında eklendi."
    )
    st.rerun()


# Ekranin giris noktasi: onceki kalici sonucu (varsa) gosterir, dosya
# yukleme formunu cizer, gonderildiginde _run_preview'i tetikler ve bekleyen
# bir taslak varsa onizlemesini gosterir.
def render() -> None:
    get_identity()
    page_intro(
        "📚 Kurumsal Terim Sözlüğü",
        "Kuruma özgü isimleri (proje adları, sistem kodları, sunucu adları vb.) ekleyin; "
        "maskeleme bunları her taramada gizler. Hiçbir terim önizlemeyi onaylamadan kaydedilmez.",
    )

    try:
        terms = api_client.list_corporate_terms()
    except ApiError as exc:
        show_error(exc)
        terms = []
    for key in (_DELETE_RESULT_KEY, _ACTIVATE_RESULT_KEY):
        message = st.session_state.pop(key, None)
        if message:
            st.success(message)

    # Yalnizca secili bolum cizilir: pasif ifadeler ve uzun tablo, ekleme
    # formlarini sayfanin altina itmez.
    passive_count = sum(not item.is_active for item in terms)
    labels = {
        "add": "➕ İfade ekle",
        "list": f"📋 Kayıtlı ifadeler ({len(terms)})",
        "pending": f"⏳ Onay bekleyen ({passive_count})",
    }
    section = st.segmented_control(
        "Bölüm", list(labels), format_func=labels.get, default="add",
        key="term_section", label_visibility="collapsed",
    ) or "add"

    if section == "list":
        _render_registry(terms)
    elif section == "pending":
        if passive_count:
            _render_pending_activation(terms)
        else:
            st.success("Onay bekleyen pasif ifade yok.")
    else:
        _render_add_section()


# Tek ifade ekleme ve dosyadan toplu yukleme (onizle -> onayla) akislari.
def _render_add_section() -> None:
    _render_single_add()
    st.divider()
    st.markdown("#### Dosyadan toplu yükle")

    result = st.session_state.get(_RESULT_KEY)
    if result:
        _render_result(result)
        st.divider()

    with st.form("term_upload_form"):
        uploaded_file = st.file_uploader(
            "Terim Dosyası",
            type=["txt", "csv", "xlsx"],
            help=(
                "Her satırda/hücrede bir terim. Desteklenen formatlar: .txt, .csv, .xlsx. "
                "Çok sütunlu bir tablo yüklüyorsanız 'Hassas Değer' başlıklı bir sütun ekleyin - "
                "sistem yalnızca o sütunu okur, ID gibi diğer sütunları yok sayar."
            ),
        )
        category = st.text_input(
            "Başlık / Proje Adı",
            placeholder="örn. Orion Personel Platformu, İç Denetim Projesi",
            help=(
                "Bu dosyadaki tüm kurumsal ifadeler bu başlık/proje adı altında gruplanır. "
                "Bu başlık çıktıya yazılmaz; yerine mask_kurumsal_ifade_<sayı> kullanılır."
            ),
        )
        submitted = st.form_submit_button("🔍 Önizle", type="primary", width="stretch")

    if submitted:
        if uploaded_file is None:
            st.error("Bir dosya seçmelisiniz.")
        elif not category.strip():
            st.error("Başlık / Proje Adı boş bırakılamaz.")
        else:
            _run_preview(uploaded_file.name, uploaded_file.getvalue(), category.strip())

    pending = st.session_state.get(_DRAFT_KEY)
    if pending:
        st.divider()
        _render_preview(pending["preview"])
