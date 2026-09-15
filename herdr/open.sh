#!/usr/bin/env bash
# Action entry: pick a python that can parse JSON, then open_sitting.py.
set -euo pipefail
ROOT="${HERDR_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
SCRIPT="$ROOT/herdr/open_sitting.py"
if [ -x "$ROOT/.venv/bin/python" ]; then
  exec "$ROOT/.venv/bin/python" "$SCRIPT" "$@"
fi
exec python3 "$SCRIPT" "$@"
