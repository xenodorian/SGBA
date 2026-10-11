#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ -x ".venv/bin/python" ]; then
  exec ".venv/bin/python" runtime.py games/demo/main.py "$@"
fi
exec python3 runtime.py games/demo/main.py "$@"
