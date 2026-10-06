"""exporter.py'nin 'bir dosyanin veya detectorin basarisiz olmasi sessizce
masking basarisi olarak kabul edilmemeli' ilkesini gercekten uyguladigini
kanitlayan testler:

  A) Katman 3 (LLM) tespiti bir dosya icin basarisiz olursa, o dosya
     (baska katmanlar bir sey bulmus olsa bile) sessizce "masked"/
     "copied_text_no_match" sayilmaz - karantinaya alinir.
  B) Hicbir primary detector eslesme bulamayan (0 mapping) bir dosya da
     YINE DE bagimsiz ikinci-gecis (second-pass) denetiminden gecer - bu
     denetim risk bulursa dosya karantinaya alinir (eskiden bu dosyalar
     denetim hic calismadan doğrudan basariya sayiliyordu).
  C) Presidio'nun kurulumu basarisiz olup fallback moda dustugu bir
     calisma, "tamamlandi" degil "uyarili tamamlandi" olarak isaretlenir
     ve rapor/denetim kaydinda acikca gorunur.
"""

from __future__ import annotations

import asyncio
import re

import pytest

from sqlalchemy import text as sqltext

from app.db.session import SessionLocal
from app.services import exporter as exporter_module
from app.services.detectors import DetectionResult, DetectorOutput, synthetic_llm_rule
from app.services.mapping_service import get_or_create_context

_IDENTITY_PREFIX = "pytest-failure-handling"


def test_parser_fallback_is_visible_in_export_report_and_audit(tmp_path, monkeypatch):
    from app.services import syntax_parsers
    project = f"{_IDENTITY_PREFIX}-parser-fallback"
    try:
        source = tmp_path / "src"
        source.mkdir()
        (source / "app.ts").write_text("const value = 1;", encoding="utf-8")
        target = tmp_path / "target"

        class NoOpOrchestrator:
            async def scan(self, text, metadata=None):
                return DetectorOutput(results=[], errors=[])

        def missing_parser(suffix):
            raise ImportError("test missing parser")

        monkeypatch.setattr(exporter_module, "build_orchestrator", lambda *a, **kw: NoOpOrchestrator())
        monkeypatch.setattr(exporter_module.settings.vllm, "enabled", False)
        monkeypatch.setattr(syntax_parsers, "_script_parser", missing_parser)
        report = _run_export(source, target, project)
        assert (target / "app.ts").exists()
        assert report.status == "completed_with_warnings"
        assert report.validation_warnings
        assert "bracket/quote" in report.summary_text()
        with SessionLocal() as db:
            details = db.execute(sqltext("SELECT detay FROM denetim_kaydi WHERE calisma_id=:r"), {"r": report.run_id}).scalars().all()
            assert any("validation_warning" in detail for detail in details)
    finally:
        _cleanup_identity(project)


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
        get_or_create_context(db, *identity)
        db.commit()
    with SessionLocal() as db:
        report = asyncio.run(
            exporter_module.export_project(
                db, source_path=str(source_dir), project_name=identity[0], sicil_no=identity[1],
                branch_name=identity[2], target_path=str(target_dir), initiated_by=identity[1],
            )
        )
        db.commit()
    return report


def test_llm_detection_failure_quarantines_file_even_with_other_matches(tmp_path, monkeypatch):
    """A: Katman 1/2 bir sey bulmus olsa bile, Katman 3 (LLM) o dosya icin
    basarisiz olduysa dosya SESSIZCE 'masked' sayilip disa aktarilmamali."""
    project = f"{_IDENTITY_PREFIX}-llm-fail"
    try:
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        (source_dir / "app.py").write_text("value = 1\n", encoding="utf-8")
        target_dir = tmp_path / "target"

        dictionary_hit = DetectionResult(
            deger="1", tip="NUMBER", guven_seviyesi="yuksek", kaynak_motor="dictionary",
            start=8, end=9, rule=synthetic_llm_rule("NUMBER"),
        )

        class _LLMFailingOrchestrator:
            async def scan(self, text, metadata=None):
                return DetectorOutput(results=[dictionary_hit], errors=["LLM taramasi basarisiz (simulated)"])

        monkeypatch.setattr(exporter_module, "build_orchestrator", lambda *a, **kw: _LLMFailingOrchestrator())
        monkeypatch.setattr(exporter_module.settings.vllm, "enabled", True)
        # The detector failure must quarantine even if the independent audit
        # succeeds. Keep this regression independent of a live vLLM server.
        async def _clean_audit(*args, **kwargs):
            from app.services.audit_reviewer import AuditVerdict
            return AuditVerdict(risky=False)

        monkeypatch.setattr(exporter_module, "audit_masked_text", _clean_audit)

        report = _run_export(source_dir, target_dir, project)

        assert report.files_quarantined_pending_audit == 1, report.summary_text()
        assert report.status == "completed_with_warnings"
        assert not (target_dir / "app.py").exists(), "LLM basarisiz olan dosya YANLISLIKLA disa aktarildi"
    finally:
        _cleanup_identity(project)


