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

**A mid-unit wall is automatically a crossroads.** When a case doesn't fit, a spec breaks, or an assumption fails, the plan has stopped picking an option — the wall is information that the design is wrong somewhere. Re-derive from first principles, then pause and escalate with the re-derived design as your recommendation; diverging from the spec's literal words is your duty when intent demands it. The forbidden third path is the **compliance patch** — a flag, a special case, a shim, a parallel channel, a test rewritten to dodge the rule — which honors the words while betraying the intent, and is rejected regardless of sunk cost.

## Delivery
The human does not poll a bus inbox, and on many substrates they are not sitting at the machine the run is hosted on. **Which live channel actually arrives is a substrate question** (`substrate.md`), and the runbook also names the ones that report success while reaching nobody. Establish that before the first escalation. An escalation lands in three places, each doing a different job:

| Where | What it is |
|---|---|
| the **`human` bus handle** | the durable, cross-session ledger of open asks (`bus send <you> human …`) |
| the queue file beside the workpaper (`action-items.md`) | what the human actually reads |
| the **live reach** the runbook names | how they learn it is waiting, now |

- **Scale the fidelity to the decision's weight (see `decision-briefs.md`).** Binary and obvious-once-stated goes as plain text. Complex, meaty or visual earns a **decision brief**, put somewhere the human can actually open it. On a machine they are not sitting at, that means published where they already look, never a path on the host. **Illustrate-only by default; capture available when it pays.**
- **Always pause the unit**, and record the ask on the `human` handle in the same beat you raise it.
- **Ask in a form answerable in one line.** An interactive widget that exists only inside one client is not a reach.
- **Routine reports / data-resolvable blockers:** async over the bus only. No escalation.
- **Blast radius:** a crossroads pauses its own unit and anything dependent on it; unrelated siblings keep running. Halt the **whole** run only when the decision likely invalidates parallel work (e.g. an architecture choice that reshapes the graph).
- **Batch what you surface.** The orchestrator reviews the `human` ledger and raises items consolidated, per `method.md`, rather than one interruption per ask.
- **The action-items file is the human's readable queue.** One file beside the workpaper — `action-items.md` — holding **every outstanding ask of the human and nothing else**: Q-numbered so they answer by number in one line ("Q6: done"), each item carrying its context inline or linked to a durable artifact, never to an ephemeral file. Maintained by the run loop itself: the queue is the third surface of `run.md`'s Record step, gated at checkpoint and reconciled at resume — an ask appears the beat it becomes askable, a completed item drops off the same beat, the workpaper journal keeps history, and the queue holds only what is open. The bus `human` handle stays the cross-session machine ledger; this file is its human-facing rendering, and when the two disagree the file is what the human actually read.
- **Record the run's reach in the workpaper before the first escalation.** Name the live channel, the queue file path, and where a brief gets published. A run that discovers its escalation path at the moment it needs one has no escalation path, and that failure is silent.

### An ask is a charter for a cold session

`method.md`'s prompt contract is written for a delegate holding none of the orchestrator's context. **The human reads the queue in that same condition** — the same contract, human edition. What an item should cost them is judgement, never reconstruction.

So the test is what acting demands beyond what the reader already holds. An item that needs an external system navigated, more than a glance of steps, or anything created or configured, carries a **numbered walkthrough** with the exact clicks and field values, the **why** in one line, and the **form of the done-signal**. A pure decision, or a one-or-two-step item, stays bare: there a walkthrough is the noise.

## Review before close
A finished session isn't a closed one. A verified delegate's context is the cheapest way to pull a thread on what it did, so surface **"unit X is done and verified"** with the disposition the runbook gives, and let the human look before anything is torn down. This is the payoff of human-reachable sessions over hidden subagents (`run.md` · `delegate.md`).
