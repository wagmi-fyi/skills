# Database Patterns

When and how skills use databases. A database gives a headless workflow app-grade state: structured, queryable, durable.

## When to Use One

Use text files and spreadsheets for simplicity. Use a database when you filter, join, aggregate, or track state across entities. Don't reach for one when markdown, CSV, or JSON would suffice — the simplest persistence that works is the right choice.

## Types by Purpose

- **System of record** — the database IS the source of truth. Design for this by default.
- **Staging layer** — a working area between external systems: source in, process, publish out.
- **Cache** — rebuildable convenience storage.

## Conventions

- **One database per skill**, named `{skill-name}.db`, at the workspace root: `database/{skill-name}.db`. Outside the skill directory — state outlives instructions.
- **SQLite by default.** Embedded, zero-config, everywhere.
- **Minimal schema.** Fewest tables that are reasonable; be generous with JSON columns (as TEXT) for flexible attributes. If the schema needs more than a handful of tables, re-examine the skill's scope.
- **STRICT tables** for new schemas — a mistyped value fails at insert instead of silently storing junk, which is exactly the failure mode agent-written INSERTs produce.
- **WAL mode** on creation (`PRAGMA journal_mode=WAL;`) for any database more than one process might touch.
- **Schema reference in the skill** — `schema.sql` (executable) or `reference/database-schema.md` (documented, with example queries). It serves query-writing and first-run initialization both.
- **Not committed.** Databases hold real data; list `database/` in `.gitignore`. Backups are their own convention below.
- **Version probe.** Scripts assert the linked SQLite meets the schema's minimum (`sqlite3.sqlite_version`) and fail with a clear message — system interpreters often link versions years old.
- **Search.** For "find records like this," FTS5 (built in; external-content tables synced by triggers) is the first answer. Vector extensions are per-skill opt-ins; their indexes are rebuildable, never the source of truth.
- **Analysis sidecar.** For heavy aggregation over raw files (CSV piles, exports), attach the ledger from DuckDB, compute, write conclusions back to SQLite. Sidecar state is disposable.
- **Tooling.** `sqlite-utils` (v4+, pinned major) is the suggested default for driving and migrating skill databases — subject to the workspace's toolchain convention (check, ask, suggest), and declared as a dependency where used.

## Initialization

Every database skill has a first-run path: auto-create from the schema reference on activation, or a dedicated onboarding operation when setup is multi-step.

## Backups

Git ignores the database, so the backup path is deliberate, never assumed:

- **Baseline, zero dependency:** before each mutating run, `VACUUM INTO` a snapshot in the system temp directory — the need expires when the run verifies, so let the OS clean up. Period closes and other milestones get durable snapshots: the remote convention where it's wired, else a short rotation in `database/backups/`.
- **Remote — required once real client work is involved.** A laptop-local backup dies with the laptop. Check what already covers the workspace (whole-machine cloud backup may suffice); otherwise replicate continuously with Litestream (0.5.8+) to storage you control, or push snapshots there. Encrypted at rest; credentials via the secrets manager; the destination named in the skill's Dependencies.
- `_workpapers/` is state the same as the database and rides the same remote convention.
