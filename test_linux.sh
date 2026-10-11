#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x ".venv/bin/python" ]; then
  echo "Run ./setup_linux_pc.sh first." >&2
  exit 1
fi
".venv/bin/python" -m compileall -q runtime.py games tests
exec ".venv/bin/python" -m unittest discover -s tests -v
