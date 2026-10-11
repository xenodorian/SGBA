"""Build a self-contained SGBA folder that needs nothing installed on the target.

    python tools/build_bundle.py arkos-aarch64   # ArkOS handhelds (64-bit ARM, glibc)
    python tools/build_bundle.py windows-x64     # Windows PC

Each bundle carries its own Python and pygame, the runtime, and packaged games.
All downloads are pinned and checked against SHA-256 before use.
Output: dist/SGBA-<target>/ and dist/SGBA-<target>.zip
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import lzma
import os
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from package_game import package_game  # noqa: E402

PBS = "https://github.com/astral-sh/python-build-standalone/releases/download/20261009/"
PYGAME_URL = "https://files.pythonhosted.org/packages/"

TARGETS = {
    "arkos-aarch64": {
        "python": (PBS + "cpython-3.11.17%2B20261009-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz",
                   "7e7e24eee3467dd666aff379c34c4ef3e6bc7142aec7d5ba783765df7da999a6"),
        "pygame": (PYGAME_URL + "00/9e/0d8aa8cf93db2d2ee38ebaf1c7b61d0df36ded27eb726221719c150c673d/"
                   "pygame-2.6.1-cp311-cp311-manylinux_2_17_aarch64.manylinux2014_aarch64.whl",
                   "c31dbdb5d0217f32764797d21c2752e258e5fb7e895326538d82b5f75a0cd856"),
        "quickjs": (PYGAME_URL + "66/1e/bf364ef846a1d36f2e7c424699d5b01b5c0eb852a0d7bae183e521742184/"
                    "quickjs_ng-0.17.0.1-cp310-abi3-manylinux2014_aarch64.manylinux_2_17_aarch64.manylinux_2_28_aarch64.whl",
                    "b8f41e2230d96925e69d40c7bbb66a81535ca63a1941d0ac8a2456c53085bafe"),
        # pygame's wheel needs a system libz; ship one built for glibc 2.17 (Ubuntu 18.04 zlib 1.2.11).
        "libz_deb": ("http://ports.ubuntu.com/ubuntu-ports/pool/main/z/zlib/zlib1g_1.2.11.dfsg-0ubuntu2.2_arm64.deb",
                     "f635c78ffb4e13b1451a9b4ac7f68e8b3a29cdf3b60e85a5561441f2d46b3ea8"),
    },
    "windows-x64": {
        "python": (PBS + "cpython-3.11.17%2B20261009-x86_64-pc-windows-msvc-install_only_stripped.tar.gz",
                   "c3045115465bb4aa7d1ca6bfe6ce311ba157d726d768bbb3520fad2580831c3f"),
        "pygame": (PYGAME_URL + "d2/55/ca3eb851aeef4f6f2e98a360c201f0d00bd1ba2eb98e2c7850d80aabc526/"
                   "pygame-2.6.1-cp311-cp311-win_amd64.whl",
                   "91476902426facd4bb0dad4dc3b2573bc82c95c71b135e0daaea072ed528d299"),
        "quickjs": (PYGAME_URL + "5b/67/623df580c13e7cc3bbce5b15c41658dfb753d99839fab25695eef99df34a/"
                    "quickjs_ng-0.17.0.1-cp310-abi3-win_amd64.whl",
                    "355511615baa17bc98092680f068e3536ea1ee5a650b16f59bf6871f497f3780"),
    },
}

RUNTIME_FILES = ("runtime.py", "sgba_package.py", "sgba_shell.py", "sgba_js.py", "sgba_js_ui.py", "sgba_js_shim.js")
PACKAGED_GAMES = ("demo", "input_lab")   # games/template is a starter, not a game
PYTHON_TRIM = ("test", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3", "pydoc_data",
               "distutils", "unittest/test")

ARKOS_LAUNCHER = r'''#!/bin/bash
# SGBA launcher for ArkOS handhelds. Everything it needs is in the "sgba" folder next to it.
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$HERE/sgba"
cd "$APP" || exit 1
PY="$APP/python/bin/python3.11"
LOG="$APP/sgba_launch.log"
echo "=== launch $(date) ===" >> "$LOG" 2>&1
chmod +x "$PY" 2>/dev/null
[ -w /dev/tty1 ] && printf '\033c' > /dev/tty1 2>/dev/null
export PYTHONPATH="$APP/site:$APP"
export PYTHONNOUSERSITE=1
export PYTHONUNBUFFERED=1
export HOME="${HOME:-$APP}"

# Prefer the device's own SDL2: it knows this device's screen and controller drivers.
SYS_SDL=""
for f in /usr/lib/aarch64-linux-gnu/libSDL2-2.0.so.0 /usr/lib64/libSDL2-2.0.so.0 /usr/lib/libSDL2-2.0.so.0 /usr/local/lib/libSDL2-2.0.so.0; do
  if [ -f "$f" ]; then SYS_SDL="$f"; break; fi
done

run_python() {
  # $1 = preload library or empty
  if [ -n "$1" ]; then export LD_PRELOAD="$1"; else unset LD_PRELOAD; fi
  if ! $SGBA_PY_PREFIX "$PY" runtime.py --fullscreen >> "$LOG" 2>&1; then
    rc=$?
    if [ $rc -eq 126 ] || [ $rc -eq 127 ]; then
      # Could not execute the bundled python directly (no exec bit); go through the loader.
      for ld in /lib/ld-linux-aarch64.so.1 /lib64/ld-linux-aarch64.so.1; do
        [ -x "$ld" ] && { $SGBA_PY_PREFIX "$ld" "$PY" runtime.py --fullscreen >> "$LOG" 2>&1; return $?; }
      done
    fi
    return $rc
  fi
  return 0
}

start=$(date +%s)
run_python "$SYS_SDL"
rc=$?
elapsed=$(( $(date +%s) - start ))
if [ $rc -ne 0 ] && [ $elapsed -lt 20 ]; then
  echo "first attempt failed (rc=$rc), retrying with bundled SDL" >> "$LOG"
  run_python ""
  rc=$?
fi
echo "exit $rc" >> "$LOG"
[ -w /dev/tty1 ] && printf '\033c' > /dev/tty1 2>/dev/null
exit 0
'''

WINDOWS_LAUNCHER = r'''@echo off
rem SGBA launcher for Windows. Everything it needs is in the "sgba" folder next to it.
set "APP=%~dp0sgba"
cd /d "%APP%"
set "PYTHONPATH=%APP%\site;%APP%"
set "PYTHONNOUSERSITE=1"
"%APP%\python\python.exe" runtime.py
if errorlevel 1 pause
'''

README = """SGBA
====
{how}

