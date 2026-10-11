# AI Game Agent Instructions

Implement compact 2D games for the SGBA Python/Pygame runtime. Prioritize stable 60 Hz gameplay, low memory use, and low integration cost on Windows and the future R36S target.

## Before editing
- Read AGENTS.md, Current_Work.md, GAME_DEVELOPMENT.md, runtime.py, and the target game's actual files.
- Treat GAME_DEVELOPMENT.md and this file as the game-generation contract. If code and docs conflict, report the mismatch.
- Keep changes scoped. Update Current_Work.md with meaningful implementation changes and test results.

## Hard constraints
- Use Python and pinned Pygame only. No unapproved dependencies, native extensions, subprocesses, network access, or platform-specific APIs.
- Default logical canvas is 640×480. Games may opt into 240×160 or 320×240 using game.json.
- Simulation uses fixed 60 Hz updates. Runtime owns the display, window, scaling, event queue, frame pacing, and display flip.
- Never call pygame.init(), pygame.display.set_mode(), pygame.display.flip(), pygame.event.get(), or create a second game loop from game code.
- Do not modify runtime, launchers, CI, setup scripts, dependency pins, or global input mapping as a side effect of game work.
- No global package installation, firmware/system changes, internet assumptions, absolute asset paths, or untracked required files.
- Never claim R36S compatibility without actual target-device testing.

## Required interface and inputs
Each games/<name>/main.py exports create_game(), returning an object with draw(surface) and optional update(dt, inputs) and handle_event(event). dt is 1/60 seconds per simulation tick. Construction and asset loading happen once. draw() must not advance state.

Use logical actions only: up, down, left, right, a, b, x, y, start, select, guide, l1, r1, l2, r2, l3, r3, rs_up, rs_down, rs_left, rs_right. Use down() for held input, pressed() for a new press, and released() for release. Unknown actions raise ValueError. Physical controller mappings are provisional.

## Virtual-console and art rules
- Default is 640×480; use 240×160 for Game Boy-style games unless the game's UI genuinely needs a larger profile. 320×240 is the intermediate option.
- Origin is top-left; x increases right and y increases down. Prefer integer pixel coordinates, small sprite sheets, tilemaps, consistent sprite origins, and clear layer order.
- Use nearest-neighbor scaling. Do not smooth-scale pixel art.
- Draw only visible map regions. Avoid world-sized surfaces, full-screen animation frames, and high-resolution art in shipped game folders.
- Use compact data for dialogue, inventory, maps, and encounters instead of large pre-rendered images.

## RAM and frame-time budget
The target has 1 GiB RAM shared with the OS and runtime. Treat memory as scarce.
- Keep each game's assets directory at or below 16 MiB unless explicitly approved.
- Default decoded-image budget is 16 MiB and max image dimension 512. For 240×160 games, prefer 8 MiB and 256×256.
- Use PNG for pixel art. Avoid video, oversized audio/fonts, duplicate assets, huge backgrounds, and unnecessary packages.
- Use runtime.AssetStore for cached images, fonts, and sounds. It confines paths to its asset folder and limits decoded image memory and image dimensions.
- Use runtime.SpriteSheet, runtime.Animation, runtime.TileMap, and runtime.draw_sprite for sprite frames, fixed-step animation, visible-region tile rendering, camera offsets, and clipping. Use runtime.SceneManager for small named scenes and runtime.SaveStore for compact JSON saves at an explicitly writable path. Do not build competing abstractions without a concrete need.
- Never load or transform assets, render static text, or allocate large temporary surfaces every frame.
- Cap entities, particles, projectiles, and other transient objects. Prefer grid-based collision and simple spatial checks over heavyweight physics.
- No networking, subprocesses, native extensions, or unnecessary threads.
- 60 Hz is a target to measure, not a guarantee. Optimize bottlenecks rather than silently lowering simulation frequency.

## Assets and manifest
Use games/<name>/main.py, optional game.json, and optional assets/. Resolve paths from Path(__file__).resolve().parent, never the current working directory. Supported manifest fields: logical_size, asset_budget_mb, max_image_dimension, target_fps. Supported sizes are 240×160, 320×240, and 640×480; target_fps is fixed at 60.

Example:

    from pathlib import Path
    from runtime import AssetStore
    ASSETS = AssetStore(Path(__file__).resolve().parent / "assets", budget_mb=8,
                        max_image_dimension=256)
    hero = ASSETS.image("hero.png")
    font = ASSETS.font(12)

## Workflow
1. Inspect the real target folder and current queue.
2. Make the smallest complete change.
3. Validate imports, paths, manifest, and action names.
4. Add headless tests where practical and run compile and unit tests.
5. Update Current_Work.md with exact changes, test results, limitations, and next task.
6. Never treat desktop CI as a hardware test.
