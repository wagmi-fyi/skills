# Quality Guidelines

## Overview

These guidelines govern agent reasoning about quality across the bookkeeping workflow. They are not checkpoint gates — the agent should internalize these principles and apply them continuously, not treat them as a checklist to satisfy at discrete moments.

The exception is **Hard Stops**. Hard Stops are non-negotiable halting points where the agent must stop, present evidence, and wait for an explicit human decision before continuing. There is no auto-proceed through a Hard Stop under any circumstance.

---

## Phases

### Ingest Quality

**Completeness:** Every configured data source for the period must be accounted for. "Accounted for" means either successfully imported or explicitly flagged as unavailable with a reason. A silent skip — where a source is simply not attempted — is a completeness failure. The agent must verify that the account queue built during initialization covers all expected sources, and that every account in the queue reaches the quality gate (even if the adapter fails, the failure must be recorded).

Verification techniques:
- Compare the account queue against the account registry — any registered account not in the queue requires explanation
- Confirm date range coverage spans the full target period per source (flag gaps where transaction dates do not cover period start through period end)
- Check import counts and totals per source against expectations from prior periods when available

**Correctness:** Imported data must match source documents. "The script ran without errors" is insufficient evidence of correctness. The agent must verify that computed ending balances reconcile against source document balances using the tri-component calculation: `computed_ending = postings_balance + unposted_imports`.

Verification techniques:
- All balance comparisons use integer-cent arithmetic internally (display as currency for humans, compare as integers to avoid floating-point errors)
- Run `verify_ingest_balances.py` and parse its structured output — do not estimate or mentally compute balances
- Load reconciliation notes before verification to account for known timing differences (statement cut dates, maintenance fees posting in next period, etc.)
- When a mismatch occurs, present the discrepancy with full detail — never silently round, adjust, or dismiss a difference

---

### Process Quality

**Completeness:** Every imported transaction must reach a terminal state. Terminal states are: categorized with journal entries created (processed=1), or flagged as a client question (processed=2). An unprocessed count of zero is the gate criterion: `SELECT COUNT(*) FROM imports WHERE processed = 0` must equal zero. Completeness also means:

- All inter-account transfers detected and linked (CC payments appearing as matching debit/credit pairs must be linked into a single balanced journal entry, not double-booked as separate expenses)
- All trade accounts created for receivables and payables
- All payments matched to their corresponding trade accounts or flagged
- All manual journal entries entered where required

Verification techniques:
- Query for unprocessed transactions after each processing phase (rules application, AI research) to confirm progress
- Verify transfer detection ran before bulk categorization — transfers removed from standard batch prevent double-booking
- Confirm that every trade account payment has a corresponding trade account record

**Correctness:** The right account code on the right transaction with the right contact. Correctness failures come in two severities:

- **Red flag (crossed account types):** An expense coded to a revenue account, or vice versa. An asset recorded as a liability. These are structural errors that distort financial statements in both direction and magnitude. The agent must validate that assigned account codes have types consistent with the transaction's nature.
- **Yellow flag (wrong contact or miscategorization):** The right general area but wrong specific account (office supplies vs. office equipment) or wrong contact assigned. These affect detail accuracy but not structural integrity.

Verification techniques:
- Validate every assigned account code exists in the chart of accounts before creating journal entries
- Check that account types align with transaction direction (debits to debit-normal accounts, credits to credit-normal accounts, with expected exceptions documented)
- For AI-researched categorizations, verify confidence scores align with the evidence (historical match count, vendor identification clarity, account consistency)
- Review categorization patterns for anomalies: single large transactions in accounts that typically have many small ones, expense accounts carrying credit balances, income accounts with unusual debit activity

---

### Publish Quality

**Completeness:** Every approved journal entry within the target scope must be synced to the system of record. "Completeness" means processed count equals the total targeted count, with zero entries left in pending or error sync status within the date range. External IDs must be confirmed for every published record — an entry without a confirmed external ID is not verifiably published.

