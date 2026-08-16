#!/usr/bin/env python3
"""
Apply Payment Tool
Applies a bank transaction payment to a trade account (A/R or A/P).
Creates a clearing journal entry (DR Bank/CR A/R for receivables, DR A/P/CR Bank for payables).
Requires either --import_id (cash account derived from import) or --payment_account (non-cash clearing).
"""

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import date

# Import shared utilities
from _shared.trade_account_utils import (
    compute_amount_due,
    compute_paid_amount,
    compute_status,
    get_balance_account_code,
)
from _shared.journal_engine import create_journal_entry_direct, parse_source
from _shared import config_loader


def parse_arguments():
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description='Apply payment to a trade account')
    parser.add_argument('--trade_account_id', required=True, help='Trade account ID to apply payment to')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--import_id', default=None, help='Bank transaction import ID (derives cash account, marks import processed)')
    group.add_argument('--payment_account', default=None, help='Non-cash account code for clearing (e.g., intercompany, clearing account)')
    parser.add_argument('--amount', required=True, type=int, help='Payment amount in cents')
    parser.add_argument('--payment_date', default=None, help='Payment date (YYYY-MM-DD, defaults to today)')
    parser.add_argument('--changed_by', default='apply_payment.py', help='Audit log changed_by value')
    return parser.parse_args()


def get_trade_account(conn, trade_account_id):
    """
    Fetch trade account with validation.
    Returns dict with trade account data or raises ValueError.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, type, contact, document_date, due_date,
               journal_entry_id, voided_at, metadata
        FROM trade_accounts
        WHERE id = ?
        """,
        (trade_account_id,)
    )
    row = cursor.fetchone()

    if not row:
        raise ValueError(f"Trade account not found: {trade_account_id}")

    trade_account = {
        'id': row[0],
        'type': row[1],
        'contact': row[2],
        'document_date': row[3],
        'due_date': row[4],
        'journal_entry_id': row[5],
        'voided_at': row[6],
        'metadata': json.loads(row[7]) if row[7] else {},
    }

    if trade_account['voided_at']:
        raise ValueError(f"Cannot apply payment to voided trade account: {trade_account_id}")

    return trade_account


def get_import_account_code(conn, import_id):
    """
    Get the account code from an import's source field.
    Source format: "<code> - <account name>" (e.g. "1001 - Operating Bank").
    Returns account_code string.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT source, processed FROM imports WHERE id = ?", (import_id,))
    row = cursor.fetchone()

    if not row:
        raise ValueError(f"Import not found: {import_id}")

    if row[1]:
        raise ValueError(f"Import already processed: {import_id}")

    return parse_source(row[0])


def mark_import_processed(conn, import_id):
    """Set processed=1 on an import record."""
    conn.execute("UPDATE imports SET processed = 1 WHERE id = ?", (import_id,))


def create_payment(conn, payment_data, changed_by):
    """Create payment record and audit log entry."""
    payment_id = str(uuid.uuid4())

    metadata = json.dumps({
        'clearing_je_id': payment_data['clearing_je_id'],
        'payment_account_code': payment_data['payment_account_code'],
    })
    conn.execute(
        """
        INSERT INTO trade_account_payments (
            id, trade_account_id, import_id, payment_date,
            amount, sync, metadata, created_at
        )
        VALUES (?, ?, ?, ?, ?, '{"status":"pending"}', ?, CURRENT_TIMESTAMP)
        """,
        (
            payment_id,
            payment_data['trade_account_id'],
            payment_data['import_id'],
            payment_data['payment_date'],
            payment_data['amount'],
            metadata,
        )
    )

    # Create audit log entry
    conn.execute(
        """
        INSERT INTO audit_log (
            id, table_name, record_id, action,
            field_changes, reason, changed_by, changed_at
        )
        VALUES (?, 'trade_account_payments', ?, 'insert', ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            str(uuid.uuid4()),
            payment_id,
            json.dumps({
                'trade_account_id': payment_data['trade_account_id'],
                'amount': payment_data['amount'],
                'import_id': payment_data['import_id'],
            }),
            f"Payment applied to trade account",
            changed_by,
        )
    )

    return payment_id


