"""Ekran 1 - Dışarı Çıkar (Export): bir proje klasörünü tarayıp içindeki
hassas bilgileri gizler. İnce sunum katmanı - tüm iş mantığı ayrı süreçte
çalışan FastAPI backend'inde (app/webapp/api_client.py üzerinden HTTP ile).

Tarama backend'de arka plan işi olarak çalışır; bu ekran işin durumunu
sorgulayıp "işlenen/toplam dosya" ilerleme çubuğu gösterir. Böylece uzun
(LLM açık, yüzlerce dosyalık) taramalar arayüz zaman aşımına takılmaz."""

from __future__ import annotations

import hashlib
import io
import re
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
from app.webapp.common import REVIEW_FOCUS_KEY, page_intro, show_error
# get_identity: aktif kullanicinin (sicil) kimligi; proje/branch bu ekranda
# her islem icin secilir (render_project_branch_inputs).
from app.webapp.identity import get_identity, render_project_branch_inputs, validate_project_branch
from app.webapp.path_guard import allowed_roots_configured
from app.webapp.uploads import upload_manifest
# Hizli metin modu ve yan yana karsilastirma Geri Donustur ekraniyla ortak.
from app.services.failure_messages import explain_failure
from app.webapp.quick_text import (
    InMemoryUpload, quick_text_file_name, quick_text_type_label, render_editable_side_by_side,
    render_quick_text_type_select, resolve_quick_text_type,
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
    "quarantined_pending_audit": "Dosya güvenlik kontrolünde durduruldu.",
}


# Dosyayi ciktidan alikoyan sebep ve yapilacak is. Kayitli kontrol kodu varsa
# inceleme ekraniyla AYNI aciklama kullanilir (bkz. failure_messages).
# Sozdizimi dogrulayicisinin yazdigi konum ("satir 31, sutun 70"); parser
# mesajinin kendisi kaynak parcasi tasiyabildigi icin yalnizca sayilar alinir.
_SYNTAX_POSITION_RE = re.compile(r"satir (\d+)(?:, sutun (\d+))?", re.IGNORECASE)


def _syntax_position(outcome) -> str:
    position = _SYNTAX_POSITION_RE.search(getattr(outcome, "error", None) or "")
    if position is None:
        return ""
    text = f" (maskelenmiş dosyada satır {position.group(1)}"
    return text + (f", sütun {position.group(2)})" if position.group(2) else ")")


def _validation_failure_reason(outcome) -> str:
    explained = explain_failure(getattr(outcome, "failed_check", None), outcome.error)
    if explained is not None:
        summary, next_step = explained
        return f"{summary} **Ne yapmalı:** {next_step}"
    if outcome.error:
        return outcome.error
    return _VALIDATION_FAILURE_FALLBACKS.get(outcome.status, "Teknik bir nedenle güvenli kabul edilemedi.")


_JOB_POLL_SECONDS = 1.0
# "Oncekini durdur ve yeniden baslat": onceki isin kapanmasi icin azami bekleme.
_STOP_WAIT_SECONDS = 120.0
_FINISHED_JOB_STATES = ("completed", "failed", "cancelled")
# Takip edilen is ve "ayni proje icin calisan is var" secimi session_state'te
# tutulur: sayfa yenilense, baska ekrana gidilip donulse ya da ekranda bir
# seye tiklansa da (Streamlit betigi bastan calistirir) ilerleme kaybolmaz.
_ACTIVE_JOB_KEY = "export_active_job"
_CONFLICT_KEY = "export_conflict"
# Baslatilmadan once "uzun surebilir" onayi bekleyen istek ve buyuk dosyalar.
_SLOW_KEY = "export_slow_files"


class _JobCancelled(Exception):
    """Takip edilen is kullanici tarafindan durduruldu."""


