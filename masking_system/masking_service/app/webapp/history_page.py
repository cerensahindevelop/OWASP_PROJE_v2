"""Ekran 4 - Geçmiş İşlemler (History): bu kimliğe ait tüm export/import
çalışmalarının listesi ve run bazlı detay. İnce sunum katmanı - tüm veri
ayrı süreçte çalışan FastAPI backend'inden (app/webapp/api_client.py) HTTP
üzerinden okunur."""

from __future__ import annotations

# streamlit: tablo/expander/badge gibi tum ekran bilesenleri icin.
import streamlit as st

# api_client: gecmis islem (run) listesini, dosya bazli denetim izini ve
# bir run'a ait karantina/onay kuyrugu kayitlarini backend'den okur.
# ApiError: backend'e ulasilamamasi/hata donmesi durumunda firlatilir.
from app.webapp.api_client import ApiError
from app.webapp import api_client
from app.webapp.audit_presentation import present_audit_entries, summarize_audit_entries
# Ortak etiket/renk sozlukleri (islem turu, durum, guven seviyesi) ve
# baslik/hata gosterimi yardimcilari.
from app.webapp.common import (
    CONFIDENCE_COLORS,
    CONFIDENCE_LABELS,
    OPERATION_LABELS,
    STATUS_COLORS,
    STATUS_LABELS,
    page_intro,
    show_error,
)
# get_identity: sadece aktif kimlige ait gecmis islemleri filtrelemek icin.
from app.webapp.identity import get_identity, render_project_branch_filter
from app.webapp.time_display import format_turkey_time

_REVIEW_STATUS_LABELS = {
    "pending": "Onay bekliyor",
    "approved": "Onaylandı",
    "rejected": "Reddedildi",
    "ignored": "Yok sayıldı",
}
_AUDIT_STATUS_LABELS = {
    "pending": "Onay bekliyor",
    "confirmed": "Risk doğrulandı (karantinada)",
    "dismissed": "Yeniden doğrulandı ve çıktıya alındı",
}


# Secilen bir run icin dosya bazli islem izini, onay kuyrugu ve
# denetim uyarisi kayitlarini detayli tablolar halinde gosterir.
def _render_detail(run) -> None:
    run_id = run.run_id
    st.divider()
    st.subheader(f"İşlem Detayı — kayıt no {run_id}")

    duration_text = "Devam ediyor"
    if run.started_at and run.completed_at:
        elapsed_seconds = max(0, int((run.completed_at - run.started_at).total_seconds()))
        minutes, seconds = divmod(elapsed_seconds, 60)
        duration_text = f"{minutes} dk {seconds} sn" if minutes else f"{seconds} sn"

    metric_cols = st.columns(4)
    metric_cols[0].metric("İşlem", OPERATION_LABELS.get(run.operation_type, run.operation_type))
    metric_cols[1].metric("Durum", STATUS_LABELS.get(run.status, run.status))
    metric_cols[2].metric("Taranan dosya", run.files_scanned if run.files_scanned is not None else "—")
    metric_cols[3].metric("Maskelenen bulgu", run.match_count if run.match_count is not None else "—")
    st.caption(
        f"Başlangıç (Türkiye saati): {format_turkey_time(run.started_at)} · "
        f"Süre: {duration_text} · İşlemi başlatan: {run.initiated_by}"
    )
    with st.expander("Kaynak ve hedef bilgilerini göster"):
        st.write(f"**Kaynak:** `{run.source_path}`")
        st.write(f"**Hedef:** `{run.target_path or 'Hedef bilgisi yok'}`")

    try:
        entries = api_client.get_run_audit_entries(run_id)
        review_items = api_client.list_reviews_for_run(run_id)
        audit_warning_items = api_client.list_audit_warnings_for_run(run_id)
    except ApiError as exc:
        show_error(exc)
        return
    audit_warning_rows = [
        {
            "id": item.id,
            "Dosya": item.file_path,
            "Gerekçe": item.reasoning,
            "Denetim Başarısız mı": item.audit_failed,
            "Durum": item.status,
        }
        for item in audit_warning_items
    ]
    audit_rows = present_audit_entries(entries)
    file_summary_rows = summarize_audit_entries(entries)
    review_rows = [
        {
            "id": item.id,
            "Dosya": item.file_path,
            "Satır": item.line_number,
            "Bulunan değer": item.found_value,
            "Güven": item.confidence_level,
            "Durum": item.status,
        }
        for item in review_items
    ]

    if not audit_rows:
        st.info("Bu işlem için kayıtlı bir detay bulunamadı.")
    else:
        st.markdown("**Dosya bazlı işlem özeti**")
        st.caption(
            "Her dosya tek satırda gösterilir. Güvenlik notu; çakışma çözümü veya token sınırı "
            "kontrolü gibi otomatik koruma kararlarını ifade eder, tek başına hata değildir."
        )
        st.dataframe(
            file_summary_rows,
            hide_index=True,
            width="stretch",
            column_config={
                "Dosya": st.column_config.TextColumn(width="large"),
                "Tarama": st.column_config.TextColumn(width="small"),
                "Bulgu": st.column_config.NumberColumn(width="small"),
                "Maskelenen": st.column_config.NumberColumn(width="small"),
                "Güvenlik notu": st.column_config.NumberColumn(width="small"),
                "Hata": st.column_config.NumberColumn(width="small"),
                "Sonuç": st.column_config.TextColumn(width="medium"),
            },
        )
        with st.expander(f"Teknik işlem izini göster ({len(audit_rows)} olay)"):
            st.caption(
                "Bu bölüm hangi tarama katmanının ne karar verdiğini zaman sırasıyla gösterir. "
                "Kurumsal kural hash’leri ve gereksiz karakter offsetleri gösterilmez."
            )
            st.dataframe(audit_rows, hide_index=True, width="stretch")

    if audit_warning_rows:
        st.markdown("**🔴 Bu işlemden çıkan maskeleme denetim uyarıları (ikincil risk)**")
        pending_audit_count = sum(1 for r in audit_warning_rows if r["Durum"] == "pending")
        if pending_audit_count:
            st.error(f"Bu işlemden {pending_audit_count} dosya hâlâ karantinada, insan onayı bekliyor.")
        for row in audit_warning_rows:
            with st.container(border=True):
                col1, col2 = st.columns([3, 1.4])
                with col1:
                    st.write(f"📄 {row['Dosya']}")
                    st.caption(row["Gerekçe"])
                with col2:
                    st.badge(
                        "Denetim Yapılamadı" if row["Denetim Başarısız mı"] else "İkincil Risk",
                        color="orange" if row["Denetim Başarısız mı"] else "red",
                    )
                    st.caption(_AUDIT_STATUS_LABELS.get(row["Durum"], row["Durum"]))

    if review_rows:
        st.markdown("**Bu işlemden çıkan onay kuyruğu kayıtları**")
        pending_count = sum(1 for r in review_rows if r["Durum"] == "pending")
        if pending_count:
            st.warning(f"Bu işlemden {pending_count} kayıt hâlâ onay bekliyor.")
        for row in review_rows:
            with st.container(border=True):
                col1, col2 = st.columns([3, 1])
                with col1:
                    location = row["Dosya"]
                    if row["Satır"]:
                        location += f" — satır {row['Satır']}"
                    st.write(f"📄 {location}")
                    st.code(row["Bulunan değer"] or "(değer kaydedilmemiş)")
                with col2:
                    st.badge(
                        CONFIDENCE_LABELS.get(row["Güven"], row["Güven"]),
                        color=CONFIDENCE_COLORS.get(row["Güven"], "gray"),
                    )
                    st.caption(_REVIEW_STATUS_LABELS.get(row["Durum"], row["Durum"]))


