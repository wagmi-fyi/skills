---
project: {{PROJECT}}
output_dir: {{resolved output dir}}
bus_handles: [orchestrator, {{unit handles}}]
status: in-progress
created: {{date}}
---

# {{PROJECT}} — Workpaper (live state & journal)

> ### ▶ Cold/compacted? START HERE.
> **What:** the live coordination surface for {{PROJECT}}, run as a verifying orchestration (`/orchestrate run`).
> **Read order:** this STATUS box → **Standing Postures (re-assert)** → the Work board ↓ → `plan.md` (graph + gates) → the `orchestrate` method.
> **Resume rule:** newest journal entry = where we are; the board = graph state; Open human asks = what's pending. **Re-assert Standing Postures before acting — a compaction silently resets them.** Nothing is "done" except recorded here, ✅, with re-verified evidence. **Update this file every orchestrator turn; refresh Standing Postures at each `checkpoint`.**

## ▶ STATUS NOW — {{date}}
- **Phase:** {{wave / what's in flight}}
- **Next orchestrator action:** {{…}}

## ▶ Standing Postures — re-assert these FIRST on resume
> Directives a compaction silently resets. Re-affirm before any action; honor **Effective config** over `config.yaml` defaults. `checkpoint` refreshes this block before you compact.

**Substrate:** {{name}} — runbook `reference/substrates/{{name}}.md`; the live reach to the human is {{what actually arrives on this host}}.

**Effective config** — deltas from `config.yaml`, each with a why (empty ⇒ running skill defaults):
- {{none — skill defaults}}   <!-- e.g. `spawn_mode: manual` — the human opens each delegate on this run -->

**Behavioral postures** — run-specific operating directives:
- {{none yet}}   <!-- e.g. "Deploy only from the proven chart, base off the live tip, verify functionally not just the diff" -->

## Work board
Legend: ☐ todo · ▶ issued/in-flight · 🔎 reported, verifying · ✅ verified · ⏸ blocked · ⏸‖ paused (crossroads/human-only)
| Unit | Type | Status | Dep | Gate (orchestrator re-verifies read-only) |
|---|---|---|---|---|
| {{U0}} | research | ☐ | — | {{evidence}} |
| {{…}} | | | | |

## Open human asks (crossroads / human-only — fire early)
- {{none yet}}

## Journal
- **{{date}}** — bootstrap: graph decomposed; waves set; `output_dir` established at `{{path}}`.
