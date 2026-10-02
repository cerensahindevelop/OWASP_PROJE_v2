from types import SimpleNamespace

from app.services.audit_warning_details import describe_audit_warning
from app.services.term_upload import commit_term_upload


def warning(reasoning, masked_content="", audit_failed=False):
    return SimpleNamespace(reasoning=reasoning, masked_content=masked_content, audit_failed=audit_failed)


def test_saved_term_locations_need_no_rescan():
    detail = describe_audit_warning(warning(
        "Kurumsal terim kontrolü: 1 açık eşleşme kaldı.\nSatır 9, sütun 20: proje_adi (kurumsal_terim_hash)"
    ), None)
    assert detail["location"] == "Satır 9, sütun 20"
    assert "proje adi" in detail["summary"]
    assert "kurumsal_terim_hash" not in detail["summary"]


def test_legacy_warning_gets_verified_locations_without_exposing_content(db_session):
    commit_term_upload(db_session, filename="terms.txt", content=b"Poseidon\n", category="pytest_location")
    detail = describe_audit_warning(warning(
        "Kurumsal terim sozlugu son kontrolu basarisiz: eski mesaj",
        "SELECT *\nFROM PUBLIC.T_Poseidon;\n",
    ), db_session)
    assert "Satır 2, sütun 15" in detail["location"]
    assert "güncel sözlük" in detail["location"]
    assert "Poseidon" not in detail["summary"]
    assert detail["evidence"][0]["found_value"] == "Poseidon"
    assert "⟦Poseidon⟧" in detail["evidence"][0]["excerpt"]


def test_legacy_false_alarm_does_not_claim_an_open_leak(db_session):
    detail = describe_audit_warning(warning(
        "Kurumsal terim sozlugu son kontrolu basarisiz: eski mesaj", "mask_proje_1"
    ), db_session)
    assert "açık terim bulunmadı" in detail["summary"]
    assert "yeniden dışa aktarın" in detail["next_step"]


def test_audit_failure_is_distinct_from_an_actual_finding():
    detail = describe_audit_warning(warning("timeout", audit_failed=True), None)
    assert "tamamlanamadı" in detail["summary"]
    assert "Dosya geneli" in detail["location"]


def test_syntax_failure_exposes_only_parser_and_position():
    detail = describe_audit_warning(warning(
        "Sözdizimi doğrulaması başarısız: JSON sozdizimi hatasi: secret-value (satir 8, sutun 12)",
        audit_failed=True,
    ), None)
    assert "JSON" in detail["summary"]
    assert detail["location"] == "Maskelenmiş dosyada satır 8, sütun 12."
    assert "secret-value" not in str(detail)


def test_roundtrip_failure_exposes_difference_position_without_source():
    detail = describe_audit_warning(warning(
        "Round-trip doğrulaması başarısız: geri cozulmus metin orijinaliyle uyusmuyor: "
        "ilk fark orijinal dosyada satir 8, sutun 12 konumunda, orijinal uzunluk=100, geri cozulmus uzunluk=99",
        audit_failed=True,
    ), None)
    assert "birebir" in detail["summary"]
    assert "satır 8" in detail["location"]
    assert "sütun 12" in detail["location"]


def test_llm_location_is_verified_against_actual_content():
    detail = describe_audit_warning(warning(
        "Olası kurum adı (ilgili bolum: 'secret-section')", "first\nsecret-section\nsecret-section"
    ), None)
    assert detail["summary"] == "Bu dosyada gizlenmemiş görünen 1 bilgi var: kurum adı."
    assert detail["location"] == "2 satırda: 2, 3"
    assert detail["evidence"][0] == {
        "line": 2, "column": 1, "found_value": "secret-section",
        "excerpt": "⟦secret-section⟧", "label": "Kurum adı",
    }
    missing = describe_audit_warning(warning("Risk (ilgili bolum: 'absent')", "other"), None)
    assert "belirlenemedi" in missing["location"]


def test_variable_name_evidence_shows_assigned_value():
    content = 'string anaMusteriAd = "Ayşe Yılmaz";\nprint(anaMusteriAd);\n'
    detail = describe_audit_warning(warning("Kisi adi (ilgili bolum: 'anaMusteriAd')", content), None)
    assert [e["found_value"] for e in detail["evidence"]] == ["Ayşe Yılmaz"]
    assert detail["evidence"][0]["excerpt"] == 'string anaMusteriAd = "⟦Ayşe Yılmaz⟧";'
    assert detail["evidence"][0]["line"] == 1
    assert detail["location"] == "1 satırda: 1"


def test_variable_name_without_clear_value_is_reported_as_likely_false_alarm():
    content = 'string anaMusteriAd = "";\nanaMusteriAd = dr["MusteriAd"].ToString();\n'
    detail = describe_audit_warning(warning("Kisi adi (ilgili bolum: 'anaMusteriAd')", content), None)
    assert detail["evidence"] == []
    assert "anaMusteriAd" in detail["summary"]
    assert "yanlış alarm" in detail["next_step"]


def test_code_expressions_are_not_reported_as_leaked_values():
    content = (
        'detay += $"TCKN : {dr["kimlikNo"]}\\n";\n'
        'adTextBox.Text = row["kisiAdi"].ToString();\n'
        "this.musteriKimlikNoTextBox.Location = new Point(1, 2);\n"
    )
    reason = (
        "Maskelenmemis kimlik no (ilgili bolum: 'dr[\"kimlikNo') | "
        "Maskelenmemis kisi adi (ilgili bolum: 'adTextBox.Text = row[\"kisiAdi\"].ToString') | "
        "Maskelenmemis kimlik no (ilgili bolum: 'musteriKimlikNoTextBox')"
    )
    detail = describe_audit_warning(warning(reason, content), None)
    assert detail["evidence"] == []
    assert "kimlikNo, kisiAdi, musteriKimlikNoTextBox" in detail["summary"]


def test_findings_are_labelled_in_plain_language():
    content = 'ad = "Ayşe Yılmaz"\ntel = "05321234567"\n'
    reason = (
        "Maskelenmemis kisi adi (ilgili bolum: 'Ayşe Yılmaz') | "
        "Maskelenmemis telefon numarasi (ilgili bolum: '05321234567')"
    )
    detail = describe_audit_warning(warning(reason, content), None)
    assert [(e["label"], e["found_value"]) for e in detail["evidence"]] == [
        ("Kişi adı", "Ayşe Yılmaz"), ("Telefon numarası", "05321234567"),
    ]
    assert detail["summary"] == "Bu dosyada gizlenmemiş görünen 2 bilgi var: kişi adı, telefon numarası."
