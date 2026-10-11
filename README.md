# SGBA

A small, resource-conscious Python/Pygame runtime for 2D games. The goal is to use the same game code on a Windows development PC and the future R36S handheld.

## Current status

The native runtime uses a fixed 60 Hz simulation loop, a default 640×480 logical canvas, optional 240×160 and 320×240 low-resolution profiles, source-aware logical input, nearest-neighbor scaling, and cached asset loading with decoded-image limits.

The R36S has 1 GiB RAM shared with the operating system and running software. Memory and frame time are design constraints. The runtime cannot guarantee 60 FPS on untested hardware or against an unbounded game; generated games must follow the resource contract in GAME_DEVELOPMENT.md.

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

To launch fullscreen, pass --fullscreen. To launch a specific game, pass its path, for example run_windows.bat games/my_game/main.py or ./run_linux.sh games/my_game/main.py. Without a path, the demo starts.

## Game API
Each game exports create_game(), returning an object with required draw(surface) and optional update(dt, inputs) and handle_event(event). Runtime owns display setup, event polling, fixed-step updates, scaling, and display flip. update() runs at 60 Hz with dt == 1/60. Input provides down(action), pressed(action), and released(action).

Optional game.json selects logical size 240×160, 320×240, or 640×480. The default remains 640×480. See GAME_DEVELOPMENT.md for the complete contract. games/template is the starter pattern; games/input_lab diagnoses generic SDL input. R36S mapping remains unverified.

## Browser preview
The optional web_preview/ build is a separate proof of concept. Native Python/Pygame remains the source architecture; browser preview success is not evidence of R36S compatibility.
