# Gather Checklist — {{client_name}}

Recurring data-source registry for period closes. One row per steady-state source.
Instantiate per period: copy to `{workpapers_dir}/period-close/{periodLabel}/gather-checklist.md`,
resolve `{periodStart}`/`{periodEnd}` placeholders, track item statuses there.

## Data Sources

### Ingest (Bank / Card / Processor)

| # | Source | Account | Format | Provider / Where | Notes |
|---|--------|---------|--------|------------------|-------|

### Trade Accounts (Revenue Channels)

| # | Source | Channel | Format | Provider / Where | Notes |
|---|--------|---------|--------|------------------|-------|

### Manual Journals

| # | Source | Type | Format | Provider / Where | Notes |
|---|--------|------|--------|------------------|-------|

<!-- Add sections as the client requires (Intercompany, Payroll, Inventory, …). -->

Per source, capture what a fresh session needs to pull it without rediscovery: provider location or
URL pattern (with `{periodStart}`/`{periodEnd}` placeholders), export steps, file naming, pull
timing (e.g. "after settlement closes"), cross-period behavior, and ready-to-paste prompts for
sources requested from humans or other agents. Sources that apply only to specific periods (e.g.
year-end valuations) get their own subsection noting applicability — per-period instances include
them only when the period qualifies.

## Per-Period Usage

1. Instantiate into the period directory; mark each item `pending → received → ingested` (or `n/a` + reason)
2. Store received files in `{workpapers_dir}/period-close/{periodLabel}/source-docs/` — descriptive names, source + date range
3. Missing data blocks the consuming domain — an explicit, documented decision; never fabricate or estimate
4. Note sources that straddle period boundaries (e.g. settlements closing in the next period) and their handling
5. Keep this registry current — new, dead, or changed sources update here, not just in the period instance
