"""Build the one package a handheld installs for a game: a launcher plus the game's single .sgba file.

    python tools/build_game_port.py games/slingshot_orbits --name "Slingshot Orbits" --out dist

Writes <out>/<slug>.sgba and <out>/<slug>-port.zip. The zip holds "<Name>.sh" (the entry EmulationStation lists
under Ports) and "<slug>-game/<slug>.sgba". The launcher starts the separately installed SGBA emulator
(SGBA.sh next to it) on that file. The emulator package itself holds no real games, only its demo and input lab.
"""
from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from package_game import package_game  # noqa: E402

LAUNCHER = """#!/bin/bash
# @NAME@: starts the SGBA emulator (installed with GitROMs) on this game's single .sgba file.
HERE="$(cd "$(dirname "$0")" && pwd)"
GAMEDIR="$HERE/@SLUG@-game"
if [ ! -f "$HERE/SGBA.sh" ]; then
  MSG="@NAME@ needs the SGBA emulator. Open GitROMs, choose [Check for updates] and install SGBA emulator."
  echo "$MSG" > "$GAMEDIR/launch.log"
  [ -w /dev/tty1 ] && printf '\\033c%s\\n' "$MSG" > /dev/tty1 2>/dev/null && sleep 6
  exit 1
fi
exec bash "$HERE/SGBA.sh" "$GAMEDIR/@SLUG@.sgba"
"""


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def build_port(game_dir: Path, name: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    key = slug(name)
    sgba = package_game(game_dir, out_dir / f"{key}.sgba", name)
    target = out_dir / f"{key}-port.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as port:
        info = zipfile.ZipInfo(f"{name}.sh", (2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.create_system = 3
        info.external_attr = 0o755 << 16
        port.writestr(info, LAUNCHER.replace("@NAME@", name).replace("@SLUG@", key).encode("utf-8"))
        entry = zipfile.ZipInfo(f"{key}-game/{key}.sgba", (2026, 1, 1, 0, 0, 0))
        entry.compress_type = zipfile.ZIP_STORED          # a .sgba is already compressed
        entry.create_system = 3
        entry.external_attr = 0o644 << 16
        port.writestr(entry, Path(sgba).read_bytes())
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("game", type=Path, help="Game folder (index.html or main.py)")
    parser.add_argument("--name", required=True, help='Display name, e.g. "Slingshot Orbits"')
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    target = build_port(args.game, args.name, args.out)
    print(f"built {target} ({target.stat().st_size / 1e3:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
