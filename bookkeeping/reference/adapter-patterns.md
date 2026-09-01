# Adapter Patterns Reference

Patterns and conventions for adapters and scripts in Bookkeeping. This reference is loaded during adapter-spec (Step 2) and adapter-dev workflows to guide implementation toward agent-first code.

---

## The Agent-First Philosophy

**Core principle:** a script does one atomic thing. Everything around it is the calling agent's job.

| The script's job | The agent's job |
|--------------|--------------|
| Execute one operation | Orchestrate multiple operations |
| Return result or error | Decide what to do with results |
| Fail loud on problems | Handle errors, decide recovery |
| Output structured JSON | Parse and act on output |

Scripts are **pure functions with side effects**: they take input, do one thing, and produce structured output. The agent sequences them, reads the errors, and makes the judgment calls.

---

## Code Placement

Every piece of code lives in one of three layers. Decide placement BEFORE writing:

### Layer 1: Core (`scripts/`)

Code that ships with the module and is replaceable on update.

- **Pure function scripts** — input → process → output
- **Shared by all clients** using this module
- Examples: parse a bank CSV, calculate journal entries, validate a chart of accounts

### Layer 2: Adapters (`adapters/`, `{firm_root}/adapters/`, or `{local_dir}/adapters/`)

Code that transforms input/output for a specific context.

- **Core adapters** (`adapters/`) — published with core, reusable across all firms and clients (e.g., Chase CSV format, QBO API)
- **Firm adapters** (`{firm_root}/adapters/`) — shared across a firm's clients, preserved on core update (e.g., firm-wide integrations, firm QBO app credentials)
- **Local adapters** (`{local_dir}/adapters/`) — client-specific, preserved on update
- Adapters **wrap core scripts** by transforming data before/after the core function
- Example: A Chase CSV adapter normalizes bank-specific columns before passing to the core ingest script

**Resolution order:** `{local_dir}/adapters/` → `{firm_root}/adapters/` → `adapters/` → error if none exists. If `{firm_root}` is not set, the firm layer is skipped.

### Layer 3: Local Overrides (`{local_dir}/overrides/`)

Client-specific step or workflow overrides. Preserved on update. Rarely code — usually step files or content.

### Decision Guide

| Question | If Yes → |
|----------|----------|
| Does this work the same for all clients at all firms? | Core script (`scripts/`) |
| Does this handle a format/system used across all firms? | Core adapter (`adapters/`) |
| Is this shared across a firm's clients but not universal? | Firm adapter (`{firm_root}/adapters/`) |
| Is this unique to one client? | Local adapter (`{local_dir}/adapters/`) |
| Does this override a workflow step? | Local override (`{local_dir}/overrides/`) |

---

## Script Conventions

### Directory and Naming

```
scripts/
├── verb_noun.py          # snake_case, verb-first
├── parse_bank_csv.py     # Atomic: one operation
├── calculate_totals.py   # Pure function: input → output
└── sync_to_qbo.py        # Side effects are fine, but one per script
```

### The `gotchas.md` Convention

Any adapter directory may carry a `gotchas.md` — field-earned quirks of the external system the
adapter talks to (read-only API fields, validation traps, post-then-fail error classes, human-relay
protocols). Operations that drive the adapter load it as a precondition: the Publish operation reads
the resolved publish adapter's `gotchas.md` before every publish, whichever resolution layer the
adapter came from (for a flat single-file adapter, `gotchas.md` sits alongside it). Scope rule:
**system-generic knowledge only** — client-specific state belongs in that client's local content,
never in a shared adapter. When work surfaces a new quirk, append it where the next driver of this
adapter will find it: here.

### Output Contract

All scripts output **structured JSON to stdout**:

```json
{
  "summary": "What was accomplished (human-readable)",
  "data": { "results": "..." },
  "next_steps": ["Suggested follow-up action"]
}
```

Error output:

```json
{
  "status": "error",
  "message": "Human-readable error description",
  "suggestion": "Optional: what to try next"
}
```

Progress output goes to **stderr** (keeps stdout clean for JSON):

```python
print(f"Processing {i}/{total}...", file=sys.stderr)
```

### Basic Script Template

```python
#!/usr/bin/env python3
"""Brief description of what this script does."""
import argparse
import json
import sys

def main():
    parser = argparse.ArgumentParser(description='What this script does')
    parser.add_argument('--input', required=True, help='Description')
    args = parser.parse_args()

    # Your logic here
    result = process(args.input)

    print(json.dumps({
        "summary": f"Processed {args.input}",
        "data": result,
        "next_steps": ["Suggested follow-up"]
    }, indent=2))

if __name__ == '__main__':
    main()
```

### Config Loading Pattern

