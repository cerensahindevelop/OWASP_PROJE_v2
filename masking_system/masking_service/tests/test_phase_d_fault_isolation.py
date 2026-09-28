"""Faz D (audit karari -> terim son kontrolu -> sozdizimi -> hedefe yazma)
ve final consistency taramasi icin dosya-bazli hata izolasyonu.

Invariant tum bu dosyada test edilir: bir dosyanin BEKLENMEYEN bir istisnasi
!= tum batch'in basarisiz olmasi. Faz A (detector) ve Faz B (mapping/DB
yazma) icin ayni ilke zaten test_exporter_failure_handling.py'de kanitlandi;
burada Faz C (audit), Faz D (sozdizimi/yazma) ve final consistency taramasi
ayni ilkeye tabi tutuluyor - sorunlu dosya karantinaya alinir/basarisiz
sayilir, DIGER saglam dosyalar etkilenmeden hedefe yazilir, hicbir yarim
DB/dosya durumu kalmaz.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text as sqltext

from app.db.session import SessionLocal
from app.services import exporter as exporter_module
from app.services.detectors import DetectionResult, DetectorOutput, synthetic_llm_rule

_IDENTITY_PREFIX = "pytest-phase-d-isolation"

# A marker that always stays verbatim in masked output (not a sensitive
# value in any rule's eyes) - used to identify "which file is this masked
# text/path" from within callbacks that don't otherwise receive the path.
_BAD_MARKER = "BADFILE_MARKER_UNIQUE"


def _cleanup_identity(project_name: str) -> None:
    with SessionLocal() as db:
        row = db.execute(
            sqltext(
                "SELECT id FROM maskeleme_baglamlari WHERE proje_adi=:p AND personel_no=:pn AND branch_adi=:b"
            ),
            {"p": project_name, "pn": "P-TEST-0001", "b": "pytest-branch"},
        ).first()
        if row is None:
            return
        context_id = row[0]
        run_ids = [
            r[0]
            for r in db.execute(
                sqltext("SELECT id FROM maskeleme_calismalari WHERE baglam_id=:c"), {"c": context_id}
            ).all()
        ]
        for run_id in run_ids:
            db.execute(sqltext("DELETE FROM denetim_kaydi WHERE calisma_id=:r"), {"r": run_id})
            db.execute(sqltext("DELETE FROM gozden_gecirme_kuyrugu WHERE calisma_id=:r"), {"r": run_id})
            db.execute(sqltext("DELETE FROM denetim_uyarilari WHERE calisma_id=:r"), {"r": run_id})
        db.execute(sqltext("DELETE FROM maskeleme_calismalari WHERE baglam_id=:c"), {"c": context_id})
        db.execute(sqltext("DELETE FROM deger_eslemeleri WHERE baglam_id=:c"), {"c": context_id})
        db.execute(sqltext("DELETE FROM maskeleme_baglamlari WHERE id=:c"), {"c": context_id})
        db.commit()


def _run_export(source_dir, target_dir, project_name):
    identity = (project_name, "P-TEST-0001", "pytest-branch")
    with SessionLocal() as db:
        report = asyncio.run(
            exporter_module.export_project(
                db, source_path=str(source_dir), project_name=identity[0], sicil_no=identity[1],
                branch_name=identity[2], target_path=str(target_dir), initiated_by=identity[1],
            )
        )
        db.commit()
    return report


class _PerFileValueOrchestrator:
    """One DetectionResult per file for whatever known sensitive value is
    present in that file's own text (dictionary-sourced, deterministic)."""

    async def scan(self, text, metadata=None):
        for value in ("SecretA", "SecretB", "SecretC", "SecretD", "CompanySecretValue"):
            start = text.find(value)
            if start >= 0:
                return DetectorOutput(results=[DetectionResult(
                    deger=value, tip="S", guven_seviyesi="yuksek", kaynak_motor="dictionary",
                    start=start, end=start + len(value), rule=synthetic_llm_rule("S"),
                )])
        return DetectorOutput()


