"""Ekran 1 - Dışarı Çıkar (Export): bir proje klasörünü tarayıp içindeki
hassas bilgileri gizler. İnce sunum katmanı - tüm iş mantığı ayrı süreçte
çalışan FastAPI backend'inde (app/webapp/api_client.py üzerinden HTTP ile).

Tarama backend'de arka plan işi olarak çalışır; bu ekran işin durumunu
sorgulayıp "işlenen/toplam dosya" ilerleme çubuğu gösterir. Böylece uzun
(LLM açık, yüzlerce dosyalık) taramalar arayüz zaman aşımına takılmaz."""

from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path

# streamlit: form/spinner/sonuc gosterimi gibi tum ekran bilesenleri icin.
import streamlit as st

# api_client: tarama isteklerini backend'e HTTP ile ileten fonksiyonlar.
# ApiError: bu cagrilardan biri basarisiz olursa firlatilir.
from app.webapp import api_client
from app.webapp.api_client import ApiError
# page_intro/show_error: ortak baslik ve hata gosterimi yardimcilari.
from app.webapp.common import page_intro, show_error
# get_identity: aktif kullanicinin (sicil) kimligi; proje/branch bu ekranda
# her islem icin secilir (render_project_branch_inputs).
from app.webapp.identity import get_identity, render_project_branch_inputs, validate_project_branch
# Hizli metin modu ve yan yana karsilastirma Geri Donustur ekraniyla ortak.
from app.webapp.quick_text import (
    QUICK_TEXT_TYPES, InMemoryUpload, quick_text_file_name, render_side_by_side,
)

_MODE_UPLOAD = "📦 Dosya / .ZIP Yükle"
_MODE_TEXT = "📝 Hızlı Metin / Kod"
_MODE_PATH = "📁 Klasör Yolu (sunucuda)"

# Sozdizimi/round-trip/consistency disindaki VALIDATION_FAILED durumlari icin
# yedek (fallback) aciklama - SADECE outcome.error bos oldugunda kullanilir
# (bkz. skipped_too_large/copied_undecodable: bu ikisi icin _prepare_file hic
# error metni yazmiyor). error DOLUYSA dogrudan O gosterilir (bkz.
# _validation_failure_reason) - bu kod tabaninda "error" alani zaten hicbir
# zaman ham dosya icerigi/hassas deger tasimaz (sadece OSError metni ya da
# java_classfile.py/exporter.py'nin curated, icerik-sizdirmayan Turkce yapisal
# aciklamalari, bkz. o modullerin "never log exc's message" ilkesi) - bu
# yuzden dosyaya OZEL gercek nedeni gizlemek yerine dogrudan gostermek daha
# aciklayici VE guvenli.
_VALIDATION_FAILURE_FALLBACKS: dict[str, str] = {
    "skipped_too_large": "Dosya boyutu çok büyük olduğu için güvenlik taraması tamamlanamadı.",
    "copied_undecodable": "Dosyanın karakter kodlaması çözülemediği için içeriği okunamadı.",
    "error": "Dosya işlenirken beklenmeyen bir hata oluştu.",
    "failed_finalization": "Dosya tüm kontrollerden geçti ama son kayıt adımında bir hata oluştu.",
    "failed_detection": "Bağımlılık/lock dosyası için tarama güvenilir şekilde tamamlanamadı.",
    "quarantined_pending_audit": "Otomatik güvenlik denetimi (yapay zekâ kontrolü) bu dosya için tamamlanamadı.",
}


def _validation_failure_reason(outcome) -> str:
    if outcome.error:
        return outcome.error
    return _VALIDATION_FAILURE_FALLBACKS.get(outcome.status, "Teknik bir nedenle güvenli kabul edilemedi.")


# "Klasor Yolu" moduyla (sunucudaki bir yolu dogrudan girerek) baslatilan
# export akisi: backend'e HTTP istegi atar, sonucu session_state'e yazar
# (render() bunu okuyup gosterir).
_JOB_POLL_SECONDS = 1.0


