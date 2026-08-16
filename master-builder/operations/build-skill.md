# Build Skill

Build a new skill or make structural changes to an existing one.

## Intent

Produce a self-contained, discoverable skill: a clear SKILL.md entry point, intent-driven operations, on-demand reference files, and atomic scripts where needed. The skill works on first invocation; everything it needs is inside its directory or declared.

## Building a Skill

### 1. Define the Domain and Description

What coherent domain does this skill cover? What problem does it solve? The description determines when the harness suggests the skill — write it like a search query answer with concrete trigger phrases.

Decide: single-file skill (instructions or a decision framework only) or registry-pattern skill (operations + references)?

**Deliverable:** Skill name and description. Structure decision (minimal vs. standard).

### 2. Map the Primitives

Scan `reference/skill-primitives.md` against the brief: which primitives does the core slice need now, and which are deliberately deferred? Record the deferrals. If a database made the cut, design its schema now — it shapes every operation and script that follows.

**Deliverable:** Primitive selections and deferrals. Schema, if a database is in.

### 3. Design the Registry

For registry-pattern skills: what operations does the agent need? What reference knowledge supports them?

**Operations:** Map intents to files. Each operation is a coherent unit of work with a "Use When" trigger.

**References:** Map knowledge to preconditions — loaded only when the agent needs it.

**Deliverable:** Operations and reference registry tables for SKILL.md.

### 4. Build the Files

Creation order:

1. Directory structure (placement per `reference/runtime-conventions.md`)
2. SKILL.md — entry point, registries, activation
3. Operation files — intent, conventions, modes
4. Reference files — domain knowledge, patterns
5. Scripts, if needed — atomic, JSON stdout, fail loudly
6. Templates, if needed — workpaper and output scaffolding

Each file follows `reference/documentation-principles.md`.

### 5. Trim

Before declaring done, re-read the build against the brief as a skeptic: what did you build that the core slice didn't need? Remove it or move it to the deferred list. Agents (and people) over-build; the smallest skill that works is the deliverable.

### 6. Test

- SKILL.md frontmatter is valid (name + description)
- Invoke the skill fresh — does activation orient the agent correctly?
- Try the trigger phrases — does the description match how the human would ask?
- If scripts exist: run each with `--help`, verify the JSON output contract
- Run the core slice once on **real input**, with the human watching the result

## Execution

Establish the rollback path before a multi-file build — a git commit, or a copy of the directory. A trade-off the brief doesn't settle is the human's call; credentials, external sends, and deletes always get the human. Name what the skill must not do and bake those limits into its operations.

Close every build by running `operations/improve-skill.md` once against the first real run.
