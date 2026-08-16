# Human-in-the-loop — the crossroads + reversibility contract

The single most important behavior in this skill. It governs **when an agent pauses for the human and when it proceeds alone** — and it applies identically to the orchestrator and to every delegated session.

## Pause + notify on exactly two classes

1. **Design crossroads.** A genuine decision among defensible options where the trade-off is *not already settled* by stated intent or the plan. Architecture choices, a schema shape, a contract two units must share — anything where "it depends" and reasonable people would weigh it. **These are the interruptions the human wants.**
2. **Human-only / irreversible.** Logins, credential/secret provisioning, OAuth approvals, and **truly irreversible** actions — a sent email/message, a payment, a destructive delete with no recovery — anything **only a human can authorize** or that **cannot be undone.** (Irreversible ≡ human-only: with no rollback, it's a go-ahead, not a task.) **Reversibility is about whether a rollback exists, not whether the action is "external":** if the agent can itself undo it in the external system (delete the record it created, unpublish, restore from the system's trash/history), it's reversible → it proceeds under the autonomy rule below.

## Otherwise: proceed autonomously

Everything else — **including large changes to critical systems** (code, databases, live config) — proceeds **without** pulling the human in, **iff all three hold:**
- **Intent** — grounded in what the human asked for.
- **Plan** — backed by a plan/directive/spec, not improvised.
- **Reversible — rollback path established FIRST.** Before the change, create the safety net and record it. *If you cannot establish a rollback path, the action is effectively irreversible → pause (class 2).*

Do **not** pause for permission to do reversible, planned, intended work — that is the toil the human is escaping. "Are you sure?" on a backed-up, branch-isolated change is noise.

## The reversibility ladder (establish before the change)
| Change class | Rollback path to establish first |
|---|---|
| Code / files | checkpoint commit or a working branch; or copy-aside |
| Database | snapshot/dump to `/tmp` — **never inside a tracked project/output dir** (a `.db`/`.bak` there evades gitignore and gets committed); or a `.bak` table; record the pre-state |
| Cluster / deploy | record the current rev + the exact rollback command |
| Bulk filesystem op | tar/copy the target aside |
| External action the agent can itself undo | the in-system undo (delete the record you created, unpublish, restore from the system's trash/history) — **reversible → proceed** |
| Truly irreversible (sent message, payment, destructive delete, no recovery) | **none possible → pause (human-only)** |

## Recognizing a crossroads (the test)
Ask: *"Is there more than one defensible option here, and does the plan/intent fail to pick one?"*
- **Yes** → crossroads → notify + **pause this unit** (siblings continue) with the options + your recommendation framed; resume on the ruling.
- **No** (the plan/intent implies the choice, or it's mechanical) → proceed; record the decision in the workpaper journal so it stays auditable.

When unsure: **lean to proceeding if it's cheaply reversible** (you can surface it afterward with the rollback intact) and **to pausing if it's expensive to undo.**

**A mid-unit wall is automatically a crossroads.** When a case doesn't fit, a spec breaks, or an assumption fails, the plan has stopped picking an option — the wall is information that the design is wrong somewhere. Re-derive from first principles, then notify + pause with the re-derived design as your recommendation; diverging from the spec's literal words is your duty when intent demands it. The forbidden third path is the **compliance patch** — a flag, a special case, a shim, a parallel channel, a test rewritten to dodge the rule — which honors the words while betraying the intent, and is rejected regardless of sunk cost.

## Delivery
- **Crossroads / human-only — scale the fidelity to the decision's weight (see `decision-briefs.md`):**
  - *Trivial* (binary / obvious-once-stated) → chat text, or a quick `AskUserQuestion`.
  - *Complex / meaty / UI / visual* → render a **decision brief** (`decision-briefs.md`), opened with `scripts/present`: a real toggle-between mockup for UI choices, or a comparison dossier for meaty non-UI ones — **in a look that fits the project (your judgment), not a fixed house style.**
  - Always: `scripts/notify "<decision>" "<one line + your rec>"` **+ pause the unit** (parked, visible in its pane) + record the ask on the **`human` handle** (`bus send <you> human …` — the consolidated "what needs me" ledger; see `session-bus.md`). **Illustrate-only by default; capture available when it pays.**
- **Routine reports / data-resolvable blockers:** async over the bus only. No notification.
- **Blast radius:** a crossroads pauses its own unit and anything dependent on it; unrelated siblings keep running. Halt the **whole** run only when the decision likely invalidates parallel work (e.g. an architecture choice that reshapes the graph).
- **How the human is reached vs. the ledger:** the human is *reached live* by `notify` + the **paused pane** they switch into (+ the browser brief) — they do **not** poll a bus inbox. The **`human` handle** is the *durable, cross-session ledger* of open asks; the orchestrator reviews it and surfaces items **batched** (one consolidated ask), per `method.md`.

## Review before close
A finished session isn't a closed one. Under `close_mode: manual` (default), the orchestrator never closes a verified delegate — it surfaces "ready" and **the human closes the window after reviewing the summary**, so a thread can be pulled before the context is gone. This is the payoff of human-reachable sessions over hidden subagents (`run.md` · `delegate.md` · `config.yaml`).
