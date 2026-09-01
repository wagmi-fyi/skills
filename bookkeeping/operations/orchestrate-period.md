# Orchestrate Period

Run a period close as a verifying orchestrator that delegates the domain work
as self-contained prompts to fresh sessions, then independently validates every
result before trusting it. This is `process-period` run in a parallel,
multi-session mode — the domain logic is unchanged; what changes is who does the
work and how it's checked.

## Substrate — the orchestrate skill if it is available

This operation predates the **orchestrate** skill, which automates the relay it
describes. Before running, read the **`orchestrate`** config key (firm-level
`{firm_root}/config.yaml`; a client `config.yaml` may override):

- **`true`, or unset/`auto` while the orchestrate skill is available to you** →
  run this close on orchestrate: bootstrap it with this close as the brief, then
  `run`. It supplies the substrate: a message **bus** in place of a human relay,
  **spawned delegate sessions**, a **wake** path, and the
  **crossroads/reversibility contract**. Everything below is unchanged and becomes
  the method orchestrate executes, including the dependency graph, the per-domain
  gates, verify-don't-trust, and the workpaper. The human-relay role dissolves, and
  the human gates only design crossroads and human-only steps.
- **`false`, or orchestrate is not available** → run the **human-relay** model below.

Whether the skill is available is something the harness already tells you. Set
`orchestrate: false` where it is not installed, so this is not re-checked every
period.

## Intent

Compress a close's wall-clock while keeping one mind accountable for
correctness. The orchestrator never does the bulk work itself: it decomposes the
close into domain-sized units, hands each to a fresh session as a prompt, and
re-derives the result against the source of truth before accepting it. A human
relays prompts and results between the orchestrator and the working sessions and
is the conduit for anything only a person can do.

Use this when a close is large enough that serial single-session work is slow and
a human is available to run sessions in parallel and relay between them. For a
small close, run `process-period` directly — delegation overhead isn't worth it.

## The shape of it

- **Orchestrator** — owns the dependency graph, authors prompts, verifies every
  output, holds the workpaper as the coordination surface, and is the only thing
  that decides what is "done." Stays lean: reads summaries and re-runs checks,
  never the raw work.
- **Working sessions** — fresh sessions that each execute one unit from a prompt
  and report back. They have none of the orchestrator's context; the prompt must
  carry everything.
- **Human relay** — moves prompts and results between orchestrator and sessions,
  and performs the steps only a person can (logins, external communication, the
  irreversible go-aheads).

The orchestrator drives, the sessions do, the human carries messages and makes
the calls that are theirs.

## Principles

- **Verify, don't trust.** A session's reported numbers are a hypothesis.
  Re-derive every load-bearing figure yourself — from the database, a script, or
  the source document — using the per-domain gates `process-period` already
  defines. Nothing is accepted on a summary's say-so.
- **One conduit, one verifier.** Sessions can't reach the human and don't decide
  correctness — the orchestrator does both. Everything a session surfaces flows
  through it.
- **Respect the graph and the gates.** Delegation changes who works, not the
  rules. The `process-period` dependency graph, its per-domain quality gates, the
  hard stops, and the `close-period` seal gates all still bind. A dependent
  domain does not start before its upstream is verified.
- **Parallelize cognition, serialize commitment.** Reading and reasoning — the
  expensive part — parallelizes freely across sessions. Commitment means the
  **system of record**: publishing stays serialized and verified, and dependent
  domains wait for their upstream. The shared staging database is NOT a
  serialization point — concurrent sessions in disjoint write lanes are by
  design; lock contention there is a recoverable error (wait briefly, re-invoke;
  verify state before retrying any ambiguous mutation), not a constraint. Run
  independent domains at once; make dependent ones wait. Don't collapse the
  fan-out to one-writer-at-a-time out of caution — that forfeits the operation's
  wall-clock value.
