"""exporter.py'nin batch pipeline'inin (Faz A/C) dosyalar arasinda
GERCEKTEN `asyncio.gather` ile es zamanli calistigini - sirali (bir dosya
biterse digeri baslar) DEGIL - ve `VLLMSettings.max_concurrent_requests`
semaforunun gercekten uygulandigini kanitlayan testler.

vLLM'e gecis gerekcesi: sistem async'e gecirildi cunku LLM cagrilarinin
dosyalar arasinda gercekten es zamanli tetiklenmesi gerekiyor (bkz.
app/services/exporter.py ve app/services/llm_recognizer.py modul
dokstring'leri). Bu dosya, o iddiayi olcerek dogrular - sadece "kod async
gorunuyor" degil, "gercekten es zamanli calisiyor".
"""

from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy import text as sqltext

from app.db.session import SessionLocal
from app.services import exporter as exporter_module
from app.services.detectors import DetectorOutput
from app.services.mapping_service import get_or_create_context

_IDENTITY_PREFIX = "pytest-llm-concurrency"
_SLEEP_SECONDS = 0.2
_FILE_COUNT = 4


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
        db.execute(sqltext("DELETE FROM maskeleme_calismalari WHERE baglam_id=:c"), {"c": context_id})
        db.execute(sqltext("DELETE FROM deger_eslemeleri WHERE baglam_id=:c"), {"c": context_id})
        db.execute(sqltext("DELETE FROM maskeleme_baglamlari WHERE id=:c"), {"c": context_id})
        db.commit()


@pytest.fixture
def cleanup():
    created: list[str] = []
    yield created
    for project_name in created:
        _cleanup_identity(project_name)


def test_export_project_scans_multiple_files_llm_detection_concurrently(cleanup, tmp_path, monkeypatch):
    """Faz A (detect_matches) dosyalar arasinda SIRALI degil ES ZAMANLI
    calisir: sahte bir orchestrator.scan() her cagrida _SLEEP_SECONDS
    uyur. N dosya sirali islenseydi toplam sure N*_SLEEP_SECONDS'a yakin
    olurdu; es zamanli (batch_size >= N) islenirse tek bir sleep'e yakin
    kalir."""
    project = f"{_IDENTITY_PREFIX}-timing"
    cleanup.append(project)

    source_dir = tmp_path / "src"
    source_dir.mkdir()
    for i in range(_FILE_COUNT):
        (source_dir / f"file_{i}.txt").write_text(f"icerik {i}\n", encoding="utf-8")
    target_dir = tmp_path / "target"

    call_times: list[float] = []

    class _SlowFakeOrchestrator:
        async def scan(self, text, metadata=None):
            call_times.append(time.monotonic())
            await asyncio.sleep(_SLEEP_SECONDS)
            return DetectorOutput()

    monkeypatch.setattr(exporter_module, "build_orchestrator", lambda *a, **kw: _SlowFakeOrchestrator())
    monkeypatch.setattr(exporter_module.settings.vllm, "max_concurrent_requests", _FILE_COUNT)

    identity = (project, "P-TEST-0001", "pytest-branch")
    with SessionLocal() as db:
        get_or_create_context(db, *identity)
        db.commit()

    with SessionLocal() as db:
        started = time.monotonic()
        report = asyncio.run(
            exporter_module.export_project(
                db, source_path=str(source_dir), project_name=identity[0], sicil_no=identity[1],
                branch_name=identity[2], target_path=str(target_dir), initiated_by=identity[1],
            )
        )
        db.commit()
        elapsed = time.monotonic() - started

    assert report.files_scanned == _FILE_COUNT
    assert len(call_times) == _FILE_COUNT
    # Sirali olsaydi >= FILE_COUNT * SLEEP_SECONDS surerdi (0.8s); es
    # zamanli oldugu icin tek bir sleep'e yakin kalmali - bol bir tolerans
    # birakildi (yavas CI/donanim icin).
    assert elapsed < _FILE_COUNT * _SLEEP_SECONDS * 0.75, (
        f"Faz A sirali calismis gibi gorunuyor: {elapsed:.3f}s (beklenen ~{_SLEEP_SECONDS:.3f}s)"
    )
    # Tum cagrilar birbirine yakin zamanda BASLAMIS olmali (sirali
    # calisilsaydi cagrilar arasinda ~SLEEP_SECONDS fark olurdu).
    assert max(call_times) - min(call_times) < _SLEEP_SECONDS * 0.5


