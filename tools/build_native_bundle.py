"""Build a self-contained folder around the native (C) runtime: no Python, nothing to install on the target.

    python tools/build_native_bundle.py arkos-aarch64 --web ../BibooSGBA/web --name "Parry Perry" \\
        --ui-config ../BibooSGBA/sgba/sgba_ui.json --exclude "*.mp3" --exclude "tests/*" --controls controls-arkos.json
    python tools/build_native_bundle.py windows-x64  --web ... (same options)

The program is built with zig as the C compiler (pip install ziglang), which links ArkOS builds against glibc 2.17.
SDL2 comes from the pinned pygame wheel (SHA-256 checked), the same one the Python bundles use; on ArkOS the device's own
SDL2 is tried first and the shipped one is the fallback. Output: dist/<game>-native-<target>/ and .zip
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_bundle as bb  # noqa: E402
from package_game import package_game  # noqa: E402

MAKE_TARGET = {"arkos-aarch64": "aarch64", "windows-x64": "windows"}

ARKOS_LAUNCHER = r'''#!/bin/bash
# SGBA launcher for ArkOS handhelds. Everything it needs is in the "sgba" folder next to it.
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$HERE/sgba"
cd "$APP" || exit 1
LOG="$APP/sgba_launch.log"
echo "=== launch $(date) ===" >> "$LOG" 2>&1
chmod +x "$APP/sgba" 2>/dev/null
[ -w /dev/tty1 ] && printf '\033c' > /dev/tty1 2>/dev/null
export HOME="${HOME:-$APP}"
GAME="${1:-@GAME@}"

run() {
  # $1 = SDL2 library to force, or empty for the device's own
  if [ -n "$1" ]; then export SGBA_SDL="$1"; else unset SGBA_SDL; fi
  "$APP/sgba" --fullscreen ${GAME:+"$GAME"} >> "$LOG" 2>&1
}

start=$(date +%s)
run ""
rc=$?
elapsed=$(( $(date +%s) - start ))
if [ $rc -ne 0 ] && [ $elapsed -lt 20 ]; then
  echo "first attempt failed (rc=$rc), retrying with bundled SDL" >> "$LOG"
  run "$APP/libSDL2-sgba.so"
  rc=$?
fi
echo "exit $rc" >> "$LOG"
[ -w /dev/tty1 ] && printf '\033c' > /dev/tty1 2>/dev/null
exit 0
'''

WINDOWS_LAUNCHER = r'''@echo off
rem SGBA launcher for Windows. Everything it needs is in the "sgba" folder next to it.
cd /d "%~dp0sgba"
set "GAME=@GAME@"
if not "%~1"=="" set "GAME=%~f1"
if defined GAME ("sgba.exe" "%GAME%") else ("sgba.exe")
if errorlevel 1 pause
'''


def build_binary(target: str) -> Path:
    make_target = MAKE_TARGET[target]
    subprocess.run(["make", "-C", str(ROOT / "native"), f"TARGET={make_target}"], check=True)
    return ROOT / "native" / "build" / make_target / ("sgba.exe" if make_target == "windows" else "sgba")


def build(target: str, cache: Path, out_dir: Path, web: Path, name: str, ui_config, excludes, controls) -> Path:
    pins = bb.TARGETS[target]
    game_id = bb.slug(name)
    label = f"{game_id}-native-{target}"
    bundle = out_dir / label
    if bundle.exists():
        shutil.rmtree(bundle)
    app = bundle / "sgba"
    app.mkdir(parents=True)

    binary = build_binary(target)
    shutil.copy2(binary, app / binary.name)
    with zipfile.ZipFile(io.BytesIO(bb.download(*pins["pygame"], cache))) as wheel:       # SDL2 from the pinned pygame wheel
        if target.startswith("arkos"):
            member = next(n for n in wheel.namelist() if n.startswith("pygame.libs/libSDL2-2-") and ".so" in n)
            (app / "libSDL2-sgba.so").write_bytes(wheel.read(member))
        else:
            (app / "SDL2.dll").write_bytes(wheel.read("pygame/SDL2.dll"))
    if controls is not None:
        data = json.loads(controls.read_text(encoding="utf-8"))
        if not isinstance(data.get("bindings"), dict) or not data["bindings"]:
            raise SystemExit(f"{controls} is not a valid controls file")
        shutil.copy2(controls, app / "controls.json")
    games = app / "games"
    games.mkdir()
    staged = out_dir / f".stage-{game_id}"                    # the game is shipped as ONE .sgba file, which the runtime opens directly
    if staged.exists():
        shutil.rmtree(staged)
    bb.stage_web_game(web, staged, name, ui_config, excludes)
    package_game(staged, games / f"{game_id}.sgba", name)
    shutil.rmtree(staged)
    (app / "saves").mkdir()
    game_arg = f"games/{game_id}.sgba" if target.startswith("arkos") else f"games\\{game_id}.sgba"

    launcher_name = f"{name}.sh" if target.startswith("arkos") else f"{name}.bat"
    how = bb.HOW[target].format(launcher=launcher_name, name=name)
    (app / "README.txt").write_text(bb.README.format(how=how, name=name), encoding="utf-8")
    shutil.copy2(app / "README.txt", bundle / "README.txt")
    launcher = bundle / launcher_name
    if target.startswith("arkos"):
        launcher.write_text(ARKOS_LAUNCHER.replace("@GAME@", game_arg), encoding="utf-8", newline="\n")
    else:
        launcher.write_text(WINDOWS_LAUNCHER.replace("@GAME@", game_arg).replace("\n", "\r\n"), encoding="utf-8", newline="")

    archive_path = out_dir / f"{label}.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(bundle.rglob("*")):
            if not path.is_file():
                continue
            info = zipfile.ZipInfo.from_file(path, path.relative_to(bundle).as_posix())
            executable = path.suffix == ".sh" or path.name == "sgba" or path.suffix == ".so"
            info.external_attr = (0o755 if executable else 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes(), compresslevel=6)
    return archive_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", choices=sorted(MAKE_TARGET))
    parser.add_argument("--cache", type=Path, default=ROOT / ".bundle_cache")
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    parser.add_argument("--web", type=Path, required=True, help="Folder of a browser game (index.html)")
    parser.add_argument("--name", required=True, help="Game name: used for the launcher file and the Ports list entry")
    parser.add_argument("--ui-config", type=Path, help="sgba_ui.json with this game's SGBA settings")
    parser.add_argument("--controls", type=Path, help="controls.json to ship as the default pad mapping")
    parser.add_argument("--exclude", action="append", default=[], help="Glob of game files to leave out (repeatable)")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    archive = build(args.target, args.cache, args.out, args.web, args.name, args.ui_config, tuple(args.exclude), args.controls)
    print(f"built {archive} ({archive.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
