"""Ekran - Kural Yönetimi: filtre_kurallari tablosundaki tespit kurallarını
listeler, yeni kural ekler, var olan bir kuralı aktif/pasif eder. İnce sunum
katmanı - tüm iş mantığı ayrı süreçte çalışan FastAPI backend'inde
(app/webapp/api_client.py üzerinden HTTP ile, bkz. app/api/routers/rules.py
ve app/services/rule_admin.py).

Kural silme YOK - CLI'daki (app/cli.py kural-pasif) ile aynı ilke: bir kural
sadece aktif/pasif edilebilir, asla silinmez (gecmiş eşlemeler rule_id'ye
bağlı kaldığı için silme geçmiş export'ları bozar)."""

from __future__ import annotations

# streamlit: tablo/form/slider gibi tum ekran bilesenleri icin.
import streamlit as st

# api_client: kural listesi/ekleme/aktif-pasif isteklerini backend'e HTTP ile
# ileten fonksiyonlar. ApiError: backend'e ulasilamamasi/hata donmesi durumunda firlatilir.
from app.webapp import api_client
from app.webapp.api_client import ApiError
# page_intro/show_error: ortak baslik ve hata gosterimi yardimcilari.
from app.webapp.common import page_intro, show_error
# get_identity: bu ekranin da kimlik-kapisi ardinda oldugunu belirtmek icin
# (kurallar kimlikten bagimsizdir, sadece erisim kontrolu icin cagrilir).
from app.webapp.identity import get_identity
from app.services.placeholder_policy import public_placeholder_prefix

_PATTERN_TYPE_LABELS = {
    "regex": "Regex (sabit desen)",
    "parametric": "Parametrik (proje/sicil/branch gibi çalışma-zamanı değeri)",
    "llm": "Yapay Zeka (LLM talimatı)",
    "presidio": "Presidio (Katman 2 hazır tanıtıcı)",
}
# pattern_type -> source_layer eslemesi, add_rule()'un kabul ettigi TEK
# gecerli kombinasyonlarla birebir ayni (bkz. app/services/rule_admin.py) -
# kullaniciya ayrica sorulmuyor, kural tipinden otomatik turetiliyor.
_SOURCE_LAYER_BY_PATTERN_TYPE = {
    "regex": "katman1",
    "parametric": "katman1",
    "llm": "llm",
    "presidio": "katman2_presidio",
}


# Secili kuralin aktif/pasif durumunu degistirir - backend'e PATCH atar.
def _run_toggle(rule_name: str, new_active: bool) -> None:
    try:
        api_client.set_rule_active(rule_name, new_active)
    except ApiError as exc:
        show_error(exc)
        return
    st.success(f"'{rule_name}' {'aktif' if new_active else 'pasif'} edildi.")


# Tum kurallari tablo olarak listeler; bir satira tiklandiginda altinda
# detay + aktif/pasif butonu gosterir (bkz. history_page.py'deki ayni desen).
def _render_list() -> None:
    only_active = st.checkbox("Sadece aktif kuralları göster", key="rule_mgmt_only_active")
    try:
        rules = api_client.list_rules(include_inactive=not only_active)
    except ApiError as exc:
        show_error(exc)
        return

    if not rules:
        st.info("Kayıtlı kural yok.")
        return

    table_rows = [
        {
            "rule_name": r.rule_name,
            "Kural Adı": r.rule_name,
            "Tip": _PATTERN_TYPE_LABELS.get(r.pattern_type, r.pattern_type),
            "Kategori": r.category,
            "Öncelik": r.priority,
            "Placeholder": f"{public_placeholder_prefix(r.rule_name, r.placeholder_prefix)}_<N>",
            "Durum": "AKTİF" if r.is_active else "PASİF",
        }
        for r in rules
    ]

    st.caption(f"Toplam {len(rules)} kural. Aktif/pasif etmek için bir satıra tıklayın.")
    event = st.dataframe(
        table_rows,
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        column_order=["Kural Adı", "Tip", "Kategori", "Öncelik", "Placeholder", "Durum"],
    )

    if not (event and event.selection and event.selection.get("rows")):
        return

    idx = event.selection["rows"][0]
    selected_name = table_rows[idx]["rule_name"]
    selected_rule = next(r for r in rules if r.rule_name == selected_name)

    st.divider()
    st.markdown(f"**{selected_rule.rule_name}**")
    if selected_rule.description:
        st.caption(selected_rule.description)
    col1, col2 = st.columns([3, 1])
    with col1:
        st.write(
            f"Tip: {_PATTERN_TYPE_LABELS.get(selected_rule.pattern_type, selected_rule.pattern_type)}  \n"
            f"Kaynak katman: {selected_rule.source_layer}  \n"
            f"Kategori: {selected_rule.category}"
        )
    with col2:
        if selected_rule.is_active:
            if st.button("⏸️ Pasif Et", key=f"deactivate_{selected_rule.rule_name}", width="stretch"):
                _run_toggle(selected_rule.rule_name, False)
                st.rerun()
        else:
            if st.button("▶️ Aktif Et", key=f"activate_{selected_rule.rule_name}", width="stretch"):
                _run_toggle(selected_rule.rule_name, True)
                st.rerun()


