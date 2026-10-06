"""Ekran 2 - Onay Bekleyenler (Review Queue): modelin şüphelendiği ama
kesin emin olmadığı bulguları insan onayına sunar. İnce sunum katmanı -
tüm iş mantığı ayrı süreçte çalışan FastAPI backend'inde
(app/webapp/api_client.py üzerinden HTTP ile)."""

from __future__ import annotations

# streamlit: buton/checkbox/badge gibi tum onay-kuyrugu ekran bilesenleri icin.
import streamlit as st

# api_client: onaylatma/reddetme/dogrulama/serbest-birakma islemlerini
# backend'e HTTP ile ileten fonksiyonlar. ApiError: bu cagrilardan biri
# basarisiz olursa (backend hatasi/erisilemezlik) firlatilir.
from app.webapp import api_client
from app.webapp.api_client import ApiError
# Guven seviyesi renk/etiketleri, baslik ve hata gosterimi yardimcilari.
from app.webapp.common import CONFIDENCE_COLORS, CONFIDENCE_LABELS, page_intro, show_error
# get_identity: sadece aktif kimlige ait onay kuyrugu kayitlarini listelemek icin.
from app.webapp.identity import get_identity, render_project_branch_filter
from app.services.audit_action_labels import CONFIRM_ACTION_LABEL, DISMISS_ACTION_LABEL, MASK_ACTION_LABEL
from app.services.learned_decisions import normalize_value, security_scope

_APPROVE_NOTE = (
    "Bu değer proje/sicil/branch bağlamında hassas olarak öğrenildi ve sonraki eşdeğer bulgularda "
    "otomatik gizlenecek. Dosyadaki tüm bekleyen kararlar tamamlanınca güvenlik kontrolleri otomatik yeniden çalışır."
)


# Her supheli bulgunun "toplu islem icin sec" checkbox'inin session_state
# anahtarini uretir.
def _select_key(review_id: int) -> str:
    return f"review_select_{review_id}"


# "Tumunu sec" checkbox'i degistiginde, ekrandaki tum kayitlarin secim
# durumunu ayni degere cekmek icin on_change callback'i olarak kullanilir.
def _toggle_select_all() -> None:
    value = st.session_state.get("review_select_all", False)
    for review_id in st.session_state.get("review_all_ids", []):
        st.session_state[_select_key(review_id)] = value


# Tek bir onay-kuyrugu kaydina onayla/reddet islemini uygular; hata olursa
# yakalayip kullaniciya gosterilecek mesaja cevirir (basari/hata durumunu
# ("ok", "") / ("error", mesaj) olarak dondurur).
def _invalidate_export_download() -> None:
    result = st.session_state.get("export_last_result")
    if result is not None and "download_bytes" in result:
        result["download_bytes"] = None
        result["download_needs_refresh"] = True


def _run_mask_file(review_id: int) -> None:
    try:
        with st.spinner("Riskli ifadeler maskeleniyor ve dosya yeniden doğrulanıyor..."):
            result = api_client.mask_review_file(review_id)
        _invalidate_export_download()
        st.session_state["review_flash"] = ("success" if result.written else "warning", result.message)
    except ApiError as exc:
        st.session_state["review_flash"] = ("warning", exc.message)
    st.rerun()


def _apply_action(action: str, review_id: int) -> tuple[str, str]:
    try:
        if action == "approve":
            api_client.approve_review(review_id)
        else:
            api_client.reject_review(review_id)
        _invalidate_export_download()
        return ("ok", "")
    except ApiError as exc:
        return ("error", exc.message)


# Tek bir satirdaki Onayla/Reddet butonuna basildiginda cagirilir: islemi
# uygular, sonucu "flash" mesaji olarak session_state'e koyup sayfayi yeniler.
def _run_single_action(action: str, review_id: int) -> None:
    status, message = _apply_action(action, review_id)
    if status == "ok":
        flash_kind = "success" if action == "approve" else "info"
        flash_text = (
            "✅ Kayıt onaylandı, değer gizlenecek."
            if action == "approve"
            else "✅ Dar kapsamlı yanlış-alarm suppression kararı kaydedildi."
        )
        if action == "approve":
            flash_text += "\n\n" + _APPROVE_NOTE
        st.session_state["review_flash"] = (flash_kind, flash_text)
    else:
        st.session_state["review_flash"] = ("warning", message)
    st.rerun()


