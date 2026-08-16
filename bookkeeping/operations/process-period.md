# Process Period

## Overview

**Intent:** Process all bookkeeping work for a given period. The agent dynamically decides what needs doing based on workpaper state and available data. Rather than following a rigid step sequence, the agent reads the current state of the period workpaper, identifies which domains are complete, incomplete, or blocked, and advances the work accordingly.

This is the workhorse operation -- it covers the full lifecycle from raw bank data to published entries in the system of record. The agent acts as a bookkeeper who understands the dependency graph, uses judgment to prioritize work, and adapts its interaction style based on what the situation requires.

**Delegated / parallel execution:** For a large close, this operation can be run in a multi-session mode where an orchestrator hands each domain to a fresh session as a prompt and independently verifies every result — see `operations/orchestrate-period.md`.

---

## Baseline Context on Entry

### 1. Load Company Overview

Read `{local_dir}/content/company-overview.md` to understand the business context: industry, entity structure, revenue model, key vendors, accounting method, fiscal year, and any special considerations. This informs categorization judgment, review materiality thresholds, and client question framing throughout the entire operation.

### 2. Load Local Context Registry

Read `{local_dir}/content/local-context.md` if it exists. This registry maps reference files to precondition triggers. When entering a domain, load all registry entries whose precondition matches. If no registry exists, proceed without local context (same as a fresh client with no accumulated knowledge).

### 3. Resolve Period

The period must resolve to a canonical tuple: `{periodLabel, periodStart, periodEnd, periodType}`.

- **Calendar-monthly:** Input `YYYY-MM` resolves to first and last day of month
- **Fiscal:** Input `YYYY-PNN` requires fiscal calendar lookup from `{local_dir}/content/period-close/fiscal-calendar.yaml`
- **Explicit range:** Input `YYYY-MM-DD_YYYY-MM-DD` used directly

Use the `period_resolver` shared library for resolution. Confirm the resolved period with the user before proceeding. All date-range queries throughout the operation use `periodStart` and `periodEnd` as `YYYY-MM-DD` values -- never `YYYY-MM` LIKE patterns.

### 4. Check for Active Workpaper

Look for an existing workpaper at `{workpapers_dir}/period-close/{periodLabel}/{periodLabel}.md`.

- **If found:** Read it. Check `domains_status` frontmatter to understand what has been completed, what is in progress, and what has not started. Present the current state to the user as a status dashboard before deciding what to work on next.
- **If not found:** Create the directory `{workpapers_dir}/period-close/{periodLabel}/` and workpaper from template at `{module_root}/templates/period-close-workpaper.md`. Populate frontmatter with resolved period values, client name, client ID, and creation date. All domain statuses initialize to `not-started`. All period-specific output files (source explorers, reconciliation workbooks, logs) go in this directory alongside the workpaper.

### 5. Load Module Configuration

Load `config.yaml` and resolve all path placeholders. Extract key operational parameters:
- `coding.min_confidence_to_categorize` and `coding.min_confidence_to_auto_approve` (drives the three-zone confidence system)
- `default_system_of_record` (determines publish adapter)
- `workpapers_dir`, `output_folder`, `local_dir`, `module_root`

### 6. Load Firm Context (if available)

If `{firm_root}` is set and exists, note the firm `content_manifest` from firm config. If `{firm_root}/firm-context.md` exists, load it as a fallback registry — firm entries apply when no matching local entry exists. Firm reference files provide fallback guidance when client-specific content doesn't exist yet. This is especially useful for early-stage clients where local content is still sparse.

### 7. Set Environment

Ensure `BOOKKEEPING_CONFIG_PATH` is set to the project's `_local-bookkeeping/config.yaml`. Every script invocation depends on this.

### 8. Reconcile to the SoR (opening bracket)

Before ingesting the period, reconcile the local ledger *through the prior close* against the system of record using the SoR's reconcile adapter (for QBO: `adapters/qbo/reconcile_trial_balance.py --as_of {priorCloseDate}`). This catches anything that diverged since the last seal — a record edited or reclassified directly in the SoR, or a prior-period entry that never synced. A non-zero variance is a **Hard Stop**: resolve it (not merely acknowledge it) before starting Ingest (see `reference/quality-guidelines.md` → Hard Stop 6). Also run the closing direct-record detect at the *front* — a second as-of tie at the period end (`--as_of {periodEnd}`) plus `scan_sor_direct_records.py` surfaces SoR-side in-period activity (a payment processor's auto-posts, direct entries) to **adopt into staging before the lanes duplicate it**. The closing half of this bracket runs before Publish (Hard Stop 7). See the **SoR Reconciliation** domain below for both sub-gates.

---

## Domain Dependency Graph

Domains are NOT a rigid sequence. The agent is free to parallelize independent domains but must respect real data dependencies:

```
SoR Recon (open) <- before Ingest; reconcile local-through-prior-close vs the SoR
Gather           <- (no dependencies)
Ingest           <- requires Gather (item-level — see gating note) + SoR Recon (open) clear
Categorize       <- requires Ingest
Trade Accounts   <- requires Ingest
Apply Payments   <- requires Trade Accounts
Manual Journals  <- requires Ingest
Review           <- requires Categorize + Trade Accounts + Apply Payments + Manual Journals
Client Questions <- requires Review (or can surface categorization flags earlier)
SoR Recon (close)<- before Publish; detect SoR records staging never created
Publish          <- requires all items resolved (Client Questions complete or explicitly deferred) + SoR Recon (close) clear
```

