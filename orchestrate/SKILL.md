---
name: orchestrate
license: Apache-2.0
description: "Run a verifying, multi-session orchestration: bootstrap a delegated project (plan + workpaper), dispatch work to human-reachable sessions over a built-in message bus, spawn delegates on whatever substrate the machine runs, and re-verify every result. Use when coordinating several agent sessions on one machine, running an orchestrator plus delegated workers, delegating build/migration/research units while staying lean, or setting up the session-bus. Pauses for design crossroads and human-only steps; runs autonomously otherwise."
---

# Orchestrate

Run real work as a **verifying orchestration**: one lean **orchestrator** session decomposes a project, delegates each unit to a **fresh working session** as a self-contained prompt, and **re-derives every result** before trusting it — while a built-in **message bus** carries prompts and reports between sessions so a human isn't the relay.

This skill packages a proven method for running real work as a verifying, multi-session orchestration into a self-contained capability. The method is **general** — domain specifics live in the artifacts `bootstrap` generates, not in this skill.

## The one rule that shapes everything

Agents **pause and bring the human in** on exactly two things — **design crossroads** (genuine trade-offs not settled by intent/plan) and **human-only / irreversible** steps (logins, secrets, external sends). **Everything else runs autonomously** — even large changes to critical systems — when it is **grounded in intent, backed by a plan, and reversible with the rollback path established first.** See `reference/human-in-the-loop.md`. Applies to orchestrator and delegates alike.

## Where a rule binds

A rule that depends on being read is not enforced, and a session's report of what it loaded is a claim. So a load-bearing rule lives in one of three places: **the boot instruction a session cannot skip, a refusal by a script that needs no reader, or a gate a unit cannot close without satisfying.** Prose in a reference file is documentation. `scripts/spawn` is built that way — the instruction it hands every delegate carries the two rules that break under friction, and a substrate it cannot run makes it refuse rather than warn.

## Where the mechanism lives

The operations and references below say **what** a run does. How a session is started, addressed, watched, woken and retired differs by host, so the core never names a mechanism: it names the act and points at `reference/substrate.md`, which routes to one runbook under `reference/substrates/`. **Resolve the substrate at activation and record it in the workpaper** before the first spawn.

## Activation

When invoked:
1. **Orient.** Which operation? *install* (stand up the substrate), *bootstrap* (new project), *run* (drive an existing one), *checkpoint* (**pin standing postures before the human compacts**), *condense* (**archive workpaper history verbatim so the live file stays resume-sized** — checkpoint runs this check itself), *resume* (**re-ground after a compaction / cold re-entry**, then run), *recover* (**after a restart took every session down — relaunch, each relaunched session then resuming**), or *delegate* (you are a worker the human/orchestrator launched).
2. **Resolve the settings** — `output_dir`, `bus_dir`, `substrate`, `spawn_mode`, `delegate_model`, `delegate_skip_permissions` — through the seam: the environment, then the machine conf, then the shipped `config.yaml` (`reference/substrate.md`). If `output_dir` is blank, it is **established at bootstrap** (detect repo convention → propose → confirm) and recorded in the project workpaper — never hardcoded.
3. **Detect the substrate, confirm its mechanism exists, then load its runbook.** `scripts/spawn --check` does the first two and prints what it found; `reference/substrate.md` says how the answer is chosen and overridden, then routes you to one runbook. A workpaper that records a substrate overrides everything below it. **A mechanism that is absent is a wall** — report it, never route around it.
4. **Load the contract + method** — `reference/human-in-the-loop.md` then `reference/method.md` — before acting.
5. **Check the bus** — does `scripts/bus handles` work? If not, run `operations/install.md` first.
6. Load the matching operation.

## Configuration (the settings seam)

Every setting resolves in one order: **the environment, then the machine conf (`$ORCHESTRATE_CONF`, default `/etc/orchestrate.conf`), then the shipped `config.yaml`.** A deployment that keeps its config somewhere else points `$ORCHESTRATE_CONF` at it. Under a managed install the skill folder is root-owned, so `config.yaml` is the layer a local ruling cannot be written into; the conf file is where a machine's ruling goes. Shell-side layers name each setting as its key uppercased and prefixed, so `delegate_model` becomes `ORCHESTRATE_DELEGATE_MODEL`. The conf file takes one `KEY="value"` per line. `scripts/spawn --check` reports every resolved value and its origin.

