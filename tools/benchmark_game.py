"""Headless game update/draw timing probe; reports rather than guessing hardware FPS."""
import argparse
import os
import statistics
import sys
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pygame
import runtime


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game", help="Path to a game's main.py")
    parser.add_argument("--frames", type=int, default=300, help="Measured frames (default: 300)")
    parser.add_argument("--warmup", type=int, default=30, help="Warm-up frames (default: 30)")
    parser.add_argument("--max-average-ms", type=float, default=16.67,
                        help="Fail if average update+draw time exceeds this threshold")
    args = parser.parse_args()
    if args.frames < 1 or args.warmup < 0:
        parser.error("frames must be positive and warmup cannot be negative")
    game_path = Path(args.game).resolve()
    if not game_path.is_file():
        parser.error(f"game file not found: {game_path}")

    pygame.init()
    try:
        config = runtime.load_game_config(game_path)
        game = runtime.load_game(game_path)
        surface = pygame.Surface(config["logical_size"], depth=32)
        inputs = runtime.InputState()
        update = getattr(game, "update", None)
        for _ in range(args.warmup):
            if callable(update):
                update(runtime.FIXED_DT, inputs)
            game.draw(surface)
        samples = []
        for _ in range(args.frames):
            start = time.perf_counter()
            if callable(update):
                update(runtime.FIXED_DT, inputs)
            game.draw(surface)
            samples.append((time.perf_counter() - start) * 1000)
        ordered = sorted(samples)
        p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
        average = sum(samples) / len(samples)
        print(f"Game: {game_path}")
        print(f"Profile: {config['logical_size'][0]}x{config['logical_size'][1]}")
        print(f"Samples: {len(samples)}")
        print(f"Update + draw: average {average:.3f} ms, p95 {p95:.3f} ms, max {max(samples):.3f} ms")
        print(f"16.67 ms frame budget: {'PASS' if average <= args.max_average_ms else 'FAIL'}")
        return 0 if average <= args.max_average_ms else 1
    finally:
        pygame.quit()


if __name__ == "__main__":
    raise SystemExit(main())