Scripts find their config via the `BOOKKEEPING_CONFIG_PATH` environment variable. The AI agent prepends this to every Python invocation.

**Core scripts** (inside `scripts/`):

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '_shared'))
import config_loader

config = config_loader.load_config()
db_path = config_loader.get_db_path()
```

**Local adapters** (inside `{local_dir}/adapters/`):

```python
import os, sys, yaml

# Bootstrap: find module_root via BOOKKEEPING_CONFIG_PATH, add scripts to path
_config_path = os.environ.get('BOOKKEEPING_CONFIG_PATH')
if not _config_path:
    raise RuntimeError("Set BOOKKEEPING_CONFIG_PATH to your project's _local-bookkeeping/config.yaml")
with open(os.path.expanduser(_config_path)) as _f:
    _MODULE_ROOT = os.path.expanduser(yaml.safe_load(_f)['module_root'])
sys.path.insert(0, os.path.join(_MODULE_ROOT, 'scripts', '_shared'))
sys.path.insert(0, os.path.join(_MODULE_ROOT, 'scripts'))

import config_loader
```

### Database Access Pattern

This module uses a domain-specific SQLite database:

```python
from _shared import config_loader

config = config_loader.load_config()
db_path = config_loader.get_db_path()
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
```

- Schema source of truth: `reference/module/schema.sql`
- Database is a **staging layer** — SQLite is the processing workspace, external SoR (QBO, Xero, etc.) is the final destination
- Data flows: Source → SQLite → Process → Publish to SoR

### Environment and Credentials

```python
import os
from dotenv import load_dotenv

config = config_loader.load_config()
env_path = os.path.join(config['local_dir'], 'adapters', '.env')
load_dotenv(env_path)

API_KEY = os.getenv('API_KEY')
if not API_KEY:
    print(json.dumps({"status": "error", "message": "API_KEY not set"}))
    sys.exit(1)
```

Secrets live in `{local_dir}/adapters/.env` (gitignored). Fail fast if missing.

**Firm-level credentials:** When a firm shares app-level credentials across clients (e.g., QBO OAuth app client_id/secret), these live in `{firm_root}/adapters/.env`. During client onboarding, firm app credentials are copied into the client's `.env` so scripts only need to read one file. The firm `.env` remains the source of truth — if app credentials rotate, update the firm file and re-copy to active clients.

---

## Anti-Patterns

### 1. Monolithic Workflow Scripts

**Don't** combine multiple steps in one script. When step 4 fails, the caller cannot tell which step failed.

```
Bad:  create_invoice_and_send.py (gather → calculate → create → push → send → log)
Good: Separate scripts, the agent sequences them
```

### 2. Built-In Retry Logic

**Don't** add retry/backoff/sleep in scripts. A script reports the failure and exits. Whether to retry, how long to wait, and whether to take a different route are decisions for the caller, which holds context the script does not.

### 3. Input Validation Overkill

**Don't** extensively validate arguments. An agent calls these scripts, not a person typing, so a bad argument comes back as an error the caller reads and corrects. Let the external API validate what it owns; QBO validates account codes, for one.

### 4. Wrapping Everything in Try/Except

**Don't** swallow errors. A caught-and-hidden error becomes a silent wrong answer. Let it surface, and put enough in the message to say what happened.

**When try/except IS appropriate:**
- Loop boundaries (catch per-item so the loop continues)
- Setup validation (fail fast with clear message)
- Known error states (specific expected errors)

### 5. Over-Engineering for Edge Cases

**Don't** special-case empty inputs, large batches, priority queues. Write the simple version and leave the shape of the work to the caller.

**The test:** does handling this error need context from outside the script? If it does, let it bubble up.

---

## Module-Specific Considerations

### Quality Gates

Every workflow must define pass/fail criteria for its output. When speccing an adapter, ask:

- **Does this adapter's output need a quality gate?** (e.g., "local balance must match statement balance")
- **Is the quality gate a check within the script** (validation logic) **or a separate verification step** (a follow-up script, or a check the agent performs)?
- Quality gates are **mandatory at the workflow level**, encouraged at the step level.

### Workpapers

Workpapers are state artifacts that persist context across sessions. When speccing an adapter, ask:

- **Does this adapter produce data that should be recorded in a workpaper?** (e.g., flagged transactions, quality gate results)
- **Does this adapter consume workpaper state?** (e.g., resuming from where a previous session left off)
- Workpaper location: `{output_folder}/workpapers/`

### Database Schema Impact

If the adapter touches the database:

- **Does it need new tables or columns?** Update `reference/module/schema.sql`
- **Is the database staging or SoR?** This module uses staging — SQLite is workspace, external SoR is destination
- Schema is self-contained, applied independently

### Content Slot Pattern

If the adapter produces output that should be customizable per client:

```markdown
**Local content check:** Read `{local_dir}/content/{content-name}.md` if it exists.
If it does not exist, use this default:

