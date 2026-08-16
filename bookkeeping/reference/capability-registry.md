# Bookkeeping -- Capability Registry

> This registry is for **discovery** -- what exists and when to use it. Always check the script's `--help` before invocation to guard against registry drift.

## Environment Convention

All scripts resolve their database and config paths via the shared `config_loader`, which reads from the `BOOKKEEPING_CONFIG_PATH` environment variable. This variable must point to the client project's `_local-bookkeeping/config.yaml`. Without it, every script will fail with `FileNotFoundError`.

```
export BOOKKEEPING_CONFIG_PATH=/path/to/project/_local-bookkeeping/config.yaml
```

## Tool Locations

**Tool locations:** §1–5 (Ingest…Journals) and §8 (Shared Libraries) live under `scripts/`. **§6 (Publishing) and §7 (Utilities) are adapters under `adapters/`** — invoke as `adapters/qbo/publish.py`, `adapters/export_to_excel.py`, `adapters/stripe_fc_transactions.py`, **not** `scripts/qbo/…`. Local/firm overrides resolve via the Adapter Resolution order below.

## Adapter Resolution

Adapters are discovered via resolution order (client overrides firm, firm overrides core):

1. **Local flat** -- `{local_dir}/adapters/{adapter_name}.py`
2. **Local dir** -- `{local_dir}/adapters/{adapter_name}/{adapter_name}.py`
3. **Firm flat** -- `{firm_root}/adapters/{adapter_name}.py`
4. **Firm dir** -- `{firm_root}/adapters/{adapter_name}/{adapter_name}.py`
5. **Shared flat** -- `{module_root}/adapters/{adapter_name}.py`
6. **Shared dir** -- `{module_root}/adapters/{adapter_name}/{adapter_name}.py`
7. **Error** -- if none found

If `{firm_root}` is not set, steps 3-4 are skipped. Core adapters documented below are in the shared directory. Client and firm projects may override or extend them.

---

## 1. Ingest

### ingest_universal.py

- **Purpose:** Reads universal JSON (file or stdin), validates, deduplicates by external_id, and bulk-inserts into the imports table.
- **Domain:** Ingest
- **Arguments:**
  - `--account_code` (str, required) -- Account code from chart_of_accounts.
  - `--file` (str, optional) -- Path to JSON file; reads stdin if omitted.
- **Input contract:** JSON envelope `{"transactions": [{external_id, date, amount, reference, balance_type, currency, raw_data?}, ...]}`. Amounts are integers (cents). balance_type is `"cash"` or `"credit"`. Dates are `YYYY-MM-DD`.
- **Output:** `{"success", "imported", "skipped", "batch_id", "source", "date_range"}`
- **Preconditions:** `account_code` must exist in `chart_of_accounts`. Input JSON must conform to the universal envelope contract.
- **Tables:** Reads `chart_of_accounts`; reads and writes `imports`.
- **When to use:** Every time bank/card/payment-processor data needs to enter the system. All source adapters (Stripe, CSV, etc.) produce universal JSON that pipes into this script.

### verify_ingest_balances.py

- **Purpose:** For each ingest account, computes postings_balance + unposted_imports and compares to a statement ending balance; reports PASS/WARN/FAIL per account.
- **Domain:** Ingest
- **Arguments:**
  - `--balances` (str, required) -- JSON mapping `account_code -> statement_balance_cents`. Inline JSON string or path to a JSON file.
  - `--db` (str, optional) -- Path to bookkeeping.db; defaults to config.
  - `--account` (str, optional) -- Single account_code to verify; defaults to all.
- **Output:** `{"success", "summary", "overall_status", "accounts": [{account_code, postings_balance, unposted_imports, computed_ending, statement_balance, difference, status}, ...]}`. Human-readable table to stderr.
- **Preconditions:** Imports must exist in the database. Statement balances must be known (e.g., from `stripe_fc_balances.py` or bank statement).
- **Tables:** Reads `imports`, `postings`, `chart_of_accounts`.
- **When to use:** After ingesting a period's transactions, to verify that (posted journals + remaining imports) reconciles to the bank statement ending balance.

### manage_bank_feeds.py

- **Purpose:** Sole owner of `{local_dir}/bank_feeds.yaml` -- the machine-maintained registry mapping `chart_of_accounts` codes to live bank-feed provider accounts (Stripe FC `fca_xxx`). Accounts in this registry are pulled live during Ingest instead of from files.
- **Domain:** Ingest
- **Arguments (subcommands):**
  - `add --account_code --provider_account_id --institution --last4 --category --subcategory [--provider stripe_fc] [--display_name]` -- Map a connected FC account to a COA code. Fails if either side is already mapped.
  - `list [--account_code] [--status active|disconnected|error]` -- List registry entries.
  - `update --account_code [--status] [--last_txn_refresh_id fctxnref_x] [--last_pulled_through YYYY-MM-DD]` -- Update an existing mapping.
  - `remap --account_code --provider_account_id fca_NEW [--institution] [--last4] [--subcategory] [--display_name]` -- Point an existing mapping at a new fca after re-auth. Preserves `last_pulled_through`, clears `last_txn_refresh_id`. Next pull MUST be date-bounded (see `reference/bank-feeds-troubleshooting.md`).
- **Output:** `add`: `{"success", "account_code", "account_name", "provider_account_id", "registry_path"}`. `list`: `{"success", "count", "registry_path", "accounts": [...]}`. `update`: `{"success", "account_code", "updated_fields", "registry_path"}`. `remap`: `{"success", "account_code", "old_provider_account_id", "new_provider_account_id", "preserved_last_pulled_through", "registry_path"}`.
- **Preconditions:** `account_code` must exist in `chart_of_accounts`. Connections are established first via `operations/connect-bank-feeds.md` (AMA bundle → `adapters/ama_client.py`).
- **Tables:** Reads `chart_of_accounts`; owns `{local_dir}/bank_feeds.yaml` (file, not a DB table).
- **When to use:** After a client connects accounts through an AMA bundle, to record the fca↔account-code mapping; during period close to resolve which accounts pull live; after pulls to record incremental-sync state.

---

## 2. Categorize

### apply_cat_rules.py