def _wait_for_export_job(job_id: str):
    """Arka plan export isini ilerleme cubuguyla bekler; sonuc nesnesini dondurur."""
    bar = st.progress(0.0, text="Tarama başlatılıyor...")
    try:
        while True:
            job = api_client.get_export_job(job_id)
            if job.status == "completed":
                return job.result
            if job.status == "failed":
                raise ApiError(job.error_message or "Tarama tamamlanamadı.", job.error_detail,
                               job.error_status or 500)
            if job.total:
                fraction = min(job.processed / job.total, 1.0)
                if job.processed >= job.total:
                    text = f"{job.total} dosya işlendi — son tutarlılık ve bütünlük kontrolleri yapılıyor..."
                else:
                    text = f"{job.processed} / {job.total} dosya işlendi"
                bar.progress(fraction, text=text)
            else:
                bar.progress(0.0, text="Dosyalar listeleniyor ve hazırlanıyor...")
            time.sleep(_JOB_POLL_SECONDS)
    finally:
        bar.empty()


def _run_export(source_path: str, target_path: str, *, project_name: str, branch_name: str) -> None:
    identity = get_identity()
    try:
        job_id = api_client.start_export_job_by_path(
            source_path=source_path,
            target_path=target_path,
            project_name=project_name,
            sicil_no=identity["sicil_no"],
            branch_name=branch_name,
            initiated_by=identity["sicil_no"],
        )
        result = _wait_for_export_job(job_id)
    except ApiError as exc:
        st.session_state["export_last_result"] = None
        show_error(exc)
        return

    st.session_state["export_last_result"] = {
        "report": result.report,
        "pending_count": result.pending_count,
        "quarantined_count": result.quarantined_count,
        "validation_failed_count": getattr(result, "validation_failed_count", 0),
    }


# "Dosya Yükle" moduyla baslatilan export akisi: yuklenen dosyalari backend'e
# HTTP ile gonderir, sonucu (indirilebilir bayt dizisiyle birlikte)
# session_state'e yazar.
def _run_export_upload(
    uploaded_files: list, *, project_name: str, branch_name: str, is_directory_upload: bool = False,
) -> None:
    identity = get_identity()
    try:
        with st.spinner("Dosyalar yükleniyor..."):
            job_id = api_client.start_export_job_upload(
                uploaded_files,
                is_directory_upload=is_directory_upload,
                project_name=project_name,
                sicil_no=identity["sicil_no"],
                branch_name=branch_name,
                initiated_by=identity["sicil_no"],
            )
        result = _wait_for_export_job(job_id)
    except ApiError as exc:
        st.session_state["export_last_result"] = None
        show_error(exc)
        return

    download_bytes: bytes | None = None
    download_name: str | None = None
    if result.output_token:
        try:
            # Upload API'sinin bu is icin dondurdugu opak cikti token'i
            # dogrudan dosya paketini adresler.
            download_bytes = api_client.download_export_output(result.output_token)
            download_name = "maskelenmis_cikti.zip"
        except ApiError as exc:
            show_error(exc)

    st.session_state["export_last_result"] = {
        "report": result.report,
        "pending_count": result.pending_count,
        "quarantined_count": result.quarantined_count,
        "validation_failed_count": getattr(result, "validation_failed_count", 0),
        "download_bytes": download_bytes,
        "download_name": download_name,
    }


# "Hızlı Metin / Kod" modu: yapistirilan metni tek dosyalik bir yukleme
# olarak tarar. Maskeli metin, indirme paketinden (karantina karari sonrasi
# yenilenen paket dahil) her gosterimde yeniden okunur.
def _run_export_text(text: str, extension: str, *, project_name: str, branch_name: str) -> None:
    name = quick_text_file_name(extension)
    _run_export_upload(
        [InMemoryUpload(name, text.encode("utf-8"))], project_name=project_name, branch_name=branch_name,
    )
    result = st.session_state.get("export_last_result")
    if result is not None:
        result["quick_text"] = {"name": name, "original": text}


