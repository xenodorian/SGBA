# Game Development Contract

Use this contract when creating games for this runtime, including with an AI coding assistant.

## Runtime-owned behavior

- Logical screen: 640×480 pixels.
- The runtime creates the Pygame window, manages the frame loop, collects input, and scales the logical screen to the display.
- Do not call `pygame.init()`, `pygame.display.set_mode()`, `pygame.display.flip()`, or run a second event loop inside a game.
- Do not change the logical resolution or assume the physical display is 640×480.
- The runtime targets 60 frames per second. `update(dt, inputs)` receives elapsed seconds, capped at 0.05 seconds per frame.

## Required game interface

Each game is a Python file that defines `create_game()` and returns an object with:

- `draw(surface)`: required; draw the full frame onto the supplied 640×480 surface.
- `update(dt, inputs)`: optional; update game state. Use `dt` for time-based movement.
- `handle_event(event)`: optional; receive Pygame events when needed.

Input actions are `up`, `down`, `left`, `right`, `a`, `b`, `x`, `y`, `start`, and `select`. Use `inputs.down(action)` for held input, `inputs.pressed(action)` for a new press, and `inputs.released(action)` for release.

## Portability rules

- Use only Python and dependencies explicitly approved for the runtime. Do not add platform-specific APIs or dependencies without discussing them first.
- Keep game rules and rendering independent of operating system, window size, and controller model.
- Resolve game assets relative to the game file, not the current working directory. For example: `Path(__file__).resolve().parent / "assets" / "player.png"`.
- Include all required assets in the game folder. Use relative paths and ordinary filenames.
- Do not assume internet access, writable system directories, or access to the user's home directory.
- Avoid large asset files and expensive per-frame work. Load images, sounds, and fonts once during initialization rather than every frame.
- Keep input mappings in the runtime. Games should ask for logical actions, not inspect physical keyboard keys or controller button numbers.

## Running a game

- Windows: `run_windows.bat games/my_game/main.py`
- Linux desktop: `./run_linux.sh games/my_game/main.py`
- Add `--fullscreen` to either command for fullscreen mode.

The template in `games/template/main.py` demonstrates the expected structure. Hardware-specific input and performance still require testing on the target device.
