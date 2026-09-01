---
name: bookkeeping
license: Apache-2.0
description: "AI-native bookkeeping: ingest, categorize, reconcile, and publish financial data with intent-based operations and principles-based quality. Use when processing bank transactions, categorizing expenses, managing trade accounts, running period closes, or onboarding new bookkeeping clients."
---

# Bookkeeping

AI-native bookkeeping with dynamic, intent-based operations. The agent reasons across all tools and context to get the work done — no rigid workflows.

---

## Activation

When this skill is invoked:

1. **Load config** from `{local_dir}/config.yaml`
   - Resolve via `BOOKKEEPING_CONFIG_PATH` environment variable, or default to `{project-root}/_local-bookkeeping/config.yaml`
   - **Resolve all path placeholders to absolute paths before using them.** Config values reference each other in a chain — resolve fully:
     1. `{project-root}` → the actual working directory
     2. `{local_dir}` → resolve `{project-root}` placeholder
     3. `{module_root}` → resolve to absolute path
     4. `{output_folder}` → resolve `{project-root}` placeholder
     5. `{workpapers_dir}` → resolve `{output_folder}` placeholder (this is often NOT under `{local_dir}`)
     6. `{database_dir}` → resolve `{project-root}` placeholder
   - **Use only resolved absolute paths for all file operations. Never assume or hardcode directory locations.**
   - Store resolved values: `{user_name}`, `{client_name}`, `{module_root}`, `{local_dir}`, `{output_folder}`, `{workpapers_dir}`, `{database_dir}`, `{database_name}`, `{firm_root}`, `{firm_id}`
   - **Read the system-of-record binding** from `default_system_of_record` — declared in config, never detected. It names the SoR and is the `{sor}` token for publish-adapter resolution. Empty means QBO, and the default is announced rather than silent. `config_loader.get_sor()` applies this for scripts.
   - If no config exists: suggest onboarding — "No client config found. Let's set one up."

2. **Load firm context** (if `{firm_root}` is set and directory exists)
   - Load firm config from `{firm_root}/config.yaml`
   - Merge firm defaults into session context — client config wins on conflicts
   - Store `{firm_name}`, `{firm_id}`, and `content_manifest` from firm config
   - Load firm context registry from `{firm_root}/firm-context.md` if it exists — firm registry entries serve as fallbacks when no matching local entry exists
   - Note available firm content for reference during work
   - If `firm_root` is empty or directory missing: skip — firm layer is optional

3. **Load company overview** from `{local_dir}/content/company-overview.md` (if exists)
   - This is baseline context — understand the client's business before doing anything

4. **Load local context registry** from `{local_dir}/content/local-context.md` (if exists)
   - This registry maps client-specific reference files to precondition triggers
   - Files marked with a precondition must be loaded before performing that action for the first time in a session
   - If missing, skip silently — registry is optional

5. **Check for active workpaper** at `{workpapers_dir}/`
   - Look for the most recent period-close or onboarding workpaper
   - Parse frontmatter: `action_items`, `roadblocks`, `domains_status`
   - Determine current state

6. **Present state and ask what to work on:**
   - If workpaper exists: "Here's where things stand for {client_name}: [state summary]. What would you like to work on?"
   - If company overview exists but no workpaper: "Ready to work on {client_name}. What period or task?"
   - If firm context loaded but no client config: "Firm context loaded for {firm_name}. Ready to onboard a client."
   - If no firm or client setup: "No firm or client setup found. Start with firm onboarding or client onboarding."

7. **Prepend `BOOKKEEPING_CONFIG_PATH={local_dir}/config.yaml`** to every Python script invocation

---

## Operations

Load on demand — only when the agent needs to perform that operation.

| Operation | File | Use When |
|-----------|------|----------|
| Onboard Firm | `operations/onboard-firm.md` | First-time firm setup — practice-wide context and standards |
| Onboard Client | `operations/onboard-client.md` | First-time client setup — from zero to operational |
| Process Period | `operations/process-period.md` | Working on bookkeeping for a period (the main workhorse) |
| Close Period | `operations/close-period.md` | Verifying and sealing a completed period |
| Orchestrate Period | `operations/orchestrate-period.md` | Running a large period close in parallel — delegating domain work as prompts to fresh sessions while you verify each result (over the `/orchestrate` bus if present, else a human relays) |
| Develop Adapter | `operations/develop-adapter.md` | Building or modifying a data adapter |
| Connect Bank Feeds | `operations/connect-bank-feeds.md` | Establishing or repairing live bank-data connections (AMA auth bundle → FC account mapping → smoke-proof) |

---

## Reference

Load on demand — when the agent needs specific guidance during work. **Precondition references** (marked below) must be read before performing the associated action for the first time in a session.

