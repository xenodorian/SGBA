"""Draw the reference scenes with the Python canvas and the C canvas and compare them.

    python tools/parity.py GAME_FOLDER [--out DIR] [--scenes title level1 ...] [--tolerance 8]

Scenes and seed come from the game's own sgba/pixel_check.py (the game repo, not this one). Text is expected to differ a little
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


def load_scenes(path: Path):
    """The scene list belongs to the game: its sgba/pixel_check.py defines SEED and SHOTS (name -> frames, input script, JS)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("game_pixel_check", path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    spec.loader.exec_module(module)
    return module.SEED, module.SHOTS


def run(cmd, env):
    subprocess.run(cmd, check=True, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("game")
    ap.add_argument("--out", default=str(ROOT / "native" / "build" / "parity"))
    ap.add_argument("--scenes-file", help="the game's pixel_check.py (default: GAME/../sgba/pixel_check.py)")
    ap.add_argument("--scenes", nargs="*")
    ap.add_argument("--tolerance", type=int, default=8)
    ap.add_argument("--max-share", type=float, default=3.0, help="fail when more than this percent of a scene differs (text never matches exactly)")
    ap.add_argument("--binary", default=str(ROOT / "native" / "build" / "x86_64" / "sgba"))
    args = ap.parse_args()
    out = Path(args.out)
    seed, SHOTS = load_scenes(Path(args.scenes_file) if args.scenes_file else Path(args.game).resolve().parent / "sgba" / "pixel_check.py")
    args.scenes = args.scenes or list(SHOTS)
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
    pygame.init()
    failed = 0
    print(f"{'scene':10s} {'differing':>10s} {'share':>7s} {'max':>4s}")
    for name in args.scenes:
        frames, script, js = SHOTS[name]
        py, c = out / f"{name}_py.png", out / f"{name}_c.png"
        jsargs = []
        for item in [seed] + js:
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