def test_bounded_limits_concurrent_execution_to_semaphore_size():
    """_bounded (Faz A/C'nin ortak semafor sarmalayicisi) ayni anda en
    fazla semaphore buyuklugu kadar coroutine'in "icerde" olmasini garanti
    eder - app/core/config.py VLLMSettings.max_concurrent_requests'in
    fiilen uygulandigini kanitlayan izole/hizli bir birim testi."""
    max_concurrent = 2
    task_count = 6
    semaphore = asyncio.Semaphore(max_concurrent)
    concurrent_count = 0
    max_observed = 0

    async def _task():
        nonlocal concurrent_count, max_observed
        concurrent_count += 1
        max_observed = max(max_observed, concurrent_count)
        await asyncio.sleep(0.02)
        concurrent_count -= 1

    async def _run():
        await asyncio.gather(*(exporter_module._bounded(_task(), semaphore) for _ in range(task_count)))

    asyncio.run(_run())

    assert max_observed == max_concurrent, f"semafor ihlal edildi: en fazla {max_observed} es zamanli calisti"


def test_next_batch_detection_overlaps_current_batch_audit(cleanup, tmp_path, monkeypatch):
    """Boru hatti: batch N+1'in tespiti, batch N'in LLM denetimi bitmeden
    baslar (eskiden her batch Faz A/C/D bariyeriyle tamamen bitiyordu).
    Cikti ve rapor yine dosya sirasiyla ve eksiksiz olmalidir."""
    project = f"{_IDENTITY_PREFIX}-pipeline"
    cleanup.append(project)

    source_dir = tmp_path / "src"
    source_dir.mkdir()
    for i in range(_FILE_COUNT):
        (source_dir / f"file_{i}.txt").write_text(f"icerik {i}\n", encoding="utf-8")
    target_dir = tmp_path / "target"
    events: list[tuple[str, str]] = []

    class _FakeOrchestrator:
        async def scan(self, text, metadata=None):
            events.append(("detect_start", text.strip()))
            await asyncio.sleep(0.01)
            return DetectorOutput()

    async def _slow_audit(masked_text, vllm_settings):
        events.append(("audit_start", masked_text.strip()))
        await asyncio.sleep(_SLEEP_SECONDS)
        events.append(("audit_end", masked_text.strip()))
        return exporter_module.AuditVerdict(risky=False)

    monkeypatch.setattr(exporter_module, "build_orchestrator", lambda *a, **kw: _FakeOrchestrator())
    monkeypatch.setattr(exporter_module, "audit_masked_text", _slow_audit)
    monkeypatch.setattr(exporter_module.settings.vllm, "max_concurrent_requests", 1)
    monkeypatch.setattr(exporter_module.settings.vllm, "file_batch_size", 2)

    identity = (project, "P-TEST-0001", "pytest-branch")
    with SessionLocal() as db:
        report = asyncio.run(
            exporter_module.export_project(
                db, source_path=str(source_dir), project_name=identity[0], sicil_no=identity[1],
                branch_name=identity[2], target_path=str(target_dir), initiated_by=identity[1],
            )
        )
        db.commit()

    assert report.files_scanned == _FILE_COUNT
    assert sorted(p.name for p in target_dir.iterdir() if p.is_file() and p.suffix == ".txt") == [
        f"file_{i}.txt" for i in range(_FILE_COUNT)
    ]
    first_batch_audit_end = events.index(("audit_end", "icerik 0"))
    second_batch_detect = events.index(("detect_start", "icerik 2"))
    assert second_batch_detect < first_batch_audit_end, events