Controls: in the menu use the D-pad and A. In a game, hold START and SELECT together to go
back to the menu. The first time, if a controller is found, SGBA asks you to press each
button once (Controller setup). Games are the .sgba files in the "games" folder.
If something goes wrong, check sgba.log and sgba_launch.log in the "sgba" folder.
"""

HOW = {
    "arkos-aarch64": ('Copy SGBA.sh and the "sgba" folder together into your Ports folder (roms/ports)\n'
                      'on the SD card, then start "SGBA" from the Ports list. Nothing else is needed.'),
    "windows-x64": 'Unzip anywhere and double-click SGBA.bat. Nothing needs to be installed.',
}


def download(url: str, sha256: str, cache: Path) -> bytes:
    cache.mkdir(parents=True, exist_ok=True)
    cached = cache / sha256
    if cached.is_file():
        data = cached.read_bytes()
    else:
        print(f"downloading {url}")
        with urllib.request.urlopen(url, timeout=120) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() == sha256:
            cached.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    if digest != sha256:
        raise SystemExit(f"checksum mismatch for {url}\n  expected {sha256}\n  got      {digest}")
    return data


def extract_tar(data: bytes, destination: Path) -> None:
    """Extract a tarball; symlinks become copies so the result also works on FAT/exFAT cards."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = archive.getmembers()
        links = [m for m in members if (m.issym() or m.islnk()) and "/share/" not in m.name]
        for member in members:
            if not (member.issym() or member.islnk()):
                archive.extract(member, destination, **({"filter": "fully_trusted"} if hasattr(tarfile, "fully_trusted_filter") else {}))
    for _ in range(3):  # links may point at other links
        for member in links:
            link = destination / member.name
            source = destination / member.linkname if member.islnk() else Path(
                os.path.normpath(str(link.parent / member.linkname)))
            if source.is_file() and not link.exists():
                link.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, link)


