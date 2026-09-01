# Substrate runbook — tmux panes

Sessions are **visible panes** in a tmux server on one machine. **This substrate requires the orchestrator itself to be running inside tmux.** A session that is not — a desktop or IDE chat, a background agent — cannot open a pane, and no flag or borrowed session substitutes; there it is a wall (`substrate.md`). Where the orchestrator does run in a pane, any CLI harness runs in one too, so a substrate with no runbook of its own is usually workable here.

Two things a pane gives that nothing else in this skill does. A delegate is a **visible pane the human switches into and drives**, and an external event can **poke a session's stdin** the instant work arrives, with no idle token burn. You can run tmux inside an editor's terminal tab; the sessions become panes in that tab.

## Hosting

Start tmux (`tmux new -s orch`) or `tmux attach`. Run each session in a pane. Panes survive the terminal closing, not machine sleep.

## Spawn

`scripts/spawn <handle> [instruction]` opens a new **named, visible** window running the delegate, and pre-registers its pane so a poke works immediately. Send the assignment over the bus **before** spawning.

## Registration

`bus init <handle> --pane "$TMUX_PANE" --role <role>`. The pane is what makes a session pokeable, and `bus init` treats it as exclusive: registering a pane another handle claims takes it, with a warning naming the loser.

**Never register a second handle from your own pane.** `bus init` defaults `--pane` to `$TMUX_PANE`, so a probe or a ledger handle registered from the orchestrator's pane silently takes it and disarms the orchestrator's own wake while traffic keeps flowing. A handle that is not a session gets `--pane -`, which records no pane. **After any bus experiment, re-derive `bus handles` and confirm your pane is still yours** — a blanked binding is invisible from the traffic.

## The board — liveness is pane CONTENT, not the command column

`tmux capture-pane -p -t <pane>` and read the live output: a spinner, a todo list, an input prompt.

- ⚠ **A spawned delegate shows `zsh` (or the launch wrapper) in `pane_current_command` while `claude` runs inside it.** Do not read that column as "dead".
- ⚠ **A scrolled-up pane lies too.** With a `N new messages ↓` indicator visible, `capture-pane -p` returns the frozen visible region rather than the live tail, so a plain capture, and even a same-position double capture five seconds apart, falsely reads idle. The reliable signals while scrolled are the footer **context-token counter advancing** and the **new-message count climbing**. To see the real output, `capture-pane -p -S -250` and read its true tail, or scroll the pane to the bottom first.

Classify each delegate: *alive and working* (leave it), *parked and waiting* (it needs a signal), *exited* (re-charter, or re-attach by session id).

**Sweep for strays.** Scan `tmux list-panes -a` for stale named delegate panes from closed units, and for **text sitting unsubmitted in a pane's input line** — a poke that lost its Enter. Stranded input is how a completion signal dies quietly and how a finished delegate reads as in-progress. Clearing it does not stick: the TUI restores cleared text on the next empty-input keypress. Dispose of it by appending an explicit retraction and submitting deliberately, then verify the pane started processing.

## Wake — push

`scripts/bus-watch.sh` (needs `fswatch` and `tmux`) watches the inbox directory and pokes the recipient's pane on a new message. **One machine-level daemon serves every orchestration on the bus;** the pidfile guard refuses a second. `status` answers "already up?", `stop` and `restart` manage it, and **`restart` is required after editing it**, because a running watcher holds the old code.

- **A sent poke is not a delivered poke.** Send the text and the Enter as **separate** `send-keys` calls, then `capture-pane` and confirm the recipient actually started processing: a spinner or an advancing token counter, not your text sitting in its input line. A single combined send gets coalesced and the TUI reads the trailing newline as a literal newline in the composer.
- **Push carries the wake leg of a report.** With the watcher up, a delegate's `bus send` pokes the orchestrator's pane by itself, so the record and the wake a report owes (`delegate.md`) are one act. With the watcher down they come apart, and the sender pokes the recipient's pane itself, under the delivery rule above.
- **Push replaces polling** for the message-wake job. A timed loop is a slow failsafe for a missed poke, or for genuinely time-triggered work. Unattended, one of the two has to be standing: push where the watcher is up and proven (`status` answers that in one command), else a timed loop running `run.md`'s beat. Establish which one before the human leaves.
- **The cursor is the truth; the poke is a hint.** A poke whose `bus inbox` returns nothing is the watcher's own defect, not a lost message. What is forbidden is treating a poke as proof of a message and skipping the cheap cursor check.

**Two open defects in this watcher, found in the field and not yet fixed.** It pokes on a **file event** rather than on the recipient's **unread count**, so every read re-announces itself and a careful reader sustains a poke loop that costs a turn per cycle. And it logs only if its caller redirects stdout, so a detached watcher started without a redirect keeps no record. On a quiet bus the loop is a net tax; stopping the watcher is a documented operation that reverses in one command, and delivery is unaffected because only the wake goes away.

## Reach the human

`scripts/notify "<title>" "<msg>"` raises a desktop notification and a terminal bell. `scripts/present <file.html>` opens a decision brief in the browser. Both are for a machine the human is **sitting at**.

⚠ **On a headless host both reach nobody and still exit 0.** `notify` finds no notifier and prints one line into an unattended pane; `present` finds no opener and prints a `file://` path naming the wrong machine. An exit status of 0 is not delivery, and neither script can say so. There the reach is the `human` handle plus the human's chat surface, and a brief gets published where they already look (`human-in-the-loop.md`, `decision-briefs.md`). **Establish which of the two worlds the run is in before the first escalation and record it in the workpaper.**

## Retire

A pane persists after its delegate exits. How it is retired is a run-level posture recorded in the workpaper's Effective config:

| Posture | Meaning |
|---|---|
| manual | the orchestrator never closes a pane. It surfaces "unit X done, pane ready, review and close it when you're satisfied" and the human closes the window. Closing it is the confirmation |
| auto | the orchestrator closes the pane once the unit is done and verified |

## Recover after a server restart

**The pane-id counter resets to `%0` on a new server**, so every recorded pane is stale and some now point at unrelated live panes, where a poke types into the wrong session. Rebuild registrations before trusting any poke; `bus init` clears a colliding claim as each live session re-registers, so the dangerous case self-heals. Relaunch each session from a **fresh OS window**, never a pane nested inside the recovering session's own server, and rename each relaunched tmux session to its project so the window list stays navigable.

## Per-OS notes

| Capability | macOS | Linux (desktop) | Linux (headless) | Windows |
|---|---|---|---|---|
| bus (files) | yes | yes | yes | yes, portable lock shim |
| tmux spawn and push | yes | yes | yes | via WSL. Native: no tmux, so open windows manually and nudge by hand |
| notify | `osascript` | `notify-send` | **none — use the `human` handle plus chat** | PowerShell toast, else the bell |
| present | `open` | `xdg-open` | **none — publish the brief where the human looks** | `start` |
| fswatch push | `brew install fswatch` | `apt install fswatch` | `apt install fswatch` | WSL only |

On native Windows without WSL the bus, the method and a manual nudge all work, while spawn and push degrade to the human launching panes. Note it in the workpaper when it applies.

## What fails silently here

- **A poke that lost its Enter** sits in the input line forever and reads as a delegate that never woke.
- **`pane_current_command` and a scrolled pane** both report a working delegate as idle.
- **notify and present on a headless host** exit 0 having reached nobody.
- **A pane id recycled after a server restart** turns a poke into keystrokes in an unrelated session.
