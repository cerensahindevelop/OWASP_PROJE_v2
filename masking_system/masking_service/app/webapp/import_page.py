"""Ekran 3 - Geri Al (Import/Reverse): maskelenmiş bir klasördeki
placeholder'ları gerçek değerlerine geri döndürür. İnce sunum katmanı -
tüm iş mantığı ayrı süreçte çalışan FastAPI backend'inde
(app/webapp/api_client.py üzerinden HTTP ile).

Not: geri dönüştürülen çıktı GERÇEK, şifresi çözülmüş hassas veri içerir -
backend bu yüzden yükleme modunda kalıcı bir "sonra indir" adresi TUTMAZ,
tek istekte hesaplayıp aynı yanıtta (base64) döner (bkz. proje kök
dizinindeki plan dosyası)."""

from __future__ import annotations

import base64
from pathlib import Path

# streamlit: form/spinner/sonuc gosterimi gibi tum ekran bilesenleri icin.
import streamlit as st

# api_client: geri donusum isteklerini backend'e HTTP ile ileten fonksiyonlar.
# ApiError: bu cagrilardan biri basarisiz olursa firlatilir.
from app.webapp import api_client
from app.webapp.api_client import ApiError
# page_intro/show_error: ortak baslik ve hata gosterimi yardimcilari.
from app.webapp.common import page_intro, show_error
# get_identity: aktif proje/sicil/branch kimligini okumak icin (unmask
# sadece export ederkenkiyle AYNI kimlikle calisir).
from app.webapp.identity import get_identity


# "Klasor Yolu" moduyla baslatilan geri-donusum akisi: backend'e HTTP
# istegi atar, sonucu session_state'e yazar.
def _run_import(source_path: str, target_path: str) -> None:
    identity = get_identity()
    with st.spinner("Geri dönüştürülüyor... Bu işlem dosya sayısına göre biraz sürebilir."):
        try:
            result = api_client.unmask_by_path(
                source_path=source_path,
                target_path=target_path,
                project_name=identity["project_name"],
                sicil_no=identity["sicil_no"],
                branch_name=identity["branch_name"],
                initiated_by=identity["sicil_no"],
                job_id=None,
            )
        except ApiError as exc:
            st.session_state["import_last_result"] = None
            show_error(exc)
            return

    st.session_state["import_last_result"] = {"report": result.report}


# "Dosya Yükle" moduyla baslatilan geri-donusum akisi: yuklenen dosyalari
# backend'e HTTP ile gonderir, sonucu (indirilebilir bayt dizisiyle)
# session_state'e yazar. Backend, GERCEK hassas veri iceren geri
# donusturulmus ciktiyi diskte kalici tutmaz - tek istekte hesaplayip
# ayni yanitta (base64) doner (bkz. modul docstring'i).
def _run_import_upload(uploaded_files: list, *, is_directory_upload: bool = False) -> None:
    identity = get_identity()
    with st.spinner("Yükleniyor ve geri dönüştürülüyor... Bu işlem dosya sayısına göre biraz sürebilir."):
        try:
            result = api_client.unmask_upload(
                uploaded_files,
                is_directory_upload=is_directory_upload,
                project_name=identity["project_name"],
                sicil_no=identity["sicil_no"],
                branch_name=identity["branch_name"],
                initiated_by=identity["sicil_no"],
                job_id=None,
            )
        except ApiError as exc:
            st.session_state["import_last_result"] = None
            show_error(exc)
            return

    download_bytes = base64.b64decode(result.download_base64) if result.download_base64 else None
    download_name = result.download_filename

    st.session_state["import_last_result"] = {
        "report": result.report,
        "download_bytes": download_bytes,
        "download_name": download_name,
    }


# Bir geri-donusum calismasinin sonucunu (bulunan/cozulen placeholder
# sayilari, cozulemeyen placeholder'lar, kimlik uyusmazligi onerisi, indirme
# butonu) ekrana basar.
def _render_result(result: dict) -> None:
    report = result["report"]

    if report.has_unresolved_placeholders:
        st.error(
            "🔴 Bazı gizlenmiş veriler için eşleşme bulunamadı, bilgileriniz doğru mu kontrol edin. "
            "Bu placeholder'lar çıktıda OLDUĞU GİBİ (geri dönüştürülmeden) bırakıldı — hiçbir "
            "değer sessizce boş geçilmedi ya da tahmin edilmedi."
        )
        suggestion = report.identity_mismatch_suggestion
        if suggestion is not None:
            st.warning(
                f"🟡 Olası neden: **yanlış kimlik**. Çözülemeyen placeholder'ların "
                f"**{suggestion.matched_count}/{suggestion.unresolved_count} tanesi "
                f"(%{suggestion.match_ratio * 100:.0f})**, şu bilgilerle daha önce maskelenmiş "
                f"bir projeye ait görünüyor:\n\n"
                f"- **Proje Adı:** {suggestion.project_name}\n"
                f"- **Sicil Numarası:** {suggestion.sicil_no}\n"
                f"- **Branch Adı:** {suggestion.branch_name}\n\n"
                "Bu bilgilerle tekrar deneyin."
            )
    elif report.status != "completed":
        st.warning("Geri dönüştürme uyarılarla tamamlandı; çıktı bütünlüğünü ve atlanan dosyaları kontrol edin.")
    else:
        st.success("Geri dönüştürme tamamlandı.")
    if getattr(report, "validation_warnings", None):
        with st.expander("Bütünlük ve doğrulama uyarıları", expanded=True):
            for notice in report.validation_warnings:
                st.write(notice)

    st.markdown(
        f"**{report.total_placeholders_found} placeholder** bulundu, "
        f"**{report.total_placeholders_resolved} tanesi** başarıyla gerçek değere dönüştürüldü."
    )

    if report.has_unresolved_placeholders:
        st.markdown(f"**Çözülemeyen placeholder sayısı: {report.total_placeholders_unresolved}**")
        with st.expander("Çözülemeyen placeholder'ların dökümü", expanded=True):
            for token, count in sorted(report.unresolved_by_placeholder.items()):
                st.write(f"- `{token}`: {count} yerde")

    extra_notes = []
    if report.files_excluded:
        extra_notes.append(f"{report.files_excluded} dosya güvenlik politikası gereği hariç tutuldu (hiç kopyalanmadı).")
    if report.files_skipped_symlink:
        extra_notes.append(f"{report.files_skipped_symlink} kısayol (symlink) atlandı.")
    if report.files_copied_undecodable:
        extra_notes.append(f"{report.files_copied_undecodable} dosya okunamadı, olduğu gibi kopyalandı.")
    if report.files_errored:
        extra_notes.append(f"{report.files_errored} dosyada hata oluştu.")
    if report.target_overwritten:
        extra_notes.append("Hedef klasördeki önceki içerik silinip yenisiyle değiştirildi.")
    for note in extra_notes:
        st.caption(f"ℹ️ {note}")

    download_bytes = result.get("download_bytes")
    if "download_bytes" in result and download_bytes is not None:
        st.download_button(
            "⬇️ Geri Dönüştürülmüş Sonucu İndir",
            data=download_bytes,
            file_name=result.get("download_name") or "geri_donusturulmus_cikti.zip",
            type="primary",
        )

    if getattr(report, "job_id", None) is not None:
        st.caption(f"Kaynak maskeleme işlemi (JOB ID): {report.job_id}")
    st.caption(f"İşlem kaydı numarası: {report.run_id} (Geçmiş İşlemler ekranında aranabilir)")


