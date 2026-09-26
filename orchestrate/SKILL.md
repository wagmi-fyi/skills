---
name: orchestrate
license: Apache-2.0
description: "Run a verifying, multi-session orchestration: bootstrap a delegated project (plan + workpaper), dispatch work to human-reachable sessions over a built-in message bus, spawn delegates on whatever substrate the machine runs, and re-verify every result. Use when coordinating several agent sessions on one machine, running an orchestrator plus delegated workers, delegating build/migration/research units while staying lean, or setting up the session-bus. Pauses for design crossroads and human-only steps; runs autonomously otherwise."
metadata:
  version: "fd07a36 2026-09-26"
---


# Orchestrate

One lean orchestrator session splits a project into units. Each unit goes to a fresh working session, a delegate, as a self-contained prompt. The orchestrator re-derives every result before it trusts it. A built-in message bus carries prompts and reports, so the human does not relay them.

## The one rule that shapes everything

Agents bring the human in on two things only. A design crossroads is a trade-off that the intent and the plan do not settle. A human-only step is a login, a secret, an external send, or anything else that cannot be undone. Everything else runs on its own, large changes included, when it rests on intent and a plan and its rollback path exists first. `reference/human-in-the-loop.md` holds the contract.

## Where a rule binds

A rule that depends on being read is not enforced. A session's report of what it loaded is a claim. So a rule the run depends on sits in the boot instruction a session cannot skip, in a script that refuses, or in a gate a unit must pass to close. Prose in a reference file is documentation.

## Where the mechanism lives

The substrate is how this host starts, addresses, watches, wakes and retires sessions. The core names each act and never its mechanism. `reference/substrate.md` routes to one runbook under `reference/substrates/`. Record the substrate in the workpaper before the first spawn.

## Activation

**First, check that this copy is current.** Run `scripts/check-current.py` from this skill's directory and read its one line. If it names a newer version and says `auto`, update this copy the way it was installed and read this file again; under `confirm`, ask the person once; under `pin`, or when the check could not run, or when the copy is not this session's to write, go on with the copy as installed.

1. **Orient.** Pick the operation from the table below.
2. **Resolve the settings.** `reference/substrate.md` gives the order they resolve in. `config.yaml` says what each one does.
3. **Detect the substrate and load its runbook.** `scripts/spawn --check` shows what this host can run and where each setting came from. An absent mechanism is a wall: report it.
4. **Load the contract, then the method:** `reference/human-in-the-loop.md`, `reference/method.md`.
5. **Check the bus.** If `scripts/bus handles` fails, run `operations/install.md`, which also checks what the machine needs.
6. **Load the operation.**

Call scripts by full path from this skill's `scripts/` directory, with `SESSION_BUS_DIR` set on every call. `reference/scripts.md` lists them.

## Operations

| Operation | File | Use when |
|---|---|---|
| Install | `operations/install.md` | First use on a machine |
| Launch | `operations/launch.md` | Filing a new project, before `bootstrap` |
| Bootstrap | `operations/bootstrap.md` | Splitting a brief into units and gates, with a plan and a workpaper |
| Run | `operations/run.md` | Driving an orchestration |
| Checkpoint | `operations/checkpoint.md` | When a turn asks for it, and before a person compacts |
| Condense | `operations/condense.md` | The live workpaper no longer fits one read |
| Resume | `operations/resume.md` | Coming back after a gap: a compaction, a move, or a resume by the machine |
| Recover | `operations/recover.md` | A restart took every session down |
| Delegate | `operations/delegate.md` | You are a delegate |

## Reference

| Reference | File | Read when |
|---|---|---|
| Human in the loop | `reference/human-in-the-loop.md` | Before any work |
| Method | `reference/method.md` | Before any work |
| Session bus | `reference/session-bus.md` | Using the bus |
| Substrate | `reference/substrate.md` | At activation, and to resolve a setting |
| Substrate runbooks | `reference/substrates/<name>.md` | Acting on a session on this machine |
| Scripts | `reference/scripts.md` | Calling a script for the first time in a run |
| Git lanes and locks | `reference/git.md` | Planning worktree lanes, or serializing a merge, deploy or publish |
| Decision briefs | `reference/decision-briefs.md` | A crossroads too large for plain text |
