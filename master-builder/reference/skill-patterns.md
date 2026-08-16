# Skill Patterns

What makes a good skill and the conventions for building one. Selection — which capabilities a skill needs — is `reference/skill-primitives.md`; this file is how each is built.

## What a Skill Is

A skill is a self-contained capability package: the knowledge, procedures, and tools an agent needs to handle a coherent domain of work. Everything ships inside its directory.

A skill supplies knowledge, not control flow. It doesn't script the agent's steps, encode retries, or enforce a sequence — the agent reasons across the contents and decides how.

## Scoping

**One skill = one coherent domain.** Operations that share context, domain knowledge, scripts, or a database belong together. Operations with none of that in common are separate skills.

Bias toward fewer skills with many operations over many skills with few. Operations in one skill share domain knowledge and stay discoverable together; a crowded roster clouds context and the agent reaches for the wrong tool.

## Structure

### Minimal (single file)

```
{skill-name}/
└── SKILL.md              # Description, activation, inline guidance
```

For skills that are primarily a framework or a set of instructions.

### Standard (registry pattern)

```
{skill-name}/
├── SKILL.md              # Entry point: activation + the two registries
├── operations/           # Intent-driven operation files (loaded on demand)
├── reference/            # Domain knowledge and conventions (loaded on demand)
├── scripts/              # Atomic scripts, JSON stdout (if needed)
├── templates/            # Workpaper and output templates (if needed)
└── assets/               # Static assets (if needed)
```

SKILL.md carries two registries: **operations** (intents → files, with "Use When" triggers) and **reference** (knowledge → precondition triggers). Both load on demand, keeping context clean.

## SKILL.md Conventions

Frontmatter is required — `name` plus a `description` written like a search-query answer with concrete trigger phrases; the description is how the harness decides when to suggest the skill. Then: an activation sequence that orients the agent, and the two registry tables.

## Operations Conventions

Operations are intent-driven — what to accomplish, the conventions that apply, how collaborative vs. autonomous execution differs. They don't enforce rigid step sequences.

Where done is objectively checkable — totals tie, tests pass, output validates — the operation states the check, and the agent loops until it passes. Where done is a judgment, the operation names the checkpoint. A check bounds the loop; it doesn't replace the human's gate on irreversible steps.

## Scripts Conventions

- Atomic — one script, one job; the agent orchestrates across scripts
- Structured JSON to stdout; progress to stderr
- Fail loudly; no swallowed exceptions, no retry logic — the agent decides recovery
- Check `--help` before invocation

## Dependencies

A skill's needs beyond its own directory are declared, not discovered by failure:

- **Script libraries** — declared inside the script where its ecosystem supports it (PEP 723 metadata for Python, for example) and resolved at invocation. The script carries its own requirements; there is no separate list to drift.
- **Everything else** — CLIs, MCP servers, system tools: a short Dependencies section in SKILL.md naming each, why it's needed, and how to verify it's present.

Activation verifies declared dependencies before first use and guides installation.

## Where Skills Live Locally

- **Workspace skills** (the default for workflow skills): inside the workspace's skill directory, traveling with the work they operate on
- **Global skills**: in the machine-wide skill directory, available from any folder — for skills used across projects

Placement mechanics, harness detection, and the symlink rule are in `reference/runtime-conventions.md`. State — workpapers and databases — lives at the workspace root, outside the skill directory.

## Multi-Tenant Skills

When one skill serves several clients with shared logic but different conventions, use the three-tier architecture:

```
Core (the skill itself)            → operations, reference, scripts, templates — shared
    ↓
Firm/Org (optional, shared dir)    → organization defaults: config, policies, shared adapters
    ↓
Local (per-client)                 → _local-{skill}/ in that client's workspace:
                                     config, content + context registry, client adapters
```

**Resolution order: Local → Firm → Core → error.** First match wins at every level — config values, adapters, context entries. Explicit and traceable; no implicit merging.

Each client engagement gets its own workspace carrying its `_local-{skill}/` tier. The local tier's content files are the client's domain knowledge — what the agent learns about the client is written there, not into the core skill. Client credentials follow `reference/runtime-conventions.md` — environment-resolved, never files in any tier.

Solo, single-client skills skip this pattern entirely.

## Setup

A skill requiring setup — database creation, credentials, an initial import — designs that path into itself: auto-initialize on activation for simple cases, a dedicated onboarding operation for complex ones.

## Self-Containment

Self-contained in knowledge: domain knowledge ships as reference files, procedures as operations — no reads outside the skill directory except the workspace-root state locations (_workpapers/, database/, the local tier), defined at activation. Anything else the skill needs — libraries, tools, credentials — is declared per Dependencies, never assumed present.