- **Purpose:** Runs all active categorization rules against unprocessed imports in priority order (first match wins) and creates journal entries via `journal_engine`.
- **Domain:** Categorize
- **Arguments:** None (no CLI arguments).
- **Output:** `{"success", "processed", "unmatched", "failed", "errors": [{import_id, error}, ...]}`
- **Preconditions:** Rules must exist in `categorization_rules` with `active=1`. Unprocessed imports (`processed=0`) must exist.
- **Tables:** Reads `categorization_rules`, `imports`, `chart_of_accounts`, `contacts`, `tags`; writes `journal_entries`, `postings`, updates `imports.processed`.
- **When to use:** After ingest, to auto-categorize as many imports as possible before falling through to AI categorization.

### bulk_cat_transactions.py

- **Purpose:** Creates double-entry journal entries from AI-generated categorizations; marks low-confidence items as `processed=2` (client question) without creating journal entries.
- **Domain:** Categorize
- **Arguments:**
  - `--categorizations` (str/JSON, required) -- JSON array of categorization objects: `[{import_id, postings: [{account_code, contact, tags?, amount?, percentage?, direction?, description?}], confidence_score, class_name?}, ...]`.
- **Output:** `{"success", "processed", "failed", "errors", "journal_entry_ids"}`
- **Preconditions:** Referenced import IDs must be unprocessed (`processed=0`). Account codes and tags must exist. Confidence threshold from `config.yaml` `coding.min_confidence_to_categorize`.
- **Tables:** Reads `imports`, `chart_of_accounts`, `contacts`, `tags`; writes `journal_entries`, `postings`; updates `imports.processed`.
- **When to use:** After the AI agent has analyzed uncategorized transactions and produced categorization suggestions. This is the primary path for AI-driven bookkeeping.

### test_cat_rule.py

- **Purpose:** Dry-run simulator that tests a categorization rule against already-processed historical imports and compares simulated postings to actual postings (three-tier: account match, contact match, tag match).
- **Domain:** Categorize
- **Arguments:**
  - `--rule_id` (str, required) -- UUID of the categorization rule to test.
  - `--start_date` (str, optional) -- Start date filter `YYYY-MM-DD`.
  - `--end_date` (str, optional) -- End date filter `YYYY-MM-DD`.
  - `--source_filter` (str, optional) -- Source partial match filter.
- **Output:** `{"success", "rule_id", "rule_name", "total_imports_tested", "rule_matches", "accurate_predictions", "partial_matches", "mismatches", "accuracy_percentage", "warning", "details": [...]}`
- **Preconditions:** The rule must exist in `categorization_rules`. Processed imports (`processed=1`) must exist for meaningful results.
- **Tables:** Reads `categorization_rules`, `imports`, `postings`, `journal_entries`.
- **When to use:** Before activating a new or modified rule, to validate its accuracy against historical data.

---

## 3. Trade Accounts

### create_trade_account.py

- **Purpose:** Creates a trade account (A/R or A/P) linked to a journal entry, with contact auto-creation and audit logging.
- **Domain:** Trade Accounts
- **Arguments:**
  - `--type` (str, required) -- `receivable` or `payable`.
  - `--contact` (str, required) -- Entity name (customer or vendor).
  - `--document_date` (str, required) -- Document/transaction date `YYYY-MM-DD`.
  - `--due_date` (str, optional) -- Due date `YYYY-MM-DD`; defaults to document_date.
  - `--journal_entry_id` (str, required) -- UUID of the linked journal entry.
  - `--balance_account_code` (str, required) -- The A/R or A/P account code (stored in metadata).
  - `--metadata` (str/JSON, optional) -- Additional metadata JSON, merged with auto-generated fields.
  - `--changed_by` (str, optional) -- Audit log attribution; defaults to `create_trade_account.py`.
- **Output:** `{"success", "trade_account_id", "type", "contact", "document_date", "due_date", "journal_entry_id", "balance_account_code"}`
- **Preconditions:** The referenced `journal_entry_id` must exist. The `balance_account_code` must exist in `chart_of_accounts`.
- **Tables:** Reads `journal_entries`, `chart_of_accounts`; writes `trade_accounts`, `contacts` (auto-create), `audit_log`.
- **When to use:** After creating a journal entry that represents an invoice or bill -- to track the receivable/payable lifecycle (open, partial, paid).

### create_credit_memo.py

- **Purpose:** Creates a Credit Memo (contra-receivable trade account + originating JE) in one transaction. Lines DR contra/return accounts, balance CRs A/R.
- **Domain:** Trade Accounts
- **Arguments:**
  - `--contact` (str, required) -- Customer name.
  - `--document_date` (str, required) -- `YYYY-MM-DD`.
  - `--document_number` (str, optional) -- CM document number; written to metadata.
  - `--balance_account_code` (str, required) -- A/R account code.
  - `--lines` (str/JSON, required) -- `[{"account_code","amount_cents","class?","description?"}, ...]`.
  - `--memo` (str, optional) -- JE memo.
  - `--metadata` (str/JSON, optional) -- Additional TA metadata.
  - `--changed_by` (str, optional) -- Defaults to `create_credit_memo.py`.
- **Output:** `{"success", "trade_account_id", "journal_entry_id", "amount_due_cents", "type": "credit_memo"}`
- **Preconditions:** Account codes exist; contact auto-created if missing.
- **Tables:** Writes `journal_entries`, `postings`, `trade_accounts`, `contacts` (auto-create), `audit_log`.
- **When to use:** To issue a customer credit (return, promotional credit, damage resolution) that will be applied to one or more invoices via `apply_credit.py`.

### create_vendor_credit.py

- **Purpose:** Creates a Vendor Credit (contra-payable trade account + originating JE). Lines CR contra accounts, balance DRs A/P.
- **Domain:** Trade Accounts
- **Arguments:** Mirrors `create_credit_memo.py` but `--contact` is a vendor and `--balance_account_code` is an A/P account code.
- **Output:** `{"success", "trade_account_id", "journal_entry_id", "amount_due_cents", "type": "vendor_credit"}`
- **Preconditions:** Account codes exist; contact auto-created if missing.
- **Tables:** Writes `journal_entries`, `postings`, `trade_accounts`, `contacts`, `audit_log`.
- **When to use:** To record a vendor refund/rebate that will be applied to one or more bills via `apply_credit.py`.

### list_open_items.py

