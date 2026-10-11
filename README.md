# R36S PyGame Runtime

A small, cross-platform Python/Pygame runtime for 2D games. The same game code should run on Windows during development and on the R36S handheld.

## Current status

This is an early proof of concept, not yet an installable R36S package. The initial target is a 640×480 logical display, gamepad-friendly input, and a minimal game API. The handheld deployment method will be chosen only after checking the R36S firmware and CPU architecture. Nothing here requires installing software on the handheld yet.

## Run on a PC

1. Install Python 3.11 or 3.12.
2. From this folder, install the pinned dependency in a virtual environment:

   `python -m venv .venv`

   Windows:
   ` .venv\Scripts\python -m pip install -r requirements.txt `

   Linux:
   ` .venv/bin/python -m pip install -r requirements.txt `

3. Launch the demo using `run_windows.bat` on Windows, or `./run_linux.sh` on Linux.

You can also run it directly:

```sh
python runtime.py games/demo/main.py
```

Add `--fullscreen` for fullscreen mode.

## Game API

A game file exports `create_game()`, returning an object with:

- `handle_event(event)`: optional; receives unhandled Pygame events.
- `update(dt, inputs)`: optional; called each frame. `dt` is elapsed seconds.
- `draw(surface)`: required; draws to a 640×480 Pygame surface.

The shared input object exposes `down(action)`, `pressed(action)`, and `released(action)`. Actions: `up`, `down`, `left`, `right`, `a`, `b`, `x`, `y`, `start`, `select`.

Keyboard defaults: arrows/WASD for directions, Z/Space for A, X/Left Ctrl for B, A for X, S for Y, Enter for Start, and Backspace for Select. Gamepad buttons are mapped to these same actions where SDL provides standard controller events.

## Compatibility goal

PC and handheld should use the same runtime version and game API. Screen scaling and platform launch scripts may differ, but game rules and logical rendering stay the same. Exact R36S packaging is not implemented until the firmware and CPU architecture are confirmed.