**Gather gating is item-level in interactive mode:** an account or source enters its consuming domain (Ingest, Trade Accounts, Manual Journals) once its checklist items are `received` — Gather may remain in-progress for later-arriving items (e.g. a settlement that closes mid-period). In an orchestrated context, gate at domain level — or delegate per-account over received items.

**Parallelization opportunities:** After Ingest completes, Categorize, Trade Accounts, and Manual Journals can all proceed independently. Apply Payments must wait for Trade Accounts. Review waits for all four processing domains. Client Questions typically follows Review but can surface `processed=2` items from Categorize earlier if beneficial.

**Dependency enforcement:** When working in an orchestrated context, the agent checks domain statuses in the workpaper frontmatter before entering a domain. If a required upstream domain is not `completed` (or `completed-with-flags`), the agent must not start the dependent domain.

**SoR Reconciliation brackets the period (opening + closing).** The system of record is reconciled to staging at *both* ends. Before Ingest, reconcile local-through-prior-close against the SoR to catch drift since the last seal (a record edited or reclassified directly in the SoR, an unpublished prior entry). Before Publish, detect SoR records dated in the period that staging never created — direct entries, system-generated artifacts, or failed syncs — so the publisher never duplicates them; the existing post-publish 0-variance reconcile then proves the commit landed exactly. Both brackets are **Hard Stops that must be resolved, not merely acknowledged** (see `reference/quality-guidelines.md` → Hard Stops 6 and 7) and run via the SoR's reconcile adapters (for QBO: `adapters/qbo/reconcile_trial_balance.py` for the as-of tie at both ends, `adapters/qbo/scan_sor_direct_records.py` for the closing direct-record detect).

---

## Domains

### SoR Reconciliation

**Intent:** Staging and the system of record must agree at *both* ends of the period. The SoR is the source of record, but it can hold transactions staging never created — records entered or edited directly in the SoR, system-generated artifacts (e.g. an SoR auto-applying a credit), or entries from a prior sync that failed to land. Reconcile both ends so none slips through: drift since the last seal is caught before work begins, and direct records are caught before they can be duplicated at Publish. Tracked as the `sor-reconciliation` step with two sub-gates.

**Opening sub-gate (before Ingest).** Reconcile the local ledger through the prior close against the SoR. For QBO, `adapters/qbo/reconcile_trial_balance.py --as_of {priorCloseDate}` performs the RE-rollup-aware as-of tie (balance-sheet/equity cumulatively; P&L per-account; the Retained-Earnings sweep derived as a check). A variance means something changed in the SoR since the seal — a direct edit, a reclass, an unpublished prior entry. **Hard Stop:** resolve the variance before Ingest; a judgment-call or locked-period rectification routes through the user (see `reference/quality-guidelines.md` → Hard Stop 6).

