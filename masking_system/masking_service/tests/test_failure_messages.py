import pytest

from app.services.failure_messages import explain_failure


@pytest.mark.parametrize("reason, expected", [
    ("final safety scan 2 acik canonical occurrence buldu: satir 3 sutun 5 (email)",
     "Tutarlılık: projenin başka dosyalarında maskelenen bir değer bu dosyada açık kaldı."),
    ("final consistency round-trip dogrulamasi basarisiz: uzunluk uyusmuyor",
     "Tutarlılık: maskeli dosya asıl haline birebir çevrilemedi."),
    ("consistency maskesi sozdizimini bozdu: JSON sozdizimi hatasi",
     "Tutarlılık: başka dosyalardaki değerleri bu dosyada da maskelemek dosyanın yapısını bozdu."),
    ("consistency taramasi: okuma basarisiz (yol=C:/x, yol_uzunlugu=40, hata=PermissionError): denied",
     "Okuma hatası: erişim izni yok."),
    ("final consistency taramasinda beklenmeyen bir hata olustu (KeyError)",
     "Tutarlılık: son tutarlılık kontrolü tamamlanamadı."),
])
def test_consistency_reason_names_the_actual_cause(reason, expected):
    assert explain_failure("tutarlilik", reason)[0] == expected


def test_file_errors_name_the_os_cause():
    long_path = "yazma basarisiz (yol=C:/a, yol_uzunlugu=300, hata=FileNotFoundError): x"
    assert "Windows sınırını" in explain_failure("yazma", "Doğrulanmış çıktı yazılamadı: " + long_path)[0]
    denied = "stat basarisiz (yol=/x, yol_uzunlugu=20, hata=PermissionError): denied"
    assert explain_failure("okuma", "Dosya güvenlik doğrulamasına hazırlanamadı: " + denied)[0] == (
        "Okuma hatası: erişim izni yok."
    )


def test_unknown_or_missing_check_returns_none():
    assert explain_failure(None, "x") is None
    assert explain_failure("bilinmeyen_kod", "x") is None


def test_syntax_message_names_the_parser():
    summary, _ = explain_failure("sozdizimi", "Sözdizimi doğrulaması başarısız: JSON sozdizimi hatasi (satir 3)")
    assert summary == "Sözdizimi: maskeleme sonrası dosya JSON kurallarına uymuyor."
