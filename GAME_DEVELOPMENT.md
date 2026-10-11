# SGBA Game Development Contract

## Fixed virtual console

- Default logical canvas: **640×480**, preserved for compatibility.
- Optional profiles: **240×160** for Game Boy-style games and **320×240** for games needing more UI space.
- Select a profile with an optional game.json beside main.py. Do not resize the logical canvas during play.
- Simulation updates use a fixed 60 Hz step. The runtime owns the window, event polling, frame pacing, display scaling, and display flip.
- Coordinates start at top-left; x increases right and y increases down. Prefer integer coordinates for pixel art.
- Rendering must not advance game state. Keep update logic separate from draw logic.
- Runtime scaling preserves aspect ratio and uses nearest-neighbor sampling.

## Game entry point

Each game folder contains main.py defining create_game(), which returns an object with draw(surface) and optional update(dt, inputs) and handle_event(event). The runtime owns the loop and display. Games must not initialize Pygame, create a window, poll the event queue, or create a second loop.

Logical actions: up, down, left, right, a, b, x, y, start, select, guide, l1, r1, l2, r2, l3, r3, rs_up, rs_down, rs_left, rs_right. Use inputs.down(action) for held state, inputs.pressed(action) for a press edge, and inputs.released(action) for a release edge. Physical controller mappings are provisional until verified on the actual handheld.

## Folder layout and manifest

- Development source: `games/<game_name>/main.py`, optional `game.json`, optional `assets/`, and optional sibling Python modules.
- Distributed package: one `.sgba` ZIP-compatible file containing root `manifest.json`, `main.py`, optional `game.json`, optional `assets/`, and any sibling modules.
- Package manifest fields are `format` (`"sgba"`), `format_version` (`1`), `title` (1-80 characters), and `entry_point` (relative `.py` path). The standard entry point is `main.py`.
- AI agents creating or modifying a game must build and validate the `.sgba` package themselves and include it in the repository deliverable. Never leave packaging or terminal commands as a task for the user. The repository packaging tool is `tools/package_game.py`.
- Runtime validates package paths, manifest version, archive integrity, file count, and size limits before extracting to a temporary cache. Limits: 32 MiB compressed, 64 MiB unpacked, 2,000 files, 16 MiB per file, and 64 KiB for `manifest.json`.
- `.sgba` is a package of executable Python code, not a compiled console ROM. Validation does not sandbox game code.

Example manifest:

    {
      "logical_size": [240, 160],
      "asset_budget_mb": 8,
      "max_image_dimension": 256,
      "target_fps": 60
    }

Defaults are 640×480, 16 MiB decoded-image budget, 512×512 maximum image dimension, and 60 Hz. Supported logical sizes are 240×160, 320×240, and 640×480. target_fps must remain 60. Asset budget must be 1-32 MiB and image dimension limit 16-1024 pixels.

## Memory and performance budget

The target has 1 GiB RAM shared by the operating system, runtime, and game. Never assume all of it is available to the game. Use these conservative defaults:

- Keep the complete assets directory at or below 16 MiB per game unless explicitly approved.
- Keep decoded images under the manifest budget. Prefer 8 MiB or less for a 240×160 game.
- Keep images at or below 512×512 by default. Use sprite sheets and tile sheets rather than many full-screen images.
- Use PNG for pixel art. Avoid video, huge backgrounds, large source art in shipped folders, oversized audio, duplicate assets, and unnecessary fonts.
- Load assets once at initialization. Cache images, fonts, sounds, transformations, and static text. Never reload or transform assets each frame.
- Use tilemaps and compact data for rooms, dialogue, inventory, stats, and encounters rather than giant pre-rendered images.
- Avoid per-frame allocations, full-screen effects, unbounded entities/particles/projectiles, heavyweight physics, expensive pathfinding, networking, subprocesses, threads, and unnecessary packages.
- Draw only visible map regions; avoid unbounded world-sized surfaces. Cap transient entities and use simple grid-based collision where suitable.
- 60 Hz is a target, not a guarantee. If a game misses its frame budget, reduce asset sizes, entities, effects, and per-frame work before increasing system requirements.

## Asset API

    from pathlib import Path
    from runtime import AssetStore

    ASSETS = AssetStore(Path(__file__).resolve().parent / "assets", budget_mb=8,
                        max_image_dimension=256)
    player = ASSETS.image("player.png")
    font = ASSETS.font(16)

AssetStore caches assets, blocks paths escaping its asset folder, and enforces decoded-image limits for images loaded through it. sound() returns None if optional audio cannot initialize; gameplay must not depend on sound. Do not bypass the store with repeated direct asset loads.

## Shared lightweight helpers

The runtime exposes small helpers for common 2D game needs:

