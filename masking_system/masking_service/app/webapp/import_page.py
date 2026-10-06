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
import io
import zipfile
from pathlib import Path

# streamlit: form/spinner/sonuc gosterimi gibi tum ekran bilesenleri icin.
import streamlit as st

# api_client: geri donusum isteklerini backend'e HTTP ile ileten fonksiyonlar.
# ApiError: bu cagrilardan biri basarisiz olursa firlatilir.
from app.webapp import api_client
from app.webapp.api_client import ApiError
# page_intro/show_error: ortak baslik ve hata gosterimi yardimcilari.
from app.webapp.common import page_intro, show_error
# get_identity: aktif kullanicinin sicili (unmask yalnizca paketi maskeleyen
# sicille calisir; proje/branch paketin islem kaydindan okunur).
from app.webapp.identity import get_identity
from app.webapp.path_guard import allowed_roots_configured
# is_single_plain_file_upload: backend'in sonucu zip'lemeden tek dosya
# olarak dondurdugu durumu ayirt etmek icin (karsilastirma onizlemesi).
from app.webapp.uploads import is_single_plain_file_upload
# Hizli metin modu ve yan yana karsilastirma Disari Cikar ekraniyla ortak.
from app.webapp.quick_text import (
    QUICK_TEXT_TYPES, InMemoryUpload, quick_text_file_name, render_side_by_side,
)

_MODE_UPLOAD = "📦 Dosya / .ZIP Yükle"
_MODE_TEXT = "📝 Hızlı Metin / Kod"
_MODE_PATH = "📁 Klasör Yolu (sunucuda)"

# Yan yana karsilastirma yalnizca makul boyuttaki metin dosyalari icin
# yapilir; buyuk/binary dosyalar onizlemeye alinmaz (indirme etkilenmez).
_PREVIEW_MAX_FILES = 300
_PREVIEW_MAX_BYTES = 256 * 1024


# Onizlemeye uygun (kucuk, UTF-8 cozulebilen, binary olmayan) baytlari
# metne cevirir; uygun degilse None.
def _decode_preview(raw: bytes) -> str | None:
    if len(raw) > _PREVIEW_MAX_BYTES:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return None if "\x00" in text else text


# Zip girdi adini, uploads.py'nin klasore yazarken kullandigi goreli yola
# cevirir ("./a/b.txt", "a\\b.txt" -> "a/b.txt").
def _normalize_entry_name(name: str) -> str:
    name = name.replace("\\", "/")
    while name.startswith("./"):
        name = name[2:]
    return name.lstrip("/")


def _zip_entries(data: bytes) -> dict[str, bytes]:
    entries: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            if info.is_dir() or info.file_size > _PREVIEW_MAX_BYTES:
                continue
            entries[_normalize_entry_name(info.filename)] = zf.read(info)
            if len(entries) >= _PREVIEW_MAX_FILES * 2:
                break
    return entries


# Yuklenen maskeli dosyalari, backend'in gecici klasore yazdigi goreli
# yollarla eslenecek sekilde {yol: bayt} olarak dondurur.
def _masked_sources(uploaded_files: list, *, is_directory_upload: bool) -> dict[str, bytes]:
    if is_directory_upload:
        return {_normalize_entry_name(uf.name): uf.getvalue() for uf in uploaded_files}
    if len(uploaded_files) == 1 and uploaded_files[0].name.lower().endswith(".zip"):
        return _zip_entries(uploaded_files[0].getvalue())
    return {Path(uf.name.replace("\\", "/")).name: uf.getvalue() for uf in uploaded_files}


# Maskeli ve geri donusturulmus iceriklerden, icerigi degisen metin
# dosyalarinin {yol: (maskeli, geri_donusturulmus)} eslemesini cikarir
# (only_changed=False ise degismeyenler de alinir).
# Dosya yolundaki placeholder'lar da cozulmusse yol eslesmez ve o dosya
# onizlemeye alinmaz (indirilen sonuc bundan etkilenmez).
def _build_preview(
    uploaded_files: list, *, is_directory_upload: bool, download_bytes: bytes | None, only_changed: bool = True,
) -> dict[str, tuple[str, str]]:
    if download_bytes is None:
        return {}
    try:
        masked = _masked_sources(uploaded_files, is_directory_upload=is_directory_upload)
        if is_single_plain_file_upload(uploaded_files, is_directory_upload=is_directory_upload):
            restored = {next(iter(masked)): download_bytes}
        else:
            restored = _zip_entries(download_bytes)
    except (zipfile.BadZipFile, OSError, StopIteration):
        return {}

    preview: dict[str, tuple[str, str]] = {}
    for name in sorted(masked):
        if name not in restored:
            continue
        masked_text = _decode_preview(masked[name])
        restored_text = _decode_preview(restored[name])
        if masked_text is None or restored_text is None or (only_changed and masked_text == restored_text):
            continue
        preview[name] = (masked_text, restored_text)
        if len(preview) >= _PREVIEW_MAX_FILES:
            break
    return preview


