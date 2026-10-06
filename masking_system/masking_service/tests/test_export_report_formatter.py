"""Asama 2 / Adim 3a: exporter.py'den ayrilan format_export_report()'un,
ExportReport.summary_text()'in eski (tek sinif icindeki) haliyle
uretti8gi metinle BIREBIR ayni ciktiyi urettigini karakterize eden testler.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.services.export_report_formatter import format_export_report
from app.services.exporter import ExportReport, FileOutcome


def _base_report(**overrides) -> ExportReport:
    defaults = dict(
        run_id=1,
        context_id=1,
        project_name="Poseidon",
        sicil_no="EMP-1001",
        branch_name="main",
        source_path="/src",
        target_path="/dst",
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        status="completed",
    )
    defaults.update(overrides)
    return ExportReport(**defaults)


def test_clean_report_has_no_attention_section():
    report = _base_report()
    report.record(FileOutcome("a.py", status="copied_text_no_match"))
    report.record(FileOutcome("b.py", status="masked", match_count=2, rule_breakdown={"email_address": 2}))

    text = format_export_report(report)

    assert "run_id=1 durum=Basarili" in text
    assert "2 dosya tarandi -> 1 dosyada hassas veri bulunup maskelendi" in text
    assert "E-posta adresi: 2" in text
    assert "Sorun yok" in text
    assert "Dikkat edilmesi gerekenler" not in text


def test_summary_text_delegates_to_formatter_with_identical_output():
    report = _base_report()
    report.record(FileOutcome("a.py", status="masked", match_count=1, rule_breakdown={"aws_access_key": 1}))

    assert report.summary_text() == format_export_report(report)


def test_quarantined_and_syntax_and_round_trip_failures_are_all_reported():
    report = _base_report(status="completed_with_warnings", target_overwritten=True)
    # Faz 2a (kural 7): rapor dosyalari kaynak yolla degil "maskeli yol#kimlik"
    # etiketiyle yazar; etiket export'ta yol planlamasinda uretilir.
    report.file_labels.update({"broken.py": "broken.py#0123456789ab", "mismatch.py": "mismatch.py#ba9876543210"})
    report.record(
        FileOutcome("secret.py", status="quarantined_pending_audit", match_count=1, error="ikincil risk")
    )
    report.record(
        FileOutcome("broken.py", status="failed_syntax_validation", match_count=1, error="Python sozdizimi hatasi")
    )
    report.record(
        FileOutcome("mismatch.py", status="failed_round_trip_validation", match_count=1, error="ilk fark 10")
    )
    report.record(FileOutcome("skipped.bin", status="skipped_symlink"))

    text = format_export_report(report)

    assert "onceki export uzerine yazildi" in text
    assert "IKINCIL RISK" in text
    assert "SOZDIZIMI HATASI" in text
    assert "broken.py#0123456789ab dosyasinda maskeleme sozdizimini bozdu" in text
    assert "ROUND-TRIP HATASI" in text
    assert "mismatch.py#ba9876543210: ilk fark 10" in text
    assert "1 dosya symlink oldugu icin atlandi" in text
    assert "Bilgi notlari (kapsam disi dosyalar)" in text


def test_unknown_rule_name_falls_back_to_raw_name():
    report = _base_report()
    report.record(FileOutcome("x.py", status="masked", match_count=1, rule_breakdown={"llm:CUSTOM_TYPE": 1}))

    text = format_export_report(report)

    assert "llm:CUSTOM_TYPE: 1" in text


def test_four_security_states_are_counted_separately():
    report = _base_report(status="completed_with_warnings")
    report.record(FileOutcome("ready.py", status="masked"))
    report.record(FileOutcome(
        "review.py", status="quarantined_pending_audit", final_state="REVIEW_REQUIRED"
    ))
    report.record(FileOutcome(
        "quarantine.py", status="quarantined_pending_audit", final_state="SECURITY_QUARANTINE"
    ))
    report.record(FileOutcome(
        "timeout.py", status="quarantined_pending_audit", final_state="VALIDATION_FAILED"
    ))

    assert report.files_ready == 1
    assert report.files_review_required == 1
    assert report.files_security_quarantine == 1
    assert report.files_validation_failed == 1
    assert (
        "✓ Hazır 1 | ⚠ İnceleme Gerekli 1 | ⛔ Güvenlik Karantinası 1 | "
        "✕ Doğrulama Başarısız 1"
    ) in report.summary_text()


def test_report_never_falls_back_to_source_paths():
    report = _base_report(source_path="/home/u/karayel-kaynak", target_path="/out/karayel-cikti")
    report.record(FileOutcome("src/karayel/X.java", status="failed_round_trip_validation", error="ilk fark 1"))

    text = format_export_report(report)

    assert "karayel" not in text.casefold()
    assert "Kaynak: <gizlendi>" in text and "<dosya?>: ilk fark 1" in text
    # Baslik kimlik degerleri de maskeli haline duser, ham deger yazilmaz.
    assert "Poseidon" not in text and "EMP-1001" not in text
    assert "Proje: <gizlendi> | sicil: <gizlendi> | Branch: <gizlendi>" in text


def test_report_uses_masked_roots_and_labels():
    report = _base_report(display_source_path="/home/u/mask_kurumsal_terim_1", display_target_path="/out/x",
                          display_project_name="mask_proje_adi_1", display_sicil_no="mask_sicil_no_1",
                          display_branch_name="main")
    report.file_labels["src/a.py"] = "src/a.py#00aa11bb22cc"
    report.record(FileOutcome("src/a.py", status="failed_round_trip_validation", error="ilk fark 1"))

    text = format_export_report(report)

    assert "Kaynak: /home/u/mask_kurumsal_terim_1" in text
    assert "Hedef:  /out/x" in text
    assert "Proje: mask_proje_adi_1 | sicil: mask_sicil_no_1 | Branch: main" in text
    assert "src/a.py#00aa11bb22cc: ilk fark 1" in text


def test_coverage_notices_are_info_not_warnings():
    # "parser yok" / yalnizca bracket-quote / .class kapsam notlari bilgi amaclidir:
    # rapor bunlari ayri gosterir, "Sorun yok" ozetini ve durumu bozmaz.
    from app.services.java_classfile import CLASS_COVERAGE
    from app.services.syntax_validator import BRACKET_ONLY_NOTICE, STRUCTURAL_ONLY_NOTICE

    report = _base_report()
    report.record(FileOutcome("README.md", status="masked", match_count=1, rule_breakdown={"email_address": 1}))
    report.validation_warnings.extend([
        f"README.md: validation_mode=structural-only; {STRUCTURAL_ONLY_NOTICE}",
        f"A.java: validation_mode=bracket/quote-based; {BRACKET_ONLY_NOTICE}",
        f"B.class: {CLASS_COVERAGE}",
    ])

    assert report.actionable_validation_warnings == []
    assert len(report.validation_notices) == 3
    text = format_export_report(report)
    assert "SOZDIZIMI DOGRULAMA UYARILARI" not in text
    assert "Bilgi notlari (dogrulama kapsami" in text
    assert "1 dosya düz metin olduğu için ayrıca biçim denetimi yapılmadı" in text and "README.md." in text
    assert "Sorun yok" in text


def test_non_coverage_validation_notice_stays_a_warning():
    report = _base_report()
    report.validation_warnings.extend([
        "a.json: validation_mode=parser-based; Kaynak dosya zaten parser hatasi iceriyor; yeni bozulma olmadigi garanti edilemez.",
        "b.py: sozdizimi hatasi uyariyla ciktiya alindi: satir 3",
    ])

    assert report.validation_notices == []
    assert len(report.actionable_validation_warnings) == 2
    text = format_export_report(report)
    assert "SOZDIZIMI DOGRULAMA UYARILARI" in text
    assert "Sorun yok" not in text


def test_notice_summary_shows_original_names_on_screen_but_report_stays_masked():
    from app.services.syntax_validator import STRUCTURAL_ONLY_NOTICE

    report = _base_report()
    original = "Cumhurbaskanligi_Backend/templates/cumhurbaskanligi_cevap.txt"
    label = "mask_kurumsal_ifade_1_Backend/templates/mask_kurumsal_ifade_3_cevap.txt#3a072a37ae08"
    report.file_labels[original] = label
    report.validation_warnings.append(f"{label}: validation_mode=structural-only; {STRUCTURAL_ONLY_NOTICE}")

    assert report.validation_notice_summary == [
        "1 dosya düz metin olduğu için ayrıca biçim denetimi yapılmadı; içerik ve geri dönüş "
        "kontrolleri tamam: cumhurbaskanligi_cevap.txt."
    ]
    text = format_export_report(report)
    assert "mask_kurumsal_ifade_3_cevap.txt." in text and "cumhurbaskanligi" not in text