async def _clean_audit_stub(masked_text, vllm_settings):
    from app.services.audit_reviewer import AuditVerdict
    return AuditVerdict(risky=False)


# ---------------------------------------------------------------------------
# 1) Audit asamasinda bir dosya exception atsin.
# ---------------------------------------------------------------------------
def test_audit_exception_quarantines_only_that_file(tmp_path, monkeypatch):
    project = f"{_IDENTITY_PREFIX}-audit-crash"
    try:
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        (source_dir / "good1.py").write_text("a = 'SecretA'\n", encoding="utf-8")
        (source_dir / "bad.py").write_text(f"b = 'SecretB'  # {_BAD_MARKER}\n", encoding="utf-8")
        (source_dir / "good2.py").write_text("c = 'SecretC'\n", encoding="utf-8")
        target_dir = tmp_path / "target"

        async def _selective_crash_audit(masked_text, vllm_settings):
            if _BAD_MARKER in masked_text:
                raise RuntimeError("simulated audit crash")
            return await _clean_audit_stub(masked_text, vllm_settings)

        monkeypatch.setattr(exporter_module, "build_orchestrator", lambda *a, **kw: _PerFileValueOrchestrator())
        monkeypatch.setattr(exporter_module, "audit_masked_text", _selective_crash_audit)
        monkeypatch.setattr(exporter_module.settings.vllm, "enabled", True)

        report = _run_export(source_dir, target_dir, project)

        outcomes = {o.relative_path: o.status for o in report.outcomes}
        assert outcomes["bad.py"] == "quarantined_pending_audit"
        assert outcomes["good1.py"] == "masked"
        assert outcomes["good2.py"] == "masked"
        assert (target_dir / "good1.py").exists()
        assert (target_dir / "good2.py").exists()
        assert not (target_dir / "bad.py").exists()
        bad_outcome = next(o for o in report.outcomes if o.relative_path == "bad.py")
        assert bad_outcome.error  # failure reason recorded
        assert bad_outcome.final_state == "VALIDATION_FAILED"
    finally:
        _cleanup_identity(project)


# ---------------------------------------------------------------------------
# 2) Syntax validation sirasinda bir dosya exception atsin.
# ---------------------------------------------------------------------------
def test_syntax_validation_exception_quarantines_only_that_file(tmp_path, monkeypatch):
    project = f"{_IDENTITY_PREFIX}-syntax-crash"
    try:
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        (source_dir / "good1.py").write_text("a = 'SecretA'\n", encoding="utf-8")
        (source_dir / "bad.py").write_text("b = 'SecretB'\n", encoding="utf-8")
        (source_dir / "good2.py").write_text("c = 'SecretC'\n", encoding="utf-8")
        target_dir = tmp_path / "target"

        real_validate = exporter_module.validate_masked_syntax

        def _crashing_validate(relative_path, masked_text, original_text=None, **kwargs):
            if relative_path == "bad.py":
                raise RuntimeError("simulated syntax validator crash")
            return real_validate(relative_path, masked_text, original_text, **kwargs)

        monkeypatch.setattr(exporter_module, "build_orchestrator", lambda *a, **kw: _PerFileValueOrchestrator())
        monkeypatch.setattr(exporter_module, "audit_masked_text", _clean_audit_stub)
        monkeypatch.setattr(exporter_module.settings.vllm, "enabled", False)
        monkeypatch.setattr(exporter_module, "validate_masked_syntax", _crashing_validate)

        report = _run_export(source_dir, target_dir, project)

        outcomes = {o.relative_path: o.status for o in report.outcomes}
        assert outcomes["bad.py"] == "quarantined_pending_audit"
        assert outcomes["good1.py"] == "masked"
        assert outcomes["good2.py"] == "masked"
        assert (target_dir / "good1.py").exists()
        assert (target_dir / "good2.py").exists()
        assert not (target_dir / "bad.py").exists()
        bad_outcome = next(o for o in report.outcomes if o.relative_path == "bad.py")
        assert bad_outcome.error
    finally:
        _cleanup_identity(project)


