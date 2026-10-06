"""Oturum boyunca hatirlanan kullanici kimligi: sicil numarasi. Kullaniciyi
tanimlayan sicildir; ayni kisi farkli proje ve branch'lerde calisabilir.
Proje/branch maskeleme formunda her islem icin secilir
(render_project_branch_inputs), listelerde istege bagli filtredir
(render_project_branch_filter); geri almada paketin kaydindan okunur.
"""

from __future__ import annotations

import re

import streamlit as st

_sicil_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,49}$")
_MAX_NAME_LEN = 200

_DRAFT_PREFIXES = ("export_", "import_", "review_", "term_upload_")
# Proje/branch secici anahtarlari: taslak sayilmaz (kayip uyarisi
# tetiklemez) ama kullanici degisince temizlenir.
_SELECTOR_PREFIX = "pb_"


_IDENTITY_GATE_STYLE = """
<style>
/* Yalnizca kimlik kapisi render edilirken sayfada bulunan scoped siniflar. */
.stMainBlockContainer:has(.osw-identity-shell) {
    max-width: 780px;
    padding-top: clamp(2.4rem, 7vh, 5rem);
    padding-bottom: 3rem;
}

.osw-identity-shell {
    margin-bottom: 1.35rem;
}

.osw-identity-brand {
    display: flex;
    align-items: center;
    gap: 1rem;
    margin-bottom: 1.1rem;
}

.osw-identity-icon {
    width: 3.4rem;
    height: 3.4rem;
    flex: 0 0 3.4rem;
    display: grid;
    place-items: center;
    border-radius: 1rem;
    color: #ffffff;
    font-size: 1.55rem;
    background: linear-gradient(145deg, #1e3a8a 0%, #2563eb 100%);
    box-shadow: 0 10px 28px rgba(37, 99, 235, 0.20);
}

.osw-identity-eyebrow {
    margin: 0 0 .2rem;
    color: #2563eb;
    font-size: .72rem;
    font-weight: 750;
    letter-spacing: .11em;
    text-transform: uppercase;
}

.osw-identity-title {
    margin: 0;
    color: var(--text-color);
    font-size: clamp(1.8rem, 4vw, 2.35rem);
    line-height: 1.12;
    letter-spacing: -.035em;
}

.osw-identity-lead {
    max-width: 650px;
    margin: 0;
    color: color-mix(in srgb, var(--text-color) 72%, transparent);
    font-size: 1rem;
    line-height: 1.65;
}

/* Kimlik ekraninda tek form vardir; secici diger sayfalara tasinmaz. */
.stMainBlockContainer:has(.osw-identity-shell) [data-testid="stForm"] {
    margin-top: 1.4rem;
    padding: 1.35rem 1.45rem 1.25rem;
    border: 1px solid rgba(128, 128, 128, .25);
    border-radius: 1.05rem;
    background: color-mix(in srgb, var(--background-color) 96%, #2563eb 4%);
    box-shadow: 0 18px 50px rgba(15, 23, 42, .07);
}

.stMainBlockContainer:has(.osw-identity-shell) [data-testid="stTextInput"] {
    margin-bottom: .18rem;
}

.stMainBlockContainer:has(.osw-identity-shell) [data-testid="stTextInput"] label p {
    font-size: .86rem;
    font-weight: 650;
}

.stMainBlockContainer:has(.osw-identity-shell) [data-testid="stTextInput"] input {
    min-height: 2.85rem;
    border-color: rgba(128, 128, 128, .3);
    border-radius: .72rem;
    background: var(--background-color);
}

.stMainBlockContainer:has(.osw-identity-shell) [data-testid="stTextInput"] input:focus {
    border-color: #2563eb;
    box-shadow: 0 0 0 1px #2563eb;
}

.stMainBlockContainer:has(.osw-identity-shell) [data-testid="stFormSubmitButton"] button {
    min-height: 2.9rem;
    margin-top: .35rem;
    border: 0;
    border-radius: .72rem;
    color: #ffffff;
    font-weight: 700;
    background: linear-gradient(90deg, #1e40af 0%, #2563eb 100%);
    box-shadow: 0 8px 20px rgba(37, 99, 235, .18);
}

.stMainBlockContainer:has(.osw-identity-shell) [data-testid="stFormSubmitButton"] button:hover {
    background: linear-gradient(90deg, #1e3a8a 0%, #1d4ed8 100%);
    box-shadow: 0 10px 24px rgba(37, 99, 235, .25);
}

.osw-identity-footnote {
    margin: .8rem 0 0;
    color: color-mix(in srgb, var(--text-color) 58%, transparent);
    font-size: .78rem;
    line-height: 1.45;
    text-align: center;
}

@media (max-width: 700px) {
    .stMainBlockContainer:has(.osw-identity-shell) {
        padding-top: 1.6rem;
    }
    .stMainBlockContainer:has(.osw-identity-shell) [data-testid="stForm"] {
        padding: 1.05rem;
    }
}
</style>
"""


