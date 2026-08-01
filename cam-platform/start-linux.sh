#!/usr/bin/env bash
# ============================================================================
#  CAM Studio - single-command Linux/macOS launcher.
#  Run:  ./start-linux.sh
#  On first run it creates a Python virtualenv, builds the web UI, seeds demo
#  data, starts every service, and opens http://localhost:8080. Subsequent runs
#  start in seconds. Prerequisites: Python 3.10+ (required) and Node.js 18+
#  (for the web UI). Press Ctrl-C to stop the platform.
# ============================================================================
set -euo pipefail

# work from the repo root (this script's directory), regardless of caller CWD
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# pick a real Python 3.10+ interpreter
PYEXE=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)' >/dev/null 2>&1; then
    PYEXE="$c"; break
  fi
done
if [ -z "$PYEXE" ]; then
  echo
  echo "  Python 3.10+ was not found on PATH."
  echo "  Install it (e.g. 'sudo apt install python3 python3-venv' or 'brew install python') and re-run."
  echo
  exit 1
fi

exec "$PYEXE" scripts/launch.py