def test_llm_detection_failure_is_ignored_when_llm_globally_disabled(tmp_path, monkeypatch):
    """LLM katmani zaten VLLM_ENABLED=false ile tamamen kapaliysa (varsayilan
    dagitim durumu), o katmandan gelen bir 'hata' anlamsizdir ve dosyayi
    karantinaya almamali - aksi halde LLM hic kullanilmayan kurulumlarda
    HER dosya gereksiz yere karantinaya duserdi."""
    project = f"{_IDENTITY_PREFIX}-llm-disabled"
    try:
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        (source_dir / "clean.py").write_text("value = 1\n", encoding="utf-8")
        target_dir = tmp_path / "target"

        class _NoOpOrchestrator:
            async def scan(self, text, metadata=None):
                return DetectorOutput(results=[], errors=[])

        monkeypatch.setattr(exporter_module, "build_orchestrator", lambda *a, **kw: _NoOpOrchestrator())
        monkeypatch.setattr(exporter_module.settings.vllm, "enabled", False)

        report = _run_export(source_dir, target_dir, project)

        assert report.files_quarantined_pending_audit == 0
        assert (target_dir / "clean.py").exists()
    finally:
        _cleanup_identity(project)


def test_zero_match_file_still_goes_through_second_pass_audit_and_can_be_quarantined(tmp_path, monkeypatch):
    """B: primary detectorlarin HICBIR SEY bulamadigi (0 mapping) bir dosya
    da second-pass audit'ten gecmeli - bu, bir detectorin gercekte bir seyi
    kacirdigi TAM OLARAK bu senaryodur. Audit risk bulursa dosya
    karantinaya alinmali, sessizce 'eslesme yok, basarili' sayilmamali."""
    project = f"{_IDENTITY_PREFIX}-zero-match-audit"
    try:
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        (source_dir / "notes.md").write_text("Bu dosyada hicbir regex/presidio eslesmesi yok.\n", encoding="utf-8")
        target_dir = tmp_path / "target"

        class _EmptyOrchestrator:
            async def scan(self, text, metadata=None):
                return DetectorOutput(results=[], errors=[])

        audit_calls = []

        async def _fake_audit(masked_text, vllm_settings):
            audit_calls.append(masked_text)
            from app.services.audit_reviewer import AuditFinding, AuditVerdict

            return AuditVerdict(
                risky=True,
                findings=[AuditFinding(aciklama="LLM bir ipucu buldu (test)", ilgili_bolum="notes.md")],
            )

        monkeypatch.setattr(exporter_module, "build_orchestrator", lambda *a, **kw: _EmptyOrchestrator())
        monkeypatch.setattr(exporter_module, "audit_masked_text", _fake_audit)
        monkeypatch.setattr(exporter_module.settings.vllm, "enabled", True)

        report = _run_export(source_dir, target_dir, project)

        assert len(audit_calls) == 1, "0-eslesmeli dosya second-pass audit'ten HIC gecmedi"
        assert report.files_quarantined_pending_audit == 1, report.summary_text()
        assert not (target_dir / "notes.md").exists()
    finally:
        _cleanup_identity(project)


