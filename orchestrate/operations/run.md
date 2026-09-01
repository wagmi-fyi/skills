# Run — drive the orchestration

Be the lean orchestrator: dispatch ready units, spawn delegates, re-verify every result, journal, repeat.

## Intent
Advance the project's graph to its gate, holding the workpaper as the source of truth, **mutating nothing yourself**, pausing only on crossroads/human-only.

## Load (every entry, including after a compaction)
`workpaper.md` (STATUS + **Standing Postures** + board + **newest journal = where we are**) → `plan.md` (graph + gates) → `reference/{human-in-the-loop,method,session-bus,substrate,git}.md` → **the runbook for this run's substrate**, which the workpaper records. **Honor the workpaper's Effective config over `config.yaml` defaults, and hold its Behavioral postures as live directives** — they're the run's real settings.

Confirm the bus is live (`bus handles`); if not, run `install`. **Survey the shared bus first** (`bus handles` / `bus locks`) — other orchestrations may be live; take a **distinct, project-namespaced** handle (`<project>-orchestrator`) and coexist (`session-bus.md`). Register with the form your runbook gives.

**Tidy the board while you're here** (orchestrator-only, once per standup): `bus gc --commit --if-stale 86400` — self-throttled to ~daily and self-logged, it retires only *fully-read* handles *idle >14d* (never `human`, an active lease-holder, or recent handles; archived to `processed/` and still in `log.jsonl`, so reversible). **Never lower `--days`**; at 0 the verb retires every fully-read handle including your own live one.

**Decide your wake and say which it is.** Arm whichever mechanism your runbook offers, or work the board by hand and report **wake: manual**. Unread counts on `bus handles` are the truth either way, so an *attended* run with no wake armed loses latency and nothing else. A wake you *believe* is armed and is not is worse than none, so prove it once and record what you proved.

**Stand a heartbeat before an unattended stretch.** Manual is not a wake when nobody is at the board. An unattended run with no standing wake path is idle-with-backlog by construction, and the backlog is invisible from both ends, because a finished delegate and a parked one are equally silent. Every remaining move serializes through you, so a wake that never arrives costs the whole stretch rather than one report's latency. Arm the recurring path your runbook gives before the human walks away, prove it fires once, and record it beside the wake you declared.

## The beat (every turn, before anything else)
**Re-derive the board. Never report from memory of what you spawned.** A delegate that finished and a delegate parked on a question can be indistinguishable, and on some substrates neither one makes a sound. So each beat, in this order:

1. **Read the board the way your runbook says to read it** (`substrate.md` → `substrates/<name>.md`). Classify every in-flight unit: working, blocked, finished, gone. **Handle blocked first** — it is spending nothing and the run is stopped on it.
2. **Your own inbox** — `bus inbox <you>`, the verb that clears the cursor. `bus read` shows the same messages and leaves the count standing, so a board still reading unread after you handled something is telling you the truth about the cursor, not about the work.
3. **Reconcile against the workpaper board.** A unit the workpaper calls in-flight with no session behind it and no report on the bus is the case that costs the most to discover late.

Any statement you make to the human about what is running comes from this pass, made this beat. A four-lanes-are-moving report is worthless if two of those lanes finished an hour ago.

## The loop (per ready unit)
1. **Author the prompt** — the 5-part contract (`method.md`). Write the full prompt to `<bus_dir>/prompts/<unit>.md`.
2. **Dispatch over the bus** — `bus send <project>-orchestrator <unit-handle> "<unit>: <subject>" "execute prompts/<unit>.md; report when your gate is green" --ref prompts/<unit>.md` (send as *your own* handle, so the delegate's reply reaches you).
3. **Launch the delegate** — honor `spawn_mode`:
   - `auto-spawn` → `scripts/spawn <unit-handle>`, from the repo root, so the delegate inherits the right cwd. It picks the branch for the configured substrate and returns JSON naming the session.
   - `manual` → tell the human "unit `<unit>` is ready on the bus" and let them open the session. **A session the human opened names itself, so take its address from the board before you message it** (`substrate.md`).

   Arming the delegate's finished-signal is part of launching, not a later nicety: in the same beat as the spawn, arm whatever one-shot done/idle notice your runbook offers for that session, so a delegate that ends without reporting still wakes you. A unit whose board row names no wake channel is not dispatched yet, whatever the session list says.
4. **Stay lean** — do NOT do the unit's work. Read only the structured report that returns over the bus + the evidence it cites.
5. **Re-verify read-only** — re-derive the unit's gate from source-of-truth (re-read the diff, re-run the named check, query read-only). The report is a *hypothesis* until re-derived.
6. **Record** — update the board (▶→🔎→✅), a one-paragraph journal entry, and the human queue in the same beat: an answered ask drops off, a newly askable one lands, and the queue's header states the run state the journal just recorded. Nothing is "done" except recorded here with re-verified evidence, and the three surfaces move together. **Jot any posture that surfaced** into the workpaper's Standing Postures block as it appears, so the next `checkpoint` isn't reconstructed from memory. The board row for an in-flight unit records its armed wake (e.g. "wake: idle-notice armed" or "wake: manual, attended") — the beat's reconcile pass treats a row without one as a dispatch error to repair now.
7. **Release** — fan out newly-unblocked units; **serialize the commitment step with a lease** (`bus lock <repo>:<resource> --holder orchestrator` before a merge/deploy/publish, `bus unlock` after — see `git.md`). One commitment per resource in flight.

## Signalling a delegate mid-unit
A ruling lands, a sibling's result changes the lane, a wall needs an answer. **Send the content over the bus** so the record holds it, then **wake the delegate the way the runbook says**. Take its address from the board, verbatim. A live delegate picks the message up; one that has exited does not, and the ruling waits for a fresh charter against its lane.

## Retiring a delegate
Follow the runbook: on some substrates a finished session parks and costs nothing, on others it holds a surface the human wants back. Either way its context is the cheapest way to pull a thread on the work, so surface **"unit X done and verified"** with whatever disposition the runbook gives, and re-charter fresh rather than reviving a delegate whose context is spent.

## Parallelism
Builds/research fan out (disjoint lanes, one delegate each). Commitment serializes. `bus handles` is the board of handles and unread counts; the substrate's own board is what tells you who is still running.

## The contract (crossroads / human-only)
When you or a delegate hits a **design crossroads** or a **human-only/irreversible** step: **pause that unit** (siblings continue), record the ask on the `human` handle, write it into the queue file the human reads, and raise it in their chat surface (`human-in-the-loop.md`). Resume on the ruling. Otherwise proceed — intent + plan + **reversible with rollback established first**. Don't pause for backed-up, planned, intended work.

## Blocker triage
Resolve-from-data silently · re-charter or do-inline a stalled/failed unit · route genuine crossroads/human-only to the human (batched) · halt at a hard stop (data-loss, unsigned irreversible, un-re-derivable failure).

## Before you compact
Compaction silently resets standing postures. Before the human compacts, run `operations/checkpoint.md` — distill this run's postures, promote any that proved general (with the human's OK), and pin the rest to the workpaper's Standing Postures block so `resume` re-asserts them.

## Gate
The project's definition-of-done (`plan.md`) is met and re-verified; the board is all ✅; the journal records the evidence.