# session_state icinde 'identity' alaninin var oldugundan emin olur.
def _init_state() -> None:
    st.session_state.setdefault("identity", None)


# Aktif kullaniciyi ({"sicil_no": ...}) dondurur, henuz girilmemisse None.
# Kullaniciyi tanimlayan sicildir; proje/branch her islemde ayrica secilir.
def get_identity() -> dict[str, str] | None:
    _init_state()
    return st.session_state["identity"]


# Bir kimlik girilmis mi diye bakar.
def has_identity() -> bool:
    return get_identity() is not None


def validate_sicil(sicil_no: str) -> list[str]:
    """Giris ekraninda backend'e gitmeden gosterilen sicil hatalari."""
    sicil_no = (sicil_no or "").strip()
    if not sicil_no:
        return ["Sicil Numarası boş bırakılamaz."]
    if not _sicil_RE.match(sicil_no):
        return [
            "Sicil Numarası geçersiz format: sadece harf, rakam, tire (-) ve alt çizgi (_) "
            "içerebilir; bir harf/rakamla başlamalı ve en az 2 karakter olmalıdır (örn. EMP-1001)."
        ]
    return []


def validate_project_branch(project_name: str, branch_name: str) -> list[str]:
    """Maskeleme formundaki proje/branch alanlarinin hatalari. Bos liste = gecerli."""
    errors: list[str] = []
    project_name = (project_name or "").strip()
    branch_name = (branch_name or "").strip()

    if not project_name:
        errors.append("Proje Adı boş bırakılamaz.")
    elif len(project_name) > _MAX_NAME_LEN:
        errors.append(f"Proje Adı en fazla {_MAX_NAME_LEN} karakter olabilir.")

    if not branch_name:
        errors.append("Branch Adı boş bırakılamaz.")
    elif len(branch_name) > _MAX_NAME_LEN:
        errors.append(f"Branch Adı en fazla {_MAX_NAME_LEN} karakter olabilir.")
    elif any(ch.isspace() for ch in branch_name):
        errors.append("Branch Adı boşluk karakteri içeremez.")

    return errors


# Aktif kullaniciyi session_state'e kaydeder.
def set_identity(sicil_no: str) -> None:
    st.session_state["identity"] = {"sicil_no": sicil_no.strip()}


# Sicilin daha once calistigi proje/branch ciftleri (en son kullanilan once).
# Oneri listesidir; API'ye ulasilamazsa bos liste doner, form yine calisir.
def known_project_branches() -> list[tuple[str, str]]:
    identity = get_identity()
    if identity is None:
        return []
    from app.webapp import api_client
    try:
        return api_client.list_project_branches(identity["sicil_no"])
    except Exception:
        return []


_NEW_PAIR = "➕ Yeni proje / branch"


def render_project_branch_inputs(key_prefix: str) -> tuple[str, str]:
    """Maskeleme formunda proje ve branch secimi: onceki ciftlerden biri ya
    da yeni bir cift. Degerler kayitlarda kullanilacagi gibi (strip) doner."""
    pairs = known_project_branches()
    choice = _NEW_PAIR
    if pairs:
        labels = [f"{project} / {branch}" for project, branch in pairs]
        choice = st.selectbox(
            "Proje / Branch",
            [*labels, _NEW_PAIR],
            key=f"{_SELECTOR_PREFIX}{key_prefix}choice",
            help="Daha önce çalıştığınız proje/branch çiftleri en son kullanılan önce listelenir.",
        )
        if choice != _NEW_PAIR:
            return pairs[labels.index(choice)]
    col_project, col_branch = st.columns(2)
    project_name = col_project.text_input(
        "Proje Adı", placeholder="Örn. Poseidon", key=f"{_SELECTOR_PREFIX}{key_prefix}project",
        help="Maskeleme kayıtlarının ilişkilendirileceği proje veya ürün adı.",
    )
    branch_name = col_branch.text_input(
        "Branch Adı", placeholder="Örn. main", key=f"{_SELECTOR_PREFIX}{key_prefix}branch",
        help="Kaynak projenin aktif dalı; örneğin main, develop veya release-1.0.",
    )
    return (project_name or "").strip(), (branch_name or "").strip()


_ALL = "Tümü"


