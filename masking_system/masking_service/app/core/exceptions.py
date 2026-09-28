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
    """The same project/sicil/branch export is already running."""


class ReviewAlreadyProcessedError(MaskingSystemError):
    """A review queue item was already approved/rejected/ignored."""
