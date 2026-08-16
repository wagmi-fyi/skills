# Improve Skill

Harden a skill after real use, and capture what was learned into permanent skill content.

## Intent

Skills earn sophistication through real runs. This operation turns run experience — friction, errors, feedback, repeated explanations — into the smallest set of durable edits.

## Hardening Triggers

Re-scan `reference/skill-primitives.md` against the most recent run(s): each signal that fired names the primitive to add. Two signals live outside the palette:

- An operation reads like keystrokes — rewrite it for intents and outcomes
- A part was never used — trim it; over-building is the default failure

## What to Capture

Non-obvious patterns that change future behavior — decision context, not execution metadata. All three must be true:

1. **Would change future behavior** next time the situation recurs
2. **Not already captured** — search the skill first
3. **Expressible in 1–3 sentences** without losing the point

One-time events stay in workpapers. Information already in the skill stays where it is.

## Approval Flow

Capture is never silent. Present each proposed edit with target file, action (create/append/update), the actual text, and rationale. Wait for approve / edit / skip. One at a time — never batch writes. If a new reference file is created, add its registry row to SKILL.md in the same pass.

## When to Run

- After each real run of a skill, especially the first
- After human feedback or a caught error
- At session wrap-up for any build session

Edits follow `reference/documentation-principles.md`.
