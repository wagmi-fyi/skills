---
name: master-builder
license: Apache-2.0
description: "Build and improve agent skills on the machine the agent runs on. Use when creating a new skill, recording a workflow as a skill, interviewing a human about a workflow to design a skill, improving or hardening an existing skill, or setting up a skills workspace."
metadata:
  version: 0.1.0
---

# Master Builder

Build skills where the agent runs: interview the human, scaffold the smallest skill that works, grow it through real runs.

> Harness-specific mechanics live in `reference/runtime-conventions.md`; everything else uses durable names and works on any agentic harness.

## Activation

When this skill is invoked:

1. Determine the way in — capture a workflow that just ran in this session (the common case for smaller workflows), design a bigger workflow from scratch, improve an existing skill, or set up a workspace
2. If the workspace is unestablished, run `operations/setup-workspace.md` first
3. Load the matching operation
4. Write all documentation per `reference/documentation-principles.md`

## Operations

Load on demand — only when performing that operation.

| Operation | File | Use When |
|-----------|------|----------|
| Elicit | `operations/elicit.md` | A workflow needs to become a skill — just performed in this session, or designed from scratch. Understand it to ~95% confidence, then propose the smallest skill that works |
| Build Skill | `operations/build-skill.md` | Creating a new skill from a settled design, or making structural changes to an existing one |
| Improve Skill | `operations/improve-skill.md` | After a real run, after feedback, or at session wrap-up — harden the skill and capture what was learned |
| Setup Workspace | `operations/setup-workspace.md` | Establishing or verifying a local workspace: skill placement, _workpapers/, database/, harness conventions |

## Reference

Load on demand — when the work needs that knowledge.

| Reference | File | Precondition |
|-----------|------|-------------|
| Documentation Principles | `reference/documentation-principles.md` | When writing any skill content — SKILL.md, operations, references |
| Skill Primitives | `reference/skill-primitives.md` | At design time — the palette a skill composes from; re-scanned during improvement |
| Skill Patterns | `reference/skill-patterns.md` | Before building or modifying a skill — structure, registries, scoping, scripts |
| Database Patterns | `reference/database-patterns.md` | Before designing a database — when to use one, schema conventions, location |
| Runtime Conventions | `reference/runtime-conventions.md` | Before placing a skill or workspace on this machine — discovery paths, symlinks, root conventions, secrets |
