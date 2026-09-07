#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT="$ROOT/dist/Attention-Is-All-You-Need.ankiaddon"

mkdir -p "$ROOT/dist"
rm -f "$OUTPUT"

cd "$ROOT/addon"
FILES=(
  "__init__.py"
  "attention_dashboard.html"
  "attention_dashboard.py"
  "config.json"
  "config.md"
  "dashboard_actions.py"
  "dashboard_window.py"
  "data.py"
  "fsrs_adapter.py"
  "manifest.json"
  "settings.py"
)

zip -q "$OUTPUT" "${FILES[@]}"

unzip -t "$OUTPUT"
printf 'Built %s\n' "$OUTPUT"
