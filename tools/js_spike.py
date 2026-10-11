"""Run a browser-style JS game headless for N frames and save a screenshot. Used to check JS compatibility."""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pygame  # noqa: E402

import runtime  # noqa: E402
import sgba_js  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--shot", type=Path)
    parser.add_argument("--press", action="append", default=[], help="frame:action, e.g. 30:start")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    pygame.init()
    pygame.display.set_mode((640, 480))
    messages = []

    def log(level, message):
        messages.append((level, message))
        if not args.quiet and len(messages) <= 60:
            print(f"[{level}] {message[:300]}")

    t0 = time.time()
    game = sgba_js.JSGame(args.root, save_dir=Path("/tmp/js_spike_saves"), log=log)
    print(f"loaded in {time.time() - t0:.2f}s, logical size {game.logical_size}, errors so far {game.host.errors}")
    inputs = runtime.InputState()
    schedule = {}
    for item in args.press:
        frame, action = item.split(":")
        schedule.setdefault(int(frame), []).append(action)
    surface = pygame.Surface(game.logical_size)
    t1 = time.time()
    for frame in range(args.frames):
        for action in schedule.get(frame, []):
            inputs._set(action, ("test",), True)
        for action in schedule.get(frame - 3, []):
            inputs._set(action, ("test",), False)
        game.update(1 / 60, inputs)
        inputs.consume_edges()
        game.draw(surface)
    per_frame = (time.time() - t1) / max(1, args.frames) * 1000
    print(f"{args.frames} frames, {per_frame:.2f} ms/frame (this machine), errors {game.host.errors}")
    if args.shot:
        pygame.image.save(surface, str(args.shot))
        print("saved", args.shot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