- **Purpose:** Lists trade accounts with computed amount_due, paid_amount, remaining_balance, and status (unpaid/partial/paid).
- **Domain:** Trade Accounts
- **Arguments:**
  - `--type` (str, optional) -- Filter: `receivable`, `payable`, `credit_memo`, or `vendor_credit`.
  - `--contact` (str, optional) -- Filter by contact name.
  - `--status` (str, optional) -- Filter: `unpaid`, `partial`, or `paid`.
  - `--include_voided` (flag, optional) -- Include voided trade accounts.
- **Output:** `{"success", "count", "totals_by_type": {<type>: {count, amount_due, paid_amount, remaining_balance}}, "filters", "items": [{id, type, contact, document_date, due_date, amount_due, paid_amount, remaining_balance, status, ...}, ...]}`. CM/VC remaining nets BOTH consumption forms — applications (`source_ta_id`) and direct/owner-cleared TAPs — via `compute_consumed_amount`.
- **Preconditions:** Trade accounts must exist.
- **Tables:** Reads `trade_accounts`, `trade_account_payments`, `postings`, `journal_entries`.
- **When to use:** To review outstanding receivables/payables, check aging, or find trade accounts that need payment application.

### aged_trade_accounts.py

- **Purpose:** Point-in-time aged schedule of open trade accounts, rolled up by counterparty + document group (settlement/payout/invoice via a metadata key chain), aged by group due date, past-due first. Net-negative groups are segregated as `residual_credit` — surfaced, never aged. The formal artifact for Review Check 2; read-only (opens the DB in SQLite `mode=ro`).
- **Domain:** Trade Accounts / Review
- **Arguments:**
  - `--as_of` (str, required) -- Report date `YYYY-MM-DD`. Bounds BOTH the open set (origin JE date ≤ as_of) and payments/applications (payment_date ≤ as_of), matching Check 11 subledger math.
  - `--type` (str, optional) -- `receivable` (with credit_memos, default) or `payable` (with vendor_credits).
  - `--contact` (str, optional) -- Filter by contact name.
  - `--group_keys` (str, optional) -- Comma-separated metadata keys tried in order to form groups. Default `settlement_id,payout_id,order_num,invoice_num,doc_number`; falls back to per-TA groups.
  - `--output` (str, optional) -- CSV artifact path (open groups + residual credits, `bucket` column distinguishes).
- **Output:** `{"success", "as_of", "type", "group_keys", "balance_accounts", "open_groups", "open_total_cents", "past_due_groups", "past_due_total_cents", "residual_credit_groups", "residual_credit_total_cents", "subledger_total_cents", "by_source", "excluded_count", "csv"}`. `subledger_total_cents` (open + residual) must tie to the Check 11 subledger. TAs missing a balance-account posting are excluded LOUDLY (stderr warning JSON + `excluded_count`).
- **Preconditions:** Trade accounts with `balance_account_code` in metadata. CM/VC netting spans BOTH consumption forms (applications via `source_ta_id` + direct/owner-cleared TAPs), matching `compute_consumed_amount` and the publisher — so the report cannot manufacture a gap that does not exist in the SoR.
- **Tables:** Reads `trade_accounts`, `journal_entries`, `postings`, `trade_account_payments`.
- **When to use:** During Review Check 2 each close (`--as_of {periodEnd}`), or any time an aging/collections view is needed. Pair with `list_open_items.py` to drill into a flagged group's member TAs.

---

## 4. Payments

### apply_payment.py

- **Purpose:** Applies a bank transaction payment to a single trade account; creates a clearing journal entry (DR Bank/CR A/R or DR A/P/CR Bank) and a payment record.
- **Domain:** Payments
- **Arguments:**
  - `--trade_account_id` (str, required) -- Trade account ID to apply payment to.
  - `--import_id` (str, mutually exclusive with `--payment_account`) -- Bank transaction import ID (derives cash account, marks import processed).
  - `--payment_account` (str, mutually exclusive with `--import_id`) -- Non-cash account code for clearing (e.g., intercompany, clearing account).
  - `--amount` (int, required) -- Payment amount in cents.
  - `--payment_date` (str, optional) -- `YYYY-MM-DD`; defaults to today.
  - `--changed_by` (str, optional) -- Audit log changed_by value; defaults to `apply_payment.py`.
- **Output:** `{"success", "payment_id", "clearing_je_id", "trade_account_id", "amount_applied", "amount_due", "prior_payments", "new_paid_amount", "remaining_balance", "status"}`
- **Preconditions:** Trade account must exist and not be voided. If `--import_id`, import must be unprocessed. Payment must not exceed remaining balance. Trade account must have `balance_account_code` in metadata.
- **Tables:** Reads `trade_accounts`, `imports`, `postings`, `trade_account_payments`, `chart_of_accounts`; writes `journal_entries`, `postings`, `trade_account_payments`, `audit_log`; updates `imports.processed`, `journal_entries.sync` (marks clearing JE as `ignore`).
- **When to use:** When a bank deposit/withdrawal corresponds to a receivable or payable -- to close out the trade account and clear the bank import.

### apply_payments_bulk.py

- **Purpose:** Applies a single bank import to multiple trade accounts in one atomic operation; creates a compound clearing journal entry whose bank line matches the settlement cash. Mixed-payment catchall: invoices/bills/CMs/VCs settle via the clearing JE; standalone co-disbursements split off into their own publishable JE.
- **Domain:** Payments
- **Arguments:**
  - `--import_id` (str, required) -- Bank import ID.
  - `--payments` (str/JSON) -- JSON array: `[{"trade_account_id": "...", "amount": 1000}, ...]`. Required unless `--auto_resolve_settlement`.
  - `--standalone_lines` (str/JSON, optional) -- Postings that ride the same wire but are NOT part of any settlement (a co-disbursement to another party): `[{"account_code", "amount"(int cents), "direction", "contact?", "description?", "class_name?"}, ...]`. Each publishes as its OWN QBO JournalEntry; triggers a signed import-split (clearing JE keeps the settlement cash; a second `pending` JE carries these lines + a balancing bank slice; the two bank slices net to the import). Defaults to `[]`.
  - `--adjustments` (str/JSON, optional) -- DEPRECATED; a non-empty value is now a hard error. Use `--standalone_lines` for a co-disbursement, or a first-class credit_memo/vendor_credit batched via `--allow_mixed_credit` for a settlement-reducing credit (fee/short-pay/FX).
  - `--auto_resolve_settlement`, `--settlement_id`, `--allow_mixed_credit` (optional) -- resolve TAs from `metadata.settlement_id`; `--allow_mixed_credit` permits credit_memo/vendor_credit TAs so the deposit closes the net (sum_R − sum_CM).
  - `--payment_date` (str, required) -- `YYYY-MM-DD`.
  - `--changed_by` (str, optional) -- Defaults to `apply_payments_bulk.py`.
