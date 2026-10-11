# Current Work Queue

Last updated: 2026-10-11
Repository: xenodorian/SGBA
Latest verified runtime/package code: 86ac857780da89a177479574a200e79c4961209a

## Handoff protocol
0. **Do not delegate terminal work to the user.** For game tasks, AI agents must create, package, and validate the `.sgba` deliverable in their own environment and include it in the repo/artifact output. If blocked, report the actual blocker; do not tell the user to run commands.
1. Read this file and inspect source before starting.
2. Read AGENTS.md, AI_GAME_AGENT_INSTRUCTIONS.md, and GAME_DEVELOPMENT.md.
3. Keep changes incremental, scoped, and testable.
4. Run checks when possible. Distinguish CI from physical R36S validation.
5. Do not install anything on the R36S, alter firmware/system files, or change global Python. Device work requires verified target details and explicit approval.

## Objective
Build a small, predictable, device-agnostic Python/Pygame virtual console for AI-generated 2D games shared between Windows and a future R36S handheld with 1 GiB RAM. Prioritize stable 60 Hz simulation, low memory use, and a constrained API coding agents can follow.

## Implemented
- `.sgba` version 1 package format: ZIP-compatible archive with root `manifest.json`, fixed-format metadata, Python entry point, optional game config/assets/modules, strict path/format/integrity validation, and bounded archive/file sizes. Runtime accepts `.sgba` paths directly and extracts validated packages to a temporary cache.
- `tools/package_game.py` creates distributable `.sgba` files from development game folders. Package tests cover valid extraction, unsupported versions, traversal rejection, and launchable packaged games.
- Fixed-step 60 Hz simulation with a maximum of five catch-up updates per render frame; excess backlog is discarded to avoid a spiral of death.
- Default logical canvas remains 640×480. game.json supports 240×160, 320×240, and 640×480 profiles.
- Source-aware logical input edges persist until consumed by the first simulation tick.
- Aspect-preserving nearest-neighbor scaling remains runtime-owned.
- AssetStore caches images, fonts, and sounds; blocks paths outside its asset folder; enforces decoded-image budget and image dimension limits.
- SpriteSheet provides row-major sprite frames. Animation advances frame indices by simulation time. TileMap stores compact tile indices and draws only the visible camera region. draw_sprite supports camera offsets and clipping.
- SceneManager provides named scene switching and update/draw/event delegation. SaveStore handles compact JSON saves at an explicit writable path, capped at 256 KiB.
- tools/benchmark_game.py measures update+draw average, p95, and maximum frame cost; CI benchmarks the demo against the 16.67 ms average budget.
- Starter game opts into 240×160 with an 8 MiB decoded-image budget and 256-pixel maximum image dimension.
- AI game contract defines conservative RAM, asset, and frame-time budgets for 1 GiB hardware.
- CI asset audit checks each game's assets directory against a 16 MiB total budget and 4 MiB per-file guardrail.
- Input lab was fixed to read the runtime input object instead of referencing an undefined local variable.
- Browser preview remains a separate proof of concept and does not define native architecture.

## Verification status
- `.sgba` implementation was merged to `main` in `86ac857780da89a177479574a200e79c4961209a`. Pull-request CI passed on Ubuntu and Windows with Python 3.11 and 3.12, including package tests, asset audit, and demo benchmark. The package format does not sandbox Python code. No R36S testing.
- Automated tests cover manifest profiles, input-edge consumption, AssetStore caching/path/dimension/memory limits, sprite sheets, camera clipping, animation, tilemaps, scenes, and save files. CI also audits asset sizes and benchmarks the demo.
- Runtime checks (tests.yml) passed on current main ae8b8d3a44c7789a83c41592390ca02caa10b795 (run 71) and on every main commit since eb18298. Web Preview passed on main (run 17). Earlier red runs (Runtime checks 45/46, superseded) and Web Preview failures on the feature branch (pages deploy from a non-main branch) are not current.
- No R36S deployment or performance test has occurred. Firmware, architecture, Python/SDL availability, and physical controller mapping remain unknown.
- Fixed-step simulation and resource limits cannot guarantee 60 FPS if game work exceeds the device's frame budget.

## Queue

### P7: `.sgba` game package format
Status: IMPLEMENTED ON MAIN, DESKTOP CI PASSED
Validate archive structure, package version, entry point, paths, integrity, and resource limits. Verify packaging tool and direct runtime loading in Windows/Linux CI. This is format validation, not a Python sandbox. PR #1 merged after all four desktop CI jobs passed.

