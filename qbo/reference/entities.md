# QBO Entity Reference

Reference for common QuickBooks Online entities, their key attributes, and query patterns.

## Priority Entities

### Account (Chart of Accounts)

**SDK Class:** `Account`

| Attribute | Type | Description |
|-----------|------|-------------|
| Id | string | Unique identifier |
| Name | string | Account name |
| AcctNum | string | Account number (optional) |
| AccountType | string | Bank, Accounts Receivable, Other Current Asset, Fixed Asset, etc. |
| AccountSubType | string | More specific type (e.g., Checking, Savings) |
| CurrentBalance | decimal | Current balance |
| Active | boolean | Whether account is active |

**Common Filters:**
- `AccountType = 'Bank'`
- `Active = true`

**Example Query:**
```bash
.venv/bin/python3 .claude/skills/qbo/scripts/query.py --entity=Account --where="AccountType = 'Bank'"
```

**Sample Output:**
```json
{
  "Id": "35",
  "Name": "Business Checking",
  "AcctNum": "1000",
  "AccountType": "Bank",
  "AccountSubType": "Checking",
  "CurrentBalance": 15000.00,
  "Active": true
}
```

---

### JournalEntry

**SDK Class:** `JournalEntry`

| Attribute | Type | Description |
|-----------|------|-------------|
| Id | string | Unique identifier |
| DocNumber | string | Journal entry number |
| TxnDate | date | Transaction date (YYYY-MM-DD) |
| PrivateNote | string | Internal memo |
| Line | array | Line items (debits/credits) |
| TotalAmt | decimal | Total amount |

**Line Item Attributes:**
- `JournalEntryLineDetail.AccountRef` - Account reference
- `JournalEntryLineDetail.PostingType` - "Debit" or "Credit"
- `Amount` - Line amount
- `Description` - Line description

**Common Filters:**
- `TxnDate >= '2024-01-01'`
- `DocNumber = 'JE-001'`

**Example Query:**
```bash
.venv/bin/python3 .claude/skills/qbo/scripts/query.py --entity=JournalEntry --where="TxnDate >= '2024-01-01'" --max_results=50
```

---

### Invoice

**SDK Class:** `Invoice`

| Attribute | Type | Description |
|-----------|------|-------------|
| Id | string | Unique identifier |
| DocNumber | string | Invoice number |
| TxnDate | date | Invoice date |
| DueDate | date | Due date |
| CustomerRef | object | Customer reference (Id, name) |
| Line | array | Line items |
| TotalAmt | decimal | Total amount |
| Balance | decimal | Amount due |
| EmailStatus | string | NotSet, NeedToSend, EmailSent |

**Common Filters:**
- `TxnDate >= '2024-01-01'`
- `Balance > '0'` (unpaid invoices)
- `CustomerRef = '123'`

**Example Query:**
```bash
# Get unpaid invoices
.venv/bin/python3 .claude/skills/qbo/scripts/query.py --entity=Invoice --where="Balance > '0'"
```

---

### Bill

**SDK Class:** `Bill`

| Attribute | Type | Description |
|-----------|------|-------------|
| Id | string | Unique identifier |
| DocNumber | string | Bill number |
| TxnDate | date | Bill date |
| DueDate | date | Due date |
| VendorRef | object | Vendor reference (Id, name) |
| Line | array | Line items |
| TotalAmt | decimal | Total amount |
| Balance | decimal | Amount due |

**Common Filters:**
- `TxnDate >= '2024-01-01'`
- `Balance > '0'` (unpaid bills)
- `VendorRef = '456'`

**Example Query:**
```bash
# Get bills from a specific vendor
.venv/bin/python3 .claude/skills/qbo/scripts/query.py --entity=Bill --where="VendorRef = '456'"
```

---

### Customer

**SDK Class:** `Customer`

| Attribute | Type | Description |
|-----------|------|-------------|
| Id | string | Unique identifier |
| DisplayName | string | Display name |
| CompanyName | string | Company name |
| PrimaryEmailAddr | object | Email address |
| PrimaryPhone | object | Phone number |
| Balance | decimal | Outstanding balance |
| Active | boolean | Whether customer is active |

**Common Filters:**
- `Active = true`
- `Balance > '0'`
- `DisplayName LIKE '%Smith%'`

**Example Query:**
```bash
.venv/bin/python3 .claude/skills/qbo/scripts/query.py --entity=Customer --where="Active = true"
```

---

### Vendor

**SDK Class:** `Vendor`

| Attribute | Type | Description |
|-----------|------|-------------|
| Id | string | Unique identifier |
| DisplayName | string | Display name |
| CompanyName | string | Company name |
| PrimaryEmailAddr | object | Email address |
| PrimaryPhone | object | Phone number |
| Balance | decimal | Outstanding balance |
| Active | boolean | Whether vendor is active |