# "Secilenleri Onayla/Reddet" butonuna basildiginda cagirilir: secilen tum
# kayitlara islemi tek tek uygular, kac tanesinin basarili/atlandigini
# ozetleyen bir flash mesaji birakip sayfayi yeniler.
def _run_bulk_action(action: str, review_ids: list[int]) -> None:
    ok_count = 0
    skipped = 0
    for review_id in review_ids:
        status, _message = _apply_action(action, review_id)
        if status == "ok":
            ok_count += 1
        else:
            skipped += 1
        st.session_state.pop(_select_key(review_id), None)

    verb = "onaylandı" if action == "approve" else "reddedildi"
    text = f"{ok_count} kayıt {verb}."
    if skipped:
        text += f" {skipped} kayıt aynı karar kapsamında veya başka bir kullanıcı tarafından zaten sonuçlandırıldı."
    if action == "approve" and ok_count:
        text += "\n\n" + _APPROVE_NOTE
    st.session_state["review_flash"] = ("success" if action == "approve" else "info", text)
    st.rerun()


# _apply_action ile ayni yapida, ama maskeleme denetim uyarilari icin:
# confirm (riski dogrula/karantinada tut) ya da dismiss (yanlis alarm/
# serbest birak) uygular.
def _apply_audit_action(action: str, warning_id: int) -> tuple[str, str]:
    try:
        if action == "confirm":
            api_client.confirm_audit_warning(warning_id)
        elif action == "mask":
            api_client.mask_audit_warning(warning_id)
        else:
            api_client.dismiss_audit_warning(warning_id)
        _invalidate_export_download()
        return ("ok", "")
    except ApiError as exc:
        return ("error", exc.message)


# Denetim uyarisi satirindaki confirm/dismiss butonuna basildiginda
# cagirilir: islemi uygular, duruma gore Turkce bir flash mesaji birakip
# sayfayi yeniler.
def _run_single_audit_action(action: str, warning_id: int) -> None:
    status, message = _apply_audit_action(action, warning_id)
    if status == "ok":
        if action == "confirm":
            flash_text = (
                "🔴 Risk doğrulandı: bu dosya dışa aktarılmış klasöre HÂLÂ kopyalanmadı. Bu proje "
                "için maskeleme kurallarını düzeltip yeniden dışa aktarmanız gerekir."
            )
        elif action == "mask":
            flash_text = "✅ Riskli ifadeler sistem tarafından maskelendi, eşlemeler veritabanına kaydedildi ve dosya çıktıya eklendi."
        else:
            flash_text = "✅ Yanlış alarm kararı kaydedildi; dosya yeniden doğrulanarak çıktıya eklendi."
        st.session_state["audit_flash"] = ("warning" if action == "confirm" else "success", flash_text)
    else:
        st.session_state["audit_flash"] = ("warning", message)
    st.rerun()


# Ekranin ust kismindaki "Maskeleme Denetim Uyarisi (Ikincil Risk)"
# bolumunu cizer: aktif kimlige ait, karantinada bekleyen dosyalari listeler
# ve her biri icin confirm/dismiss butonlarini gosterir. Uyari yoksa hicbir
# sey cizmez (erken doner).
def _render_audit_warnings_section(identity: dict, review_count: int = 0) -> None:
    identity_key = (identity["project_name"], identity["sicil_no"], identity["branch_name"])
    polling = st.session_state.get("audit_revalidation_poll_identity") == identity_key
    st.fragment(run_every="5s" if polling else None)(_render_audit_warnings_panel)(identity, review_count)