def test_zero_match_file_is_exported_when_second_pass_audit_is_clean(tmp_path, monkeypatch):
    """Supported text with no matches still passes independent LLM audit."""
    project = f"{_IDENTITY_PREFIX}-zero-match-clean"
    try:
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        (source_dir / "notes.md").write_text("Tamamen zararsiz bir metin.\n", encoding="utf-8")
        target_dir = tmp_path / "target"

        class _EmptyOrchestrator:
            async def scan(self, text, metadata=None):
                return DetectorOutput(results=[], errors=[])

        async def _fake_audit(masked_text, vllm_settings):
            from app.services.audit_reviewer import AuditVerdict
            return AuditVerdict(risky=False)

        monkeypatch.setattr(exporter_module, "build_orchestrator", lambda *a, **kw: _EmptyOrchestrator())
        monkeypatch.setattr(exporter_module, "audit_masked_text", _fake_audit)
        monkeypatch.setattr(exporter_module.settings.vllm, "enabled", True)

        report = _run_export(source_dir, target_dir, project)

        assert report.files_quarantined_pending_audit == 0
        assert report.files_copied_text_no_match == 1
        assert (target_dir / "notes.md").exists()
    finally:
        _cleanup_identity(project)


@pytest.mark.parametrize("token", [
    "MAX_LOGIN_TEST_3", "service_test_1", "MASK_EMAIL_7", "mask_email_7suffix",
    "811199000000123", "9111990000000123",
])
def test_lookalike_placeholder_identifier_does_not_fail_round_trip(tmp_path, monkeypatch, token):
    """An ordinary identifier that merely happens to match our OWN
    placeholder grammar (legacy PREFIX_TEST_N shape, e.g. a constant named
    MAX_LOGIN_TEST_3) is detected as "already placeholder-formatted" and
    left untouched by design (see rule_engine.PLACEHOLDER_RE). It must not
    then fail the automatic round-trip self-check as an "unresolved
    placeholder" - nothing was masked there, so nothing needs to resolve;
    the real sensitive value elsewhere in the file must still mask fine."""
    project = f"{_IDENTITY_PREFIX}-lookalike-placeholder"
    try:
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        original = f'const sentinel = "{token}";\nconst contact = "foo@example.com";\n'
        (source_dir / "config.ts").write_text(original, encoding="utf-8")
        target_dir = tmp_path / "target"

        class _EmailOnlyOrchestrator:
            """Mirrors what RuleBasedDetector really does: report the email
            match AND flag the pre-existing placeholder-shaped identifier as
            already-masked (untouched) - see rule_engine.find_matches_compiled."""

            async def scan(self, text, metadata=None):
                from app.services.rule_engine import PLACEHOLDER_RE

                start = text.index("foo@example.com")
                hit = DetectionResult(
                    deger="foo@example.com", tip="EMAIL", guven_seviyesi="yuksek", kaynak_motor="dictionary",
                    start=start, end=start + len("foo@example.com"), rule=synthetic_llm_rule("EMAIL"),
                )
                already_masked = [(m.start(), m.end()) for m in PLACEHOLDER_RE.finditer(text)]
                return DetectorOutput(results=[hit], already_masked_spans=already_masked)

        monkeypatch.setattr(exporter_module, "build_orchestrator", lambda *a, **kw: _EmailOnlyOrchestrator())
        monkeypatch.setattr(exporter_module.settings.vllm, "enabled", False)

        report = _run_export(source_dir, target_dir, project)

        assert report.files_failed_round_trip_validation == 0, report.summary_text()
        assert report.status == "completed"
        masked = (target_dir / "config.ts").read_text(encoding="utf-8")
        assert token in masked
        assert "foo@example.com" not in masked
        # Verify actual unmask as well as the export-time self-check. The
        # authenticated source record proves these unknown tokens were source
        # content, not lost mappings for newly generated placeholders.
        from app.services.unmasker import unmask_project

        restored_dir = tmp_path / "restored"
        with SessionLocal() as db:
            restored = unmask_project(
                db, source_path=str(target_dir), target_path=str(restored_dir),
                project_name=project, sicil_no="P-TEST-0001", branch_name="pytest-branch",
                initiated_by="P-TEST-0001",
            )
            db.commit()
        assert not restored.has_unresolved_placeholders
        assert restored.status == "completed"
        assert (restored_dir / "config.ts").read_bytes() == original.encode("utf-8")
    finally:
        _cleanup_identity(project)