def main():
    try:
        args = parse_arguments()

        # Validate amount is positive
        if args.amount <= 0:
            raise ValueError("Payment amount must be positive")

        # Parse payment date
        payment_date = args.payment_date or date.today().isoformat()

        # Connect to database
        conn = sqlite3.connect(config_loader.get_db_path())
        conn.execute("PRAGMA foreign_keys = ON")

        try:
            # Get and validate trade account
            trade_account = get_trade_account(conn, args.trade_account_id)

            # Determine the other-side account for the clearing JE
            if args.import_id:
                other_account = get_import_account_code(conn, args.import_id)
            else:
                other_account = args.payment_account

            # Get balance_account_code from metadata
            balance_account_code = get_balance_account_code(trade_account)
            if not balance_account_code:
                raise ValueError(
                    f"Trade account {args.trade_account_id} missing balance_account_code in metadata. "
                    "Cannot compute amount_due."
                )

            # Compute amount due and prior payments using shared utilities
            amount_due = compute_amount_due(
                conn,
                trade_account['journal_entry_id'],
                balance_account_code
            )
            prior_payments = compute_paid_amount(conn, args.trade_account_id)

            # Calculate remaining balance
            remaining_balance = amount_due - prior_payments

            # Validate payment doesn't exceed balance
            if args.amount > remaining_balance:
                raise ValueError(
                    f"Payment amount ({args.amount} cents) exceeds remaining balance. "
                    f"Amount due: {amount_due} cents, Prior payments: {prior_payments} cents, "
                    f"Remaining: {remaining_balance} cents"
                )

            # Build clearing JE postings based on TA type
            # Receivable: DR other_account (cash in), CR A/R (clear receivable)
            # Payable: DR A/P (clear payable), CR other_account (cash out)
            if trade_account['type'] == 'receivable':
                postings = [
                    {
                        'account_code': other_account,
                        'direction': 'debit',
                        'amount': args.amount,
                        'contact': trade_account['contact'],
                        'description': f"Payment received - {trade_account['contact']}",
                    },
                    {
                        'account_code': balance_account_code,
                        'direction': 'credit',
                        'amount': args.amount,
                        'contact': trade_account['contact'],
                        'description': f"Clear A/R - {trade_account['contact']}",
                    },
                ]
            else:
                postings = [
                    {
                        'account_code': balance_account_code,
                        'direction': 'debit',
                        'amount': args.amount,
                        'contact': trade_account['contact'],
                        'description': f"Clear A/P - {trade_account['contact']}",
                    },
                    {
                        'account_code': other_account,
                        'direction': 'credit',
                        'amount': args.amount,
                        'contact': trade_account['contact'],
                        'description': f"Payment sent - {trade_account['contact']}",
                    },
                ]

            memo = f"Payment - {trade_account['contact']} ({trade_account['type']})"
            clearing_je_id = create_journal_entry_direct(conn, payment_date, memo, postings)

            # Mark clearing JE as 'ignore' immediately — it should never be published
            # as a standalone JE. The Payment/BillPayment object handles the QBO side.
            conn.execute(
                "UPDATE journal_entries SET sync = ? WHERE id = ?",
                ('{"status":"ignore"}', clearing_je_id)
            )

            # Create payment record
            payment_data = {
                'trade_account_id': args.trade_account_id,
                'import_id': args.import_id,
                'payment_date': payment_date,
                'amount': args.amount,
                'clearing_je_id': clearing_je_id,
                'payment_account_code': other_account,
            }

            payment_id = create_payment(conn, payment_data, args.changed_by)

            # Mark import as processed if linked
            if args.import_id:
                mark_import_processed(conn, args.import_id)

            # Commit
            conn.commit()

            # Calculate new balance and status
            new_paid_amount = prior_payments + args.amount
            new_balance = remaining_balance - args.amount
            status = compute_status(amount_due, new_paid_amount)

            # Output success
            result = {
                "success": True,
                "payment_id": payment_id,
                "clearing_je_id": clearing_je_id,
                "trade_account_id": args.trade_account_id,
                "amount_applied": args.amount,
                "amount_due": amount_due,
                "prior_payments": prior_payments,
                "new_paid_amount": new_paid_amount,
                "remaining_balance": new_balance,
                "status": status,
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
