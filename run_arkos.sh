#!/bin/sh
# Launcher for ArkOS-style handhelds (e.g. R36S). Uses the system python3 and
# pygame only. Installs nothing and changes nothing outside this folder.
# Usage: ./run_arkos.sh [game path or .sgba] [runtime options]
cd "$(dirname "$0")" || exit 1
LOG="sgba_launch.log"
{
  echo "SGBA launch $(date 2>/dev/null)"
  command -v python3 || echo "python3 not found"
} > "$LOG" 2>&1
if ! command -v python3 >/dev/null 2>&1; then
  echo "SGBA: python3 not found. See $LOG" >&2
  exit 1
fi
if ! python3 -c "import pygame" >> "$LOG" 2>&1; then
  echo "SGBA: pygame is not installed for system python3. See $LOG" >&2
  exit 1
fi
exec python3 runtime.py --fullscreen "$@" >> "$LOG" 2>&1
