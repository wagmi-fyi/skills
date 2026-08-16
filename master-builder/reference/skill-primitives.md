# Skill Primitives

The design palette: which capabilities a skill composes, and the signals that call for each. A v0 composes a few; a mature workflow skill earns most over its life. Scan at design time, build only what the core slice earns, re-scan during improvement. Deliberate deferrals go in the brief's deferred list. Construction conventions for each primitive live in `reference/skill-patterns.md`.

**Routing** — SKILL.md's registries split the skill into on-demand operations and reference files. Reference holds what you'd otherwise re-explain. Signal: more than one intent, or knowledge worth loading only sometimes.

**Scripts** — atomic code for the predictable parts. If a step runs the same way every time, script it: a script spends no tokens and cannot improvise. Markdown is for judgment. Signal: a step re-derived in chat every run, or a result that varies where it must not.

**Adapters** — code at the variability seam. Core scripts run identically for everyone; adapters bridge what differs — a source system's format, a client's API. Most applicable in multi-tenant skills, where they follow the tiers: core adapters every tenant uses, firm adapters shared across some, local adapters for one (`reference/skill-patterns.md`). Credentials environment-resolved per `reference/runtime-conventions.md`. Signal: the same workflow step differs by client or source system.

**Workpapers** — durable run state in `_workpapers/`, period-named, human-readable. Signal: state being reconstructed from memory or scrollback.

**Database** — structured, queryable state at `database/{skill-name}.db`. When selected, design the schema at the start — it shapes operations and scripts (`reference/database-patterns.md`). Signal: filtering, joining, or tracking state across entities.

**Templates** — reusable scaffolding the skill instantiates: workpaper shapes, config files, output documents. Signal: the same document shape produced run after run.

**Activation and self-bootstrapping** — the first-run path: check prerequisites and declared dependencies, initialize what's missing, guide the human through the rest. Signal: any setup beyond invoke-and-go.

**Validation loops** — checks designed into an operation, so the skill proves its work before proceeding. An objective definition of done turns the check into a loop the agent runs until it passes. Signal: an error found late, or by luck.

**Human checkpoints** — a designed gate where a person judges before the skill proceeds. Distinct from validation: validation is the skill checking itself; a checkpoint is a human deciding. Signal: anything irreversible or outward — sends, payments, filings, deletes.

**Self-improvement** — the skill carries its own learning loop: run experience turned into edits, fixes accumulating in the skill rather than in chat. Signal: the same correction made twice.

**Multi-tenancy** — one core serving many clients through the three-tier pattern (`reference/skill-patterns.md`). Signal: a second client with the same logic and different conventions.
