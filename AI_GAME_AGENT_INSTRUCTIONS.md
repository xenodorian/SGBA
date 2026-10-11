# AI Game Agent Instructions

ROLE: Implement games that run unchanged under this repository's Python/Pygame runtime. Optimize for reliable execution, low integration cost, and portability across Windows PC and a future R36S target. Treat the runtime API as the engine boundary.

SOURCE OF TRUTH:
- Read `AGENTS.md` and `Current_Work.md` before modifying the repository.
- This file is the normative game-generation contract.
- Inspect `runtime.py`, `games/demo/main.py`, and `games/template/main.py` for actual current behavior. Do not hallucinate APIs.
- If docs conflict with code, do not silently invent behavior. Report the mismatch and update the contract only alongside an approved implementation change.

## HARD CONSTRAINTS

- Python + the pinned Pygame dependency only. Do not add dependencies, native extensions, subprocesses, network access, or platform-specific APIs without explicit approval.
- Target logical resolution is exactly 640x480. The runtime owns the window, display mode, scaling, frame pacing, event polling, and display flip.
- Never call `pygame.init()`, `pygame.display.set_mode()`, `pygame.display.flip()`, `pygame.event.get()`, or create a second game loop from game code.
- Do not alter `runtime.py`, launchers, setup scripts, dependency pins, CI, or global input mapping as a side effect of implementing a game. If an engine change is genuinely required, stop and propose the smallest API change; update the engine and all affected tests/docs together only when authorized.
- No assumption of internet, writable system paths, user-home access, current working directory, or a specific controller model.
- No absolute asset paths. No reliance on untracked/generated local files. Commit all required game assets.
- No hidden setup steps. No global package installation. No firmware/system modification.
- Do not claim the game works on R36S until actual target-device testing is recorded in `Current_Work.md`.

## REQUIRED GAME INTERFACE

Each game entry file must define `create_game()` and return an object:
- `draw(surface)`: required. Draw the complete frame to the supplied 640x480 Pygame surface.
- `update(dt, inputs)`: optional. Advance simulation. `dt` is seconds and is capped by the runtime at 0.05 seconds.
- `handle_event(event)`: optional. Receive each Pygame event. Do not consume/poll the event queue independently.

Keep construction cheap and side-effect-limited. The runtime calls `create_game()` once after Pygame initialization. Cache loaded assets/fonts/sounds in initialization; never reload them each frame.

## INPUT CONTRACT

Use only logical action names:
`up`, `down`, `left`, `right`, `a`, `b`, `x`, `y`, `start`, `select`.

- `inputs.down(action)`: true while any mapped source holds the action.
- `inputs.pressed(action)`: true for the frame a logical action transitions from released to held.
- `inputs.released(action)`: true for the frame it transitions from held to released.
- Call these methods with the exact supported action names. Unknown names raise `ValueError`.
- Never inspect keyboard keycodes, joystick indices, button indices, axes, or physical device names inside a game.
- Use `pressed()` for edge-triggered actions such as menu confirmation, attack initiation, pause toggles, and item use. Use `down()` for continuous movement or charge/hold behavior.
- Physical controller mapping is provisional and not yet verified on the R36S. Do not encode assumptions about physical button placement into game logic.

## PORTABILITY / SIMULATION

- Game rules must be independent of OS, physical screen size, input device, and launch working directory.
- Use `dt` for time-based motion and timers; do not assume a fixed number of updates per second.
- Clamp or otherwise handle movement bounds explicitly. Avoid nondeterministic behavior unless required by the design.
- Keep update logic separate from drawing. `draw(surface)` should render current state and should not advance the simulation.
- Use integer pixel coordinates for crisp pixel art when possible; avoid smoothing/scaling individual pixel-art sprites unless the art direction requires it.
- Avoid expensive allocations, image transformations, pathfinding, or full-screen effects in every frame when they can be cached.
- Load assets using a path derived from the game module, e.g. `Path(__file__).resolve().parent / "assets" / "player.png"`. Do not resolve assets relative to `Path.cwd()`.
- Bundle assets within the game's folder. Keep filenames case-consistent and avoid relying on Windows case-insensitivity.
- Prefer Pygame primitives or small local assets over new libraries. If assets are generated, include the actual files in the patch and verify referenced paths.
- Avoid fonts that may not exist on the target. Prefer bundled fonts or Pygame's built-in default font when adequate.
- Audio is not yet validated on the target device. Do not make gameplay depend on audio playback or assume a particular mixer device is available.

## IMPLEMENTATION WORKFLOW

1. Read current work state and inspect the actual target game folder before editing.
2. Implement the smallest coherent playable slice. Do not replace unrelated files or rewrite the runtime to support a game-specific convenience.
3. Use the template as the entry-point pattern. Keep game-specific code/assets under `games/<game_name>/`.
4. Validate every import, asset path, action name, and required interface method. Avoid optional API guesses.
5. Add/update tests for deterministic, headless-testable game logic where practical. Use `SDL_VIDEODRIVER=dummy` for headless CI where appropriate; do not claim this validates graphics/input hardware.
6. Run compile checks and the repository test command if the environment permits. Report exact commands/results; if unavailable, state not run.
7. Update `Current_Work.md` in the same commit: mark task status, record commit SHA, test results, known limitations, and next task.
8. Keep the diff focused. No drive-by refactors, speculative abstractions, generated build outputs, or unrelated README churn.

## DELIVERABLE CONTRACT

- Entry point exports `create_game()`.
- Game runs through existing launch scripts with a path such as `run_windows.bat games/<game_name>/main.py` or `./run_linux.sh games/<game_name>/main.py`.
- Required assets are committed under that game's folder.
- No game-specific runtime modification unless separately authorized.
- Include a concise change summary, files changed, test evidence, unverified assumptions, and any blocker in the handoff.
- Do not mark an item complete if only code was written but tests/device validation remain outstanding.

## WHEN STUCK

- Do not compensate for an unclear requirement by expanding scope.
- Inspect source and tests, then state the exact missing information.
- If an engine capability is missing, propose a minimal interface delta and wait rather than bypassing the runtime boundary.
- If the target hardware's capabilities are unknown, keep implementation within the documented portable subset.
- Preserve current user data, project-local dependencies, firmware, and existing unrelated files.