# Export indirme paketinden hizli metnin maskeli halini cikarir; dosya
# karantinada/dogrulanamamis oldugu icin pakete girmediyse None.
def _masked_quick_text(download_bytes: bytes, name: str) -> str | None:
    try:
        with zipfile.ZipFile(io.BytesIO(download_bytes)) as zf:
            files = [info for info in zf.infolist() if not info.is_dir()]
            match = next((info for info in files if info.filename == name), None)
            if match is None and len(files) == 1:
                match = files[0]
            if match is None:
                return None
            return zf.read(match).decode("utf-8", errors="replace")
    except zipfile.BadZipFile:
        return None


# Hizli metin sonucunu: orijinal ve maskeli metni yan yana, maskeli metni
# tek dosya olarak indirme dugmesiyle gosterir.
def _render_quick_text_result(result: dict) -> None:
    quick = result["quick_text"]
    download_bytes = result.get("download_bytes")
    if download_bytes is None:
        st.warning(
            "Maskelenmiş metin alınamadı. Tarama sonucu kaydedildi; "
            f"işlem #{result['report'].run_id} için yeniden deneyin."
        )
        return
    masked = _masked_quick_text(download_bytes, quick["name"])
    st.subheader("Yan Yana Karşılaştırma")
    if masked is None:
        st.warning(
            "Maskelenmiş metin güvenlik kontrollerinden geçemediği için gösterilmiyor. "
            "Yukarıdaki uyarılara bakın; karar verdikten sonra bu ekrana döndüğünüzde sonuç yenilenir."
        )
        return
    if masked == quick["original"]:
        st.caption("Metinde hassas bilgi bulunamadı; içerik değiştirilmedi.")
    render_side_by_side(quick["name"], ("Orijinal Metin", quick["original"]), ("Maskelenmiş Metin", masked))
    st.download_button(
        "⬇️ Maskelenmiş Metni İndir",
        data=masked.encode("utf-8"),
        file_name=f"maskelenmis_{quick['name']}",
        type="primary",
    )


# Rapordaki dosya sayilarindan ("N dosya tarandi, M dosyada bulgu bulundu, ...")
# tek satirlik ozet bir Turkce cumle uretir.
def _clean_copied_label(report) -> str:
    copied_unchanged = report.files_copied_binary + report.files_copied_undecodable + report.files_skipped_too_large
    parts = [f"**{report.files_scanned} dosya** tarandı"]
    if report.files_masked:
        parts.append(f"**{report.files_masked} dosyada** hassas bilgi bulundu ve gizlendi")
    if report.files_copied_text_no_match:
        parts.append(f"**{report.files_copied_text_no_match} dosya** temiz (hassas bilgi bulunamadı)")
    if copied_unchanged:
        parts.append(f"**{copied_unchanged} dosya** doğrulanamadı ve çıktıya alınmadı")
    unsupported = getattr(report, "files_skipped_unsupported", 0)
    if unsupported:
        parts.append(f"**{unsupported} dosya** desteklenmeyen içerik nedeniyle atlandı")
    return ", ".join(parts) + "."


# Bir export calismasinin sonucunu (ozet, hata notlari, karantina/sozdizimi/
# round-trip uyarilari, onay kuyrugu sayisi, indirme butonu) ekrana basar.
def _refresh_review_download(result: dict) -> None:
    if not result.get("download_needs_refresh"):
        return
    result["download_bytes"] = None
    try:
        result["download_bytes"] = api_client.download_run_output(result["report"].run_id)
        result["download_needs_refresh"] = False
    except ApiError as exc:
        show_error(exc)


