"""Coarse asset-size guardrail for games shipped in this repository."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
LIMIT_BYTES = 16 * 1024 * 1024
SINGLE_FILE_LIMIT = 4 * 1024 * 1024
failed = False

for game_dir in sorted((ROOT / "games").iterdir()):
    asset_dir = game_dir / "assets"
    if not asset_dir.is_dir():
        continue
    files = [path for path in asset_dir.rglob("*") if path.is_file()]
    total = sum(path.stat().st_size for path in files)
    print(f"{game_dir.name}: {total / (1024 * 1024):.2f} MiB across {len(files)} asset files")
    if total > LIMIT_BYTES:
        print(f"ERROR: exceeds the 16 MiB asset budget by {(total - LIMIT_BYTES) / (1024 * 1024):.2f} MiB")
        failed = True
    for path in files:
        if path.stat().st_size > SINGLE_FILE_LIMIT:
            print(f"ERROR: single asset exceeds 4 MiB: {path.relative_to(ROOT)}")
            failed = True

if failed:
    sys.exit(1)
print("Asset budget audit passed.")
