"""Application-specific exception hierarchy.

Service code should raise these typed errors instead of generic Exception
where callers can make a meaningful decision.
"""

from __future__ import annotations


class MaskingSystemError(Exception):
    """Base class for expected application-level errors."""


class GecersizEslesmeHatasi(MaskingSystemError):
    """A detection/mapping result was internally inconsistent."""


class ExportInProgressError(MaskingSystemError):
    """The same project/sicil/branch export is already running.

    active_job_id: ayni backend surecindeki calisan isin kimligi (biliniyorsa);
    arayuz bununla "durdur ve yeniden baslat" secenegi sunar.
    """

    def __init__(self, message: str = "", *, active_job_id: str | None = None) -> None:
        super().__init__(message)
        self.active_job_id = active_job_id


class ExportCancelledError(MaskingSystemError):
    """The user stopped a running export; nothing was published."""


class ReviewAlreadyProcessedError(MaskingSystemError):
    """A review queue item was already approved/rejected/ignored."""


class AuditFindingsUpdatedError(MaskingSystemError, ValueError):
    """Kullanicinin karari uygulandi, ancak final AI denetimi yeni bulgu buldu.

    Dosya karantinada kalir; uyari yeni bulgularla guncellenmistir ve
    kullanici onlar icin ayrica karar verir. `code` API yanitina eklenir:
    arayuz bunu "ayni butona basmak ayni sonucu verir" hatasindan ayirir.
    Mesaj bulgu degerlerini icermez (hata mesajlari loglanabilir).
    """

    code = "audit_findings_updated"


class BuildMismatchError(MaskingSystemError):
    """Running code is a mixed version (stamp mismatch or files changed since start)."""
