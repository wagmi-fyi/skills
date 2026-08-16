# Substrate — tmux foundation, spawn, push-wake, notify

The orchestration runs on a **tmux** foundation. tmux is what makes two things possible that native Claude Code can't:
1. **Human-reachable spawned sessions** — an orchestrator opens a delegate as a **visible pane** you can switch into and drive. Native subagents/Workflow are headless + hidden → they fail the "all real work is reachable" rule.
2. **Push-wake** — an external event (mail landing) pokes a session's stdin the instant there's work, with **zero idle token burn** (a native file-watcher waits, not a polling model turn).

You can run tmux **inside a VS Code terminal tab** — your sessions become tmux panes in that tab (navigate `Ctrl-b` + numbers) instead of native VS Code tabs.

## Hosting
Start tmux (`tmux new -s orch`) or `tmux attach`. Run each Claude Code session in a pane. Panes survive the terminal closing (not machine sleep). Each session registers: `bus init <handle> --pane "$TMUX_PANE"`.

## Spawn (orchestrator → visible delegate)
`scripts/spawn <handle> [initial-instruction]` opens a new **named, visible** tmux window running `claude`, seeded to run the `delegate` operation and pick up its assignment from the bus. The orchestrator `bus send`s the assignment **before** spawning. The pane is a peer session — watch it, take it over, or let it report back. (Honor `spawn_mode`: `auto-spawn` ⇒ orchestrator spawns; `manual` ⇒ tell the human to launch.)

## Push-wake
`scripts/bus-watch.sh` (`brew install fswatch` / `apt install fswatch` first) watches `inbox/` and, on a new message, `tmux send-keys` the recipient's pane an instruction to check the bus. It is **one machine-level daemon serving every orchestration on the bus** — a single instance (the pidfile guard refuses a second).

Manage it as the long-lived process it is, rather than re-improvising:
- `bus-watch.sh` — run in the foreground (`run.md` backgrounds this with `nohup … &`).
- `bus-watch.sh status` — is one running? (use for the "already up?" check)
- `bus-watch.sh stop` / `restart` — stop, or stop + relaunch detached. **Run `restart` after editing the script** — a running watcher holds the old code until bounced. (Both target the pidfile pid + its children, never a command-pattern, so they can't self-kill the caller.)

- **Poke-then-verify — a sent poke is not a delivered poke.** A `send-keys` poke to a Claude Code pane can strand (bracketed paste eats the Enter) or sit queued without submitting. Send the text and the Enter as **separate** `send-keys` calls, then `capture-pane` and confirm the recipient actually started processing (a spinner / advancing token counter — not just your text sitting in its input line). Applies equally to the watcher's automatic pokes (verify the important ones) and to any manual nudge.
- **Push replaces polling** for the message-wake job. Use a `/loop` only as (a) a slow failsafe for a missed poke, or (b) genuinely time-triggered work (not message-driven).

## Notify (crossroads / human-only escalation)
`scripts/notify "<title>" "<msg>"` raises a desktop notification + terminal bell, cross-platform. Used when an agent hits a design crossroads or a human-only step (see `human-in-the-loop.md`) — paired with **pausing the unit**.

## Per-OS matrix
| Capability | macOS | Linux | Windows |
|---|---|---|---|
| Bus (files) | ✅ | ✅ | ✅ (portable lock shim) |
| tmux spawn/push | ✅ | ✅ | via **WSL**; native: no tmux → open panes with Windows Terminal `wt`, **manual nudge** (no send-keys) |
| notify | `osascript` | `notify-send` | PowerShell toast / bell fallback |
| fswatch push | `brew install fswatch` | `apt install fswatch` | WSL only |

On native Windows without WSL: the **bus + method + manual nudge** work; **auto-spawn + push degrade** to the human launching panes and nudging. Note this in the project workpaper if it applies.
