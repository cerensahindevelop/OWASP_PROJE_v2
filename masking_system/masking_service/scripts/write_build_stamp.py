"""app/BUILD_STAMP.json uretir: commit kimligi + app/ dosyalarinin SHA256 ozetleri.

Intranete kopyalanacak paket hazirlanirken (git'in oldugu makinede) calistirilir;
scripts/build_offline_bundle.py bunu otomatik yapar. Backend acilista ve
scripts/check_llm_preflight.py damgayi diskteki dosyalarla karsilastirir; uyusmazlikta
export reddedilir (bkz. app/core/build_info.py).

    python scripts/write_build_stamp.py                  (Linux/macOS)
    .venv\\Scripts\\python.exe scripts\\write_build_stamp.py   (Windows)
    python scripts/write_build_stamp.py --app-dir <paket>/source/masking_service/app
"""
from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SERVICE = Path(__file__).resolve().parents[1]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, SERVICE / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclass, modulu sys.modules'ta arar
    spec.loader.exec_module(module)
    return module


def load_build_info():
    """app/core/build_info.py'yi app paketini import etmeden (config/DB yan etkisi
    olmadan) yukler; tek bagimliligi stdlib ve app/core/exceptions.py."""
    sys.modules.setdefault("app.core.exceptions", _load("app.core.exceptions", "app/core/exceptions.py"))
    return _load("_build_info", "app/core/build_info.py")


def _dirty() -> bool:
    try:
        result = subprocess.run(["git", "status", "--porcelain", "--", "app"], cwd=SERVICE,
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--app-dir", type=Path, default=SERVICE / "app")
    parser.add_argument("--commit", help="Varsayilan: git rev-parse (yoksa 'unknown')")
    args = parser.parse_args(argv)
    build_info = load_build_info()
    commit = args.commit or build_info.git_commit(SERVICE)
    if commit and not args.commit and _dirty():
        commit += "-dirty"  # commit'lenmemis degisiklikle paketlendi
    stamp = build_info.write_stamp(
        args.app_dir.resolve(), commit=commit, built_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    print(f"stamp={args.app_dir / build_info.STAMP_NAME} commit={stamp['commit']} tree={stamp['tree']} "
          f"files={len(stamp['files'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