**Opening orphan scan (run the closing direct-record detect at the front, too).** The prior-close tie is blind to activity the SoR accrued *within* the new period — a payment processor auto-posting collections, direct manual entries, a live bank feed, system artifacts — because those postdate the seal. Left undetected until Publish, staging re-books the same events and the close ends in a delete-and-republish dedup: a repeated, avoidable cost. Catch them at the front. Staging holds *no* new-period entries yet, so a second as-of tie at the **period end** isolates exactly this set — `reconcile_trial_balance.py --as_of {periodEnd}`, run now, returns per account precisely the in-period SoR activity staging lacks (the prior-close tie already proved everything before it matches, so every variance here is new SoR-side activity). Read that variance map, enumerate the specific records with `scan_sor_direct_records.py --period_start {periodStart} --period_end {periodEnd}` (the same closing-detect tool), and **adopt each into staging before processing** — mirror the record linked to its SoR Id and tagged, so the downstream lanes reconcile against it instead of creating a duplicate (apply a client-specific resolution playbook where one exists — e.g. a processor's settled-vs-in-transit routing). This is *discovery*, not a zero-variance gate: the variance is expected and large. The closing sub-gate still runs as the backstop for anything the SoR posts mid-close.

**Closing sub-gate (before Publish).** Detect SoR records dated in the period that staging did not create, BEFORE committing this period — so the publisher never duplicates something the SoR already holds. For QBO, `adapters/qbo/scan_sor_direct_records.py --period_start {periodStart} --period_end {periodEnd}` queries each transaction entity over the window and flags every record lacking the publisher's `[bk:<key>]` idempotency tag (stamped in PrivateNote/DocNumber on everything the pipeline creates — see `adapters/qbo/_shared/locate.py`). Read-only: no SoR or local writes. An untagged record is a **Hard Stop:** reconcile it into staging or otherwise resolve it (account for a direct entry; neutralize a colliding auto-applied credit) before Publish — do not publish over it (see `reference/quality-guidelines.md` → Hard Stop 7). After the live publish, the same as-of reconcile (`reconcile_trial_balance.py --as_of {periodEnd}`) proves zero variance — the post-publish tie that already gates the close.

**Quality gate:** Opening — the as-of reconcile returns zero variances, or every variance is explicitly resolved (not waived). Closing — the direct-record scan returns an empty untagged set, or every surfaced record is resolved before Publish. Both sub-gates record evidence (script output, resolved items) to the workpaper.

---

### Gather

**Intent:** Every recurring data source for the period is acquired or explicitly accounted for before the domains that consume it run. A source nobody pulled is the same completeness failure as a file nobody ingested — one level earlier.

**Local context:** Load the gather checklist from the local context registry (precondition "before starting a period close").

**Processing approach:**

1. **Instantiate.** Copy the registry checklist to `{workpapers_dir}/period-close/{periodLabel}/gather-checklist.md` (if already present, resume it — never overwrite), resolving `{periodStart}`/`{periodEnd}` placeholders into concrete URLs and prompts.
2. **Work the items.** Present pull instructions; fire human-dependent and cross-agent asks first — their turnaround is usually the critical path. Track each item `pending → received → ingested` (or `n/a` with reason). Bank-feed accounts (mapped in `{local_dir}/bank_feeds.yaml`) are self-service: the agent triggers the FC refresh itself at gather time (`adapters/stripe_fc_refresh.py --trigger`) and marks the item `received` when the refresh succeeds — no human dependency, no file in `source-docs/` until the pull is saved during Ingest.
3. **Store.** Received files go in `source-docs/` within the period directory — descriptive names, source + date range.
4. **Hand off per item.** As items reach `received`, their consuming domains may begin (see the gating note under the dependency graph). `ingested` is set by the consuming domain's verification, not at download time.

**No registered checklist:** mark the domain `completed` with the note "no gather checklist registered — data availability handled ad hoc," and suggest building one during finalize.

**Quality gate:** every checklist item terminal; zero silently-pending items; every `received`/`ingested` item's file present in `source-docs/` (bank-feed items are the exception: `received` = refresh succeeded; their pull file lands in `source-docs/` during Ingest).

**Domain status determination:** All items received/ingested -> `completed`. Any `n/a`-with-reason -> `completed-with-flags`. Items still pending at session end -> `in-progress` (blocks dependent domains per the gating note).

---

### Ingest

**Intent:** Get all transaction data for the period into the database, verified against source documents. Every configured data source must be accounted for -- either successfully imported or explicitly flagged as unavailable with a reason. A silent skip is a completeness failure.

**Local context:** Load files from `local-context.md` registry where precondition matches this domain. If no registry or no matching entries, proceed without domain-specific context. However, if no account registry is available in an orchestrated context, warn and halt: the agent needs account information to proceed.

**Scripts:**
- `ingest_universal.py` -- bulk-inserts transformed universal JSON into the imports table
- `verify_ingest_balances.py` -- computes `postings_balance + unposted_imports` and compares to statement ending balance

**Local adapters:** `{local_dir}/adapters/ingest/` -- client-specific source adapters (Stripe, Chase CSV, etc.)

**Bank-feed accounts (Stripe FC):** If `{local_dir}/bank_feeds.yaml` exists, accounts mapped there (registry `status: active`) are pulled live instead of from files. Per account:

1. Ensure a fresh refresh: if Gather already triggered one this session (item `received`), skip to step 2. Otherwise `adapters/stripe_fc_refresh.py --account_id {fca} --trigger` (transactions+balance together so the transaction list and balance snapshot are coherent). Re-triggering inside Stripe's refresh window fails -- read mode surfaces `next_refresh_available_at`.
2. Poll read mode until both refreshes are `succeeded` (agent-paced waits; the first transactions refresh can take minutes; `failed` is an adapter error -- no retries inside scripts)
3. `adapters/stripe_fc_transactions.py --account_id {fca} --start_date {periodStart}` (or `last_pulled_through + 1`; overlap within the same connection is harmless -- dedup skips it). **No `--end_date`: pull through now**, so the ledger covers everything up to the balance `as_of` and the step-6 spot-check is exact. Rows dated after `{periodEnd}` are expected -- they sit unposted for the next period. Write **to a file** in the period's `source-docs/`, inspect the count -- an empty pull is a finding to investigate, not an ingest input (`ingest_universal.py` fails on an empty transactions array)
4. `ingest_universal.py --account_code {code} --file {saved_pull}.json`
5. `adapters/stripe_fc_balances.py --account_id {fca}` (read) -- capture `balance.current` and `as_of`
6. Verify with spot-check semantics: with the through-now pull, `verify_ingest_balances.py` computed (postings + all unposted imports) IS the ledger through `as_of` -- pass the FC **current** balance as the statement balance (positive owed for credit accounts; never `available`). Mismatch is the same Hard Stop as step 4 below.

Connections and mappings are established via `operations/connect-bank-feeds.md`. After successful ingest, record incremental-sync state: `manage_bank_feeds.py update --account_code {code} --last_pulled_through {date of latest pulled row} --last_txn_refresh_id {fctxnref}`. Failures, broken connections (re-auth/remap), and seam mismatches: `reference/bank-feeds-troubleshooting.md`.

**Processing approach:**

1. **Build account queue.** Parse the account registry (loaded from the local context registry) for account codes, adapter names, and data source paths. Validate data source accessibility (file existence or API config) during queue building. Remove inaccessible accounts from queue with explicit warning. Resolve adapters using the standard resolution order: local flat -> local dir -> shared flat -> shared dir -> error. Cross-check the period gather checklist instance (if present): an account enters the queue only when its source items are `received`; `pending` items are explicit blockers, not removals. Cross-check `{local_dir}/bank_feeds.yaml` (if present): accounts mapped there enter the queue on the live FC pipeline -- accessibility is `.env` keys present plus registry `status: active`, not file existence.

2. **Process each account.** For each account in the queue:
   - Invoke the resolved adapter, which transforms source data to universal JSON format: `{"transactions": [{external_id, date, amount, reference, balance_type, currency, raw_data}]}`
   - Pipe the universal JSON to `ingest_universal.py --account_code {code}`
   - Capture the structured result: `{success, imported, skipped, batch_id, source, date_range}`
   - Adapters are black boxes -- invoke, capture, report. No modification or workarounds for errors.

3. **Verify each account.** After import, run balance verification:
   - Collect statement ending balances (from balance adapters like `stripe_fc_balances.py`, or from bank statements). When the anchor is an FC balance, compare against the ledger computed through the balance `as_of` date -- not period end -- if they differ.
   - Run `verify_ingest_balances.py --balances '{"{code}": {amount_in_cents}}' --account {code}`
   - All balance comparisons use integer-cent arithmetic internally. Display as currency for users.
   - Load reconciliation notes BEFORE verification to account for known timing differences

4. **Handle mismatches.** Balance mismatch is a **Hard Stop** (see `reference/quality-guidelines.md`). Present I/D/F/X menu and HALT:
   - **[I]nvestigate:** Debug mode. After investigation, REDISPLAY the menu -- investigation does not resolve the block.
   - **[D]elete batch:** `DELETE FROM imports WHERE batch_id = ?` for the specific batch only (surgical, not all imports for the account). Route back to re-import.
   - **[F]lag:** Record flagged status with user-provided reason. Proceed to next account.
   - **[X] Cancel:** Record cancellation, terminate workflow for this account.

**Evidence recording:** Write quality evidence to the workpaper progressively per account, not batched at the end. Merge verification script results (postings_balance, unposted_imports, computed_ending, statement_ending_balance, difference) with adapter results (imported, skipped, date_range, batch_id) for complete evidence.

**Domain status determination:** All accounts passed -> `completed`. Any flagged but none failed -> `completed-with-flags`. Any failed (adapter error or cancelled) -> `failed`.

---

### Categorize

**Intent:** Every transaction must reach a terminal state -- categorized with journal entries created (`processed=1`) or flagged as a client question (`processed=2`). Nothing left in limbo. Zero unprocessed imports is the gate criterion.

**Local context:** Load files from `local-context.md` registry where precondition matches this domain. If no registry or no matching entries, proceed without domain-specific context.

**Scripts:**
- `apply_cat_rules.py` -- runs all active rules against unprocessed imports in priority order
- `bulk_cat_transactions.py` -- creates journal entries from AI-generated categorizations; marks low-confidence items as `processed=2`
- `test_cat_rule.py` -- dry-run simulator for rule accuracy testing
- `apply_transfer.py` -- links multiple imports from different feeds into a single balanced journal entry

**Processing order (this sequence matters):**

1. **Load chart of accounts.** `SELECT code, name, type, meta FROM chart_of_accounts ORDER BY code`. Retry once on failure. If retry fails, STOP -- cannot proceed without valid account codes.

2. **Assess current state.** Query transaction states grouped by processed flag: unprocessed (0), categorized (1), client questions (2). If zero unprocessed, skip to quality gate.

3. **Detect inter-account transfers FIRST.** Before any bulk categorization, scan for matching/summing amounts across different source accounts on same or adjacent dates. Common patterns:
   - CC payments: checking debit = sum of CC credits
   - Intercompany transfers
   - Bank-to-bank moves

   Use `apply_transfer.py` for detected transfers. Link all related imports (primary + secondary) into a single balanced journal entry. All linked imports are marked `processed=1` -- do NOT send secondary-side imports to client questions.

4. **Apply existing rules.** Run `apply_cat_rules.py`. Parse result: `{success, processed, unmatched, failed, errors[]}`. If rules categorize everything (unmatched = 0), skip AI research. On script failure, retry once. If retry fails, report "0 categorized by rules" and continue -- AI research can still process everything.

5. **AI research for remaining.** Process uncategorized transactions in batches of 5-15, grouping repeat vendors together:
   - Research each vendor via web search and historical database patterns
   - Historical pattern query: `SELECT i.reference, i.amount, i.date, je.account_code, je.confidence_score FROM imports i LEFT JOIN journal_entries je ON i.id = je.import_id WHERE i.processed IN (1, 2) AND i.reference LIKE '%{vendor_pattern}%' ORDER BY i.date DESC LIMIT 10`
   - Validate every assigned account code exists in the chart of accounts
   - Apply the three-zone confidence system:
     - Below `min_confidence_to_categorize` -> `processed=2` (client questions, no JE created)
     - Between thresholds -> `processed=1` (judgment calls, JE created, surfaced for review)
     - At or above `min_confidence_to_auto_approve` -> `processed=1` (auto-approved, no review needed)
   - Watch for crossed account types (expense coded to revenue, asset as liability) -- these are red flags
   - On batch script failure, retry once, then log error, skip batch, and continue with next segment

6. **Create rules from confirmed patterns.** Track patterns worthy of rule creation. Criteria: (1) second or later occurrence of same vendor pattern, (2) consistent account code across history, (3) clear match criteria formulable. Always test rules before inserting using `test_cat_rule.py`. 100% accuracy or zero historical matches -> proceed. Below 100% -> present mismatches and offer update/delete/keep options.

**Context complexity alert:** If pattern count exceeds 5, warn the user about context strain and offer: [A] proceed anyway, [B] skip and defer to fresh session. Save the pending patterns to `{workpapers_dir}/period-close/{periodLabel}/` (e.g., `rules-to-create-{date}.md`) and provide the path for standalone invocation.

**Quality gate:** `SELECT COUNT(*) FROM imports WHERE processed = 0` must equal zero. Both `processed=1` and `processed=2` are valid terminal states.

---

### Trade Accounts

**Intent:** All A/R and A/P records exist and are accurate. This is the foundation for payment matching in the next domain. Every source defined in the registry must be processed, and every creation must be verified.

**Local context:** Load files from `local-context.md` registry where precondition matches this domain. If no registry or no matching entries, proceed without domain-specific context.

**Scripts:**
- `create_trade_account.py` -- creates a trade account linked to a journal entry, with contact auto-creation
- `create_credit_memo.py` -- creates a Credit Memo (contra-receivable TA + originating JE) for customer returns, refunds, promotional credits
- `create_vendor_credit.py` -- creates a Vendor Credit (contra-payable TA + originating JE) for vendor refunds, rebates

**Local adapters:** `{local_dir}/adapters/trade-accounts/` -- client-specific adapters for A/R and A/P data sources

**Processing approach:**

1. **Load inventory.** Parse registry for source definitions. If registry missing, switch to "generic identification mode" where the agent reviews imported transactions to identify trade account candidates. Recommend creating a registry during finalize for future automation.

2. **Check for existing records.** For each registry source, run `list_open_items.py` to check if trade accounts already exist (duplicate prevention). Mark found items as "complete" in the checklist.

3. **Process each source.** Follow three-tier adapter resolution: local -> shared -> manual fallback.
   - **Adapter path:** Adapters accept source files + account configuration, create journal entries and trade accounts, return `{success, trade_accounts_created, trade_accounts[]}`.
   - **Manual path:** Guide creation with `create_trade_account.py`, collecting type (receivable/payable), contact, document date, journal entry ID, balance account code, optional due date and metadata. Entity validation covers amount, contact, and date.
   - **Post-creation verification:** After each creation, run `list_open_items.py` filtered by contact/type to confirm records exist. Mark failed if verification does not match expectations.

4. **Handle failures.** On adapter failure, present: [R]etry, [S]kip (mark failed, continue), [I]nvestigate (collaborative troubleshooting, then re-present options). Wait for user selection.

**Quality gate:** Dual-source verification -- cross-reference (1) workpaper item statuses with (2) `list_open_items.py` database query. Check registry item completeness, contact name alignment, type matching, and count consistency. Execute firm-specific verification checklist if available. Override option requires justification and marks gate as "passed with overrides."

---

### Apply Payments

**Intent:** Match bank transactions to open trade accounts. Differentiate high-confidence matches (exact amount + date + registry pattern) from AI-inferred matches (heuristic-based). Every open item must be matched, flagged, or explicitly accounted for.

**Local context:** Load files from `local-context.md` registry where precondition matches this domain. If no registry or no matching entries, proceed without domain-specific context.

**Scripts:**
- `apply_payment.py` -- applies a single payment to a trade account, creates clearing journal entry
- `apply_payments_bulk.py` -- applies a single bank deposit to multiple trade accounts atomically
- `apply_credit.py` -- applies a credit_memo to a receivable invoice, or a vendor_credit to a payable bill (no clearing JE — credits already posted to A/R or A/P at issuance)

**Processing approach:**

1. **Load open items.** Query both `--status=unpaid` and `--status=partial` from `list_open_items.py`, combine into a single working set. If zero open items, complete immediately -- write "No open items to match" to workpaper and move on.

2. **Tag match strategies.** Based on registry patterns (if available), tag each open item:
   - `direct` -- bank transaction matching entity patterns
   - `clearing` -- clearing account activity
   - `bulk` -- single transaction covering multiple trade accounts
   - `ai-inferred` -- no registry pattern, heuristic-based

   If no registry loaded, all items are `ai-inferred`. Warn about lower confidence expectations and recommend creating a registry during finalize.

3. **Identify candidates.** Confidence tiering:
   - **High:** Strong registry pattern + amount/entity alignment
   - **Medium:** Partial pattern or AI with multiple signals
   - **Low:** Weak signals, ambiguous
   - AI-inferred matches are NEVER tagged as high confidence regardless of signal quality

4. **Present for confirmation.** Adapt presentation style:
   - If most matches are high confidence -> batch summary table for efficient confirmation
   - If most are AI-inferred -> conversational walkthrough with explicit rationale per match
   - User actions per match: confirm, reject, adjust, skip (flag for review)
   - Unmatched items: manually match, flag for Client Questions, or skip with explicit reason

5. **Apply payments in dependency order:**
   - Direct matches first (simplest)
   - Clearing account matches second
   - Bulk payments last (one `import_id` with different `trade_account_id` and `amount` per sub-application)
   - All amounts in cents for script invocation. Display as currency for users.
   - Individual payment failures are logged and flagged but do NOT stop processing. Continue through all confirmed matches.

6. **Resolve flagged items.** Each flagged item offers:
   - **[M] Manually match:** User provides import ID, validate and execute
   - **[F] Flag for Client Questions:** Defer with full context for the client questions workflow
   - **[S] Skip with reason:** User provides explicit reason (e.g., "payment expected next month," "disputed invoice")

**Quality gate:** ALL must be true: (1) every open item with identified candidate has payment applied OR explicitly flagged, (2) each executed `apply_payment.py` call returned `success: true`, (3) no misapplied payments (duplicate detection -- same import applied to same trade account multiple times). Flagged items documented as exceptions do not prevent gate pass.

---

### Manual Journals

**Intent:** Process the manual journal entry inventory -- accruals, depreciation, adjustments, COGS, payroll allocations, recurring entries. Each entry must balance (total debits = total credits). Every registry item must be processed, and every creation must be verified.

**Local context:** Load files from `local-context.md` registry where precondition matches this domain. If no registry or no matching entries, proceed without domain-specific context.

**Scripts:**
- `create_manual_journal.py` -- creates manual journal entries with pre-built postings

**Local adapters:** `{local_dir}/adapters/manual-journals/` -- client-specific adapters for automated journal entry creation (e.g., depreciation schedules, payroll allocations)

**Processing approach:**

1. **Load inventory.** Parse registry for entry definitions. If registry missing, switch to "generic mode" -- ask user what entries are needed. Suggest creating registry during finalize for future automation.

2. **Check for duplicates.** Query `journal_entries` where `metadata->>'source' = 'manual'` for the period to detect existing entries. Mark found items as "complete" to prevent re-creation on re-runs.

3. **Process each item.** Follow adapter resolution: local -> shared -> manual fallback.
   - **Adapter path:** Adapters create entries via `journal_engine.create_journal_entry_direct()`, return `{success, journal_entries_created, journal_entries[]}`.
   - **Manual path:** Pre-fill accounts, amounts, and memo patterns from registry definitions for user confirmation (not from scratch). Enforce journal policies (loaded via registry precondition) if available: approval thresholds, class requirements, memo conventions, reversal policies. Posting structure: `{account_code, direction, amount (integer cents), contact (optional), description (optional)}`.
   - **Balance enforcement:** Every entry must balance. Debits must equal credits.

4. **Verify each creation.** After each item, query database to confirm journal entries were created: `metadata->>'source' = 'manual'` with expected date/memo patterns. Mark failed if verification does not pass.

5. **Handle failures.** Same pattern: [R]etry, [S]kip, [I]nvestigate. Wait for user selection. Master rule: "Process every item. Verify every creation. Report every result. No silent failures."

**Quality gate:** Dual verification -- (1) all workpaper items "complete" with zero "failed" or "pending", (2) database cross-reference confirming entries exist with correct memo patterns, account codes, and balanced postings. Per-entry evidence must be granular enough for a manager to verify without querying the database: entry name, JE ID, date, memo, accounts (Dr/Cr codes), amount, balance verification.

---

### Review

**Intent:** Read-only analysis across all completed domains. Flag irregularities for human judgment. NEVER modify the database during review. This is a **Hard Stop** -- any proposed data modification during review is a system failure (see `reference/quality-guidelines.md`).

**Reference:** Load `reference/review-checks.md` before starting. This is the single source of truth for all review checks.

**Local context:** Load `review-notes.md` from local context registry (known exceptions, clearing account designations). Check firm `content_manifest` for firm-level review guidance when no local review context exists.

**Scripts:**
- `list_open_items.py` -- trade account queries and subledger totals
- `export_to_excel.py` -- generates multi-sheet review workbook (`review_package` envelope)

**Processing approach:**

1. **Run all checks** defined in `reference/review-checks.md` (12 checks covering imports, trade accounts, balance sheet, income statement, error detection, and subledger ties). Suppress known exceptions from `review-notes.md`. The agent reasons about materiality using business context -- no hardcoded dollar thresholds.

2. **Generate Excel review package** at `{workpapers_dir}/period-close/{periodLabel}/{periodLabel}-review.xlsx` — alongside the workpaper, like every period-specific artifact (matches `reference/review-checks.md`; never a separate top-level output folder) — with dashboard, transaction register, subledger-to-GL tie, and period-over-period variance tabs. This is the primary deliverable for human review.

3. **Record results to workpaper** -- per-check status (pass/warn/fail), flagged item counts, narrative evidence.

**Flagged item requirements:** Every flagged item must include: description, affected account(s)/source/counterparty, amount, recommended action. Missing any field causes quality gate failure.

**Quality gate:** All checks completed with status recorded, all flagged items have required fields populated, Excel review package generated. Evidence must be self-contained and manager-readable.

---

### Client Questions

**Intent:** Gather flagged items from any domain, translate to plain language the client can understand, deliver questions, and process responses. This domain acts as a feedback loop -- client answers flow back into categorization, trade accounts, and journals.

**Local context:** Load files from `local-context.md` registry where precondition matches this domain. If no registry or no matching entries, proceed without domain-specific context.

**Scripts:**
- `export_to_excel.py` -- generates formatted Excel deliverables from universal JSON

**CRITICAL: Dual-source extraction.** Extract from TWO distinct sources and deduplicate:

1. **Database queries:**
   - `imports.processed=2` -- items flagged as client questions during categorization
   - `postings.confidence_score` between `min_confidence_to_categorize` and `min_confidence_to_auto_approve - 1` -- judgment calls that were coded but not auto-approved
   - Transaction descriptions from `imports.raw_data` JSON with case-insensitive key matching (reference, description, memo -- field names vary by adapter)

2. **Workpaper flags from upstream domains:**
   - Review workflow anomalies and flagged items
   - Apply Payments unmatched or deferred items
   - Any domain flags marked for client attention

3. **Deduplication:** Match by `import_id` or by `amount + date + reference` composite key. Merge context from both sources when deduplicating.

**Plain language mandate:** Questions must be translated from accounting jargon to plain language. Never use raw account codes in client-facing output. Group by topic, provide context.

**Delivery:** Default to Excel export at `{workpapers_dir}/period-close/{periodLabel}/client-questions-{periodStart}-to-{periodEnd}.xlsx` — alongside the workpaper, like every period-specific artifact (a multi-period consolidated batch lives in the earliest period's directory, referenced from the others). Agent generates the file directly if delivery method specified in `deliver.md`. Record file path as quality gate evidence.

**Optional pause:** After delivery, the workflow can pause for client response time. Offer: Continue to process feedback OR Exit and resume later.

**Processing responses:** Three feedback collection modes: one at a time, all at once (paste full response), or from file (annotated Excel). Item-by-item processing with user confirmation on each action:
- **Code it:** Invoke `bulk_cat_transactions.py`
- **Create rule:** Invoke `create-cat-rule` sub-workflow or guide manual creation
- **Adjust existing rule:** Present current rule, apply change after confirmation
- **Defer:** Record reason for later re-run

Database mutations only via scripts, never direct writes. Cross-workflow knowledge updates during feedback processing: categorization patterns and payment patterns flow to the relevant domain context files (loaded from the local context registry).

**Quality gate:** `open_items = extractedItems - resolvedItems - deferredItems` must equal zero. Gate failure does NOT block -- flag and offer: process remaining, defer all with single reason, or continue with flag documented. Evidence must pass the "manager test" -- someone reading just the workpaper knows the workflow completed successfully.

**Onboarding expectation:** In onboarding context, explicitly warn: "Expect more questions than usual -- no prior rules or context built up yet." Reinforce at completion: "Question list will shrink in future periods as rules build up."

---

### Publish

**Intent:** Push all approved data to the system of record. Must be idempotent -- never double-post entries. This domain has two Hard Stops (publish errors with failed entries; first-time client dry-run) and one judgment-based stop (pre-publish scope review) — see `reference/quality-guidelines.md`.

**Local context:** Load files from `local-context.md` registry where precondition matches this domain. If no registry or no matching entries, proceed without domain-specific context.

**Scripts/Adapters:** `publish_to_qbo.py` (or SoR-appropriate adapter based on `default_system_of_record` config). Adapter resolution follows the standard six-layer order:
1. `{local_dir}/adapters/publish/publish_to_{sor}.py` (local flat)
2. `{local_dir}/adapters/publish/{sor}/` (local directory)
3. `{firm_root}/adapters/publish/publish_to_{sor}.py` (firm flat — skip if no firm_root)
4. `{firm_root}/adapters/publish/{sor}/` (firm directory — skip if no firm_root)
5. `{module_root}/adapters/publish_to_{sor}.py` (shared flat)
6. `{module_root}/adapters/publish/{sor}/` (shared directory)

If none found, list directory contents to check for alternate naming, then stop with error.

**Adapter gotchas (precondition):** after resolving the adapter, read its co-located `gotchas.md` if present (in the adapter's directory, or alongside a flat adapter file) — field-earned quirks of this SoR that shape how the publisher must be driven (read-only API fields, validation traps, post-then-fail error classes, human-relay protocols). During error handling, append newly-earned SoR-generic gotchas back to that file; client-specific state goes to the client's local content instead. See `reference/adapter-patterns.md` § The `gotchas.md` Convention.

**Processing approach:**

1. **Validate preconditions.** Confirm approved journal entries exist for the target date range. If zero entries in scope, stop gracefully: "Nothing to publish." Do not proceed with a no-op.

2. **Dry-run (recommended, required for first-time clients).** Invoke adapter with `--dry_run` flag. Validation dimensions:
   - OAuth/credentials status
   - Account mapping completeness (every internal account code must map to a valid SoR account)
   - Balance validation (debits = credits per journal entry)
   - Scope summary (entry count, posting count, date range)
   - Show 2-3 sample transformed entries for manual inspection of amounts, account refs, entity refs

   After dry-run, provide assessment. If zero errors: "All checks passed. Safe to proceed." If any errors: "Issues found. {summary}. These would cause failures during live publish."

   **Judgment-based stop:** After the dry-run, proceed directly to live publish when validations are clean, scope matches the workpaper's expectations, and standing authorization exists for a recurring close (first-time clients always require explicit confirmation — Hard Stop 5). Stop and present the [P]/[X] choice when anything is off, novel, or outsized. The SoR is rectifiable; post-publish verification is the real gate.

3. **Live publish.** Invoke adapter for live publish. Dependency ordering: trade accounts published before payments (entries referencing other entries must be published after their dependencies).
   - **Happy path:** processed = target count, failed = 0, skipped = 0. Brief confirmation and auto-proceed to finalize. This is the ONLY auto-proceed in publish.
   - **Partial/full failure:** Hard Stop. Present counts (processed/failed/skipped). Group errors by type/pattern to surface root causes. Engage collaboratively: "What do you think is going on here?" Then present error protocol menu:
     - **[R] Retry:** Re-invoke adapter with `--sync_status=error` targeting only failed entries. Re-evaluate results.
     - **[S] Skip:** Mark failed as ignored, proceed with acknowledged partial result.
     - **[W] Write and Stop:** Write error state to workpaper for team investigation and later re-run.
     - **[X] Cancel:** Abort entirely, nothing written.
   - Load firm error protocol (`error-protocol.md`) if it exists -- may override default menu options or add automatic behaviors.
   - Distinguish fatal infrastructure errors (credentials expired, network error -- skip straight to error menu) from per-entry failures (collaborative analysis first).
   - **`verify` rows are NOT retryable:** sync status `verify` means the record's posted-state in QBO is unknown (the publisher's post-fault read-back was ambiguous or inconclusive) -- a blind retry could double-post, so no `--sync_status` option selects them. Resolution is manual: search QBO for the row's `[bk:…]` tag; if the object exists, record its real id via `update_sync_success`; if it does not, reset the row to pending. Never close a period over an open `verify` row.

4. **Verify external IDs.** Every targeted record must have a non-null `external_id` in the adapter response. An entry without a confirmed external ID is not verifiably published. Also scan the run's stderr warnings: `LOCATE_RECOVERED` / `AUTH_RETRY` lines mean the run self-healed -- the books are right, but an elevated count signals QBO instability or token churn worth noting in the workpaper.

**Quality gate:** All must be true: processed = targeted count, failed = 0, skipped = 0, all external IDs confirmed, no records in pending/error/**verify** sync status within scope (check: `SELECT COUNT(*) FROM <table> WHERE json_extract(sync,'$.status') IN ('pending','error','verify')` for journal_entries, trade_accounts, and trade_account_payments in the date range). Partial pass (bookkeeper chose Skip) is a distinct outcome from failure -- documented as acknowledged partial result.

**Full pass acknowledgment:** "All {processed} entries are live in {sor_target}. Every external ID confirmed. The books are synced."

---

## Cross-Cutting Work

When work spans multiple domains, handle it fluidly rather than forcing it into a single domain's context:

- **Client question responses** that require coding + trade account creation + journal entries: load the relevant context files for each domain, use the appropriate scripts, and update the workpaper across multiple domain sections.
- **Review findings** that reveal categorization errors: flag them for correction in the appropriate domain (coding re-run, not during review itself -- review is read-only).
- **Transfer detection** during categorization that affects trade accounts: complete the transfer handling in categorize, note the implications for trade accounts.
- **Payment matching** that reveals missing trade accounts: route back to trade accounts domain to create the missing records, then return to apply payments.

The agent should maintain awareness of the full dependency graph and route work to the correct domain, loading domain-specific context as needed. The workpaper is the central coordination surface -- update it as work completes regardless of which domain the work originated from.

---

## Knowledge Capture

At the end of each session (or when completing a significant domain), review what was learned and propose intent-based updates to relevant content files. See `reference/knowledge-capture.md` for full guidance.

**Filter criteria -- ALL three must be true:**
1. Would change how the agent handles the same situation in the future
2. Not already captured in existing content files, config, or rules
3. Expressible in 1-3 sentences without losing key detail

**Target files:** Determined by checking the `local-context.md` registry and applying the principles in `reference/knowledge-capture.md`. Create new files and register them organically — do not route to predetermined paths.

**Approval flow:** Present each candidate with target file, registry entry (if new file), action (append/update/create), proposed content, and rationale. Wait for user response: [Y] Approve, [E] Edit, [S] Skip. Process sequentially, never batch. Never write without approval.

**Cross-workflow knowledge flow:** Client Questions feedback processing can drive updates to OTHER domains' content files. This is intentional -- knowledge flows across domain boundaries.

---

## Workpaper Updates

After completing work in any domain, update the workpaper with:

### 1. Domain Status
Update `domains_status.{domain}` in frontmatter to one of: `not-started`, `in-progress`, `completed`, `completed-with-flags`, `completed-with-overrides`, `failed`.

### 2. Structured Summary (minimum)
Under the domain's section in the workpaper body:
- **Items processed:** Count of items handled
- **Items flagged:** Count and brief description of items requiring attention or left unresolved
- **Verification method:** What script, query, or check was run (not just "verified" -- name the method)
- **Discrepancies:** Any differences, mismatches, or anomalies with their resolution or current status

### 3. Narrative Evidence
Answer two questions:
- "What evidence would convince a manager this was done correctly?" -- State specific checks and results.
- "How could they reproduce this verification?" -- Reference the specific script or query.

### 4. Progressive Writing
Write evidence progressively as work completes within a domain -- not batched at the end. This ensures partial progress is preserved if the session is interrupted.

### 5. Action Items and Roadblocks
Update `action_items` and `roadblocks` arrays in frontmatter as they surface or resolve. These provide a quick-scan summary of outstanding issues across all domains.

---

## References

- `reference/capability-registry.md` -- Script discovery, arguments, input/output contracts, database tables
- `reference/quality-guidelines.md` -- Quality phases, failure modes, Hard Stops, workpaper evidence standards, anti-fabrication rule
- `reference/bookkeeping-principles.md` -- Principles (confidence scaling, deduplication, learning from patterns, idempotent publishing) and Constraints (period boundaries, balance reconciliation, review is read-only, anti-fabrication, precondition blocking)
- `reference/knowledge-capture.md` -- What to capture, what not to capture, where to capture, filter criteria, approval flow
- `reference/adapter-patterns.md` -- Agent-first philosophy, code placement, script conventions, adapter resolution