- **Adapt the fan-out to the work.** Delegate the heavy, independent, many-unit
  domains; handle trivial or tightly-serial units inline rather than pay
  delegation overhead. Judge per domain as the real volume reveals itself.

## Authoring delegated prompts

A prompt is a contract with a context-less session. Write each so a fresh session
can execute its unit correctly and report verifiably. Principles, not a template:

- **Load context first — point the delegate at the registry; do NOT pre-resolve
  the file list yourself.** The session ran no skill activation. Open every prompt
  by having the delegate run the `/bookkeeping` activation *itself*: read
  `config.yaml`, `content/company-overview.md`, **and
  `{local_dir}/content/local-context.md` — then resolve and read the registry
  entries whose precondition matches its domain** (plus the core references for
  its mode of work), and report what it loaded. **Hand-pre-resolving a curated
  file list of your own is the failure mode:** it (a) skips registry-driven
  discovery of **local overrides** (a `_local-bookkeeping/operations/` or
  `content/` file that shadows a core one) and (b) injects path-resolution errors —
  an ambiguous `../operations/foo.md` copied out of the registry resolves against
  the *core skill*, where it's absent, so the delegate wrongly reports it "missing"
  and silently falls back to a generic path. When you must name a specific file,
  use a **project-root-absolute path**. A context-blind — or wrongly-pathed —
  session silently violates conventions. *(Earned CPL P07: a review delegate
  mis-resolved `../operations/aged-receivables.md` to the core skill, never loaded
  the local registry, missed the local by-invoice adapter, and shipped the wrong
  aged artifact until a human caught it.)*
- **Bound the work.** State the exact scope — period, domain, inputs with paths,
  what is already done, what to deliberately leave for another domain. Don't rely
  on the session to infer boundaries.
- **Name the gate.** Tell the session how to verify its own output and to report
  the evidence, not an assertion.
- **Constrain writes.** Assign each session an explicit, disjoint write lane —
  which imports, which tables, which scripts — and name its sibling sessions and
  their lanes. Include the contention etiquette: on "database is locked," wait a
  few seconds and re-invoke; after an ambiguous error on a mutating script,
  verify actual state with a read query before retrying — never blind-retry a
  write. For read-only work, say so.
- **Define the report.** Ask for a structured summary the orchestrator can
  re-verify against — counts, ties, artifacts produced, anomalies — not a
  narrative.

## Blockers and questions

Working sessions never block waiting on a human and never guess a figure into the
books — they record what they hit and return. The orchestrator triages:

- **Resolve from data** whatever a query, the chart of accounts, history, or a
  reference answers — silently, and proceed.
- **Re-charter or do it inline** when a session stalled for lack of scope, or its
  output fails verification.
- **Route to the right human, batched** for genuine judgment calls and
  client/external facts — deduped, plain language, one consolidated ask per
  recipient.
- **Halt at a hard stop.** A balance mismatch or a publish error is a gate, not a
  question: reproduce it, then stop with the defined menu.

Fire the human-dependent requests (external documents, client questions,
confirmations) as early as possible — their turnaround is usually the real
critical path, and they resolve in parallel with the machine work.

## Sequencing and the workpaper

Drive order from the `process-period` dependency graph: fan out the domains that
are independent once their inputs exist; hold dependent ones until their upstream
is verified. The workpaper is the shared, durable coordination surface — update
it progressively as each domain is verified, so the close survives session
boundaries and the orchestrator stays lean. When every domain is terminal and
verified, hand off to `close-period` for the seal.

## References

- `operations/process-period.md` — the domains, dependency graph, per-domain
  gates, and hard stops this operation delegates and verifies
- `operations/close-period.md` — the seal gates that terminate the close
- `reference/subagent-patterns.md` — the writer-not-reporter contract and
  file-artifact handoff for delegated work
- `reference/quality-guidelines.md` — anti-fabrication, hard stops, the
  verification bar
- `reference/bookkeeping-principles.md` — the constraints that bind regardless of
  execution mode