def _wait_for_export_job(job_id: str):
    """Arka plan export isini ilerleme cubuguyla bekler; sonuc nesnesini dondurur."""
    bar = st.progress(0.0, text="Tarama başlatılıyor...")
    try:
        while True:
            job = api_client.get_export_job(job_id)
            if job.status == "completed":
                return job.result
            if job.status == "cancelled":
                raise _JobCancelled(job.error_message or "Tarama durduruldu.")
            if job.status == "failed":
                raise ApiError(job.error_message or "Tarama tamamlanamadı.", job.error_detail,
                               job.error_status or 500)
            fraction = min(job.processed / job.total, 1.0) if getattr(job, "total", 0) else 0.0
            if getattr(job, "cancel_requested", False):
                bar.progress(fraction, text="Tarama durduruluyor... Yarım kalan çıktı temizleniyor.")
            elif job.status == "queued":
                bar.progress(0.0, text="Sıradasınız. Çalışan işlemler tamamlandığında taramanız başlayacak.")
            elif job.total:
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


# Taramayi backend'de baslatir ve takibe alir. Ayni proje/sicil/branch icin
# calisan bir is varsa hata yerine "durdur ve yeniden baslat" secimi sunulur.
# request: {"kind": "path"|"upload", "project_name", "branch_name", ...}
def _start_export(request: dict) -> None:
    identity = get_identity()
    common = dict(
        project_name=request["project_name"], sicil_no=identity["sicil_no"],
        branch_name=request["branch_name"], initiated_by=identity["sicil_no"],
        skip_paths=request.get("skip_paths") or [],
    )
    st.session_state["export_last_result"] = None
    try:
        if request["kind"] == "path":
            job_id = api_client.start_export_job_by_path(
                source_path=request["source_path"], target_path=request["target_path"], **common,
            )
        else:
            with st.spinner("Dosyalar yükleniyor..."):
                job_id = api_client.start_export_job_upload(
                    request["files"], is_directory_upload=request.get("is_directory_upload", False), **common,
                )
    except ApiError as exc:
        if exc.active_job_id:
            st.session_state[_CONFLICT_KEY] = {"active_job_id": exc.active_job_id, "request": request}
        else:
            show_error(exc)
        return
    st.session_state[_CONFLICT_KEY] = None
    st.session_state[_ACTIVE_JOB_KEY] = {"job_id": job_id, "quick_text": request.get("quick_text")}


# Tamamlanan isin sonucunu (yukleme modunda indirilebilir paketle) saklar.
def _store_result(result, quick_text: dict | None) -> None:
    entry = {
        "report": result.report,
        "pending_count": result.pending_count,
        "quarantined_count": result.quarantined_count,
        "validation_failed_count": getattr(result, "validation_failed_count", 0),
    }
    if getattr(result, "output_token", None):
        entry["download_bytes"] = None
        entry["download_name"] = None
        try:
            # Upload API'sinin bu is icin dondurdugu opak cikti token'i
            # dogrudan dosya paketini adresler.
            entry["download_bytes"] = api_client.download_export_output(result.output_token)
            entry["download_name"] = "maskelenmis_cikti.zip"
        except ApiError as exc:
            show_error(exc)
    if quick_text is not None:
        entry["quick_text"] = quick_text
    st.session_state["export_last_result"] = entry


# Takip edilen isin ilerlemesini "Durdur" dugmesiyle gosterir; is bitince
# sonucu saklar. Her render'da cagrilir, boylece yarim kalan takip surer.
def _follow_active_job() -> None:
    active = st.session_state.get(_ACTIVE_JOB_KEY)
    if not active:
        return
    job_id = active["job_id"]
    stop_slot = st.empty()
    if stop_slot.button("⏹ Taramayı Durdur", key=f"export_stop_{job_id}",
                        help="Tarama güvenli şekilde durdurulur: yarım kalan çıktı silinir, "
                        "varsa önceki çıktı korunur. Ardından yeniden tarayabilirsiniz."):
        try:
            api_client.cancel_export_job(job_id)
        except ApiError as exc:
            show_error(exc)
    try:
        result = _wait_for_export_job(job_id)
    except _JobCancelled as exc:
        st.session_state[_ACTIVE_JOB_KEY] = None
        stop_slot.empty()
        st.info(f"⏹ {exc}")
        return
    except ApiError as exc:
        st.session_state[_ACTIVE_JOB_KEY] = None
        stop_slot.empty()
        show_error(exc)
        return
    st.session_state[_ACTIVE_JOB_KEY] = None
    stop_slot.empty()
    _store_result(result, active.get("quick_text"))


