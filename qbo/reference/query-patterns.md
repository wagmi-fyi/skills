# QBO Query Patterns

SDK query methods, syntax reference, and best practices for querying QuickBooks Online.

## SDK Query Methods

The `python-quickbooks` SDK provides several methods for fetching data:

### Entity.all(qb=client)

Fetch all records of an entity type (up to default limit).

```python
from quickbooks.objects.account import Account

accounts = Account.all(qb=client)
for account in accounts:
    print(account.Name)
```

### Entity.get(id, qb=client)

Fetch a single record by ID.

```python
invoice = Invoice.get(123, qb=client)
print(invoice.TotalAmt)
```

### Entity.filter(**kwargs, qb=client)

Filter by field values using keyword arguments.

```python
# Single filter
active_customers = Customer.filter(Active=True, qb=client)

# Multiple filters (AND)
bank_accounts = Account.filter(AccountType='Bank', Active=True, qb=client)
```

### Entity.query(query_string, qb=client)

Execute a raw SQL-like query string. Most flexible option.

```python
# Basic query
query = "SELECT * FROM Invoice WHERE TxnDate > '2024-01-01'"
invoices = Invoice.query(query, qb=client)

# With pagination
query = "SELECT * FROM JournalEntry STARTPOSITION 1 MAXRESULTS 100"
entries = JournalEntry.query(query, qb=client)
```

### Entity.count(qb=client)

Get count of all records.

```python
customer_count = Customer.count(qb=client)
print(f"Total customers: {customer_count}")
```

### Entity.where(**kwargs, qb=client)

Alternative filter syntax using WHERE-style conditions.

```python
# Similar to filter but with different syntax options
customers = Customer.where(qb=client, Active=True)
```

---

## Query Syntax

QBO uses a SQL-like query language.

### Basic SELECT

```sql
SELECT * FROM Invoice
SELECT * FROM Customer
SELECT * FROM Account
```

### WHERE Clause

```sql
-- String comparison (use single quotes)
SELECT * FROM Customer WHERE DisplayName = 'Acme Corp'

-- Numeric comparison
SELECT * FROM Invoice WHERE TotalAmt > '1000'
SELECT * FROM Bill WHERE Balance > '0'

-- Date comparison (YYYY-MM-DD format)
SELECT * FROM Invoice WHERE TxnDate >= '2024-01-01'
SELECT * FROM JournalEntry WHERE TxnDate > '2024-06-01' AND TxnDate < '2024-07-01'

-- Reference field comparison
SELECT * FROM Invoice WHERE CustomerRef = '123'
SELECT * FROM Bill WHERE VendorRef = '456'

-- LIKE for partial matching (% wildcard)
SELECT * FROM Customer WHERE DisplayName LIKE '%Smith%'
SELECT * FROM Vendor WHERE CompanyName LIKE 'Acme%'

-- IN clause
SELECT * FROM Account WHERE AccountType IN ('Bank', 'Credit Card')

-- Boolean
SELECT * FROM Customer WHERE Active = true
SELECT * FROM Vendor WHERE Active = false
```

### Combining Conditions

```sql
-- AND
SELECT * FROM Invoice WHERE TxnDate >= '2024-01-01' AND Balance > '0'

-- Multiple AND
SELECT * FROM Bill
WHERE TxnDate >= '2024-01-01'
  AND TxnDate < '2024-02-01'
  AND VendorRef = '456'
```

**Note:** QBO query language has limited OR support. For complex OR conditions, run multiple queries and merge results.

### Pagination

```sql
-- STARTPOSITION: 1-based index (starts at 1, not 0)
-- MAXRESULTS: Maximum records to return (max 1000)

SELECT * FROM Invoice STARTPOSITION 1 MAXRESULTS 100
SELECT * FROM Invoice STARTPOSITION 101 MAXRESULTS 100
SELECT * FROM Invoice STARTPOSITION 201 MAXRESULTS 100
```

### Including Inactive Records

By default, queries return only active records. To include inactive/deleted:

```sql
-- Include both active and inactive
SELECT * FROM Customer WHERE Active IN (true, false)

-- Only inactive
SELECT * FROM Vendor WHERE Active = false
```

---

## Date Formatting

QBO uses `YYYY-MM-DD` format for dates.

```sql
-- Correct
WHERE TxnDate = '2024-01-15'
WHERE TxnDate >= '2024-01-01'
WHERE TxnDate > '2023-12-31' AND TxnDate < '2024-02-01'

-- Incorrect (will fail)
WHERE TxnDate = '01/15/2024'
WHERE TxnDate = 'January 15, 2024'
```

---

## Pagination Patterns

### Loop Through All Results

```python
def fetch_all_invoices(client, where_clause=None):
    """Fetch all invoices with pagination."""
    all_invoices = []
    start_position = 1
    max_results = 1000

    while True:
        query = f"SELECT * FROM Invoice"
        if where_clause:
            query += f" WHERE {where_clause}"
        query += f" STARTPOSITION {start_position} MAXRESULTS {max_results}"

        batch = Invoice.query(query, qb=client) or []
        all_invoices.extend(batch)

        if len(batch) < max_results:
            break  # No more results

        start_position += max_results

    return all_invoices
```

