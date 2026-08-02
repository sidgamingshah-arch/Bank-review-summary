#!/usr/bin/env bash
# Build an OFFLINE install kit (a Python wheelhouse) for an air-gapped bank server.
#
# Run this on an INTERNET-CONNECTED build host whose operating system, CPU
# architecture and Python version MATCH the target server (wheels for packages
# with C extensions — cryptography, psycopg, aiohttp, azure — are platform
# specific). Then copy deploy/wheelhouse/ together with this repository to the
# air-gapped server and follow deploy/OFFLINE_INSTALL.md.
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # repo root

OUT="deploy/wheelhouse"
rm -rf "$OUT"; mkdir -p "$OUT"

python -m pip install --upgrade pip wheel >/dev/null

echo "• downloading pinned dependency wheels into $OUT ..."
python -m pip download --prefer-binary -r deploy/requirements.lock.txt -d "$OUT"

echo "• building the CAM platform wheel ..."
python -m pip wheel --no-deps . -w "$OUT"

echo
echo "✔ wheelhouse ready: $OUT ($(ls "$OUT" | wc -l) files, $(du -sh "$OUT" | cut -f1))"
echo "  Ship deploy/wheelhouse/ + this repo to the server, then see deploy/OFFLINE_INSTALL.md"
echo "  (Optionally build the web UI here too: cd frontend && npm ci && npm run build,"
echo "   and copy frontend/dist to the server so the gateway can serve it.)"