def _render_audit_warnings_panel(identity: dict, review_count: int = 0) -> None:
    flash = st.session_state.pop("audit_flash", None)
    if flash:
        kind, text = flash
        getattr(st, kind)(text)

    try:
        revalidation = api_client.revalidate_pending_audit_warnings(
            project_name=identity["project_name"], sicil_no=identity["sicil_no"], branch_name=identity["branch_name"],
        )
        if revalidation.scheduled:
            _invalidate_export_download()
        pending = api_client.list_pending_audit_warnings(
            project_name=identity["project_name"],
            sicil_no=identity["sicil_no"],
            branch_name=identity["branch_name"],
        )
    except ApiError as exc:
        show_error(exc)
        return
    identity_key = (identity["project_name"], identity["sicil_no"], identity["branch_name"])
    previous = st.session_state.get("audit_pending_snapshot")
    current_ids = frozenset(item.id for item in pending)
    if previous is not None and previous[0] == identity_key and previous[1] - current_ids:
        _invalidate_export_download()
    st.session_state["audit_pending_snapshot"] = (identity_key, current_ids)
    active_ids = (frozenset(revalidation.active_ids) | frozenset(
        item.id for item in pending if getattr(item, "revalidating", False)
    )) & current_ids
    was_polling = st.session_state.get("audit_revalidation_poll_identity") == identity_key
    if bool(active_ids) != was_polling:
        st.session_state["audit_revalidation_poll_identity"] = identity_key if active_ids else None
        st.rerun()
    if active_ids:
        st.info("Eski denetim bulguları otomatik yeniden kontrol ediliyor. Sonuçlar burada güncellenecek.")
    pending = [item for item in pending if item.id not in active_ids]
    rows = [
        {
            "id": item.id,
            "file_path": item.file_path,
            "reasoning": item.reasoning,
            "audit_failed": item.audit_failed,
            "run_id": item.run_id,
            "summary": getattr(item, "summary", "") or "Denetim ayrıntısını inceleyin.",
            "location": getattr(item, "location", "") or "Konum bilgisi kaydedilmemiş.",
            "next_step": getattr(item, "next_step", ""),
            "evidence": list(getattr(item, "evidence", []) or []),
            "project": f"{getattr(item, 'project_name', None) or '-'} / {getattr(item, 'branch_name', None) or '-'}",
        }
        for item in pending
    ]

    if not rows:
        validation_rows = []
        quarantine_rows = []
    else:
        validation_rows = [row for row in rows if row["audit_failed"]]
        quarantine_rows = [
            row for row in rows
            if not row["audit_failed"] and not row["reasoning"].startswith("INCELEME_GEREKLI:")
        ]

    ready_count = 0
    try:
        latest = next((run for run in api_client.list_runs(
            project_name=identity["project_name"], sicil_no=identity["sicil_no"],
            branch_name=identity["branch_name"], limit=10,
        ) if run.operation_type == "mask"), None)
        if latest is not None:
            audit = api_client.get_run_audit_entries(latest.run_id)
            final_states: dict[str, str] = {}
            for event in audit:
                detail = event.detail or ""
                if "final_state=READY" in detail or "final_output=written" in detail:
                    final_states[event.file_path] = "ready"
                if (
                    "final_output=blocked" in detail
                    or event.action in {"error", "sozdizimi_hatasi", "round_trip_hatasi"}
                ) and event.file_path:
                    final_states[event.file_path] = "blocked"
            ready_count = sum(state == "ready" for state in final_states.values())
    except (ApiError, AttributeError, StopIteration):
        pass

    st.info(
        f"✓ Hazır: {ready_count}  |  ⚠ İnceleme Gerekli: {review_count}  |  "
        f"⛔ Güvenlik Karantinası: {len(quarantine_rows)}  |  "
        f"✕ Doğrulama Başarısız: {len(validation_rows)}  |  ⟳ Yeniden doğrulanıyor: {len(active_ids)}"
    )

    if validation_rows:
        st.markdown("### ✕ Doğrulama Başarısız")
        st.error("Bu teknik hatalar kullanıcı kararı beklemez. Dosyalar çıktıya alınmadı; proje yeniden çalıştırılmalıdır.")
        st.dataframe([
            {"İşlem": row["run_id"], "Dosya": row["file_path"], "Teknik sorun": row["summary"]}
            for row in validation_rows
        ], hide_index=True, width="stretch")

    if not quarantine_rows:
        if validation_rows:
            st.divider()
        return

    st.markdown("### ⛔ Güvenlik Karantinası")
    st.warning(
        "Bu dosyalar maskelendikten sonra yapılan son kontrolde gizlenmemiş bilgi içeriyor olabilir. "
        "Siz karar verene kadar çıktıya eklenmedi."
    )

    for row in quarantine_rows:
        file_name = row["file_path"].replace("\\", "/").rsplit("/", 1)[-1]
        with st.expander(f"📄 {file_name} — İncele ve karar ver"):
            header_col, badge_col = st.columns([4, 1.4])
            with header_col:
                st.markdown(f"**📄 {file_name}**")
                st.caption(f"{row['project']} · {row['file_path']} · İşlem #{row['run_id']}")
            with badge_col:
                if row["audit_failed"]:
                    st.badge("Denetim Yapılamadı", color="orange")
                else:
                    st.badge("İnceleme gerekli", color="red")

            st.markdown("**Ne bulundu?**")
            st.write(row["summary"])
            values_with_lines: dict[str, dict] = {}
            for evidence in row["evidence"]:
                value = getattr(evidence, "found_value", "")
                if not value:
                    continue
                item = values_with_lines.setdefault(
                    value, {"label": getattr(evidence, "label", "") or "Hassas bilgi", "lines": []},
                )
                if getattr(evidence, "line", None):
                    item["lines"].append(str(evidence.line))
            for value, item in values_with_lines.items():
                where = f" — satır {', '.join(dict.fromkeys(item['lines']))}" if item["lines"] else ""
                st.markdown(f"- **{item['label']}:** `{value}`{where}")
            if row["evidence"]:
                with st.expander("Dosyada nerede geçtiğini göster"):
                    for evidence in row["evidence"]:
                        line = getattr(evidence, "line", None)
                        st.caption(f"Satır {line}" if line else "Dosya geneli")
                        excerpt = getattr(evidence, "excerpt", "")
                        if excerpt:
                            st.code(excerpt, language=None)

            st.markdown("**Ne yapmalıyım?**")
            if row["next_step"]:
                st.info(row["next_step"])
            st.markdown(
                f"- **{MASK_ACTION_LABEL}:** Bulunan değerleri sistem gizler, dosyayı yeniden kontrol edip çıktıya ekler.\n"
                f"- **{DISMISS_ACTION_LABEL}:** Bunlar gerçek veri değilse seçin. Dosya yeniden kontrol edilip çıktıya eklenir.\n"
                f"- **{CONFIRM_ACTION_LABEL}:** Dosya dışarı çıkmaz. Maskeleme kurallarını düzeltip projeyi yeniden dışa aktarmanız gerekir."
            )
            action_col1, action_col2, action_col3 = st.columns(3)
            with action_col1:
                if st.button(MASK_ACTION_LABEL, key=f"audit_mask_{row['id']}", type="primary", width="stretch",
                             disabled=not values_with_lines):
                    _run_single_audit_action("mask", row["id"])
            with action_col2:
                if st.button(DISMISS_ACTION_LABEL, key=f"audit_dismiss_{row['id']}", width="stretch"):
                    _run_single_audit_action("dismiss", row["id"])
            with action_col3:
                if st.button(CONFIRM_ACTION_LABEL, key=f"audit_confirm_{row['id']}", width="stretch"):
                    _run_single_audit_action("confirm", row["id"])
            with st.popover("Teknik ayrıntı"):
                st.text(row["reasoning"])

    st.divider()


