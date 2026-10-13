"""Maskelemenin sozdizimini bozdugu dosyalar (2026-10-13 olayi).

Gozlenen: Presidio'nun Ingilizce spaCy modeli Python'daki `islemler=[]`
icinden `islemler=` ifadesini DATE_TIME saydi; yer tutucu `=` isaretini
yutunca `mask_date_time_1[]` gecersiz Python oldu ve dosya ciktidan dustu.

- Rakamsiz bir Presidio "tarihi" artik bulgu sayilmaz.
- Ekran hatanin konumunu, sorunlu yer tutucuyu ve yerine gectigi degeri gosterir.
- Kullanici dosyayi bozuk haliyle isteyebilir; sozdizimi disindaki tum
  final kontroller yine calisir.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db.models import AuditLog, AuditWarning, MaskingContext, MaskingRun
from app.services import audit_warning_details, audit_warning_service
from app.services.audit_action_labels import ACCEPT_SYNTAX_ACTION_LABEL
from app.services.audit_warning_details import describe_audit_warning
from app.services.audit_warning_service import AuditWarningService
from app.services.failed_checks import FailedCheck
from app.services.failure_messages import explain_failure
from app.services.presidio_detector import PresidioDetector

_LINE = "    r=random.Random(42); kartlar=[]; islemler=[]; sayac=0"


class _FakeAnalyzer:
    def __init__(self, values):
        self.values = values

    def analyze(self, *, text, language, entities=None, nlp_artifacts=None):
        found = []
        for value in self.values:
            start = text.find(value)
            if start >= 0:
                found.append(SimpleNamespace(entity_type="DATE_TIME", start=start, end=start + len(value),
                                             score=0.85, recognition_metadata={}))
        return found


def test_presidio_date_without_digits_is_not_a_finding(monkeypatch):
    monkeypatch.setattr(PresidioDetector, "_build_analyzer", lambda self: None)
    detector = PresidioDetector([])
    detector._analyzer = _FakeAnalyzer(["islemler=", "12.03.1985"])
    text = f"{_LINE}\ndogum = '12.03.1985'\n"

    output = asyncio.run(detector.detect(text, {"file_path": "araclar/veri_uret.py"}))

    assert [r.deger for r in output.results] == ["12.03.1985"]


def _context_run(db_session, tmp_path, suffix):
    context = MaskingContext(project_name=f"pytest-syntax-{suffix}", sicil_no="P-SYN-1", branch_name="main")
    db_session.add(context)
    db_session.flush()
    run = MaskingRun(context_id=context.id, operation_type="mask", source_path="/tmp/src",
                     target_path=str(tmp_path), initiated_by="P-SYN-1", status="completed_with_warnings")
    db_session.add(run)
    db_session.flush()
    return run


def _syntax_warning(db_session, run, content, *, failed_check=FailedCheck.SOZDIZIMI, file_path="araclar/veri_uret.py"):
    warning = AuditWarning(
        run_id=run.id, file_path=file_path, masked_content=content, encoding="utf-8",
        reasoning="Sözdizimi doğrulaması başarısız: Python sozdizimi hatasi (satir 2, sutun 40)",
        audit_failed=True, failed_check=failed_check,
    )
    db_session.add(warning)
    db_session.flush()
    return warning


def test_location_names_the_placeholder_and_the_value_it_replaced(db_session, tmp_path, monkeypatch):
    run = _context_run(db_session, tmp_path, "location")
    content = "import random\n    r=random.Random(42); kartlar=[]; mask_date_time_1[]; sayac=0\n"
    warning = _syntax_warning(db_session, run, content)
    monkeypatch.setattr(audit_warning_details, "_run_reverse_map", lambda db, w: {"mask_date_time_1": "islemler="})

    details = describe_audit_warning(warning, db_session)

    assert details["location"].startswith("Maskelenmiş dosyada satır 2, sütun 40.")
    assert "`mask_date_time_1`" in details["location"] and "`islemler=`" in details["location"]
    [evidence] = details["evidence"]
    assert evidence["line"] == 2 and evidence["column"] == 40 and evidence["found_value"] == "islemler="
    assert "⟦mask_date_time_1⟧[]" in evidence["excerpt"]


def test_syntax_message_says_rerun_does_not_help_and_offers_the_option():
    summary, next_step = explain_failure(FailedCheck.SOZDIZIMI, "Python sozdizimi hatasi (satir 2, sutun 40)")
    assert summary == "Sözdizimi: maskeleme sonrası dosya Python kurallarına uymuyor."
    assert "sonucu değiştirmez" in next_step
    assert ACCEPT_SYNTAX_ACTION_LABEL in next_step


@pytest.fixture()
def broken_syntax(monkeypatch):
    monkeypatch.setattr(audit_warning_service, "validate_masked_syntax",
                        lambda *a, **k: "Python sozdizimi hatasi (satir 1, sutun 5)")


def test_user_can_release_syntax_broken_file(db_session, tmp_path, broken_syntax):
    run = _context_run(db_session, tmp_path, "accept")
    warning = _syntax_warning(db_session, run, "x = (\n")
    service = AuditWarningService(db_session)

    # Varsayilan (block) davranis degismedi: ayni icerik kontrolden gecmez.
    assert "sözdizimi doğrulaması başarısız" in service._offline_checks("x = (\n", warning, run)

    released = asyncio.run(service.accept_syntax_error(warning.id))

    assert released.status == "dismissed"
    assert (tmp_path / "araclar" / "veri_uret.py").read_text(encoding="utf-8") == "x = (\n"
    details = [row.detail for row in db_session.scalars(select(AuditLog).where(AuditLog.run_id == run.id))]
    assert any("validation_warning; user_decision=accept_syntax_error" in d for d in details)
    assert any("user_decision=accept_syntax_error" in d and "final_output=written" in d for d in details)


def test_other_checks_still_block_a_syntax_broken_file(db_session, tmp_path, broken_syntax):
    run = _context_run(db_session, tmp_path, "accept-privacy")
    # Esleme kaydi olmayan yer tutucu: geri donus dogrulamasi gecmez.
    warning = _syntax_warning(db_session, run, "x = mask_ip_99(\n")

    with pytest.raises(ValueError, match="geri dönüş"):
        asyncio.run(AuditWarningService(db_session).accept_syntax_error(warning.id))

    assert not (tmp_path / "araclar" / "veri_uret.py").exists()
    db_session.refresh(warning)
    assert warning.status == "pending"


def test_option_is_only_for_syntax_failures(db_session, tmp_path, broken_syntax):
    run = _context_run(db_session, tmp_path, "accept-other")
    warning = _syntax_warning(db_session, run, "x = 1\n", failed_check=FailedCheck.GERI_DONUS)

    with pytest.raises(ValueError, match="yalnızca sözdizimi"):
        asyncio.run(AuditWarningService(db_session).accept_syntax_error(warning.id))
    assert not (tmp_path / "araclar" / "veri_uret.py").exists()