| Key | Meaning |
|---|---|
| `output_dir` | where `<project>/plan.md` + `workpaper.md` are written. Blank ⇒ bootstrap establishes + records it. Resolved relative to the orchestrator's repo root, or absolute. |
| `bus_dir` | bus data location (default `~/.claude/session-bus`; the CLI also honors `$SESSION_BUS_DIR`). `scripts/spawn` resolves it here and hands every delegate the path as a value, so a machine-wide `$SESSION_BUS_DIR` cannot put a delegate on a bus its orchestrator is not reading |
| `shared_bus` | the bus every account on this machine joins, where one exists. `scripts/desktop-wake --check` reports it, so a session can tell the shared rail from a private one. Blank ⇒ no shared bus, which is the ordinary single-person case |
| `substrate` | which runbook under `reference/substrates/` describes this machine. Detection answers it when nothing declares one; a run's workpaper overrides both |
| `spawn_mode` | `auto-spawn` (the orchestrator starts each delegate) or `manual` (the human opens the session) |
| `delegate_skip_permissions` | launch spawned delegates with permission prompts bypassed so they don't stall unseen. Ships **false** (security bypass — opt in per machine) |
| `delegate_model` | model for spawned delegates, so a charter written for one model's dispositions doesn't inherit whatever default the human last saved. Blank ⇒ inherit |

A run can **override any of these for itself** via the workpaper's **Standing Postures → Effective config** block. Those overrides survive compaction and are re-asserted on `resume`; `config.yaml` holds the machine baseline, the workpaper holds the run's effective settings. See `operations/checkpoint.md`.

## Requirements
Minimal — run `install` to check. **No language packages** (the bus is Python **standard-library only**), so no `requirements.txt`. The dependencies are system tools:
- **Python 3** — the bus CLI (stdlib only).
- **The agent CLI the substrate spawns with** — named in the substrate's runbook.
- **git** — worktree/branch lanes for build units.
- Whatever else the chosen runbook declares (a terminal multiplexer, a file watcher). Substrate-specific, never assumed.

## Operations
| Operation | File | Use When |
|---|---|---|
| Install | `operations/install.md` | First use on a machine — stand up the bus, resolve the substrate, health-check |
| Bootstrap | `operations/bootstrap.md` | Starting a new orchestrated project — decompose a brief into a unit/wave graph + gates; scaffold plan + workpaper |
| Run | `operations/run.md` | Driving an orchestration — dispatch ready units over the bus, spawn delegates, re-verify, journal |
| Checkpoint | `operations/checkpoint.md` | **Before the human compacts** — distill this run's standing postures, promote any proven general (with OK), pin the rest to the workpaper so they survive |
| Condense | `operations/condense.md` | The live workpaper has outgrown a single read, or closed-work history outweighs live state — archive history **verbatim** to a dated sibling, keep the live file resume-sized |
| Resume | `operations/resume.md` | Re-entering after a **compaction** or cold re-entry — re-assert standing postures + re-ground into the role + re-derive live state (trust nothing) before handing off to `run` |
| Recover | `operations/recover.md` | After a **restart took every session down** — relaunch each persistent session, each of which then `resume`s. Run from *outside* the dead orchestration |
| Delegate | `operations/delegate.md` | You are a delegated working session — pick up your assignment from the bus, execute one unit, report back |

## Reference
| Reference | File | Precondition |
|---|---|---|
| Human-in-the-loop | `reference/human-in-the-loop.md` | Before any work — the crossroads + reversibility contract (when to pause, when to proceed) |
| Method | `reference/method.md` | Before any orchestrate work — the model, the prompt contract, verify-don't-trust, the loop |
| Session bus | `reference/session-bus.md` | When sending/receiving over the bus — verbs, directed inboxes, pointers, cross-verify |
| Substrate | `reference/substrate.md` | At activation — the acts a run needs, how the substrate is selected, which runbook answers them |
| Substrate runbooks | `reference/substrates/<name>.md` | Whenever you spawn, address, watch, wake or retire a session — the mechanism for **this** machine |
| Git lanes + locks | `reference/git.md` | When planning worktree lanes or serializing a commitment (merge/deploy/publish) across concurrent orchestrations |
| Decision briefs | `reference/decision-briefs.md` | When escalating a complex/meaty/UI crossroads — render it visually (the agent judges the look) instead of as text |

## Scripts
Invoke by full path from the skill directory: `scripts/<name>` (`--help` where applicable). Set `SESSION_BUS_DIR` on every invocation — shell state does not persist between an agent's tool calls.

Substrate-independent:
- `bus` — the message-bus CLI (`init`/`send`/`inbox`/`read`/`log`/`handles`/`pane`/`whois`/`wake`/`rotate`/`gc`/`lock`/`unlock`/`locks`).
- `spawn <handle> [instruction]` — start a delegate on the configured substrate; JSON on stdout. It refuses, naming the reason, when that substrate's mechanism is absent here.
- `spawn --check` — the activation preflight: resolves every setting and its origin, detects which substrates this host can run, proves the selected one is among them. Spawns nothing.

Substrate-specific, and **each one reaches nobody on a substrate its runbook doesn't claim it for**:
- `desktop-wake <handle>` — emit one line per new bus message for a harness's background-watch primitive; `--check` first, and again later to prove the watcher is still up.
- `bus-watch.sh [run|status|stop|restart]` — poke a registered pane on new mail. Needs a multiplexer and a file watcher.
- `notify <title> <msg>` — desktop alert plus terminal bell, for a machine the human is sitting at.
- `present <file.html>` — open a decision brief in a local browser.
