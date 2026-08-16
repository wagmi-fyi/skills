# Recover — after a tmux-server crash, re-stand the substrate and relaunch every session

A machine crash or a `tmux kill-server` takes down the **whole** orchestration at once: every session dies, every bus pane registration goes stale, and push-wake plus any SSH control-masters are gone. This operation rebuilds the substrate and relaunches each persistent session from **outside** the wreckage — then hands each one to `resume`.

## Intent
Bring a crashed orchestration back to a live, self-consistent state: the substrate running again, every persistent orchestrator relaunched into its own visible window on a *fresh* pane — each re-registered, re-grounded, renamed to its project, and re-attaching the delegates that were mid-flight — and the bus board describing the world that now exists rather than the one that died. Trust nothing the board says until you re-verify it against the live machine — a crash is the moment the board and reality diverge hardest.

## When to run this — and why it isn't `resume`
Run `recover` when the tmux **server** is gone, not just one session's context:
- `tmux ls` shows an empty server (or only a fresh, unrelated session) while `bus handles` still lists panes — the recorded `pane=%NN` values point at panes that no longer exist.
- You are a **fresh session or the human**, standing *outside* the orchestration. There is no live orchestrator to run this — that's the whole distinction. `resume` re-grounds *one* orchestrator that lost context but whose server, bus, and pane survived; `recover` rebuilds the substrate *underneath* the sessions and relaunches them, and each relaunched orchestrator then runs `resume` itself. `recover` fans out into N× `resume` the way `resume` hands off to `run`.

If the server and its panes are intact and only one session lost its thread, stop — you want `resume`, not this.

## The recovery sequence

1. **Confirm it's a server-level loss.** Compare `tmux list-panes -a` against `scripts/bus handles`. If the board's panes aren't in the live server, the registrations are stale and you're in the right operation. A restarted server is the trap underneath everything below: **tmux's pane-id counter resets to `%0`**, so every recorded pane is now either dead (a poke fails silently — harmless) or, worse, **reassigned to an unrelated live pane** (a poke types keystrokes into the wrong session). This is why you rebuild registrations instead of trusting them — and why "just re-attach and keep going" is unsafe.

