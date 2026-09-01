# Recover — after a restart took every session down, relaunch them

A reboot, a crash, or a killed host process ends every session on the machine at once: orchestrators, delegates, any wake armed under a session. The bus data survives on disk and the board keeps describing a world that no longer exists. This operation rebuilds from **outside** the wreckage, then hands each relaunched session to `resume`.

## Intent
Bring a dead orchestration back to a live, self-consistent state: every persistent orchestrator running again where the human works, its in-flight delegates re-attached or deliberately re-chartered, and the bus board describing the machine that now exists. Trust nothing the board says until you check it against the live machine — a restart is the moment the board and reality diverge hardest.

## When to run this — and why it isn't `resume`
Run `recover` when the sessions are gone, not just one session's context:
- The substrate's board is empty, or missing every handle, while `bus handles` still lists them.
- You are a **fresh session or the human**, standing outside the orchestration. There is no live orchestrator to run this, which is the whole distinction. `resume` re-grounds one orchestrator that lost context; `recover` relaunches sessions that no longer exist, and each relaunched orchestrator then runs `resume` itself.

If the sessions are alive and only one lost its thread, stop — you want `resume`.

## The recovery sequence

1. **Confirm it is a host-level loss.** Compare the substrate's board against `scripts/bus handles`. Handles on the bus with no live session are what you are recovering.

2. **Take the manifest from the bus board.** `bus handles` is the list of what existed; `bus locks` shows any lease held by a dead session (stale — it re-takes at the next commitment; don't force it). **Orchestrator-role** handles are what this outside pass relaunches. **Worker-role** handles are the delegates that were in flight; you do not touch them from here, because each is reconciled by its own orchestrator during that orchestrator's `resume`. Record every handle with its role and its project, so the orchestrators know what was mid-flight.

3. **Map each handle to its session id.** The runbook names the listing that survives a restart and carries session ids; start there, since it resolves most of the manifest directly. For a session it does not list, recover the id from the harness's own transcripts, which are per-working-directory: the persistent orchestrators are the largest, most-recently-written ones, their last write is the crash instant, and counting a handle's occurrences disambiguates them, since the file that names a handle thousands of times is that orchestrator. A delegate's transcript is smaller and unit-scoped. Record `handle → session-id → cwd`.

4. **Relaunch each orchestrator where the human works — hand over the command, don't spawn it.** An orchestrator has to live on the human's own surface, so this is a human-shaped step even under `spawn_mode: auto-spawn`. Prepare one ready-to-paste command per orchestrator, in the resume form the runbook gives, and **start it in that orchestrator's own working directory**: a session id usually resolves against the project the cwd names, and the launcher does not set cwd for you.

5. **Hand each relaunched orchestrator to `resume`.** Resuming restores the session's pre-crash mind, so it does not know the machine went down until you tell it. Pair each launch command with a kickoff that names the restart and directs the session to run `resume` — which re-registers its handle, re-derives live state from source-of-truth, and reconciles its own delegates:
   - **Re-attach an in-flight delegate** by session id, in whatever spawn-and-resume form the runbook gives, then hand it a kickoff so it re-verifies its own in-flight work before continuing: did its last write land, is its lane clean, where in the unit was it?
   - **Re-charter fresh** only where a resume is unsafe — an uncommitted or half-mutated lane the re-verify can't reconcile. The resumed delegate's own re-verify is what tells you which.

6. **Board hygiene.** Once the live sessions re-register, what remains on the bus is harmless: a dead handle is a name with no session behind it. Check the runbook first, though, because a substrate that recycles session addresses can leave a stale registration pointing at a **live, unrelated** session, and that one is not harmless. Clear the stragglers at leisure with a plain `bus gc --commit`, after consuming your own unread (`bus inbox <your-handle>` — only ever your own; gc won't retire a handle with unread > 0). **Never pass `--days 0`**: idle is always ≥ 0, so it retires every fully-read handle, live ones included. Retired handles archive to `processed/` and stay in `log.jsonl`, so it is reversible. Delegates never gc.

## Durable gotchas
- **A dead session is not lost work.** Every unit's output lives in its commits. Read the lane before you conclude a delegate needs re-chartering, and re-attach by session id when the lane is clean.
- **recover doesn't re-derive state — `resume` does.** The recovering session relaunches and hands off. It must not start doing orchestrator work; each orchestrator re-verifies its own deploy revs, shas and credentials in `resume`.
- **A lease that lapsed in the crash is fine.** Never hold a commitment lease across a restart; it re-takes at the next commitment.
- **The board outlives the machine.** Handles, inboxes, cursors and the archive are files. That is why the manifest survives a restart that took every process with it.

## Gate
Every orchestrator from the manifest is running and has completed its own `resume`; every in-flight delegate is re-attached or deliberately re-chartered with the decision recorded; the substrate's board and `bus handles` describe the same set of live sessions; no orchestration is waiting on a session that no longer exists.

## References
- `operations/resume.md` — the per-session re-ground each relaunched orchestrator runs; `recover` fans out into it.
- `reference/substrate.md` and this run's runbook under `reference/substrates/` — the relaunch and re-attach forms this whole operation depends on.
- `reference/session-bus.md` — handles, roles, and why inboxes are directed.
