# Knowledge Capture

## What to Capture

Recurring patterns, business rules, vendor behaviors, and client preferences that inform future processing. Focus on the nature of transactions and reasoning behind categorization — not mechanical outcomes.

**Good:** "Client uses accrual above gross margin, cash basis for OpEx" — decision-making context.
**Bad:** "TechCorp → Account 4010" — no reasoning. "Processed 15 transactions" — execution metadata.

## What NOT to Capture

- One-time events (stay in workpapers only)
- Account codes without reasoning context
- Session timestamps, debugging steps, script commands, batch IDs
- Information already in rules, config, or existing content files

## Filter Criteria

ALL three must be true:

1. **Would change future behavior** — alters how the agent handles the same situation next time
2. **Not already captured** — not duplicating existing rules, config, or content files
3. **Expressible concisely** — 1-3 sentences without losing key detail

When in doubt: "Should this become a permanent pattern or stay in the workpaper?"

## File Placement

**Client-specific content must never be written to core skill files** (`{module_root}/`). Core files are shared across all clients — client rules, quality gates, account mappings, and business logic belong in `{local_dir}/content/` (or `{firm_root}/` for firm-wide patterns). If you're about to edit a file under `~/.claude/skills/`, stop and find or create the right local file instead.

Files are managed through the `local-context.md` registry:

1. **Check the registry** for an existing file that fits the knowledge type *and* domain
2. **If a file exists in the same domain** — append to it, following its existing structure
3. **If no file fits** — create a new file in `content/`. Don't force knowledge into an unrelated file just because it exists. Creating a new file is the right call when the knowledge belongs to a different workflow phase or domain.
4. **Write order:** content file first, then registry entry — both in one approval

**Naming convention:** For phase-specific context, use `{phase}-context.md` (e.g., `coding-context.md`, `review-context.md`, `ingest.md`). For domain-specific reference, use `{domain}/context.md` or `{domain}/{topic}.md`. Use true flat naming unless a domain accumulates multiple files.

## Approval Flow

1. Identify candidates during finalize/wrap-up
2. Present each candidate with: target file, action (append/update/create), proposed content, registry entry (if new file), and rationale
3. Wait for response: **[Y]** Approve and write | **[E]** Edit then write | **[S]** Skip
4. Process sequentially — one at a time, never batch

## Design Principles

- **Agent audience** — files are for AI agents, not people. Record non-obvious information only.
- **Token economy** — fewest words possible without corrupting intent. No filler, no redundant headers.
- **Cross-workflow flow** — knowledge flows across domain boundaries. Client Questions feedback can update coding, trade accounts, or any other domain's files.
- **Plain language** — in client-facing content, translate jargon to plain language. "Monthly software subscription" not "SaaS recurring debit expense allocation."

## When to Capture

- After completing a domain or work session — reflect on what was learned
- After client feedback — identify patterns that should inform future processing
- During onboarding — capture insights after each domain, building the knowledge base from scratch
- Never silently — always propose and wait for approval
