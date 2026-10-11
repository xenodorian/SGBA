# Current Work Queue

Last updated: 2026-10-11
Repository: xenodorian/SGBA
Default branch: main
Current head before this documentation update: 36b6446b4813dd5cecf73d2cf78fefe67e671a54

## Agent handoff protocol

1. Read this file before starting work. Then inspect the current repository state; this file is a handoff index, not a substitute for reading the code.
2. Read `AGENTS.md` for repository-wide instructions. For game implementation, also read `AI_GAME_AGENT_INSTRUCTIONS.md`.
3. Do not assume an item is complete because an older commit or note says it was. Verify against current files and CI.
4. Claim a task by changing its status to `IN PROGRESS` and recording the agent, intended scope, and date in this file before substantial edits. Avoid overlapping edits with another active task.
5. Keep changes minimal and scoped. Do not rewrite unrelated files, add frameworks, add dependencies, or expand the architecture without a demonstrated requirement.
6. After changes, run available checks when feasible. If checks cannot be run, state that explicitly. Do not label untested behavior as working.
7. Update this file in the same commit as implementation changes: mark status, record commit SHA, results, blockers, and next actionable task. Keep unresolved items in the queue.
8. Never install anything on the R36S, alter its firmware, or ask the user to run device commands until the firmware, architecture, and deployment approach are established and the user explicitly agrees.

## Project objective

Build a small, predictable Python/Pygame runtime so AI-generated 2D games can use one game API and the same game logic on a Windows PC and an R36S handheld. The logical game surface is 640×480. Prioritize low porting friction, safe/reversible deployment, and maintainability over feature count.

## Current state

- Runtime entry point: `runtime.py`.
- Pygame dependency pinned in `requirements.txt`.
- Demo: `games/demo/main.py`.
- New-game template: `games/template/main.py`.
- PC-only local virtual environment setup scripts exist for Windows and desktop Linux.
- Launch scripts accept a game path and `--fullscreen`; no argument starts the demo.
- Automated unit tests and a Windows/Linux, Python 3.11/3.12 GitHub Actions matrix exist.
- Latest known GitHub Actions run for commit `36b6446b4813dd5cecf73d2cf78fefe67e671a54` completed successfully: https://github.com/xenodorian/SGBA/actions/runs/38113275635
- The successful CI result is not equivalent to visual, controller, audio, or performance testing on the R36S.
- R36S firmware, CPU architecture, OS userspace, Python availability, SDL support, and controller event layout remain unknown. No handheld deployment package exists.
- Repository is still named `SGBA`; the desired rename to `R36S-PyGame-Runtime` has not been performed.

## Queue

### P0: Preserve safe project boundaries
Status: IN PROGRESS
- Do not run desktop Linux setup on the R36S.
- Do not modify firmware, system Python, system libraries, boot configuration, or emulator frontend.
- Keep PC dependencies inside the project-local `.venv`.
- Before any target deployment work, establish exact firmware/distribution, CPU architecture/ABI, libc, Python availability/version, SDL libraries, display access, controller device/event details, and a rollback path.
Acceptance: all agents follow these constraints; no device modification occurs without explicit approval.

### P1: Establish persistent agent workflow
Status: IN PROGRESS
- Maintain this file as the canonical task queue and handoff state.
- Maintain `AI_GAME_AGENT_INSTRUCTIONS.md` as the canonical game-generation contract.
- Maintain `AGENTS.md` as the repository entrypoint for coding agents.
Acceptance: each agent can identify current priorities, constraints, current state, and the next concrete task without relying on chat history.