Verification techniques:
- Compare processed count against total targeted count from the adapter response
- Verify that every targeted record has a non-null external ID in the response
- Query for any records remaining in pending or error sync status within the scope date range
- For partial publishes (bookkeeper chose to skip some entries), record the acknowledged partial result distinctly from a failure

**Correctness:** Published entries must match staging data exactly. The transformation from internal format to SoR format must be lossless for financial data (amounts, account references, entity references, dates). Idempotency must be verified — re-running a publish must not create duplicate entries in the SoR.

Verification techniques:
- During dry-run, inspect 2-3 sample transformed entries to confirm amounts, account mappings, and entity references match internal data
- Verify account mapping completeness — every internal account code used in targeted entries must map to a valid SoR account
- Confirm balanced entries (total debits equal total credits per journal entry) before publish
- On retry operations, use sync_status=error filter to target only failed entries, preventing re-publish of already-synced entries

---

## Failure Modes

These are the specific ways quality breaks down. The agent should be vigilant for each.

### 1. Data Not Fully Ingested

A data source was missed entirely, or a source was partially imported (date range does not cover the full period, or transaction count is unexpectedly low compared to prior periods). This is an ingest completeness failure. Common causes: account missing from registry, adapter resolution failure that was silently skipped, data source file not available at expected path, API credentials expired.

**Detection:** Compare account queue against registry. Check date range coverage per source. Compare transaction counts to prior period baselines when available.

### 2. Data Ingested Incorrectly

Data was imported but does not match the source. Computed ending balance does not reconcile with the statement ending balance. This is an ingest correctness failure. Common causes: wrong file version imported, duplicate import creating doubled transactions, currency or sign convention mismatch in adapter transformation, statement balance recorded in wrong denomination.

**Detection:** Tri-component balance verification. Integer-cent comparison. Reconciliation notes review for known timing items.

### 3. Data Processed Incorrectly

Transactions were categorized but to wrong accounts or with wrong attributes.

- **Red flag — crossed account types:** Revenue coded as expense (understates both revenue and expenses on IS, no BS impact). Expense coded as asset (understates expenses, overstates assets). Liability coded as equity (misrepresents capital structure). These distort financial statement structure and require immediate correction.
- **Yellow flag — wrong contact or miscategorization within correct type:** Office supplies coded to office equipment (both are expenses, affects detail but not totals by type). Wrong vendor contact assigned (affects subsidiary ledger accuracy). These should be flagged for review but do not require workflow halt.

**Detection:** Account type validation against chart of accounts. Confidence score review. Pattern anomaly scanning (credit balances in expense accounts, debit activity in income accounts, single large entries in typically-small-transaction accounts).

### 4. Data Not Fully Processed / Skipped Steps

Some transactions never reached a terminal state. Common manifestations: unprocessed imports remaining after coding workflow completes, transfer detection skipped so matching pairs were independently categorized (creating double-booked entries), trade accounts not created for receivable/payable transactions, payments not matched.

**Detection:** Unprocessed count query. Transfer pair scanning. Trade account completeness check against categorized AR/AP transactions. Payment matching verification.

### 5. Data Not Published Correctly or Completely

Entries were published but the result does not match intent. Common manifestations: some entries failed to sync but workflow continued silently, duplicate entries created from non-idempotent retry, account mappings incorrect causing entries to land in wrong SoR accounts, entity references broken causing orphaned entries.

**Detection:** Processed vs. targeted count comparison. External ID confirmation for every record. Duplicate detection on retry. Account mapping validation during dry-run.

---

## Hard Stops

Stops 1, 3, 4, 5, 6, and 7 are **non-negotiable**: when triggered, the agent MUST halt execution, present the relevant evidence clearly, and wait for an explicit human decision — no auto-proceed, no timeout, no default action. Stop 2 is **judgment-based**: the system of record is rectifiable, so a clean publish under standing authorization flows without a mandatory pause. Stops 6 and 7 (SoR reconciliation, opening and closing) carry an additional bar: the gate clears only when the variance is *resolved*, not by a decision to proceed past it.