2. **Take the manifest from the bus board.** `bus handles` is the list of what existed; `bus locks` shows any lease held by a now-dead session (stale — it re-takes at the next commitment; don't force it). The **orchestrator-role** handles are the persistent sessions *this outside operation* relaunches (step 5). **Worker-role** handles are the delegates that were in flight — you do **not** touch them from here; each is reconciled by *its own orchestrator* during that orchestrator's `resume` (re-attached via `claude --resume` into a fresh pane, or re-chartered fresh only where its lane is unsafe — step 6). Record **every** handle, its role, and its stale pane: the orchestrator handles you relaunch (overwriting their registration in step 6), and the worker handles so their orchestrator has the manifest of what was mid-flight to re-attach.

3. **Map each orchestrator handle → its Claude session id.** The bus stores handles and panes, not session ids, so recover the id from the transcripts. Each orchestrator's transcript lives under `~/.claude/projects/<cwd-slug>/` (the working directory with `/` turned to `-`). The persistent orchestrators are the **largest, most-recently-written** `.jsonl` files — their last write is the crash instant. Disambiguate by content: `grep -c '<handle>' <file>.jsonl` and grep for the workpaper path each orchestrator drives; the file that names a handle thousands of times is that orchestrator. Record `handle → session-id → project cwd`. (Fallback when a fingerprint is ambiguous: launch `claude --resume` with no id and pick from the interactive list by its summary.) The **same fingerprint recovers a delegate's** session id when its orchestrator re-attaches it in step 6 — grep the delegate handle + its unit's `--ref` path; a delegate transcript is smaller and unit-scoped, not the multi-hundred-message orchestrator log.

4. **Re-stand the machine substrate — once, machine-level.** Push-wake died with the server; restart the single per-machine instance (`scripts/bus-watch.sh restart`; idempotent). Any SSH control-master / tunnel sockets under `/tmp` died too — note them, but **don't re-auth them from here**; each orchestrator re-establishes its own during `resume`. The bus *data* is on disk (`bus_dir`, resolved from `config.yaml`) and survived — nothing to restore there.

5. **Relaunch each orchestrator in its own visible window — hand the human the commands; don't auto-spawn.** Each orchestrator must come back on a **fresh pane with a clean id**, which means a **fresh OS terminal window** — *not* a pane nested inside the recovering session's own tmux (that inherits a confused/nested id). The recovering session is standing outside the wreckage and is itself usually in a tmux pane, so it can't cleanly open those windows. So — like any login-shaped step — it **prepares one ready-to-paste command per orchestrator and hands them to the human**, who opens a window per orchestrator and runs it. This holds even under `spawn_mode: auto-spawn`: that governs an orchestrator spawning *delegate* panes into its own live server, not re-standing *orchestrators* after the server itself died. Launching a session is human-reachable by nature.

   The command to hand over, per orchestrator — this is what we actually paste:
   ```
   cd <project-cwd> && claude --resume <session-id>
   ```
   The `cd` is load-bearing: `--resume <id>` resolves the session against the project dir derived from the cwd, and the launch wrapper doesn't set cwd itself. On a machine whose `claude` is the auto-tmux wrapper (from `install`), this self-wraps into a fresh `claude-<pid>` tmux session with a clean pane id — the orchestrator **renames it to the project name** on standup (step 6). On a machine **without** the wrapper, hand over the explicit two-liner instead, so the session is named + cwd'd from the start:
   ```
   tmux new-session -A -s <project> -c <project-cwd>   # then, inside it:
   claude --resume <session-id>
   ```
   One window per orchestrator. Present each launch command **paired with its step-6 kickoff** (below) as a ready-to-paste block, so the human opens the window, runs the launch line, then pastes the kickoff.

6. **Hand each relaunched orchestrator to `resume`.** recover restores the machine; `resume` re-grounds the session. Give the human a per-orchestrator **kickoff** to paste into the window they just opened (a `--resume` restores the session's pre-crash mind — it won't know the server died until the kickoff tells it). The kickoff directs the session to run `resume` — which:
   - re-registers its handle onto its **new** pane (`bus init <handle> --pane "$TMUX_PANE" --role <role>`, overwriting the stale entry from step 2);
   - **renames its tmux session to the project name** (`tmux rename-session <project>`) — a pid-named `claude-<pid>` from the launch wrapper is illegible in the window list;
   - confirms push-wake and re-derives live state from source-of-truth;
   - **re-attaches the delegates the crash killed** — for each in-flight worker handle, recover its session id by the step-3 fingerprint, open a fresh pane running `claude --resume <delegate-session-id>`, re-register that delegate onto its new pane, and hand it a **crash kickoff** so it *re-grounds and re-verifies its own in-flight work before continuing* (did its last write/build land? is its lane clean? where in the unit was it?). Re-charter a **fresh** delegate only where a resume is unsafe — an uncommitted or half-mutated lane the re-verify can't reconcile; a resumed delegate's own re-verify is what tells you which.

   The kickoff carries the session's **old** pane id so it knows which stale registration it's replacing (and, per step 7, its `bus init` now auto-evicts any *other* handle still parked on its new pane).

7. **Board hygiene — the dangerous collisions self-heal at re-registration.** The reborn server hands out low pane ids again, so a dead worker registration from step 2 can land on the same id as a freshly-spawned live pane — the dangerous case from step 1. **`bus init` now clears this on its own:** when a live session registers onto pane `%N` (step 6), any other handle still claiming `%N` has its pane dropped on the spot, so push-wake can't mis-poke it. Nothing to run for the dangerous case — it's healed the moment each orchestrator + re-attached delegate re-registers. What remains are *harmless* stale entries (dead handles whose old pane id no live session reclaimed — a poke there just fails silently into a nonexistent pane); clear those at leisure with a plain `bus gc --commit`, consuming your own straggler unread first (`bus inbox <handle>` — only ever *your own* inbox; gc won't retire unread > 0). **Don't reach for `bus gc --days 0`** — a zero threshold sweeps *every* fully-read handle (idle is always ≥ 0), live ones included; you don't need it, because collisions are handled at registration and gc dates a handle by the *later* of its last message and its last (re)registration, so a re-registered session is safe at any `--days ≥ 1`. Retired handles archive to `processed/` and stay in `log.jsonl` — reversible. Delegates never gc.

## Durable gotchas
- **The pane-id counter resets on a new server.** Every recorded pane is stale after a crash; a stale registration that lands on a live-but-unrelated pane makes push-wake type into the wrong session. Rebuild registrations (step 6) before trusting any poke.
- **Relaunch every live session — orchestrators *and* their in-flight delegates — via `claude --resume`, and re-ground each before it continues.** A crash freezes a delegate's transcript on disk *identically* to the orchestrator's, so resuming restores its context; the re-ground is what catches whatever moved underneath it (an interrupted build, a half-written file) — the same trust-nothing pass the orchestrator runs. This *outside* session relaunches only the orchestrator-role handles; each orchestrator re-attaches its own delegates during `resume`. Re-charter a delegate fresh only when its lane is unsafe to resume, and the resume's own re-verify is what tells you which — blanket-re-chartering every delegate needlessly discards recoverable context.
- **Name each relaunched session for its project.** The launch wrapper self-names `claude-<pid>`; rename to `migration` / `apps` on standup (step 6) so the window list is navigable and re-attachable, not a wall of pids.
- **Relaunch from a fresh window, never by nesting.** A new pane inside the old-name tmux inherits confusion; a fresh OS window gives a clean pane id the orchestrator can claim cleanly.
- **A lease that lapsed in the crash is fine.** Never hold a commitment lease across a crash; it re-takes at the next commitment.
- **recover doesn't re-derive state — `resume` does.** Don't let the recovering session start doing orchestrator work; its only job is to relaunch and hand off. Each orchestrator re-verifies its own live state (deploy rev, sha's, credentials) in `resume`.

## Gate
Recovery is complete when: `tmux list-panes -a` shows one live pane per relaunched orchestrator **and per re-attached delegate**; each live tmux session is **named for its project**, not a pid; every handle's `bus pane <handle>` matches its live `$TMUX_PANE`; push-wake is running; no handle still points at a live, unrelated pane; and each orchestrator has run `resume`, re-derived its own state, and **re-attached (or deliberately re-chartered) its in-flight delegates**. The board once again describes the live machine.

## References
- `operations/resume.md` — the per-session re-ground each relaunched orchestrator runs; `recover` fans out into it.
- `operations/install.md` — the substrate this restores (the bus, the auto-tmux `claude` wrapper, the push-wake daemon).
- `reference/session-bus.md` — handles, roles, `bus gc`, and why inboxes are directed (a fresh handle inherits no stale traffic).
- `reference/substrate.md` — tmux panes, push-wake, and pane registration.
