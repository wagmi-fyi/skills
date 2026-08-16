# Review Checks

Unified analytical procedures for the Review domain. All checks are read-only — no database writes. The agent runs each check, reasons about the results using business context (company overview, prior period data, materiality), and records findings to the workpaper.

## Checks

### 1. Imports Completeness

**Intent:** Every configured data source is accounted for — imported or explicitly flagged as unavailable. No silent skips.

**Flag when:** Unprocessed imports exist (`processed=0`), a configured source was not ingested at all, transaction count is materially different from prior period for the same source.

### 2. Trade Accounts Aging

**Intent:** All open receivables and payables are current or explained. Aging reveals collection problems, stale balances, and potential write-offs.

**Artifact:** Produce the aged schedule for each side with open items via `aged_trade_accounts.py --as_of {periodEnd} --output {workpapers_dir}/period-close/{periodLabel}/{periodLabel}-aged-receivables.csv` (and `--type payable` → `…-aged-payables.csv` when payable TAs exist). The report is point-in-time on both sides (origin JEs and payments bounded by `--as_of`), so its `subledger_total_cents` must equal the Check 11 subledger for the same control account — a difference means the two computations drifted; trace it before trusting either.

**Flag when:** Items 60+ days past due, same counterparty appears repeatedly in aged buckets, duplicate trade accounts for the same counterparty/date, unclosed fully-paid accounts, small remaining balances suggesting rounding issues. Groups netting below zero land in the report's `residual_credit` bucket — never age them as receivables; each must match a documented convention in `review-notes.md` or be flagged.

**Reading the report:** The due-date basis is client-specific (order day vs. payout date vs. invoice terms), so healthy in-flight groups can read "past due" by deposit lag, and settlements straddling period end can read gross until their closing credit posts. Load the client's aging-basis notes from `review-notes.md` before interpreting; record suppressed groups per the Suppression section.

**Scripts:** `aged_trade_accounts.py` for the grouped aging artifact; `list_open_items.py` for per-TA drill-down by status, type, and contact.

### 3. Balance Sheet Activity

**Intent:** Period activity on BS accounts is consistent with expectations. Unusual swings, new activity in dormant accounts, and structural anomalies surface here.

**Flag when:** Activity breaks established patterns — the agent common-sizes BS movements against revenue and compares to prior periods, flagging outliers. Large round-number postings, new activity in previously dormant accounts. The agent assigns severity (red/yellow) based on business context, not thresholds. **Skip prior-period variance in onboarding context.**

### 4. Income Statement Activity

**Intent:** Revenue and expenses are complete, correctly classified, and consistent with expectations. Missing recurring items and variance from prior periods indicate omissions or errors.

**Flag when:** The agent common-sizes all P&L accounts as a percentage of revenue and compares to prior period common-size. Accounts whose share of revenue shifted meaningfully, recurring items present in prior period that are absent, single large entries in typically-small-transaction accounts. Severity (red/yellow) assigned by agent reasoning, not thresholds. **Skip prior-period comparisons in onboarding context.**

### 5. Contra-Direction Postings

**Intent:** Catch the high-signal classification error — a posting that **reduces a revenue account** (a debit, or a negative-amount credit). Revenue reductions (refunds, discounts, chargebacks) belong in **dedicated contra-revenue / sales-adjustment accounts**, never in the revenue account itself, so a debit to a true revenue account is almost always a miscoding. **The expense side is not symmetric** — crediting an expense account is routinely legitimate (vendor refunds and rebates are netted back to the originating expense), so individual expense credits are *not* flagged here; a genuinely wrong one surfaces as net-credit period activity in **Check 6 (Opposite-Sign)**.

**Flag when:** a posting **reduces a revenue (income) account that is not a dedicated contra-revenue account** — test the **signed amount, not just the `direction` label** (a negative-amount credit to revenue is economically a debit — the common miss, e.g. a line discount booked as a negative credit to Gross Sales): `(direction=debit AND amount>0)` OR `(direction=credit AND amount<0)`. Every hit requires review.
- Dedicated contra-revenue / sales-adjustment accounts (refunds, discounts — listed in `review-notes.md`) are debit-normal; their debits are expected and **not** flagged.
- **Expense accounts:** do **not** flag routine credits (refunds/rebates netted to the originating account are normal); defer to **Check 6** for net-credit expense period activity. Surface an individual expense credit only if it's clearly income miscoded or otherwise anomalous (judgment).

Suppress only the **specific** known exceptions listed in `review-notes.md`; don't blanket-suppress an entire transaction class to quiet the check — a rare/one-time pattern is recorded as a named exception, not canonized into the rule.

### 6. Opposite-Sign Balances

**Intent:** Detect accounts whose balance runs against their normal sign. Often indicates a directional booking error, a reversal that wasn't offset, or an overcollection/overpayment.

**Flag when:** For BS accounts (asset, liability, equity), check the **closing balance** — a negative asset or positive liability (debit-direction) is the signal. Period activity can legitimately run either direction (e.g., A/R credits exceeding debits when payments outpace invoicing). For P&L accounts (income, expense), check **period activity** — income should be credit-normal, expense should be debit-normal within the period. Known exceptions from `review-notes.md` are suppressed.

### 7. Unbalanced Journal Entries

**Intent:** Safety net — every journal entry must have total debits equal to total credits. Scripts enforce this at creation time, so this check should never fire. If it does, something bypassed the journal engine.

**Flag when:** Any journal entry where sum of debit postings ≠ sum of credit postings. Every hit is a hard failure — investigate immediately.

### 8. Duplicate Postings

**Intent:** Detect double-booked entries — same transaction recorded twice creates overstatement. Common causes: re-import without dedup, manual JE duplicating an automated one.