# ---------------------------------------------------------------------------
# 3) Dosya yazma sirasinda bir dosya exception atsin (OSError DISI, genel).
# ---------------------------------------------------------------------------
def test_file_write_exception_quarantines_only_that_file(tmp_path, monkeypatch):
    project = f"{_IDENTITY_PREFIX}-write-crash"
    try:
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        (source_dir / "good1.py").write_text("a = 'SecretA'\n", encoding="utf-8")
        (source_dir / "bad.py").write_text("b = 'SecretB'\n", encoding="utf-8")
        (source_dir / "good2.py").write_text("c = 'SecretC'\n", encoding="utf-8")
        target_dir = tmp_path / "target"

        real_write = exporter_module.write_text_preserving_encoding

        def _crashing_write(dest_path, text, encoding):
            if dest_path.name == "bad.py":
                raise RuntimeError("simulated unexpected write-path crash")
            return real_write(dest_path, text, encoding)

        monkeypatch.setattr(exporter_module, "build_orchestrator", lambda *a, **kw: _PerFileValueOrchestrator())
        monkeypatch.setattr(exporter_module, "audit_masked_text", _clean_audit_stub)
        monkeypatch.setattr(exporter_module.settings.vllm, "enabled", False)
        monkeypatch.setattr(exporter_module, "write_text_preserving_encoding", _crashing_write)

        report = _run_export(source_dir, target_dir, project)

        outcomes = {o.relative_path: o.status for o in report.outcomes}
        assert outcomes["bad.py"] == "quarantined_pending_audit"
        assert outcomes["good1.py"] == "masked"
        assert outcomes["good2.py"] == "masked"
        assert (target_dir / "good1.py").exists()
        assert (target_dir / "good2.py").exists()
        assert not (target_dir / "bad.py").exists()
        bad_outcome = next(o for o in report.outcomes if o.relative_path == "bad.py")
        assert bad_outcome.error
    finally:
        _cleanup_identity(project)


# ---------------------------------------------------------------------------
# 4) Final consistency taramasi sirasinda bir dosya exception atsin.
# ---------------------------------------------------------------------------
def test_final_consistency_exception_quarantines_only_that_file(tmp_path, monkeypatch):
    """CompanySecretValue is matched in seed.py (feeds the registry). The
    consistency pass then re-scans EVERY output file for that value - one of
    them (bad.py) crashes during that scan; it must not affect good1, which
    never contains the value and is otherwise untouched."""
    project = f"{_IDENTITY_PREFIX}-consistency-crash"
    try:
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        (source_dir / "seed.py").write_text("secret = 'CompanySecretValue'\n", encoding="utf-8")
        (source_dir / "bad.py").write_text("unrelated_content = 1\n", encoding="utf-8")
        (source_dir / "good1.py").write_text("also_unrelated = 2\n", encoding="utf-8")
        target_dir = tmp_path / "target"

        real_find = exporter_module.find_consistency_occurrences

        def _crashing_find(text, registry, *, file_path=""):
            if file_path == "bad.py":
                raise RuntimeError("simulated consistency scan crash")
            return real_find(text, registry, file_path=file_path)

        monkeypatch.setattr(exporter_module, "build_orchestrator", lambda *a, **kw: _PerFileValueOrchestrator())
        monkeypatch.setattr(exporter_module, "audit_masked_text", _clean_audit_stub)
        monkeypatch.setattr(exporter_module.settings.vllm, "enabled", False)
        monkeypatch.setattr(exporter_module, "find_consistency_occurrences", _crashing_find)

        report = _run_export(source_dir, target_dir, project)

        outcomes = {o.relative_path: o.status for o in report.outcomes}
        assert outcomes["bad.py"] == "failed_consistency_validation"
        assert outcomes["seed.py"] == "masked"
        assert outcomes["good1.py"] == "copied_text_no_match"
        assert (target_dir / "seed.py").exists()
        assert (target_dir / "good1.py").exists()
        assert not (target_dir / "bad.py").exists()
        bad_outcome = next(o for o in report.outcomes if o.relative_path == "bad.py")
        assert bad_outcome.error
    finally:
        _cleanup_identity(project)


