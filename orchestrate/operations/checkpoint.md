# Checkpoint — pin standing postures before a compaction

Run this **before you compact** the orchestrator (compaction is human-initiated here). A compaction compresses the contract and the state, and it silently resets **standing postures** — the working directives that accrued this run (drive/parallelize, auto-reap on, deploy-only-from-the-proven-chart, serialize-via-lease…). This operation distills them and pins them where the *next* context will re-read them, so they survive.

> Pairs with `resume`: checkpoint **writes** the postures; resume **re-asserts** them. The workpaper's **Standing Postures** block is the carrier.

## Intent
Leave the run so a freshly-compacted orchestrator re-grounds into the *same* postures and the *same* effective config it held a moment ago — nothing load-bearing lost to the compaction.

## What a posture is (and isn't)
A posture is a **standing directive that shapes future behavior** — not run narrative. The journal already holds "what happened"; postures are "how I operate on this run." Keep a candidate only if all three hold (the capture filter):
1. **Changes future behavior** — a resumed orchestrator would act differently without it.
2. **Not already captured** — not a core principle you'll reload anyway *unless this run overrides it*.
3. **States concisely** — one line, the directive not the story.

Drop execution metadata, one-off events, and anything the newest journal + board already carry.

## The loop
For each candidate posture:

1. **Distill** it to one line — the directive, plus (if it's an override) what it overrides.
2. **Reflect on promotion — "default, or runtime?"** Ask: *should every orchestration inherit this, or is it true only for this run?* The concrete test is **domain translation**: restate the posture in a different medium than this run's (a period close, a content program, a data migration). If it holds unchanged, it's a general principle; if it only makes sense in this run's domain, it stays runtime.
   - **Runtime** (this run only) → the workpaper. **Autonomous** — just write it.
   - **Promote to default** → a **core file**, **surfaced for the human's OK first** (it edits shared infrastructure — design-shaping; `[Y]` write / `[E]` edit / `[S]` skip, one at a time). See `reference/human-in-the-loop.md`.
3. **Route by scope** — match the file to the posture's nature:

| Posture kind | Runtime home (autonomous) | Promoted home (needs OK) |
|---|---|---|
| Config value (e.g. `close_mode: auto`) | workpaper → **Effective config** | `config.yaml` default |
| General operating principle | workpaper → **Behavioral postures** | `reference/method.md` (Principles) |
| Resume/run behavior | workpaper → **Behavioral postures** | the relevant operation (`resume.md` / `run.md`) |

Most postures stay **runtime**. Promotion is the exception — reserve it for a posture proven general *across* runs, not one this session happened to need. When unsure, keep it runtime; a later run can promote it.

## Promotion is a translation, not a copy
A promoted posture is **re-expressed medium-neutrally** before it lands in a core file:
- Replace run vocabulary with the resource *classes* it stands for (a base sha / build box / deploy lease → *an input, a scarce resource, a commitment slot, a human decision*).
- State the **rule generally**; a concrete current instance may be cited *as an example* inside it — models, tools, and vendors drift; the rule shouldn't.
- **The workpaper keeps the case law.** Mark the runtime posture PROMOTED (don't delete it) and leave its run-specific instances — filenames, incidents, dates — where they are. Evidence stays local; the principle goes global. That trail is what keeps a promoted principle auditable back to what earned it.

## The effective-config mechanism (why the override survives)
The workpaper's **Effective config** block is the source of truth a resumed orchestrator reads **over** `config.yaml`. That's what makes a per-run override stick: set `close_mode: auto` there and an auto-reaping run keeps auto-reaping after a compaction, even though the skill default is `manual`. Record only the *deltas* from `config.yaml`, each with a one-line why.

## Write the checkpoint
Refresh the workpaper's **Standing Postures** block (Effective config + Behavioral postures) so it is the current, complete set — add new, drop retired. It's a live snapshot, not an append log. Then add a one-line journal entry noting the checkpoint. **Also sweep the substrate before compacting:** `tmux list-panes -a` for stale delegate panes to reap and for text stranded unsubmitted in a pane's input line (a lost-Enter poke) — a checkpoint that audits only the bus board misses both. Now it's safe to compact.

## Condense check (keep the workpaper resume-sized)
A checkpoint is also the natural moment to ask whether the live workpaper still fits its post-compaction job. If a single read no longer returns it whole — or closed-work history visibly outweighs live state — run `operations/condense.md` now: **after** the postures are pinned (its knowledge sweep feeds on the same filter), **before** compacting. Its invariant: archive the whole file verbatim to a dated sibling first, then cut; evidence moves, it is never summarized away.

## Gate
The Standing Postures block is current and self-contained — a cold reader could re-assert every posture from it alone; every promotion was human-approved; the newest journal notes the checkpoint. The workpaper is resume-sized — the condense check ran, and `condense.md` executed if it was due.