> Default content here.
```

---

## Script vs Markdown Decision

| Use Script When | Use Markdown When |
|----------------|-------------------|
| External APIs, database ops | Reference docs, decision frameworks |
| File operations with side effects | Step-by-step instructions the agent follows |
| Credential requirements | Informational knowledge |
| Structured JSON output needed | |

**The test:** does this *do* something with side effects, or *inform* a decision the agent makes?

---

## The System-of-Record Seam

The SoR is the external ledger staging publishes into. Which one is in play is **declared** in config as `default_system_of_record` — never detected from the environment, never inferred from which adapter happens to be installed. That value is also the `{sor}` token the publish-adapter resolution uses. An empty declaration defaults to QBO and says so; `config_loader.get_sor()` applies this for scripts.

### The contract is the staging schema, not a bundle

An SoR adapter reads and writes the same SQLite staging tables every other part of the module uses. There is **no handoff bundle** — no export file, no intermediate payload, no serialized batch passed between the core and the adapter. The seam is the schema:

- **`sync` (JSON)** on `journal_entries`, `trade_accounts`, `trade_account_payments` — `{status, external_id, error, last_synced_at}`. `status` is the state machine: `pending` → `synced`, or `error`, or `verify`. `verify` means the object's posted state is *unknown*; no re-publish selects it, so a lost race cannot become a double-post. See `adapters/qbo/_shared/sync_status.py`.
- **`remote_id`** on the reference tables (chart of accounts, contacts, classes) — the SoR's identifier for a record that exists on both sides.

Because the contract is table state, an adapter is resumable by construction and two adapters cannot disagree about what "published" means.

### An SoR adapter is bidirectional

Publishing is half of it. The adapter also reads the SoR back, and the close depends on those reads:

- **Out:** the publish entry point maps staging records to SoR objects and records their external IDs.
- **In, reference data:** the `sync_*` entry points pull the chart of accounts, classes, and contacts so staging codes resolve to real SoR identifiers.
- **In, verification:** an as-of trial-balance tie and a direct-record scan bracket the period. The SoR is not a write-only mirror — it holds records staging never created (entered directly, system-generated, or a prior sync that failed to land). Both brackets are Hard Stops in `reference/quality-guidelines.md`.

An adapter that only publishes is not an SoR adapter. It cannot close a period.

### Transaction ingest is not the SoR's job

Bank and processor transactions enter through **feed adapters** (the flat modules in `adapters/`), not through the SoR adapter — even when the SoR could serve some of that data. The two classes have different contracts: a feed adapter writes `imports` rows deduplicated on `(source, external_id)` and never publishes; an SoR adapter never ingests source transactions. Keeping the split means the SoR can change without touching how data arrives.

### Landing a second SoR

The seam is already SoR-agnostic; a second one (Xero, say) is an adapter directory, not a refactor. What it touches:

- A new adapter directory under `adapters/{sor}/` supplying the same entry points — publish, reference sync, as-of reconcile, direct-record scan — plus its own `gotchas.md`.
- `default_system_of_record` in the client's config, which resolves the publish adapter through the six-layer order in `operations/process-period.md`.
- Its own dependency block in `requirements.txt`, adapter-tier like the QBO block, and its tests guarded on that SDK so a deployment without it still runs a green core suite.

What it does **not** touch: the staging schema, the sync-status vocabulary, the core scripts, or the feed adapters. If landing an SoR requires changing those, the seam has been breached and the design is wrong — stop rather than widen the core.

---

## Dual CM/VC Path (Credit Memos and Vendor Credits)

The QBO publishers handle Credit Memos and Vendor Credits via two coexisting paths:

1. **Standalone path** — first-class TAs of `type='credit_memo'` or `'vendor_credit'`, created via `scripts/create_credit_memo.py` / `create_vendor_credit.py` and applied to invoices/bills via `scripts/apply_credit.py`. Published by `_publishers/credit_memos.py`, `vendor_credits.py`, and `credit_applications.py`. Applications publish as zero-amount `Payment` / `BillPayment` objects with two `LinkedTxn` entries (Pattern B). Use this path for any standalone-credit case: customer returns, vendor refunds/rebates, promotional credits, damage-claim resolutions.

2. **Inline-adjustment path** — chargebacks/damages detected automatically from clearing-JE adjustment postings during `apply_payments_bulk`. Published inline by `_publishers/payments.py` and `bill_payments.py` (synthesizes a CM/VC during payment publish; no local TA). This path is fire-and-forget — nothing is tracked locally.

The two paths coexist because the inline path is empirically validated and has no operational need to migrate. Don't consolidate them without a regression test that snapshots GL balances before and after on a real client DB.