# ---------------------------------------------------------------------------
# 5) Detector exception atsin - onceki duzeltmenin adversarial dogrulamasi.
# ---------------------------------------------------------------------------
def test_detector_exception_in_any_layer_quarantines_only_that_file(tmp_path, monkeypatch):
    """Presidio/LLM/Rule katmanlarindan biri (burada orchestrator
    seviyesinde simule edildi - katman farketmeksizin AYNI izolasyon
    uygulanir, bkz. detectors.DetectionOrchestrator.scan) beklenmeyen bir
    hata atarsa: hata ilgili dosyada gorunur, dosya karantinaya alinir,
    diger dosyalar islenmeye devam eder, yarim/supheli maskelenmis dosya
    hedef pakete alinmaz."""
    project = f"{_IDENTITY_PREFIX}-detector-crash-adversarial"
    try:
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        (source_dir / "good1.py").write_text("a = 'SecretA'\n", encoding="utf-8")
        (source_dir / "bad.py").write_text("boom_trigger = 1\n", encoding="utf-8")
        (source_dir / "good2.py").write_text("c = 'SecretC'\n", encoding="utf-8")
        (source_dir / "good3.py").write_text("d = 'SecretD'\n", encoding="utf-8")
        target_dir = tmp_path / "target"

        class _CrashingLayerOrchestrator:
            async def scan(self, text, metadata=None):
                if "boom_trigger" in text:
                    raise RuntimeError("simulated Presidio/LLM internal crash")
                for value in ("SecretA", "SecretC", "SecretD"):
                    start = text.find(value)
                    if start >= 0:
                        return DetectorOutput(results=[DetectionResult(
                            deger=value, tip="S", guven_seviyesi="yuksek", kaynak_motor="dictionary",
                            start=start, end=start + len(value), rule=synthetic_llm_rule("S"),
                        )])
                return DetectorOutput()

        monkeypatch.setattr(exporter_module, "build_orchestrator", lambda *a, **kw: _CrashingLayerOrchestrator())
        monkeypatch.setattr(exporter_module.settings.vllm, "enabled", False)

        report = _run_export(source_dir, target_dir, project)

        outcomes = {o.relative_path: o.status for o in report.outcomes}
        assert outcomes["bad.py"] == "quarantined_pending_audit"
        assert outcomes["good1.py"] == "masked"
        assert outcomes["good2.py"] == "masked"
        assert outcomes["good3.py"] == "masked"
        for good in ("good1.py", "good2.py", "good3.py"):
            assert (target_dir / good).exists()
        assert not (target_dir / "bad.py").exists()
        bad_outcome = next(o for o in report.outcomes if o.relative_path == "bad.py")
        assert bad_outcome.error
        assert bad_outcome.final_state == "VALIDATION_FAILED"
    finally:
        _cleanup_identity(project)


