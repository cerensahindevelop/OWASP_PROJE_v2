"""Teknik exception'lari kullaniciya gosterilecek Turkce/anlasilir mesaja
ceviren, sunum katmanindan bagimsiz (Streamlit'e ozel hicbir sey icermeyen)
saf fonksiyon (Asama 2 / Adim 3c).

app/webapp/common.py (Streamlit) ve app/ui.py (FastAPI) ayni hata-ceviri
mantigini PAYLASIR - eskiden sadece webapp/common.py'de vardi, app/ui.py
kendi ad-hoc `except Exception` bloklarini tekrarlayip ham `str(exc)`
gosteriyordu (orn. ExportInProgressError bile ValueError'in alt sinifi
OLMADIGI icin app/ui.py'nin eski except tuple'inda hic yakalanmiyor,
"Beklenmeyen hata" olarak gosteriliyordu)."""

from __future__ import annotations

# SQLAlchemyError: DB katmanindan gelen tum hatalari tek bir genel/kullanici-
# guvenli mesaja cevirmek icin yakalanir (baglanti kopmasi, kisit ihlali vb.).
from sqlalchemy.exc import SQLAlchemyError

from app.core.exceptions import ExportInProgressError, MaskingSystemError, ReviewAlreadyProcessedError
from app.services.exporter import ExportValidationError
from app.services.term_file_parser import TermFileError
from app.services.term_upload import TermUploadValidationError
from app.services.unmasker import ContextNotFoundError


def friendly_error(exc: Exception) -> tuple[str, str | None]:
    """(kullaniciya gosterilecek mesaj, varsa teknik detay) dondurur.
    Teknik detay None ise cagiran taraf 'detaylari goster' benzeri bir
    genisletici/blok hic gostermemelidir."""
    if isinstance(exc, ContextNotFoundError):
        return (
            "Bu proje/sicil/branch bilgisiyle daha önce yapılmış bir dışa aktarma bulunamadı. "
            "Kimlik bilgilerinizi kontrol edin.",
            str(exc),
        )
    if isinstance(exc, ExportInProgressError):
        return (
            "Bu proje/sicil/branch için zaten devam eden bir işlem var. Önceki işlemin "
            "bitmesini bekleyip tekrar deneyin.",
            str(exc),
        )
    if isinstance(exc, ReviewAlreadyProcessedError):
        return ("Bu kayıt başka biri tarafından zaten işlenmiş. Liste güncellendi.", str(exc))
    if isinstance(exc, ExportValidationError):
        return (str(exc), None)
    if isinstance(exc, (TermFileError, TermUploadValidationError)):
        # Bu iki hiyerarsinin mesajlari zaten kullanici-guvenli/konum-only
        # yazildi (bkz. term_file_parser.py/term_upload.py) - ek teknik
        # detay gostermeye gerek yok.
        return (str(exc), None)
    if isinstance(exc, MaskingSystemError):
        return (str(exc), None)
    if isinstance(exc, ValueError):
        return (str(exc), None)
    if isinstance(exc, SQLAlchemyError):
        return ("Sistemle bağlantı kurulamadı, lütfen yöneticinize başvurun.", str(exc))
    return ("Beklenmeyen bir hata oluştu, lütfen yöneticinize başvurun.", f"{type(exc).__name__}: {exc}")
