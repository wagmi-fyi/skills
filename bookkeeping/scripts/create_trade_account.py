#!/usr/bin/env python3
"""
Create Trade Account Tool
Creates a trade account (A/R or A/P) linked to a journal entry,
with contact auto-creation and audit logging.
"""

import argparse
import json
import os
import sqlite3
import sys
import uuid

# Add shared module to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '_shared'))
import config_loader


def parse_arguments():
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description='Create a trade account (receivable or payable)')
    parser.add_argument('--type', required=True, choices=['receivable', 'payable'],
                        help='Trade account type: receivable or payable')
    parser.add_argument('--contact', required=True, help='Entity name (customer or vendor)')
    parser.add_argument('--document_date', required=True, help='Document/transaction date YYYY-MM-DD')
    parser.add_argument('--due_date', default=None, help='Due date YYYY-MM-DD (defaults to document_date)')
    parser.add_argument('--journal_entry_id', required=True, help='UUID of the linked journal entry')
    parser.add_argument('--balance_account_code', required=True,
                        help='The A/R or A/P account code (stored in metadata)')
    parser.add_argument('--metadata', default=None,
                        help='Additional metadata JSON (merged with auto-generated fields)')
    parser.add_argument('--changed_by', default='create_trade_account.py',
                        help='Audit log attribution')
    args = parser.parse_args()

    # Parse metadata JSON if provided
    if args.metadata:
        try:
            args.metadata = json.loads(args.metadata)
        except json.JSONDecodeError as e:
            print(json.dumps({
                "success": False,
                "error": f"Invalid JSON in metadata: {str(e)}"
            }))
            sys.exit(1)

    return args


def validate_journal_entry(conn, journal_entry_id):
    """Validate journal entry exists."""
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM journal_entries WHERE id = ?", (journal_entry_id,))
    if not cursor.fetchone():
        raise ValueError(f"Journal entry not found: {journal_entry_id}")


def validate_account_code(conn, account_code):
    """Validate account code exists in chart of accounts."""
    cursor = conn.cursor()
    cursor.execute("SELECT code FROM chart_of_accounts WHERE code = ?", (account_code,))
    if not cursor.fetchone():
        raise ValueError(f"Account code not found in chart_of_accounts: {account_code}")


def create_trade_account(conn, trade_account_data, changed_by):
    """Create trade account, auto-create contact, and audit log entry.

    Contact auto-creation is intentionally inside the transaction —
    if the trade account INSERT fails, the contact creation rolls back too.
    """
    trade_account_id = str(uuid.uuid4())

    # Auto-create contact if not exists
    conn.execute(
        "INSERT OR IGNORE INTO contacts (name, remote_id, meta) VALUES (?, NULL, '{}')",
        (trade_account_data['contact'],)
    )

    # Build metadata: user-provided merged, then balance_account_code wins last
    if trade_account_data.get('user_metadata'):
        metadata = dict(trade_account_data['user_metadata'])
        metadata['balance_account_code'] = trade_account_data['balance_account_code']
    else:
        metadata = {'balance_account_code': trade_account_data['balance_account_code']}

    # Insert trade account
    conn.execute(
        """
        INSERT INTO trade_accounts (
            id, type, contact, document_date, due_date,
            journal_entry_id, voided_at, sync, metadata, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, NULL, '{"status":"pending"}', ?, CURRENT_TIMESTAMP)
        """,
        (
            trade_account_id,
            trade_account_data['type'],
            trade_account_data['contact'],
            trade_account_data['document_date'],
            trade_account_data['due_date'],
            trade_account_data['journal_entry_id'],
            json.dumps(metadata),
        )
    )

    # Create audit log entry
    conn.execute(
        """
        INSERT INTO audit_log (
            id, table_name, record_id, action,
            field_changes, reason, changed_by, changed_at
        )
        VALUES (?, 'trade_accounts', ?, 'insert', NULL, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            str(uuid.uuid4()),
            trade_account_id,
            "Trade account created",
            changed_by,
        )
    )

    return trade_account_id


def main():
    try:
        args = parse_arguments()

        # Resolve due_date default
        due_date = args.due_date or args.document_date

        conn = sqlite3.connect(config_loader.get_db_path())
        conn.execute("PRAGMA foreign_keys = ON")

        try:
            # Validate foreign keys
            validate_journal_entry(conn, args.journal_entry_id)
            validate_account_code(conn, args.balance_account_code)

            # Build trade account data
            trade_account_data = {
                'type': args.type,
                'contact': args.contact,
                'document_date': args.document_date,
                'due_date': due_date,
                'journal_entry_id': args.journal_entry_id,
                'balance_account_code': args.balance_account_code,
                'user_metadata': args.metadata,
            }

            trade_account_id = create_trade_account(conn, trade_account_data, args.changed_by)

            conn.commit()

            result = {
                "success": True,
                "trade_account_id": trade_account_id,
                "type": args.type,
                "contact": args.contact,
                "document_date": args.document_date,
                "due_date": due_date,
                "journal_entry_id": args.journal_entry_id,
                "balance_account_code": args.balance_account_code,
            }
            print(json.dumps(result, indent=2))
            sys.exit(0)

        except Exception as e:
            conn.rollback()
            raise
        finally:
            conn.close()

    except ValueError as e:
        print(json.dumps({
            "success": False,
            "error": str(e)
        }, indent=2))
        sys.exit(1)

    except Exception as e:
        print(json.dumps({
            "success": False,
            "error": f"Unexpected error: {str(e)}"
        }, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
