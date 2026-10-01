"""Download and lock a Windows wheelhouse, including Windows-only dependencies.

Run on an internet-connected machine with the project's Python environment.
pip cross-download alone evaluates some markers against the build host; the
metadata pass below explicitly checks the target environment and extras.
"""
from __future__ import annotations

import argparse
from email import message_from_bytes
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.tags import compatible_tags, cpython_tags
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

SERVICE = Path(__file__).resolve().parents[1]


def wheel_inventory(directory: Path, python_version: str, platform: str) -> dict:
    version = tuple(map(int, python_version.split(".")))
    abi = f"cp{version[0]}{version[1]}"
    compatible = set(cpython_tags(version, abis=[abi], platforms=[platform]))
    compatible.update(compatible_tags(version, interpreter=abi, platforms=[platform]))
    inventory = {}
    for wheel in sorted(directory.glob("*.whl")):
        name, wheel_version, _, tags = parse_wheel_filename(wheel.name)
        if not tags & compatible:
            raise ValueError(f"Target-incompatible wheel: {wheel.name}")
        if name in inventory:
            raise ValueError(f"Duplicate distribution: {name}")
        with zipfile.ZipFile(wheel) as archive:
            metadata_path = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
            metadata = message_from_bytes(archive.read(metadata_path))
        inventory[name] = {
            "version": wheel_version, "path": wheel,
            "requires_python": metadata.get("Requires-Python"),
            "requirements": [Requirement(r) for r in metadata.get_all("Requires-Dist", [])],
        }
    return inventory


def missing_dependencies(inventory: dict, python_version: str, platform: str) -> list[str]:
    from packaging.specifiers import SpecifierSet
    env = default_environment()
    env.update(python_version=python_version, python_full_version=f"{python_version}.0",
               os_name="nt", sys_platform="win32", platform_system="Windows",
               platform_machine="AMD64" if platform == "win_amd64" else "ARM64",
               implementation_name="cpython", platform_python_implementation="CPython",
               implementation_version=f"{python_version}.0", platform_release="11", platform_version="")
    extras = {name: {""} for name in inventory}
    missing = set()
    changed = True
    while changed:
        changed = False
        for name, info in inventory.items():
            if info["requires_python"] and Version(env["python_full_version"]) not in SpecifierSet(info["requires_python"]):
                raise ValueError(f"Python-incompatible distribution: {name}")
            for requirement in info["requirements"]:
                if requirement.marker and not any(requirement.marker.evaluate({**env, "extra": extra}) for extra in extras[name]):
                    continue
                dependency = canonicalize_name(requirement.name)
                installed = inventory.get(dependency)
                if installed is None or installed["version"] not in requirement.specifier:
                    # No environment markers: the target was already evaluated.
                    requested_extras = f"[{','.join(sorted(requirement.extras))}]" if requirement.extras else ""
                    missing.add(f"{requirement.name}{requested_extras}{requirement.specifier}")
                elif not requirement.extras <= extras[dependency]:
                    extras[dependency].update(requirement.extras)
                    changed = True
    return sorted(missing)


def write_lock(output: Path, inventory: dict, python_version: str, platform: str) -> None:
    lines = ["# Fully pinned offline installation; verify wheel hashes before use."]
    manifest = {"python": python_version, "platform": platform, "files": {}}
    for name, info in sorted(inventory.items()):
        with info["path"].open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        lines.append(f"{name}=={info['version']} --hash=sha256:{digest}")
        manifest["files"][info["path"].name] = digest
    (output / "requirements.lock").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def copy_project_source(output: Path) -> None:
    """Explicit source allow-list: never include .env, DBs or uploaded projects."""
    destination = output / "source" / "masking_service"
    destination.mkdir(parents=True, exist_ok=True)
    for directory in ("app", "alembic", "tests", "scripts", ".streamlit"):
        shutil.copytree(SERVICE / directory, destination / directory, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", "secrets.toml", ".env", "*.db"))
    for filename in ("api_app.py", "streamlit_app.py", "start.py", "start.ps1", "alembic.ini", "pytest.ini",
                     "requirements.txt", "requirements-intranet.txt", "requirements-validation.txt",
                     "constraints-offline-windows.txt"):
        shutil.copy2(SERVICE / filename, destination / filename)
    for filename in (".env.example", "README.md"):
        shutil.copy2(SERVICE.parent / filename, output / "source" / filename)
    # Surum damgasi: intranette git yok; backend ve preflight karisik surumu
    # bu damgayla yakalar (bkz. app/core/build_info.py).
    subprocess.run([sys.executable, str(SERVICE / "scripts" / "write_build_stamp.py"),
                    "--app-dir", str(destination / "app")], check=True)
    shutil.copy2(SERVICE / "scripts" / "OFFLINE_INSTALL.md", output / "README.md")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-version", default="3.14", choices=["3.11", "3.12", "3.13", "3.14"])
    parser.add_argument("--platform", default="win_amd64", choices=["win_amd64", "win_arm64"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validation-only", action="store_true")
    parser.add_argument("--finalize-only", action="store_true", help="No network: check existing wheels and write lock/manifest")
    args = parser.parse_args()
    output = args.output.resolve()
    wheelhouse = output / "wheels"
    wheelhouse.mkdir(parents=True, exist_ok=True)
    pip = [sys.executable, "-m", "pip", "download", "--no-cache-dir", "--only-binary=:all:",
           "--platform", args.platform, "--python-version", args.python_version,
           "--implementation", "cp", "--abi", "cp" + args.python_version.replace(".", ""),
           "--dest", str(wheelhouse)]
    if not args.finalize_only:
        requirements = SERVICE / ("requirements-validation.txt" if args.validation_only else "requirements-intranet.txt")
        command = pip + ["--find-links", str(SERVICE.parent / "deployment" / "wheels"), "-r", str(requirements)]
        if not args.validation_only:
            command += ["-c", str(SERVICE / "constraints-offline-windows.txt")]
        subprocess.run(command, check=True)
    for _ in range(8):
        inventory = wheel_inventory(wheelhouse, args.python_version, args.platform)
        if not inventory:
            raise SystemExit("No wheels found")
        missing = missing_dependencies(inventory, args.python_version, args.platform)
        if not missing:
            break
        if args.finalize_only:
            raise SystemExit("Missing target dependencies: " + ", ".join(missing))
        constraints = output / "download-constraints.txt"
        constraints.write_text("\n".join(f"{name}=={info['version']}" for name, info in sorted(inventory.items())), encoding="utf-8")
        subprocess.run(pip + ["-c", str(constraints), *missing], check=True)
    else:
        raise SystemExit("Dependency closure could not be resolved")
    write_lock(output, inventory, args.python_version, args.platform)
    for filename in ("install_offline.ps1", "verify_validation_offline.py"):
        shutil.copy2(SERVICE / "scripts" / filename, output / filename)
    copy_project_source(output)
    print(f"Validated {len(inventory)} wheels for Python {args.python_version} / {args.platform}: {output}")


if __name__ == "__main__":
    main()
