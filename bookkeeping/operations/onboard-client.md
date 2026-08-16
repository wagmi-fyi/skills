# Operation: Onboard Client

First-time client setup — from zero to operational. This operation walks a new client through discovery, infrastructure build-out, and initial period processing until the system is ready for autonomous recurring closes.

## Workpaper

Create onboarding workpaper from template at `{workpapers_dir}/onboarding/onboarding.md` using `templates/onboarding-workpaper.md`. If a workpaper already exists with progress, resume from where it left off — never recreate or overwrite.

## State Artifacts

These files are **live state** — not one-time outputs. Update them as work progresses, not just when they're first created:

| File | Contains | Updated When |
|------|----------|-------------|
| `{workpapers_dir}/onboarding/onboarding.md` | Phase progress, discovered facts, decisions, next steps | Every session |
| `{local_dir}/content/onboarding/gather-checklist.md` | What's acquired, pending, blocked | Items change status |
| `{local_dir}/content/onboarding/build-steps.md` | What's built, what's remaining | Infrastructure changes |
| `{local_dir}/content/company-overview.md` | Client facts, data sources, contacts | New facts discovered |
| `{local_dir}/content/period-close/gather-checklist.md` | Recurring period-close data-source registry — steady-state sources, formats, providers, cadences | Created at Handoff |

## Session Close Protocol

**Before ending any session**, update all state artifacts so the next session can resume without re-discovering context:

1. **Workpaper** — Update current phase, action items, roadblocks, and append a dated note summarizing what was accomplished. The workpaper must contain enough detail for a fresh session to resume without re-reading source files or re-discovering facts. Include: file paths, account codes, adapter usage patterns, credentials locations, and any decisions made with their rationale.
2. **Gather checklist** — Reflect current status of every item (done/pending/blocked with notes).
3. **Build steps** — Reflect current status of every item (done/pending/blocked with notes).
4. **Company overview** — Incorporate any newly discovered facts (entity name, contacts, data sources, vendors, business patterns).
5. **Content files** — If insights were learned during the session (coding patterns, vendor identification, reconciliation quirks), write them to the appropriate content file — don't defer to "next time."

**The test:** Could a fresh agent, reading only the state artifacts, resume work without asking the user to repeat anything? If not, the session close is incomplete.

---

## Phases

Phases are sequential with real dependencies — each phase produces artifacts the next phase consumes. Within each phase, reason dynamically about what this specific client needs. The phase descriptions below express intent, not procedure.

### Phase: Plan — Understand the client

**Intent:** Build a deep, genuine understanding of who this client is, how their business works, and what the bookkeeping engagement requires. This is not a questionnaire — it is a conversation.

**Firm context:** If `{firm_root}` is set and firm context exists, load it before starting the conversation. Firm context provides informed defaults — use them as starting points, not assumptions. Reference firm standards naturally: "Your firm typically uses accrual basis — will this client follow that, or do they need something different?" Let firm context reduce discovery time without constraining it.

**Mindset:** Be genuinely curious. Pull on open strings. Ask follow-up questions. Probe for things the user might not think to mention. Walk through topics conversationally — one or two questions at a time, thinking about each response before going deeper.

**Areas to explore:**
- Client identity and engagement scope — what do they do, who are they, what does this engagement cover
- System of record setup — which SoR (QBO, Xero, etc.), current state, access, existing data. If firm has a default SoR, reference it.
- Chart of accounts strategy — sync from SoR, import from firm template, import a starter template, or build from scratch. If firm has COA templates (check firm `content_manifest`), offer firm template as an option.
- Data source configuration — every system that feeds financial data (banks, revenue platforms, payroll, inventory, other)
- Manual journal needs — depreciation, accruals, reclassifications, recurring adjustments
- Historical baseline and catch-up scope — conversion balances, how many periods to catch up, where clean data starts
- Multi-entity, multi-brand, or dimensional complexity — classes, departments, locations, cost centers, consolidation
- Seasonal patterns, growth trajectory, compliance considerations
- Key contacts — who handles what, communication preferences, decision-making authority
- Tool and integration readiness — developer capability, API access, credential management

Do not treat this as a fixed list. Some clients need deep exploration of multi-entity complexity; others need extensive data-source mapping. Follow what matters for this client.

