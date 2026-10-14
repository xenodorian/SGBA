"""Build the SGBA emulator package (native C runtime, no Python, no games of its own).

    python tools/build_native_emulator.py arkos-aarch64     # ArkOS handhelds such as the R36S -> dist/SGBA-arkos-aarch64.zip
    python tools/build_native_emulator.py windows-x64        # Windows PC -> dist/SGBA-windows-x64.zip

Layout (ArkOS): SGBA.sh next to a folder "sgba" holding the program, the SDL2 fallback library, controls.json (the R36S pad
mapping, if given with --controls), the demo and input lab games, and saves/. Games live in their own packages; a game's launcher
runs  SGBA.sh path/to/game.sgba . SGBA.sh with no argument shows the menu of the .sgba files in sgba/games.
The program is built with zig (pip install ziglang) so it needs nothing newer than glibc 2.17.
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_bundle as bb  # noqa: E402
import build_native_bundle as nb  # noqa: E402
from package_game import package_game  # noqa: E402

README = """SGBA {version} for {platform}

Start {launcher}. With no game it shows the games in the sgba/games folder (a demo and the input lab).
A game from its own package starts through its own launcher, which runs the emulator on the game's .sgba file.
First run: hold START and SELECT together in any game to return to the menu. Controller mapping is in sgba/controls.json.
Logs: sgba/sgba.log (frame times every 10 seconds) and sgba/sgba_launch.log. Saves are in sgba/saves.
"""


def build(target: str, cache: Path, out_dir: Path, controls: Path | None) -> Path:
    pins = bb.TARGETS[target]
    arkos = target.startswith("arkos")
    label = "SGBA-" + target
    bundle = out_dir / label
    if bundle.exists():
        shutil.rmtree(bundle)
    app = bundle / "sgba"
    app.mkdir(parents=True)
    binary = nb.build_binary(target)
    shutil.copy2(binary, app / binary.name)
    with zipfile.ZipFile(io.BytesIO(bb.download(*pins["pygame"], cache))) as wheel:       # SDL2 from the pinned pygame wheel
        if arkos:
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
    for demo in bb.PACKAGED_GAMES:
        package_game(ROOT / "games" / demo, games / f"{demo}.sgba")
    (app / "saves").mkdir()
    launcher_name = "SGBA.sh" if arkos else "SGBA.bat"
    text = README.format(version="native", platform="ArkOS handhelds" if arkos else "Windows", launcher=launcher_name)
    (app / "README.txt").write_text(text, encoding="utf-8")
    shutil.copy2(app / "README.txt", bundle / "README.txt")
    launcher = bundle / launcher_name
    if arkos:
        launcher.write_text(nb.ARKOS_LAUNCHER.replace("@GAME@", ""), encoding="utf-8", newline="\n")
    else:
        launcher.write_text(nb.WINDOWS_LAUNCHER.replace("@GAME@", "").replace("\n", "\r\n"), encoding="utf-8", newline="")
    archive_path = out_dir / f"{label}.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(bundle.rglob("*")):
            if not path.is_file():
                continue
            info = zipfile.ZipInfo.from_file(path, path.relative_to(bundle).as_posix())
            info.date_time = (2026, 1, 1, 0, 0, 0)
            executable = path.suffix == ".sh" or path.name == "sgba" or path.suffix == ".so"
            info.external_attr = (0o755 if executable else 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes(), compresslevel=6)
    return archive_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", choices=sorted(nb.MAKE_TARGET))
    parser.add_argument("--cache", type=Path, default=ROOT / ".bundle_cache")
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    parser.add_argument("--controls", type=Path, help="controls.json to ship as the default pad mapping")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    archive = build(args.target, args.cache, args.out, args.controls)
    print(f"built {archive} ({archive.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
