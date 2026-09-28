from datetime import datetime
from types import SimpleNamespace

from app.webapp.audit_presentation import present_audit_entries, summarize_audit_entries
from app.webapp.common import error_next_step


def _entry(file_path: str, action: str, detail: str):
    return SimpleNamespace(
        file_path=file_path,
        action=action,
        detail=detail,
        created_at=datetime(2026, 8, 14, 14, 17, 21),
    )


def test_file_summary_collapses_many_events_into_one_row():
    entries = [
        _entry(
            "sql/kurum_ici_sorgu.sql",
            "skipped",
            "scan_policy mode=mask presidio=True llm=True",
        ),
        _entry("sql/kurum_ici_sorgu.sql", "matched", "rule=kurumsal_terim_i_denetim_c097f4235ba4"),
        _entry("sql/kurum_ici_sorgu.sql", "replaced", "placeholder=mask_i_denetim_13"),
        _entry("sql/kurum_ici_sorgu.sql", "sinir_ihlali", "token siniri guvenli degil"),
    ]

    assert summarize_audit_entries(entries) == [
        {
            "Dosya": "sql/kurum_ici_sorgu.sql",
            "Tarama": "Metin taraması",
            "Bulgu": 1,
            "Maskelenen": 1,
            "Güvenlik notu": 1,
            "Hata": 0,
            "Sonuç": "Maskelendi",
        }
    ]


def test_technical_trace_hides_rule_hash_and_explains_events():
    entries = [
        _entry("README.md", "matched", "rule=kurumsal_terim_i_denetim_c097f4235ba4"),
        _entry("README.md", "replaced", "placeholder=mask_i_denetim_13"),
        _entry(
            "README.md",
            "cakisma",
            "cakisma: [tip=IC_DOMAIN_ADI kaynak=katman2_presidio guven=yuksek offset=(1,2)] "
            "bulgusu, [tip=i_denetim kaynak=dictionary guven=yuksek offset=(1,4)] ile ortustugu icin uygulanmadi",
        ),
    ]

    rows = present_audit_entries(entries)
    rendered = str(rows)
    assert "Kurumsal sözlük — I Denetim" in rendered
    assert "geri alınabilir bir yer tutucuyla" in rendered
    assert "Metin iki kez maskelenmedi" in rendered
    assert "c097f4235ba4" not in rendered
    assert "offset=" not in rendered


def test_error_next_step_is_specific_for_missing_download_and_backend_connection():
    missing = error_next_step(404, "Bu çalışma için indirilebilir bir çıktı bulunamadı.")
    disconnected = error_next_step(0, "Backend API'sine bağlanılamadı.")

    assert "karantina" in missing
    assert "yeniden dışa aktar" in missing
    assert "WEB_API_BASE_URL" in disconnected


def test_scan_policy_trace_describes_enabled_layers():
    rows = present_audit_entries([
        _entry("README.md", "skipped", "scan_policy mode=mask presidio=True llm=True"),
    ])
    assert rows[0]["Seviye"] == "Bilgi"
    assert rows[0]["Açıklama"] == "Metin taraması. Etkin katmanlar: regex/sözlük, Presidio, yapay zekâ."
