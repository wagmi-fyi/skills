# Substrate — the acts a run needs, and the runbook that answers them

The method needs a handful of physical acts: start a session, address it, see what it is doing, wake it, reach the human, retire it. **Nothing in the core says how those are performed.** Each is named as an act here and answered by one runbook under `reference/substrates/`, chosen once per run.

| Act | What the core needs of it | Where the core calls for it |
|---|---|---|
| **Spawn a delegate** | a fresh session, human-reachable, running the `delegate` operation | `run.md` |
| **Address a peer** | a name that reaches a live session | `run.md`, `resume.md` |
| **Read the board** | who is working, who is blocked, who finished, who is gone | `run.md`, `resume.md` |
| **Wake a peer** | make a session look at its inbox — to signal it mid-unit, and to carry the wake leg of a report | `run.md`, `delegate.md` |
| **Reach the human** | the live channel an escalation travels on | `human-in-the-loop.md`, `decision-briefs.md` |
| **Retire a delegate** | what happens to a session whose unit closed | `run.md` |

## Waking has a standing rail, and it keeps the seam

`scripts/bus-nudge` is the one act above that runs continuously rather than on
demand. Its core watches buses, resolves who owns a handle, decides live from
dead and composes one fixed sentence, and it holds no knowledge of any harness.
Reaching a session is an adapter under `scripts/bus-nudge-adapters/`, one per
substrate, selected the same way this file selects a runbook. So the seam holds
in code as well as in prose: a substrate whose adapter is absent is refused by
name, and a run learns that instead of watching a rail deliver nothing in
silence.

The program and its adapters ship here, so a machine that installs the rail as a
service takes its copy from this directory. Which bus the rail watches is the
same `shared_bus` the settings seam above already decides, read the same way, so
one answer says where sessions meet and a second one cannot drift from it.

Each runbook's wake section says what its adapter does and what consent means
there.

## The bus is the same on every substrate

Handles, directed inboxes, the cursor, pointers, leases and the `human` ledger are files (`session-bus.md`). They work identically wherever the sessions live, and they outlive every session. **When a substrate signal and the bus disagree about what happened, the bus is the record** — a session's screen, its status field and its process are all evidence about the session, not about the work.

## Selection — detect first, once, at activation

1. **Detect what this host can actually run.** `scripts/spawn --check` proves each substrate's mechanism is present — a multiplexer session for panes, the background-agent capability for the harness — and prints what it found, which settings are in force, and where each came from.
2. **A deliberate declaration overrides detection**: the workpaper's **Standing Postures → Effective config** first, then `config.yaml: substrate`, then the settings seam below. `--check` refuses when the declared substrate's mechanism is absent.
3. **Record the result in the workpaper** before the first spawn.

A charter is written for a substrate whether or not its author noticed: it tells the delegate how to register, where the human will find it, and how to report. A run that discovers its substrate mid-flight has already sent the wrong one.

## A substrate mismatch is a wall

A declared substrate the host cannot run is not an obstacle to work around. The run stops and reports it. Pointing a spawn at somebody else's session, passing the missing mechanism in by flag, or recording a deviation and proceeding are the compliance patch `method.md` forbids.

**The harness's in-process subagent tool is not a substrate.** It is not human-reachable and never appears in the session listing, so no unit that mutates state or produces a deliverable runs there, whatever the substrate situation is. Read-only research may (`method.md`). Where nothing can spawn, `spawn_mode: manual` is the way through: the human opens the session and it becomes addressable once it lists. Reachability is read from the listing, never asserted.

## The settings seam

Every setting resolves in one order: **the environment, then the machine conf, then the shipped `config.yaml`.** The environment names each setting as its `config.yaml` key uppercased and prefixed — `ORCHESTRATE_SUBSTRATE`, `ORCHESTRATE_DELEGATE_MODEL` — and the conf file uses those same names, one `KEY="value"` per line. `$ORCHESTRATE_CONF` names the conf file; `scripts/spawn --check` reports its path and whether it was found.

The seam exists because the shipped file is the layer a local ruling **cannot** be written into: under a managed install the skill folder is root-owned, and an edit there is either refused or overwritten by the next update. A ruling written where the skill cannot read it is silently lost, and a delegate spawned on an unread `delegate_model` runs a different model than its charter was written for.

## Runbooks

| Substrate | File | Use when |
|---|---|---|
| `claude` | `reference/substrates/claude.md` | delegates are background agents; the human reaches them through the harness's agent view |
| `tmux` | `reference/substrates/tmux.md` | sessions are terminal panes on one machine. **Requires the orchestrator itself to be running inside tmux** — a session that is not cannot open a pane, and no flag substitutes |
| `codex` | `reference/substrates/codex.md` | unwritten. The stub says what writing it requires |

## What a runbook has to answer

The six acts, plus three questions that decide whether a run is safe on it:

- **How does the orchestrator learn that a delegate blocked?** A delegate parked on a question it cannot ask is the failure this method is most exposed to, because it looks exactly like a delegate that finished.
- **What stands a wake path while nobody is watching?** The mechanism that serves an attended run is often a person, and it stops when they walk away. Name the recurring one, and name what kills it.
- **What fails silently here?** Name every path that reports success while reaching nobody. A tool that exits 0 into the void is worse than an absent one, and the run has to know which ones those are before its first escalation.

A runbook missing any of the three is incomplete, and a run on it carries that gap in its workpaper.