### Using query.py Script

```bash
# First batch
python query.py --entity=Invoice --max_results=100 --start_position=1

# Second batch
python query.py --entity=Invoice --max_results=100 --start_position=101

# Third batch
python query.py --entity=Invoice --max_results=100 --start_position=201
```

The script output includes `total_count` and `truncated` to help determine if more pages exist.

---

## Error Handling

### Common Errors

| Error | Cause | Solution |
|-------|-------|----------|
| `ValidationFault` | Invalid query syntax | Check quotes, field names |
| `AuthenticationError` | Invalid/expired token | Token auto-refreshes; check refresh token |
| `ObjectNotFound` | Entity ID doesn't exist | Verify ID before querying |
| `RateLimit (429)` | Too many requests | Use exponential backoff |

### Handling in Code

```python
from quickbooks.exceptions import QuickbooksException, AuthorizationException

try:
    invoices = Invoice.query(query, qb=client)
except AuthorizationException:
    print("Auth failed - refresh token may be expired")
except QuickbooksException as e:
    print(f"QBO error: {e.message}")
except Exception as e:
    print(f"Unexpected error: {e}")
```

---

## Rate Limits

QBO API has rate limits (varies by endpoint, approximately 500 requests/minute).

### Best Practices

1. **Batch queries** - Use MAXRESULTS=1000 to minimize requests
2. **Implement backoff** - Retry with exponential delay on 429 errors
3. **Cache results** - Don't re-query data that hasn't changed
4. **Sequential queries** - Don't run parallel queries (causes token refresh conflicts)

### Backoff Example

```python
import time

def query_with_backoff(func, max_retries=3):
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            if '429' in str(e) or 'rate' in str(e).lower():
                delay = 2 ** attempt
                time.sleep(delay)
            else:
                raise
    raise Exception("Max retries exceeded")
```

---

## Common Query Examples

### Financial Queries

```bash
# All bank accounts
python query.py --entity=Account --where="AccountType = 'Bank'"

# Unpaid invoices
python query.py --entity=Invoice --where="Balance > '0'"

# Unpaid bills
python query.py --entity=Bill --where="Balance > '0'"

# Journal entries this month
python query.py --entity=JournalEntry --where="TxnDate >= '2024-01-01' AND TxnDate < '2024-02-01'"

# Payments received this year
python query.py --entity=Payment --where="TxnDate >= '2024-01-01'"

# Deposits to specific account
python query.py --entity=Deposit --where="DepositToAccountRef = '35'"
```

### Customer/Vendor Queries

```bash
# Active customers
python query.py --entity=Customer --where="Active = true"

# Customers with balance due
python query.py --entity=Customer --where="Balance > '0'"

# Find vendor by name
python query.py --entity=Vendor --where="DisplayName LIKE '%Office%'"

# All vendors (including inactive)
python query.py --entity=Vendor --where="Active IN (true, false)"
```

### Inventory/Item Queries

```bash
# All service items
python query.py --entity=Item --where="Type = 'Service'"

# All inventory items
python query.py --entity=Item --where="Type = 'Inventory'"

# Active items only
python query.py --entity=Item --where="Active = true"
```

### Single Record Lookups

```bash
# Get specific invoice
python query.py --entity=Invoice --id=123

# Get specific journal entry
python query.py --entity=JournalEntry --id=456

# Get company info
python query.py --entity=CompanyInfo --max_results=1
```

### Count Queries

```bash
# Count all customers
python query.py --entity=Customer --count_only

# Count unpaid invoices
python query.py --entity=Invoice --where="Balance > '0'" --count_only

# Count journal entries in date range
python query.py --entity=JournalEntry --where="TxnDate >= '2024-01-01'" --count_only
```

---

## Token Refresh

The QBO access token expires every 60 minutes. The `qbo_client.py` module handles this automatically:

1. On each API call, token is refreshed proactively
2. New tokens are persisted to `.env` file
3. If refresh token expires (100 days), manual re-auth required

### Manual Re-authorization

If you see `REFRESH_TOKEN_EXPIRED` error:

1. Go to [Intuit OAuth Playground](https://developer.intuit.com/v2/OAuth2Playground/RedirectUrl)
2. Complete OAuth flow for your app
3. Copy new access and refresh tokens
4. Update `.claude/skills/qbo/scripts/.env`

---

## Tips

1. **Always use single quotes** for string values in WHERE clauses
2. **Field names are case-sensitive** - use exact casing from SDK
3. **Reference fields** (CustomerRef, VendorRef) compare by ID, not name
4. **STARTPOSITION is 1-based**, not 0-based
5. **Run queries sequentially** to avoid token refresh race conditions
6. **Check `truncated` flag** in query.py output to know if more results exist
