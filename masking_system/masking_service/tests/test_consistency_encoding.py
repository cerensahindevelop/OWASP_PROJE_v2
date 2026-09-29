"""Tutarlilik adiminda kodlama kaymasi regresyonu.

Maskeli cikti tutarlilik taramasi icin yeniden okunurken kodlama
peek_classify ile YENIDEN tahmin ediliyordu. cp1254 bir dosya maskelemeden
once cp1258, sonra cp1250 tahmin edilebiliyor; metin farkli cozuldugu icin
verify_round_trip_digest basarisiz oluyor ve dogru maskelenmis dosya
failed_consistency_validation ile dusuyordu.
"""

from __future__ import annotations

from app.services import exporter as exporter_module
from app.services.exporter import _read_consistency_target
from app.services.file_type import write_text_preserving_encoding
from app.services.roundtrip_validator import text_digest, verify_round_trip_digest

# Bu kaynak icin charset_normalizer maskelemeden once ve sonra FARKLI
# kodlama tahmin ediyordu (cp1258 -> cp1250).
_SOURCE = (
    "// Şirket içi sunucu güncellemesi\n"
    "public class A {\n"
    '    String h = "10.20.30.40";\n'
    "}\n"
)
_PLACEHOLDER = "IP_ADDRESS_0001"


def test_consistency_reread_keeps_original_encoding(tmp_path, monkeypatch):
    masked = _SOURCE.replace("10.20.30.40", _PLACEHOLDER)
    # Turkce tercihi bu ornekteki kaymayi artik onluyor; yeniden tahminin
    # yine de kayabilecegi (baska bir Latin kodlamasi) durumu sabitliyoruz.
    monkeypatch.setattr(exporter_module, "peek_classify", lambda path: (True, "cp1250"))

    target = tmp_path / "A.java"
    assert write_text_preserving_encoding(target, masked, "cp1254") == "cp1254"

    text, encoding, error = _read_consistency_target(target, 1_000_000, preferred_encoding="cp1254")

    assert error is None
    assert encoding == "cp1254"
    assert text == masked
    result = verify_round_trip_digest(
        text_digest(_SOURCE), len(_SOURCE), text, {_PLACEHOLDER: "10.20.30.40"},
    )
    assert result.ok


def test_consistency_reread_without_preference_keeps_detection(tmp_path):
    target = tmp_path / "a.txt"
    target.write_bytes("değer = 1\n".encode("utf-8"))

    text, encoding, error = _read_consistency_target(target, 1_000_000)

    assert (text, encoding, error) == ("değer = 1\n", "utf-8", None)


def test_consistency_reread_falls_back_when_preferred_cannot_decode(tmp_path):
    target = tmp_path / "a.txt"
    target.write_bytes("değer = 1\n".encode("utf-8"))

    # ascii bu baytlari cozemez; tespit edilen kodlamaya dusulmeli.
    text, encoding, error = _read_consistency_target(target, 1_000_000, preferred_encoding="ascii")

    assert (text, encoding, error) == ("değer = 1\n", "utf-8", None)
