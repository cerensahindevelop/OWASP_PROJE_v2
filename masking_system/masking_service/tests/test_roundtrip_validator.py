"""roundtrip_validator.py icin birim testler - saf mantik, DB/dosya erisimi
yok. "Restore edilen cikti orijinal dosyayla birebir ayni olmali" hedefini
export ANINDA (gercek bir unmask calismasi beklemeden) dogrulayan
verify_round_trip()'in temel durumlarini kapsar: tam eslesme, yalnizca
harf-buyuklugu farkinin da veri-butunlugu hatasi sayilmasi ve gercek bir
uyusmazligin sessizce gecilmemesi."""

from __future__ import annotations

import pytest

from app.services.roundtrip_validator import verify_round_trip
from app.services.roundtrip_validator import text_digest, verify_round_trip_digest


@pytest.mark.parametrize("token", [
    "SERVICE_TEST_1", "service_test_1", "MASK_EMAIL_7", "mask_email_7suffix",
    "811199000000123", "9111990000000123",
])
def test_original_lookalikes_are_valid_only_when_source_is_restored_exactly(token):
    original = f"{token}\ncontact = user@example.com\n"
    masked = f"{token}\ncontact = mask_email_99\n"
    mapping = {"mask_email_99": "user@example.com"}
    assert verify_round_trip(original, masked, mapping).ok
    assert verify_round_trip_digest(text_digest(original), len(original), masked, mapping).ok
    # An unchanged lookalike must never hide a missing real mapping, an
    # incorrect restored value, or a change elsewhere in the file.
    for broken_text, broken_map in (
        (masked, {}),
        (masked, {"mask_email_99": "wrong@example.com"}),
        (masked + "changed", mapping),
    ):
        assert not verify_round_trip(original, broken_text, broken_map).ok
        assert not verify_round_trip_digest(
            text_digest(original), len(original), broken_text, broken_map,
        ).ok


def test_final_digest_handles_reused_placeholder_without_identity_collision():
    original = "ELENA " * 10
    first_pass = "mask_person_355 " * 2 + "ELENA " * 8
    final = "mask_person_355 " * 10
    mapping = {"mask_person_355": "ELENA"}
    old = verify_round_trip(first_pass, final, mapping)
    assert not old.ok  # Two old tokens shrink by 10 chars: the reported -20.
    assert len(first_pass) - len(original) == 20
    assert verify_round_trip_digest(text_digest(original), len(original), final, mapping).ok


def test_final_digest_still_rejects_real_corruption_and_wrong_mapping():
    original = "ELENA ELENA"
    for final, mapping in [
        ("mask_person_1", {"mask_person_1": "ELENA"}),
        ("mask_person_1 mask_person_1", {"mask_person_1": "OTHER"}),
        ("mask_person_1 mask_person_1", {}),
    ]:
        assert not verify_round_trip_digest(text_digest(original), len(original), final, mapping).ok


def test_exact_match_is_ok():
    original = 'password = "hunter2ABCDEFG"\n'
    masked = 'password = "mask_secret_1"\n'
    placeholder_map = {"mask_secret_1": "hunter2ABCDEFG"}

    result = verify_round_trip(original, masked, placeholder_map)

    assert result.ok is True
    assert result.exact_match is True
    assert result.case_normalized_only is False


def test_case_only_difference_is_rejected_and_flagged():
    # Detection case-insensitive olabilir; reversible cikti yine de
    # karakter-karakter ayni olmak zorundadir.
    original = "project = MixedCaseIdentity\n"
    masked = "project = mask_proje_adi_1\n"
    placeholder_map = {"mask_proje_adi_1": "mixedcaseidentity"}

    result = verify_round_trip(original, masked, placeholder_map)

    assert result.ok is False
    assert result.exact_match is False
    assert result.case_normalized_only is True
    assert result.detail is not None


def test_real_mismatch_is_reported_not_silently_passed():
    # masked_text, mappings ile ILGISIZ bir icerik - reverse_text hicbir
    # placeholder bulamaz, oldugu gibi doner ve orijinalle uyusmaz.
    original = 'password = "hunter2ABCDEFG"\n'
    masked = "def f(:\n    broken syntax here\n"
    placeholder_map = {"mask_secret_1": "hunter2ABCDEFG"}

    result = verify_round_trip(original, masked, placeholder_map)

    assert result.ok is False
    assert result.exact_match is False
    assert result.case_normalized_only is False
    assert result.detail is not None
    # Guvenlik: hata mesaji ham/gercek degeri (hunter2ABCDEFG) ICERMEMELI.
    assert "hunter2ABCDEFG" not in result.detail


def test_unresolved_placeholder_is_reported():
    # masked_text'te bir placeholder var ama placeholder_map'te karsiligi
    # yok - bu, az once olusturulan/bulunan mapping'lerle TUTARSIZ bir
    # durum, sessizce gecilmemeli.
    original = 'password = "hunter2ABCDEFG"\n'
    masked = 'password = "mask_secret_1"\n'
    placeholder_map: dict[str, str] = {}

    result = verify_round_trip(original, masked, placeholder_map)

    assert result.ok is False
    assert result.unresolved_placeholders == ["mask_secret_1"]


def test_unresolved_placeholder_detail_points_to_the_exact_location():
    """The user must be able to find WHERE the problem is without opening a
    debugger - the token name and its (line, column) in the masked output,
    never the real underlying value."""
    original = 'password = "hunter2ABCDEFG"\n'
    masked = 'line1 = 1\nline2 = 2\npassword = "mask_secret_1"\n'
    placeholder_map: dict[str, str] = {}

    result = verify_round_trip(original, masked, placeholder_map)

    assert "mask_secret_1" in result.detail
    assert "satir 3" in result.detail
    assert "hunter2ABCDEFG" not in result.detail


def test_unresolved_placeholder_digest_detail_points_to_the_exact_location():
    masked = 'line1 = 1\nline2 = 2\npassword = "mask_secret_1"\n'
    result = verify_round_trip_digest(text_digest("anything"), 8, masked, {})
    assert "mask_secret_1" in result.detail
    assert "satir 3" in result.detail


def test_multiple_placeholders_round_trip_correctly():
    original = 'email = "user@example.com"\nkey = "AKIAABCDEFGHIJKLMNOP"\n'
    masked = 'email = "mask_email_1"\nkey = "mask_aws_key_1"\n'
    placeholder_map = {"mask_email_1": "user@example.com", "mask_aws_key_1": "AKIAABCDEFGHIJKLMNOP"}

    result = verify_round_trip(original, masked, placeholder_map)

    assert result.ok is True
    assert result.exact_match is True
