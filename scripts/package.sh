#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT="$ROOT/dist/Attention-Is-All-You-Need.ankiaddon"

mkdir -p "$ROOT/dist"
rm -f "$OUTPUT"

cd "$ROOT/addon"
zip -q -r "$OUTPUT" . \
  -x '__pycache__/*' \
  -x '*.pyc'

unzip -t "$OUTPUT"
printf 'Built %s\n' "$OUTPUT"