**Produces:**
- Company overview at `{local_dir}/content/company-overview.md` (from `templates/company-overview.md`)
- Gather list — everything that needs to be acquired (credentials, files, access, historical records)
- Build list — everything that needs to be constructed (config, database, adapters, content scaffolding)
- Processing scope — which periods to process and in what order

Present these outputs for user confirmation before moving forward.

**Before leaving Plan:** Consider running an adversarial review on the plan itself. What did we miss? What assumptions are we making? What will bite us in Build or Process? Reference `reference/adversarial-review.md`.

---

### Phase: Gather — Acquire everything needed

**Intent:** Systematically work through the gather list — credentials, data files, system access, historical records, conversion balances. Track what has been acquired, what is pending, and what is blocked.

**Mindset:** Patient and collaborative. Gathering takes time and often requires back-and-forth. Help the user figure out where things are, who to ask, and what format is needed. Use web research and sub-agents when the user is unsure about SoR capabilities, export formats, or API availability.

**Key behaviors:**
- Walk through items methodically, one at a time
- For pending items: offer to wait, proceed without (if non-blocking), or skip (with confirmation)
- Identify what can move forward even if some items are blocked
- Check for local gather checklist at `{local_dir}/content/onboarding/gather-checklist.md` and incorporate if present
- **Keep the gather checklist current** — update status and notes as items are acquired, blocked, or resolved. This is a live artifact, not a snapshot.
- Record provider mechanics as you learn them (export paths, formats, URL patterns, cadences) — they seed the recurring period-close gather checklist built at Handoff.

---

### Phase: Build — Construct the infrastructure

**Intent:** Set up the technical infrastructure that makes the processing pipeline possible. This includes configuration, database, adapters, chart of accounts, content directories, and local knowledge scaffolding.

**Key areas:**
- **Config** — Populate `config.yaml` from planning decisions. If firm context exists, pre-populate firm defaults (SoR, period_type, coding thresholds) — the user only needs to confirm or override. Present changes in table format (current vs. new) and confirm before writing. Reference `templates/config-template.yaml` for structure.
- **Database** — Initialize `bookkeeping.db` with staging layer schema. If database exists, confirm reset or keep. Never overwrite without permission.
- **Adapters** — For each data source identified in Plan: assess developer capability, then either build adapters collaboratively or produce a developer handoff document. Reference `reference/adapter-patterns.md`.
- **Chart of accounts** — Execute the COA strategy determined in Plan (sync from SoR, import from firm template, import starter, or build from scratch).
- **Conversion balances** — If applicable, enter and validate opening balances. Debits must equal credits.
- **Local context** — Create `{local_dir}/content/local-context.md` from `templates/local-context-template.md`. Populate only with reference files that have substance from planning data (e.g., coding policies, trade account registries). Create each referenced file as a flat file in `content/`. Do not pre-scaffold empty placeholders — files are created organically during processing as knowledge accumulates.
- **Firm starter content** — If `{firm_root}/templates/` exists, check for firm starter content (review checklists, coding policy templates, etc.) and copy to client's local content directories as starting points. These are customizable — the client may diverge from firm defaults.
- **Company overview — firm team** — If firm `content_manifest` includes a team roster, auto-populate the "Internal (Your Firm)" section of `company-overview.md` with team members assigned to this client (by `client_id` or wildcard). Present the auto-populated section for confirmation.
- **Firm credentials** — If `{firm_root}/adapters/.env` exists, copy firm app-level credentials (e.g., QBO client_id/secret) into the client's `{local_dir}/adapters/.env`. Client-specific tokens (access_token, refresh_token, realm_id) are added separately.

**Keep the build steps current** — update `{local_dir}/content/onboarding/build-steps.md` as items are completed, including file paths and usage notes for what was built.

**References:** `templates/config-template.yaml`, `templates/company-overview.md`, `templates/local-context-template.md`, `reference/adapter-patterns.md`

---

### Phase: Process — Run the pipeline

**Intent:** Process historical and/or current data through the full bookkeeping pipeline. This is learning mode — slower, more questions, deliberately building the knowledge base that will power future autonomous closes.

