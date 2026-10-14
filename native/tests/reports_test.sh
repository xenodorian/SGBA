#!/bin/bash
# Diagnostics: session lines, crash report with backtrace, "did not exit cleanly" report, errors report. PC only.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${SGBA_BIN:-$ROOT/build/x86_64/sgba}"
GAME="$ROOT/../web_games/slingshot_orbits"
T="$(mktemp -d)"; trap 'rm -rf "${T:?}"' EXIT
export SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy
cp "$BIN" "$T/sgba"; R="$T/reports"
fail=0; chk() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }

timeout 30 "$T/sgba" "$GAME" --seconds 2 >/dev/null 2>&1
chk "a played session adds one line with frames and fps" "[ \$(wc -l < $R/sessions.log) = 1 ] && grep -q 'game=slingshot_orbits .*loops=[0-9]* .*fps=[0-9.]* ' $R/sessions.log"
chk "clean exit leaves no running marker" "[ ! -e $R/.running ]"

SGBA_TEST_CRASH_AT_START=1 timeout 30 "$T/sgba" "$GAME" --seconds 2 >/dev/null 2>&1
chk "a crash writes crash-*.txt with the signal and a backtrace" "grep -q 'signal: 11' $R/crash-*.txt && grep -q 'backtrace' $R/crash-*.txt && grep -q 'sgba+0x' $R/crash-*.txt"
chk "a crash leaves no running marker (so no duplicate report)" "[ ! -e $R/.running ]"

( timeout 30 "$T/sgba" "$GAME" --seconds 30 >/dev/null 2>&1 & P=$!; sleep 2; kill -9 $P 2>/dev/null; wait $P 2>/dev/null )
chk "kill -9 leaves the marker" "[ -e $R/.running ]"
timeout 30 "$T/sgba" "$GAME" --seconds 2 >/dev/null 2>&1
chk "next start reports the unclean exit with the old marker and log tail" "grep -q 'did not exit cleanly' $R/unclean-*.txt && grep -q 'game=slingshot_orbits' $R/unclean-*.txt && grep -q 'sgba.log' $R/unclean-*.txt"

mkdir -p "$T/bad"; echo '<!doctype html><canvas id=c width=640 height=480></canvas><script>setTimeout(function(){ throw new Error("boom from the game"); }, 100);</script>' > "$T/bad/index.html"
timeout 30 "$T/sgba" "$T/bad" --seconds 2 >/dev/null 2>&1
chk "a game that throws gets an errors report with the message" "grep -q 'boom from the game' $R/errors-*.txt"
chk "sessions.log has a line per played session (3)" "[ \$(wc -l < $R/sessions.log) = 3 ]"
chk "at most 20 report files are kept" "[ \$(ls $R/*.txt | wc -l) -le 20 ]"
exit $fail
