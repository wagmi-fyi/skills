---
name: qbo
license: Apache-2.0
description: Query and create QuickBooks Online data. Retrieve accounts, invoices, bills, journal entries, customers, vendors, items, classes, payments, deposits. Create new accounts in the chart of accounts. Use when pulling data from QBO, checking balances, looking up transactions, reading QuickBooks records, or adding accounts.
---

# QBO Skill

Read and write QuickBooks Online data using the `python-quickbooks` SDK.

**Prerequisites:** Project venv (`.venv/bin/python3`) with the deps from [`requirements.txt`](requirements.txt) installed (see [Dependencies](#dependencies)). Credentials at `.claude/skills/qbo/.env` (see Project Setup).

## Quick Reference

| Operation | Command |
|-----------|---------|
| Query entities | `.venv/bin/python3 ~/.claude/skills/qbo/scripts/query.py --entity=<Entity> [--where="..."] [--max_results=N]` |
| Get by ID | `.venv/bin/python3 ~/.claude/skills/qbo/scripts/query.py --entity=<Entity> --id=<ID>` |
| Count records | `.venv/bin/python3 ~/.claude/skills/qbo/scripts/query.py --entity=<Entity> --count_only` |
| Paginate | `.venv/bin/python3 ~/.claude/skills/qbo/scripts/query.py --entity=<Entity> --start_position=101 --max_results=100` |
| Create account | `.venv/bin/python3 ~/.claude/skills/qbo/scripts/create_account.py --name="..." --account_type=Expense [--acct_num=6500] [--account_sub_type=...] [--description="..."] [--parent_id=85]` |
| Find/create customer | `.venv/bin/python3 ~/.claude/skills/qbo/scripts/create_customer.py --display_name="..." [--company_name=...] [--email=...] [--phone=...] [--line1=...] [--city=...] [--state=...] [--zip=...] [--country=...]` |
| Create invoice | `.venv/bin/python3 ~/.claude/skills/qbo/scripts/create_invoice.py --customer_id=ID --invoice_num="..." --txn_date=YYYY-MM-DD --due_date=YYYY-MM-DD --line_items='[...]' [--class_id=...] [--bill_email=...]` |
| Send invoice | `.venv/bin/python3 ~/.claude/skills/qbo/scripts/send_invoice.py --invoice_id=ID [--send_to="email@..."]` |

## Common Operations

### List Accounts (Chart of Accounts)

```bash
.venv/bin/python3 ~/.claude/skills/qbo/scripts/query.py --entity=Account
```

### Get Bank Accounts Only

```bash
.venv/bin/python3 ~/.claude/skills/qbo/scripts/query.py --entity=Account --where="AccountType = 'Bank'"
```

### Get Unpaid Invoices

```bash
.venv/bin/python3 ~/.claude/skills/qbo/scripts/query.py --entity=Invoice --where="Balance > '0'"
```

### Get Journal Entries by Date

```bash
.venv/bin/python3 ~/.claude/skills/qbo/scripts/query.py --entity=JournalEntry --where="TxnDate >= '2024-01-01'"
```

### Get Specific Record by ID

```bash
.venv/bin/python3 ~/.claude/skills/qbo/scripts/query.py --entity=Invoice --id=123
```

### Count Customers

```bash
.venv/bin/python3 ~/.claude/skills/qbo/scripts/query.py --entity=Customer --count_only
```

## Create Account

```bash
# Expense account with number
.venv/bin/python3 ~/.claude/skills/qbo/scripts/create_account.py \
  --name="Merchant Processing Fees" --account_type=Expense --acct_num=6500

# Sub-account under an existing parent
.venv/bin/python3 ~/.claude/skills/qbo/scripts/create_account.py \
  --name="Stripe Fees" --account_type=Expense --parent_id=85 \
  --account_sub_type=OtherMiscellaneousExpense
```

**Required:** `--name`, `--account_type`. **Optional:** `--account_sub_type`, `--acct_num`, `--description`, `--parent_id`.

**Valid AccountType values:** Bank, Accounts Receivable, Other Current Asset, Fixed Asset, Other Asset, Accounts Payable, Credit Card, Other Current Liability, Long Term Liability, Equity, Income, Cost of Goods Sold, Expense, Other Income, Other Expense

Checks for duplicate names before creating. Returns JSON with the created account details.

## Create Customer

```bash
# Find or create by display name
.venv/bin/python3 ~/.claude/skills/qbo/scripts/create_customer.py \
  --display_name="Example Bakery Co" \
  --company_name="Example Bakery Co" \
  --email="info@example.com" --phone="555-0100" \
  --line1="1 Example Street" --city="Anytown" --state="OH" --zip="43000" --country="US"
```

**Required:** `--display_name`. **Optional:** `--company_name`, `--email`, `--phone`, `--line1`, `--city`, `--state`, `--zip`, `--country`.

Returns existing customer if DisplayName already exists (`"action": "found_existing"`), otherwise creates new (`"action": "created"`).

## Create Invoice

```bash
.venv/bin/python3 ~/.claude/skills/qbo/scripts/create_invoice.py \
  --customer_id=123 \
  --invoice_num="1700710" \
  --txn_date="2026-03-01" \
  --due_date="2026-03-31" \
  --class_id="1571398" \
  --line_items='[{"description": "Scottish Shortbread x24", "amount": 150.00, "item_id": "1"}]'
```

**Required:** `--customer_id`, `--invoice_num`, `--txn_date`, `--due_date`, `--line_items`. **Optional:** `--class_id`, `--bill_email`, `--private_note`.

Line items JSON: `[{"description": "...", "amount": 150.00, "item_id": "QBO_ITEM_ID", "qty": 1}]`. Checks for duplicate DocNumber before creating.

## Send Invoice

```bash
# Send to customer's default email
.venv/bin/python3 ~/.claude/skills/qbo/scripts/send_invoice.py --invoice_id=456

# Send to a specific email
.venv/bin/python3 ~/.claude/skills/qbo/scripts/send_invoice.py --invoice_id=456 --send_to="buyer@example.com"
```

**Required:** `--invoice_id`. **Optional:** `--send_to` (override recipient email).

## Supported Entities

**Priority entities:** Account, JournalEntry, Invoice, Bill, Customer, Vendor, Item, Class, Payment, Deposit

**All entities:** Account, Attachable, Bill, BillPayment, Budget, Class, CompanyInfo, CreditCardPayment, CreditMemo, Customer, Department, Deposit, Employee, Estimate, Invoice, Item, JournalEntry, Payment, PaymentMethod, Preferences, Purchase, PurchaseOrder, RefundReceipt, SalesReceipt, TaxAgency, TaxCode, TaxRate, TaxService, Term, TimeActivity, Transfer, Vendor, VendorCredit

## Script Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--entity` | Yes | - | Entity type (Account, Invoice, etc.) |
| `--id` | No | - | Fetch single record by ID |
| `--where` | No | - | WHERE clause filter |
| `--max_results` | No | 100 | Max records (1-1000) |
| `--start_position` | No | 1 | Pagination offset (1-based) |
| `--count_only` | No | false | Return count only |

## Output Format

All queries return JSON to stdout:

```json
{
  "success": true,
  "entity": "Invoice",
  "count": 25,
  "total_count": 150,
  "truncated": true,
  "query": "SELECT * FROM Invoice WHERE ...",
  "data": [...]
}
```

- `count`: Records returned in this response
- `total_count`: Total matching records
- `truncated`: true if more records exist beyond `max_results`

## Reference

Load on demand — when the work needs that knowledge. **Precondition references** must be read before performing the associated action for the first time in a session.

| Reference | File | Contains | Precondition |
|-----------|------|----------|-------------|
| Entity Reference | `reference/entities.md` | Entity attributes, filters, examples | Before querying an unfamiliar entity |
| Query Patterns | `reference/query-patterns.md` | SDK methods, syntax, pagination | Before composing a query or paginating a result set |
| Production Testing | `reference/production-testing.md` | Safe live-realm probes: rails, cleanup, per-realm empirics | Before any write against a live realm |

## Project Setup

### Credential Resolution

The skill finds QBO credentials via this resolution order:

1. **`QBO_ENV_PATH`** env var — explicit path to a `.env` file (override for edge cases)
2. **`BOOKKEEPING_CONFIG_PATH`** → derives `{local_dir}/adapters/.env` — shared with bookkeeping adapters (single source of truth when both systems are in use)
3. **`{cwd}/.claude/skills/qbo/.env`** — standalone use, when this skill runs without bookkeeping

When used alongside bookkeeping, credentials live at `_local-bookkeeping/adapters/.env` and are shared by both systems. No separate `.claude/skills/qbo/.env` is needed.

### Standalone Setup (without bookkeeping)

If using this skill without bookkeeping:

```bash
mkdir -p .claude/skills/qbo
cp ~/.claude/skills/qbo/scripts/.env.example .claude/skills/qbo/.env
```

Edit `.claude/skills/qbo/.env` with your QBO OAuth credentials:
- **Client ID / Secret** — from your app on the [Intuit Developer Portal](https://developer.intuit.com)
- **Access / Refresh Tokens** — from the [OAuth 2.0 Playground](https://developer.intuit.com/v2/OAuth2Playground/RedirectUrl)
- **Realm ID** — your QBO company ID (visible in the QBO URL)

### Dependencies

Pinned in [`requirements.txt`](requirements.txt), verified on Python 3.12 and 3.14. One-command
install into the project venv:

```bash
# uv-managed venv:
uv pip install --python .venv/bin/python -r ~/.claude/skills/qbo/requirements.txt
# plain venv:
.venv/bin/pip install -r ~/.claude/skills/qbo/requirements.txt
```

**Install the package names, not the import names.** The OAuth client imports as `intuitlib` but ships on PyPI as **`intuit-oauth`** — `pip install intuitlib` silently grabs the wrong project, and the SDK import then fails closed (`qbo_client.py` sets `QBO_IMPORTS_AVAILABLE = False` rather than erroring). `requirements.txt` carries the correct mapping; install from it rather than by hand.

### Verify `.gitignore` coverage

Ensure `.env` is in your `.gitignore` (covers credential files at any depth).

### Token Behavior

**Lazy refresh** — tokens are only refreshed when an API call fails with 401. Refreshed tokens are persisted back to whichever `.env` file was loaded at startup (logged to stderr).

**Single source of truth** — when `BOOKKEEPING_CONFIG_PATH` is set, both the QBO skill and bookkeeping adapters read/write the same `.env` file, preventing token drift.

**Refresh token expiry** — QBO refresh tokens expire after 100 days of non-use. If you see `REFRESH_TOKEN_EXPIRED`, re-authorize at the [OAuth Playground](https://developer.intuit.com/v2/OAuth2Playground/RedirectUrl).

## Limitations

- **No bank feeds** - QBO API doesn't expose raw bank feed transactions.
- **Rate limits** - ~500 requests/minute. Scripts include automatic retry with backoff.
- **Max 1000 results** - Use pagination for larger result sets.
- **Sequential only** - Don't run parallel queries (token refresh conflicts).
