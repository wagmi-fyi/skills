# Elicit

Understand a workflow well enough to design the smallest skill that works, then propose it as a build brief.

## Intent

Most of the workflow is tacit. Surface enough to design well, not to transcribe every detail. Output: a one-screen build brief, not a specification.

## Two Ways In

**After a run** — the run is most of the elicitation. Mine it for the steps, inputs, exactness points, and judgment calls that occurred; ask only about what it didn't show — cadence, variations, failure history, boundaries.

**From scratch** — no run exists. The interview carries the design, and state and infrastructure deserve forethought.

## Interviewing

Small batches, plain language, their vocabulary. What usually matters: what kicks the workflow off and what exists when it's done; where the inputs live; the intent of each stage; where results must be exact (script territory) and where a human decides (checkpoint territory); what has gone wrong before. Judge what this workflow needs asked — the list above is orientation, not a script.

Share your confidence as you go. Stop at ~95%, or when answers stop changing the design.

## Focusing Principle: Gall's Law

Complex systems that work grow out of simple systems that work. From everything you learned, carve out the **smallest core that works** — one coherent slice, end-to-end, on real input. Everything else goes on the deferred list, in writing.

## Deliverable

A build brief the human approves before anything is written:

- Skill name and one-line purpose
- The core slice (what v0 does, start to finish)
- Definition of done — the objective check that says the slice worked, where one exists
- First operation(s) — usually one
- Where state lives (workpapers, database, or none yet)
- Deferred list — everything real but not yet earned

Hand the approved brief to `operations/build-skill.md`.
