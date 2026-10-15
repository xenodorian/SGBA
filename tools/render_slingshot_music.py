"""Render the Slingshot Orbits ambient music to an Ogg file.

The browser version (slingshot_orbits_v6.html) made its music live with Web Audio oscillators, which SGBA's JS host
cannot play. This script reproduces that exact recipe offline and writes a seamless loop:

  * a constant 55 Hz sine drone at gain 0.05
  * every 560 ms, a 55 percent chance of a note: octave 0 or +12 semitones, a degree of the pentatonic scale
    [0, 2, 4, 7, 9] above A3 (220 Hz), a sine that glides f -> f * 1.004 over 1.8 s with a 10 ms attack and an
    exponential decay from 0.045 to 0.0001
  * the browser version also ran everything through a master gain of 0.6

The browser picked notes with Math.random, so it never repeated. Here a seeded generator picks 120 ticks (67.2 s)
and note tails wrap around the end, so the file loops with no seam. 67.2 s is exactly 3696 cycles of the 55 Hz drone.
The result is peak-normalised, because the original levels (about -20 dBFS peak) are too quiet for a handheld speaker.

    python tools/render_slingshot_music.py web_games/slingshot_orbits/assets/slingshot_music.ogg

Needs numpy and an ffmpeg with libvorbis.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np

RATE = 22050                      # the native mixer runs at 22.05 kHz, so no resampling happens on the device
TICK = 0.560                      # seconds between note chances (the browser's setInterval(ambientNote, 560))
TICKS = 120                       # 120 * 0.56 = 67.2 s
NOTE_CHANCE = 0.55                # the browser skipped a tick when Math.random() < 0.45
SCALE = (0, 2, 4, 7, 9)
NOTE_LEN = 1.8
NOTE_VOL = 0.045
DRONE_HZ, DRONE_VOL = 55.0, 0.05
MASTER = 0.6
PEAK = 0.55                       # normalised peak of the written file
SEED = 20261014


def exp_ramp(v0: float, v1: float, n: int) -> np.ndarray:
    """Web Audio exponentialRampToValueAtTime: geometric interpolation from v0 to v1 over n samples."""
    return v0 * (v1 / v0) ** (np.arange(n) / max(1, n))


def note(freq: float) -> np.ndarray:
    n = int(NOTE_LEN * RATE)
    t = np.arange(n) / RATE
    # exponential frequency glide f -> f * 1.004 across the note; integrate it to get phase
    inst = freq * 1.004 ** (t / NOTE_LEN)
    phase = 2 * np.pi * np.cumsum(inst) / RATE
    attack = int(0.01 * RATE)
    env = np.concatenate([exp_ramp(0.0001, NOTE_VOL, attack), exp_ramp(NOTE_VOL, 0.0001, n - attack)])
    return np.sin(phase) * env


def render() -> np.ndarray:
    total = int(round(TICK * TICKS * RATE))
    rng = np.random.RandomState(SEED)
    out = np.sin(2 * np.pi * DRONE_HZ * np.arange(total) / RATE) * DRONE_VOL
    for i in range(TICKS):
        if rng.random_sample() < 1 - NOTE_CHANCE:
            continue
        octave = 0 if rng.random_sample() < 0.5 else 12
        degree = SCALE[int(rng.random_sample() * len(SCALE))]
        n = note(220.0 * 2 ** ((degree + octave) / 12))
        start = int(round(i * TICK * RATE))
        idx = (start + np.arange(len(n))) % total            # wrap the tail over the start for a seamless loop
        np.add.at(out, idx, n)
    out *= MASTER
    return out * (PEAK / np.max(np.abs(out)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("output", type=Path)
    ap.add_argument("--quality", default="6", help="libvorbis quality 0-10 (default 6, which keeps the file above the 96 KB the native player needs to stream it)")
    args = ap.parse_args()
    pcm = (render() * 32767).astype("<i2").tobytes()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "s16le", "-ar", str(RATE), "-ac", "1", "-i", "-",
           "-c:a", "libvorbis", "-q:a", args.quality, str(args.output)]
    subprocess.run(cmd, input=pcm, check=True)
    print(f"wrote {args.output} ({args.output.stat().st_size} bytes, {TICK * TICKS:.1f} s, {RATE} Hz mono)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