def test_lookalike_passthrough_does_not_hide_a_genuinely_corrupt_mapping(tmp_path, monkeypatch):
    """Adversarial companion to the lookalike test above: a lookalike
    identifier sits in the SAME file as a real match, but this time the
    mapping actually created for the real match is corrupted (simulating an
    internal bug - e.g. a hash collision writing the wrong decrypted
    value). The round-trip self-check must still catch and quarantine this,
    proving the passthrough fix is purely additive and cannot mask a real
    failure just because a harmless lookalike is also present."""
    project = f"{_IDENTITY_PREFIX}-lookalike-plus-real-corruption"
    try:
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        (source_dir / "config.ts").write_text(
            "const MAX_LOGIN_TEST_3 = 5;\nconst contact = \"foo@example.com\";\n",
            encoding="utf-8",
        )
        target_dir = tmp_path / "target"

        class _EmailOnlyOrchestrator:
            async def scan(self, text, metadata=None):
                from app.services.rule_engine import PLACEHOLDER_RE

                start = text.index("foo@example.com")
                hit = DetectionResult(
                    deger="foo@example.com", tip="EMAIL", guven_seviyesi="yuksek", kaynak_motor="dictionary",
                    start=start, end=start + len("foo@example.com"), rule=synthetic_llm_rule("EMAIL"),
                )
                already_masked = [(m.start(), m.end()) for m in PLACEHOLDER_RE.finditer(text)]
                return DetectorOutput(results=[hit], already_masked_spans=already_masked)

        monkeypatch.setattr(exporter_module, "build_orchestrator", lambda *a, **kw: _EmailOnlyOrchestrator())
        monkeypatch.setattr(exporter_module.settings.vllm, "enabled", False)

        # Corrupt the mapping AFTER it is created for the real match, so the
        # encrypted value no longer matches what was actually replaced in
        # the text - simulating a genuine storage/consistency bug.
        from app.services import mapping_service as mapping_service_module
        from app.core.crypto import encrypt_value

        real_get_or_create = mapping_service_module.get_or_create_mapping

        def _corrupting_get_or_create(db, context_id, rule, original_value, cache=None, *, numeric=False, run_id=None):
            mapping, created = real_get_or_create(db, context_id, rule, original_value, cache, numeric=numeric, run_id=run_id)
            if original_value == "foo@example.com":
                mapping.original_value_encrypted = encrypt_value("WRONG-VALUE-DOES-NOT-MATCH")
            return mapping, created

        # apply_detections (called from exporter._apply_masking) resolves
        # get_or_create_mapping through mapping_service's own module globals,
        # not through exporter's re-exported name - patch it there.
        monkeypatch.setattr(mapping_service_module, "get_or_create_mapping", _corrupting_get_or_create)

        report = _run_export(source_dir, target_dir, project)

        assert report.files_failed_round_trip_validation == 1, report.summary_text()
        assert not (target_dir / "config.ts").exists(), "bozuk mapping'e ragmen dosya yanlislikla disa aktarildi"
    finally:
        _cleanup_identity(project)


def test_detector_exception_quarantines_only_that_file(tmp_path, monkeypatch):
    """A detector layer raising an unexpected exception on ONE file (a
    Presidio/spaCy internal bug, a regex blowing up on pathological input,
    etc.) must not cancel the whole asyncio.gather batch and lose every
    OTHER file's already-correct work - only the crashing file may be
    quarantined."""
    project = f"{_IDENTITY_PREFIX}-detector-crash"
    try:
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        (source_dir / "good1.py").write_text("value = 1\n", encoding="utf-8")
        (source_dir / "bad.py").write_text("boom = 1\n", encoding="utf-8")
        (source_dir / "good2.py").write_text("value = 2\n", encoding="utf-8")
        target_dir = tmp_path / "target"

        class _CrashingOrchestrator:
            async def scan(self, text, metadata=None):
                if "boom" in text:
                    raise RuntimeError("simulated detector crash")
                return DetectorOutput()

        monkeypatch.setattr(exporter_module, "build_orchestrator", lambda *a, **kw: _CrashingOrchestrator())
        monkeypatch.setattr(exporter_module.settings.vllm, "enabled", False)

        report = _run_export(source_dir, target_dir, project)

        outcomes = {o.relative_path: o.status for o in report.outcomes}
        assert outcomes["bad.py"] == "quarantined_pending_audit"
        assert outcomes["good1.py"] == "copied_text_no_match"
        assert outcomes["good2.py"] == "copied_text_no_match"
        assert (target_dir / "good1.py").exists()
        assert (target_dir / "good2.py").exists()
        assert not (target_dir / "bad.py").exists()
    finally:
        _cleanup_identity(project)


