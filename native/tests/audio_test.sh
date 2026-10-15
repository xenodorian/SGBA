#!/bin/bash
# Music playback: a 32 kHz mono tone (the format of Parry Perry's music) is streamed through the native engine into SDL's disk audio
# driver and the captured output must be a clean 440 Hz sine with no clicks at the stream chunk seams. PC only, needs ffmpeg.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${SGBA_BIN:-$ROOT/build/x86_64/sgba}"
command -v ffmpeg >/dev/null || { echo "SKIP audio test (no ffmpeg)"; exit 0; }
T="$(mktemp -d)"; trap 'rm -rf "${T:?}"' EXIT
fail=0; chk() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }
mkdir -p "$T/g"; cp "$ROOT/tests/audio/index.html" "$T/g/"
ffmpeg -loglevel error -f lavfi -i "sine=frequency=440:sample_rate=32000:duration=60" -ac 1 -c:a libvorbis -q:a 6 "$T/g/tone.ogg"
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=disk SDL_DISKAUDIOFILE="$T/out.raw" \
  "$BIN" "$T/g" --seconds 8 >"$T/log" 2>&1
chk "audio device opened" "grep -q 'audio on' $(dirname $BIN)/sgba.log"
chk "music stream started" "tail -20 $(dirname $BIN)/sgba.log | grep -q 'music start'"
python3 -I - "$T/out.raw" <<'PY' && echo "PASS tone is continuous (no clicks at chunk seams)" || { echo "FAIL tone has discontinuities"; fail=1; }
import sys, struct, math
d = open(sys.argv[1], 'rb').read()
n = len(d) // 4
if n < 22050 * 3: print("too little audio:", n, "frames"); sys.exit(1)
L = struct.unpack('<%dh' % (n * 2), d[:n * 4])[0::2]
L = L[11025:]                                   # skip start-up
# second difference of a clean 440 Hz sine at 22.05 kHz has amplitude about A*(2*pi*440/22050)^2; a click is far above that
A = max(abs(x) for x in L)
lim = A * (2 * math.pi * 440 / 22050) ** 2 * 1.5 + 8
worst = max(abs(L[i + 1] - 2 * L[i] + L[i - 1]) for i in range(1, len(L) - 1))
print("peak %d, worst 2nd difference %d, limit %d" % (A, worst, lim))
sys.exit(0 if worst <= lim else 1)
PY
exit $fail