def _render_result(result: dict) -> None:
    _refresh_review_download(result)
    report = result["report"]
    pending_count = result["pending_count"]
    quarantined_count = result["quarantined_count"]

    if report.status != "completed":
        st.warning("Tarama tamamlandı ama dikkat edilmesi gereken noktalar var:")
    else:
        st.success("Tarama tamamlandı.")

    degraded_detectors = getattr(report, "degraded_detectors", None)
    if degraded_detectors:
        st.warning(
            f"Şu tespit katman(lar)ı bu çalışma boyunca düşük kapasiteli (fallback) modda çalıştı, "
            f"tespit kapsamı eksik olabilir: {', '.join(degraded_detectors)}."
        )

    st.markdown(_clean_copied_label(report))
    notices = list(getattr(report, "validation_notices", None) or [])
    actionable = [w for w in (getattr(report, "validation_warnings", None) or []) if w not in notices]
    if actionable:
        with st.expander(f"Doğrulama uyarısı olan {len(actionable)} kayıt", expanded=True):
            for warning in actionable:
                st.write(warning)
    for summary in getattr(report, "validation_notice_summary", None) or []:
        st.caption(f"ℹ️ {summary}")
    unsupported = getattr(report, "files_skipped_unsupported", 0)
    if unsupported:
        st.info(
            f"ℹ️ {unsupported} dosya desteklenen format kapsamı dışında (metin olarak tanınamadı). "
            "Bu dosyalar taranmadı ve çıktıya alınmadı; indirilen proje bu dosyaları içermez."
        )
        with st.expander(f"Kapsam dışı {unsupported} dosyayı ve nedenlerini gör"):
            for outcome in report.outcomes:
                if outcome.status == "skipped_unsupported":
                    st.write(f"{outcome.relative_path}: {outcome.error}")
    extra_notes = []
    if report.files_excluded:
        extra_notes.append(f"{report.files_excluded} dosya hariç tutma kuralı gereği atlandı (hiç kopyalanmadı).")
    if report.files_skipped_symlink:
        extra_notes.append(f"{report.files_skipped_symlink} kısayol (symlink) atlandı.")
    if report.files_copied_undecodable:
        extra_notes.append(f"{report.files_copied_undecodable} dosya okunamadı ve çıktıya alınmadı.")
    if report.files_errored:
        extra_notes.append(f"{report.files_errored} dosyada hata oluştu.")
    if report.target_overwritten:
        extra_notes.append("Hedef klasördeki önceki içerik silinip yenisiyle değiştirildi.")
    for note in extra_notes:
        st.caption(f"ℹ️ {note}")

    if quarantined_count:
        st.error(
            f"🔴 {quarantined_count} dosya, maskeleme sonrası bağımsız denetimden geçemediği için "
            "hedef klasöre KOPYALANMADI. Bu, sıradan bir şüpheli bulgudan daha ciddi bir durumdur "
            "— insan onayı gerekiyor."
        )
        if st.button("⚠️ Maskeleme Denetim Uyarılarını İncele →", type="primary", key="goto_audit_warnings"):
            st.switch_page(st.session_state["_nav_pages"]["review"])

    # archive_unsupported/scan_only_sensitive: final_state == SECURITY_QUARANTINE
    # oldugu icin ne quarantined_count'a (audit_failed=False sarti) ne de
    # asagidaki other_validation_failed'a (final_state == VALIDATION_FAILED
    # sarti) dahil olurlar - digerlerinin izledigi "dosya + kisa neden"
    # deseniyle burada ayrica listelenmezlerse rapor status'u "completed_with_
    # warnings" olsa bile ekranda hicbir aciklama gorunmuyordu.
    unverifiable = [
        o for o in report.outcomes
        if o.status in {"archive_unsupported", "scan_only_sensitive"}
    ]
    if unverifiable:
        st.error(
            f"🔴 {len(unverifiable)} dosya (arşiv/paket ya da bağımlılık-lock dosyası) içeriği "
            "güvenli şekilde doğrulanamadığı için hedef klasöre KOPYALANMADI — incelemeye gönderildi."
        )
        with st.expander(f"Doğrulanamayan {len(unverifiable)} dosyayı ve nedenini gör", expanded=True):
            for outcome in unverifiable:
                st.write(f"- `{outcome.relative_path}`: {outcome.error}")
        if st.button("⚠️ Maskeleme Denetim Uyarılarını İncele →", type="primary", key="goto_unverifiable"):
            st.switch_page(st.session_state["_nav_pages"]["review"])

    # validation_failed_count; asagidaki sozdizimi/round-trip/consistency
    # bloklarinin kapsadigi statuslerin YANI SIRA (skipped_too_large,
    # copied_undecodable, error, failed_finalization, failed_detection,
    # quarantined_pending_audit'in teknik-hata varyanti gibi) diger tum
    # VALIDATION_FAILED dosyalari da icerir - o dosyalar burada, ayni "hangi
    # dosya + kisa neden" bicimiyle, jenerik/belirsiz tek satirlik ozet
    # YERINE listelenir (bkz. _validation_failure_reason).
    _detailed_validation_statuses = {
        "failed_syntax_validation", "failed_round_trip_validation", "failed_consistency_validation",
    }
    other_validation_failed = [
        o for o in report.outcomes
        if o.final_state == "VALIDATION_FAILED" and o.status not in _detailed_validation_statuses
    ]
    if other_validation_failed:
        st.error(
            f"{len(other_validation_failed)} dosya güvenlik kontrolünden geçemediği için çıktıya "
            "dahil edilmedi. Bu dosyaları inceleyip gerekirse düzelttikten sonra projeyi tekrar taramanız gerekir."
        )
        with st.expander(f"Etkilenen {len(other_validation_failed)} dosyayı ve nedenini gör", expanded=True):
            for outcome in other_validation_failed:
                st.write(f"- `{outcome.relative_path}`: {_validation_failure_reason(outcome)}")

    # Sozdizimi/round-trip dogrulamasindan gecemeyen dosyalar da hedef
    # klasore YAZILMAZ (basarisiz_dosyalar/'a tasinir) - bu SESSIZCE
    # gecilmemeli, kullaniciya acikca gosterilmeli (rapor nesnesi bunu
    # zaten tutuyordu ama bu ekran ana bilgi kaynagi olarak report.
    # summary_text() yerine kendi ozel render'ini kullandigi icin
    # gosterilmiyordu).
    syntax_failed = [o for o in report.outcomes if o.status == "failed_syntax_validation"]
    if syntax_failed:
        st.error(
            f"🔴 {len(syntax_failed)} dosya, maskeleme SÖZDİZİMİNİ bozduğu için hedef klasöre "
            "KOPYALANMADI (başarısız_dosyalar/ klasörüne taşındı) — manuel incelemeniz gerekiyor."
        )
        with st.expander(f"Sözdizimi hatası olan {len(syntax_failed)} dosyayı gör"):
            for outcome in syntax_failed:
                st.write(f"- `{outcome.relative_path}`: {outcome.error}")

    round_trip_failed = [o for o in report.outcomes if o.status == "failed_round_trip_validation"]
    if round_trip_failed:
        st.error(
            f"🔴 {len(round_trip_failed)} dosyada, maskelenmiş içerik geri çözüldüğünde ORİJİNALİYLE "
            "UYUŞMADI (restore garanti edilemiyor) — hedef klasöre KOPYALANMADI (başarısız_dosyalar/ "
            "klasörüne taşındı), manuel incelemeniz gerekiyor."
        )
        with st.expander(f"Round-trip doğrulaması başarısız olan {len(round_trip_failed)} dosyayı gör"):
            for outcome in round_trip_failed:
                st.write(f"- `{outcome.relative_path}`: {outcome.error}")

    consistency_failed = [o for o in report.outcomes if o.status == "failed_consistency_validation"]
    if consistency_failed:
        st.error(
            f"🔴 {len(consistency_failed)} dosyada final tutarlılık taraması, ilk turda doğrulanmış "
            "hassas değerlerin tamamen kapatıldığını garanti edemedi. Bu dosyalar hedef pakete "
            "ALINMADI ve başarısız_dosyalar klasörüne taşındı."
        )
        with st.expander(f"Tutarlılık doğrulaması başarısız olan {len(consistency_failed)} dosyayı gör"):
            for outcome in consistency_failed:
                st.write(f"- `{outcome.relative_path}`: {outcome.error}")

    if pending_count:
        st.warning(f"⚠️ {pending_count} adet şüpheli bulgu var, onaylanması gerekiyor.")
        if st.button("Onay Bekleyenler ekranına git →", key="goto_review_queue"):
            st.switch_page(st.session_state["_nav_pages"]["review"])

    if "quick_text" in result:
        _render_quick_text_result(result)
    elif "download_bytes" in result:  # yukleme modu - path modunda bu anahtar hic yok
        download_bytes = result["download_bytes"]
        if download_bytes is not None:
            st.download_button(
                "⬇️ Maskelenmiş Sonucu İndir",
                data=download_bytes,
                file_name=result.get("download_name") or "maskelenmis_cikti.zip",
                type="primary",
            )
            if quarantined_count:
                st.caption(
                    "ℹ️ İlk taramada karantinaya alınan dosyalar o aşamada pakete eklenmedi. "
                    "Karar sonrasında doğrulamadan geçen dosyalar çıktıya eklenir. "
                    "Onay ekranından döndüğünüzde indirme paketi yenilenir; tarama raporu ilk çalışmanın sonucudur."
                )
            if syntax_failed or round_trip_failed or consistency_failed:
                st.caption(
                    "ℹ️ Sözdizimi/round-trip/consistency hatası olan "
                    f"{len(syntax_failed) + len(round_trip_failed) + len(consistency_failed)} "
                    "dosya da yukarıdaki indirilen pakete DAHİL EDİLMEDİ — yukarıdaki listeye bakıp "
                    "manuel olarak inceleyin/düzeltin."
                )
        else:
            # `None` yalnizca indirme isteginin teknik olarak basarisiz
            # oldugunu anlatir; dosya sayisi veya karantina nedeni hakkinda
            # buradan cikarim yapilamaz. Onceki sabit metin 185 dosyalik,
            # karantinasiz bir calismayi bile "tek dosya karantinada" diye
            # yanlis raporluyordu.
            st.warning(
                "İndirme paketi hazırlanamadı. Tarama sonucu kaydedildi; "
                f"işlem #{report.run_id} için indirmeyi yeniden deneyin."
            )

    st.caption(f"Maskeleme işlem numarası (JOB ID): {report.run_id}. Tek dosyayı geri alırken bu numarayı kullanın.")