def test_mapping_creation_exception_quarantines_only_that_file(tmp_path, monkeypatch):
    """An unexpected exception while writing a mapping for ONE file's real
    match (simulating a DB/encryption bug) must roll back only that file's
    partial work via a savepoint - other files in the same batch, already
    correctly masked, must still be exported and committed."""
    project = f"{_IDENTITY_PREFIX}-mapping-crash"
    try:
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        (source_dir / "a_good.py").write_text("x = 'secret1'\n", encoding="utf-8")
        (source_dir / "b_bad.py").write_text("y = 'secret2'\n", encoding="utf-8")
        target_dir = tmp_path / "target"

        class _TwoSecretOrchestrator:
            async def scan(self, text, metadata=None):
                for value in ("secret1", "secret2"):
                    if value in text:
                        start = text.index(value)
                        return DetectorOutput(results=[DetectionResult(
                            deger=value, tip="S", guven_seviyesi="yuksek", kaynak_motor="dictionary",
                            start=start, end=start + len(value), rule=synthetic_llm_rule("S"),
                        )])
                return DetectorOutput()

        from app.services import mapping_service as mapping_service_module

        real_get_or_create = mapping_service_module.get_or_create_mapping

        def _crashing_get_or_create(db, context_id, rule, original_value, cache=None, *, numeric=False, run_id=None):
            if original_value == "secret2":
                raise RuntimeError("simulated mapping write failure")
            return real_get_or_create(db, context_id, rule, original_value, cache, numeric=numeric, run_id=run_id)

        monkeypatch.setattr(exporter_module, "build_orchestrator", lambda *a, **kw: _TwoSecretOrchestrator())
        monkeypatch.setattr(exporter_module.settings.vllm, "enabled", False)
        monkeypatch.setattr(mapping_service_module, "get_or_create_mapping", _crashing_get_or_create)

        report = _run_export(source_dir, target_dir, project)

        outcomes = {o.relative_path: o.status for o in report.outcomes}
        assert outcomes["a_good.py"] == "masked"
        assert outcomes["b_bad.py"] == "quarantined_pending_audit"
        masked_a = (target_dir / "a_good.py").read_text(encoding="utf-8")
        assert re.fullmatch(r"x = 'mask_s_\d+'\n", masked_a), masked_a
        assert not (target_dir / "b_bad.py").exists()
    finally:
        _cleanup_identity(project)


def test_degraded_presidio_marks_run_as_completed_with_warnings(tmp_path, monkeypatch):
    """C: Presidio kurulumu basarisiz olup (fallback moda dustugunde), bu
    hicbir yerde raporlanmadan calisma 'basarili' sayilmamali."""
    project = f"{_IDENTITY_PREFIX}-presidio-degraded"
    try:
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        (source_dir / "clean.py").write_text("deger = 1\n", encoding="utf-8")
        target_dir = tmp_path / "target"

        # AnalyzerEngine'i None yaparak _build_analyzer'in "optional
        # dependency absent" erken-cikisini (is_degraded=True) tetikler -
        # gercek kurulum hatasiyla AYNI sonuc (self._analyzer is None).
        monkeypatch.setattr("app.services.presidio_detector.AnalyzerEngine", None)

        report = _run_export(source_dir, target_dir, project)

        assert report.degraded_detectors == ["katman2_presidio"]
        assert report.has_degraded_detectors is True
        assert report.status == "completed_with_warnings"
        assert "katman2_presidio" in report.summary_text()
    finally:
        _cleanup_identity(project)


def _letters(n, width):
    # Satir kimligi harflerle: yalnizca rakamlari farkli satirlar LLM icin ayni sayilir.
    out = ""
    for _ in range(width):
        n, r = divmod(n, 26)
        out = "abcdefghijklmnopqrstuvwxyz"[r] + out
    return out

