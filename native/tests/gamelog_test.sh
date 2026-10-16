#!/bin/bash
# One log per game (logs/<game>.log): every line starts with its date and time, and it records the session start and end, errors,
# crashes (with the epoch) and a previous unclean exit. Stderr lines (sgba_launch.log) carry timestamps too. PC only.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${SGBA_BIN:-$ROOT/build/x86_64/sgba}"
GAME="$ROOT/../web_games/slingshot_orbits"
T="$(mktemp -d)"; trap 'rm -rf "${T:?}"' EXIT
export SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy
cp "$BIN" "$T/sgba"; L="$T/logs/slingshot_orbits.log"; R="$T/reports"
fail=0; chk() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }
TS='^20[0-9][0-9]-[01][0-9]-[0-3][0-9] [0-2][0-9]:[0-5][0-9]:[0-5][0-9] '

timeout 30 "$T/sgba" "$GAME" --seconds 2 >"$T/err.txt" 2>&1
chk "a played game gets logs/<game>.log with a session start and a session end" "grep -q 'session start: build ' $L && grep -q 'session end (' $L"
chk "every line of the game log starts with a date and time" "[ \$(grep -vc '$TS' $L) = 0 ]"
chk "the log names the game file and its size" "grep -q 'game file slingshot_orbits' $L"
chk "stderr lines (what sgba_launch.log collects) carry a date and time" "[ \$(grep -c '$TS\\[info\\]' $T/err.txt) -ge 1 ] && [ \$(grep -vc '$TS' $T/err.txt) = 0 ]"

SGBA_TEST_CRASH_IN_SESSION=1 timeout 30 "$T/sgba" "$GAME" --seconds 2 >/dev/null 2>&1
chk "a crash in a session adds a CRASH line with the epoch to the game's log" "grep -q 'CRASH at epoch [0-9]\\{10\\}' $L"
chk "the crash report carries the crash time too" "grep -q '^crash time (epoch seconds): [0-9]\\{10\\}' $R/crash-*.txt"

( timeout 30 "$T/sgba" "$GAME" --seconds 30 >/dev/null 2>&1 & P=$!; sleep 2; kill -9 $P 2>/dev/null; wait $P 2>/dev/null )
timeout 30 "$T/sgba" "$GAME" --seconds 2 >/dev/null 2>&1
chk "after a kill the next start notes in the game's log that the session did not end cleanly" "grep -q 'previous session did not end cleanly' $L"

mkdir -p "$T/bad"; echo '<!doctype html><canvas id=c width=640 height=480></canvas><script>setTimeout(function(){ throw new Error("boom from the game"); }, 100);</script>' > "$T/bad/index.html"
timeout 30 "$T/sgba" "$T/bad" --seconds 2 >/dev/null 2>&1
chk "a game that throws gets its own log with the error line" "grep -q 'error: .*boom from the game' $T/logs/bad.log && [ \$(grep -vc '$TS' $T/logs/bad.log) = 0 ]"
exit $fail