# Yeni kural ekleme formu. Kural tipi (pattern_tipi) BILEREK form DISINDA -
# Streamlit form icindeki widget'lar submit'e kadar rerun tetiklemez, ama bu
# secim hangi alanlarin gosterilecegini belirledigi icin anlik rerun gerekir
# (bkz. export_page.py'deki "mode" radio'su ile ayni desen).
def _render_create_form() -> None:
    st.subheader("➕ Yeni Kural Ekle")
    st.caption(
        "Yeni bir kural eklemek kod değişikliği veya deploy gerektirmez - bir sonraki "
        "Dışarı Çıkar işleminde otomatik olarak devreye girer."
    )

    pattern_tipi = st.selectbox(
        "Kural Tipi",
        options=list(_PATTERN_TYPE_LABELS.keys()),
        format_func=lambda k: _PATTERN_TYPE_LABELS[k],
        key="rule_mgmt_pattern_type",
        help=(
            "Regex: IP/e-posta gibi sabit formatlı desenler. "
            "Parametrik: proje adı/sicil no/branch adı gibi çalışma zamanında verilen değerler. "
            "Yapay Zeka: sabit formatı olmayan (kişi adı, adres gibi) veriler için LLM'e talimat. "
            "Presidio: Katman 2'nin hazır tanıtıcılarına eklenen kurum-içi pattern."
        ),
    )

    with st.form("rule_create_form"):
        rule_name = st.text_input("Kural Adı (--tip)", placeholder="örn. tc_kimlik_no")
        placeholder_format = st.text_input(
            "Placeholder Formatı",
            placeholder="örn. mask_tc_kimlik_{sayac}",
            help="'_{sayac}' ile bitmelidir - sistem genelindeki <PREFIX>_<SAYAC> formatını korur.",
        )
        category = st.text_input("Kategori (opsiyonel)", placeholder="Boş bırakılırsa Kural Adı ile aynı olur")
        priority = st.number_input(
            "Öncelik (opsiyonel, düşük sayı önce çalışır)",
            min_value=0, value=0, step=10,
            help="0 bırakılırsa otomatik olarak en sona (mevcut en yüksek öncelik + 10) atanır.",
        )

        pattern = regex_flags = validator_name = description = entity_type = None
        confidence_score = 0.85
        is_allow_list = False

        if pattern_tipi in ("regex", "presidio"):
            pattern = st.text_input("Regex Deseni", placeholder=r"örn. \b\d{11}\b")
            regex_flags = st.text_input("Regex Bayrakları (opsiyonel)", placeholder="örn. i (büyük/küçük harf duyarsız)")
        if pattern_tipi == "regex":
            validator_name = st.text_input(
                "Doğrulayıcı (opsiyonel)",
                placeholder="örn. tc_kimlik_no",
                help="Regex eşleşmesinden sonra ek checksum kontrolü yapan, sistemde önceden tanımlı bir fonksiyon adı.",
            )
        if pattern_tipi == "llm":
            description = st.text_area(
                "Yapay Zekaya Tarama Talimatı (zorunlu)",
                placeholder="örn. Kişi adı - metinde geçen gerçek bir insanın ad soyadı.",
            )
        if pattern_tipi == "presidio":
            entity_type = st.text_input("Entity Tipi", placeholder="örn. IC_DOMAIN_ADI")
            confidence_score = st.slider("Güven Skoru", min_value=0.0, max_value=1.0, value=0.85, step=0.05)
            is_allow_list = st.checkbox("Bu bir izin listesi (allow-list) kuralı mı?")

        is_active = st.checkbox("Aktif olarak ekle", value=True)
        submitted = st.form_submit_button("Kuralı Ekle", type="primary", width="stretch")

    if not submitted:
        return

    if not rule_name.strip():
        st.error("Kural Adı boş bırakılamaz.")
        return
    if not placeholder_format.strip():
        st.error("Placeholder Formatı boş bırakılamaz.")
        return
    if pattern_tipi in ("regex", "presidio") and not (pattern or "").strip():
        st.error("Bu kural tipi için Regex Deseni zorunludur.")
        return
    if pattern_tipi == "llm" and not (description or "").strip():
        st.error("Bu kural tipi için Yapay Zekaya Tarama Talimatı zorunludur.")
        return
    if pattern_tipi == "presidio" and not (entity_type or "").strip():
        st.error("Bu kural tipi için Entity Tipi zorunludur.")
        return

    try:
        rule = api_client.create_rule(
            rule_name=rule_name.strip(),
            placeholder_format=placeholder_format.strip(),
            pattern_type=pattern_tipi,
            source_layer=_SOURCE_LAYER_BY_PATTERN_TYPE[pattern_tipi],
            regex_pattern=(pattern or "").strip() or None,
            regex_flags=(regex_flags or "").strip() or None,
            validator_name=(validator_name or "").strip() or None,
            category=(category or "").strip() or None,
            priority=int(priority) or None,
            description=(description or "").strip() or None,
            is_active=is_active,
            entity_type=(entity_type or "").strip() or None,
            confidence_score=confidence_score,
            is_allow_list=is_allow_list,
        )
    except ApiError as exc:
        show_error(exc)
        return

    st.success(f"Kural eklendi: '{rule.rule_name}' (placeholder={rule.placeholder_prefix}_<N>)")
    st.rerun()


# Ekranin giris noktasi: kural listesini + aktif/pasif kontrolunu ve yeni
# kural ekleme formunu cizer.
def render() -> None:
    get_identity()
    page_intro(
        "🛠️ Kural Yönetimi",
        "Sistemin hangi hassas veri türlerini tespit ettiğini burada yönetebilirsiniz. Yeni bir "
        "kural eklemek ya da var olan birini aktif/pasif etmek kod değişikliği veya deploy "
        "gerektirmez - bir sonraki Dışarı Çıkar işleminde otomatik devreye girer.",
    )

    _render_list()
    st.divider()
    _render_create_form()