# "Klasor Yolu" moduyla baslatilan geri-donusum akisi: backend'e HTTP
# istegi atar, sonucu session_state'e yazar.
def _run_import(source_path: str, target_path: str, *, job_id: int | None = None) -> None:
    identity = get_identity()
    with st.spinner("Geri dönüştürülüyor... Bu işlem dosya sayısına göre biraz sürebilir."):
        try:
            result = api_client.unmask_by_path(
                source_path=source_path,
                target_path=target_path,
                sicil_no=identity["sicil_no"],
                initiated_by=identity["sicil_no"],
                job_id=job_id,
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
def _run_import_upload(
    uploaded_files: list, *, is_directory_upload: bool = False, only_changed: bool = True,
    job_id: int | None = None,
) -> None:
    identity = get_identity()
    with st.spinner("Yükleniyor ve geri dönüştürülüyor... Bu işlem dosya sayısına göre biraz sürebilir."):
        try:
            result = api_client.unmask_upload(
                uploaded_files,
                is_directory_upload=is_directory_upload,
                sicil_no=identity["sicil_no"],
                initiated_by=identity["sicil_no"],
                job_id=job_id,
            )
        except ApiError as exc:
            st.session_state["import_last_result"] = None
            show_error(exc)
            return

    download_bytes = base64.b64decode(result.download_base64) if result.download_base64 else None
    download_name = result.download_filename

    # Onceki sonucun dosya secimi yeni sonucun dosya listesinde olmayabilir.
    st.session_state.pop("import_preview_file", None)
    st.session_state["import_last_result"] = {
        "report": result.report,
        "download_bytes": download_bytes,
        "download_name": download_name,
        "preview": _build_preview(
            uploaded_files, is_directory_upload=is_directory_upload, download_bytes=download_bytes,
            only_changed=only_changed,
        ),
    }


# "Hızlı Metin / Kod" modu: yapistirilan maskeli metni tek dosyalik bir
# yukleme olarak backend'e gonderir; geri donusturulmus metni yan yana
# karsilastirma icin session_state'e yazar.
def _run_import_text(masked_text: str, extension: str, *, job_id: int | None = None) -> None:
    upload = InMemoryUpload(quick_text_file_name(extension), masked_text.encode("utf-8"))
    _run_import_upload([upload], is_directory_upload=False, only_changed=False, job_id=job_id)


# Bir geri-donusum calismasinin sonucunu (bulunan/cozulen placeholder
# sayilari, cozulemeyen placeholder'lar, kimlik uyusmazligi onerisi, indirme
# butonu) ekrana basar.
def _render_result(result: dict) -> None:
    report = result["report"]

    if report.has_unresolved_placeholders:
        st.error(
            "🔴 Bazı gizlenmiş veriler için eşleşme bulunamadı, bilgileriniz doğru mu kontrol edin. "
            "Bu yer tutucular çıktıda OLDUĞU GİBİ (geri dönüştürülmeden) bırakıldı — hiçbir "
            "değer sessizce boş geçilmedi ya da tahmin edilmedi."
        )
        suggestion = report.identity_mismatch_suggestion
        if suggestion is not None:
            st.warning(
                f"🟡 Olası neden: **yanlış proje/branch**. Çözülemeyen yer tutucuların "
                f"**{suggestion.matched_count}/{suggestion.unresolved_count} tanesi "
                f"(%{suggestion.match_ratio * 100:.0f})**, sicilinizle daha önce maskelenmiş "
                f"**{suggestion.project_name} / {suggestion.branch_name}** projesine ait görünüyor. "
                "O projenin maskeleme JOB ID'siyle tekrar deneyin."
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
        f"**{report.total_placeholders_found} gizlenmiş değer (yer tutucu)** bulundu, "
        f"**{report.total_placeholders_resolved} tanesi** başarıyla gerçek değere dönüştürüldü."
    )

    if report.has_unresolved_placeholders:
        st.markdown(f"**Çözülemeyen yer tutucu sayısı: {report.total_placeholders_unresolved}**")
        with st.expander("Çözülemeyen yer tutucuların dökümü", expanded=True):
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
        download_name = result.get("download_name") or "geri_donusturulmus_cikti.zip"
        is_zip = download_name.lower().endswith(".zip")
        st.download_button(
            "⬇️ Geri Dönüştürülmüş Sonucu İndir (.ZIP)" if is_zip else "⬇️ Geri Dönüştürülmüş Sonucu İndir",
            data=download_bytes,
            file_name=download_name,
            mime="application/zip" if is_zip else None,
            type="primary",
        )

    if "preview" in result:
        _render_comparison(result["preview"])

    if getattr(report, "project_name", None):
        st.caption(f"Paketin projesi: {report.project_name} / {report.branch_name}")
    if getattr(report, "job_id", None) is not None:
        st.caption(f"Kaynak maskeleme işlemi (JOB ID): {report.job_id}")
    st.caption(f"İşlem kaydı numarası: {report.run_id} (Geçmiş İşlemler ekranında aranabilir)")


# Maskeli ve geri donusturulmus icerigi yan yana gosterir; birden fazla
# dosya varsa hangisinin karsilastirilacagi secilir.
def _render_comparison(preview: dict[str, tuple[str, str]]) -> None:
    st.subheader("Yan Yana Karşılaştırma")
    if not preview:
        st.caption(
            "Karşılaştırılacak metin dosyası yok: içeriği değişen, boyutu uygun ve okunabilir "
            "bir dosya bulunamadı."
        )
        return

    names = list(preview)
    if len(names) == 1:
        selected = names[0]
    else:
        selected = st.selectbox(
            f"Karşılaştırılacak dosya ({len(names)} dosyada değişiklik var)", names, key="import_preview_file",
        )
    masked_text, restored_text = preview[selected]
    render_side_by_side(
        selected, ("Maskeli İçerik", masked_text), ("Orijinaline Dönüştürülmüş İçerik", restored_text),
    )


# Ekranin giris noktasi: st.navigation tarafindan cagirilir. Kaynak turune
# (yol/yukleme) gore formu cizer, gonderildiginde ilgili _run_import* akisini
# tetikler ve varsa onceki sonucu gosterir.
def render() -> None:
    identity = get_identity()
    page_intro(
        "📥 Geri Al",
        "Maskelenmiş bir paketteki gizlenmiş değerleri gerçek değerlerine döndürün. Yalnızca paketi "
        "maskeleyen sicille yapılabilir; proje ve branch paketin kaydından otomatik okunur.",
    )

    st.info(f"Geri alma **{identity['sicil_no']}** sicilinizle yapılacak.")

    modes = [_MODE_UPLOAD, _MODE_TEXT] + ([_MODE_PATH] if allowed_roots_configured() else [])
    mode = st.radio("Kaynak türü", modes, horizontal=True, key="import_mode")

    # Paketle gelen .masking-integrity.json islem numarasini zaten tasir;
    # numara yalnizca tek dosya ya da yapistirilan metin icin gerekir.
    with st.expander(
        "Maskeleme JOB ID — yalnızca tek dosya veya yapıştırılan metin için", expanded=mode == _MODE_TEXT,
    ):
        job_id_text = st.text_input(
            "JOB ID",
            key="import_job_id",
            placeholder="Örn. 27",
            help="Maskeleme sonucunda gösterilen işlem numarası. Tüm paketi (.zip veya klasör) "
            "yüklüyorsanız boş bırakın.",
        ).strip()
    job_id = int(job_id_text) if job_id_text.isdecimal() and int(job_id_text) > 0 else None
    job_id_error = "JOB ID yalnızca rakamlardan oluşmalıdır." if job_id_text and job_id is None else None

    if mode == _MODE_UPLOAD:
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
                    "Maskeli Dosyaları veya .zip Paketini Yükleyin",
                    accept_multiple_files=True,
                    help="Tek tek dosyalar seçebilir ya da tüm maskelenmiş proje klasörünü tek bir "
                    ".zip dosyası olarak yükleyebilirsiniz.",
                    key="import_file_uploader",
                )
            submitted = st.form_submit_button("Geri Dönüştür", type="primary", width="stretch")

        if submitted:
            if job_id_error:
                st.error(job_id_error)
            elif not uploaded_files:
                st.error("En az bir dosya/klasör seçmelisiniz.")
            else:
                _run_import_upload(uploaded_files, is_directory_upload=is_directory_upload, job_id=job_id)
    elif mode == _MODE_TEXT:
        with st.form("import_form_text"):
            masked_text = st.text_area(
                "Maskeli Metin / Kod",
                height=260,
                placeholder="Maskelenmiş kod parçasını veya metni buraya yapıştırın.",
                key="import_text_input",
            )
            type_label = st.selectbox(
                "Dosya türü",
                list(QUICK_TEXT_TYPES),
                help="Metin bu türde tek bir dosya olarak işlenir; sonucu dosya olarak da indirebilirsiniz.",
                key="import_text_type",
            )
            submitted = st.form_submit_button("Geri Dönüştür", type="primary", width="stretch")

        if submitted:
            if job_id_error:
                st.error(job_id_error)
            elif not masked_text.strip():
                st.error("Geri dönüştürülecek metin boş bırakılamaz.")
            else:
                _run_import_text(masked_text, QUICK_TEXT_TYPES[type_label], job_id=job_id)
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
            if job_id_error:
                st.error(job_id_error)
            elif not source_path.strip() or not target_path.strip():
                st.error("Maskelenmiş Proje Klasörü ve Çıktı Klasörü alanları boş bırakılamaz.")
            elif not Path(source_path.strip()).is_dir():
                st.error(f"Maskelenmiş proje klasörü bulunamadı: {source_path.strip()}")
            else:
                # Path izin kontrolu artik sadece backend tarafinda yapilir
                # (bkz. export_page.py'deki ayni not) - backend reddederse
                # _run_import bunu ApiError -> show_error ile gosterir.
                _run_import(source_path.strip(), target_path.strip(), job_id=job_id)

    result = st.session_state.get("import_last_result")
    if result:
        st.divider()
        _render_result(result)