# Onceki isi durdurur ve kapanmasini bekler. True: is artik calismiyor.
def _stop_and_wait(job_id: str) -> bool:
    try:
        api_client.cancel_export_job(job_id)
    except ApiError as exc:
        if exc.status_code == 404:
            return True
        show_error(exc)
        return False
    with st.spinner("Önceki tarama durduruluyor..."):
        deadline = time.monotonic() + _STOP_WAIT_SECONDS
        while time.monotonic() < deadline:
            try:
                job = api_client.get_export_job(job_id)
            except ApiError as exc:
                if exc.status_code == 404:
                    return True
                show_error(exc)
                return False
            if job.status in _FINISHED_JOB_STATES:
                return True
            time.sleep(_JOB_POLL_SECONDS)
    st.error("Önceki tarama henüz durmadı (son yayımlama adımında olabilir). Birkaç saniye sonra tekrar deneyin.")
    return False


# Taramayi uzun surdurecek buyuk metin dosyalarini backend'e sorar (bkz.
# app/services/slow_files.py). Hizli metin modu yapistirilan kisa bir metindir,
# sorulmaz. (esik_mb, buyuk_dosyalar) ya da hata gosterildiyse None doner.
def _slow_files_for(request: dict) -> tuple[float, list] | None:
    if request.get("quick_text") is not None:
        return 0.0, []
    try:
        if request["kind"] == "path":
            preflight = api_client.export_preflight_by_path(request["source_path"])
        else:
            manifest = upload_manifest(request["files"], is_directory_upload=request.get("is_directory_upload", False))
            preflight = api_client.export_preflight_upload(manifest)
    except ApiError as exc:
        show_error(exc)
        return None
    return preflight.warn_mb, list(preflight.slow_files)


# Buyuk dosya varsa taramayi baslatmadan once kullaniciya sorar; yoksa baslatir.
def _request_export(request: dict) -> None:
    st.session_state[_SLOW_KEY] = None
    preflight = _slow_files_for(request)
    if preflight is None:
        return
    warn_mb, slow_files = preflight
    if slow_files:
        st.session_state[_SLOW_KEY] = {"request": request, "slow_files": slow_files, "warn_mb": warn_mb}
        return
    _start_export(request)


# 1 MB = 1.048.576 bayt: SCAN_MAX_FILE_MB / SCAN_SLOW_FILE_WARN_MB sinirlari ve
# Windows Gezgini ayni birimi kullanir; boylece sinirin altindaki bir dosya
# sinirdan buyuk gorunmez. (Streamlit'in yukleme kutusu 1.000.000 bayt kullanir;
# o etiket kutunun kendi bileseni oldugu icin degistirilemez.)
def _format_mb(size_bytes: int) -> str:
    return f"{size_bytes / (1024 * 1024):.1f}".replace(".", ",") + " MB"


# Onay bekleyen buyuk dosyalar icin uyari ve secim: dosyalari taramadan
# cikarip baslat, hepsiyle baslat ya da vazgec. Cikarilan dosya backend'de
# haric tutulan dosya gibi islenir (taranmaz, ciktiya alinmaz); kullanici
# dosyayi silip yeniden yuklemek zorunda kalmaz.
def _render_slow_files() -> None:
    pending = st.session_state.get(_SLOW_KEY)
    if not pending:
        return
    slow_files = pending["slow_files"]
    paths = [item.path for item in slow_files]
    slot = st.empty()
    with slot.container():
        st.warning(
            f"**Uzun sürebilir:** {len(slow_files)} büyük metin dosyası taramayı uzatabilir. Bu dosyalar "
            f"{pending['warn_mb']:g} MB sınırından büyük; veri dökümü, log ve CSV "
            "gibi büyük metin dosyaları satır satır tarandığı için tek bir dosya taramayı dakikalarca, çok "
            "büyükse saatlerce uzatabilir. Bu sürede projedeki diğer dosyalar da sırada bekler.\n\n"
            "**Ne yapmalı:** Dosyanın maskelenmesi gerekmiyorsa (örneğin örnek veri ya da log) taramadan "
            "çıkarın: tarama diğer dosyalarla hemen başlar, çıkarılan dosya taranmaz ve çıktıya eklenmez. "
            "Maskelenmesi gerekiyorsa tümüyle devam edin; tarama arka planda sürer ve istediğiniz an "
            "durdurabilirsiniz."
        )
        st.markdown("\n".join(f"- `{item.path}` — {_format_mb(item.size_bytes)}" for item in slow_files))
        if len(paths) > 1:
            to_skip = st.multiselect("Taramadan çıkarılacak dosyalar", paths, default=paths, key="export_slow_pick")
        else:
            to_skip = paths
        skip_col, proceed_col, dismiss_col = st.columns(3)
        skip = skip_col.button(
            "Dosyayı çıkarıp başlat" if len(paths) == 1 else "Seçilenleri çıkarıp başlat",
            type="primary", key="export_slow_skip", disabled=not to_skip,
        )
        proceed = proceed_col.button("Yine de tümüyle başlat", key="export_slow_continue")
        dismiss = dismiss_col.button("Vazgeç", key="export_slow_dismiss")
    if skip or proceed:
        slot.empty()
        st.session_state[_SLOW_KEY] = None
        request = dict(pending["request"], skip_paths=list(to_skip)) if skip else pending["request"]
        _start_export(request)
    elif dismiss:
        slot.empty()
        st.session_state[_SLOW_KEY] = None


