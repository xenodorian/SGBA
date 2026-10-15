#!/bin/bash
# A page that calls window.close() leaves the game like Quit in the pause menu: a direct launch exits at once
# (well before the --seconds limit) and the log says the game was left.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${SGBA_BIN:-$ROOT/build/x86_64/sgba}"
T="$(mktemp -d)"; trap 'rm -rf "${T:?}"' EXIT
fail=0; chk() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }
mkdir -p "$T/g"
cat > "$T/g/index.html" <<'HTML'
<!doctype html><html><body><canvas id="c" width="640" height="480"></canvas>
<script>
const ctx = document.getElementById('c').getContext('2d');
let n = 0;
function frame() { ctx.fillStyle = '#123'; ctx.fillRect(0, 0, 640, 480); if (++n === 20) window.close(); requestAnimationFrame(frame); }
requestAnimationFrame(frame);
</script></body></html>
HTML
start=$(date +%s)
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy timeout 30 "$BIN" "$T/g" --seconds 20 >"$T/log" 2>&1
rc=$?
took=$(( $(date +%s) - start ))
chk "exit code 0" "[ $rc -eq 0 ]"
chk "left well before the 20 s limit ($took s)" "[ $took -lt 10 ]"
chk "log says the game was left" "grep -q 'game left' $(dirname $BIN)/sgba.log"
exit $fail