**Flag when:** Multiple postings share the same date + amount + account code. Exclude legitimate recurrences by checking that the underlying imports have different external_ids, or that the journal entries have different source metadata.

### 9. Clearing Account Balances

**Intent:** Accounts designed to clear (payroll liabilities, suspense, intercompany, undeposited funds) should have zero or near-zero balances at period end. A nonzero balance means something didn't clear — either a missing entry or a timing issue.

**Flag when:** A designated clearing account has a nonzero closing balance for the period. Which accounts are clearing is client-specific — check `review-notes.md` for a `## Clearing Accounts` section. If none exists, default to accounts with "clearing", "suspense", or "payable" in their name where the expected behavior is periodic settlement.

### 10. Immaterial/Orphan Balances

**Intent:** Surface P&L accounts with trivial activity and BS accounts with trivial balances — these clutter financial statements and often indicate miscoding to an overly specific or rarely-used account. Candidates for reclassification to a broader parent.

**Flag when:** A P&L account has total period activity that the agent judges immaterial given the client's scale, or a BS account has a closing balance that appears orphaned (small, static, no clear purpose). The agent uses business context rather than a fixed threshold.

### 11. Subledger-to-GL Tie

**Intent:** The A/R subledger must tie to the A/R control account in the general ledger. The subledger is composed of all open trade_accounts that touch A/R: receivable invoices (positive) and credit_memo TAs (negative). Same idea on A/P: payable bills (positive) and vendor_credit TAs (negative). A mismatch means trade accounts and journal entries are out of sync.

**Flag when:** The GL balance of the A/R control account does not equal the sum of open `--type=receivable` remaining balance minus the sum of open `--type=credit_memo` remaining balance. Any difference is a hard failure. Same logic for A/P with `--type=payable` minus `--type=vendor_credit`. Account codes are client-specific — resolve via the chart of accounts.

**Note — compute CM/VC remaining correctly:** a credit_memo/vendor_credit is consumed through two legitimate forms: **credit applications** (TAPs with `source_ta_id` = the CM/VC, applied against a target invoice/bill) and **direct/owner-cleared settlement** (TAPs with `trade_account_id` = the CM/VC and no `source_ta_id` — used when the credit settles through a clearing/owner account because funds moved outside business accounts, or a vendor refund arrives as a bank deposit; the publisher's owner-cleared phase publishes exactly this shape). Remaining is `amount_due − compute_consumed_amount` (which sums both forms). Counting only one form makes the other read as permanently open credit — a phantom subledger-to-GL variance that does not exist in the system of record. Prefer `list_open_items.py` — it already dispatches by type.

### 12. Additional Local Steps

**Intent:** Extensibility hook for client-specific review procedures beyond the core checks.

**Behavior:** If `{local_dir}/content/review/additional-steps/` exists and is not empty, process files alphabetically. Each file defines its own review scope, flagging criteria, and recommended actions. Auto-skip if directory missing or empty.

---

## Suppression

Before flagging, check `review-notes.md` (loaded via precondition) for known exceptions. Items listed as expected behavior are suppressed — not flagged, not silently dropped. Record in the workpaper: "Suppressed: {description} per review-notes.md."

---

## Excel Review Package

After all checks complete, generate a review workbook for human review at `{workpapers_dir}/period-close/{periodLabel}/{periodLabel}-review.xlsx`.

**Script:** `export_to_excel.py` with `review_package` envelope type.

### Input Contract

```json
{
  "type": "review_package",
  "period_label": "{periodLabel}",
  "sheets": [
    {"type": "review_dashboard", "items": [...]},
    {"type": "transaction_register", "items": [...]},
    {"type": "subledger_gl_tie", "items": [...]},
    {"type": "pop_variance", "items": [...]}
  ]
}
```

### Tabs

**Dashboard** — One row per check. Columns: check name, status (pass/warn/fail), items flagged, notes. The reviewer reads this tab first to know if the close is clean. Include an informational row with the period's judgment-call count ("Judgment calls (confidence {jc_min}–{jc_max})") so the reviewer knows to filter the register for yellow rows.

**Transaction Register** — All period transactions in a single tab, visually segmented by account. Each account section: header row with account code + name, **opening balance row** (net Dr−Cr of all postings through the prior close), transaction rows (date, reference, contact, memo, debit, credit, **confidence**), closing balance row — closing = opening + period activity, the account's true as-of balance, not just period movement. Sorted by account code, then date within account. Each item carries `confidence_score` (the coding confidence from `postings`, null for non-coded rows: transfers, payment clearings, TA-origin and manual JEs). **Rows in the judgment range (`min_confidence_to_categorize` … `min_confidence_to_auto_approve − 1`) render highlighted yellow** — reviewers see judgment calls inline with surrounding transactions and can filter on the Confidence column. The export adapter applies the highlighting; the envelope producer's job is only to populate `confidence_score`.

**Subledger-to-GL Tie** — One row per subledger (A/R, A/P if applicable). Columns: description, subledger total, GL balance, difference, status.

**Period-over-Period Variance** — One row per account with activity in either period. Columns: account code, account name, type, prior period net, current period net, % of revenue (current), % of revenue (prior), shift, severity (red/yellow/blank). The agent common-sizes against revenue, assigns severity based on pattern recognition, and pre-filters to accounts worth reviewing. Sort by severity (red first), then absolute $ change descending.

---

## Workpaper Evidence

Record each check result in the workpaper Review section:

- **Per check:** Status (pass/warn/fail), count of flagged items, one-line narrative
- **Flagged items:** Description, affected account(s)/counterparty, amount, recommended action
- **Suppressed items:** Count and reference to review-notes.md
- **Excel package:** File path to generated workbook

The evidence must pass the manager test: someone reading the workpaper knows the review completed, what was flagged, and where to look for detail.