# Ayni proje/branch icin calisan bir is varken taramaya basildiysa secenekleri gosterir.
def _render_conflict() -> None:
    conflict = st.session_state.get(_CONFLICT_KEY)
    if not conflict:
        return
    slot = st.empty()
    with slot.container():
        st.warning(
            "Bu proje ve branch için şu anda devam eden bir tarama var. Önceki taramayı durdurup "
            "yenisini başlatabilir ya da devam eden taramanın ilerlemesini izleyebilirsiniz."
        )
        restart_col, follow_col, dismiss_col = st.columns(3)
        restart = restart_col.button("⏹ Öncekini durdur ve yeniden başlat", type="primary",
                                     key="export_conflict_restart")
        follow = follow_col.button("Devam eden taramayı izle", key="export_conflict_follow")
        dismiss = dismiss_col.button("Vazgeç", key="export_conflict_dismiss")
    if restart:
        if _stop_and_wait(conflict["active_job_id"]):
            slot.empty()
            st.session_state[_CONFLICT_KEY] = None
            _start_export(conflict["request"])
    elif follow:
        slot.empty()
        st.session_state[_CONFLICT_KEY] = None
        st.session_state[_ACTIVE_JOB_KEY] = {"job_id": conflict["active_job_id"], "quick_text": None}
    elif dismiss:
        slot.empty()
        st.session_state[_CONFLICT_KEY] = None


# "Klasor Yolu" moduyla (sunucudaki bir yolu dogrudan girerek) baslatilan export.
def _run_export(source_path: str, target_path: str, *, project_name: str, branch_name: str) -> None:
    _request_export({
        "kind": "path", "source_path": source_path, "target_path": target_path,
        "project_name": project_name, "branch_name": branch_name,
    })


# "Dosya Yükle" moduyla baslatilan export: yuklenen dosyalar backend'e gider;
# sonuc indirilebilir paketle birlikte saklanir.
def _run_export_upload(
    uploaded_files: list, *, project_name: str, branch_name: str, is_directory_upload: bool = False,
    quick_text: dict | None = None,
) -> None:
    _request_export({
        "kind": "upload", "files": list(uploaded_files), "is_directory_upload": is_directory_upload,
        "project_name": project_name, "branch_name": branch_name, "quick_text": quick_text,
    })


# "Hızlı Metin / Kod" modu: yapistirilan metni tek dosyalik bir yukleme
# olarak tarar. Maskeli metin, indirme paketinden (karantina karari sonrasi
# yenilenen paket dahil) her gosterimde yeniden okunur.
def _run_export_text(
    text: str, extension: str, *, project_name: str, branch_name: str, detected: bool = False,
) -> None:
    name = quick_text_file_name(extension)
    _run_export_upload(
        [InMemoryUpload(name, text.encode("utf-8"))], project_name=project_name, branch_name=branch_name,
        quick_text={"name": name, "original": text, "extension": extension, "detected": detected},
    )


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


