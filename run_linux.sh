#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x ".venv/bin/python" ]; then
  echo "Run ./setup_linux_pc.sh first. Do not use this setup script on the R36S yet." >&2
  exit 1
fi
exec ".venv/bin/python" runtime.py games/demo/main.py "$@"
