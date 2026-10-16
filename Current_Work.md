# Current Work Queue

Last updated: 2026-10-15
Repository: xenodorian/SGBA
Latest verified runtime/package code: 86ac857780da89a177479574a200e79c4961209a

## Handoff protocol
0. **Do not delegate terminal work to the user.** For game tasks, AI agents must create, package, and validate the `.sgba` deliverable in their own environment and include it in the repo/artifact output. If blocked, report the actual blocker; do not tell the user to run commands.
1. Read this file and inspect source before starting.
2. Read AGENTS.md, AI_GAME_AGENT_INSTRUCTIONS.md, and GAME_DEVELOPMENT.md.
3. Keep changes incremental, scoped, and testable.
4. Run checks when possible. Distinguish CI from physical R36S validation.
5. Do not install anything on the R36S, alter firmware/system files, or change global Python. Device work requires verified target details and explicit approval.

## Target requirement (owner, 2026-10-11)
Must run on any version of the R36S running ArkOS 2, and on a Windows PC. The handheld has no keyboard, terminal, or internet, and the owner will not install anything: the project must be fully self-contained and work from the handheld's own menu. Implemented as:
- Self-contained bundles (tools/build_bundle.py): ArkOS aarch64 (portable Python 3.11 + pygame 2.6.1 + libz, SGBA.sh launcher) and Windows x64 (SGBA.bat). All downloads are pinned and SHA-256 verified. CI builds both and runs the test suite with the bundled interpreters (ARM64 under qemu).
- On-screen shell (sgba_shell.py): game menu, first-run controller setup wizard that records whatever the pad sends (button/hat/axis) to controls.json, on-screen error screen, sgba.log. Start+Select returns to the menu.
- Video: SDL auto driver first, then kmsdrm/x11/wayland/fbcon/directfb. Launcher prefers the device's own libSDL2 via LD_PRELOAD and retries without it if the first attempt fails within 20 seconds.
Unverified on real hardware (nothing here has touched a device): LD_PRELOAD of the system SDL2 over the bundled one, KMSDRM/Mali display output, pad button numbering (the wizard exists because of this), audio, execute permission of the SD card mount, and 32-bit ArkOS builds (not supported; bundle is aarch64 only).

