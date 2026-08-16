-- Bookkeeping Schema
-- Database Design: Staging Layer (SQLite as processing workspace, external SoR as final destination)
-- Applied to: {project-root}/database/bookkeeping.db (domain-specific database for bookkeeping)

-- ============================================================================
-- CHART OF ACCOUNTS: Synced from System of Record
-- ============================================================================

CREATE TABLE chart_of_accounts (
    code TEXT PRIMARY KEY,    -- e.g., "1001", "4000"
    name TEXT NOT NULL,       -- e.g., "Checking Account", "Product Sales"
    type TEXT NOT NULL,       -- e.g., "asset", "liability", "equity", "income", "expense"
    remote_id TEXT,           -- The ID from QBO/Xero (e.g., "35")
    meta JSON                 -- Store tax codes, currency, description
);

-- ============================================================================
-- CONTACTS: Vendors and customers
-- ============================================================================

CREATE TABLE contacts (
    name TEXT PRIMARY KEY,    -- e.g., "Amazon", "Stripe Fee"
    remote_id TEXT,           -- The ID from QBO/Xero
    meta JSON                 -- Type (vendor/customer), email, tax ID, etc.
);

-- ============================================================================
-- TAGS: Flexible labeling for postings
-- ============================================================================

CREATE TABLE tags (
    name TEXT PRIMARY KEY,    -- e.g., "Project X", "East Coast"
    category TEXT,            -- e.g., "Region", "Department"
    remote_id TEXT            -- The ID from QBO/Xero
);

-- ============================================================================
-- CATEGORIZATION RULES: AI learning and overrides
-- ============================================================================

CREATE TABLE categorization_rules (
    id TEXT PRIMARY KEY,          -- UUID
    priority INTEGER NOT NULL,    -- Lower number runs first (e.g. 10 runs before 100)
    name TEXT NOT NULL,           -- e.g., "Adobe Subscription", "Stripe Fees"

    -- The Filter (When to apply)
    -- Logic: {"description": {"contains": "Adobe"}, "amount": {"lt": 0}}
    match_criteria JSON NOT NULL,

    -- The Output (What to apply)
    -- Logic: {"account_code": "6100", "contact": "Adobe", "tags": ["SaaS"]}
    apply_actions JSON NOT NULL,

    active BOOLEAN DEFAULT 1
);

-- ============================================================================
-- IMPORTS: Raw bank data ingestion
-- ============================================================================

CREATE TABLE imports (
    id TEXT PRIMARY KEY,      -- UUID or Source-generated ID
    source TEXT NOT NULL,     -- e.g., "shopify", "chase_checking", "amex"
    type TEXT NOT NULL,       -- e.g., "daily_sales", "bank_feed", "payout"
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    -- Banking / Reconciliation Affordances (Nullable)
    banking_date DATE,        -- Critical for bank feeds
    amount INTEGER,           -- Stored in cents (e.g., 100 = $1.00)
    batch_id TEXT,            -- Groups line items (e.g., "stmt_2023_10")
    external_id TEXT,         -- Source-generated ID for deduplication (e.g., "amex-20240115-001")

    raw_data JSON NOT NULL,   -- The full original payload
    processed BOOLEAN DEFAULT 0
);

-- ============================================================================
-- JOURNAL ENTRIES: Staged transactions
-- ============================================================================

CREATE TABLE journal_entries (
    id TEXT PRIMARY KEY,      -- UUID
    import_id TEXT,           -- Link back to the source import
    transaction_date DATE NOT NULL,
    memo TEXT,                -- Header-level description

    -- Sync Status (Staging Zone Logic)
    -- Legacy columns (deprecated — retained for SQLite compatibility, no longer read or written):
    sync_status TEXT DEFAULT 'pending',
    external_id TEXT,
    sync_error JSON,
    last_synced_at DATETIME,

    -- Consolidated sync tracking (canonical — use this for all sync operations):
    sync JSON,                         -- {"status","external_id","error","last_synced_at"}
    metadata JSON,

    FOREIGN KEY(import_id) REFERENCES imports(id)
);

-- ============================================================================
-- POSTINGS: Debits and credits for journal entries
-- ============================================================================

