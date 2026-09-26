# Scripts

Every script sits in this skill's `scripts/` directory. Call it by full path. Set `SESSION_BUS_DIR` on every call, because shell state does not carry between an agent's tool calls. Where a script answers `--help`, read that before the first call.

## On every substrate

- `bus`: the message-bus CLI. `session-bus.md` explains its verbs.
- `spawn <handle> [instruction]`: starts a delegate on the configured substrate and prints JSON. The instruction it hands the delegate carries the two rules that break under friction. It refuses, and names the reason, when the substrate's mechanism is absent here.
- `spawn --check`: the activation check. It resolves every setting and its origin, finds the substrates this host can run, and confirms the selected one is among them. It spawns nothing.
- `bus-nudge --check|--once|--watch|--law`: the standing wake rail. It watches the buses on the machine from outside every session. It tells a live session it has unread mail, in one fixed sentence. Delivery is an adapter under `bus-nudge-adapters/`, one per substrate. `--law` proves the sentence carries nothing else, and the program will not run when that proof fails.
- `session-guard [--pid <pid>]`: prints `live` or `superseded` for the session it runs in, and exits 0 or 1. Superseded means a newer process carries the same session. `resume` and every beat of `run` call it first. Where the harness keeps no session records, it answers `live`.
- `check-current.py`: says in one line whether this copy of the skill is current. The activation paragraph calls it.

## On the substrates whose runbook names them

Each of these reaches nobody on a substrate whose runbook does not claim it.

- `session-sweep --check|--dry-run`: ends an older process of a session once a newer one has run for a grace period and holds its socket. It runs beside `bus-nudge` as a machine service on its own timer.
- `desktop-wake <handle>`: prints one line per new bus message, for a harness that can watch a background command. Run `--check` first, and again later to prove the watcher is still up.
- `notify <title> <msg>`: a desktop alert and a terminal bell, for a machine the human sits at.
- `present <file.html>`: opens a decision brief in a local browser.