- AssetStore: cached image/font/sound loading, path confinement, decoded-image budget.
- SpriteSheet: row-major frames from a single image; supports camera offsets and destination clipping.
- Animation: compact frame-index animation advanced with simulation time.
- TileMap: compact integer grid that draws only the camera-visible tile region; tile index -1 is empty.
- draw_sprite(surface, image, position, camera=(0, 0), clip=None): integer-position blit with optional camera offset and clip rectangle.
- SceneManager: named scenes with add(), switch(), update(), draw(), and optional event delegation.
- SaveStore(path): explicit-path JSON save files, capped at 256 KiB. Choose a path that is writable on the target; the runtime does not assume a particular home directory.

Keep scenes and saves simple. Store compact game state and references rather than duplicate copies of large assets. SaveStore is not an encryption or anti-cheat system.

## Performance probe

Run `python tools/benchmark_game.py games/my_game/main.py --frames 300` to measure headless update-plus-draw cost. It reports average, 95th-percentile, and maximum time and exits nonzero if the average exceeds its configured budget (16.67 ms by default). This is a development smoke test, not an R36S benchmark; only measurements on the actual device establish target performance.

## Portability

- Use Python and pinned Pygame only unless another dependency is explicitly approved.
- Resolve paths from Path(__file__).resolve().parent, never the process working directory.
- Keep controller details out of game logic.
- The browser preview is secondary and does not define the native architecture.
- Do not claim R36S compatibility or performance before actual device testing.


## JavaScript (browser) games

SGBA also runs browser games written in JavaScript: a folder with `index.html`, scripts and assets, drawing to one 2D `<canvas>`. Develop and test them on the web as usual; the same folder runs in SGBA on PC and handhelds. Supported: canvas 2D, images, timers, `requestAnimationFrame`, keyboard, the Gamepad API (fed from SGBA's standard buttons), `localStorage`, `fetch` of local files. Not supported: WebGL, modules (`type="module"`), networking, synthesized Web Audio (oscillators and filters are silent).

**Buttons and menus.** HTML you place over the canvas (menus, loading bars, dialogs) is drawn by SGBA as one centred panel, and every `<button>` (and `a[href]`, `input`, `[tabindex]`) is a focusable row. No pointer is used. Two modes, chosen in an optional `sgba_ui.json` next to `index.html`:
- `"navigation": "engine"` (default): D-pad Up/Down moves focus, A presses the focused button, B goes back (sends Escape, else presses a Back/Close/Cancel button). While a panel is open the pad is hidden from the game so A does not also attack.
- `"navigation": "game"`: the game already moves focus itself from the Gamepad API (as Parry Perry does); SGBA only draws the panel and its focus highlight.
- `"hotkeys": {"select": "btn-hud-start"}` presses the on-screen button with that id when a pad action fires and no panel is open (for a lone HUD button such as "Menu").
Layout is simple on purpose: headings, text, tables, buttons stacked vertically, icons from small canvases, progress bars (`.bar > .fill` with a percentage width). Keep menu markup plain and it will lay out correctly; CSS beyond `display:none` is ignored.

### How JavaScript games use memory, CPU and sound

The engine is built like a small console, to fit a 1 GB handheld with most of the RAM already taken by the system:

- **Drawing is retained and diffed.** Each frame the page's canvas calls are recorded, compared with the previous frame, and only the screen regions that changed are redrawn and sent to the display. A page that repaints the whole canvas every frame (draws an opaque background or cover first) gets this; pages that draw incrementally still work, just without the saving.
- **Images are decoded on first use** into a compact format (opaque, or cut-out sprites with a colour key), kept in a byte-budgeted cache (`image_budget_mb`, default 96) and evicted least-recently-used. Pre-decode the images a level needs behind its loading screen with `"preload": ["assets/layers/*.png"]`.
- **Sound stays compressed.** Music (Ogg Vorbis or MP3, over 96 KB) is streamed from the file by SDL_mixer; short effects are decoded at 22.05 kHz into a 6 MB cache. Use Web Audio `AudioBufferSourceNode` + `GainNode` (gain automation works) or `new Audio(...)`. Ogg is recommended.
- **Frame skipping.** If the device is slow, frames that were never shown are dropped before being drawn; game logic still runs every tick.
- **Colour.** The canvas is 32-bit by default (SDL blends it fastest); `"color_depth": 16` and `"image_depth": 16` cut memory to roughly half but make blending slower, so use them only if RAM is the limit.
- **Telemetry.** Every ~10 seconds a line like `perf: 600 frames, update 1.3 ms, draw 2.1 ms, worst 63 ms, rss 95 MB, images 20/139 ...` is written to `sgba.log` and `sgba_launch.log`. After a play session on a device, send that file to see real timings.

`sgba_ui.json` options: `navigation`, `hotkeys`, `color_depth` (16|32), `image_depth` (16|32), `rle` (true|false), `image_budget_mb`, `alpha` (`"threshold"` cuts faint anti-aliased edges on sprites to on/off transparency, `"keep"` preserves them), `preload`.

Writing for the engine: avoid `getImageData`/`putImageData` (forces slow drawing), avoid per-frame full-screen translucent overlays and gradients (they are blended pixel by pixel), prefer sprite sheets under a few thousand pixels wide, and keep sound files under a few MB.
