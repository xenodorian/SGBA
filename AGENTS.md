# Repository Agent Entry Point

Before any work:
1. Read `Current_Work.md` and inspect the actual repository state.
2. Read `AI_GAME_AGENT_INSTRUCTIONS.md` for any game implementation task.
3. Read `GAME_DEVELOPMENT.md` for the current public game API overview.
4. Treat `Current_Work.md` as the persistent cross-agent handoff queue. Update it in the same commit as meaningful work.
5. Make small, scoped changes. Verify before claiming completion. Never infer target-device compatibility from desktop CI.
6. Do not install anything on the R36S or modify firmware/system files. Device work is blocked until target details are known and the user explicitly approves a reversible test.

## Mandatory game-delivery rule
When asked to create or modify a game, the AI agent owns the complete workflow. Do not tell the user to open a terminal, type commands, install dependencies manually, run a packaging script, or assemble the deliverable. Make the source changes, run the available checks and packaging tool in the agent environment, and create a valid `.sgba` package as part of the task. Include the `.sgba` file in the repository when the task is repository-based, or provide the actual generated artifact through the available file-delivery mechanism when supported. Do not claim a package exists unless it was created and validated. If tools or permissions prevent creating/testing it, state the exact blocker and do not delegate command-line work to the user.

Current project objective: a simple Python/Pygame runtime with a 640x480 logical canvas, allowing AI-generated games to run unchanged on many devices (Windows, Linux, handhelds such as the R36S). Never add device-specific code paths or assumptions; the runtime must stay device-agnostic. Preserve the existing framework and avoid unnecessary architecture.