### P0: Safe target deployment
Status: DEFERRED (owner has no further device information; no device-specific work)
Do not run desktop setup on the R36S or modify firmware, system Python, system libraries, boot configuration, or frontend. Determine firmware, architecture/ABI, libc, Python, SDL, display, and controller details before proposing deployment. Require a reversible plan.

### P1: Fixed-step runtime and input edges
Status: IMPLEMENTED, DESKTOP CI PASSED
Verified on main ae8b8d3 (Runtime checks run 71).

### P2: Virtual-console and rendering contract
Status: IMPLEMENTED, DESKTOP CI PASSED
Verified on main ae8b8d3 (Runtime checks run 71).

### P3: Memory and performance budget
Status: IMPLEMENTED, DESKTOP CI PASSED
AssetStore limits cover only assets loaded through the API; asset-folder auditing is a coarse guardrail, not a process-memory profiler.

### P4: R36S hardware validation
Status: DEFERRED, OPTIONAL (owner-run only if desired; runtime must not depend on it)
Confirm firmware, CPU architecture, userspace/libc, Python, SDL, and controller mapping. Test launch/exit, scaling, input, frame pacing, asset loading, and performance on actual hardware. Record measurements.

### P5: Browser preview
Status: DONE ON MAIN, CI PASSED
Keep preview isolated from native runtime. Pages path fix d280b08; Web Preview workflow passed on main (run 17).

### P6: Repository rename
Status: BLOCKED ON REPOSITORY ADMIN ACTION
Desired name: R36S-PyGame-Runtime. Rename through GitHub Settings if desired and verify redirects.

## Device facts (R36S, ArkOS)
Source: owner's launch.log, GitROMs run, CryMon handoff notes, relayed second-hand. Not independently checked by this repo.
Confirmed: ArkOS with PortMaster (/opt/system/Tools/PortMaster, ports in /roms/ports); kernel Linux 4.4.189 (Oct 13 2021), hostname rg351mp; aarch64; glibc (loader cache lists AArch64 and 32-bit hard-float; version unknown, 2.17+ ran an aarch64 binary); libSDL2-2.0.so.0 present for both ABIs (version unknown); app renders 640x480.
Unconfirmed (general knowledge/notes): RK3326 SoC; gptokeyb maps pad to keyboard keys (A=z, B=x observed in GitROMs). runtime.py KEY_MAP already maps z->a and x->b, so keyboard-mapped input should work without code changes.
Unknown: Python and pygame availability and versions, exact glibc/SDL versions, SDL video driver (gitroms.log in the GitROMs data folder records it), real RAM/free disk.
Direction (owner, 2026-10-11): no more device information is available. The runtime must work across many devices; never build device-specific code. Design to SDL2/pygame generics: any display size via aspect-preserving scaling, keyboard plus gamepad (buttons, hats, axes), no assumed hardware.

## Known limits
- Fixed-step simulation and 60 Hz render pacing do not guarantee 60 FPS on hardware that cannot keep up.
- Image budget covers images loaded through AssetStore; games must not bypass it with direct loading or create unbounded surfaces/entities.
- Asset directory audit is a coarse guardrail, not a process-memory profiler.
- No R36S deployment or performance test has occurred.

## Change log
- 2026-10-11: Recorded relayed R36S device facts, stated the device-agnostic direction (device probe tool removed), refreshed CI status for P1/P2/P5 from GitHub Actions on ae8b8d3, removed duplicate bullet.
- 2026-10-11: Added `.sgba` version 1 ZIP-compatible game packages, manifest validation, archive/path/size guards, runtime extraction/loading, packaging CLI, regression tests, and documentation. Desktop CI passed after merge; no R36S testing.
- 2026-10-11: Made AI-owned packaging mandatory: game agents must generate and validate `.sgba` files and include them in deliverables; never ask the user to run terminal commands.
- 2026-10-11: Added fixed-step simulation, low-resolution profiles, cached/budgeted assets, SpriteSheet, Animation, TileMap, draw_sprite, SceneManager, SaveStore, low-memory AI contract, template manifest, asset audit, benchmark probe, and regression tests. Desktop CI passed on eb18298846c86046910b63619a3a405c12dd1cc6; no R36S testing.