### 1. Balance Mismatch After Ingest Verification

**Trigger:** `verify_ingest_balances.py` reports a non-zero difference between computed ending balance and statement ending balance, after accounting for known reconciliation notes.

**Required action:** Present the full discrepancy detail: computed ending balance, statement ending balance, difference amount, postings balance, unposted imports total. Present the I/D/F/X menu and halt. After [I]nvestigate, redisplay the menu — investigation does not resolve the block. Only D (delete batch and re-import), F (flag with reason), or X (cancel) allow the workflow to continue.

### 2. Pre-Publish Scope Review (judgment-based stop)

**Trigger:** Publish workflow reaches the point of committing entries to the system of record (live publish, not dry-run).

**Required action:** Run the dry-run and review it against the period's workpaper expectations. **Proceed without pausing** when ALL hold: validations clean (zero mapping/balance/contact/class errors), scope matches expectations, recurring-close context, and the user has given standing authorization for the close (e.g. "run the publish loop"). **Stop and present the scope + [P]/[X] choice** when any of: validation errors, unexpected or structurally novel scope, unusually large volume, first-time client (Hard Stop 5 governs), or anything the agent judges worth human eyes. Rationale: SoR objects are rectifiable (update/void by external_id), so the cost of a wrong publish is bounded — mandatory gates on every clean recurring publish add friction without protection. Post-publish verification (external IDs, sync-status sweep, trial-balance reconcile) is the real safety net and remains mandatory.

### 3. Publish Errors With Failed Entries

**Trigger:** Adapter response contains failed > 0 or skipped > 0 after a live publish attempt.

**Required action:** Present the failure counts (processed/failed/skipped), group errors by type/pattern to surface root causes, and engage collaboratively: "What do you think is going on here?" Present the error protocol menu (R/S/W/X) and halt. Do not silently continue with partial results. Do not auto-retry without human decision.

### 4. Any Proposed Data Modification During Review

**Trigger:** Any action during the Review workflow that would write to the database (INSERT, UPDATE, DELETE on financial data tables).

**Required action:** Review is read-only analysis. If the agent identifies something that needs correction, it must flag the finding with a recommended action and halt. The correction must be performed in the appropriate workflow (coding, ingest re-run, manual adjustment) — never during review. Writing to the database during review is a system failure.

### 5. First-Time Publish for a New Client

**Trigger:** Onboarding context detected during publish workflow (first period being processed for this client).

**Required action:** Require a dry-run before live publish. Present the dry-run as a strong recommendation, not an optional step. Show all validation dimensions (credentials, account mappings, balance validation, scope summary) and sample transformed entries. The bookkeeper must explicitly confirm readiness for live publish after reviewing dry-run results.

### 6. SoR Reconciliation — Opening (before Ingest)

**Trigger:** At period entry, the as-of reconcile of the local ledger through the prior close against the system of record reports any variance (for QBO: `adapters/qbo/reconcile_trial_balance.py --as_of {priorCloseDate}` returns `success: false`).

**Required action:** Halt before Ingest and present the variances (account, local, SoR, difference). A variance means the SoR changed since the last seal — a record edited or reclassified directly in the SoR, or a prior-period entry that never synced. **Resolve the variance — do not acknowledge-and-proceed.** A judgment-call or locked-period rectification routes through the user (locked periods follow the mirror-date reclass convention; new entries are never edited in place). The gate clears only when the reconcile returns zero variances (or every variance is explicitly reconciled and recorded) — never by a decision to proceed past an open difference.

### 7. SoR Reconciliation — Closing (before Publish)