### P2: Confirm desktop runtime correctness
Status: IN PROGRESS
- Added `games/input_lab/main.py` for 640x480 dither, logical input indicators, raw SDL button indices 0-15, axes 0-5, hat values, and recent controller events.
- Extended provisional runtime actions for guide, L1/R1, L2/R2, L3/R3, and right-stick directions; added keyboard test bindings and regression tests.
- Run CI and record results. Raw SDL layout and physical labels remain unverified on R36S.
- Review `runtime.py` and tests against current Pygame event behavior and the intended game API.
- Check the runtime lifecycle, initial controller enumeration, hotplug, source-aware key/button/hat/axis state, frame-based pressed/released semantics, invalid game paths, game-load errors, and shutdown cleanup.
- Add regression tests for verified issues only; do not speculate about hardware mappings.
- Check GitHub Actions after each meaningful code change.
Acceptance: CI passes on all configured OS/Python combinations; failures are fixed rather than ignored. This does not certify R36S behavior.

### P3: Make the game API stable and easy for AI agents
Status: READY
- Evaluate whether the existing `create_game()`, `update(dt, inputs)`, `draw(surface)`, and optional `handle_event(event)` contract is sufficient.
- Keep game code isolated from window management, frame-loop ownership, physical controller mappings, and OS-specific paths.
- Add only APIs needed by multiple games or justified by a concrete portability issue.
- If changing the API, update demo, template, docs, and tests together.
Acceptance: a simple game can be generated from the contract without inventing engine responsibilities or adding dependencies.

### P4: Define deterministic game packaging/assets conventions
Status: READY
- Establish a consistent per-game folder layout and asset-loading convention.
- Require asset paths relative to the game file/package, never the process working directory.
- Decide how optional assets, fonts, audio, and game metadata should be represented before implementing a packaging system.
- Avoid adding archive formats, installers, asset pipelines, or manifests until a real need is established.
Acceptance: games launch by file path from different working directories without breaking asset lookup.

### P5: Inspect deployment options without touching the handheld
Status: BLOCKED ON DEVICE INFORMATION
- Gather target details from the user only when available: firmware name/version, architecture, userspace/libc, Python version, SDL/Pygame availability, and frontend launch expectations.
- Research compatible deployment options once target details are known. Prefer an isolated/self-contained bundle and a reversible test.
- Do not infer the architecture from the R36S product name alone. Do not assume a PyPI wheel is compatible.
Acceptance: deployment plan matches the actual device image and has an explicit rollback procedure before any test.

### P6: Rename the repository
Status: BLOCKED ON REPOSITORY ADMIN ACTION
- Desired repository name: `R36S-PyGame-Runtime`.
- The connected GitHub actions used so far did not expose a repository rename operation.
- If still desired, rename through GitHub repository Settings > General > Repository name, then verify redirects and update any hard-coded references.
Acceptance: new repository URL/name verified; old URL redirect behavior confirmed.

### P7: Target-device validation
Status: BLOCKED ON DEVICE ACCESS AND APPROVAL
- After P5, test only a disposable/self-contained build and preserve a rollback path.
- Validate launch/exit, fullscreen/aspect ratio, input mapping, unplug/reconnect behavior if applicable, frame pacing, asset loading, and performance.
- Record actual results, firmware/build identity, logs, and any workarounds here.
Acceptance: target results are documented; never infer success from desktop CI.

## Known constraints / non-goals

- No R36S installer or deployment package currently exists.
- No device-specific controller mapping has been verified.
- Do not switch frameworks (e.g. to LÖVE, browser/HTML, SDL bindings other than the current Pygame dependency) without explicit user direction and a concrete reason.
- Do not add networking, an editor, a plugin architecture, a game launcher UI, automatic update machinery, or broad asset tooling preemptively.
- Preserve the existing simple PC workflow and avoid changing the user's global Python environment.

## Change log

- 2026-10-11: Added source-aware input tracking and tests; latest CI run succeeded at commit `36b6446b4813dd5cecf73d2cf78fefe67e671a54`.
- 2026-10-11: Added game template and initial game-development contract.
- 2026-10-11: Added `games/input_lab/main.py`, extended controller actions and tests, and updated input documentation. Desktop CI and actual R36S controller mapping still require verification.
