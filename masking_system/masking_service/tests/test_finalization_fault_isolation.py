"""Finalize adimi (chmod + butunluk/manifest kaydi - export_project'in
consistency pass'ten SONRA, output_files uzerinde calisan son dongusu) icin
dosya-bazli hata izolasyonu.

Bu noktaya gelen bir dosya butun icerik dogrulamalarindan (syntax/round-trip/
consistency/audit) zaten BASARIYLA gecmistir - burada SADECE izin (chmod) ve
butunluk kaydi (manifest) metaverisi yaziliyor. Yine de dosya-basina
BEKLENMEYEN bir hata (orn. chmod/digest OSError'i) tum digerlerinin zaten
basarili islerini coktermemeli: ayni invariant, Faz A/B/C/D'de zaten
dogrulanan ilkenin devami.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sqlalchemy import text as sqltext

from app.db.session import SessionLocal
from app.services import exporter as exporter_module
from app.services.detectors import DetectionResult, DetectorOutput, synthetic_llm_rule

_IDENTITY_PREFIX = "pytest-finalization-isolation"


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
    async def scan(self, text, metadata=None):
        for value in ("SecretA", "SecretB", "SecretC", "SecretD", "SecretE"):
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


def _read_manifest_files(target_dir: Path) -> dict:
    raw = json.loads((target_dir / ".masking-integrity.json").read_text())
    return raw["payload"]["files"]


def _setup_common(monkeypatch):
    monkeypatch.setattr(exporter_module, "build_orchestrator", lambda *a, **kw: _PerFileValueOrchestrator())
    monkeypatch.setattr(exporter_module, "audit_masked_text", _clean_audit_stub)
    monkeypatch.setattr(exporter_module.settings.vllm, "enabled", False)


# ---------------------------------------------------------------------------
# 1) Tek dosyada chmod exception.
# ---------------------------------------------------------------------------
def test_chmod_exception_quarantines_only_that_file(tmp_path, monkeypatch):
    project = f"{_IDENTITY_PREFIX}-chmod-crash"
    try:
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        (source_dir / "good1.py").write_text("a = 'SecretA'\n", encoding="utf-8")
        (source_dir / "bad.py").write_text("b = 'SecretB'\n", encoding="utf-8")
        (source_dir / "good2.py").write_text("c = 'SecretC'\n", encoding="utf-8")
        target_dir = tmp_path / "target"

        _setup_common(monkeypatch)
        real_chmod = Path.chmod

        def _crashing_chmod(self, mode):
            if self.name == "bad.py":
                raise RuntimeError("simulated chmod crash")
            return real_chmod(self, mode)

        monkeypatch.setattr(Path, "chmod", _crashing_chmod)

        report = _run_export(source_dir, target_dir, project)

        outcomes = {o.relative_path: o.status for o in report.outcomes}
        assert outcomes["bad.py"] == "failed_finalization"
        assert outcomes["good1.py"] == "masked"
        assert outcomes["good2.py"] == "masked"
        assert (target_dir / "good1.py").exists()
        assert (target_dir / "good2.py").exists()
        assert not (target_dir / "bad.py").exists(), "guvensiz/yarim dosya hedefte kalmamali"
        bad_outcome = next(o for o in report.outcomes if o.relative_path == "bad.py")
        assert bad_outcome.error
        assert bad_outcome.final_state == "VALIDATION_FAILED"
        assert report.files_failed_finalization == 1
        assert report.status == "completed_with_warnings"
        assert "Sorun yok" not in report.summary_text()

        manifest_files = _read_manifest_files(target_dir)
        assert set(manifest_files.keys()) == {"good1.py", "good2.py"}
    finally:
        _cleanup_identity(project)


# ---------------------------------------------------------------------------
# 2) Tek dosyada manifest/final metadata (sha256 digest) exception.
# ---------------------------------------------------------------------------
def test_manifest_metadata_exception_quarantines_only_that_file(tmp_path, monkeypatch):
    project = f"{_IDENTITY_PREFIX}-manifest-crash"
    try:
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        (source_dir / "good1.py").write_text("a = 'SecretA'\n", encoding="utf-8")
        (source_dir / "bad.py").write_text("b = 'SecretB'\n", encoding="utf-8")
        (source_dir / "good2.py").write_text("c = 'SecretC'\n", encoding="utf-8")
        target_dir = tmp_path / "target"

        _setup_common(monkeypatch)
        real_digest = exporter_module.file_digest

        def _crashing_digest(path):
            if path.name == "bad.py":
                raise OSError("simulated digest/read crash")
            return real_digest(path)

        monkeypatch.setattr(exporter_module, "file_digest", _crashing_digest)

        report = _run_export(source_dir, target_dir, project)

        outcomes = {o.relative_path: o.status for o in report.outcomes}
        assert outcomes["bad.py"] == "failed_finalization"
        assert outcomes["good1.py"] == "masked"
        assert outcomes["good2.py"] == "masked"
        assert (target_dir / "good1.py").exists()
        assert (target_dir / "good2.py").exists()
        assert not (target_dir / "bad.py").exists()

        manifest_files = _read_manifest_files(target_dir)
        assert set(manifest_files.keys()) == {"good1.py", "good2.py"}
        assert json.loads((target_dir / ".masking-integrity.json").read_text())["payload"]["complete"] is False
    finally:
        _cleanup_identity(project)


# ---------------------------------------------------------------------------
# 3) Hata dosya HEDEFE YAZILDIKTAN SONRA olursa hedefte guvensiz/yarim
#    dosya kalmamali (finalize hatasi, icerik zaten diskte olsa bile).
# ---------------------------------------------------------------------------
def test_failure_after_write_leaves_no_unsafe_file_in_target(tmp_path, monkeypatch):
    project = f"{_IDENTITY_PREFIX}-post-write-crash"
    try:
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        (source_dir / "good1.py").write_text("a = 'SecretA'\n", encoding="utf-8")
        (source_dir / "bad.py").write_text("b = 'SecretB'\n", encoding="utf-8")
        target_dir = tmp_path / "target"

        _setup_common(monkeypatch)
        # bad.py's masked content is ALREADY written to the (staged) output
        # by Faz D by the time finalize runs (finalize is a separate, later
        # pass over already-written output_files). Crashing here proves the
        # file gets removed again, not merely left alone. Scoped to the
        # SOURCE bad.py path's SECOND stat() call: the first is Faz A's own
        # size check (read_scanned_file, before anything is written), the
        # second is finalize's `(source / rel).stat()` for the chmod mode -
        # by construction strictly after Faz D already wrote the file.
        real_stat = Path.stat
        source_bad_path = source_dir / "bad.py"
        call_count = {"n": 0}

        def _crashing_stat(self, *args, **kwargs):
            # Exclude lstat()-style calls (follow_symlinks=False, used by
            # the initial directory scan's is_symlink() check) - only count
            # "real" stat() calls like the size check and finalize's mode read.
            if self == source_bad_path and kwargs.get("follow_symlinks", True) is not False:
                call_count["n"] += 1
                if call_count["n"] >= 2:
                    raise OSError("simulated source stat crash")
            return real_stat(self, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", _crashing_stat)

        report = _run_export(source_dir, target_dir, project)

        assert not (target_dir / "bad.py").exists(), "yazildiktan sonra basarisiz olan dosya hedefte kalmamali"
        assert (target_dir / "good1.py").exists()
        outcomes = {o.relative_path: o.status for o in report.outcomes}
        assert outcomes["bad.py"] == "failed_finalization"
        assert outcomes["good1.py"] == "masked"
    finally:
        _cleanup_identity(project)


# ---------------------------------------------------------------------------
# 4) Gercek export_project uzerinden coklu dosya: N saglam + 1 problemli,
#    kac saglam dosyanin output'a ciktigi acikca dogrulanir.
# ---------------------------------------------------------------------------
def test_multi_file_finalization_fault_injection_real_pipeline(tmp_path, monkeypatch):
    project = f"{_IDENTITY_PREFIX}-multi-file-real"
    try:
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        good_files = {
            "good1.py": "SecretA", "good2.py": "SecretB", "good3.py": "SecretC",
            "good4.py": "SecretD", "good5.py": "SecretE",
        }
        for name, value in good_files.items():
            (source_dir / name).write_text(f"x = '{value}'\n", encoding="utf-8")
        (source_dir / "bad.py").write_text("y = 'SecretF'\n", encoding="utf-8")
        target_dir = tmp_path / "target"

        class _AllValuesOrchestrator:
            async def scan(self, text, metadata=None):
                for value in ("SecretA", "SecretB", "SecretC", "SecretD", "SecretE", "SecretF"):
                    start = text.find(value)
                    if start >= 0:
                        return DetectorOutput(results=[DetectionResult(
                            deger=value, tip="S", guven_seviyesi="yuksek", kaynak_motor="dictionary",
                            start=start, end=start + len(value), rule=synthetic_llm_rule("S"),
                        )])
                return DetectorOutput()

        monkeypatch.setattr(exporter_module, "build_orchestrator", lambda *a, **kw: _AllValuesOrchestrator())
        monkeypatch.setattr(exporter_module, "audit_masked_text", _clean_audit_stub)
        monkeypatch.setattr(exporter_module.settings.vllm, "enabled", False)

        real_chmod = Path.chmod

        def _crashing_chmod(self, mode):
            if self.name == "bad.py":
                raise RuntimeError("simulated finalize crash")
            return real_chmod(self, mode)

        monkeypatch.setattr(Path, "chmod", _crashing_chmod)

        report = _run_export(source_dir, target_dir, project)

        # Explicitly verify exactly how many good files made it to output -
        # never assume; count and compare against the expected 5.
        present_good_files = [name for name in good_files if (target_dir / name).exists()]
        assert len(present_good_files) == 5, (
            f"beklenen 5 saglam dosyadan sadece {len(present_good_files)} tanesi hedefte: "
            f"{present_good_files} - eksik olanlar: {set(good_files) - set(present_good_files)}\n"
            f"{report.summary_text()}"
        )
        assert not (target_dir / "bad.py").exists()

        outcomes = {o.relative_path: o.status for o in report.outcomes}
        for name in good_files:
            assert outcomes[name] == "masked", f"{name}: {outcomes[name]}"
        assert outcomes["bad.py"] == "failed_finalization"

        # Mapping DB tutarli: TUM 6 deger icin (bad.py'nin SecretF'i DAHIL -
        # mapping'in kendisi gecerli, sadece dosya CIKTIYA alinmadi) satir var,
        # hayalet/eksik mapping yok.
        with SessionLocal() as db:
            row = db.execute(
                sqltext("SELECT id FROM maskeleme_baglamlari WHERE proje_adi=:p"), {"p": project}
            ).first()
            mapping_count = db.execute(
                sqltext("SELECT count(*) FROM deger_eslemeleri WHERE baglam_id=:c"), {"c": row[0]}
            ).scalar()
            assert mapping_count == 6
            audit_rows = db.execute(
                sqltext(
                    "SELECT detay FROM denetim_kaydi WHERE calisma_id=(SELECT id FROM maskeleme_calismalari "
                    "WHERE baglam_id=:c) AND dosya_yolu='bad.py' AND eylem='error'"
                ),
                {"c": row[0]},
            ).all()
            assert audit_rows, "bad.py icin hata nedeni AuditLog'a kaydedilmemis"

        assert report.status == "completed_with_warnings"
        assert report.files_failed_finalization == 1
    finally:
        _cleanup_identity(project)
