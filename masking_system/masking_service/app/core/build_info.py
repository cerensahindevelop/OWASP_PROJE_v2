"""Calisan kodun surum damgasi ve karisik surum (kismi kopyalama) kontrolu.

TypeError olayi (diagnostics/llm-typeerror-20260928) intranette kismen
kopyalanmis ya da yeniden baslatilmamis kodla yasandi. Intranet kurulumu
flash bellekten kopyalandigi icin orada `.git` yok; bu yuzden paketleme
sirasinda `app/BUILD_STAMP.json` uretilir (scripts/write_build_stamp.py,
scripts/build_offline_bundle.py): commit kimligi + app/ altindaki her
dosyanin SHA256 ozeti. Calisma aninda ozetler diskle karsilastirilir.

Durumlar:
- ok: damga var ve diskteki dosyalarla birebir uyusuyor.
- no_stamp: damga yok (gelistirme kopyasi). Yalnizca uyari; git varsa
  commit oradan okunur.
- mismatch: damgadaki bir dosya degismis ya da eksik. Export uc noktalari
  reddeder (fail-closed), health ve preflight bunu gosterir.
Damgada olmayan fazladan dosyalar (eski surumden kalmis) yalnizca
raporlanir: yeni kod onlari import etmedigi icin davranisi degistirmez.

Ozet hesaplanirken satir sonlari CRLF -> LF normalize edilir; boylece
Windows'a kopyalanmis ya da autocrlf'li bir checkout ayni sonucu verir.
Yalnizca stdlib kullanir ve import edildiginde yan etkisi yoktur (preflight
bu modulu dosya yolundan yukler).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.core.exceptions import BuildMismatchError

STAMP_NAME = "BUILD_STAMP.json"
STAMP_VERSION = 1
APP_DIR = Path(__file__).resolve().parents[1]
_SKIP_DIRS = {"__pycache__"}
_SKIP_SUFFIXES = {".pyc", ".pyo"}


@dataclass(frozen=True)
class BuildStatus:
    state: str  # ok | no_stamp | mismatch
    commit: str
    source: str  # stamp | git | none
    tree: str
    mismatched: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    extra: tuple[str, ...] = field(default=())

    @property
    def blocks_export(self) -> bool:
        return self.state == "mismatch"

    def line(self) -> str:
        return (
            f"build state={self.state} commit={self.commit} source={self.source} tree={self.tree} "
            f"mismatched={len(self.mismatched)} missing={len(self.missing)} extra={len(self.extra)}"
        )

    def message(self) -> str:
        names = ", ".join((*self.mismatched, *self.missing)[:10])
        return (
            "Calisan kod karisik surumde: app/ klasorundeki dosyalar surum damgasiyla "
            f"(BUILD_STAMP.json, commit {self.commit}) uyusmuyor ({names}). Dışa aktarma "
            "güvenlik nedeniyle durduruldu. app/ klasörünün tamamını aynı paketten yeniden "
            "kopyalayıp backend'i yeniden başlatın, ardından scripts/check_llm_preflight.py çalıştırın."
        )

    def as_dict(self) -> dict:
        return {
            "state": self.state, "commit": self.commit, "source": self.source, "tree": self.tree,
            "mismatched": list(self.mismatched), "missing": list(self.missing), "extra": list(self.extra),
        }


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def tree_digests(app_dir: Path = APP_DIR) -> dict[str, str]:
    digests = {}
    for path in sorted(app_dir.rglob("*")):
        rel = path.relative_to(app_dir)
        if (not path.is_file() or path.name == STAMP_NAME or path.suffix in _SKIP_SUFFIXES
                or any(part in _SKIP_DIRS for part in rel.parts)):
            continue
        digests[rel.as_posix()] = file_digest(path)
    return digests


def tree_digest(digests: dict[str, str]) -> str:
    joined = "".join(f"{name}:{digest}\n" for name, digest in sorted(digests.items()))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]


def git_commit(directory: Path = APP_DIR) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"], cwd=directory, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit = result.stdout.strip()
    return commit if result.returncode == 0 and commit else None


def write_stamp(app_dir: Path = APP_DIR, *, commit: str | None = None, built_at: str | None = None) -> dict:
    digests = tree_digests(app_dir)
    stamp = {
        "version": STAMP_VERSION,
        "commit": commit or git_commit(app_dir) or "unknown",
        "built_at": built_at,
        "tree": tree_digest(digests),
        "files": digests,
    }
    (app_dir / STAMP_NAME).write_text(json.dumps(stamp, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return stamp


def check_build(app_dir: Path = APP_DIR) -> BuildStatus:
    digests = tree_digests(app_dir)
    tree = tree_digest(digests)
    stamp_path = app_dir / STAMP_NAME
    if not stamp_path.is_file():
        commit = git_commit(app_dir)
        return BuildStatus("no_stamp", commit or "unknown", "git" if commit else "none", tree)
    try:
        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
        expected = dict(stamp["files"])
        commit = str(stamp.get("commit") or "unknown")
    except (OSError, ValueError, KeyError, TypeError):
        # Okunamayan damga belirsiz bir durumdur: fail-closed.
        return BuildStatus("mismatch", "unknown", "stamp", tree, mismatched=(STAMP_NAME,))
    mismatched = tuple(sorted(n for n, d in expected.items() if n in digests and digests[n] != d))
    missing = tuple(sorted(n for n in expected if n not in digests))
    extra = tuple(sorted(n for n in digests if n not in expected))
    state = "mismatch" if mismatched or missing else "ok"
    return BuildStatus(state, commit, "stamp", tree, mismatched, missing, extra)


# Surec basina bir kez hesaplanir: export reddi ve health ayni sonucu gorur.
@lru_cache(maxsize=1)
def current_build_status() -> BuildStatus:
    return check_build(APP_DIR)


# Export oncesi kontrol (fail-closed): acilistaki durum karisik surumse ya da
# app/ dosyalari surec basladiktan sonra degistiyse (yeni kod kopyalandi ama
# backend yeniden baslatilmadi; bellekteki kod diskteki koddan farkli) reddeder.
def ensure_export_allowed() -> None:
    started = current_build_status()
    if started.blocks_export:
        raise BuildMismatchError(started.message())
    if tree_digest(tree_digests(APP_DIR)) != started.tree:
        raise BuildMismatchError(
            "app/ klasöründeki kod backend başladıktan sonra değişti; çalışan süreç eski kodu "
            "kullanıyor. Dışa aktarma güvenlik nedeniyle durduruldu: backend'i yeniden başlatın."
        )