# Ekranin giris noktasi: aktif kimlige ait tum run'lari tablo olarak
# listeler; kullanici bir satira tikladiginda _render_detail ile detayi acar.
def render() -> None:
    identity = get_identity()
    page_intro(
        "🗂️ Geçmiş İşlemler",
        "Bu ekranda, sicilinizle yaptığınız tüm proje ve branch'lerdeki geçmiş Dışarı Çıkar ve "
        "Geri Al işlemlerini görebilir, herhangi birini seçerek dosya bazlı detaylarını "
        "inceleyebilirsiniz.",
    )

    project_name, branch_name = render_project_branch_filter("history_")
    try:
        runs = api_client.list_runs(
            project_name=project_name,
            sicil_no=identity["sicil_no"],
            branch_name=branch_name,
            limit=200,
        )
    except ApiError as exc:
        show_error(exc)
        return

    if not runs:
        st.info("Bu filtreyle eşleşen bir işlem yok.")
        return

    table_rows = [
        {
            "run_id": r.run_id,
            # Yalnizca maskeleme islemlerinin numarasi geri almada kullanilir.
            "JOB ID": r.run_id if r.operation_type == "mask" else None,
            "Tarih": format_turkey_time(r.started_at, "%d.%m.%Y %H:%M", missing="-"),
            "Proje": r.project_name,
            "Branch": r.branch_name,
            "İşlem": OPERATION_LABELS.get(r.operation_type, r.operation_type),
            "Durum": STATUS_LABELS.get(r.status, r.status),
            "Dosya sayısı": r.files_scanned if r.files_scanned is not None else "-",
            "Bulgu sayısı": r.match_count if r.match_count is not None else "-",
        }
        for r in runs
    ]

    st.caption("Ayrıntı için satırın solundaki kutuyu işaretleyin. Saatler Türkiye saatidir.")
    event = st.dataframe(
        table_rows,
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        column_order=["JOB ID", "Tarih", "Proje", "Branch", "İşlem", "Durum", "Dosya sayısı", "Bulgu sayısı"],
        column_config={"JOB ID": st.column_config.NumberColumn(
            "JOB ID", format="%d",
            help="İşlem numarası. Tek dosya veya yapıştırılan metni geri alırken Geri Al ekranında kullanılır.",
        )},
    )

    selected_run = None
    if event and event.selection and event.selection.get("rows"):
        idx = event.selection["rows"][0]
        selected_run = runs[idx]

    if selected_run is not None:
        _render_detail(selected_run)
