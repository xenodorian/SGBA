# SGBA

A small, resource-conscious Python/Pygame runtime for 2D games. The goal is one device-agnostic runtime: the same game code runs on any device with Python, pygame/SDL2, a display, and a keyboard or gamepad (Windows, Linux, handhelds such as the R36S).

## Current status

The native runtime uses a fixed 60 Hz simulation loop, a default 640×480 logical canvas, optional 240×160 and 320×240 low-resolution profiles, source-aware logical input, nearest-neighbor scaling, and cached asset loading with decoded-image limits.

Low-end handhelds such as the R36S have about 1 GiB RAM shared with the operating system and running software. Memory and frame time are design constraints. The runtime cannot guarantee 60 FPS on untested hardware or against an unbounded game; generated games must follow the resource contract in GAME_DEVELOPMENT.md.

## Supported targets and how to run
SGBA ships as a self-contained bundle: it carries its own Python and pygame, so nothing is installed on the device or PC.
- **ArkOS handhelds (R36S class, 64-bit ARM):** build `SGBA-arkos-aarch64.zip` (`python tools/build_bundle.py arkos-aarch64`, or download it from the CI run). Unzip, copy `SGBA.sh` and the `sgba` folder to the SD card's Ports folder, then start "SGBA" from the Ports menu.
- **Windows PC:** build `SGBA-windows-x64.zip` (`python tools/build_bundle.py windows-x64`). Unzip and double-click `SGBA.bat`.
- **Developers:** `./run_linux.sh` or `run_windows.bat` after the setup script, with Python 3.7+ and pygame 2.0+.

No keyboard or terminal is needed on a handheld. The engine draws its own game menu (D-pad and A), asks you to press each controller button once on first run (and under "Controller setup" later), shows errors on screen, and writes `sgba.log` and `sgba_launch.log`. In a game, hold START and SELECT together to return to the menu. Games are the `.sgba` files in the bundle's `games` folder.

Verification status: CI runs the full test suite with the bundled ARM64 Python and pygame under emulation and with the bundled Windows Python. It has NOT run on ArkOS hardware. The launcher prefers the device's own SDL2 (so its screen and controller drivers are used) and falls back to the bundled SDL2 and then through other video drivers; none of that is verified on a real device.

**Do not run the desktop Linux setup script on the R36S.** Firmware, CPU architecture, system libraries, Python, SDL support, and controller layout must be confirmed first. Do not modify the system installation or firmware.

## Agent handoff
- Current_Work.md: persistent task queue and status.
- AGENTS.md: repository-level agent entrypoint.
- AI_GAME_AGENT_INSTRUCTIONS.md: binding AI game-coding instructions.
- GAME_DEVELOPMENT.md: API, virtual-console profiles, and resource budgets.

## PC setup
Requirements: Python 3.11 or 3.12.

- Windows: run setup_windows.bat, then run_windows.bat.
- Linux desktop: run sh setup_linux_pc.sh, then ./run_linux.sh.

Setup creates a project-local .venv and does not install packages globally. If setup fails, inspect the error instead of installing system packages. Run checks using test_windows.bat or ./test_linux.sh.

To launch fullscreen, pass --fullscreen. To launch a packaged game, pass its `.sgba` path, for example `run_windows.bat games/my_game.sgba` or `./run_linux.sh games/my_game.sgba`. Development folders can still be launched directly with a path to `main.py`. Without a path, the demo starts.

## `.sgba` game packages

`.sgba` is SGBA's versioned game distribution format. It is a ZIP-compatible archive containing a root `manifest.json`, `main.py`, optional `game.json`, optional `assets/`, and any additional Python modules. The manifest identifies the format/version, display title, and entry point. The runtime validates the package before extracting it to a temporary cache and launching it. Packages are limited to 32 MiB compressed, 64 MiB unpacked, 2,000 files, and 16 MiB per file. These checks validate package structure and size, not the safety of Python code inside a package. Game code is executable and is not sandboxed.

AI game agents are responsible for creating and validating the `.sgba` package whenever they create or modify a game. They must run the repository packaging tool in their own execution environment and include the resulting package in the repository deliverable. Users should not need to run packaging commands or manually assemble game files. The packaging tool is `tools/package_game.py`; package format version 1 uses ZIP/Deflate and `manifest.json`. The file extension itself is not a security boundary.

## Game API
Each game exports create_game(), returning an object with required draw(surface) and optional update(dt, inputs) and handle_event(event). Runtime owns display setup, event polling, fixed-step updates, scaling, and display flip. update() runs at 60 Hz with dt == 1/60. Input provides down(action), pressed(action), and released(action).

Optional game.json selects logical size 240×160, 320×240, or 640×480. The default remains 640×480. See GAME_DEVELOPMENT.md for the complete contract. games/template is the starter pattern; games/input_lab diagnoses generic SDL input. R36S mapping remains unverified.

## Browser preview
The optional web_preview/ build is a separate proof of concept. Native Python/Pygame remains the source architecture; browser preview success is not evidence of R36S compatibility.
