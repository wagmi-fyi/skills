#!/usr/bin/env python3
"""
Migration: Add credit_memo and vendor_credit support.

Two changes:
  1. trade_accounts.type CHECK extends to ('receivable','payable','credit_memo','vendor_credit')
     -- requires SQLite table recreation since CHECK can't be ALTERed.
  2. trade_account_payments gets a nullable source_ta_id column + index
     -- simple ALTER TABLE ADD COLUMN.

Idempotent: re-runs are no-ops.
Backup: copies bookkeeping.db -> bookkeeping.db.pre_credit_types_backup before changes.
"""

import json
import os
import shutil
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '_shared'))
import config_loader


NEW_TYPE_CHECK = "type IN ('receivable', 'payable', 'credit_memo', 'vendor_credit')"
TARGET_COLUMN = 'source_ta_id'


def column_exists(conn, table, column):
    cursor = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def check_extended(conn):
    """Return True if trade_accounts.type CHECK already permits credit_memo."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='trade_accounts'"
    ).fetchone()
    if not row:
        return False
    return 'credit_memo' in row[0]


def recreate_trade_accounts(conn):
    """Recreate trade_accounts with extended CHECK.

    SQLite has no ALTER for CHECK constraints. Standard pattern:
    create _new with new schema, copy rows, drop old, rename, recreate indexes.

    Wrapped in an explicit BEGIN/COMMIT so the recreation is atomic — a
    Python crash mid-recreation rolls the whole thing back rather than
    leaving the DB with no trade_accounts table.

    PRAGMA foreign_keys must be set OUTSIDE the transaction (SQLite ignores
    it inside one), so we toggle it before/after the BEGIN.
    """
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN")
        try:
            conn.execute("""
                CREATE TABLE trade_accounts_new (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL CHECK(type IN ('receivable', 'payable', 'credit_memo', 'vendor_credit')),
                    contact TEXT NOT NULL,
                    document_date DATE NOT NULL,
                    due_date DATE,
                    journal_entry_id TEXT NOT NULL,
                    voided_at DATETIME,
                    sync JSON,
                    metadata JSON,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(journal_entry_id) REFERENCES journal_entries(id),
                    FOREIGN KEY(contact) REFERENCES contacts(name)
                )
            """)
            conn.execute("""
                INSERT INTO trade_accounts_new
                    (id, type, contact, document_date, due_date, journal_entry_id,
                     voided_at, sync, metadata, created_at)
                SELECT id, type, contact, document_date, due_date, journal_entry_id,
                       voided_at, sync, metadata, created_at
                FROM trade_accounts
            """)
            conn.execute("DROP TABLE trade_accounts")
            conn.execute("ALTER TABLE trade_accounts_new RENAME TO trade_accounts")
            for stmt in (
                "CREATE INDEX idx_trade_accounts_sync_status ON trade_accounts(json_extract(sync, '$.status'))",
                "CREATE INDEX idx_trade_accounts_type ON trade_accounts(type)",
                "CREATE INDEX idx_trade_accounts_contact ON trade_accounts(contact)",
                "CREATE INDEX idx_trade_accounts_document_date ON trade_accounts(document_date)",
                "CREATE INDEX idx_trade_accounts_due_date ON trade_accounts(due_date)",
            ):
                conn.execute(stmt)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def add_source_ta_id_column(conn):
    conn.execute("""
        ALTER TABLE trade_account_payments
        ADD COLUMN source_ta_id TEXT REFERENCES trade_accounts(id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_trade_account_payments_source
        ON trade_account_payments(source_ta_id)
    """)


def main():
    config = config_loader.load_config()
    db_path = config_loader.get_db_path()

    if not os.path.exists(db_path):
        print(json.dumps({"success": False, "error": f"Database not found at {db_path}"}))
        sys.exit(1)

    # Pre-flight idempotency check
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    has_column = column_exists(conn, 'trade_account_payments', TARGET_COLUMN)
    has_extended_check = check_extended(conn)
    conn.close()

    if has_column and has_extended_check:
        print(json.dumps({
            "migrated": False,
            "reason": "already current",
            "db_path": db_path
        }))
        return

    # Backup
    backup_path = db_path + ".pre_credit_types_backup"
    shutil.copy2(db_path, backup_path)
    print(f"Backup created: {backup_path}", file=sys.stderr)

    # Pre-migration row counts
    conn = sqlite3.connect(db_path)
    pre_ta = conn.execute("SELECT COUNT(*) FROM trade_accounts").fetchone()[0]
    pre_tap = conn.execute("SELECT COUNT(*) FROM trade_account_payments").fetchone()[0]

    try:
        if not has_extended_check:
            recreate_trade_accounts(conn)
        if not has_column:
            add_source_ta_id_column(conn)

        # Verify row counts unchanged
        post_ta = conn.execute("SELECT COUNT(*) FROM trade_accounts").fetchone()[0]
        post_tap = conn.execute("SELECT COUNT(*) FROM trade_account_payments").fetchone()[0]

        if post_ta != pre_ta:
            raise RuntimeError(f"trade_accounts row count mismatch: pre={pre_ta} post={post_ta}")
        if post_tap != pre_tap:
            raise RuntimeError(f"trade_account_payments row count mismatch: pre={pre_tap} post={post_tap}")

        conn.commit()
    except Exception as e:
        conn.close()
        print(json.dumps({
            "migrated": False,
            "error": str(e),
            "backup_path": backup_path,
            "restore_command": f"cp {backup_path} {db_path}"
        }))
        sys.exit(1)

    conn.close()

    print(json.dumps({
        "migrated": True,
        "backup_path": backup_path,
        "ta_count": post_ta,
        "tap_count": post_tap,
        "db_path": db_path
    }))


if __name__ == "__main__":
    main()
