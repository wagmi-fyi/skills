# Install — stand up the substrate

Make this machine ready to orchestrate. **Idempotent** — safe to re-run.

## Intent
The bus exists and passes a health-check, the machine's substrate is identified, and the acts that substrate's runbook names are available. Report what the machine has, so `bootstrap`/`run`/`delegate` are working from fact.

## Steps
1. **Bus.** Resolve `bus_dir` (config; default `~/.claude/session-bus`). Run `scripts/bus handles` — if it runs, the bus is live (the CLI creates its dirs on first use).
2. **Python 3.** The bus needs `python3` (stdlib only): `python3 --version`. Windows uses the automatic portable-lock shim, so nothing to do.
3. **Identify the substrate.** Read `reference/substrate.md`, pick the runbook that matches this machine, and confirm it against the machine rather than against `config.yaml`: the sessions this skill will spawn are the ones the human has to reach, so check what actually exists here. Record the answer and write it into the run's workpaper at bootstrap.
4. **Verify that substrate's declared dependencies.** Each runbook names what it needs and what it does with them. Check every one and report the version or the absence. **An absent dependency changes the run, not just the setup**: it usually forces `spawn_mode: manual` or removes the wake, and both belong in the workpaper before the first unit.
5. **git.** `command -v git` — worktree/branch lanes for build units.
6. **Confirm the reach to the human.** Establish which channel actually arrives on this machine, per the runbook's "what fails silently here" section, and record it. A run whose escalation path is a line of stdout nobody reads has no escalation path.
7. **Shell convenience** (optional, ask first — never edit a shell rc unprompted): an alias for the bus CLI. It helps the human, not an agent, whose shell state resets between tool calls.

## Health-check (report evidence)
```
bus send _selftest _selftest ping ok && bus inbox _selftest   # shows 1 msg
bus inbox _selftest                                            # shows none (cursor works)
```
Report: bus ✅ + python3 version + the substrate you identified with the evidence for it + each runbook dependency present or absent + git → the available `spawn_mode` and wake. (The `_selftest` files under `bus_dir` are throwaway; remove if you like.)
