"""Stage trees, commit mappings before publication, retain a recovery journal.

SQLite and directory renames cannot share an atomic transaction. The old
tree survives until the final commit. A lock/journal survives abrupt process
termination and prevents another writer from discarding recovery data.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import logging

from sqlalchemy.orm import Session
from app.db.models import AuditLog

logger = logging.getLogger(__name__)

# Windows: antivirus real-time scanning or OneDrive's Known Folder Backup can
# transiently hold a freshly-written file/directory open, making rename()
# fail with PermissionError (WinError 5) even though nothing is actually
# wrong. POSIX rename() has no such window, so this retry is a no-op there.
_RENAME_RETRY_ATTEMPTS = 5
_RENAME_RETRY_DELAY_SECONDS = 0.2


def _rename_with_retry(src: Path, dst: Path) -> None:
    for attempt in range(_RENAME_RETRY_ATTEMPTS):
        try:
            src.rename(dst)
            return
        except PermissionError:
            if attempt == _RENAME_RETRY_ATTEMPTS - 1:
                raise
            time.sleep(_RENAME_RETRY_DELAY_SECONDS * (attempt + 1))


class OutputPublication:
    def __init__(self, target: Path, run_id: int):
        self.target = target
        target.parent.mkdir(parents=True, exist_ok=True)
        self.lock = target.parent / f".{target.name}.masking.lock"
        try:
            fd = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise ValueError("Hedef baska bir islem tarafindan kilitli veya kesinti sonrasi kurtarma bekliyor.") from None
        self.promoted = False
        self.old_moved = False
        self.committed = False
        try:
            self.overwritten = target.is_dir() and any(target.iterdir())
            self.work = Path(tempfile.mkdtemp(prefix=f".{target.name}.masking-", dir=target.parent))
            self.stage = self.work / "output"
            self.backup = self.work / "previous"
            self.stage.mkdir()
            journal = {"version": 1, "run_id": run_id, "target": str(target), "work": str(self.work),
                       "had_target": target.exists()}
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(journal, stream)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            self.lock.unlink(missing_ok=True)
            raise

    def promote(self) -> None:
        if self.target.exists():
            _rename_with_retry(self.target, self.backup)
            self.old_moved = True
        _rename_with_retry(self.stage, self.target)
        self.promoted = True

    def sync(self) -> None:
        # Flush staged bytes before the durable mapping commit. Recovery
        # still depends on the filesystem's directory-rename durability.
        for path in self.stage.rglob("*"):
            if path.is_file():
                with path.open("r+b") as stream:
                    os.fsync(stream.fileno())

    def restore(self) -> None:
        if self.promoted:
            _rename_with_retry(self.target, self.stage)
            self.promoted = False
        if self.old_moved:
            _rename_with_retry(self.backup, self.target)
            self.old_moved = False

    def close(self) -> None:
        # Never discard the previous tree if restoration itself failed.
        if (self.old_moved or self.promoted) and not self.committed:
            return
        try:
            shutil.rmtree(self.work)
            self.lock.unlink(missing_ok=True)
        except OSError:
            # Output is already committed. Keep any remaining recovery data
            # and expose the cleanup failure rather than claiming rollback.
            logger.warning("Cikti kurtarma dosyalari temizlenemedi; recover-output ile kontrol edin.")


def publish_run(db: Session, run, report, publication: OutputPublication) -> None:
    """Own the final transaction boundary; callers may safely commit again."""
    final_status = report.status
    if publication.overwritten and getattr(report, "files_errored", 0):
        raise OSError("Dosya okuma/yazma hatalari var; onceki hedef eksik ciktiyla degistirilmedi.")
    run.status = "in_progress"
    publication.sync()
    db.commit()
    try:
        publication.promote()
        run.status = final_status
        db.commit()
    except BaseException:
        db.rollback()
        publication.restore()
        run.status = "failed"
        db.add(AuditLog(run_id=run.id, file_path="", action="error",
                        detail="Cikti yayimlanamadi; onceki hedef korundu. Eslemeler kurtarma icin saklandi."))
        db.commit()
        raise
    publication.committed = True
    report.target_overwritten = publication.overwritten


def recover_output(db: Session, target: Path, *, application_stopped: bool = False) -> None:
    """Offline recovery. Never steal a lock from a possibly active writer."""
    from app.db.models import MaskingRun
    if not application_stopped:
        raise ValueError("Kurtarma icin backend/UI ve CLI yazicilarini durdurun; --application-stopped gerekli.")
    target = target.resolve()
    lock = target.parent / f".{target.name}.masking.lock"
    try:
        if lock.is_symlink() or lock.stat().st_size > 8192:
            raise ValueError
        journal = json.loads(lock.read_text(encoding="utf-8"))
        work = Path(journal["work"])
        if (journal["version"] != 1 or journal["target"] != str(target)
            or work.parent != target.parent or work.is_symlink()
            or not work.name.startswith(f".{target.name}.masking-")
            or not isinstance(journal["had_target"], bool)):
            raise ValueError
    except (OSError, KeyError, ValueError, TypeError):
        raise ValueError("Gecerli kurtarma gunlugu bulunamadi; hedef degistirilmedi.") from None
    run = db.get(MaskingRun, journal["run_id"])
    if run is not None and Path(run.target_path).resolve() != target:
        raise ValueError("Kurtarma gunlugu islem kaydiyla eslesmiyor.")
    backup, stage = work / "previous", work / "output"
    if backup.is_symlink() or stage.is_symlink():
        raise ValueError("Kurtarma klasorunde guvensiz baglanti bulundu.")
    completed = run is not None and run.status in ("completed", "completed_with_warnings")
    if completed:
        if not target.is_dir():
            raise ValueError("Tamamlanmis islemin hedefi eksik; kurtarma dosyalari korundu.")
    else:
        if backup.exists():
            if target.exists():
                _rename_with_retry(target, work / "interrupted-output")
            _rename_with_retry(backup, target)
        elif not stage.exists() and target.exists():
            if journal["had_target"]:
                raise ValueError("Onceki hedefin yedegi eksik; manuel inceleme gerekli.")
            _rename_with_retry(target, work / "interrupted-output")
        if run is not None:
            run.status = "failed"
            db.add(AuditLog(run_id=run.id, file_path="", action="error",
                            detail="Kesilen cikti islemi offline kurtarma ile geri alindi."))
            db.commit()
    if work.exists():
        shutil.rmtree(work)
    lock.unlink()
