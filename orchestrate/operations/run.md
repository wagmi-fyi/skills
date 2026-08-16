# Run — drive the orchestration

Be the lean orchestrator: dispatch ready units, auto-spawn visible delegates, re-verify every result, journal, repeat.

## Intent
Advance the project's graph to its gate, holding the workpaper as the source of truth, **mutating nothing yourself**, pausing only on crossroads/human-only.

## Load (every entry, including after a compaction)
`workpaper.md` (STATUS + **Standing Postures** + board + **newest journal = where we are**) → `plan.md` (graph + gates) → `reference/{human-in-the-loop,method,session-bus,substrate,git}.md`. **Honor the workpaper's Effective config over `config.yaml` defaults, and hold its Behavioral postures as live directives** — they're the run's real settings. Confirm the bus is live (`bus handles`); if not, run `install`. **Survey the shared bus first** (`bus handles` / `bus locks`) — other orchestrations may be live; take a **distinct, project-namespaced** handle (`<project>-orchestrator`) and coexist (`session-bus.md`). **Tidy the board while you're here** (orchestrator-only, once per standup): `bus gc --commit --if-stale 86400` — self-throttled to ~daily + self-logged, it retires only *fully-read* handles *idle >14d* (never `human`, an active lease-holder, or recent handles; archived to `processed/` and still in `log.jsonl`, so reversible). Register: `bus init <project>-orchestrator --pane "$TMUX_PANE" --role orchestrator`. **Ensure push-wake** (so worker reports auto-poke you instead of piling up unread): if `tmux` + `fswatch` are present, ensure one watcher — `scripts/bus-watch.sh status`, and if not running start it (`nohup scripts/bus-watch.sh &`, or a spare pane) — and report **push: on**; if either is missing, report **push: off — you'll poll/nudge** so it's explicit.

## The loop (per ready unit)
1. **Author the prompt** — the 5-part contract (`method.md`). Write the full prompt to `<bus_dir>/prompts/<unit>.md`.
2. **Dispatch over the bus** — `bus send <project>-orchestrator <unit-handle> "<unit>: <subject>" "execute prompts/<unit>.md; report when your gate is green" --ref prompts/<unit>.md` (send as *your own* handle, so the delegate's reply reaches you).
3. **Launch the delegate** — honor `spawn_mode`:
   - `auto-spawn` → `scripts/spawn <unit-handle>` — a **visible** pane; the delegate runs `operations/delegate.md`, picks up the assignment, executes. You can switch in anytime.
   - `manual` → tell the human "unit `<unit>` is ready on the bus" and let them launch it.
4. **Stay lean** — do NOT do the unit's work. Read only the structured report that returns over the bus + the evidence it cites.
5. **Re-verify read-only** — re-derive the unit's gate from source-of-truth (re-read the diff, re-run the named check, query read-only). The report is a *hypothesis* until re-derived.
6. **Record** — update the board (▶→🔎→✅) + a one-paragraph journal entry. Nothing is "done" except recorded here with re-verified evidence. **Jot any posture that surfaced** (a run-specific directive or config override you'll want to hold) into the workpaper's Standing Postures block as it appears, so the next `checkpoint` isn't reconstructed from memory.
7. **Release** — fan out newly-unblocked units; **serialize the commitment step with a lease** (`bus lock <repo>:<resource> --holder orchestrator` before a merge/deploy/publish, `bus unlock` after — see `git.md`). One commitment per resource in flight.

## Retiring a delegate pane
Under `close_mode: manual` (default), **never close a delegate pane yourself** — when its unit is done + verified, surface **"unit X done — pane ready; review and close it when you're satisfied"** and leave it parked. The human closes the window (or keeps it to pull a thread); closing it *is* the confirmation. Under `auto`, close it yourself once the unit is done + verified.

## Parallelism
Builds/research fan out (disjoint lanes, separate panes). Commitment serializes. `bus handles` is your live board of who's working.

## The contract (crossroads / human-only)
When you or a delegate hits a **design crossroads** or a **human-only/irreversible** step: `scripts/notify` + **pause that unit** (siblings continue); post the decision + your recommendation to the human's bus inbox; resume on the ruling. Otherwise proceed — intent + plan + **reversible with rollback established first**. Don't pause for backed-up, planned, intended work.

## Blocker triage
Resolve-from-data silently · re-charter or do-inline a stalled/failed unit · route genuine crossroads/human-only to the human (batched) · halt at a hard stop (data-loss, unsigned irreversible, un-re-derivable failure).

## Before you compact
Compaction silently resets standing postures. Before the human compacts, run `operations/checkpoint.md` — distill this run's postures, promote any that proved general (with the human's OK), and pin the rest to the workpaper's Standing Postures block so `resume` re-asserts them.

## Gate
The project's definition-of-done (`plan.md`) is met and re-verified; the board is all ✅; the journal records the evidence.
