"""Create a validated, ZIP-based .sgba game package from a game directory."""
from __future__ import annotations

import argparse
import json
import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sgba_package import MAX_ARCHIVE_BYTES, MAX_FILE_BYTES, MAX_FILES, MAX_UNPACKED_BYTES, validate_package

SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache"}
SKIP_SUFFIXES = {".pyc", ".pyo"}


def package_game(game_dir: Path, output_path: Path, title: str | None = None) -> Path:
    game_dir = game_dir.resolve()
    if not game_dir.is_dir():
        raise ValueError(f"Game folder not found: {game_dir}")
    entry = game_dir / "main.py"
    if not entry.is_file():
        raise ValueError("Game folder must contain main.py")
    output_path = output_path.resolve()
    if output_path == entry or game_dir in output_path.parents:
        raise ValueError("Output package must be outside the source game folder")

    game_config = game_dir / "game.json"
    config = json.loads(game_config.read_text(encoding="utf-8")) if game_config.is_file() else {}
    if not isinstance(config, dict):
        raise ValueError("game.json must contain a JSON object")
    package_title = title or game_dir.name.replace("_", " ").strip() or "SGBA Game"
    manifest = {
        "format": "sgba",
        "format_version": 1,
        "title": package_title[:80],
        "entry_point": "main.py",
    }

    files = []
    for path in sorted(game_dir.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symbolic links are not allowed in game packages: {path.relative_to(game_dir)}")
        if not path.is_file():
            continue
        relative = path.relative_to(game_dir)
        if any(part in SKIP_DIRS for part in relative.parts) or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if path.name == "manifest.json":
            raise ValueError("Source game folders must not contain their own manifest.json")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f"File exceeds 16 MiB package limit: {relative}")
        files.append((relative.as_posix(), path))
    if len(files) + 1 > MAX_FILES:
        raise ValueError(f"Package exceeds {MAX_FILES} file limit")
    total_size = sum(path.stat().st_size for _, path in files)
    if total_size > MAX_UNPACKED_BYTES:
        raise ValueError("Game exceeds 64 MiB unpacked package limit")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(output_path.stem + ".tmp.sgba")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
            for relative, path in files:
                archive.write(path, relative)
        if temporary.stat().st_size > MAX_ARCHIVE_BYTES:
            raise ValueError("Package exceeds 32 MiB compressed-size limit")
        validate_package(temporary)
        os.replace(temporary, output_path)
        validate_package(output_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Package an SGBA game folder into one .sgba file")
    parser.add_argument("game_dir", type=Path, help="Folder containing main.py and optional game.json/assets")
    parser.add_argument("output", nargs="?", type=Path, help="Output .sgba path (default: <game folder>.sgba)")
    parser.add_argument("--title", help="Display title (maximum 80 characters)")
    args = parser.parse_args()
    output = args.output or args.game_dir.with_suffix(".sgba")
    if output.suffix.lower() != ".sgba":
        parser.error("Output filename must end in .sgba")
    try:
        result = package_game(args.game_dir, output, args.title)
    except (OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        parser.error(str(exc))
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