@pytest.mark.parametrize('failed_phase', ['detection', 'audit'])
def test_later_llm_chunk_failure_is_quarantined_not_published(tmp_path, monkeypatch, failed_phase):
    """Exercise real chunk runners all the way through the publication gate."""
    import json
    from app.services import llm_recognizer, audit_reviewer
    from app.services.llm_detector import LLMDetector

    project = f'{_IDENTITY_PREFIX}-chunk-{failed_phase}'
    counts = {'detection': 0, 'audit': 0}
    try:
        source = tmp_path / 'source'
        source.mkdir()
        (source / 'big.txt').write_text(''.join(f'public text {_letters(i, 4)}\n' for i in range(1500)), encoding='utf-8')
        target = tmp_path / 'target'
        s = exporter_module.settings.vllm
        monkeypatch.setattr(s, 'enabled', True)
        # .env icerigine bagimli olmasin: gercek istek zaten sahte call_vllm'e gider.
        monkeypatch.setattr(s, 'host', 'http://fake-llm')
        monkeypatch.setattr(s, 'model', 'fake-model')
        monkeypatch.setattr(s, 'transient_retries', 0)
        monkeypatch.setattr(s, 'max_file_chars', 6000)
        monkeypatch.setattr(s, 'max_concurrent_requests', 1)

        async def fake(host, timeout, payload, api_key=None):
            phase = 'audit' if payload['response_format']['json_schema']['name']=='denetim_semasi' else 'detection'
            counts[phase] += 1
            if phase == failed_phase and counts[phase] == 2:
                raise llm_recognizer.LLMRecognitionError('simulated chunk timeout')
            data = {'bulgular': []}
            if phase == 'audit':
                data['risk_var'] = False
            return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(data)}}]}

        class DetectorOrchestrator:
            async def scan(self, text, metadata=None):
                return await LLMDetector(s).detect(text, metadata)

        monkeypatch.setattr(exporter_module, 'build_orchestrator', lambda *a, **k: DetectorOrchestrator())
        monkeypatch.setattr(llm_recognizer, 'call_vllm', fake)
        monkeypatch.setattr(audit_reviewer, 'call_vllm', fake)
        report = _run_export(source, target, project)
        assert counts[failed_phase] == 2  # No repeat of the completed first chunk.
        assert report.files_quarantined_pending_audit == 1
        assert report.outcomes[0].final_state == 'VALIDATION_FAILED'
        assert not (target / 'big.txt').exists()
    finally:
        _cleanup_identity(project)


# Asama 8: yapi saglam ama tek bulgu bozuksa (liste/dict guven) deger metinde
# dogrulandigi icin orta/KURUMSAL_TANIMLAYICI ile maskelenir; yanit yapisi
# bozuksa denetim temiz dese bile dosya eskisi gibi karantinaya alinir.
@pytest.mark.parametrize("confidence", [["yuksek"], {"level": "yuksek"}, "broken_structure"])
def test_malformed_llm_confidence_quarantines_even_when_audit_succeeds(tmp_path, monkeypatch, confidence):
    import json
    from app.services import llm_recognizer, audit_reviewer
    from app.services.detectors import DetectorRegistry, DetectionOrchestrator
    from app.services.llm_detector import LLMDetector

    project = f"{_IDENTITY_PREFIX}-malformed-confidence"
    try:
        source = tmp_path / "source"
        source.mkdir()
        (source / "bad.txt").write_text("SYNTHETIC_VALUE", encoding="utf-8")
        (source / "good.txt").write_text("public text", encoding="utf-8")
        target = tmp_path / "target"
        s = exporter_module.settings.vllm
        monkeypatch.setattr(s, "enabled", True)
        monkeypatch.setattr(s, "host", "http://llm.test")
        monkeypatch.setattr(s, "model", "test")
        audit_calls = []

        async def fake(host, timeout, payload, api_key=None):
            is_audit = payload["response_format"]["json_schema"]["name"] == "denetim_semasi"
            data = {"bulgular": []}
            if is_audit:
                audit_calls.append(1)
                data["risk_var"] = False
            elif "SYNTHETIC_VALUE" in payload["messages"][1]["content"]:
                if confidence == "broken_structure":
                    data["bulgular"] = "SYNTHETIC_VALUE"
                    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(data)}}]}
                data["bulgular"] = [{"bulunan_deger": "SYNTHETIC_VALUE", "tip": "TEST",
                                     "guven_seviyesi": confidence, "gerekce": "test"}]
            return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(data)}}]}

        registry = DetectorRegistry()
        registry.register(LLMDetector(s))
        monkeypatch.setattr(exporter_module, "build_orchestrator", lambda *a, **k: DetectionOrchestrator(registry))
        monkeypatch.setattr(llm_recognizer, "call_vllm", fake)
        monkeypatch.setattr(audit_reviewer, "call_vllm", fake)
        report = _run_export(source, target, project)
        outcomes = {out.relative_path: out for out in report.outcomes}
        assert audit_calls
        assert (target / "good.txt").read_text(encoding="utf-8") == "public text"
        if confidence != "broken_structure":
            assert report.files_quarantined_pending_audit == 0, report.summary_text()
            assert outcomes["bad.txt"].final_state == "READY"
            masked = (target / "bad.txt").read_text(encoding="utf-8")
            assert "SYNTHETIC_VALUE" not in masked and masked.startswith("mask_")
            return
        assert report.files_quarantined_pending_audit == 1
        assert report.status == "completed_with_warnings"
        assert outcomes["bad.txt"].final_state == "VALIDATION_FAILED"
        assert "bulgular" in outcomes["bad.txt"].error
        assert "TypeError" not in outcomes["bad.txt"].error
        assert "SYNTHETIC_VALUE" not in outcomes["bad.txt"].error
        assert not (target / "bad.txt").exists()
    finally:
        _cleanup_identity(project)


