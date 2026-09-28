param(
    [string]$Python = "python",
    [string]$ServicePath = (Join-Path $PSScriptRoot "source/masking_service"),
    [string]$VenvPath = (Join-Path $ServicePath ".venv")
)
$ErrorActionPreference = "Stop"
$manifestPath = Join-Path $PSScriptRoot "manifest.json"
$wheelhouse = Join-Path $PSScriptRoot "wheels"
$lock = Join-Path $PSScriptRoot "requirements.lock"
if (!(Test-Path (Join-Path $ServicePath "app/services/syntax_validator.py"))) {
    throw "Project source missing. Set -ServicePath to the updated masking_service directory."
}

$verify = @'
import hashlib, json, pathlib, platform, sys
p = pathlib.Path(sys.argv[1])
m = json.loads(p.read_text(encoding="utf-8"))
expected = tuple(map(int, m["python"].split(".")))
if sys.version_info[:2] != expected or sys.platform != "win32":
    raise SystemExit("This bundle requires Windows / Python " + m["python"])
machine = platform.machine().lower()
allowed = {"win_amd64": {"amd64", "x86_64"}, "win_arm64": {"arm64", "aarch64"}}
if machine not in allowed[m["platform"]]:
    raise SystemExit("CPU architecture does not match bundle target")
for name, digest in m["files"].items():
    with (p.parent / "wheels" / name).open("rb") as f:
        if hashlib.file_digest(f, "sha256").hexdigest() != digest:
            raise SystemExit("Wheel checksum mismatch: " + name)
print("Target and wheel checksums verified")
'@
& $Python -c $verify $manifestPath
if ($LASTEXITCODE -ne 0) { throw "Bundle preflight failed" }
if (!(Test-Path (Join-Path $VenvPath "Scripts/python.exe"))) {
    & $Python -m venv $VenvPath
    if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed" }
}
$venvPython = Join-Path $VenvPath "Scripts/python.exe"
& $venvPython -c $verify $manifestPath
if ($LASTEXITCODE -ne 0) { throw "Existing virtual environment has the wrong Python/CPU target" }
& $venvPython -m pip install --no-index --no-cache-dir --only-binary=:all: --find-links $wheelhouse --require-hashes -r $lock
if ($LASTEXITCODE -ne 0) { throw "Offline installation failed" }
& $venvPython -m pip check
if ($LASTEXITCODE -ne 0) { throw "Dependency check failed" }
& $venvPython (Join-Path $PSScriptRoot "verify_validation_offline.py") --service-path $ServicePath
if ($LASTEXITCODE -ne 0) { throw "Offline parser smoke test failed" }
Write-Host "Offline dependencies and parsers are ready. Configure source/.env, then apply migrations."