# Ekranin giris noktasi: once denetim uyarilari bolumunu, sonra normal
# supheli bulgu onay kuyrugunu (tekli/coklu onayla-reddet aksiyonlariyla)
# cizer.
def render() -> None:
    page_intro(
        "🕵️ Onay Bekleyenler",
        "Önce dışa aktarımı durdurulan dosyaları, ardından maskelenmesi için onay bekleyen bulguları inceleyin. "
        "Sicilinizle yaptığınız tüm proje ve branch'lerdeki kayıtlar listelenir.",
    )
    # Kullanici sicille tanimlanir; proje/branch bu ekranda yalnizca filtredir
    # (None = tumu). Asagidaki yardimcilar bu kapsami "identity" olarak alir.
    project_name, branch_name = render_project_branch_filter("review_")
    identity = {**get_identity(), "project_name": project_name, "branch_name": branch_name}

    flash = st.session_state.pop("review_flash", None)
    if flash:
        kind, text = flash
        getattr(st, kind)(text)

    try:
        pending = api_client.list_pending_reviews(
            project_name=identity["project_name"],
            sicil_no=identity["sicil_no"],
            branch_name=identity["branch_name"],
        )
    except ApiError as exc:
        show_error(exc)
        return
    rows = [
        {
            "id": item.id,
            "run_id": getattr(item, "run_id", None),
            "file_path": item.file_path,
            "line_number": item.line_number,
            "found_value": item.found_value,
            "entity_type": item.entity_type,
            "confidence_level": item.confidence_level,
            "reason": item.reason,
            "surrounding_context": item.surrounding_context,
            "project": f"{getattr(item, 'project_name', None) or '-'} / {getattr(item, 'branch_name', None) or '-'}",
        }
        for item in pending
    ]

    # Ayni deger farkli projelerde ayri karardir: tek tikla baska bir
    # projedeki bulgu da onaylanmasin diye proje/branch grubun parcasidir.
    grouped: dict[tuple[str, str, str, str, str], list[dict]] = {}
    for row in rows:
        if not row["found_value"]:
            continue
        key = (
            row["project"], normalize_value(row["found_value"]), row["entity_type"],
            row["confidence_level"], security_scope(row["file_path"]),
        )
        grouped.setdefault(key, []).append(row)

    _render_audit_warnings_section(identity, review_count=len(grouped))

    st.markdown("### ⚠ İnceleme Gerekli")
    if not grouped:
        st.success("Şu anda onay bekleyen şüpheli bulgu yok.")
        return

    st.caption(f"{len(grouped)} eşdeğer bulgu grubu · {sum(map(len, grouped.values()))} toplam kullanım")
    for group_index, group_rows in enumerate(grouped.values()):
        row = group_rows[0]
        ids = [item["id"] for item in group_rows]
        file_count = len({item["file_path"] for item in group_rows})
        with st.container(border=True):
            header_col, badge_col = st.columns([4, 1.2])
            with header_col:
                st.markdown(f"**{row['found_value']}**")
                st.caption(f"{row['project']} · {file_count} dosya / {len(group_rows)} kullanım")
            with badge_col:
                level = row["confidence_level"]
                st.badge(
                    f"AI güveni: {CONFIDENCE_LABELS.get(level, level)}",
                    color=CONFIDENCE_COLORS.get(level, "gray"),
                )

            st.write(f"**Risk:** {row['entity_type']}")
            st.write(f"**Neden:** {row['reason'] or 'Model bir gerekçe belirtmedi.'}")
            if row["surrounding_context"]:
                st.caption("Örnek bağlam")
                st.code(row["surrounding_context"], language=None)

            with st.expander("Detayları İncele"):
                detail_rows = [{
                    "Dosya": item["file_path"], "Satır": item["line_number"] or "—",
                    "AI güveni": CONFIDENCE_LABELS.get(item["confidence_level"], item["confidence_level"]),
                    "Bağlam": item["surrounding_context"] or "—",
                } for item in group_rows]
                st.dataframe(detail_rows, hide_index=True, width="stretch")

            action_col1, action_col2, action_col3 = st.columns(3)
            with action_col1:
                if st.button("Tümünü Gizle", key=f"approve_group_{group_index}", type="primary", width="stretch"):
                    _run_bulk_action("approve", ids)
            with action_col2:
                with st.popover("Düzenle", width="stretch"):
                    st.caption("Dosyayı seçin. Sistem o dosyadaki bekleyen riskli ifadeleri otomatik maskeler, eşlemeleri kaydeder ve dosyayı yeniden doğrular.")
                    files = {}
                    for item in group_rows:
                        files.setdefault((item["run_id"], item["file_path"]), item)
                    for item in files.values():
                        if st.button(f"{item['file_path']} — Maskele", key=f"mask_file_{group_index}_{item['id']}", width="stretch"):
                            _run_mask_file(item["id"])
            with action_col3:
                if st.button("Yanlış Alarm", key=f"reject_group_{group_index}", width="stretch"):
                    _run_bulk_action("reject", ids)
