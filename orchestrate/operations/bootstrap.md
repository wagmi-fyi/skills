# Bootstrap — brief → orchestrated project (plan + workpaper)

Turn a project brief into a runnable orchestration: a decomposed unit/wave graph with gates, plus the live workpaper. **Guided + human-collaborative** — decomposition is crossroads-rich, so the human is in the loop here by design.

## Intent
Produce `<output_dir>/<project-slug>/{plan.md, workpaper.md}` — a dependency graph of units (each with a lane + a gate), waves, the serialization point, and a cold-boot-able workpaper — ready for `run`.

## 0. Resolve the output location (FIRST — never hardcode)
- **A project that came from `operations/launch.md` already has a directory.** That is the output location, and the convention file already settled it. Do not ask again.
- Use `--out <dir>` if given; else `config.yaml: output_dir`; else **establish it**: detect the repo's convention (does it already keep generated docs/output somewhere? a `docs/` dir? else propose `./orchestrations/`), propose, and **confirm with the human** (a small "where do artifacts live" crossroads).
- `<project-slug>` = a short kebab name from the brief; confirm.
- **Record** the resolved `output_dir` in the workpaper front-matter; offer to persist it to `config.yaml`.

## 1. Elicit intent to ~95% confidence (BEFORE decomposing)
The discipline that makes everything downstream cheap. **North-star prompt: *"elicit until you're 95% confident you understand the user's intent."*** One prompt is usually enough to make you surface the requisite detail and unpack the goal. Read the brief if one exists (a file), or elicit it from scratch; then **restate back** the goal, the definition of done, and the hard constraints, and **ask follow-ups until the picture is ~95% clear**. Don't decompose against a half-understood goal — a wrong spec is expensive downstream. Load `reference/human-in-the-loop.md` + `reference/method.md`.

## 2. Decompose (collaborative — pause only on real crossroads)
- Break the work into **units** — each a coherent, independently-delegable chunk with one **gate** (how you'll verify it, with evidence) and a **lane** (disjoint writes: worktree/branch, tables, deploy slot).
- Order them into a **dependency graph** + **waves** (what runs in parallel). Name the **serialization point** — the single commitment step (deploy/publish/system-of-record write) that runs one-at-a-time.
- Flag **human-only** units (logins/secrets/sign-offs) to fire early.
- Where a cut is a genuine design crossroads (a boundary that could go two ways, a contract two units share), **pause + bring the human in** (`human-in-the-loop.md`). Routine cuts: just make them.

## 3. Write the artifacts (from templates)
> **Composing with a domain skill?** If a host skill already owns the workpaper (e.g. `/bookkeeping`'s period-close workpaper), **defer** — use it as the live surface; write `plan.md` only if it adds run-specific structure the host doesn't encode. Don't duplicate the workpaper.
- `plan.md` from `templates/plan.md` — the durable graph + gates + lanes + serialization point + **project-specific method notes** (domain specifics — your infra/deploy/tooling conventions, etc. — live HERE, not in the skill).
- `workpaper.md` from `templates/workpaper.md` — STATUS box, the work board (one row per unit), open human-only asks, an empty journal. Front-matter records `output_dir`, `project`, and the `bus` handles. **Record the substrate and the live reach to the human here** (`reference/substrate.md`): every later operation reads them from the workpaper, and a charter written before they are settled is written for a machine nobody checked.
- `action-items.md` beside it — the human's queue, scaffolded empty. The convention lives in `human-in-the-loop.md`; it is the ONLY surface for asks of the human.

## 4. Hand off to `run`
Confirm the graph with the human, fire early human-only asks, then proceed to `operations/run.md`.

## Gate
`plan.md` + `workpaper.md` exist and are coherent: every unit has a gate + a disjoint lane; the graph is acyclic; the serialization point is named; human-only units are flagged. Report the path + the wave structure.
