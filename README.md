# R36S PyGame Runtime

A small, cross-platform Python/Pygame runtime for 2D games. The goal is to run the same game code on a Windows development PC and the R36S handheld.

## Current status

This is an early proof of concept, not yet an installable R36S package. The logical game display is 640×480. The runtime provides a common game API, keyboard/gamepad actions, and aspect-preserving display scaling.

**Do not run the desktop Linux setup script on the R36S.** The handheld's firmware, CPU architecture, system libraries, and available Python version must be confirmed first. The eventual handheld package should be self-contained and must not modify system Python or firmware.

## PC setup

Requirements: Python 3.11 or 3.12.

- Windows: run `setup_windows.bat`, then `run_windows.bat`.
- Linux desktop: run `sh setup_linux_pc.sh`, then `./run_linux.sh`.

Setup creates a project-local `.venv` and installs the pinned Pygame dependency there. It does not install Python packages globally. If setup fails, stop and inspect the error instead of installing system packages.

To run automated checks after setup:

- Windows: `test_windows.bat`
- Linux desktop: `./test_linux.sh`

To launch fullscreen, pass `--fullscreen` to the launcher. To launch a specific game, pass its Python file path, for example `run_windows.bat games/my_game/main.py` or `./run_linux.sh games/my_game/main.py`. Without a path, the demo starts.

## Game API

A game file exports `create_game()`, returning an object with:

- `handle_event(event)`: optional; receives Pygame events.
- `update(dt, inputs)`: optional; called each frame. `dt` is elapsed seconds.
- `draw(surface)`: required; draws to a 640×480 Pygame surface.

The shared input object exposes `down(action)`, `pressed(action)`, and `released(action)`. Actions: `up`, `down`, `left`, `right`, `a`, `b`, `x`, `y`, `start`, `select`.

Keyboard defaults: arrows/WASD for directions, Z/Space for A, X/Left Ctrl for B, Q for X, E for Y, Enter for Start, and Backspace for Select. Controller mappings are provisional until tested on the target hardware.

## Compatibility plan

PC and handheld should use the same runtime version and game API. Platform-specific packaging, display scaling, and controller adapters may differ, but game rules and logical rendering should remain consistent. The next handheld step is identifying its firmware and CPU architecture, then testing a reversible, self-contained package without touching the system installation.

See [Game Development Contract](GAME_DEVELOPMENT.md) for rules intended to keep AI-generated games portable. A starter file is available at `games/template/main.py`.
