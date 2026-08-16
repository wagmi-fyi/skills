---
name: orchestrate
license: Apache-2.0
description: "Run a verifying, multi-session orchestration: bootstrap a delegated project (plan + workpaper), dispatch work to human-reachable sessions over a built-in message bus, auto-spawn visible delegate panes, and re-verify every result. Use when coordinating several Claude Code sessions on one machine, running an orchestrator plus delegated workers, delegating build/migration/research units while staying lean, or setting up the session-bus. Pauses for design crossroads and human-only steps; runs autonomously otherwise."
---

# Orchestrate

Run real work as a **verifying orchestration**: one lean **orchestrator** session decomposes a project, delegates each unit to a **fresh working session** as a self-contained prompt, and **re-derives every result** before trusting it — while a built-in **message bus** carries prompts and reports between sessions so a human isn't the relay.

This skill packages a proven method for running real work as a verifying, multi-session orchestration into a self-contained capability. The method is **general** — domain specifics live in the artifacts `bootstrap` generates, not in this skill.

## The one rule that shapes everything

Agents **pause and bring the human in** on exactly two things — **design crossroads** (genuine trade-offs not settled by intent/plan) and **human-only / irreversible** steps (logins, secrets, external sends). **Everything else runs autonomously** — even large changes to critical systems — when it is **grounded in intent, backed by a plan, and reversible with the rollback path established first.** See `reference/human-in-the-loop.md`. Applies to orchestrator and delegates alike.

## Activation

When invoked:
1. **Orient.** Which operation? *install* (stand up the substrate), *bootstrap* (new project), *run* (drive an existing one), *checkpoint* (**pin standing postures before the human compacts**), *condense* (**archive workpaper history verbatim so the live file stays resume-sized** — checkpoint runs this check itself), *resume* (**re-ground after a compaction / cold re-entry**, then run), *recover* (**after a tmux-server / machine crash — re-stand the substrate + relaunch every session, each of which then resumes**), or *delegate* (you are a worker the human/orchestrator launched).
2. **Load `config.yaml`** and resolve: `output_dir`, `bus_dir`, `spawn_mode`, `notify`. If `output_dir` is blank, it is **established at bootstrap** (detect repo convention → propose → confirm) and recorded in the project workpaper — never hardcoded.
3. **Load the contract + method** — `reference/human-in-the-loop.md` then `reference/method.md` — before acting.
4. **Check the substrate** — does `scripts/bus handles` work? If not, run `operations/install.md` first.
5. Load the matching operation.

## Configuration (`config.yaml`)
| Key | Meaning |
|---|---|
| `output_dir` | where `<project>/plan.md` + `workpaper.md` are written. Blank ⇒ bootstrap establishes + records it. Resolved relative to the orchestrator's repo root, or absolute. |
| `bus_dir` | bus data location (default `~/.claude/session-bus`; CLI also honors `$SESSION_BUS_DIR`) |
| `spawn_mode` | `auto-spawn` (orchestrator opens visible panes) or `manual` (human launches) |
| `close_mode` | `manual` (orchestrator never closes — surfaces "ready, you close it"; the human closes the window) or `auto` (orchestrator closes when done+verified) |
| `delegate_skip_permissions` | launch spawned delegates with `--dangerously-skip-permissions` so they don't stall on prompts. Ships **false** (security bypass — opt in per machine) |
| `notify` | raise a desktop alert + pause on a crossroads/human-only step |

A run can **override any of these for itself** via the workpaper's **Standing Postures → Effective config** block (e.g. `close_mode: auto` on a run whose skill default is `manual`). Those overrides survive compaction and are re-asserted on `resume`; `config.yaml` holds the machine baseline, the workpaper holds the run's effective settings. See `operations/checkpoint.md`.

## Requirements
Minimal — run `install` to check + set up. **No language packages** (the bus is Python **standard-library only**), so no `requirements.txt`; the dependencies are system tools:
- **Python 3** — the bus CLI (stdlib only).
- **tmux** — visible spawn + push-wake (degrades to a manual nudge without it).
- **fswatch** — push-wake only (optional).
- **git** — worktree/branch lanes for build units.
- **claude** CLI — to spawn delegate sessions.
- OS-native browser opener (`open`/`xdg-open`) + notifier (`osascript`/`notify-send`) — for `present`/`notify`; nothing to install.

## Operations
| Operation | File | Use When |
|---|---|---|
| Install | `operations/install.md` | First use on a machine — stand up the bus + tmux/push substrate; health-check |
| Bootstrap | `operations/bootstrap.md` | Starting a new orchestrated project — decompose a brief into a unit/wave graph + gates; scaffold plan + workpaper |
| Run | `operations/run.md` | Driving an orchestration — dispatch ready units over the bus, auto-spawn visible delegate panes, re-verify, journal |
| Checkpoint | `operations/checkpoint.md` | **Before the human compacts** — distill this run's standing postures, promote any proven general (with OK), pin the rest to the workpaper so they survive |
| Condense | `operations/condense.md` | The live workpaper has outgrown a single read, or closed-work history outweighs live state — archive history **verbatim** to a dated sibling, keep the live file resume-sized (checkpoint runs this check; also fine standalone at a quiet moment) |
| Resume | `operations/resume.md` | Re-entering after a **compaction** or cold re-entry — re-assert standing postures + re-ground into the role + re-derive live state (trust nothing) before handing off to `run` |
| Recover | `operations/recover.md` | After a **tmux-server crash or machine reboot** — the whole server is gone, so every session + pane registration is stale; re-stand the substrate, relaunch each orchestrator into a fresh window, each of which then `resume`s. Run from *outside* the dead orchestration (a fresh session or the human) |
| Delegate | `operations/delegate.md` | You are a delegated working session — pick up your assignment from the bus, execute one unit, report back |

## Reference
| Reference | File | Precondition |
|---|---|---|
| Human-in-the-loop | `reference/human-in-the-loop.md` | Before any work — the crossroads + reversibility contract (when to pause, when to proceed) |
| Method | `reference/method.md` | Before any orchestrate work — the model, the prompt contract, verify-don't-trust, the loop |
| Session bus | `reference/session-bus.md` | When sending/receiving over the bus — verbs, directed inboxes, pointers, cross-verify |
| Substrate | `reference/substrate.md` | When spawning/waking sessions — tmux panes, push-wake, notify, per-OS setup |
| Git lanes + locks | `reference/git.md` | When planning worktree lanes or serializing a commitment (merge/deploy/publish) across concurrent orchestrations |
| Decision briefs | `reference/decision-briefs.md` | When escalating a complex/meaty/UI crossroads — render it visually (the agent judges the look) instead of as text |

## Scripts
Invoke by full path: `~/.claude/skills/orchestrate/scripts/<name>` (`--help` where applicable).
- `bus` — the message-bus CLI (`init`/`send`/`inbox`/`read`/`log`/`handles`/`pane`/`rotate`/`gc`).
- `spawn <handle>` — open a **visible** tmux pane running a delegate session (orchestrator-side).
- `notify <title> <msg>` — cross-platform alert for a crossroads escalation.
- `present <file.html>` — open a decision-brief HTML in the browser (crossroads escalation).
- `bus-watch.sh [run|status|stop|restart]` — push-wake daemon (fswatch → tmux send-keys); one machine-level instance; `restart` after editing it.