# Ekranin giris noktasi: st.navigation tarafindan cagirilir. Kaynak turune
# (yol/yukleme) gore formu cizer, gonderildiginde ilgili _run_import* akisini
# tetikler ve varsa onceki sonucu gosterir.
def render() -> None:
    identity = get_identity()
    page_intro(
        "📥 Geri Al",
        "Bu ekranda daha önce maskelenmiş bir proje klasörünü, içindeki gizli placeholder'ları "
        "gerçek değerlerle değiştirerek geri döndürebilirsiniz. Sadece dışa aktarırken kullandığınız "
        "**aynı** proje/sicil/branch kimliğiyle geri dönüştürülebilir.",
    )

    st.info(
        f"Bu işlem **{identity['project_name']} / {identity['sicil_no']} / {identity['branch_name']}** "
        "kimliğiyle aranacak."
    )

    mode = st.radio(
        "Kaynak türü",
        ["📁 Klasör Yolu (bu bilgisayarda/sunucuda)", "⬆️ Dosya Yükle"],
        horizontal=True,
        key="import_mode",
    )

    if mode == "⬆️ Dosya Yükle":
        upload_kind = st.radio(
            "Ne yüklemek istiyorsunuz?",
            ["Dosya(lar) / .zip", "📂 Bir Klasör (tüm alt klasörleriyle)"],
            horizontal=True,
            key="import_upload_kind",
        )
        is_directory_upload = upload_kind == "📂 Bir Klasör (tüm alt klasörleriyle)"

        with st.form("import_form_upload"):
            if is_directory_upload:
                uploaded_files = st.file_uploader(
                    "Maskelenmiş Klasörü Seçin",
                    accept_multiple_files="directory",
                    help="Tarayıcınızın klasör seçme penceresi açılır; seçtiğiniz klasördeki tüm "
                    "dosyalar, alt klasör yapısı korunarak yüklenir.",
                    key="import_dir_uploader",
                )
            else:
                uploaded_files = st.file_uploader(
                    "Maskelenmiş Dosya(lar)ı Seçin",
                    accept_multiple_files=True,
                    help="Tek tek dosyalar seçebilir ya da tüm maskelenmiş proje klasörünü tek bir "
                    ".zip dosyası olarak yükleyebilirsiniz.",
                    key="import_file_uploader",
                )
            submitted = st.form_submit_button("Geri Dönüştür", type="primary", width="stretch")

        if submitted:
            if not uploaded_files:
                st.error("En az bir dosya/klasör seçmelisiniz.")
            else:
                _run_import_upload(uploaded_files, is_directory_upload=is_directory_upload)
    else:
        with st.form("import_form"):
            source_path = st.text_input(
                "Maskelenmiş Proje Klasörü", key="import_source", placeholder="örn. /home/kullanici/disari-aktarilan/poseidon"
            )
            target_path = st.text_input(
                "Çıktı Klasörü", key="import_target", placeholder="örn. /home/kullanici/geri-donusturulmus/poseidon"
            )
            submitted = st.form_submit_button("Geri Dönüştür", type="primary", width="stretch")

        if submitted:
            if not source_path.strip() or not target_path.strip():
                st.error("Maskelenmiş Proje Klasörü ve Çıktı Klasörü alanları boş bırakılamaz.")
            elif not Path(source_path.strip()).is_dir():
                st.error(f"Maskelenmiş proje klasörü bulunamadı: {source_path.strip()}")
            else:
                # Path izin kontrolu artik sadece backend tarafinda yapilir
                # (bkz. export_page.py'deki ayni not) - backend reddederse
                # _run_import bunu ApiError -> show_error ile gosterir.
                _run_import(source_path.strip(), target_path.strip())

    result = st.session_state.get("import_last_result")
    if result:
        st.divider()
        _render_result(result)