- **Output:** `{"success", "clearing_je_id", "standalone_je_id", "import_id", "import_total_cents", "clearing_bank_cents", "standalone_bank_cents", "payments_created", "payments": [...], "credit_apps_created", "credit_apps": [...]}`. `standalone_je_id` is null when no standalone lines.
- **Preconditions:** Import must be unprocessed. All trade accounts must exist, not be voided, and have sufficient remaining balance. Each JE must balance; standalone lines must leave settlement cash in the wire's direction (else use `create_manual_journal.py`).
- **Tables:** Reads `imports`, `trade_accounts`, `trade_account_payments`, `postings`, `chart_of_accounts`; writes `journal_entries` (clearing JE `sync=ignore`; optional standalone JE `sync=pending`), `postings`, `trade_account_payments`, `audit_log`; updates `imports.processed`.
- **When to use:** When a single bank import covers multiple invoices/bills (bulk-allocate), optionally with a standalone co-disbursement that shared the wire. For a fee/short-pay/FX credit that reduces a settlement, create a first-class CM/VC and batch it with `--allow_mixed_credit`.

---

## 5. Journals

### apply_transfer.py

- **Purpose:** Links multiple imports from different feeds into a single balanced journal entry for inter-account transfers (e.g., paying a credit card from the bank).
- **Domain:** Journals
- **Arguments:**
  - `--transfer` (str/JSON, required) -- JSON object: `{primary_import_id, secondary_import_ids: [...], transaction_date, memo, postings: [{account_code, direction, amount, contact?, description?}, ...], class_name?}`.
- **Output:** `{"success", "journal_entry_id", "imports_processed"}`
- **Preconditions:** All referenced imports must exist and not be `processed=1` (accepts `processed=0` and `processed=2`). Postings must balance. Account codes must exist.
- **Tables:** Reads `imports`, `chart_of_accounts`, `contacts`, `tags`; writes `journal_entries`, `postings`; updates `imports.processed`.
- **When to use:** When a bank withdrawal and a credit card payment are two sides of the same transfer -- link them into one balanced JE so both imports are resolved.

### apply_credit.py

- **Purpose:** Applies a CM/VC trade account to a target invoice/bill. Inserts a single TAP row with `source_ta_id` set; **no clearing JE** — the application is a sub-ledger reallocation, not a GL movement.
- **Domain:** Payments
- **Arguments:**
  - `--source_ta_id` (str, required) -- CM or VC trade account ID.
  - `--target_ta_id` (str, required) -- Receivable invoice or payable bill ID.
  - `--amount_cents` (int, required) -- Application amount in cents.
  - `--application_date` (str, required) -- `YYYY-MM-DD`.
  - `--changed_by` (str, optional) -- Defaults to `apply_credit.py`.
- **Output:** `{"success", "tap_id", "source_ta_id", "target_ta_id", "amount_applied_cents", "source_remaining_cents", "target_remaining_cents"}`
- **Preconditions:** Source type ∈ {credit_memo, vendor_credit}; target type matches (CM↔receivable, VC↔payable); same contact on both; neither voided; amount within both remaining balances.
- **Tables:** Reads `trade_accounts`, `trade_account_payments`, `postings`; writes `trade_account_payments`, `audit_log`. **Does not** write to `journal_entries` or `postings`.
- **When to use:** To apply a CM to one or more invoices, or a VC to one or more bills. Can be called multiple times to split a credit across multiple targets.

### migrate_add_credit_types.py

- **Purpose:** One-shot schema migration adding first-class CM/VC support: extends `trade_accounts.type` CHECK to include `credit_memo`/`vendor_credit`, adds nullable `source_ta_id` column to `trade_account_payments` with index. Backs up the DB before running.
- **Domain:** Utilities (one-time)
- **Arguments:** None.
- **Output:** `{"migrated": bool, "backup_path", "ta_count", "tap_count", "db_path"}`. On no-op re-run: `{"migrated": false, "reason": "already current"}`.
- **Preconditions:** `bookkeeping.db` exists at the path resolved by `BOOKKEEPING_CONFIG_PATH`.
- **Tables:** Recreates `trade_accounts` (CHECK extension via SQLite table-recreation pattern); ALTERs `trade_account_payments` to add `source_ta_id`. Verifies row counts match pre-recreation.
- **When to use:** Once per `bookkeeping.db` before using `create_credit_memo.py` / `create_vendor_credit.py` / `apply_credit.py`. Idempotent re-runs are safe.

### create_manual_journal.py

- **Purpose:** Creates manual journal entries (adjustments, accruals, reclassifications) with pre-built postings. CLI wrapper for `journal_engine.create_journal_entry_direct()`.
- **Domain:** Journals
- **Arguments:**
  - `--date` (str, required) -- Transaction date `YYYY-MM-DD`.
  - `--memo` (str, required) -- Journal entry description/memo.
  - `--postings` (str/JSON, required) -- JSON array of posting objects: `[{account_code, direction, amount, contact, description, class_name?}, ...]`.
  - `--class_name` (str, optional) -- QBO Class name (validated against tags table with `category='Class'`).
- **Output:** `{"success", "journal_entry_id", "posting_count", "total_debits", "total_credits"}`
- **Preconditions:** Debits must equal credits. All account codes must exist. Class name (if provided) must exist in tags with `category='Class'`. Contacts are auto-created if they don't exist.
- **Tables:** Reads `chart_of_accounts`, `tags`, `contacts`; writes `journal_entries`, `postings`, `contacts` (auto-create).
- **When to use:** For any journal entry not driven by a bank import -- adjusting entries, accruals, reclassifications, opening balances, depreciation entries, etc.

---

## 6. Publishing

