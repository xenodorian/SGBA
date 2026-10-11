# Repository Agent Entry Point

Before any work:
1. Read `Current_Work.md` and inspect the actual repository state.
2. Read `AI_GAME_AGENT_INSTRUCTIONS.md` for any game implementation task.
3. Read `GAME_DEVELOPMENT.md` for the current public game API overview.
4. Treat `Current_Work.md` as the persistent cross-agent handoff queue. Update it in the same commit as meaningful work.
5. Make small, scoped changes. Verify before claiming completion. Never infer target-device compatibility from desktop CI.
6. Do not install anything on the R36S or modify firmware/system files. Device work is blocked until target details are known and the user explicitly approves a reversible test.

Current project objective: a simple Python/Pygame runtime with a 640x480 logical canvas, allowing AI-generated games to share code between Windows development and a future R36S deployment. Preserve the existing framework and avoid unnecessary architecture.