**Trigger:** Before committing the period, the direct-record scan finds any SoR record dated in the period that staging did not create — i.e. that lacks the publisher's idempotency tag (for QBO: `adapters/qbo/scan_sor_direct_records.py --period_start {periodStart} --period_end {periodEnd}` returns a non-empty `untagged_records` / `success: false`).

**Required action:** Halt before Publish and present each surfaced record (type, id, date, amount, name/memo). These are direct SoR entries, system-generated artifacts (e.g. an auto-applied credit), or failed syncs — anything the publisher would otherwise duplicate. **Resolve each one — do not publish over it.** Resolution means reconciling the record into staging, accounting for it explicitly, or neutralizing a colliding artifact; rectification that touches a locked period or needs judgment routes through the user. The gate clears only when the scan returns an empty untagged set (or every surfaced record is resolved and recorded). This is distinct from Hard Stop 2 (judgment-based scope review): the closing scan is non-negotiable and resolve-to-clear, because the cost it prevents — a duplicate posting in the SoR — is incurred the moment Publish runs.

---

## Workpaper as Audit Trail

When the agent completes work in a domain, it must record quality evidence to the workpaper. This evidence serves two purposes: it proves the work was done correctly, and it enables a manager to verify the work without re-running it.

### 1. Minimum Structured Summary

Every domain must record at minimum:

- **Items processed:** Count of items handled (transactions imported, transactions categorized, entries published, etc.)
- **Items flagged:** Count and brief description of items that required attention or were left unresolved
- **Verification method used:** What script, query, or check was run to verify quality (not just "verified" — name the method)
- **Discrepancies found:** Any differences, mismatches, or anomalies discovered, with their resolution or current status

This structured summary is written progressively as work completes — not batched at the end of the workflow.

### 2. Narrative Elaboration

Beyond the minimum structured fields, the agent should provide narrative evidence that answers two questions:

- **"What evidence would convince a manager this was done correctly?"** — State the specific checks performed and their results. For example: "Imported 47 transactions for checking account 1000. Computed ending balance of $12,847.33 matches statement ending balance exactly. Date range covers Feb 1 through Feb 28 with no gaps."
- **"How could they reproduce this verification themselves?"** — Reference the specific script, query, or file that produced the evidence. For example: "Run `verify_ingest_balances.py --account 1000 --balances '{"1000": 1284733}'` to reproduce this verification."

The narrative should be concise but complete. A manager reading the workpaper should be able to understand what happened, trust that it was done correctly, and know how to double-check if needed.

### 3. Ingest Evidence Table

| Account | Statement Date | Statement Balance | DB Computed Balance | Match |
|---------|---------------|-------------------|---------------------|-------|

Computed = postings_balance + unposted_imports. All accounts must match exactly ($0.00 difference) or the mismatch must be explained. Verify against actual statement ending balances — do not manually adjust balances for out-of-period transactions.

---

## Anti-Fabrication Rule

All financial figures reported by the agent — balances, totals, counts, amounts, percentages — must originate from script outputs, database query results, or source documents. The agent must never generate, estimate, interpolate, or mentally compute financial data from its own reasoning.

The agent is a facilitator and orchestrator. It invokes scripts, parses their outputs, presents results, and coordinates decisions. It does not produce financial content. If a script fails to return expected data, the agent reports the failure — it does not fill in the gap with a plausible number.

This rule applies to all phases: ingest balances come from `verify_ingest_balances.py`, processing counts come from database queries, publish results come from adapter responses. No exceptions.

---

## Precondition Discipline

Before entering any domain or writing to any content file, check the local context registry for precondition triggers. Do not rely on session-start loading — re-check before each action. This is not optional overhead; it prevents compounding errors that cost more to fix than the time saved by skipping.

When something doesn't balance, reconcile, or match — stop. Investigate the root cause before proceeding. Never compensate for a gap with adjustment postings, reclassifications, or assumptions carried forward from prior periods. A gap is a signal, not an inconvenience to route around.

When uncertain about the right approach, ask. A question costs seconds; a wrong assumption costs hours of rework.