### adapters/qbo/publish.py

- **Purpose:** Publishes journal entries, invoices (receivable TAs), bills (payable TAs), credit memos, vendor credits, credit applications, bank-funded payments/bill-payments, and owner-cleared CM/VC applications to QuickBooks Online. Also handles inline payment adjustments (chargebacks, damages) via the legacy CM/VC inline path in `_publishers/payments.py` and `bill_payments.py`. Thin orchestrator — business logic in `qbo/_publishers/`.
- **Domain:** Publishing
- **Arguments:**
  - `--sync_status` (str, optional) -- Filter by sync status: `pending` (default) or `error`.
  - `--start_date` (str, optional) -- Start date filter `YYYY-MM-DD`.
  - `--end_date` (str, optional) -- End date filter `YYYY-MM-DD`.
  - `--dry_run` (flag, optional) -- Validate OAuth and mappings without publishing.
  - `--publish_type` (str, optional) -- What to publish: `all` (default), `jes`, `trade_accounts` (now covers invoices, bills, credit memos, vendor credits, **and** credit applications), `payments` (also includes credit applications and owner-cleared applications), or `owner_cleared` (only the owner-cleared phase).
- **Output:** `{"success", "dry_run", "jes": {...}, "invoices": {...}, "bills": {...}, "credit_memos": {...}, "vendor_credits": {...}, "credit_applications": {...}, "payments": {...}, "bill_payments": {...}, "owner_cleared": {...}, "errors", "external_ids", "date_range"}`
- **Phases (in order):** 1) JEs, 2) Invoices + Bills, 2b) Credit Memos + Vendor Credits (standalone documents), 2c) Credit Applications (zero-amount Payment/BillPayment with two LinkedTxns), 3) Bank-funded Payments + BillPayments (filtered to `source_ta_id IS NULL` to exclude credit-funded TAPs; also excludes payouts carrying a CM-consume TAP — those route to 3b), **3b) Payout-consumed credits** (a bank-funded settlement/payout whose lines include a `credit_memo`-typed TAP — e.g. a Shopify chargeback consumed within the deposit: emits ONE consolidated mixed-Line Payment per `payout_id`, N invoice + M credit-memo lines, `TotalAmt = Σgross − ΣCM`, reusing the settlement mixed-Line builder, so the bank nets and the CM applies — never gross singletons + a floating CM), 3c) Owner-cleared CM/VC applications (TAPs with no import_id and no source_ta_id whose parent is a credit_memo/vendor_credit: publishes the backing clearing JE — or reuses Phase 1's external_id — plus a zero-amount Payment/BillPayment linking the floating CM/VC to the JE's sub-ledger charge, so A/R/A/P aging stays clean, not just the trial balance).
- **Robustness (2026-06-10):** Every save is retry-safe: a `10000`/`6140`/`6240` fault triggers a read-back (`_shared/locate.py`) that links the already-posted object by its `[bk:<local-id>]` idempotency tag (stamped in PrivateNote; also DocNumber when the adapter provides none — QBO's duplicate-DocNumber enforcement then hard-blocks double-posts). Ambiguous/inconclusive read-backs route the row to sync status **`verify`** — selected by NEITHER `--sync_status pending` nor `error`; a human must check QBO and either set the real external_id or reset the row to pending. Long runs survive token expiry: a `ClientHolder` (`_shared/auth.py`) proactively refreshes the access token every ~40 min and retries once on a typed 401; an expired refresh token fails fast/loud (`AUTH_DEAD`, rows stay retryable after re-auth).
- **Preconditions:** QBO OAuth credentials in `{local_dir}/adapters/.env`. All account codes must have `remote_id` in `chart_of_accounts`. Contacts on A/R lines need Customer type; A/P lines need Vendor type. For VC applications (credit-app or owner-cleared): `qbo_default_bank_remote_id` must be set in config (BillPayment.CheckPayment.BankAccountRef is required even at TotalAmt=0).
- **Tables:** Reads `journal_entries`, `postings`, `chart_of_accounts`, `contacts`, `tags`, `trade_accounts`, `trade_account_payments`; updates `sync` on `journal_entries`, `trade_accounts`, `trade_account_payments`.
- **When to use:** After a period is fully categorized and reviewed -- push everything to QBO. Always run `--dry_run` first to catch mapping errors. Supports file-based concurrency lock. After a failed run, re-publish with `--sync_status error` is safe (locate prevents double-posts); rows in status `verify` must be resolved by hand first.

### adapters/qbo/sync_contacts.py

- **Purpose:** Creates QBO Customers/Vendors from local contacts. Classifies by posting context (A/R → Customer, A/P → Vendor). Splits dual-use contacts into separate Customer and "(Vendor)" entities.
- **Domain:** Publishing
- **Arguments:**
  - `--dry_run` (flag, optional) -- Report planned actions without creating.
- **Output:** `{"success", "dry_run", "company", "contacts_analyzed", "customers_created", "customers_existing", "vendors_created", "vendors_existing", "dual_use_splits", "skipped", "errors", "details"}`
- **Preconditions:** QBO OAuth credentials in `{local_dir}/adapters/.env`. Contacts with postings in local DB.
- **Tables:** Reads `contacts`, `postings`, `chart_of_accounts`, `trade_accounts`; writes `contacts` (`remote_id`, `meta`); may insert split vendor contacts and update `postings`/`trade_accounts` references.
- **When to use:** Before publishing invoices/bills. Run `--dry_run` first to review classifications.

### adapters/qbo/reconcile_trial_balance.py

- **Purpose:** Verifies the local DB is faithfully published to QBO **as of a date**, with zero variances -- RE-rollup-aware (sees through QBO's automatic prior-year P&L -> Retained Earnings sweep). Balance-sheet/equity compared cumulatively; P&L per-account against the non-rolling ProfitAndLoss report (prior fiscal years + current fiscal year); the RE sweep is *derived* as a check, never assumed.
- **Domain:** Publishing
- **Arguments:**
  - `--as_of_date` (str, required; alias `--end_date`) -- Reconciliation frontier `YYYY-MM-DD`. Pick the frontier you have published through; activity dated `<= as_of` but unpublished correctly FAILs.
  - `--fiscal_year_start` (str, optional, default `January`) -- Client's fiscal-year start month (name or 1-12). The agent supplies this; research via QBO `Preferences.AccountingInfoPrefs.FirstMonthOfFiscalYear` or client config if unknown. A wrong value cannot cause a false PASS -- it surfaces as a Retained-Earnings check failure.
  - `--start_date` (str, optional) -- Also report P&L activity over `[start_date, as_of]` as a focused secondary section.
  - `--retained_earnings_code` (str, optional) -- Local Retained Earnings account code; if omitted, auto-detects equity accounts named "Retained Earnings".
- **Output:** `{"success", "as_of_date", "fiscal_year_start", "current_fy", "prior_years", "checks": {balance_sheet, other_equity, prior_years_pl, current_fy_pl, retained_earnings_sweep, parse_validation, unmapped_qbo_accounts}, "period_activity", "summary"}`. Each P&L pull self-validates (per-account leaf sum must reconstruct the report's Net Income, else FAIL).
- **Preconditions:** QBO OAuth credentials in `{local_dir}/adapters/.env`. Everything intended for QBO through `as_of_date` must be published.
- **Tables:** Reads `journal_entries`, `postings`, `chart_of_accounts`.
- **When to use:** Both ends of the SoR↔staging reconciliation bracket. **Opening (before Ingest):** run `--as_of {priorCloseDate}` to catch drift since the last seal — a direct SoR edit/reclass or an unpublished prior entry (Hard Stop 6 in `quality-guidelines.md`). **Closing (after `adapters/qbo/publish.py` completes):** run `--as_of {periodEnd}` to prove the just-committed period landed with zero variance. PASS = local faithfully published as of the date. Any variance indicates a publish error, mapping issue, direct SoR change, or unpublished activity dated `<= as_of` that must be resolved before the period can close. Pair the pre-Publish step with `scan_sor_direct_records.py` (closing direct-record detect).

### adapters/qbo/scan_sor_direct_records.py

- **Purpose:** Closing bracket of the SoR↔staging reconciliation — a PRE-PUBLISH, READ-ONLY scan that finds QBO transaction records dated in the period that lack the publisher's `[bk:<key>]` idempotency tag (stamped in PrivateNote, and DocNumber on documents — see `_shared/locate.py`). Untagged = created directly in QBO (manual entry, bank feed), a system-generated artifact (e.g. an AutoApplyCredit zero-$ Payment), or a failed sync — i.e. records staging never created. Surfacing them before Publish prevents the publisher from posting a duplicate of something the SoR already holds.
- **Domain:** Publishing
- **Arguments:**
  - `--period_start` (str, required) -- Period start `YYYY-MM-DD` (inclusive TxnDate floor).
  - `--period_end` (str, required) -- Period end `YYYY-MM-DD` (inclusive TxnDate ceiling).
  - `--entity_types` (str, optional) -- Comma-separated subset to scan. Default scans all: JournalEntry, Invoice, Bill, CreditMemo, VendorCredit, Payment, BillPayment (the types the publisher tags), plus Deposit and Purchase (never created by the publisher, so any in-window is inherently a direct record).
- **Output:** `{"success", "period", "entity_types_scanned", "scanned_counts_by_type", "untagged_count", "untagged_records": [{type, id, txn_date, amount, name_or_memo}], "summary"}`. `success` = scan completed AND zero untagged records (closing gate clear). Exit 0 = gate clear; exit 1 = untagged records found (Hard Stop 7) or scan error (then an `error` key is present). PrivateNote is not queryable in QBO, so the scan queries each entity over the TxnDate window and matches the tag client-side, paging to exhaustion (a truncated page would read as a false "clean").
- **Preconditions:** QBO OAuth credentials in `{local_dir}/adapters/.env`. No local DB required.
- **Tables:** None — reads QBO only; performs no QBO or local-DB writes (OAuth token rotation in `.env` is the shared client housekeeping, as with every QBO adapter).
- **When to use:** Before every Publish (Hard Stop 7 in `quality-guidelines.md`). Resolve every surfaced record — reconcile it into staging, account for it, or neutralize a colliding artifact — before publishing; never publish over an untagged in-period record. Complements `reconcile_trial_balance.py` (the as-of value tie at both period ends).

### adapters/qbo/sync_coa.py

- **Purpose:** Pulls the account list from QuickBooks Online and upserts to the local `chart_of_accounts` table, mapping QBO AccountType to simplified types.
- **Domain:** Publishing
- **Arguments:**
  - `--dry_run` (flag, optional) -- Show what would be synced without making changes.
- **Output:** `{"success", "dry_run", "accounts_fetched", "inserted", "updated", "skipped", "details?"}`
- **Preconditions:** QBO OAuth credentials in `{local_dir}/adapters/.env`.
- **Tables:** Reads and writes `chart_of_accounts`.
- **When to use:** During initial client setup or when the chart of accounts changes in QBO. Run before publishing to ensure local accounts have `remote_id` mappings.

### adapters/qbo/sync_classes.py

- **Purpose:** Pulls the class list from QBO and upserts to the local `tags` table with `category='Class'`.
- **Domain:** Publishing
- **Arguments:**
  - `--dry_run` (flag, optional) -- Show what would be synced without making changes.
- **Output:** `{"success", "dry_run", "classes_fetched", "inserted", "updated", "skipped", "details?"}`
- **Preconditions:** QBO OAuth credentials in `{local_dir}/adapters/.env`.
- **Tables:** Reads and writes `tags`.
- **When to use:** During initial client setup or when QBO classes change. Run before publishing to ensure local classes have `remote_id` mappings.

---

## 7. Utilities

### adapters/export_to_excel.py

- **Purpose:** Transforms universal JSON into formatted `.xlsx` workbooks. Supports single-sheet and multi-sheet modes. Pure format transform -- no database operations.
- **Domain:** Utilities
- **Arguments:**
  - `--input` (str, optional) -- Path to JSON file; reads stdin if omitted.
  - `--output` (str, required) -- Output path for the Excel file (`.xlsx`).
  - `--template` (str, optional) -- Path to Excel template for client branding (not yet implemented).
- **Input contract (single-sheet):** JSON with `{type, items, period_label?, count?}`. Supported types: `client_questions`, `judgment_calls`, `review_dashboard`, `transaction_register`, `subledger_gl_tie`, `pop_variance`.
- **Input contract (multi-sheet):** JSON with `{type: "review_package", period_label, sheets: [{type, items}, ...]}`. Each sheet entry uses a supported single-sheet type. Creates one tab per sheet entry.
- **Output:** `{"success", "output_file", "type", "rows", "sheets": [sheet_names]}`
- **Preconditions:** Input JSON must have `type` field. Single-sheet types require `items` array. `review_package` requires `sheets` array. Requires `openpyxl` package.
- **Tables:** None (pure file transform).
- **When to use:** To generate formatted Excel deliverables -- client questions, judgment calls, or the multi-tab review package produced during the Review domain.

### adapters/stripe_fc_transactions.py

- **Purpose:** Fetches transactions from a Stripe Financial Connections account, transforms to universal JSON, and outputs to stdout for piping to `ingest_universal.py`.
- **Domain:** Utilities (Source Adapter)
- **Arguments:**
  - `--account_id` (str, required) -- Stripe FC account ID (`fca_xxx`).
  - `--start_date` (str, optional) -- Filter transactions on or after `YYYY-MM-DD`.
  - `--end_date` (str, optional) -- Filter transactions on or before `YYYY-MM-DD`.
  - `--include_pending` (flag, optional) -- Include pending transactions (default: posted only).
  - `--after_refresh` (str, optional) -- Stripe TransactionRefresh ID (`fctxnref_…`, from `stripe_fc_refresh.py` read mode) for incremental sync.
  - `--balance_type` (str, optional) -- Override: `cash` or `credit`. Fallback if account subcategory unavailable.
- **Output:** Universal JSON envelope: `{"transactions": [{external_id, date, amount, reference, balance_type, currency, raw_data}, ...]}`. Pipe to `ingest_universal.py`.
- **Preconditions:** `STRIPE_API_KEY` in `{local_dir}/adapters/.env`. Requires `stripe` package.
- **Tables:** None (outputs to stdout).
- **When to use:** To pull bank/card transactions from Stripe FC before ingesting. Pull **to a file**, inspect the count, then ingest via `ingest_universal.py --account_code XXX --file …` — an empty pull exits 1 by design (it's a finding, not an ingest input).

### adapters/stripe_fc_balances.py

- **Purpose:** Refreshes and returns balance data for a Stripe Financial Connections account. Used by agent/quality gate logic, not piped to ingest.
- **Domain:** Utilities (Source Adapter)
- **Arguments:**
  - `--account_id` (str, required) -- Stripe FC account ID (`fca_xxx`).
  - `--refresh` (flag, optional) -- Trigger a balance refresh (fire-and-forget).
- **Output (read mode):** `{"success", "account_id", "balance": {current, available, used, as_of, type}, "currency", "refresh_status", "next_refresh_available_at"}`
- **Output (refresh mode):** `{"success", "account_id", "refresh_triggered", "refresh_status", "balance": null}`
- **Preconditions:** `STRIPE_API_KEY` in `{local_dir}/adapters/.env`. Requires `stripe` package.
- **Tables:** None.
- **When to use:** To get statement ending balances for `verify_ingest_balances.py`. Typical flow: `--refresh`, wait, then read. For a combined transactions+balance refresh (preferred before period-close pulls), use `stripe_fc_refresh.py --trigger` instead.

### adapters/stripe_fc_refresh.py

- **Purpose:** Triggers transactions and/or balance refreshes for a Stripe FC account (fire-and-forget) and reads refresh statuses, including the `transaction_refresh.id` needed for incremental pulls. Refreshing both features together keeps the transaction list and balance snapshot coherent for verification.
- **Domain:** Utilities (Source Adapter)
- **Arguments:**
  - `--account_id` (str, required) -- Stripe FC account ID (`fca_xxx`).
  - `--trigger` (flag, optional) -- Trigger a refresh (fire-and-forget; the agent paces the wait).
  - `--features` (str, optional) -- Comma-separated refresh features: `transactions`, `balance` (SINGULAR -- the session-permission spelling `balances` is invalid here), `ownership`. Default `transactions,balance`.
- **Output (trigger):** `{"success", "account_id", "refresh_triggered", "features", "refresh_status": "pending"}`
- **Output (read):** `{"success", "account_id", "account_status", "subscriptions", "balance_refresh": {status, last_attempted_at, next_refresh_available_at}, "transaction_refresh": {id, status, last_attempted_at, next_refresh_available_at}}`
- **Preconditions:** `STRIPE_API_KEY` in `{local_dir}/adapters/.env`. Requires `stripe` package. Account must be active for refreshes to succeed.
- **Tables:** None.
- **When to use:** Before period-close pulls: `--trigger`, wait (the first transactions refresh can take minutes), read until both statuses are `succeeded`, then pull with `stripe_fc_transactions.py` and read the balance with `stripe_fc_balances.py`. The emitted `transaction_refresh.id` feeds `--after_refresh` and `manage_bank_feeds.py update --last_txn_refresh_id`.

### adapters/ama_client.py

- **Purpose:** Client for the Auth My Accountant (AMA) session broker. Creates multi-institution auth bundles (one URL the client uses to connect several banks via Stripe FC) and retrieves connected-account results (`fca_xxx` IDs + metadata).
- **Domain:** Utilities (Integration)
- **Arguments (subcommands):**
  - `create-bundle [--consent_title] [--consent_body] [--firm_name] [--client_ref] [--max_sessions 5] [--expires_in_hours 72] [--permissions transactions,balances] [--prefetch transactions,balances]` -- Create a bundle; outputs the URL to send to the client. Permissions/prefetch use the PLURAL `balances` enum (unlike the refresh feature enum).
  - `status --bundle_id <uuid>` -- Get bundle status and flattened connected accounts.
- **Output:** `create-bundle`: `{"success", "bundle_id", "url", "token", "status", "expires_at", "max_sessions", "client_ref"}`. `status`: `{"success", "bundle_id", "status", "client_ref", "sessions_completed", "sessions_total", "expires_at", "accounts": [{provider_account_id, institution_name, last4, category, subcategory, display_name, account_status, session_index}]}`.
- **Preconditions:** `AMA_FIRM_API_KEY` in `{local_dir}/adapters/.env`; `create-bundle` additionally requires `STRIPE_API_KEY`/`STRIPE_PUBLISHABLE_KEY` (the sk/pk prefix -- `sk_test_`/`sk_live_` -- determines mode). `AMA_API_URL` optional (defaults to production).
- **Tables:** None.
- **When to use:** During `operations/connect-bank-feeds.md` to establish bank connections. Accounts remain retrievable from expired bundles, so late `status` polling is safe.

---

## 8. Shared Libraries

These are imported by scripts, not invoked directly from the CLI.

### _shared/config_loader.py

- **Purpose:** Reads `config.yaml` from the project's `_local-bookkeeping/` directory, resolves path placeholders, and exposes helper functions.
- **Key exports:**
  - `load_config()` -- Returns full config dict with all placeholders resolved (`{project-root}`, `{local_dir}`, `{module_root}`, `{output_folder}`).
  - `get_db_path()` -- Returns resolved path to `bookkeeping.db`.
  - `get_coding_config()` -- Returns `{min_confidence_to_categorize, min_confidence_to_auto_approve}`.
  - `get_period_config()` -- Returns `{period_type, fiscal_calendar}`.
- **Environment:** Requires `BOOKKEEPING_CONFIG_PATH` to be set.
- **Used by:** Every script and adapter.

### _shared/period_resolver.py

- **Purpose:** Resolves period input strings to explicit date ranges. Supports `calendar-monthly` (`YYYY-MM`), `date-range` (`YYYY-MM-DD_YYYY-MM-DD`), and `fiscal` (`YYYY-PNN`) period types.
- **Key exports:**
  - `resolve_period(period_input, period_type=None, fiscal_calendar_path=None)` -- Returns `{periodLabel, periodStart, periodEnd, periodType}`. Auto-detects type if not specified.
- **Used by:** Workflow orchestrators. Scripts receive pre-resolved `--start_date`/`--end_date` and are period-type-agnostic.

### _shared/rule_matcher.py

- **Purpose:** Reusable rule-matching logic for categorization rules. Evaluates conditions against import records using `all`/`any` logic.
- **Key exports:**
  - `extract_field(import_record, field_name)` -- Extracts value from import record. Fields: `reference`, `amount`, `source`, `any_text`.
  - `evaluate_condition(field_value, operator, expected_value)` -- Operators: `equals`, `contains`, `starts_with`, `is_blank`, `less_than`, `greater_than`, `equals_number`.
  - `match_rule(import_record, rule)` -- Returns True/False. Respects `logic: "all"` (AND) or `"any"` (OR).
- **Used by:** `apply_cat_rules.py`, `test_cat_rule.py`.

### _shared/trade_account_utils.py

- **Purpose:** Reusable computation functions for trade account operations.
- **Key exports:**
  - `compute_amount_due(conn, journal_entry_id, balance_account_code)` -- Sums non-balance-account postings to derive amount due (always positive cents).
  - `compute_paid_amount(conn, trade_account_id)` -- Sums payments applied to a trade account (cents).
  - `compute_consumed_amount(conn, trade_account_id)` -- CM/VC total consumption across both forms: applications (`source_ta_id`) + direct/owner-cleared TAPs (cents).
  - `compute_status(amount_due, paid_amount)` -- Returns `"unpaid"`, `"partial"`, or `"paid"`.
  - `get_balance_account_code(trade_account)` -- Extracts `balance_account_code` from trade account metadata.
- **Used by:** `apply_payment.py`, `apply_payments_bulk.py`, `list_open_items.py`.

### _shared/journal_engine.py

- **Purpose:** Core double-entry journal creation module. Handles account validation, contact auto-creation, direction calculation, split amounts, balance checks, and posting insertion.
- **Key exports:**
  - `parse_source(source)` -- Extracts account code from `"CODE - Name"` format.
  - `get_import_data(conn, import_id)` -- Fetches and validates import record; returns `{id, source, banking_date, amount, balance_type, bank_account_code}`.
  - `validate_account_codes(conn, codes)` -- Returns list of invalid account codes.
  - `validate_tags(conn, tags)` -- Returns list of invalid tags.
  - `validate_class(conn, class_name)` -- Returns error message or None.
  - `auto_create_contact(conn, contact_name)` -- INSERT OR IGNORE into contacts.
  - `validate_and_calculate_split_amounts(postings, import_amount)` -- Xero-style split logic (fixed amounts first, then percentages on remainder).
  - `determine_direction(balance_type, amount, is_bank_account)` -- Computes debit/credit from balance type and amount sign.
  - `create_journal_entry(conn, categorization, import_data, confidence_score?, metadata?, class_name?)` -- Import-linked JE creation. Supports standard mode (auto-direction) and explicit mode (caller-specified direction per posting).
  - `create_journal_entry_direct(conn, transaction_date, memo, postings_data, je_metadata?)` -- Direct JE creation with pre-built postings (no import link). Used by manual journals, trade account scripts.
  - `create_journal_entry_transfer(conn, import_id, transaction_date, memo, postings_data, all_import_ids, class_name?, je_metadata?)` -- Transfer JE linking multiple imports. Stores `transfer_group` in metadata.
- **Used by:** `bulk_cat_transactions.py`, `apply_cat_rules.py`, `test_cat_rule.py`, `apply_transfer.py`, `apply_payment.py`, `apply_payments_bulk.py`, `create_manual_journal.py`.

### _shared/__init__.py

- **Purpose:** Package marker for the `_shared` module. No exports.

---

## Database Tables Reference

| Table | Purpose |
|---|---|
| `chart_of_accounts` | Account codes, names, types, and QBO remote_id mappings |
| `contacts` | Vendor/customer names with QBO remote_id |
| `tags` | Labels and QBO Classes (category='Class') with remote_id |
| `categorization_rules` | Priority-ordered match/apply rules for auto-categorization |
| `imports` | Raw ingested transactions; `processed`: 0=unprocessed, 1=fully processed, 2=client question |
| `journal_entries` | Double-entry headers; `sync` JSON tracks publishing status |
| `postings` | Debit/credit lines; linked to journal_entries, chart_of_accounts, contacts |
| `trade_accounts` | A/R and A/P lifecycle tracking; linked to journal_entries and contacts |
| `trade_account_payments` | Payment allocations against trade accounts; linked to imports |
| `audit_log` | Change history for trade accounts and payments |
