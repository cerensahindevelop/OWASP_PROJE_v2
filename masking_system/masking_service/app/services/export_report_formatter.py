"""ExportReport'u insan-okunur, Turkce ozet metnine ceviren saf sunum
katmani (Asama 2 / Adim 3a).

exporter.py'nin (803 satir) hem toplama/veri sorumlulugunu (ExportReport.
record(), has_*) hem de ~110 satirlik metin uretimini tek sinifta tasimasi
SRP ihlaliydi - bu modul o metin uretimini ayirir. SAF "extract method"
refaktorudur: davranis (uretilen metnin BIREBIR ayni olmasi) korunur,
ExportReport.summary_text() geriye-uyumlu ince bir sarmalayici olarak kalir.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.failed_checks import failed_check_label

if TYPE_CHECKING:
    from app.services.exporter import ExportReport

# Kural adlarinin (filter_rules.rule_name) rapor ciktisinda gosterilecek
# insan-okunur karsiligi. Eslesmesi olmayan (orn. sonradan kural-ekle ile
# eklenmis) bir rule_name oldugu gibi gosterilir - bu bir hata degildir.
_RULE_DISPLAY_NAMES = {
    "project_name": "Proje adi",
    "sicil_no": "Sicil numarasi",
    "branch_name": "Branch adi",
    "aws_access_key": "AWS erisim anahtari",
    "generic_secret_assignment": "Genel gizli bilgi (API anahtari/parola/token)",
    "email_address": "E-posta adresi",
    "ipv4_address": "IP adresi",
}

_STATUS_DISPLAY_NAMES = {
    "completed": "Basarili",
    "completed_with_warnings": "Uyarili tamamlandi",
    "failed": "Basarisiz",
    "in_progress": "Devam ediyor",
}


# Raporu insan tarafindan okunabilir, Turkce ozet metnine cevirir (CLI ciktisi).
# Once duz cumlelik bir baslik/ozet, sonra (varsa) kural dagilimi, en sonda da
# SADECE dikkat gerektiren (sifirdan farkli) durumlar - 8 satirlik "hepsi 0"
# dokumu okunurlugu bogar, o yuzden burada gosterilmez.
def format_export_report(report: "ExportReport") -> str:
    status_label = _STATUS_DISPLAY_NAMES.get(report.status, report.status)
    lines = [
        f"Export raporu - run_id={report.run_id} durum={status_label}",
        f"  Proje: {report.project_name} | sicil: {report.sicil_no} | Branch: {report.branch_name}",
        f"  Kaynak: {report.source_path}",
        f"  Hedef:  {report.target_path}"
        + (" (onceki export uzerine yazildi)" if report.target_overwritten else ""),
        "",
    ]

    headline = (
        f"  {report.files_scanned} dosya tarandi -> "
        f"{report.files_masked} dosyada hassas veri bulunup maskelendi"
    )
    if report.files_copied_text_no_match:
        headline += f", {report.files_copied_text_no_match} dosyada eslesme bulunamadi"
    if report.files_copied_binary:
        headline += f", {report.files_copied_binary} binary dosya dogrulanamadi ve ciktiya alinmadi"
    if report.files_skipped_unsupported:
        headline += f", {report.files_skipped_unsupported} dosya desteklenmeyen icerik nedeniyle atlandi (ciktiya alinmadi)"
    if report.files_scan_only_clean:
        headline += f", {report.files_scan_only_clean} bagimlilik lock dosyasi tarandi ve degismeden kopyalandi"
    if report.files_scan_only_sensitive:
        headline += (
            f", {report.files_scan_only_sensitive} lock dosyasinda hassas olabilecek icerik bulundu "
            "ve DISA AKTARILMADI"
        )
    if report.files_archive_unsupported:
        headline += f", {report.files_archive_unsupported} arsiv dosyasi taranamadigi icin DISA AKTARILMADI"
    if report.files_failed_detection:
        headline += f", {report.files_failed_detection} dosyada tarama guvenilir sekilde tamamlanamadi"
    if report.files_quarantined_pending_audit:
        headline += (
            f", {report.files_quarantined_pending_audit} dosya ikincil risk denetiminden "
            "gecemedigi icin DISA AKTARILMADI"
        )
    if report.files_failed_syntax_validation:
        headline += (
            f", {report.files_failed_syntax_validation} dosya maskeleme sozdizimini bozdugu "
            "icin DISA AKTARILMADI"
        )
    if report.files_failed_round_trip_validation:
        headline += (
            f", {report.files_failed_round_trip_validation} dosya round-trip (geri-cozum) "
            "dogrulamasindan gecemedigi icin DISA AKTARILMADI"
        )
    if report.files_failed_consistency_validation:
        headline += (
            f", {report.files_failed_consistency_validation} dosya final consistency "
            "dogrulamasindan gecemedigi icin DISA AKTARILMADI"
        )
    if report.files_failed_finalization:
        headline += (
            f", {report.files_failed_finalization} dosya sonlandirma (izin/butunluk kaydi) "
            "adiminda basarisiz oldugu icin DISA AKTARILMADI"
        )
    lines.append(headline + ".")
    lines.append(
        "  Durumlar: "
        f"✓ Hazır {report.files_ready} | "
        f"⚠ İnceleme Gerekli {report.files_review_required} | "
        f"⛔ Güvenlik Karantinası {report.files_security_quarantine} | "
        f"✕ Doğrulama Başarısız {report.files_validation_failed}"
    )
    blocked_by_check = report.blocked_by_check
    if blocked_by_check:
        lines.append("  Ciktiya alinmama nedenleri (dosyayi alikoyan kontrol):")
        lines.extend(
            f"    - {failed_check_label(code)} ({code}): {count}" for code, count in blocked_by_check.items()
        )

    if report.degraded_detectors:
        lines.append("")
        lines.append(
            "  !!! TESPIT KAPASITESI DUSUK: su detector katman(lar)i bu calisma boyunca "
            f"fallback modda calisti, kapsam eksik olabilir: {', '.join(report.degraded_detectors)} !!!"
        )

    if report.llm_disabled:
        lines.append("")
        lines.append(
            "  (i) BILGI: LLM (Katman 3) bu calisma icin KAPALI (VLLM_ENABLED=false) - "
            "sadece kural/sozluk (Katman 1) ve Presidio (Katman 2) taramasi yapildi."
        )

    if report.validation_warnings:
        lines.append("")
        lines.append("  SOZDIZIMI DOGRULAMA UYARILARI:")
        lines.extend(f"    {notice}" for notice in report.validation_warnings)

    if report.matches_by_rule:
        lines.append("")
        lines.append(f"  Bulunan hassas veri turleri (toplam {report.total_matches} eslesme):")
        for rule_name, count in sorted(report.matches_by_rule.items(), key=lambda kv: -kv[1]):
            display_name = _RULE_DISPLAY_NAMES.get(rule_name, rule_name)
            lines.append(f"    - {display_name}: {count}")

    if report.files_quarantined_pending_audit:
        lines.append("")
        lines.append(
            f"  !!! IKINCIL RISK: {report.files_quarantined_pending_audit} dosya, maskeleme "
            "sonrasi bagimsiz denetimden gecemedigi icin hedef klasore YAZILMADI - insan "
            "onayi bekliyor (bkz. denetim_uyarilari) !!!"
        )

    if report.files_failed_syntax_validation:
        lines.append("")
        lines.append(
            f"  !!! SOZDIZIMI HATASI: {report.files_failed_syntax_validation} dosya, maskeleme "
            "sozdizimini bozdu ve DISA AKTARILMADI - basarisiz_dosyalar/ klasorune tasindi, "
            "manuel incelemeniz gerekiyor !!!"
        )
        for outcome in report.outcomes:
            if outcome.status == "failed_syntax_validation":
                lines.append(
                    f"    - {outcome.relative_path} dosyasinda maskeleme sozdizimini bozdu, "
                    "bu dosya disa aktarilmadi, manuel incelemeniz gerekiyor."
                )

    if report.files_failed_round_trip_validation:
        lines.append("")
        lines.append(
            f"  !!! ROUND-TRIP HATASI: {report.files_failed_round_trip_validation} dosyada "
            "maskelenmis metin, az once olusturulan mapping'lerle geri cozulunce ORIJINALIYLE "
            "UYUSMADI (restore garanti edilemiyor) - DISA AKTARILMADI, basarisiz_dosyalar/ "
            "klasorune tasindi, manuel incelemeniz gerekiyor !!!"
        )
        for outcome in report.outcomes:
            if outcome.status == "failed_round_trip_validation":
                lines.append(f"    - {outcome.relative_path}: {outcome.error}")

    if report.files_failed_consistency_validation:
        lines.append("")
        lines.append(
            f"  !!! CONSISTENCY HATASI: {report.files_failed_consistency_validation} dosyada "
            "ilk turda dogrulanmis hassas bir degerin acik kalmadigi garanti edilemedi - "
            "dosya DISA AKTARILMADI ve basarisiz_dosyalar/ klasorune tasindi !!!"
        )
        for outcome in report.outcomes:
            if outcome.status == "failed_consistency_validation":
                lines.append(f"    - {outcome.relative_path}: {outcome.error}")

    if report.files_failed_finalization:
        lines.append("")
        lines.append(
            f"  !!! SONLANDIRMA HATASI: {report.files_failed_finalization} dosya, tum icerik "
            "dogrulamalarindan gecmesine ragmen sonlandirma (izin/butunluk kaydi) adiminda "
            "basarisiz oldu - hedef paketten kaldirildi !!!"
        )
        for outcome in report.outcomes:
            if outcome.status == "failed_finalization":
                lines.append(f"    - {outcome.relative_path}: {outcome.error}")

    attention: list[str] = []
    if report.files_skipped_unsupported:
        attention.append(
            f"{report.files_skipped_unsupported} dosyada maskeleme desteklenmiyor; "
            "dosyalar taranmadi ve ciktiya alinmadi"
        )
        for outcome in report.outcomes:
            if outcome.status == "skipped_unsupported":
                attention.append(f"{outcome.relative_path}: {outcome.error}")
    if report.files_copied_binary:
        attention.append(f"{report.files_copied_binary} binary dosya dogrulanamadi ve ciktiya alinmadi")
    if report.files_skipped_symlink:
        attention.append(f"{report.files_skipped_symlink} dosya symlink oldugu icin atlandi")
    if report.files_skipped_too_large:
        attention.append(
            f"{report.files_skipped_too_large} dosya boyut esigini astigi icin dogrulanamadi ve kopyalanmadi"
        )
    if report.files_copied_undecodable:
        attention.append(
            f"{report.files_copied_undecodable} dosya decode edilemedi, manuel inceleme gerekli"
        )
    if report.files_excluded:
        attention.append(
            f"{report.files_excluded} dosya guvenlik politikasi geregi haric tutuldu (hic kopyalanmadi)"
        )
    if report.files_errored:
        attention.append(f"{report.files_errored} dosya hata aldi")
    if report.files_scan_only_sensitive:
        attention.append(
            f"{report.files_scan_only_sensitive} bağımlılık/lock dosyasında hassas olabilecek içerik "
            "bulundu; bütünlüğü bozulmasın diye maskelenmedi, çıktıya alınmadı (bkz. denetim_uyarilari)"
        )
    if report.files_archive_unsupported:
        attention.append(
            f"{report.files_archive_unsupported} arşiv dosyası bu sürümde taranamadı; çıktıya alınmadı "
            "(bkz. denetim_uyarilari)"
        )
    if report.files_failed_detection:
        attention.append(
            f"{report.files_failed_detection} dosyada tarama katmanı güvenilir bir sonuç üretemedi; "
            "güvenlik gereği çıktıya alınmadı (bkz. denetim_uyarilari)"
        )

    lines.append("")
    if attention:
        lines.append("  Dikkat edilmesi gerekenler:")
        for item in attention:
            lines.append(f"    - {item}")
    elif (
        not report.files_quarantined_pending_audit
        and not report.files_failed_syntax_validation
        and not report.files_failed_round_trip_validation
        and not report.files_failed_consistency_validation
        and not report.files_failed_finalization
        and not report.validation_warnings
        and not report.degraded_detectors
    ):
        lines.append("  Sorun yok: hata, atlanan symlink, boyut asimi ya da haric tutma yasanmadi.")

    return "\n".join(lines)
