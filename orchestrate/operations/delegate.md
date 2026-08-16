# Delegate — execute one unit as a working session

You are a fresh, **human-reachable** session (a visible pane) the human or orchestrator launched. You hold the heavy context the orchestrator deliberately doesn't. Do your one unit well, then report verifiably.

> **`bus` = `~/.claude/skills/orchestrate/scripts/bus`** — the CLI is not on PATH, and shell state doesn't persist between Bash calls (so an alias/export won't stick). Use that full path in every `bus …` command below.

## Intent
Execute exactly one delegated unit per its prompt, honoring the contract, and report a structured summary the orchestrator can re-verify.

## Steps
1. **Register.** `bus init <your-handle> --pane "$TMUX_PANE" --role worker` (your handle is named in your launch prompt).
2. **Pick up your assignment.** `bus inbox <your-handle>` → it carries a `--ref` pointer; read `<bus_dir>/prompts/<unit>.md` for the full prompt.
3. **Load context first.** Read every doc/skill the prompt names, plus `reference/human-in-the-loop.md`. **Report what you loaded** before working — a context-blind session silently violates conventions.
4. **Work the unit — within your lane only.** Honor the contract:
   - Reversible, planned, intended big change → **establish the rollback path FIRST** (checkpoint commit / DB snapshot to `/tmp` / record the rev), then proceed. Don't ask permission for backed-up, planned work.
   - **Design crossroads** or **human-only / irreversible** → `scripts/notify` + **pause** with the options + your recommendation; post to the human's inbox; wait. Never guess a decision in.
   - **Creating or updating a skill or runtime doc?** Search the target tree for its authoring guidelines first (an AUTHORING/CONTRIBUTING/style doc, the skill's own philosophy/patterns references, release runbooks, the last shipped change's precedent) and **conform** — cite the conformance in your report. If no written guideline exists, report that absence as a finding, don't invent a private convention.
5. **Self-verify your gate.** Produce the evidence the prompt asks for (command output, IDs, a test result) — not an assertion.
6. **Report over the bus** — to **the orchestrator handle that sent your assignment** (named in your prompt; *not* a bare `orchestrator`): `bus send <your-handle> <orchestrator-handle> "<unit>: <result>" "<structured summary>"`. For anything large, write it to `<bus_dir>/prompts/<unit>-report.md` and send a `--ref`. Include: what loaded · what shipped (lane/branch/IDs) · the gate evidence · anomalies · the rollback path you established.
7. **Stay reachable.** You're a visible pane — remain parked and available; **don't exit**. Under `close_mode: manual` (default) the human closes your window themselves after reviewing your summary (so a thread can be pulled first); under `auto` the orchestrator closes you once verified. Either way it may re-charter you — stay put until you're closed.

## Don't
- Don't reach the human except via a notify+pause crossroads/human-only escalation.
- Don't touch anything outside your lane.
- Don't do irreversible work without a human go-ahead.
- Don't patch around a wall (a flag, a special case, a shim, a parallel path, a test dodged) to comply with your prompt's literal words — a wall means **stop + report it with your re-derived recommendation**. An honestly-reported blocker is a good outcome; a "working" deliverable built on a workaround is the worst one.
