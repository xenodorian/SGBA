"""Draw the reference scenes with the Python canvas and the C canvas and compare them.

    python tools/parity.py GAME_FOLDER [--out DIR] [--scenes title level1 ...] [--tolerance 8]

Scenes and seed come from the game's own sgba/pixel_check.py. Text is expected to differ a little
(stb_truetype against SDL_ttf), so the report counts pixels whose channels differ by more than the tolerance and
shows the share of the picture, plus a diff image per scene.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import pygame

ROOT = Path(__file__).resolve().parent.parent
SEED = "0=(function(){let s=12345;Math.random=function(){s=(s*1664525+1013904223)>>>0;return s/4294967296;};})()"
START = "30=document.getElementById('btn-start').click()"
UNLOCK = "60=P.unlockAllLevels();bibooGame.unlock('laser_beam')"
SHOTS = {
    "title": (200, "", []),
    "level1": (300, "150:hold:right:60", [START, UNLOCK, "90=bibooGame.enterLevel(1)"]),
    "level1b": (420, "150:hold:right:60,300:tap:a:20", [START, UNLOCK, "90=bibooGame.enterLevel(1)", "100=bibooGame.setMeters(100,100,100)"]),
    "level3": (360, "150:hold:right:90,260:tap:a:12", [START, UNLOCK, "90=bibooGame.enterLevel(3)", "100=bibooGame.setMeters(100,100,100)"]),
    "level4": (360, "170:hold:right:150,250:tap:a:25", [START, UNLOCK, "90=bibooGame.enterLevel(4)"]),
    "beam": (192, "149:hold:l1:60,152:hold:a:3", [START, UNLOCK, "90=bibooGame.enterLevel(3)", "100=bibooGame.setMeters(100,100,100)"]),
    "overworld": (200, "", [START, "60=goOverworld()"]),
}


def run(cmd, env):
    subprocess.run(cmd, check=True, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("game")
    ap.add_argument("--out", default=str(ROOT / "native" / "build" / "parity"))
    ap.add_argument("--scenes", nargs="*", default=list(SHOTS))
    ap.add_argument("--tolerance", type=int, default=8)
    ap.add_argument("--max-share", type=float, default=3.0, help="fail when more than this percent of a scene differs (text never matches exactly)")
    ap.add_argument("--binary", default=str(ROOT / "native" / "build" / "x86_64" / "sgba"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
    pygame.init()
    failed = 0
    print(f"{'scene':10s} {'differing':>10s} {'share':>7s} {'max':>4s}")
    for name in args.scenes:
        frames, script, js = SHOTS[name]
        py, c = out / f"{name}_py.png", out / f"{name}_c.png"
        jsargs = []
        for item in [SEED] + js:
            jsargs += ["--js", item]
        run([sys.executable, str(ROOT / "tools" / "js_bench.py"), args.game, "--frames", str(frames), "--script", script or "9999:a", "--shot", str(py)] + jsargs, env)
        run([args.binary, args.game, "--frames", str(frames), "--script", script or "9999:a", "--shot", str(c)] + jsargs, env)
        a, b = pygame.image.load(str(py)), pygame.image.load(str(c))
        if a.get_size() != b.get_size():
            print(f"{name:10s} size differs {a.get_size()} {b.get_size()}")
            continue
        w, h = a.get_size()
        diff = pygame.Surface((w, h))
        bad = worst = 0
        for y in range(h):
            for x in range(w):
                p, q = a.get_at((x, y)), b.get_at((x, y))
                d = max(abs(p[0] - q[0]), abs(p[1] - q[1]), abs(p[2] - q[2]))
                worst = max(worst, d)
                if d > args.tolerance:
                    bad += 1
                    diff.set_at((x, y), (255, 0, 0))
                else:
                    diff.set_at((x, y), (p[0] // 3, p[1] // 3, p[2] // 3))
        pygame.image.save(diff, str(out / f"{name}_diff.png"))
        share = 100.0 * bad / (w * h)
        failed += share > args.max_share
        print(f"{name:10s} {bad:10d} {share:6.2f}% {worst:4d}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
