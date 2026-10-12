"""Check that a browser game folder will run on SGBA: scan for unsupported features, then load it and run some frames.

    python tools/sgba_check.py path/to/web [--frames 90]

Exit status 1 on an unsupported feature that cannot work, or on JavaScript errors while loading; warnings never fail.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# (pattern, message, fatal)
RULES = [
    (r"getContext\(\s*['\"](webgl2?|experimental-webgl|bitmaprenderer|webgpu)['\"]", "WebGL/WebGPU is not supported (SGBA draws 2D canvas only)", True),
    (r"^\s*(import\s[^\n]*\sfrom\s|import\s*['\"]|export\s+(default|const|function|class|\{))", "ES module syntax needs <script type=module>, which SGBA does not run", True),
    (r"\bnew\s+(Shared)?Worker\s*\(", "Web Workers are not supported", False),
    (r"\bnew\s+WebSocket\s*\(|\bRTCPeerConnection\b|\bEventSource\s*\(", "networking is not available on the device", False),
    (r"\bindexedDB\b", "indexedDB is not supported (localStorage is)", False),
    (r"\b(getImageData|putImageData)\b", "pixel read-back forces slow drawing on the main canvas", False),
    (r"\bcreateOscillator\s*\(|\bcreateBiquadFilter\s*\(|\bcreateConvolver\s*\(", "synthesized Web Audio is silent on SGBA (buffer sources and gain work)", False),
    (r"\bnavigator\.(bluetooth|usb|serial|geolocation|mediaDevices)\b", "this browser feature does not exist on the device", False),
    (r"\bOffscreenCanvas\b", "OffscreenCanvas works but is treated like an ordinary canvas", False),
]


def scan(root: Path):
    problems = []
    skip = {"tests", "node_modules", ".git"}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in (".js", ".html", ".mjs"):
            continue
        if any(part in skip for part in path.relative_to(root).parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if path.suffix == ".html" and re.search(r"<script[^>]*type=[\"']module", text):
            problems.append((True, path, 1, "<script type=module> is not supported"))
        for pattern, message, fatal in RULES:
            for match in re.finditer(pattern, text, re.M):
                line = text.count("\n", 0, match.start()) + 1
                problems.append((fatal, path, line, message))
                break
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root", type=Path)
    parser.add_argument("--frames", type=int, default=90)
    args = parser.parse_args()
    root = args.root.resolve()
    if not (root / "index.html").is_file():
        print(f"no index.html in {root}")
        return 1
    failed = False
    for fatal, path, line, message in scan(root):
        print(f"{'ERROR' if fatal else 'warning'}: {path.relative_to(root)}:{line}: {message}")
        failed = failed or fatal

    import pygame
    import runtime
    import sgba_js
    pygame.init()
    pygame.display.set_mode((640, 480))
    errors = []
    game = sgba_js.JSGame(root, save_dir=Path(os.environ.get("TMPDIR", "/tmp")) / "sgba_check_saves",
                          log=lambda level, message: errors.append(message) if level == "error" else None)
    inputs = runtime.InputState()
    surface = pygame.Surface(game.logical_size, 0, game.surface_depth)
    for _ in range(args.frames):
        game.update(1 / 60, inputs)
        inputs.consume_edges()
        game.draw(surface)
    for message in errors[:10]:
        print(f"ERROR: JavaScript error while running: {message[:200]}")
    failed = failed or bool(errors)
    store = game.host.images
    print(f"loaded {len(store.entries)} images ({store.decoded_count} used in {args.frames} frames), canvas {game.logical_size}, "
          f"{len(errors)} JavaScript errors")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