**Common Filters:**
- `Active = true`
- `Balance > '0'`

**Example Query:**
```bash
.venv/bin/python3 .claude/skills/qbo/scripts/query.py --entity=Vendor --where="Active = true"
```

---

### Item (Products/Services)

**SDK Class:** `Item`

| Attribute | Type | Description |
|-----------|------|-------------|
| Id | string | Unique identifier |
| Name | string | Item name |
| Type | string | Inventory, NonInventory, Service, etc. |
| Description | string | Item description |
| UnitPrice | decimal | Unit price |
| IncomeAccountRef | object | Income account |
| ExpenseAccountRef | object | Expense account |
| Active | boolean | Whether item is active |

**Common Filters:**
- `Type = 'Service'`
- `Active = true`

**Example Query:**
```bash
.venv/bin/python3 .claude/skills/qbo/scripts/query.py --entity=Item --where="Type = 'Service'"
```

---

### Class (Tracking Classes)

**SDK Class:** `Class` (via `trackingclass` module)

| Attribute | Type | Description |
|-----------|------|-------------|
| Id | string | Unique identifier |
| Name | string | Class name |
| FullyQualifiedName | string | Full hierarchical name |
| ParentRef | object | Parent class (for subclasses) |
| Active | boolean | Whether class is active |

**Common Filters:**
- `Active = true`

**Example Query:**
```bash
.venv/bin/python3 .claude/skills/qbo/scripts/query.py --entity=Class
```

---

### Payment

**SDK Class:** `Payment`

| Attribute | Type | Description |
|-----------|------|-------------|
| Id | string | Unique identifier |
| TxnDate | date | Payment date |
| CustomerRef | object | Customer reference |
| TotalAmt | decimal | Payment amount |
| PaymentMethodRef | object | Payment method |
| DepositToAccountRef | object | Deposit account |
| Line | array | Applied to invoices |

**Common Filters:**
- `TxnDate >= '2024-01-01'`
- `CustomerRef = '123'`

**Example Query:**
```bash
.venv/bin/python3 .claude/skills/qbo/scripts/query.py --entity=Payment --where="TxnDate >= '2024-01-01'"
```

---

### Deposit

**SDK Class:** `Deposit`

| Attribute | Type | Description |
|-----------|------|-------------|
| Id | string | Unique identifier |
| TxnDate | date | Deposit date |
| DepositToAccountRef | object | Account deposited to |
| TotalAmt | decimal | Total deposit amount |
| Line | array | Line items (payments, direct deposits) |

**Common Filters:**
- `TxnDate >= '2024-01-01'`
- `DepositToAccountRef = '35'`

**Example Query:**
```bash
.venv/bin/python3 .claude/skills/qbo/scripts/query.py --entity=Deposit --where="TxnDate >= '2024-01-01'"
```

---

## All Supported Entities

| Entity | SDK Class | Notes |
|--------|-----------|-------|
| Account | Account | Chart of accounts |
| Attachable | Attachable | File attachments |
| Bill | Bill | Vendor bills (A/P) |
| BillPayment | BillPayment | Bill payments |
| Budget | Budget | Budgets |
| Class | Class | Tracking classes |
| CompanyInfo | CompanyInfo | Company details |
| CreditCardPayment | CreditCardPayment | CC payments |
| CreditMemo | CreditMemo | Customer credits |
| Customer | Customer | Customers |
| Department | Department | Departments/locations |
| Deposit | Deposit | Bank deposits |
| Employee | Employee | Employees |
| Estimate | Estimate | Quotes/estimates |
| Invoice | Invoice | Customer invoices (A/R) |
| Item | Item | Products/services |
| JournalEntry | JournalEntry | Journal entries |
| Payment | Payment | Customer payments |
| PaymentMethod | PaymentMethod | Payment methods |
| Preferences | Preferences | Company preferences |
| Purchase | Purchase | Purchases (checks, expenses) |
| PurchaseOrder | PurchaseOrder | Purchase orders |
| RefundReceipt | RefundReceipt | Refunds |
| SalesReceipt | SalesReceipt | Sales receipts |
| TaxAgency | TaxAgency | Tax agencies |
| TaxCode | TaxCode | Tax codes |
| TaxRate | TaxRate | Tax rates |
| TaxService | TaxService | Tax services |
| Term | Term | Payment terms |
| TimeActivity | TimeActivity | Time tracking |
| Transfer | Transfer | Bank transfers |
| Vendor | Vendor | Vendors |
| VendorCredit | VendorCredit | Vendor credits |
