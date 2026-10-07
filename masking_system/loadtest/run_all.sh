#!/usr/bin/env bash
# Tum yuk testi kampanyasini sirayla calistirir (yeniden baslatilabilir: DONE olan tekrarlar atlanir).
#   cd masking_system && bash loadtest/run_all.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PY=loadtest/.venv/bin/python
export LOADTEST_WORK="${LOADTEST_WORK:-/tmp/masking-loadtest-work}"
$PY -m loadtest.datasets
$PY -m loadtest.runner --plan main    --campaign loadtest/results/main
$PY -m loadtest.runner --plan workers --campaign loadtest/results/workers
$PY -m loadtest.analyze loadtest/results/main loadtest/results/workers
$PY -m loadtest.report --main loadtest/results/main --workers loadtest/results/workers --out loadtest/results/RAPOR_AYRINTILI.md
$PY -m loadtest.report_simple --main loadtest/results/main --workers loadtest/results/workers --out loadtest/results/RAPOR.md --html loadtest/results/RAPOR.html
