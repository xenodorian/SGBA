#!/bin/bash
# Memory and background work: canvases the page drops are freed (a page that makes a canvas every frame must not grow without
# bound), and the output is identical with the read-ahead image worker and the canvas release switched off. PC only.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${SGBA_BIN:-$ROOT/build/x86_64/sgba}"
export SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy
T="$(mktemp -d)"; trap 'rm -rf "${T:?}"' EXIT
fail=0; chk() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }

peak=0
"$BIN" "$ROOT/tests/leak" --frames 3000 >/dev/null 2>&1 &
P=$!
while kill -0 $P 2>/dev/null; do r=$(awk '/VmRSS/{print int($2/1024)}' /proc/$P/status 2>/dev/null); [ -n "$r" ] && [ "$r" -gt "$peak" ] && peak=$r; sleep 0.2; done
chk "3000 frames of a new 400x300 canvas per frame peak under 200 MB (was 1.4 GB: $peak MB)" "[ $peak -lt 200 ]"

SEED='0=(function(){var s=12345;Math.random=function(){s|=0;s=s+0x6D2B79F5|0;var t=Math.imul(s^s>>>15,1|s);t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296;};})()'
for game in slingshot_orbits demo input_lab; do
  SGBA_NO_CANVAS_RELEASE=1 SGBA_NO_READAHEAD=1 SGBA_THREADS=1 "$BIN" "$ROOT/../web_games/$game" --frames 600 --script "30:a,60:right,90:a" --js "$SEED" --shot "$T/a.png" >/dev/null 2>&1
  "$BIN" "$ROOT/../web_games/$game" --frames 600 --script "30:a,60:right,90:a" --js "$SEED" --shot "$T/b.png" >/dev/null 2>&1
  chk "$game: same final frame with read-ahead and canvas release on or off" "cmp -s $T/a.png $T/b.png"
done

# first-use order file: written on exit for games in the cache folder, used by the worker next run
mkdir -p "$T/cache/g"; cp "$ROOT/../web_games/slingshot_orbits/"* "$T/cache/g/" 2>/dev/null
"$BIN" "$T/cache/g" --frames 60 >/dev/null 2>&1
chk "a game in the cache folder runs and leaves no order file when it uses no image files" "[ ! -s $T/cache/g/.image-order ]"
exit $fail