def render_project_branch_filter(key_prefix: str) -> tuple[str | None, str | None]:
    """Listelerde istege bagli proje/branch filtresi; None = filtre yok."""
    pairs = known_project_branches()
    projects = sorted({project for project, _branch in pairs})
    col_project, col_branch = st.columns(2)
    project = col_project.selectbox("Proje", [_ALL, *projects], key=f"{_SELECTOR_PREFIX}{key_prefix}filter_project")
    branches = sorted({b for p, b in pairs if project == _ALL or p == project})
    branch = col_branch.selectbox("Branch", [_ALL, *branches], key=f"{_SELECTOR_PREFIX}{key_prefix}filter_branch")
    return (None if project == _ALL else project), (None if branch == _ALL else branch)


# Ekran taslaklarini (form durumu) session_state'ten temizler.
def _clear_page_drafts() -> None:
    for key in list(st.session_state.keys()):
        if key.startswith((*_DRAFT_PREFIXES, _SELECTOR_PREFIX)):
            del st.session_state[key]


# Aktif kimligi ve tum ekran taslaklarini temizler.
def clear_identity() -> None:
    st.session_state["identity"] = None
    _clear_page_drafts()


def has_unsaved_work() -> bool:
    """Kimlik degistirme onayi istemeden once kontrol edilir: ekranda henuz
    kaybolmasi sakincali olabilecek bir sonuc/taslak var mi?"""
    for key, value in st.session_state.items():
        if key.startswith(_DRAFT_PREFIXES) and value:
            return True
    return False


def render_identity_gate() -> None:
    """Kimlik henuz girilmemisse gosterilen tek ekran - baska hicbir
    sekme/menu bu asamada erisilebilir degildir."""
    st.markdown(_IDENTITY_GATE_STYLE, unsafe_allow_html=True)
    st.markdown(
        """
        <section class="osw-identity-shell" aria-labelledby="osw-identity-title">
          <div class="osw-identity-brand">
            <div class="osw-identity-icon" aria-hidden="true">◆</div>
            <div>
              <p class="osw-identity-eyebrow">Güvenli çalışma alanı</p>
              <h1 class="osw-identity-title" id="osw-identity-title">Sicil numaranızla devam edin</h1>
            </div>
          </div>
          <p class="osw-identity-lead">
            İşlemleriniz sicil numaranızla kaydedilir. Proje ve branch'i her maskeleme
            işleminde ayrıca seçersiniz; tüm projelerdeki kayıtlarınızı tek yerden görürsünüz.
          </p>
        </section>
        """,
        unsafe_allow_html=True,
    )

    with st.form("identity_form", clear_on_submit=False):
        sicil_no = st.text_input(
            "Sicil Numarası",
            placeholder="Örn. EMP-1001",
            help="İşlem sahibini belirleyen kurum içi personel numarası.",
        )
        submitted = st.form_submit_button("Çalışma Alanına Devam Et  →", type="primary", width="stretch")

    st.markdown(
        '<p class="osw-identity-footnote">🔒 Sicil numarası hassas içerik değildir; işlem kayıtlarını ve geri alma eşlemelerini size bağlamak için kullanılır.</p>',
        unsafe_allow_html=True,
    )

    if submitted:
        errors = validate_sicil(sicil_no)
        if errors:
            for message in errors:
                st.error(message)
        else:
            set_identity(sicil_no)
            st.rerun()


# Kimlik degistirme oncesi, kaydedilmemis is kaybolabilecegi icin onay ister.
@st.dialog("Kullanıcıyı değiştir")
def _confirm_identity_change_dialog() -> None:
    st.warning(
        "Aktif kullanıcıyı değiştiriyorsunuz. Devam eden bir işleminiz veya henüz kapatmadığınız bir "
        "sonuç varsa kaybolabilir."
    )
    col_confirm, col_cancel = st.columns(2)
    if col_confirm.button("Evet, değiştir", type="primary", width="stretch"):
        clear_identity()
        st.rerun()
    if col_cancel.button("Vazgeç", width="stretch"):
        st.rerun()


def render_identity_badge() -> None:
    """Sidebar'in en ustunde her ekranda gorunen, sabit kimlik etiketi."""
    identity = get_identity()
    if identity is None:
        return

    with st.container(border=True):
        st.caption("Aktif Kullanıcı")
        st.markdown(f"**Sicil:** {identity['sicil_no']}")
        if st.button("🔄 Kullanıcıyı Değiştir", width="stretch"):
            if has_unsaved_work():
                _confirm_identity_change_dialog()
            else:
                clear_identity()
                st.rerun()