# Hizli metin sonucunu: orijinal metni salt okunur, maskeli metni duzenlenebilir
# olarak yan yana; maskeli metni (duzenlenmis haliyle) indirme dugmesiyle gosterir.
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
    if quick.get("detected"):
        st.caption(
            f"Algılanan dosya türü: **{quick_text_type_label(quick['extension'])}**. Yanlışsa "
            "\"Dosya türü\" listesinden doğrusunu seçip taramayı tekrar başlatın."
        )
    if masked is None:
        st.warning(
            "Maskelenmiş metin güvenlik kontrollerinden geçemediği için gösterilmiyor. "
            "Yukarıdaki uyarılara bakın; karar verdikten sonra bu ekrana döndüğünüzde sonuç yenilenir."
        )
        return
    if masked == quick["original"]:
        st.caption("Metinde hassas bilgi bulunamadı; içerik değiştirilmedi.")
    # Anahtar maskeli metne baglidir: karantina karari sonrasi metin
    # yenilenirse eski duzenleme yeni sonucun ustune tasinmaz.
    edit_key = f"export_quick_edit_{hashlib.sha256(masked.encode('utf-8')).hexdigest()[:16]}"
    edited = render_editable_side_by_side(
        quick["name"], ("Orijinal Metin", quick["original"]), ("Maskelenmiş Metin (düzenlenebilir)", masked),
        key=edit_key,
    )
    st.caption(
        "Sağdaki metni düzenleyebilirsiniz; indirilen dosya düzenlenmiş hali içerir. Düzenlemeler "
        "yeniden taranmaz: gizlenmiş gerçek değerleri metne geri yazmayın."
    )
    download_column, reset_column = st.columns([1, 1])
    download_column.download_button(
        "⬇️ Maskelenmiş Metni İndir",
        data=edited.encode("utf-8"),
        file_name=f"maskelenmis_{quick['name']}",
        type="primary",
        width="stretch",
    )
    if edited != masked:
        reset_column.button(
            "↺ Düzenlemeleri Geri Al", key=f"{edit_key}_reset", width="stretch",
            on_click=lambda: st.session_state.update({edit_key: masked}),
        )