# Ekranin giris noktasi: st.navigation tarafindan cagirilir. Kaynak turune
# (yukleme/hizli metin/yol) gore formu cizer, gonderildiginde ilgili _run_export* akisini
# tetikler ve varsa onceki sonucu gosterir.
def render() -> None:
    identity = get_identity()
    page_intro(
        "📤 Dışarı Çıkar",
        "Bu ekranda bir proje klasörünü, dosyaları ya da yapıştırdığınız bir kod parçasını tarayıp "
        "içindeki hassas bilgileri (IP adresi, e-posta, şifre/anahtar gibi) otomatik olarak "
        "gizleyebilirsiniz. Sonuç güvenli bir kopya olarak üretilir; orijinal içeriğinize hiçbir "
        "şekilde dokunulmaz.",
    )

    st.caption("Geri dönüşüm ve desteklenen dosya türlerinde sözdizimi kontrol edilir. Çıktı derlenmez veya çalıştırılmaz; çalışma davranışı ve tüm hassas verilerin yakalandığı garanti edilmez.")
    project_name, branch_name = render_project_branch_inputs("export_")
    pair_errors = validate_project_branch(project_name, branch_name)
    if pair_errors:
        st.info("Taramayı başlatmadan önce proje ve branch'i seçin ya da yazın.")
    else:
        st.info(
            f"Bu işlem **{project_name} / {branch_name}** projesine, **{identity['sicil_no']}** "
            "sicilinizle kaydedilecek."
        )
    target = {"project_name": project_name, "branch_name": branch_name}

    mode = st.radio(
        "Kaynak türü",
        [_MODE_UPLOAD, _MODE_TEXT, _MODE_PATH],
        horizontal=True,
        key="export_mode",
    )

    if mode == _MODE_UPLOAD:
        upload_kind = st.radio(
            "Ne yüklemek istiyorsunuz?",
            ["Dosya(lar) / .zip", "📂 Bir Klasör (tüm alt klasörleriyle)"],
            horizontal=True,
            key="export_upload_kind",
        )
        is_directory_upload = upload_kind == "📂 Bir Klasör (tüm alt klasörleriyle)"

        with st.form("export_form_upload"):
            if is_directory_upload:
                uploaded_files = st.file_uploader(
                    "Bir Klasör Seçin",
                    accept_multiple_files="directory",
                    help="Tarayıcınızın klasör seçme penceresi açılır; seçtiğiniz klasördeki tüm "
                    "dosyalar, alt klasör yapısı korunarak yüklenir.",
                    key="export_dir_uploader",
                )
            else:
                uploaded_files = st.file_uploader(
                    "Dosya(lar)ı Seçin",
                    accept_multiple_files=True,
                    help="Tek tek dosyalar seçebilir ya da tüm proje klasörünü tek bir .zip dosyası "
                    "olarak yükleyebilirsiniz (alt klasör yapısı sadece .zip yüklemede korunur).",
                    key="export_file_uploader",
                )
            submitted = st.form_submit_button("Taramayı Başlat", type="primary", width="stretch")

        if submitted:
            if pair_errors:
                for message in pair_errors:
                    st.error(message)
            elif not uploaded_files:
                st.error("En az bir dosya/klasör seçmelisiniz.")
            else:
                _run_export_upload(uploaded_files, is_directory_upload=is_directory_upload, **target)
    elif mode == _MODE_TEXT:
        with st.form("export_form_text"):
            text = st.text_area(
                "Metin / Kod",
                height=260,
                placeholder="Maskelenecek kod parçasını veya metni buraya yapıştırın.",
                key="export_text_input",
            )
            type_label = st.selectbox(
                "Dosya türü",
                list(QUICK_TEXT_TYPES),
                help="Metin bu türde tek bir dosya olarak taranır; türe özgü kurallar (ör. .properties "
                "anahtarları) buna göre uygulanır.",
                key="export_text_type",
            )
            submitted = st.form_submit_button("Taramayı Başlat", type="primary", width="stretch")

        if submitted:
            if pair_errors:
                for message in pair_errors:
                    st.error(message)
            elif not text.strip():
                st.error("Taranacak metin boş bırakılamaz.")
            else:
                _run_export_text(text, QUICK_TEXT_TYPES[type_label], **target)
    else:
        with st.form("export_form"):
            source_path = st.text_input(
                "Kaynak Klasör", key="export_source", placeholder="örn. /home/kullanici/projelerim/poseidon"
            )
            target_path = st.text_input(
                "Hedef Klasör", key="export_target", placeholder="örn. /home/kullanici/disari-aktarilan/poseidon"
            )
            submitted = st.form_submit_button("Taramayı Başlat", type="primary", width="stretch")

        if submitted:
            if pair_errors:
                for message in pair_errors:
                    st.error(message)
            elif not source_path.strip() or not target_path.strip():
                st.error("Kaynak Klasör ve Hedef Klasör alanları boş bırakılamaz.")
            elif not Path(source_path.strip()).is_dir():
                st.error(f"Kaynak klasör bulunamadı: {source_path.strip()}")
            else:
                # 'Klasor Yolu' modunun izinli olup olmadigi (WEB_ALLOWED_ROOTS)
                # BILEREK burada kontrol edilmiyor - path artik backend'e ag
                # uzerinden giden bir girdi, tek gecerli dogrulama backend
                # tarafinda yapilir (bkz. app/webapp/path_guard.py, artik
                # sadece backend tarafindan kullanilir); backend reddederse
                # _run_export bunu ApiError -> show_error ile gosterir.
                _run_export(source_path.strip(), target_path.strip(), **target)

    result = st.session_state.get("export_last_result")
    if result:
        st.divider()
        _render_result(result)