CREATE TABLE postings (
    id TEXT PRIMARY KEY,      -- UUID
    journal_entry_id TEXT NOT NULL,

    -- Financials
    account_code TEXT NOT NULL,   -- Links to chart_of_accounts.code
    direction TEXT NOT NULL CHECK(direction IN ('debit', 'credit')),
    amount INTEGER NOT NULL,      -- Absolute value in cents

    -- Categorization (Links to Reference Tables)
    contact TEXT,                  -- Links to contacts.name (nullable for multi-contact JEs)
    description TEXT,             -- Line-level description
    tags JSON,                    -- Array of strings linking to tags.name
    confidence_score INTEGER,     -- AI confidence score (1-10 scale)
    metadata JSON,                -- Additional flexible data storage
    fx JSON,                      -- Foreign currency detail: {"amount": int_cents, "currency": "CAD", "rate": 1.3426}

    FOREIGN KEY(journal_entry_id) REFERENCES journal_entries(id) ON DELETE CASCADE,
    FOREIGN KEY(account_code) REFERENCES chart_of_accounts(code),
    FOREIGN KEY(contact) REFERENCES contacts(name)
);

-- ============================================================================
-- TRADE ACCOUNTS: A/R and A/P tracking
-- ============================================================================

CREATE TABLE trade_accounts (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL CHECK(type IN ('receivable', 'payable', 'credit_memo', 'vendor_credit')),
    contact TEXT NOT NULL,
    document_date DATE NOT NULL,
    due_date DATE,                      -- when payment expected (for aging/forecasting)
    journal_entry_id TEXT NOT NULL,
    voided_at DATETIME,
    sync JSON,                         -- {"status","external_id","error","last_synced_at"}
    metadata JSON,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY(journal_entry_id) REFERENCES journal_entries(id),
    FOREIGN KEY(contact) REFERENCES contacts(name)
);

-- ============================================================================
-- TRADE ACCOUNT PAYMENTS: Payment allocation for A/R and A/P
-- ============================================================================

CREATE TABLE trade_account_payments (
    id TEXT PRIMARY KEY,
    trade_account_id TEXT NOT NULL,    -- the TA being reduced (target invoice/bill, OR source CM/VC for self-tracking)
    import_id TEXT,                     -- bank transaction that paid this (nullable for manual adjustments and credit applications)
    source_ta_id TEXT,                  -- when set, this row is a credit application; references the source CM or VC
    payment_date DATE NOT NULL,
    amount INTEGER NOT NULL,            -- stored in cents
    sync JSON,                         -- {"status","external_id","error","last_synced_at"}
    metadata JSON,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY(trade_account_id) REFERENCES trade_accounts(id),
    FOREIGN KEY(import_id) REFERENCES imports(id),
    FOREIGN KEY(source_ta_id) REFERENCES trade_accounts(id)
);

-- ============================================================================
-- AUDIT LOG: Change tracking
-- ============================================================================

CREATE TABLE audit_log (
    id TEXT PRIMARY KEY,
    table_name TEXT NOT NULL,
    record_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('insert', 'update', 'delete')),
    field_changes JSON,
    reason TEXT,
    changed_by TEXT,
    changed_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- INDEXES
-- ============================================================================

CREATE INDEX idx_journal_sync_status ON journal_entries(sync_status);  -- legacy, retained
CREATE INDEX idx_journal_sync_status_v2 ON journal_entries(json_extract(sync, '$.status'));
CREATE INDEX idx_trade_accounts_sync_status ON trade_accounts(json_extract(sync, '$.status'));
CREATE INDEX idx_trade_account_payments_sync_status ON trade_account_payments(json_extract(sync, '$.status'));
CREATE INDEX idx_imports_processed ON imports(processed);
CREATE UNIQUE INDEX idx_imports_source_external_id ON imports(source, external_id);
CREATE INDEX idx_postings_account ON postings(account_code);
CREATE INDEX idx_rules_priority ON categorization_rules(priority);
CREATE INDEX idx_trade_accounts_type ON trade_accounts(type);
CREATE INDEX idx_trade_accounts_contact ON trade_accounts(contact);
CREATE INDEX idx_trade_accounts_document_date ON trade_accounts(document_date);
CREATE INDEX idx_trade_accounts_due_date ON trade_accounts(due_date);
CREATE INDEX idx_trade_account_payments_account ON trade_account_payments(trade_account_id);
CREATE INDEX idx_trade_account_payments_source ON trade_account_payments(source_ta_id);
CREATE INDEX idx_audit_log_table_record ON audit_log(table_name, record_id);