| Reference | File | Contains | Precondition |
|-----------|------|----------|-------------|
| Capability Registry | `reference/capability-registry.md` | All scripts and adapters — purpose, args, output contracts | Before invoking any script |
| Quality Guidelines | `reference/quality-guidelines.md` | Verification principles, hard stops, anti-fabrication rule | Before any coding, verification, or publishing |
| Bookkeeping Principles | `reference/bookkeeping-principles.md` | Core decision-making principles and constraints | Before any coding, verification, or publishing |
| Knowledge Capture | `reference/knowledge-capture.md` | How to update client knowledge files | Before writing to content files |
| Database Schema | `reference/schema.sql` | SQLite staging layer schema | Before querying or modifying the database directly |
| Adapter Patterns | `reference/adapter-patterns.md` | Adapter design conventions and code placement | Before building an adapter |
| Adversarial Review | `reference/adversarial-review.md` | Cynical quality review pattern | Before running an adversarial review |
| Review Checks | `reference/review-checks.md` | Unified review checks and Excel export package | Before running review |
| Subagent Patterns | `reference/subagent-patterns.md` | When and how to delegate work to parallel sub-agents | Before launching any sub-agent |
| PDF Ingestion | `reference/pdf-ingestion.md` | Principles for extracting financial data from PDF statements | Before building a PDF adapter or ingesting PDF data |
| Bank Feeds Troubleshooting | `reference/bank-feeds-troubleshooting.md` | Live-feed failure modes, re-auth/remap procedure, seam playbook | Before diagnosing or repairing a live bank feed |

---

## Architecture

### Three Tiers

```
Core (versioned, updatable)          → ~/.claude/skills/bookkeeping/
  scripts/, adapters/, reference/, templates/, operations/
      ↓
Firm (per-firm, shared by clients)   → ~/.claude/bookkeeping-firm/
  config.yaml, firm-context.md, content/, adapters/, reference/, templates/
      ↓
Local (per-client, preserved)        → {project-root}/_local-bookkeeping/
  config.yaml, content/local-context.md, content/, adapters/
```

Resolution order: `{local_dir}/` → `{firm_root}/` → core → error

The firm layer is optional. Solo deployments (no firm) skip it entirely — resolution falls through from local to core.

### Context Resolution

Domain-relevant context files are discovered via registry. Resolution order: `local-context.md` → `firm-context.md` → skill.md reference section. First match wins. Precondition triggers are evaluated against the current operation.

### Database

SQLite staging layer at `{database_dir}/{database_name}` — NOT system of record.

```
Source (CSV, API) → Ingest → SQLite → Process → Publish → SoR (QBO, Xero)
```

### Workpapers

State persistence across sessions at `{workpapers_dir}/`:
- `period-close/{periodLabel}/{periodLabel}.md` — per-period progress, quality evidence, action items (all period-specific files — source explorers, reconciliation workbooks, logs — live alongside the workpaper in the same subdirectory)
- `onboarding/onboarding.md` — onboarding phases and accumulated learnings

Frontmatter tracks: `domains_status`, `action_items`, `roadblocks`, `status`

### Scripts

Atomic operations with JSON on stdout. The agent sequences them. A script never retries; recovery is the caller's decision. Always check `--help` before invocation, since the capability registry is for discovery and not for invocation syntax.

---

## Environment

`BOOKKEEPING_CONFIG_PATH` must be set to `{project-root}/_local-bookkeeping/config.yaml` when invoking any Python script. The agent prepends this to every command.

---

## Dependencies

**A deliberate deviation.** `master-builder`'s convention is that Python dependencies
are declared inline in each script, as PEP 723 metadata, with no separate list to
drift. This skill keeps a manifest instead. Its QBO adapters put the `qbo` skill's
`scripts` directory on `sys.path` and import from it at runtime, so the two skills
share one environment, and PEP 723's per-script isolation does not serve that. The
manifest also carries the core-against-adapter grouping and the import-name trap
below, neither of which fits in a bare package list. Ruled 2026-08-16. One manifest,
never both.

Python deps are pinned in [`requirements.txt`](requirements.txt), verified on Python 3.12 and 3.14. They are not declared inline, so the invocation carries them. Match whatever the workspace already establishes; with nothing established, `uv` resolves them without a pre-built environment:

```bash
uv run --with-requirements {module_root}/requirements.txt {module_root}/scripts/<script>.py
```

`requirements.txt` is grouped **core vs. adapter**. Pure core (ingest → categorize → reconcile → SQLite staging) needs only `pyyaml`; the system-of-record and feed adapters each add their own. The full file is the safe default. A minimal or non-QBO deployment installs core plus only the adapter blocks it uses.

The QBO block is a **superset tie to the [`qbo`](../qbo/SKILL.md) skill's deps.** The QBO adapters re-export `qbo_client` from the qbo skill at runtime, so `qbo` must be installed and its packages present. `adapters/qbo/_shared/client.py` resolves where qbo is: `QBO_SKILL_SCRIPTS` first, then a qbo skill sitting beside this one, then a global install. Its error names every path it tried.

**Install the package names, not the import names.** The OAuth client imports as `intuitlib` and ships on PyPI as **`intuit-oauth`**. Installing `intuitlib` fetches an unrelated project. `requirements.txt` carries the mapping; install from it rather than by hand.
