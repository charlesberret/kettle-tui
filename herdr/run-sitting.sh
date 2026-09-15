#!/usr/bin/env bash
# Pane command: kettle-tui --here in this pane's cwd (herdr sets --cwd).
set -euo pipefail
ROOT="${HERDR_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
if [ -x "$ROOT/.venv/bin/kettle-tui" ]; then
  exec "$ROOT/.venv/bin/kettle-tui" --here
fi
if command -v kettle-tui >/dev/null 2>&1; then
  exec kettle-tui --here
fi
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m kettle_tui --here