# Onay Bekleyenler ekranina gecer. focus verilirse ({run_id, file_path}) o
# dosyanin bolumu orada acik ve listenin basinda gosterilir (bkz. review_page).
def _goto_review(focus: dict | None) -> None:
    st.session_state[REVIEW_FOCUS_KEY] = focus
    st.switch_page(st.session_state["_nav_pages"]["review"])


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

    ready = getattr(report, "files_ready", None)
    if ready is not None and ready < report.files_scanned:
        st.warning(
            f"Çıktı eksik: taranan {report.files_scanned} dosyanın {ready} tanesi çıktıya hazır. "
            "Diğer dosyaların neden eklenmediğini aşağıdaki uyarılardan kontrol edin."
        )

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
    user_skipped = list(getattr(report, "user_skipped_paths", None) or [])
    if report.files_excluded - len(user_skipped):
        extra_notes.append(
            f"{report.files_excluded - len(user_skipped)} dosya hariç tutma kuralı gereği atlandı (hiç kopyalanmadı)."
        )
    if user_skipped:
        extra_notes.append(
            f"Taramadan çıkardığınız {len(user_skipped)} büyük dosya taranmadı ve çıktıya eklenmedi: "
            + ", ".join(f"`{path}`" for path in user_skipped)
        )
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
            f"🔴 {quarantined_count} dosya güvenlik kontrolünde durduruldu ve hedef klasöre KOPYALANMADI. "
            "Her dosyanın sebebi aşağıda; karar vermek için Onay Bekleyenler ekranına gidin."
        )
        quarantined = [o for o in report.outcomes if o.status == "quarantined_pending_audit"
                       and o.final_state != "VALIDATION_FAILED"]
        if quarantined:
            with st.expander(f"Durdurulan {len(quarantined)} dosyayı ve sebebini gör", expanded=True):
                for index, outcome in enumerate(quarantined):
                    reason_col, button_col = st.columns([5, 1.6], vertical_alignment="center")
                    reason_col.write(f"- `{outcome.relative_path}`: {_validation_failure_reason(outcome)}")
                    if button_col.button("Onay Bekleyenler'de incele →", key=f"goto_quarantine_{index}",
                                         width="stretch"):
                        _goto_review({"run_id": report.run_id, "file_path": outcome.relative_path})
        if st.button("🔍 Maskeleme Denetim Uyarılarını İncele →", type="primary", key="goto_audit_warnings"):
            _goto_review(None)

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
                st.write(f"- `{outcome.relative_path}`: {_validation_failure_reason(outcome)}")
        if st.button("🔍 Maskeleme Denetim Uyarılarını İncele →", type="primary", key="goto_unverifiable"):
            _goto_review(None)

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
        with st.expander(f"Sözdizimi hatası olan {len(syntax_failed)} dosyayı gör", expanded=True):
            for outcome in syntax_failed:
                st.write(f"- `{outcome.relative_path}`{_syntax_position(outcome)}: {_validation_failure_reason(outcome)}")
        if st.button("Sözdizimi hatasının konumunu gör ve dosyayı yine de çıktıya ekle →", key="goto_syntax_failed"):
            _goto_review({"run_id": report.run_id, "file_path": syntax_failed[0].relative_path}
                         if len(syntax_failed) == 1 else None)

    round_trip_failed = [o for o in report.outcomes if o.status == "failed_round_trip_validation"]
    if round_trip_failed:
        st.error(
            f"🔴 {len(round_trip_failed)} dosyada, maskelenmiş içerik geri çözüldüğünde ORİJİNALİYLE "
            "UYUŞMADI (restore garanti edilemiyor) — hedef klasöre KOPYALANMADI (başarısız_dosyalar/ "
            "klasörüne taşındı), manuel incelemeniz gerekiyor."
        )
        with st.expander(f"Round-trip doğrulaması başarısız olan {len(round_trip_failed)} dosyayı gör"):
            for outcome in round_trip_failed:
                st.write(f"- `{outcome.relative_path}`: {_validation_failure_reason(outcome)}")

    consistency_failed = [o for o in report.outcomes if o.status == "failed_consistency_validation"]
    if consistency_failed:
        st.error(
            f"🔴 {len(consistency_failed)} dosya son tutarlılık kontrolünden geçemedi; hedef pakete "
            "ALINMADI ve başarısız_dosyalar klasörüne taşındı. Her dosyanın sebebi aşağıda."
        )
        with st.expander(f"Tutarlılık kontrolünden geçemeyen {len(consistency_failed)} dosyayı gör", expanded=True):
            for outcome in consistency_failed:
                st.write(f"- `{outcome.relative_path}`: {_validation_failure_reason(outcome)}")

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
                    + (" Sözdizimi hatalı dosyaları Onay Bekleyenler ekranından bozuk haliyle pakete ekleyebilirsiniz; "
                       "bu ekrana döndüğünüzde indirme paketi yenilenir." if syntax_failed else "")
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
        "Proje dosyalarını ya da bir kod parçasını tarayıp içindeki hassas bilgileri gizleyin. "
        "Sonuç ayrı bir kopya olarak üretilir; orijinal içeriğe dokunulmaz.",
    )
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

    # Sunucudaki klasor yolu yalnizca WEB_ALLOWED_ROOTS tanimliysa calisir;
    # tanimli degilse secenek gosterilmez (secilse her zaman hata verirdi).
    modes = [_MODE_UPLOAD, _MODE_TEXT] + ([_MODE_PATH] if allowed_roots_configured() else [])
    mode = st.radio("Kaynak türü", modes, horizontal=True, key="export_mode")

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
            type_label = render_quick_text_type_select(
                key="export_text_type",
                help="Metin bu türde tek bir dosya olarak taranır; türe özgü kurallar (ör. .properties "
                "anahtarları) buna göre uygulanır.",
            )
            submitted = st.form_submit_button("Taramayı Başlat", type="primary", width="stretch")

        if submitted:
            if pair_errors:
                for message in pair_errors:
                    st.error(message)
            elif not text.strip():
                st.error("Taranacak metin boş bırakılamaz.")
            else:
                extension, detected = resolve_quick_text_type(type_label, text)
                _run_export_text(text, extension, detected=detected, **target)
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

    _render_slow_files()
    _render_conflict()
    _follow_active_job()

    result = st.session_state.get("export_last_result")
    if result:
        st.divider()
        _render_result(result)
