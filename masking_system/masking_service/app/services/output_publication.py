"""Stage trees, commit mappings before publication, retain a recovery journal.

SQLite and directory renames cannot share an atomic transaction. The old
tree survives until the final commit. A lock/journal survives abrupt process
termination and prevents another writer from discarding recovery data. The
writer holds an OS lock on the journal while it runs, so a journal whose lock
can be taken belongs to a dead process and is recovered automatically.
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
from app.services.file_locks import try_lock, unlock
from app.services.long_paths import extended_length_path

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


def _lock_path(target: Path) -> Path:
    return target.parent / f".{target.name}.masking.lock"


class OutputPublication:
    def __init__(self, target: Path, run_id: int):
        self.target = target
        target.parent.mkdir(parents=True, exist_ok=True)
        self.lock = _lock_path(target)
        try:
            fd = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        except FileExistsError:
            raise ValueError("Hedef baska bir islem tarafindan kilitli veya kesinti sonrasi kurtarma bekliyor.") from None
        # Gunluk, sureci boyunca bir isletim sistemi kilidiyle tutulur. Surec
        # kapaninca (cokse bile) kilit kendiliginden kalkar; kilidi alinabilen
        # bir gunluk terk edilmistir (bkz. recover_abandoned_output). Kilit
        # gunluk yazilmadan ONCE alinir: kilitsiz-ama-dolu gunluk hic gorulmez.
        self._lock_handle = os.fdopen(fd, "r+b")
        self.promoted = False
        self.old_moved = False
        self.committed = False
        try:
            if not try_lock(self._lock_handle):
                raise ValueError("Hedef baska bir islem tarafindan kilitli veya kesinti sonrasi kurtarma bekliyor.")
            self.overwritten = target.is_dir() and any(target.iterdir())
            work = Path(tempfile.mkdtemp(prefix=f".{target.name}.masking-", dir=target.parent))
            # Staging yolu hedeften ~25 karakter uzun; Windows'ta MAX_PATH'e
            # takilmamasi icin stage/backup onekli kokten turetilir. Gunluge
            # oneksiz yol yazilir (recover_output target.parent ile karsilastirir).
            self.work = extended_length_path(work)
            self.stage = self.work / "output"
            self.backup = self.work / "previous"
            self.stage.mkdir()
            journal = {"version": 1, "run_id": run_id, "target": str(target), "work": str(work),
                       "had_target": target.exists()}
            self._lock_handle.seek(0)
            self._lock_handle.write(json.dumps(journal).encode("utf-8"))
            self._lock_handle.flush()
            os.fsync(self._lock_handle.fileno())
        except BaseException:
            self.release_lock()
            self.lock.unlink(missing_ok=True)
            raise

    def release_lock(self) -> None:
        """Gunlugu diskte birakir ama kilidi birakir: islem artik calismiyor."""
        handle, self._lock_handle = self._lock_handle, None
        if handle is None:
            return
        try:
            unlock(handle)
        except OSError:
            pass
        handle.close()

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
        self.release_lock()
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
    if not application_stopped:
        raise ValueError("Kurtarma icin backend/UI ve CLI yazicilarini durdurun; --application-stopped gerekli.")
    state = recover_abandoned_output(db, target)
    if state == RECOVERY_ACTIVE:
        raise ValueError("Hedef uzerinde hala calisan bir islem var; once o islemi durdurun.")
    if state == RECOVERY_MISSING:
        raise ValueError("Gecerli kurtarma gunlugu bulunamadi; hedef degistirilmedi.")


RECOVERY_MISSING = "missing"
RECOVERY_ACTIVE = "active"
RECOVERED = "recovered"


def recover_abandoned_output(db: Session, target: Path) -> str:
    """Kilidi hicbir surecte tutulmayan (terk edilmis) gunlugu kurtarir.

    Doner: RECOVERY_MISSING (gunluk yok), RECOVERY_ACTIVE (gunluk calisan bir
    surecin kilidinde; dokunulmadi) ya da RECOVERED. Gunluk bozuksa ValueError.
    Kilit kurtarma boyunca tutulur; ayni anda ikinci bir kurtarici giremez.
    """
    target = target.resolve()
    lock = _lock_path(target)
    if lock.is_symlink():
        raise ValueError("Gecerli kurtarma gunlugu bulunamadi; hedef degistirilmedi.")
    try:
        handle = open(lock, "r+b")
    except FileNotFoundError:
        return RECOVERY_MISSING
    with handle:
        if not try_lock(handle):
            return RECOVERY_ACTIVE
        handle.seek(0)
        raw = handle.read(8193)
        if not raw:
            # Olusturulmakta ya da az once kurtarilip silinmekte olan gunluk.
            unlock(handle)
            return RECOVERY_MISSING
        _recover_journal(db, target, raw)
        # Windows acik dosyayi silemez: once bosaltilir (ikinci bir okuyucu
        # bunu "gunluk yok" sayar), sonra kilit birakilip dosya silinir.
        handle.seek(0)
        handle.truncate()
        handle.flush()
        unlock(handle)
    lock.unlink(missing_ok=True)
    return RECOVERED


def _recover_journal(db: Session, target: Path, raw: bytes) -> None:
    from app.db.models import MaskingRun
    try:
        if len(raw) > 8192:
            raise ValueError
        journal = json.loads(raw.decode("utf-8"))
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
    work = extended_length_path(work)
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
                            detail="Kesilen cikti islemi kurtarma ile geri alindi."))
            db.commit()
    if work.exists():
        shutil.rmtree(work)
