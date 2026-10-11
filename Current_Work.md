# Current Work Queue

Last updated: 2026-10-11
Repository: xenodorian/SGBA
Latest implementation commit before verification: 35bdcb68e5588f27d458712eaab609a3bd636ed8

## Handoff protocol
1. Read this file and inspect source before starting.
2. Read AGENTS.md, AI_GAME_AGENT_INSTRUCTIONS.md, and GAME_DEVELOPMENT.md.
3. Keep changes incremental, scoped, and testable.
4. Run checks when possible. Distinguish CI from physical R36S validation.
5. Do not install anything on the R36S, alter firmware/system files, or change global Python. Device work requires verified target details and explicit approval.

## Objective
Build a small, predictable Python/Pygame runtime for AI-generated 2D games shared between Windows and a future R36S handheld with 1 GiB RAM. Prioritize stable 60 Hz simulation, low memory use, and an API coding agents can follow without inventing subsystems.

## Implemented in this change
- Fixed-step 60 Hz simulation with at most five catch-up updates per render frame; excess backlog is discarded to avoid a spiral of death.
- Default logical canvas remains 640×480. game.json supports 240×160, 320×240, and 640×480 profiles.
- Source-aware logical input edges persist until consumed by the first simulation tick.
- Aspect-preserving nearest-neighbor scaling remains runtime-owned.
- AssetStore caches images, fonts, and sounds; blocks paths outside its asset folder; enforces decoded-image budget and image dimension limits.
- Template game opts into 240×160 with an 8 MiB decoded-image budget and 256-pixel maximum image dimension.
- AI game contract defines conservative RAM, asset, and frame-time budgets for 1 GiB target hardware.
- CI asset audit checks each game's assets directory against a 16 MiB total budget and 4 MiB per-file guardrail.
- Browser preview remains a separate proof of concept and does not define native architecture.

## Verification status
- Automated tests were expanded for manifest validation, edge consumption, asset caching, path traversal, dimensions, and memory budget.
- GitHub Actions must finish after this commit before this work is marked verified.
- No R36S deployment or performance test has occurred. Firmware, architecture, Python/SDL availability, and physical controller mapping remain unknown.
- Fixed-step simulation and resource limits cannot guarantee 60 FPS if a game's work exceeds the device's frame budget.

## Queue

### P0: Safe target deployment
Status: IN PROGRESS
Do not run desktop setup on the R36S or modify firmware, system Python, system libraries, boot configuration, or frontend. Determine firmware, architecture/ABI, libc, Python, SDL, display, and controller details before proposing a deployment. Require a reversible plan.

### P1: Fixed-step runtime and input edges
Status: IMPLEMENTED, CI PENDING
Acceptance: regression tests and desktop CI pass.

### P2: Virtual-console and rendering contract
Status: IMPLEMENTED, CI PENDING
Acceptance: manifest validation tests and documentation agree.

### P3: Memory and performance budget
Status: IMPLEMENTED, CI PENDING
AssetStore limits cover only assets loaded through the API; the CI asset audit is a coarse guardrail, not a process-memory profiler.

### P4: R36S hardware validation
Status: BLOCKED ON DEVICE INFORMATION
Confirm firmware, CPU architecture, userspace/libc, Python, SDL, and controller mapping. Test launch/exit, scaling, input, frame pacing, asset loading, and performance on actual hardware. Record measurements here.

### P5: Browser preview
Status: IN PROGRESS
Keep preview isolated from native runtime. Recent Pages path fix: d280b08a04745f0907230be54386ec234b8073be. Verify workflow separately.

### P6: Repository rename
Status: BLOCKED ON REPOSITORY ADMIN ACTION
Desired name: R36S-PyGame-Runtime. Rename through GitHub Settings if desired and verify redirects.

## Change log
- 2026-10-11: Added fixed-step simulation, optional low-resolution profiles, cached and budgeted asset loading, AI resource contract, template manifest, and CI asset audit. Desktop CI pending; no target hardware claims.
