"""Benchmark a JS game headless: frame time (update + draw), memory, and drawing work per frame.

    python tools/js_bench.py /path/to/web --script "30:a,150:hold:right:300,200:tap:a:25" --frames 600

Script items (frame:action): `F:a` press for 3 frames; `F:hold:ACTION:N` hold N frames; `F:tap:ACTION:K` tap every K frames until the end.
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pygame  # noqa: E402

import runtime  # noqa: E402
import sgba_js  # noqa: E402


def rss_mb() -> float:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    except OSError:
        pass
    return 0.0


def parse_script(text: str, frames: int) -> dict:
    by_frame: dict = {}

    def add(frame, action, down):
        by_frame.setdefault(frame, []).append((action, down))

    for item in filter(None, text.split(",")):
        parts = item.split(":")
        frame = int(parts[0])
        if len(parts) == 2:
            add(frame, parts[1], True)
            add(frame + 3, parts[1], False)
        elif parts[1] == "hold":
            add(frame, parts[2], True)
            add(frame + int(parts[3]), parts[2], False)
        elif parts[1] == "tap":
            for f in range(frame, frames, int(parts[3])):
                add(f, parts[2], True)
                add(f + 3, parts[2], False)
    return by_frame


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--frames", type=int, default=600)
    parser.add_argument("--script", default="30:a,150:hold:right:300,200:tap:a:25")
    parser.add_argument("--nav", choices=["engine", "game", "off"], default="game")
    parser.add_argument("--depth", type=int, choices=[16, 32], help="override color_depth")
    parser.add_argument("--image-depth", type=int, choices=[16, 32])
    parser.add_argument("--rle", action="store_true")
    parser.add_argument("--budget", type=int, help="override image_budget_mb")
    parser.add_argument("--shot", type=Path)
    parser.add_argument("--js", action="append", default=[], help="FRAME=CODE: run JavaScript in the page at that frame (repeatable)")
    parser.add_argument("--json", type=Path, help="write the summary as JSON")
    parser.add_argument("--skip", type=int, default=0, help="leave out the first N frames from the statistics")
    args = parser.parse_args()
    pygame.init()
    pygame.display.set_mode((640, 480))
    base_rss = rss_mb()
    overrides = {}
    if args.depth:
        overrides["color_depth"] = args.depth
    if args.image_depth:
        overrides["image_depth"] = args.image_depth
    if args.rle:
        overrides["rle"] = True
    if args.budget:
        overrides["image_budget_mb"] = args.budget
    original = sgba_js.JSGame._load_config
    sgba_js.JSGame._load_config = staticmethod(lambda root: dict(original(root), **overrides))
    game = sgba_js.JSGame(args.root, save_dir=Path(__import__("tempfile").mkdtemp()), log=(lambda *a: print("LOG", *a)) if os.environ.get("SGBA_BENCH_LOG") else (lambda *a: None))
    game.config["navigation"] = args.nav
    inputs = runtime.InputState()
    surface = pygame.Surface(game.logical_size, 0, game.surface_depth)
    by_frame = parse_script(args.script, args.frames)
    times = []
    js_at: dict = {}
    for item in args.js:
        at, _, code = item.partition("=")
        js_at.setdefault(int(at), []).append(code)
    for frame in range(args.frames):
        for code in js_at.get(frame, []):
            try:
                game.host.ctx.eval(code)
            except Exception as exc:  # noqa: BLE001
                print(f"js at frame {frame} failed: {exc}")
        for action, down in by_frame.get(frame, []):
            inputs._set(action, ("bench",), down)
        t = time.process_time()
        game.update(1 / 60, inputs)
        inputs.consume_edges()
        game.draw(surface)
        times.append((time.process_time() - t) * 1000)
    if args.shot:
        pygame.image.save(surface, str(args.shot))
    times = times[args.skip:] or times
    ordered = sorted(times)
    print(f"frames {len(times)}  avg {statistics.mean(times):.2f} ms  p95 {ordered[int(len(ordered) * .95)]:.2f} ms  "
          f"p99 {ordered[int(len(ordered) * .99)]:.2f} ms  max {ordered[-1]:.2f} ms")
    print(f"memory: rss {rss_mb():.0f} MB (before load {base_rss:.0f} MB)")
    host = game.host
    store = host.images
    canvas_mb = sum(c.surface.get_width() * c.surface.get_height() * c.surface.get_bytesize() for c in host.canvases.values()) / 1e6
    print(f"images: {store.decoded_count}/{len(store.entries)} decoded = {store.bytes / 1e6:.1f} MB (peak {store.peak / 1e6:.1f}), "
          f"loads {store.loads}, evictions {store.evictions}, kinds {store.kinds}")
    print(f"canvases {len(host.canvases)} = {canvas_mb:.1f} MB, variant cache {host.variants.bytes / 1e6:.1f} MB "
          f"({host.variants.hits} hits/{host.variants.misses} misses), js errors {host.errors}")
    st = host.stats
    n = max(1, st.frames)
    print(f"per frame: {st.ops / n:.0f} ops, {st.replayed / n:.0f} replayed, skipped {st.skipped}/{st.frames} frames, "
          f"{st.blits / n:.0f} blits (copy {st.copy_px / n / 1e3:.0f}k px, key {st.key_px / n / 1e3:.0f}k, alpha {st.alpha_px / n / 1e3:.0f}k), "
          f"fills {st.fill_px / n / 1e3:.0f}k px, dirty {st.dirty_px / n / 1e3:.0f}k px")
    if args.json:
        import json
        args.json.write_text(json.dumps({
            "avg_ms": round(statistics.mean(times), 2), "p95_ms": round(ordered[int(len(ordered) * .95)], 2),
            "p99_ms": round(ordered[int(len(ordered) * .99)], 2), "max_ms": round(ordered[-1], 1),
            "rss_mb": round(rss_mb()), "decoded_mb": round(store.bytes / 1e6, 1), "images_used": store.decoded_count,
            "loads": store.loads, "evictions": store.evictions, "js_errors": host.errors,
            "alpha_kpx": round(st.alpha_px / n / 1e3), "key_kpx": round(st.key_px / n / 1e3),
            "copy_kpx": round(st.copy_px / n / 1e3), "fill_kpx": round(st.fill_px / n / 1e3), "ops": round(st.ops / n)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
