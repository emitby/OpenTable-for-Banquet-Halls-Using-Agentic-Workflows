#!/usr/bin/env sh
# Banquet Concierge launcher (macOS/Linux). Same commands as run.ps1; keeps caches in .cache/.
ROOT="$(cd "$(dirname "$0")" && pwd)"
PY="$ROOT/.venv/bin/python"
export PYTHONPYCACHEPREFIX="$ROOT/.cache/pycache"
export PLAYWRIGHT_BROWSERS_PATH="$ROOT/.cache/ms-playwright"
# Temporary files (e.g. Chromium's throwaway profile while building the PDF) also stay in the project.
mkdir -p "$ROOT/.cache/tmp"
export TMPDIR="$ROOT/.cache/tmp" TEMP="$ROOT/.cache/tmp" TMP="$ROOT/.cache/tmp"
cd "$ROOT" || exit 1
cmd="${1:-help}"; [ $# -gt 0 ] && shift
case "$cmd" in
  serve)           exec "$PY" -m banquet.serve "$@" ;;
  cli)             exec "$PY" -m banquet.cli "$@" ;;
  test)            exec "$PY" -m pytest -q "$@" ;;
  scenarios)       exec "$PY" -m banquet.harness.run_scenarios "$@" ;;
  eval)            exec "$PY" -m evals.run_evals "$@" ;;
  install-browser) exec "$PY" -m playwright install chromium ;;
  guide)           exec "$PY" -m banquet.guide "$@" ;;
  *) echo "usage: ./run.sh serve|cli|test|scenarios|eval|install-browser|guide" ;;
esac
