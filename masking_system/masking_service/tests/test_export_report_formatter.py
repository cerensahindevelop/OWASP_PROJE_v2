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
    assert "Dikkat edilmesi gerekenler" in text


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


def test_report_uses_masked_roots_and_labels():
    report = _base_report(display_source_path="/home/u/mask_kurumsal_terim_1", display_target_path="/out/x")
    report.file_labels["src/a.py"] = "src/a.py#00aa11bb22cc"
    report.record(FileOutcome("src/a.py", status="failed_round_trip_validation", error="ilk fark 1"))

    text = format_export_report(report)

    assert "Kaynak: /home/u/mask_kurumsal_terim_1" in text
    assert "Hedef:  /out/x" in text
    assert "src/a.py#00aa11bb22cc: ilk fark 1" in text
