#!/bin/bash
# The ArkOS launchers refuse an empty or damaged program, or a missing/empty game file, with a dated line in the launch log instead of
# exiting quietly (an empty executable "runs" and exits 0). PC only.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${SGBA_BIN:-$ROOT/build/x86_64/sgba}"
T="$(mktemp -d)"; trap 'rm -rf "${T:?}"' EXIT
export SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy
fail=0; chk() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }
python3 -I - "$ROOT/../tools" "$T" <<'PY'
import re, sys
tools, out = sys.argv[1], sys.argv[2]
src = open(tools + "/build_native_bundle.py").read()
open(out + "/SGBA.sh", "w").write(re.search(r"ARKOS_LAUNCHER = r'''(.*?)'''", src, re.S).group(1).replace("@GAME@", ""))
g = open(tools + "/build_game_port.py").read()
t = re.search(r'LAUNCHER = """(.*?)"""', g, re.S).group(1).replace("@NAME@", "Test Game").replace("@SLUG@", "test").replace("\\\\", "\\")
open(out + "/Test_Game.sh", "w").write(t)
PY
mkdir -p "$T/sgba" "$T/test-game"
L="$T/sgba/sgba_launch.log"; GL="$T/test-game/launch.log"
DATE='^[0-9]\{4\}-[0-9][0-9]-[0-9][0-9] [0-9:]\{8\} '

: > "$T/sgba/sgba"; chmod +x "$T/sgba/sgba"
timeout 30 bash "$T/SGBA.sh" >/dev/null 2>&1; rc=$?
chk "an empty sgba is refused with a dated message (exit 0, nothing started)" "[ $rc = 0 ] && grep -q '$DATE.*program file is damaged (0 bytes)' $L && ! grep -q 'exit ' $L"
head -c 100000 /dev/urandom > "$T/sgba/sgba"
timeout 30 bash "$T/SGBA.sh" >/dev/null 2>&1
chk "a junk sgba is refused too" "[ \$(grep -c 'program file is damaged' $L) = 2 ]"
cp "$BIN" "$T/sgba/sgba"
timeout 30 bash "$T/SGBA.sh" "$ROOT/../web_games/slingshot_orbits" >/dev/null 2>&1 &
sleep 4; kill %1 2>/dev/null; wait 2>/dev/null
chk "a real sgba starts: the launch header carries a time" "grep -q '^=== launch [0-9]\{4\}-[0-9][0-9]-[0-9][0-9] [0-9:]\{8\} ===' $L"

timeout 30 bash "$T/Test_Game.sh" >/dev/null 2>&1; rc=$?
chk "a missing game file is refused (exit 1) with a dated line in launch.log" "[ $rc = 1 ] && grep -q '$DATE.*game file is missing or empty' $GL"
: > "$T/test-game/test.sgba"
timeout 30 bash "$T/Test_Game.sh" >/dev/null 2>&1; rc=$?
chk "an empty game file is refused too" "[ $rc = 1 ] && [ \$(grep -c 'game file is missing or empty' $GL) = 2 ]"
exit $fail
