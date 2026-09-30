# Backend + web arayuzunu tek komutla baslatir (bkz. start.py).
#   .\start.ps1              -> normal baslatma
#   .\start.ps1 --migrate    -> once veritabani migration'ini uygular
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "Sanal ortam bulunamadi: $python - README 1. bolumdeki kurulumu tamamlayin."
    exit 1
}
& $python (Join-Path $PSScriptRoot "start.py") @args
exit $LASTEXITCODE