def test_audit_finding_in_collapsed_lines_remediates_every_digit_variant(tmp_path, monkeypatch):
    """Tespit kod adini kacirsa bile denetim tek varyanti gordugunde, atlanan
    benzer satirlardaki tum varyantlar otomatik duzeltilir; insan onayi gerekmez."""
    import json
    from app.services import llm_recognizer, audit_reviewer
    from app.services.llm_detector import LLMDetector

    project = f'{_IDENTITY_PREFIX}-digit-variants'
    try:
        source = tmp_path / 'source'
        source.mkdir()
        lines = ''.join(
            f'2026-10-05 08:{(i // 60) % 60:02d}:{i % 60:02d} INFO deploy PRJ-ALFA-{i % 13} tamam\n'
            for i in range(1500)
        )
        (source / 'app.log').write_text(lines, encoding='utf-8')
        target = tmp_path / 'target'
        s = exporter_module.settings.vllm
        monkeypatch.setattr(s, 'enabled', True)
        monkeypatch.setattr(s, 'host', 'http://fake-llm')
        monkeypatch.setattr(s, 'model', 'fake-model')
        monkeypatch.setattr(s, 'transient_retries', 0)
        monkeypatch.setattr(s, 'max_file_chars', 6000)
        monkeypatch.setattr(s, 'max_concurrent_requests', 1)
        audit_calls = []

        async def fake(host, timeout, payload, api_key=None):
            chunk = payload['messages'][-1]['content']
            if payload['response_format']['json_schema']['name'] != 'denetim_semasi':
                data = {'bulgular': []}  # tespit kod adini kaciriyor
            else:
                audit_calls.append(chunk)
                line = next((l for l in chunk.splitlines() if 'PRJ-ALFA-' in l), None)
                code = line.split('deploy ')[1].split(' ')[0] if line else None
                data = {'risk_var': bool(code),
                        'bulgular': [{'ilgili_bolum': code, 'aciklama': 'kod adi'}] if code else []}
            return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(data)}}]}

        class DetectorOrchestrator:
            async def scan(self, text, metadata=None):
                return await LLMDetector(s).detect(text, metadata)

        monkeypatch.setattr(exporter_module, 'build_orchestrator', lambda *a, **k: DetectorOrchestrator())
        monkeypatch.setattr(llm_recognizer, 'call_vllm', fake)
        monkeypatch.setattr(audit_reviewer, 'call_vllm', fake)
        report = _run_export(source, target, project)

        assert report.files_quarantined_pending_audit == 0
        assert report.outcomes[0].final_state == 'READY'
        output = (target / 'app.log').read_text(encoding='utf-8')
        assert 'PRJ-ALFA' not in output  # hicbir varyant acik kalmadi
        assert len(audit_calls) <= 4  # 1500 satir her turda tek parca denetlendi
    finally:
        _cleanup_identity(project)
