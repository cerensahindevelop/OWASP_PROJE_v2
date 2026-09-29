"""Degeri bilinen sizintilarin (acik kalan sozluk terimi, metinde birebir
dogrulanmis denetim alintisi) insana sorulmadan otomatik maskelenmesi.

Otomatik duzeltilen dosya ciktiya yazilmadan once ayni son kontrollerden
(geri donus + sozdizimi + acik terim + LLM denetimi) tekrar gecer; gecemezse
mevcut davranisa (insan onayi) donulur.
"""

from __future__ import annotations

import asyncio

from app.db.models import AuditLog, AuditWarning
from app.services import exporter
from app.services.audit_reviewer import AuditFinding, AuditVerdict
from app.services.detectors import DetectorOutput
from app.services.exporter import export_project
from app.services.term_upload import commit_term_upload


class _NoDetections:
    """Ilk tespit gecisinin degeri kacirdigi durumu canlandirir."""

    async def scan(self, text, metadata=None):
        return DetectorOutput(results=[])


def _export(db_session, tmp_path, files: dict[str, str], suffix: str):
    source = tmp_path / f"{suffix}-src"
    for rel, content in files.items():
        path = source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    target = tmp_path / f"{suffix}-out"
    report = asyncio.run(export_project(
        db_session, source_path=str(source), target_path=str(target),
        project_name=f"pytest-{suffix}", sicil_no="P-AUTO", branch_name="main", initiated_by="P-AUTO",
    ))
    return report, target


def _outcome(report, rel):
    return next(o for o in report.outcomes if o.relative_path == rel)


def _trail(db_session, report):
    return [row.detail or "" for row in db_session.query(AuditLog).filter_by(run_id=report.run_id).all()]


def _clean_audit_factory(calls: list[str] | None = None):
    async def clean(masked_text, *args, **kwargs):
        if calls is not None:
            calls.append(masked_text)
        return AuditVerdict(risky=False)
    return clean


def test_leaked_dictionary_term_is_auto_masked_without_review(db_session, tmp_path, monkeypatch):
    commit_term_upload(db_session, filename="terms.txt", content=b"Poseidon\n", category="pytest_auto_term")
    audits: list[str] = []
    monkeypatch.setattr(exporter, "build_orchestrator", lambda *a, **k: _NoDetections())
    monkeypatch.setattr(exporter, "audit_masked_text", _clean_audit_factory(audits))

    report, target = _export(db_session, tmp_path, {"src/a.py": "client = PoseidonService()\n"}, "auto-term")

    output = (target / "src" / "a.py").read_text(encoding="utf-8")
    assert "Poseidon" not in output
    assert _outcome(report, "src/a.py").final_state == "READY"
    assert db_session.query(AuditWarning).filter_by(run_id=report.run_id).count() == 0
    trail = _trail(db_session, report)
    assert any("auto_remediated" in detail for detail in trail)
    assert not any("Poseidon" in detail for detail in trail)
    # Duzeltilen metin LLM denetiminden tekrar gecti.
    assert len(audits) == 2 and "Poseidon" not in audits[1]


def test_verified_audit_quote_is_auto_masked_and_propagated(db_session, tmp_path, monkeypatch):
    audits: list[str] = []

    async def audit(masked_text, *args, **kwargs):
        audits.append(masked_text)
        if "# sorumlu: Hakan Yilmaz" in masked_text:
            return AuditVerdict(risky=True, findings=[
                AuditFinding(aciklama="kisi adi", ilgili_bolum="Hakan Yilmaz")])
        return AuditVerdict(risky=False)

    monkeypatch.setattr(exporter, "build_orchestrator", lambda *a, **k: _NoDetections())
    monkeypatch.setattr(exporter, "audit_masked_text", audit)

    report, target = _export(db_session, tmp_path, {
        "src/b.py": "# sorumlu: Hakan Yilmaz\nx = 1\n",
        # Denetimin temiz buldugu baska bir dosya: deger tutarlilik
        # registry'si uzerinden burada da maskelenmeli.
        "docs/notes.md": "Proje sahibi Hakan Yilmaz\n",
    }, "auto-audit")

    output = (target / "src" / "b.py").read_text(encoding="utf-8")
    assert "Hakan Yilmaz" not in output
    assert "mask_denetim_bulgusu_" in output
    assert _outcome(report, "src/b.py").final_state == "READY"
    assert "Hakan Yilmaz" not in (target / "docs" / "notes.md").read_text(encoding="utf-8")
    assert db_session.query(AuditWarning).filter_by(run_id=report.run_id).count() == 0
    trail = _trail(db_session, report)
    assert any("auto_remediated" in detail for detail in trail)
    assert not any("Hakan" in detail for detail in trail)