**Approach:**
- Link to `operations/process-period.md` for each period
- For multi-period catch-up: process the first period thoroughly to build coding rules, adapter patterns, and content knowledge. Apply accumulated learnings to subsequent periods — each should go faster than the last.
- The pipeline processes in a fixed sequence: Ingest, Trade Accounts, Apply Payments, Coding, Manual Journals, Review, Client Questions, Publish
- After each sub-workflow completes, capture insights — surprises, quirks, patterns, anything worth remembering. Tag insights with their target content file when obvious.
- Re-read workpaper state before every status display to catch changes from other sessions
- Track sub-workflow status. All must pass before proceeding.

**Learning mode behaviors:**
- Provide first-time context explaining what each sub-workflow does
- Ask more questions than you would in a recurring close
- Surface judgment calls rather than auto-resolving them
- Build coding rules incrementally — capture the rationale, not just the mapping

---

### Phase: Verify — Confirm end-to-end

**Intent:** Safety check confirming the system is ready for autonomous operation. All processed periods should have quality evidence. Every sub-workflow should show passed status.

**Key checks:**
- All sub-workflows passed for every processed period
- Quality gates have evidence in workpapers
- Adapters are tested and reliable
- COA matches the system of record
- Conversion balances are accurate (if applicable)
- Local context registry (`local-context.md`) exists with entries only for files that have substance

If anything is incomplete or failed, route back to the appropriate phase rather than forcing forward.

---

### Phase: Handoff — Transition to operational

**Intent:** Seal the onboarding engagement and transition the client to recurring operations.

**Key activities:**
- **Deliver insights** — Extract all insights captured during Process phase. Insights are registered in `local-context.md` as they're captured — verify completeness and accuracy of registry entries and their referenced files.
- **Build the period gather checklist** — Distill the recurring data-gathering routine into `{local_dir}/content/period-close/gather-checklist.md` from `templates/gather-checklist.md`. Inputs: the onboarding gather checklist, adapters built, company-overview data sources, and cadences/quirks learned during Process. Include only steady-state recurring sources — onboarding-only sources (historical statements, personal accounts) stay out. Per source: format, provider/location, pull timing, period-placeholder URLs or ready-to-paste prompts where applicable. Register in `local-context.md` with precondition "Before starting a period close."
- **Seed the review baseline** — Onboarding manufactures exactly the artifacts the recurring Review domain must know how to read: conversion-era trade accounts, legacy representations predating later migrations, clearing-account flows, FX remodel residue, structural contra accounts. Before sealing, distill these into `{local_dir}/content/review-notes.md`: known-exception suppressions each with a signature and a verification query (a count/total a future session can re-run to confirm the set is frozen), clearing-account expectations, per-channel aging-basis caveats for Check 2, and expected-open conventions at period end. Register in `local-context.md` with precondition "Before running review." A review that runs without this file re-litigates every onboarding decision from scratch — or worse, flags settled history as findings.
- **Review self-accumulated content** — Some sub-workflows write directly to content files during execution. Review these for completeness and accuracy.
- **Handoff verification** — Confirm: config correct, database initialized, adapters configured and tested, COA matches SoR, conversion balances accurate, first close complete with gates passed, insights delivered, self-accumulated content reviewed, local context registry reflects actual client knowledge, period gather checklist built and registered, client ready for recurring closes. Check for local handoff checklist and incorporate.
- **Seal workpaper** — Mark onboarding as complete only after all verification items are confirmed.
- **Orient to operations** — The client is now ready for recurring closes via `operations/process-period.md` and `operations/close-period.md`. Provide clear guidance on what comes next.

**Tone:** This is a milestone. The client is transitioning from "setting up" to "operational." Be warm about it.

---

## References

- `templates/company-overview.md` — client overview template populated during Plan
- `templates/config-template.yaml` — configuration structure and defaults
- `templates/local-context-template.md` — context manifest template
- `templates/onboarding-workpaper.md` — workpaper template
- `reference/adapter-patterns.md` — adapter development patterns and conventions
- `reference/knowledge-capture.md` — how to capture and store learned knowledge
- `reference/adversarial-review.md` — structured challenge process for plans and outputs

## Quality Mindset

Onboarding is learning mode. Every interaction is an opportunity to capture knowledge that makes future closes faster and more accurate. Prefer more questions over fewer. Prefer explicit confirmation over assumptions. Prefer capturing a nuance now over rediscovering it later. The goal is not just a working system — it is a system that knows this client deeply enough to operate with minimal guidance going forward.