# ---------------------------------------------------------------------------
# 6) Full multi-stage fault injection: 1 problemli dosya + birden fazla
#    saglam dosya, HER pipeline asamasinda, gercek export_project uzerinden.
# ---------------------------------------------------------------------------
def test_multi_file_fault_injection_across_pipeline_stages(tmp_path, monkeypatch):
    """1 problemli dosya (`bad.py`) + 3 saglam dosya, gercek export_project
    uzerinden, HER pipeline asamasinda (detector/mapping/audit/syntax/yazma)
    ayri ayri hata enjekte edilerek calistirilir - saglam dosyalarin HER
    durumda korundugu dogrulanir."""
    stages = ["detector", "mapping", "audit", "syntax", "write"]
    for stage in stages:
        project = f"{_IDENTITY_PREFIX}-multi-fault-{stage}"
        try:
            source_dir = tmp_path / f"src_{stage}"
            source_dir.mkdir()
            (source_dir / "good1.py").write_text("x = 'SecretA'\n", encoding="utf-8")
            (source_dir / "good2.py").write_text("y = 'SecretC'\n", encoding="utf-8")
            (source_dir / "good3.py").write_text("z = 'SecretD'\n", encoding="utf-8")
            bad_content = (
                "b = 1  # TRIGGER_BAD\n" if stage == "detector" else f"b = 'SecretB'  # {_BAD_MARKER}\n"
            )
            (source_dir / "bad.py").write_text(bad_content, encoding="utf-8")
            target_dir = tmp_path / f"target_{stage}"

            class _StageOrchestrator:
                async def scan(self, text, metadata=None):
                    if stage == "detector" and "TRIGGER_BAD" in text:
                        raise RuntimeError("simulated detector crash")
                    for value in ("SecretA", "SecretB", "SecretC", "SecretD"):
                        start = text.find(value)
                        if start >= 0:
                            return DetectorOutput(results=[DetectionResult(
                                deger=value, tip="S", guven_seviyesi="yuksek", kaynak_motor="dictionary",
                                start=start, end=start + len(value), rule=synthetic_llm_rule("S"),
                            )])
                    return DetectorOutput()

            monkeypatch.setattr(exporter_module, "build_orchestrator", lambda *a, **kw: _StageOrchestrator())
            monkeypatch.setattr(exporter_module, "audit_masked_text", _clean_audit_stub)
            monkeypatch.setattr(exporter_module.settings.vllm, "enabled", False)

            if stage == "mapping":
                from app.services import mapping_service as mapping_service_module
                real_gocm = mapping_service_module.get_or_create_mapping

                def _crashing_gocm(db, context_id, rule, original_value, cache=None, *, numeric=False, run_id=None):
                    if original_value == "SecretB":
                        raise RuntimeError("simulated mapping crash")
                    return real_gocm(db, context_id, rule, original_value, cache, numeric=numeric, run_id=run_id)

                monkeypatch.setattr(mapping_service_module, "get_or_create_mapping", _crashing_gocm)
            elif stage == "audit":
                async def _selective_audit(masked_text, vllm_settings):
                    if _BAD_MARKER in masked_text:
                        raise RuntimeError("simulated audit crash")
                    return await _clean_audit_stub(masked_text, vllm_settings)

                monkeypatch.setattr(exporter_module, "audit_masked_text", _selective_audit)
                monkeypatch.setattr(exporter_module.settings.vllm, "enabled", True)
            elif stage == "syntax":
                real_validate = exporter_module.validate_masked_syntax

                def _crashing_validate(relative_path, masked_text, original_text=None, **kwargs):
                    if relative_path == "bad.py":
                        raise RuntimeError("simulated syntax crash")
                    return real_validate(relative_path, masked_text, original_text, **kwargs)

                monkeypatch.setattr(exporter_module, "validate_masked_syntax", _crashing_validate)
            elif stage == "write":
                real_write = exporter_module.write_text_preserving_encoding

                def _crashing_write(dest_path, text, encoding):
                    if dest_path.name == "bad.py":
                        raise RuntimeError("simulated write crash")
                    return real_write(dest_path, text, encoding)

                monkeypatch.setattr(exporter_module, "write_text_preserving_encoding", _crashing_write)

            report = _run_export(source_dir, target_dir, project)

            outcomes = {o.relative_path: o.status for o in report.outcomes}
            assert outcomes["good1.py"] == "masked", f"stage={stage}: {report.summary_text()}"
            assert outcomes["good2.py"] == "masked", f"stage={stage}"
            assert outcomes["good3.py"] == "masked", f"stage={stage}"
            assert outcomes["bad.py"] != "masked", f"stage={stage}: bad.py sizinti yaptigi halde basarili sayildi"
            for good in ("good1.py", "good2.py", "good3.py"):
                assert (target_dir / good).exists(), f"stage={stage}: {good} hedefe yazilmadi"
            assert not (target_dir / "bad.py").exists(), f"stage={stage}: bad.py yanlislikla hedefe sizdi"
            assert report.status == "completed_with_warnings", f"stage={stage}: {report.status}"
        finally:
            _cleanup_identity(project)
