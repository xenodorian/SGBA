#!/bin/bash
# Slingshot Orbits sticks: the left stick (axes 0,1) aims and the right stick (axes 2,3) is the power dial. Axes are fed through the
# script language ("frame:axis:index:value"), so this covers the game's reading of the pad, not the device's axis numbering
# (the runtime logs "axis N first moved" in the report for that). PC only.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${SGBA_BIN:-$ROOT/build/x86_64/sgba}"
G="$ROOT/../web_games/slingshot_orbits"
T="$(mktemp -d)"; trap 'rm -rf "${T:?}"' EXIT
fail=0; chk() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }
shot() { SDL_VIDEODRIVER=dummy "$BIN" "$G" --frames 200 --script "$2" --js "20=document.getElementById('btn-go').click()" --shot "$T/$1.png" >/dev/null 2>&1; }
shot base 9999:a; shot down 60:axis:1:1; shot dial 60:axis:2:1; shot dial2 60:axis:3:-1
chk "left stick pulled down changes the aim" "! cmp -s $T/base.png $T/down.png"
chk "right stick right changes the power dial" "! cmp -s $T/base.png $T/dial.png"
chk "right stick up gives a different power than right" "! cmp -s $T/dial.png $T/dial2.png"
exit $fail