def trim_python(python_dir: Path) -> None:
    lib = python_dir / "lib"
    for pattern in ("tcl*", "itcl*", "tk*", "libtcl*", "libtk*", "pkgconfig", "python3.11/config-*"):
        for path in lib.glob(pattern):
            shutil.rmtree(path) if path.is_dir() else path.unlink()
    stdlib = lib / "python3.11" if (lib / "python3.11").is_dir() else python_dir / "Lib"
    for name in PYTHON_TRIM:
        shutil.rmtree(stdlib / name, ignore_errors=True)
    for pattern in ("bin/2to3*", "bin/idle3*", "bin/pip*", "bin/pydoc3*", "bin/python3-config", "bin/python3.11-config",
                    "bin/python", "bin/python3", "lib/libpython3.11.so", "lib/libpython3.so"):
        for path in python_dir.glob(pattern):
            path.unlink()
    for package in (stdlib / "site-packages").glob("*"):   # pip and setuptools are not needed at run time
        shutil.rmtree(package) if package.is_dir() else package.unlink()
    for name in ("share", "include", "tcl", "Scripts"):
        shutil.rmtree(python_dir / name, ignore_errors=True)
    for cache in python_dir.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    for path in list(python_dir.rglob("*_tkinter*")) + list(python_dir.rglob("tcl*.dll")) + list(python_dir.rglob("tk*.dll")):
        if path.is_file():
            path.unlink()


def deb_member(deb: bytes, wanted_prefix: str) -> bytes:
    """Read one file out of a .deb (ar archive holding data.tar.xz) using only the stdlib."""
    assert deb[:8] == b"!<arch>\n"
    position = 8
    while position < len(deb):
        name = deb[position:position + 16].decode().strip()
        size = int(deb[position + 48:position + 58])
        body = deb[position + 60:position + 60 + size]
        position += 60 + size + (size & 1)
        if name.startswith("data.tar"):
            raw = lzma.decompress(body) if name.endswith(".xz") else body
            with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
                for member in archive.getmembers():
                    if member.name.lstrip("./").startswith(wanted_prefix) and member.isfile():
                        return archive.extractfile(member).read()
    raise SystemExit(f"{wanted_prefix} not found in deb")


def build(target: str, cache: Path, out_dir: Path) -> Path:
    pins = TARGETS[target]
    bundle = out_dir / f"SGBA-{target}"
    if bundle.exists():
        shutil.rmtree(bundle)
    app = bundle / "sgba"
    app.mkdir(parents=True)

    extract_tar(download(*pins["python"], cache), app)
    trim_python(app / "python")

    site = app / "site"
    with zipfile.ZipFile(io.BytesIO(download(*pins["pygame"], cache))) as wheel:
        wheel.extractall(site)
    for leftover in site.glob("pygame-*.data"):
        shutil.rmtree(leftover, ignore_errors=True)
    with zipfile.ZipFile(io.BytesIO(download(*pins["quickjs"], cache))) as wheel:
        wheel.extractall(site)
    for unused in ("docs", "tests", "examples", "__pyinstaller"):
        shutil.rmtree(site / "pygame" / unused, ignore_errors=True)
    for stub in (site / "pygame").glob("*.pyi"):
        stub.unlink()
    if "libz_deb" in pins:
        libz = deb_member(download(*pins["libz_deb"], cache), "lib/aarch64-linux-gnu/libz.so.1.")
        (site / "pygame.libs" / "libz.so.1").write_bytes(libz)

    for name in RUNTIME_FILES:
        shutil.copy2(ROOT / name, app / name)
    games = app / "games"
    games.mkdir()
    for name in PACKAGED_GAMES:
        package_game(ROOT / "games" / name, games / f"{name}.sgba")

    (app / "README.txt").write_text(README.format(how=HOW[target]), encoding="utf-8")
    if target.startswith("arkos"):
        launcher = bundle / "SGBA.sh"
        launcher.write_text(ARKOS_LAUNCHER, encoding="utf-8", newline="\n")
    else:
        launcher = bundle / "SGBA.bat"
        launcher.write_text(WINDOWS_LAUNCHER.replace("\n", "\r\n"), encoding="utf-8", newline="")
    shutil.copy2(app / "README.txt", bundle / "README.txt")

    archive_path = out_dir / f"SGBA-{target}.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(bundle.rglob("*")):
            if not path.is_file():
                continue
            info = zipfile.ZipInfo.from_file(path, path.relative_to(bundle).as_posix())
            executable = path.suffix == ".sh" or path.parent.name == "bin" or path.suffix == ".so" or ".so." in path.name
            info.external_attr = (0o755 if executable else 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes(), compresslevel=6)
    return archive_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", choices=sorted(TARGETS))
    parser.add_argument("--cache", type=Path, default=ROOT / ".bundle_cache")
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    archive = build(args.target, args.cache, args.out)
    print(f"built {archive} ({archive.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
