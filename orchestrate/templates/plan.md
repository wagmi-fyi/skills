# {{PROJECT}} — Orchestration Plan (durable design)

> The WHAT + HOW of this orchestration. Changes only when a design decision changes. Live state is `workpaper.md`. Method = the `orchestrate` skill (`reference/method.md`).

## Goal & definition of done
{{one-paragraph goal}}
**Done when:** {{the verifiable end-state}}

## Hard constraints / standing rules
- {{e.g. no tenant names in platform code; never print secrets; domain rules}}
- **Reversibility conventions:** {{branch/snapshot/rev — how big changes are made undo-able in this project}}

## Units
| Unit | Type (build/deploy/research/human) | Lane (disjoint writes) | Gate (orchestrator re-verifies read-only) | Dep |
|---|---|---|---|---|
| {{U0}} | research | — | {{evidence}} | — |
| {{U1}} | build | {{worktree/branch + artifact tag}} | {{compile/test + diff re-read}} | U0 |
| {{…}} | | | | |

## Waves (parallelism)
- **Wave A:** {{U0}} ∥ fire {{human-only asks}}
- **Wave B:** {{U1 ∥ U2 ∥ …}} (parallel lanes)
- **Wave C:** {{commitment}} (serial)

## Serialization point (commitment)
{{the single one-at-a-time step — the deploy / publish / system-of-record write}}

## Human-only units (fire early — turnaround is the critical path)
- {{logins / secrets / OAuth / sign-offs}}

## Project-specific method notes
{{Domain specifics the general method doesn't carry — e.g. your infra/deploy/tooling conventions and project-specific rules. These live HERE, not in the skill, so the skill stays general.}}