def test_second_remediation_round_resolves_new_finding(db_session, tmp_path, monkeypatch):
    audits: list[str] = []

    async def audit(masked_text, *args, **kwargs):
        audits.append(masked_text)
        for name in ("Hakan Yilmaz", "Ayse Kaya"):
            if name in masked_text:
                return AuditVerdict(risky=True, findings=[AuditFinding(aciklama="kisi adi", ilgili_bolum=name)])
        return AuditVerdict(risky=False)

    monkeypatch.setattr(exporter, "build_orchestrator", lambda *a, **k: _NoDetections())
    monkeypatch.setattr(exporter, "audit_masked_text", audit)

    report, target = _export(db_session, tmp_path, {
        "src/b.py": "# sorumlu: Hakan Yilmaz\n# yedek: Ayse Kaya\n",
    }, "auto-two")

    output = (target / "src" / "b.py").read_text(encoding="utf-8")
    assert "Hakan Yilmaz" not in output and "Ayse Kaya" not in output
    assert _outcome(report, "src/b.py").final_state == "READY"
    assert len(audits) == 3
    assert any("auto_remediated rounds=2" in detail for detail in _trail(db_session, report))


def test_unresolvable_remediation_falls_back_to_review_with_original_text(db_session, tmp_path, monkeypatch):
    audits: list[str] = []

    async def always_risky(masked_text, *args, **kwargs):
        # Duzeltmeden sonra da ayni alintiyi gosteren denetim: cozulemez.
        audits.append(masked_text)
        return AuditVerdict(risky=True, findings=[
            AuditFinding(aciklama="kisi adi", ilgili_bolum="Hakan Yilmaz")])

    monkeypatch.setattr(exporter, "build_orchestrator", lambda *a, **k: _NoDetections())
    monkeypatch.setattr(exporter, "audit_masked_text", always_risky)

    source_text = "# sorumlu: Hakan Yilmaz\nx = 1\n"
    report, target = _export(db_session, tmp_path, {"src/b.py": source_text}, "auto-fail")

    assert not (target / "src" / "b.py").exists()
    assert _outcome(report, "src/b.py").final_state == "SECURITY_QUARANTINE"
    warning = db_session.query(AuditWarning).filter_by(run_id=report.run_id).one()
    # Insan, duzeltme oncesi orijinal maskeli metni ve denemenin notunu gorur.
    assert warning.masked_content == source_text
    assert "otomatik düzeltme denendi" in warning.reasoning
    # Sonsuz dongu yok: ilk denetim + en fazla 2 duzeltme turu.
    assert len(audits) <= 3


def test_remediation_breaking_syntax_falls_back_to_review(db_session, tmp_path, monkeypatch):
    # Alinti bir operatoru de kapsiyor: maskelemek Python sozdizimini bozar.
    async def risky_operator(masked_text, *args, **kwargs):
        if "x = 1" in masked_text:
            return AuditVerdict(risky=True, findings=[AuditFinding(aciklama="x", ilgili_bolum="= 1")])
        return AuditVerdict(risky=False)

    monkeypatch.setattr(exporter, "build_orchestrator", lambda *a, **k: _NoDetections())
    monkeypatch.setattr(exporter, "audit_masked_text", risky_operator)

    source_text = "x = 1\n"
    report, target = _export(db_session, tmp_path, {"src/c.py": source_text}, "auto-syntax")

    assert not (target / "src" / "c.py").exists()
    warning = db_session.query(AuditWarning).filter_by(run_id=report.run_id).one()
    assert warning.masked_content == source_text
    assert "otomatik düzeltme denendi" in warning.reasoning
    assert "sözdizimi" in warning.reasoning
    trail = _trail(db_session, report)
    assert any("auto_remediation=failed check=sozdizimi" in detail for detail in trail)
    # Basarisiz denemenin eslemesi geri alindi.
    from app.db.models import ValueMapping
    assert db_session.query(ValueMapping).filter_by(run_id=report.run_id).count() == 0


def test_leak_with_failed_audit_is_not_auto_released(db_session, tmp_path, monkeypatch):
    from app.services.llm_recognizer import LLMRecognitionError

    commit_term_upload(db_session, filename="terms.txt", content=b"Poseidon\n", category="pytest_auto_err")

    async def failing(*args, **kwargs):
        raise LLMRecognitionError("denetim yok")

    monkeypatch.setattr(exporter, "build_orchestrator", lambda *a, **k: _NoDetections())
    monkeypatch.setattr(exporter, "audit_masked_text", failing)

    report, target = _export(db_session, tmp_path, {"src/a.py": "client = PoseidonService()\n"}, "auto-err")

    assert not (target / "src" / "a.py").exists()
    assert db_session.query(AuditWarning).filter_by(run_id=report.run_id).count() == 1
