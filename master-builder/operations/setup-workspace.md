# Setup Workspace

Establish or verify a local workspace where skills and their state live.

## Intent

One folder per practice or project, laid out so any harness discovers the skills and the state conventions hold. Run before the first build in a new location; re-run when placement looks wrong.

## Procedure

1. **Detect the harness** — per `reference/runtime-conventions.md` (Discovery; Both Conventions Active when two are present).
2. **Decide placement** — workspace skill or global, per `reference/skill-patterns.md` (Where Skills Live Locally). A global skill is best symlinked from a repo you control, so updates are a `git pull`.
3. **Create the layout** — the workspace roots per `reference/runtime-conventions.md` (Workspace Roots). Seed `.gitignore` with `database/` and `.env`.
4. **Wire the toolchain** — script runtimes and the secrets manager, per `reference/runtime-conventions.md` (Script Runtimes; Secrets): detect what the machine already uses; ask if nothing is established; suggest the defaults.
5. **Multi-client work** — one workspace per client engagement, each carrying its own `_local-{skill}/` tier (`reference/skill-patterns.md`, Multi-Tenant Skills). Never folder-per-client inside one workspace.

Record every choice — harness convention, placement, managers — in the workspace instruction file (`CLAUDE.md` / `AGENTS.md`), so later sessions inherit them instead of re-deciding.
