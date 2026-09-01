# Documentation Principles

All skill content — SKILL.md, operations, references, templates — follows these principles.

**Principles over specifications.** Document intent, conventions, and concepts. Do not document specific commands, queries, or values the agent can discover by reading source or inspecting the system.

**No stale statistics.** Do not hardcode values that drift: counts, versions, rosters, paths that move. Omit them or point to where the current value lives.

**Point to locations, not contents.** When the agent needs current state, point to the file, table, or config where it lives. Enumerated contents go stale.

**Start simple.** Document what IS, not what might be. Layer complexity as the skill grows.

**Trust the agent.** Define conventions and boundaries; the agent reasons within them. No step-by-step recipes. The right level of detail: enough to reason from, not enough to copy-paste from. Trust scales with verifiability: where done is objectively checkable, state the check and let the agent loop until it passes; where done is a judgment, name the judge.

**Canonize the present.** State how the skill works now — never prior conventions, migration history, or old-vs-new framing.

**Harness-neutral prose.** A skill may be read under any harness. Write "the harness" rather than naming one; keep paths resolvable from where the reader runs (see `reference/runtime-conventions.md`).

**Domain vocabulary only where the subject is that domain.** A bookkeeping skill talks about books; the builder patterns themselves stay domain-agnostic.

**Resolver-managed invocation.** Any command written for someone else to run invokes scripts through the ecosystem's resolver (`uv run` for Python, for example) — never a bare interpreter or package tool, which may not resolve on the reader's machine. Shebangs are exempt.

**Token economy.** Fewest words without corrupting intent. No filler, no redundant headers, no summaries of what the reader just read.

**Plain register.** Write for the spoken voice. State each idea once, in one short sentence, and let the fact carry the weight. Use the everyday word over the domain term; define a term of art at first use or drop it. Vary sentence length.

Do not use: em dashes; antithesis or corrective negation ("X, not Y"); contrasting pairs; rules of three; parallel sentence structures within a paragraph; setup and payoff constructions; landing sentences; summary beats; negative parallelisms; stacked noun phrases; nominalization; hedging qualifiers; filler intensifiers; performed enthusiasm. A sentence that dramatizes a fact is hiding it.

When this register conflicts with an existing file's voice, the register wins for new prose. A legacy file converges as its sentences are touched. Do not run a dedicated rewrite sweep.
