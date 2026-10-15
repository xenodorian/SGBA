"""Render the Slingshot Orbits sound effects (launch, target hit) to Ogg files.

The browser version (slingshot_orbits_v6.html) made these live with Web Audio oscillators, which SGBA's JS host cannot
play. This reproduces the same recipes offline:

  * launch: a 0.35 s sawtooth that glides from 240 Hz to 980 Hz (exponential), 10 ms attack, exponential decay to 0.0001,
    peak gain 0.07
  * target: five triangle notes (523, 659, 784, 1047, 1319 Hz), one every 120 ms, each 0.7 s long with a 10 ms attack and
    an exponential decay, peak gain 0.14
  * the browser version also ran both through a master gain of 0.6

Each file is then peak-normalised (the originals peak near -20 dBFS, too quiet for a handheld speaker). Audio is 22.05 kHz
mono, the rate of the native mixer.

    python tools/render_slingshot_sfx.py web_games/slingshot_orbits/assets

Writes launch.ogg and target.ogg into that folder. Needs numpy and an ffmpeg with libvorbis.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np

RATE = 22050
MASTER = 0.6
PEAK = 0.6


def exp_ramp(v0: float, v1: float, n: int) -> np.ndarray:
    """Web Audio exponentialRampToValueAtTime: geometric interpolation from v0 to v1 over n samples."""
    return v0 * (v1 / v0) ** (np.arange(n) / max(1, n))


def envelope(n: int, peak: float) -> np.ndarray:
    attack = int(0.01 * RATE)
    return np.concatenate([exp_ramp(0.0001, peak, attack), exp_ramp(peak, 0.0001, n - attack)])


def saw(phase: np.ndarray, freq: np.ndarray) -> np.ndarray:
    """Band-limited sawtooth (additive), so the glide does not alias."""
    out = np.zeros_like(phase)
    for k in range(1, int(RATE / 2 / freq.min()) + 1):
        keep = k * freq < RATE / 2
        out += np.where(keep, (-1) ** (k + 1) * np.sin(k * phase) / k, 0.0)
    return out * 2 / np.pi


def launch() -> np.ndarray:
    n = int(0.35 * RATE)
    t = np.arange(n) / RATE
    freq = 240.0 * (980.0 / 240.0) ** (t / 0.35)
    phase = 2 * np.pi * np.cumsum(freq) / RATE
    return saw(phase, freq) * envelope(n, 0.07)


def target() -> np.ndarray:
    notes = (523, 659, 784, 1047, 1319)
    gap, length = int(0.12 * RATE), int(0.7 * RATE)
    out = np.zeros(gap * (len(notes) - 1) + length + int(0.05 * RATE))
    for i, f in enumerate(notes):
        phase = 2 * np.pi * f * np.arange(length) / RATE
        tri = 2 / np.pi * np.arcsin(np.sin(phase))
        out[i * gap:i * gap + length] += tri * envelope(length, 0.14)
    return out


def write(samples: np.ndarray, path: Path, quality: str) -> None:
    samples = samples * MASTER
    samples = samples * (PEAK / np.max(np.abs(samples)))
    pcm = (samples * 32767).astype("<i2").tobytes()
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "s16le", "-ar", str(RATE), "-ac", "1", "-i", "-",
           "-c:a", "libvorbis", "-q:a", quality, str(path)]
    subprocess.run(cmd, input=pcm, check=True)
    print(f"wrote {path} ({path.stat().st_size} bytes, {len(samples) / RATE:.2f} s)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--quality", default="5", help="libvorbis quality 0-10 (default 5)")
    args = ap.parse_args()
    args.folder.mkdir(parents=True, exist_ok=True)
    write(launch(), args.folder / "launch.ogg", args.quality)
    write(target(), args.folder / "target.ogg", args.quality)
    return 0


if __name__ == "__main__":
    sys.exit(main())