## Slingshot Orbits port (owner request, 2026-10-14)
Source: an uploaded single-file browser game (slingshot_orbits_v6.html). Ported to an SGBA browser game with analog controls.
Controls: left stick sets the slingshot direction (the band is pulled toward the stick, so the ship flies the opposite way); right stick is a 360 degree dial with up = 0 (no power), left = 90 degrees (25 percent), down = 180 (50 percent), right = 270 (75 percent), wrapping; R1 launches. Start retries the orbit, Select opens the difficulty menu. Keyboard and d-pad feed the same axes when no stick is pushed.
Files:
- `web_games/slingshot_orbits/`: `index.html` (game, canvas 640x480), `physics.js` (physics, generator and solver shared with the level tool), `levels.js` (20 precomputed levels, generated), `sgba_ui.json`.
- `games/slingshot_orbits.sgba`: the packaged game (validated with `sgba_package.validate_package`, opens and starts through `runtime.open_game`).
- `tools/slingshot_levels.js`: regenerates `levels.js` with the browser build's search (Node). The search takes 0.3 to 3 s per orbit in V8 and minutes per orbit in QuickJS, so SGBA cannot run it on the device. Every stored launch is re-simulated and must win; all 220 do, all are strict with no shortcuts. Orbits 1 to 20 are keyed by planet count, and orbits 21 and up cycle through 21 to 30 at 20 planets.
Framework changes (small, additive):
- `runtime.py`: `InputState.stick(name)` reads a stick's analog axes live from the first connected pad.
- `sgba_js.py`: the page's pad feed carries analog stick axes (previously the sticks were digital only, and the left stick sent no axes at all).
Tests: `tests/test_slingshot.py` (5 tests: levels shipped, menu start, dial clock face, dial hold, stored launch wins orbit 1 through the sticks). `tests/test_js.py` gained 3 tests and `tests/test_runtime.py` gained 1 for the analog path. Full suite: 80 tests pass. `compileall`, `audit_assets` and the demo benchmark pass.
Verified in the headless host: menu to launch, aim and dial, R1 flight, crash, and the stored winning launches for orbits 1 to 3 through the real controls (score 1000, 2000, 3000 with two shots left). Frame cost on this PC (QuickJS, headless, update plus draw): median about 5 ms while aiming and in flight on a 5-planet level (p95 about 6.5 ms), with the aim preview recalculated every 4 frames. Not measured on any handheld, and a handheld CPU is likely several times slower, so this is the first thing to measure on the device.
Decisions to confirm with the owner:
- Left stick direction: the stick is the pull point, so the launch goes the opposite way (as the mouse drag did). Flip the sign in `index.html` `applyAim()` if the owner wants the ship to follow the stick.
- Audio: the original's synthesized sounds were dropped because SGBA's JS host renders Web Audio as silence. The music was restored on 2026-10-15 (see "Slingshot music" below); the sound effects (launch, dust, boom, win) are still not in the port.
- Difficulty: the mouse tolerance (pointer pixels) that the level search used is kept, but a stick's precision is different, so playtesting may show levels are harder or easier than the browser build.
Not done: no handheld test; the R36S axis numbering is unverified (the default map uses axes 0 to 3, the same as the runtime's default).

## JavaScript game hosting (owner direction, 2026-10-11)
Purpose: SGBA hosts browser games written in JavaScript (Parry Perry in xenodorian/Biboo, CryMon in xenodorian/CryMon) so they can be developed and tested on the web and run locally on PC and ArkOS handhelds with only a thin port. Python games remain supported.
Implemented (spike, branch claude/epic-franklin-qljrs1): sgba_js.py (QuickJS-ng host, canvas 2D on pygame, Image, timers, rAF, events, localStorage, fetch for local files, gamepad from SGBA input) and sgba_js_shim.js (browser shim, tolerant DOM stub). JSGame is a normal SGBA game object, so menu, controller setup and packaging work unchanged. quickjs-ng 0.17.0.1 is bundled for ArkOS aarch64 and Windows (prebuilt wheels, pinned and checksummed). tools/js_spike.py runs a web folder headless and saves a screenshot. tests/test_js.py covers canvas, timers/rAF/microtask order, images and transforms, storage, gamepad, fetch.
Spike result on Biboo web/ (Parry Perry): all scripts load with zero errors, the title screen renders from the real game code. Full suite (38 tests) passes with the bundled ARM64 Python under qemu. NOT run on hardware.
Known gaps, in priority order:
1. DOM UI: DONE for Biboo's menus (see below). Remaining: more CSS fidelity (only display:none is honoured), CryMon-scale React UIs untested.
2. Speed. On a desktop CPU the Biboo title screen costs about 8 ms per frame, mostly pygame blits of full-screen layers and gradients rebuilt every frame (JS itself is small). A handheld CPU will be several times slower, so caching gradients, avoiding per-draw alpha copies and pre-scaling layers is needed before the 16.7 ms budget can be met. Unmeasured on hardware.
3. Rendering fidelity: Biboo title text lacks the gold gradient and outline of the browser version, and a black rectangle appears at the ground in the first frames (unexplained). Compare against the Chromium reference screenshot.
4. Audio: only Sound playback of local files is forwarded; Web Audio nodes are silent stubs. Module scripts (type=module) are not supported. getImageData/putImageData are slow hex bridges.
5. Packaging: a JS game needs a .sgba layout/manifest entry (entry main.py calling sgba_js.create_game_for) and a documented port recipe; CryMon (TypeScript/React/Vite build, 51 .ts files) would run from its built static output, and is far more DOM-heavy than Biboo, so its feasibility is unassessed.

### HTML buttons made pad-navigable (owner priority, 2026-10-11)
Implemented in sgba_js_shim.js (real DOM tree, selector engine, innerHTML parser, focus/click/back, overlay snapshot) and sgba_js_ui.py (layout, drawing, focus navigation). Overlays are the page elements laid over the main canvas; they are drawn as one centred panel with every button a focusable row; no pointer. Modes via optional sgba_ui.json: "engine" (default; D-pad moves focus, A presses, B back, pad hidden from the game while a panel is open), "game" (the game moves focus itself, SGBA draws), plus "hotkeys" for lone HUD buttons. Verified on Parry Perry: start menu, Moves tables with row cursor and Back, scrolling, both modes, via tests/test_js.py (10 tests) and screenshots. Parry Perry should ship with {"navigation": "game"} since its own pad menu code (17_menus.js) already drives UI.nav/activate/back.

### Efficiency redesign (owner direction, 2026-10-11)
Goal: stop redrawing everything in Python each frame and stay inside ~1 GB RAM shared with the OS, taking lessons from mGBA and the GBA (fixed small memory, compact formats, update only what changed), with compressed sound.
Implemented: sgba_gfx.py (lazy, byte-budgeted ImageStore decoding on first use to compact opaque/colour-key surfaces; LRU caches for flipped/scaled/faded sprites, text, tiles), sgba_canvas.py (canvas ops are recorded; the main canvas diffs each frame's op list against the last, redraws only changed regions, falls back to plain drawing when a page is not a full repaint or reads pixels back; frame-skipping by dropping unshown complete frames), runtime.run_scene presents only changed rectangles (nothing at all when nothing changed), sgba_audio.py (music streamed from Ogg/MP3 via mixer.music, effects decoded at 22.05 kHz into a 6 MB LRU, Web Audio gain graph with automation and <audio> mapped onto it), preload list, gc.freeze after load, perf telemetry lines in sgba.log, and a dependency-free stub for the MIDI synthesizer so the ARM bundle needs only the C library.
Measured on the same Parry Perry gameplay script (desktop CPU, so use as relative numbers): average frame 6.5 ms -> about 4.6-5.2 ms, p95 12.8 -> 7 ms, RSS 300 MB -> 95 MB (images 240 MB -> 30 MB decoded), pixel work per frame about 6x lower, music costs ~0 MB decoded. Matrix result: a 32-bit canvas with 32-bit images and RLE for cut-outs is fastest; 16-bit saves RAM but SDL blends it slowly (full-screen translucent overlay 4.6 ms vs 0.2 ms), so 16-bit is opt-in.
Known limits and next steps: (1) the game's own JavaScript (QuickJS, no JIT) is now about a third of frame time on desktop and unmeasured on the R36S CPU; the new perf log lines exist to measure it on the device. (2) Scrolling levels still redraw nearly the whole screen each frame (12+ parallax layers); a scroll-blit trick or a render_scale option would help if needed. (3) First draw of each image costs 10-15 ms desktop (decode); use "preload". (4) Full-screen translucent overlays/gradients are the most expensive remaining draw ops. (5) Nothing here has run on an R36S.

### BibooSGBA port (owner direction, 2026-10-12)
The Parry Perry port lives in github.com/xenodorian/BibooSGBA (main repo going forward; old xenodorian/Biboo is an untouched archive). Engine support added here: `.sgba` manifest `runtime: js`, loose web-folder games (run/list/package), `hide` and `title` in sgba_ui.json, per-game save folders, cache dir on disk (not temp RAM), single-game bundles with direct launch and a launcher override argument (tools/build_bundle.py --web/--name/--ui-config/--exclude), tools/sgba_check.py (feature scan + load test), gradient-filled canvas text. BibooSGBA's sgba/sgba.ref pins this branch (claude/epic-franklin-qljrs1) until it is merged.

## ArkOS 2 research (web, 2026-10-11)
Source: geniot/arkos-docker installed.txt (reproduces ArkOS 2.0 developer-mode packages; not an official image listing): Ubuntu 19.10 base, python3 3.7.5, libsdl2-2.0-0 2.0.10, libc6 2.30, arm64 and armhf. No pygame package listed. Conclusion: system Python is 3.7 and pygame is probably NOT installed, so the project bundles its own Python and pygame instead of relying on the device. Developer-path floor remains Python 3.7, SDL 2.0.10. PortMaster ships its own runtimes per port rather than altering the OS. Unconfirmed: any pygame wheel for aarch64 cp37 using system SDL2 with KMSDRM video; pip availability on the device.

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

### Device report review (2026-10-15)
- Reports from the R36S (build 8070abc): the crash is `text_shutdown` at exit (text.c:146, symbolized with an unstripped build of the same commit), the double free already fixed in 0e5c169. The device needs a build at or after 0e5c169.
- Music: Parry Perry's streams are 32 kHz mono, resampled to 22.05 kHz. The resampler dropped the fractional position at each 2048-frame chunk seam (a click about every 93 ms). Fixed with a carried frame; `native/tests/audio_test.sh` measures it (old 168 vs limit 106, new 75). Audio now logs device open, music start and underruns. (Superseded for Slingshot: it now has music, see "Slingshot music".)
- Sticks: Slingshot reads axes 0-3 of the standard pad; `native/tests/stick_test.sh` covers that. Device axis numbering is still unconfirmed: the report log line "axis N first moved" will show it. Use `"axes"` in controls.json to remap.

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
- 2026-10-12: Browser-game packaging and single-game bundles for the BibooSGBA port; sgba_check; gradient text; hide/title settings; per-game saves.
- 2026-10-11: Efficiency redesign for JS games: retained/diffed canvas with dirty-rect presentation, lazy budgeted image store, compressed streaming audio, caches, telemetry; RSS 300 -> 95 MB and faster frames on the Biboo test script; ARM bundle tests pass under qemu; not run on hardware.
- 2026-10-11: HTML overlay buttons now pad-navigable in JS games (real DOM, overlay drawing, engine and game navigation modes, hotkeys), tests and docs. Biboo menus work in both modes; not run on hardware.
- 2026-10-11: JS hosting spike (sgba_js.py, shim, tests, quickjs-ng in bundles). Biboo loads and renders its title screen with zero script errors; DOM menus, speed on handhelds and fidelity gaps remain.
- 2026-10-11: Self-contained bundles for ArkOS aarch64 and Windows x64, on-screen shell (menu, controller wizard, error screen, logs), custom pad bindings, video-driver fallback, bundle CI jobs. Tested: full suite with bundled ARM64 Python 3.11 + pygame 2.6.1 under qemu, plus desktop 3.8 and 3.13. No hardware testing.
- 2026-10-11: ArkOS 2 / Windows support work: Python 3.7 and pygame 2.0 floor with CI job, joystick-id fallback, run_arkos.sh, Start+Select quit. Tested locally on Python 3.7+/pygame 2.0.1 and 3.13/2.6.1; not on hardware.
- 2026-10-11: Recorded relayed R36S device facts, stated the device-agnostic direction (device probe tool removed), refreshed CI status for P1/P2/P5 from GitHub Actions on ae8b8d3, removed duplicate bullet.
- 2026-10-11: Added `.sgba` version 1 ZIP-compatible game packages, manifest validation, archive/path/size guards, runtime extraction/loading, packaging CLI, regression tests, and documentation. Desktop CI passed after merge; no R36S testing.
- 2026-10-11: Made AI-owned packaging mandatory: game agents must generate and validate `.sgba` files and include them in deliverables; never ask the user to run terminal commands.
- 2026-10-11: Added fixed-step simulation, low-resolution profiles, cached/budgeted assets, SpriteSheet, Animation, TileMap, draw_sprite, SceneManager, SaveStore, low-memory AI contract, template manifest, asset audit, benchmark probe, and regression tests. Desktop CI passed on eb18298846c86046910b63619a3a405c12dd1cc6; no R36S testing.


## Native runtime (C) in progress

The plan and checklist are in `NATIVE_TODO.md`. Entries below are dated notes as each step is pushed.

### 2026-10-12: native runtime, list A done (A1 to A10)

- `native/` holds a C runtime: vendored QuickJS-ng 0.17.0 (full source from the v0.17.0 tag, the PyPI sdist lacks generated headers), stb (image, truetype, vorbis, image_write), SDL2 headers (types only; SDL2 itself is loaded at run time with dlopen / LoadLibrary).
- It runs the same game folder: `sgba_js_shim.js` is embedded unchanged, `__py_*` natives are implemented in C (canvas, images, audio, storage, files).
- Canvas 2D in C follows `sgba_canvas.py`: op recording, frame diff, dirty rectangles, groups, composite modes, text (stb_truetype with pygame's default font), gradients, shear/rotate.
- Audio: Ogg and WAV via stb_vorbis, 22.05 kHz mixer in the SDL callback, streamed music decoded by a helper thread, small SFX cache. Mixer output checked through SDL's disk audio driver.
- Input: keyboard and pad, `controls.json` bindings, raw presses for the setup page. Menus (launcher, pause, controller setup) are a web page (`native/shell/shell.html`) run by the same runtime.
- Build: `make -C native` (x86_64), `make -C native TARGET=aarch64`. Run: `native/build/x86_64/sgba GAME_FOLDER [--frames N --shot out.png --script S --js F=CODE]` for headless runs, or without `--frames` for a window.
- Speed (aarch64 build under QEMU, one core, steady state, per frame): title 12.6 ms, level 1 14.7 ms, level 4 13.5 ms. The Python runtime measured 54, 28 and 38.8 ms in the same simulator. The simulator is a proxy, not the device.
- Not done yet: HTML overlay menus (B1), pixel parity with the Python canvas (B2), packaging and the R36S glibc baseline (B3). Intro-scene fixes are on the list as D1, D2.

2026-10-12: native runtime, B1 done (HTML menus over the game)
- native/shell/overlay.js (port of sgba_js_ui.py) lays the page's buttons out on a panel canvas; the runtime darkens the game (alpha 110) and draws the panel over it.
- Pad navigation in "engine" mode (up/down with repeat, A click, B back, hotkeys, neutral pad to the game while a menu is open); "game" mode only draws. Config keys navigation, hotkeys, hide read from sgba_ui.json.
- Checked on the Parry Perry start menu: panel draws, focus and clicks work. Not yet run on a real display or pad.

2026-10-12: native runtime, B2 done (drawing parity with the Python canvas)
- tools/parity.py draws the seven reference scenes (same seed, scripts and fixed step as pixel_check) with both runtimes and reports pixels that differ by more than 8 per channel; diff images in native/build/parity. Needs pygame and quickjs-ng on the PC (pip install pygame quickjs-ng).
- Result: game scenes 0.02% to 0.13% of pixels differ (HUD and label text, a few pixels on rotating coins); title scene 2.7% (menu text). Tolerance is 3% per scene.
- Fixes found by the comparison: headless step now follows the game's fps (30), thick lines use pygame's algorithm (crate crosses), text metrics (height, ascent, advances, descender line heights) now come from pygame's own tables (tools/gen_font_metrics.py -> native/src/fontmetrics.h) so menus lay out the same, engine navigation only forces focus in "engine" mode.
- Still different on purpose: glyph shapes (stb_truetype against hinted SDL_ttf, pygame text looks slightly bolder), 16-bit colour of the menu panel in the Python runtime.

2026-10-12: native runtime, B3 done (R36S build baseline and packaging)
- Release builds use zig as the C compiler (pip install ziglang, pinned in CI): `make TARGET=aarch64` links against glibc 2.17 (objdump shows only GLIBC_2.17 symbols; ArkOS 2 is glibc 2.30) and `make TARGET=windows` makes sgba.exe, no separate toolchains. Windows fixes found by that build: SDL_CreateThread takes the caller's thread start functions, CreateWindow macro clash, SDL_MAIN_HANDLED, strcasestr and setenv shims.
- tools/build_native_bundle.py builds the bundle like the Python one: Parry Perry folder + sgba_ui.json, controls.json (R36S mapping), the launcher script (device SDL first, retries with the shipped libSDL2-sgba.so, same as before) or .bat, README, zip. SDL2 is taken from the pinned, checksummed pygame wheel. Parry Perry R36S bundle: 23.4 MB zip (the generic Python ArkOS bundle without a game is 30 MB).
- .github/workflows/native.yml builds all three targets, smoke-tests the PC build and rejects any glibc symbol newer than 2.17.
- Checked: aarch64 binary under QEMU gives a byte-identical screenshot to the x86_64 build; the full bundle starts under QEMU with the shipped SDL and draws the title.
- NOT checked: nothing has run on the real R36S yet (video driver, pad, sound, real speed), and sgba.exe has not been run (no Windows or wine here; it links and the code paths are the same as Linux apart from LoadLibrary and the thread call).
- Next: owner test of the native bundle on the R36S; C1/C2 (retire Python) only on the owner's word. Later game tasks: D1, D2.

2026-10-12: D1 and D2 done (BibooSGBA st177)
- Cause: the story pictures are 216 px tall panoramas but were drawn at the top of the 480 px canvas without clearing, so the title scene (and its Perry) showed below. drawStoryBg in game/18_story.js now clears and stretches the 384x216 view of the picture over the whole 640x480, the mapping the Perry and twin placements already use.
- The fire and double pages still show Perry and the red twin together; that is the story (the figure on the lawn, the Double), not the leak.

2026-10-13: native runtime opens .sgba packages directly
- Owner's report: the GitROMs download put the whole unpacked project on the device (ports/parry-perry with manifest.json and the game files), and they expected a single .sgba that the emulator runs.
- Finding: the rolling release of BibooSGBA already carries parry-perry.sgba (19 MB, BIBOO_VER st177, CRATE_SCALE 2.8), so the content was current; the native runtime could only run folders, never a .sgba.
- native/src/pkg.c: the runtime now takes a .sgba path (command line, or listed in the launcher from games/*.sgba with the manifest title). It unpacks once into cache/<name>-<size>-<mtime> next to the program and runs from there; a changed file unpacks again and the old copy is deleted. Path and size limits as in sgba_package.py. Tested with the released parry-perry.sgba (same picture as the folder run) and under QEMU in the aarch64 bundle.
- tools/build_native_bundle.py now ships games/<name>.sgba (one file) instead of a game folder; the launcher script starts it by that path.
- Not done: GitROMs (separate repo, not read) still decides what it downloads and where it puts it; for the native runtime it should place the single parry-perry.sgba in sgba/games/ and not unpack it. Not run on the real R36S.

## Slingshot Orbits: handheld controls, speed pass and its own launcher (owner request, 2026-10-14)
Owner report on the R36S: the game ran badly and no button worked. Causes found: (1) it was steered with analog sticks, but the pad mapping shipped in the emulator bundle had no stick bindings and no `controls.json` at all (the Parry Perry bundle carried the real device mapping, the emulator bundle did not), so power could never be set and R1 never launched; (2) it drew about 134 canvas operations per frame (80 individual star fills, per-planet pulsing rings with huge alpha bounding boxes, per-segment trail strokes, per-frame text, dashed preview lines) and computed a 1400-step trajectory preview every 4th frame.
Changes (all in `web_games/slingshot_orbits/index.html`; `physics.js` and `levels.js` untouched, so the level solver still agrees):
- Controls work with no sticks: D-pad up/down turns the aim, left/right sets the power, B held makes both slow, A or R1 launches, Start retries, Select opens the menu. Real analog sticks still work (left stick aim, right stick dial). The pad feed mirrors the d-pad onto the left-stick axes, so the stick path is ignored while a d-pad direction is held. Starting power is 50 percent. The press that chooses Launch in the menu no longer fires a shot (`launchLock`).
- Speed: the background, stars, planet sprites, rocks, border, faint rings, portal glow and help text are baked into one layer per orbit (`bakeScene`); the HUD and the power label are baked text strips redrawn only when a value changes; the trail is 3 strokes, particles are capped at 64 and drawn as small rects from a fixed palette, the ship, dust and portal use computed coordinates instead of save/translate/rotate; collisions use an 8 px lookup grid; gravity uses one flat array; the preview is the exact flight computed 140 steps per frame and restarted when the aim changes.
- Measured headless on a desktop CPU (relative numbers): canvas ops per frame 134 -> about 30; average frame 8.7 ms -> about 3 ms; p95 15.8 -> about 5 ms. The preview and the stored launches match the original `sim()` exactly (480 shots over 12 levels, 0 mismatches, node check), and the 5 existing Slingshot tests still pass. Not measured on the R36S.
- `controls-arkos.json` (the R36S mapping captured on the owner's device, copied from BibooSGBA `sgba/controls-arkos.json`) is now shipped as the emulator bundle's default `controls.json` (`--controls` in the CI bundle step), so no game asks for the controller setup again. The player can still remap from the pause menu.
- Own launcher: `tools/build_game_port.py` builds `slingshot-orbits-port.zip` = `Slingshot Orbits.sh` + `slingshot-orbits-game/slingshot-orbits.sgba`; CI publishes it to the `latest` release. The launcher runs the separately installed emulator (`SGBA.sh`) on that file, so the game shows in the Ports list. `tests/test_slingshot_dpad.py` covers the d-pad, the first-press guard, the package, and a ceiling on canvas ops per frame.
- Open: the game still lives in this repo (`web_games/slingshot_orbits/`, `games/slingshot_orbits.sgba`); the owner wants games in their own repos. Moving it needs a new repository.

## Speed pass: native runtime becomes the emulator (owner order, 2026-10-14: "make it fast", Python may go)
Measured first (desktop CPU, so ratios only): Slingshot frame 2.2 ms in the Python runtime, 0.78 ms in the C runtime; Parry Perry 4.1 ms in C. Profiles of the C runtime: 75 percent of Parry Perry's time was per-pixel blending (`r_blit`, `r_comp_px`, `over`, gradient fills), 5 to 9 percent the JavaScript itself. Python's cost was the canvas layer, not JavaScript.
Done in `native/`:
- SIMD span kernels (`r_span_over`, `r_span_fill_over`, `r_span_fill_comp`, `r_span_comp`) in SSE2 and NEON, used by blits (scaled and flipped rows are gathered first), constant fills, gradient fills (a row buffer, not a per-pixel call) and masks. `tests/raster_test.c` compares every kernel with the scalar code on random data (920000 comparisons, exact for the over modes, within 2 for multiply, lighter and atop): run natively and for aarch64 under qemu. Drawing parity against the Python canvas is unchanged; against the previous C build the largest channel difference is 1.
- Threaded repaint: a region of 30000 pixels or more is cut into strips painted on every core (`pool.c`, at most 4 threads, `SGBA_THREADS` overrides). Images and text a strip reads are loaded first; a miss while painting is flagged and the region is painted again on one thread. Output is byte-identical to one thread on all 7 reference scenes.
- Build: -O3, QuickJS -O3 -DNDEBUG, `-mcpu=cortex_a35` for the R36S SoC (tuning only), QuickJS cycle collector threshold 24 MB.
- Result, Parry Perry headless on 4 cores: 4.1 ms -> about 1.0 ms per frame (3.8 s for 3000 frames including 0.4 s start; 1 thread 5.5 to 7.5 s). Slingshot: 0.78 -> about 0.45 ms. Not measured on an R36S; the `perf:` lines in `sgba/sgba.log` will show update, draw and present times there.
- Packaging: `tools/build_native_emulator.py` builds the emulator package (about 1.5 MB, no games but the demo and input lab, `controls.json` from the R36S mapping, SDL2 fallback library). CI publishes it as `SGBA-arkos-aarch64.zip`. A game's own launcher still runs `SGBA.sh path/to/game.sgba`; a relative path now survives the launcher's cd.
- Gemini's four suggestions, checked: no DOM here (already true, canvas only); GPU compositing not taken (the present path is one 640x480 upload, and the software path is no longer the bottleneck; revisit with device `present` numbers); GC: QuickJS frees by reference counting, threshold raised; bare metal: already (one binary, SDL2 on KMSDRM, no browser or X11).
- Not done, ideas: render and JavaScript on separate threads (needs offscreen-canvas ownership rules), a tile or span cache for parallax layers, 16-bit output on the device if present time dominates.

### 2026-10-14: review of ChatGPT's optimisation list (source-level review of this repo), what was taken
- Taken: (1) scratch buffers per thread (`r_scratch`) for the scaled/flipped blit rows and the polygon edge list, no malloc per draw; (2) the gradient row buffer uses the same scratch; (5) rotated sprites: column and row maps built once per op, source coordinates stepped per pixel instead of recomputed with cos/sin/floor and two 64-bit divisions per pixel, solid pixels stored directly. Measured with callgrind (instruction counts are exact): Parry Perry, 500 frames, one thread: 4.08 G -> 3.30 G (-19 percent). Output is byte-identical to the build before on the 7 reference scenes and on Parry Perry frame 1200 (seeded Math.random); incremental, full and 1-vs-4-thread repaint still agree.
- Already done before the review: SIMD blending, threaded repaint, tile dirty tracking (replaces the repeated rectangle merge; `merge_rects` now only joins a handful of rectangles for the display), scaled presentation column map.
- Not taken, with reasons: partial scaling of the display (the R36S panel is 640x480, so the display is never scaled there; only desktop windows are, revisit if a scaled device appears); the Python preload loop and `sgba_canvas.py` merging (the Python runtime is no longer shipped); audio (not in any profile, already streamed and threaded); a rotated-sprite cache (rotation was about 1 percent of Parry Perry's time, not worth the memory).

### 2026-10-14: menu fixes (owner report: SGBA jumped straight into the demo; Slingshot was not listed)
- Cause of the jump: the launcher page treated the A press that started SGBA from EmulationStation, still held when the page loaded, as "choose the first entry" (the demo). The page now ignores the pad until every button has been released once (`armed` in `native/shell/shell.html`); this also covers returning from the pause menu.
- The list now also shows the games installed next to the emulator: every `<name>-game` folder beside `SGBA.sh` holding a `.sgba` (Parry Perry, Slingshot Orbits), plus `sgba/games` (demo, input lab). Sorted by title. The games stay their own packages; only the list is shared.

## Slingshot controls (analog stick support rolled back)
- D-pad swapped: up/down = power, left/right = aim rotation (B = fine). Game-only change.
- The native runtime change that exposed analog stick axes (3b95703) was reverted: after that build the owner reported the emulator and every game exiting back to the menu on the R36S. It could not be reproduced on PC or under qemu (aarch64, headless, ASAN input harness, Parry Perry with axis input), so the cause is unconfirmed. native/src/input.c, input.h and main.c are back to e995c8f (the last build confirmed working on the device).
- To retry sticks later: get sgba/sgba.log and sgba/sgba_launch.log from the device after a failed launch first.

## Diagnostics: reports, crash handler, frame statistics (native runtime)
- `native/src/report.c`. Files go to `<emulator folder>/reports/` (on the card: `sgba/reports/`):
  - `sessions.log`: one line per game session (secs, loops, avg/max loop ms, on-time %, late/slow/stalled counts, rss, JS errors, fps, build, how it ended). Capped near 48 KB.
  - `crash-<epoch>.txt`: written from the signal handler (SEGV, BUS, ILL, FPE, ABRT) with signal, fault address, backtrace (`sgba+0xOFFSET` is the position inside the build) and the last 48 log lines. Verified on x86 and on aarch64 under qemu (zig glibc 2.17).
  - `unclean-<time>.txt`: next start finds `.running` (rewritten every 10 s, removed on a clean exit) and reports the old state plus the `sgba.log` tail. Covers kill, power loss, hangs.
  - `errors-<time>.txt`: a session that logged errors (JS exceptions are logged as errors).
  - `failed-<time>.txt`: SGBA returned a non-zero code outside a game.
  - Newest 20 `.txt` reports are kept; uploaded ones move to `reports/sent/`.
- All games run inside SGBA, so they are covered; the game name is in every report. The build id is the git short hash (`SGBA_BUILD`).
- Test: `native/tests/reports_test.sh` (hooks: `SGBA_TEST_CRASH_AT_START=1`). Not measured on the device.

## Crash found from the first uploaded reports (2026-10-14)
- Reports on the owner's device (issues #1 to #3 in xenodorian/GitROMs) showed `signal 6 (abort)`, "double free or corruption", backtrace symbolized with an unstripped build of the same commit: `text_shutdown` (text.c) called from `natives_shutdown` / `host_destroy` in `run_interactive`.
- Cause: the text cache is global but every host (game, pause menu, launcher) calls `text_shutdown()` when destroyed. It freed `m.a` without clearing it, so the second host destroy freed the same buffers again. It only happens after a pause menu or the launcher has been used, which is why direct launches exited with code 0. This is the "games go to a loading screen and drop back to the menu" crash. It was NOT the analog stick change.
- Fix: clear each cache slot (and the byte count) after freeing. Test: `native/tests/text_test.c` (AddressSanitizer; fails with a double free on the old code, passes now).
- Other finding in the reports, not fixed: one long Parry Perry session grew from 430 MB to 508 MB rss over about 12 minutes on a 897 MB device (possible leak), and update times spiked to 10 to 15 ms at times.

## Memory leak, frame spikes, analog sticks, launcher retry (2026-10-14)
Evidence: the first uploaded reports (xenodorian/GitROMs issues #1 to #3) showed Parry Perry growing from 430 MB to 508 MB over about 12 minutes on a 897 MB device, and update times of 10 to 15 ms against a 33 ms frame (30 fps game).
- **Leak 1 (runtime): canvases were never freed.** The shim's canvas objects had no finalizer, so every `document.createElement('canvas')` kept its pixels until the process exited. A page that makes one 400x300 canvas per frame went to 1.4 GB in 3000 frames. Now the shim registers each canvas in a `FinalizationRegistry`; the native side frees it 16 frames after the object is collected (a frame still being drawn may reference it). Since JS cannot see native pixel memory, `host_tick` also runs `JS_RunGC` when canvas memory has grown by 24 MB. Result on the test page: peak 46 MB.
- **Leak 2 (game): unbounded silhouette caches.** Parry Perry caches one canvas per sprite frame x size x light colour (`_sil`, `_gameSil`); sizes change with depth, so the key space is open ended. Both are now Maps capped at 300 entries (BibooSGBA `web/game/15_draw_scenery.js`, `18_story.js`).
- **Frame spikes: first-use image decode.** Each large PNG is decoded when the game first draws it (up to 28 ms on a PC, about 5x that on the device). Fix: a read-ahead worker thread decodes into a small ready queue (24 MB cap); the main thread installs the image at first use exactly as before, so output is identical. It decodes (a) images next to the one just requested, (b) images in the order the game first used them last time (`<game cache folder>/.image-order`, written on exit). Measured on Parry Perry: image time 93 ms -> 0.3 ms over 6000 frames on the second run, slowest decode 26 ms -> 0 ms. Switch off with `SGBA_NO_READAHEAD=1`.
- Perf log lines now show the slowest update, how many updates used over 75% of a frame, and image decode time, so the next report shows where time goes. `SGBA_FRAMESTATS=1` prints frame time percentiles and the slowest frames in headless runs.
- **Analog sticks restored** (the earlier revert was a mistake: the crash was the text cache double free). Left stick aims, right stick is the power dial, d-pad as backup. The log now says how many axes the pad has and which axis moved first, so a report shows the real numbering. Axis map override: `"axes": {"lx":0,"ly":1,"rx":2,"ry":3}` in controls.json.
- **Launcher retry:** SGBA.sh retried the program whenever it exited non-zero within 20 s, so a crash on exit started the game or menu again. It now retries (with the bundled SDL) only for exit codes 1, 126 and 127, which mean it could not start.
- Checks: output identical (final frame, seeded) with read-ahead and canvas release on or off on Parry Perry, Slingshot, demo and input lab; aarch64 under qemu identical to x86; ThreadSanitizer (no reports) and AddressSanitizer (no errors) on Parry Perry with the worker; `native/tests/memory_test.sh`. Not measured on the device.

## Slingshot music (owner request, 2026-10-15: "the music still doesn't play")
- Cause: the port never had music. The browser game made its ambient music live with Web Audio oscillators (a 55 Hz drone plus random pentatonic notes), SGBA's JS host plays oscillators as silence, so the port dropped it in 9f2d84c and nothing replaced it. The analog stick work (42c7c90) and the Parry Perry music fix (3b493a3) did not touch it.
- Fix: `tools/render_slingshot_music.py` reproduces the original recipe offline (same drone, scale, note length, glide, envelope, 0.6 master gain) with a seeded random pick over 120 ticks, 67.2 s, notes wrapping round the end so it loops with no seam, and writes Ogg Vorbis: `web_games/slingshot_orbits/assets/slingshot_music.ogg` (22.05 kHz mono, about 108 KB, kept above the 96 KB the native player needs to stream it). The result is peak-normalised to 0.55; the original levels peaked near -20 dBFS and would be too quiet on a handheld speaker. `index.html` plays it with `new Audio(...)`, looped at volume 0.7, started at load and again when Launch is pressed (a browser needs a gesture first).
- Verified on this PC: native runtime run of the packaged game with SDL's disk audio driver logged `audio on` and `music start ... loop 1`, no underruns, no JS errors, and the captured output has the drone and the notes (peak 0.38). Full test suite passes (88). Not verified: playback on the R36S itself, and how it sounds there.
- `games/slingshot_orbits.sgba` was rebuilt from `web_games/slingshot_orbits/`. The previous committed package was stale: it still had the old d-pad mapping (up/down aim, left/right power) that 3b95703 swapped, so this rebuild also brings that swap into the package.
- Not done: the four sound effects (launch, dust pickup, crash noise, win chime) from the browser game. They are short synthesized sounds, so they would be rendered to small Ogg or WAV files the same way.

## Slingshot campaign and sound effects (owner request, 2026-10-15)
- Request: not endless any more. Orbit 1 has one planet, each orbit adds one, orbit 20 has 20 planets, and clearing orbit 20 wins the game. Add sound effects for launching the ship and hitting the target.
- Campaign: `levelKey(orbit)` is now `orbit:orbit` (orbit N has N planets, N = 1 to 20; `FINAL_ORBIT` = `MAX_PLANETS`). The starting-difficulty picker (Fewer/More planets, `slingshot-start` in localStorage) was removed because a fixed start of one planet replaces it; the old key is ignored. The HUD shows `ORBIT n/20`.
- Win: the orbit-20 clear plays the usual 2.6 s celebration, then the menu panel opens as a win screen ("You beat Slingshot Orbits", final and best score, a "Play again" button that starts orbit 1 with a zero score). Nothing loads orbit 21. The same panel is the title screen (button "Launch") and the Select menu during play (button "New game", plus "Back to orbit").
- Effects: `tools/render_slingshot_sfx.py` renders the browser game's launch (0.35 s sawtooth glide 240 to 980 Hz) and target (five triangle notes 523 to 1319 Hz, 1.2 s) recipes to `assets/launch.ogg` (6 KB) and `assets/target.ogg` (9 KB), peak-normalised to 0.6 at 22.05 kHz mono. `index.html` plays them with `new Audio(...)` in `launch()` and `win()`. The crash and dust-pickup sounds from the browser version are still not in the port (not requested).
- Data left in place: `levels.js` still holds all 220 levels, but only the 20 keys `1:1` to `20:20` are reachable now. Trimming it would shrink the package by about 230 KB; not done.
- Test hook: `window.slingshotGoto(n)` jumps to an orbit so tests can reach orbit 20 without playing 19 orbits. `slingshotStatus()` gained `finished`.
- Verified on this PC: `tests/test_slingshot.py` now covers all 20 level keys, one planet at the start and N planets at orbit N, the win at orbit 20 (win screen up, no orbit 21, Play again resets), the move from orbit 1 to 2, and both effects triggering in order. Native runtime: the win screen and orbit 1 render, and both effect files play through the native mixer (launch, target chime, launch again). Full suite passes (91), native stick and audio tests pass, asset audit passes. Not verified: playback or timing on the R36S, and no one has played all 20 orbits by hand (the stored launches for orbits 1 and 20 were flown through the sticks).

## Slingshot: mode menu, Blind Crash, random starts, bigger planets (owner requests, 2026-10-14)
- Title menu: Pathfinder (the existing game) and Blind Crash. The same panel is the Select menu, the win screen ("Play again" keeps the mode, "Change mode" returns to the title) and the Blind Crash game over screen.
- Blind Crash: no route line, 20 HP for all 20 orbits, each crash (wall, planet, rock, or drifting too long) costs 1 HP, zero HP is game over. No shots: after a crash the ship launches again from the orbit's start spot. Start (retry) is disabled in this mode because it would respawn the stardust. Stardust collected is carried across orbits: X spends 3 for 1 HP (max 20), Y spends 10 to turn the route line on. Assumption to confirm with the owner: the route line stays on for the current orbit only; (superseded: owner asked for relaunch from the crash site, done). Best scores are kept per mode.
- Levels: ship and portal are now random per level (stored as `start` in levels.js, distance band cycles close, medium, far by orbit), planets are larger (radius about 51 at 1 planet down to about 26 at 20), rocks larger. `physics.js` generator was replaced: it grows a layout along a real flight (planets dropped beside the flown path on alternating sides, re-simulated each time, with backtracking), then scatters the remaining planets clear of the route, adds a blocker rock if the straight line is open, and covers shortcuts with rocks. The old mouse-pixel margin and pull-back code were removed; margin is now aim/power steps (tol k = every launch within k*0.25 degrees and k*0.5 percent power still wins; every level has k >= 3).
- Squiggle result (honest): all 20 levels are strict (no findable shortcut). Routes have 1 to 3 bends by `countBends` and 150 to 540 degrees of total turning; orbits 17 to 20 are only 1 to 2 bends. Gravity alone makes smooth arcs; the planets scattered after the snake tend to bend the route into one big hook. Next idea (not done): require each grown planet to reverse the previous bend, and reject scattered planets that reduce the route's bends.
- Tools: `node tools/slingshot_levels.js` now builds only the 20 campaign levels (about 15 minutes). Tests: tests/test_slingshot.py covers the mode menu, Blind Crash HP, game over, repair, route line purchase, per-level starts (100 tests pass). Native runtime checked (title menu, Blind Crash orbit 9 render, no JS errors). Not tested on the R36S.

### Blind Crash stardust rework (owner request, 2026-10-14)
- No more spending. In Blind Crash each stardust picked up mends 1 HP; at full HP it turns the route line on for the rest of that orbit (once). The X and Y buttons and the carried-stardust wallet were removed; the HUD shows collected/total like Pathfinder.
- Frequency: Blind Crash levels carry a second set `bdust` of 10 stardust (Pathfinder keeps the 2 to 4 in `dust`). `node tools/slingshot_levels.js --dust` redoes only that set in the existing levels.js (about a second; no level search). `scatterDust(L, sol, count, 'blind')` in physics.js.
- Test hooks: `slingshotGrant(hp)`, `slingshotDust()`, `slingshotWarp(x, y)`. 102 tests pass; native render of a Blind Crash orbit shows the 10 stardust, no JS errors. Not tested on the R36S. Open question: the route line lasts one orbit; stardust picked up while the line is already on and the hull is full has no effect.

### Slingshot pause menu Quit and Blind Crash difficulty (owner request, 2026-10-15)
- Select (pause) menu is now its own panel: "Back to orbit" and "Quit to title". Quit ends the run and shows the title menu. A web game has no way to exit the runtime (no JS quit hook in the native host or the Python host), so this goes to the title, not the SGBA launcher; Start+Select still returns to the launcher. Adding a JS exit hook would be an engine change and was not done.
- Blind Crash opens a difficulty pick: Easy, Medium, Hard. Hard is the previous rules. Medium: each stardust mends 2 HP (capped at 20). Easy: 20 stardust a level instead of 10 (`bdust` now stores 20 per level; Medium and Hard use the first 10). Assumption: the tiers are not cumulative (Easy keeps 1 HP per stardust). Win and game over screens, Play again and Try again keep the difficulty; best scores are kept per difficulty (Hard keeps the old `slingshot-best-blind` key).
- `tools/slingshot_levels.js --dust` redoes only the stardust sets. Tests: 106 pass (difficulty pick, quit, medium heal and cap, easy count). Native render of the difficulty panel checked. Not tested on the R36S.

### Slingshot title screen black until a button press (owner report, 2026-10-15)
- Report: the splash (title menu) shows only a black screen until a button is pressed. Not reproducible on this PC: the native runtime draws the menu on the first frame (checked with a temporary trace of the present path, since removed). The title screen was fully static, so the runtime presented it once (frame 0, count 1) and never again; a handheld display that drops or blanks that single present would stay black until a button press changes the overlay. That is a hypothesis, not a confirmed cause.
- Fix (game side only): the title screen now draws the eight twinkling stars every frame, so the runtime presents it continuously (trace after the change: a present on every frame). Pathfinder and Blind Crash unchanged. Tests pass (106). Needs confirming on the R36S; if the title still starts black, the first-present handling in native/src/main.c (static frames after a single present) is the place to look.

### Real Quit, and orbit-clear healing (owner requests, 2026-10-15)
- Quit now really quits. New engine hook: a page calling `window.close()` leaves the game exactly like Quit in the pause menu (native: `__sgba_quit` sets `quit_req`, `run_loop` returns `END_LEAVE`, the caller treats it as the pause menu's quit: a direct launch exits, a launcher launch returns to the launcher). The shim defines `window.close` to call it when it exists. Slingshot's pause menu button is now "Quit game" and calls `window.close()`; a runtime that ignores it (the Python host has no hook) falls back to the title screen after 0.6 s. Test: `native/tests/quit_test.sh` (also in the native CI workflow). Not verified on the R36S.
- Blind Crash Easy and Medium: clearing an orbit mends 1 HP (capped at 20); Hard does not. The Clear message shows the new HP. 107 tests pass.

## Logs with dates, per-game logs, launcher guards (2026-10-16)
- Every log line carries a date and time: sgba.log (already), stderr lines (sgba_launch.log, new), reports (a crash report has the crash epoch, unclean and error reports have times), sessions.log, and the new per-game logs.
- `sgba/logs/<game>.log`: one file per game, rotated at 128 KB. It records session start (build, game file size), a status line every 60 s, warnings and errors, session end, an unclean previous session, and a CRASH line with the epoch (written from the crash handler). GitROMs' status report includes their tails.
- Launchers: `SGBA.sh` refuses an empty or non-ELF `sgba` and says so on screen and in `sgba_launch.log`; each game launcher refuses a missing or empty `.sgba`. Tests: `native/tests/gamelog_test.sh`, `launcher_guard_test.sh`.
- GBA ROM games run in ArkOS's own emulator, which these logs cannot see; GitROMs includes the tail of `/dev/shm/runcommand.log` in its status report when that file exists (unverified on the device).
