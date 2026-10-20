#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ $EUID -ne 0 ]]; then
  echo 'Hardware validation requires root for IBS PA and pagemap PFNs.' >&2
  echo 'Run: sudo bash validate_hardware.sh [NEW_OUTPUT_DIRECTORY]' >&2
  exit 77
fi
make all
OUT="${1:-results/hardware_$(date +%Y%m%d_%H%M%S_%N)}"
python3 profile.py record --out "$OUT" --freq 2000 --focus-comm ibs-memory \
  --block-bytes 262144 -- ./build/memory_phases "$OUT/manifest.jsonl" 2
python3 profile.py validate "$OUT/samples.csv" "$OUT/manifest.jsonl" \
  --require-pa --out "$OUT/validation.json"
echo "Validated capture, CSV, VA/PA and plots: $OUT"
