"""Run the real ARM (ArkOS) bundle under QEMU with a small memory limit and one CPU core, to stand in for a handheld.

    python tools/device_sim.py dist/parry-perry-arkos-aarch64.zip --mem 640 -- --frames 600 --script "..." --js "90=..."

Everything after `--` goes to tools/js_bench.py, which runs inside the bundle's own aarch64 Python and pygame.
The game folder is the bundle's single game unless --game is given.

What this is and is not:
* It executes the same ARM machine code and the same Python/pygame/QuickJS builds that ship on the device, so memory
  behaviour, library paths and crashes are realistic. --mem caps the address space (a bit above resident memory).
* Speed is only a proxy. QEMU is usually several times slower than the real core and not uniformly so. Calibrate with
  a device log (sgba.log): run the scene the log came from and compare, then use the ratio for before/after comparisons.
* Needs qemu-aarch64, prlimit and an aarch64 sysroot (Debian: qemu-user, libc6-arm64-cross).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def prepare(bundle_zip: Path, workdir: Path) -> Path:
    """Unpack the bundle and drop js_bench.py next to its runtime. Returns the bundle's `sgba` folder."""
    if shutil.which("unzip"):             # keeps symlinks and permissions, which zipfile.extractall drops
        subprocess.run(["unzip", "-q", "-o", str(bundle_zip), "-d", str(workdir)], check=True)
    else:
        with zipfile.ZipFile(bundle_zip) as archive:
            archive.extractall(workdir)
    app = workdir / "sgba"
    if not (app / "python" / "bin").is_dir():
        raise SystemExit(f"{bundle_zip} does not look like an ArkOS bundle (no sgba/python)")
    shutil.copy2(HERE / "js_bench.py", app / "js_bench.py")
    return app


def command_for(app: Path, args: list, mem_mb: int, cpus: str) -> tuple:
    for tool in ("qemu-aarch64", "prlimit"):
        if shutil.which(tool) is None:
            raise SystemExit(f"{tool} not found: install qemu-user and util-linux")
    sysroot = os.environ.get("QEMU_LD_PREFIX", "/usr/aarch64-linux-gnu")
    env = dict(os.environ, QEMU_LD_PREFIX=sysroot, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy",
               PYTHONPATH=f"{app / 'site'}:{app}", PYTHONNOUSERSITE="1", LD_LIBRARY_PATH=str(app / "python" / "lib"),
               SGBA_CACHE_DIR=str(app / "cache"))
    env.pop("PYTHONHOME", None)
    (app / "cache").mkdir(exist_ok=True)
    command = ["prlimit", f"--as={mem_mb * 1024 * 1024}"]
    if shutil.which("taskset") and cpus:
        command += ["taskset", "-c", cpus]
    command += ["qemu-aarch64", str(app / "python" / "bin" / "python3.11"), str(app / "js_bench.py")] + args
    return command, env


def run(bundle_zip: Path, bench_args: list, mem_mb: int = 640, cpus: str = "0", game: str = None, keep: Path = None) -> str:
    """Run js_bench inside the bundle and return its output."""
    workdir = keep or Path(tempfile.mkdtemp(prefix="sgba_sim_"))
    app = workdir / "sgba" if (workdir / "sgba" / "js_bench.py").is_file() else prepare(bundle_zip, workdir)
    folder = game or next(str(p.relative_to(app)) for p in sorted((app / "games").iterdir()) if p.is_dir())
    command, env = command_for(app, [folder] + bench_args, mem_mb, cpus)
    result = subprocess.run(command, cwd=app, env=env, capture_output=True, text=True)
    if keep is None:
        shutil.rmtree(workdir, ignore_errors=True)
    if result.returncode != 0:
        raise SystemExit(f"device simulation failed ({result.returncode}):\n{result.stdout[-1500:]}\n{result.stderr[-1500:]}")
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bundle", type=Path, help="the ArkOS zip built by tools/build_bundle.py arkos-aarch64")
    parser.add_argument("--mem", type=int, default=640, help="address-space limit in MB (default 640; the device has 1 GB shared with the OS)")
    parser.add_argument("--cpus", default="0", help="CPU list for taskset, '' for no pinning (default: one core)")
    parser.add_argument("--game", help="game folder inside the bundle (default: the first one)")
    argv = sys.argv[1:]
    split = argv.index("--") if "--" in argv else len(argv)
    args = parser.parse_args(argv[:split])
    bench_args = argv[split + 1:]
    sys.stdout.write(run(args.bundle, bench_args, args.mem, args.cpus, args.game))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
