"""app/core/error_translation.friendly_error icin birim testler (Asama 2 /
Adim 3c). DB'siz, saf bir fonksiyon - her exception tipi icin beklenen
(mesaj, detay) ciftini dogrular.
"""

from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError

from app.core.error_translation import friendly_error
from app.core.exceptions import ExportInProgressError, ReviewAlreadyProcessedError
from app.services.exporter import ExportValidationError
from app.services.unmasker import ContextNotFoundError


def test_context_not_found_gets_identity_hint_and_detail():
    exc = ContextNotFoundError("Kayitli masking context bulunamadi: proje_adi='X'")
    message, detail = friendly_error(exc)

    assert "Kimlik bilgilerinizi kontrol edin" in message
    assert detail == str(exc)


def test_export_in_progress_gets_retry_hint_and_detail():
    exc = ExportInProgressError("bu proje/sicil/branch icin zaten devam eden export var")
    message, detail = friendly_error(exc)

    assert "zaten devam eden bir işlem var" in message
    assert detail == str(exc)


def test_review_already_processed_gets_friendly_message_and_detail():
    exc = ReviewAlreadyProcessedError("review_queue id=5 zaten islenmis")
    message, detail = friendly_error(exc)

    assert "başka biri tarafından zaten işlenmiş" in message
    assert detail == str(exc)


def test_export_validation_error_shows_raw_message_without_detail():
    exc = ExportValidationError("hedef dizin, kaynak proje dizininin icinde olamaz")
    message, detail = friendly_error(exc)

    assert message == str(exc)
    assert detail is None


def test_plain_value_error_shows_raw_message_without_detail():
    exc = ValueError("gecersiz deger")
    message, detail = friendly_error(exc)

    assert message == "gecersiz deger"
    assert detail is None


def test_sqlalchemy_error_is_masked_with_technical_detail():
    exc = SQLAlchemyError("connection refused")
    message, detail = friendly_error(exc)

    assert "Sistemle bağlantı kurulamadı" in message
    assert detail == str(exc)


def test_unexpected_exception_gets_generic_message_with_type_and_detail():
    exc = RuntimeError("beklenmedik cokme")
    message, detail = friendly_error(exc)

    assert "Beklenmeyen bir hata oluştu" in message
    assert detail == "RuntimeError: beklenmedik cokme"
