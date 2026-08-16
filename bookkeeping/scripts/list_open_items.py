#!/usr/bin/env python3
"""
List Open Items Tool
Queries trade accounts with computed amount, paid amount, and status.
"""

import argparse
import json
import sqlite3
import sys

# Import shared utilities
from _shared.trade_account_utils import (
    compute_amount_due,
    compute_consumed_amount,
    compute_paid_amount,
    compute_status,
    is_credit_memo,
)
from _shared import config_loader


def parse_arguments():
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description='List open trade accounts (receivables/payables)')
    parser.add_argument('--type',
                        choices=['receivable', 'payable', 'credit_memo', 'vendor_credit'],
                        default=None,
                        help='Filter by type')
    parser.add_argument('--contact', default=None, help='Filter by contact name')
    parser.add_argument('--status', choices=['unpaid', 'partial', 'paid'], default=None,
                        help='Filter by computed status')
    parser.add_argument('--include_voided', action='store_true',
                        help='Include voided trade accounts')
    return parser.parse_args()


def list_trade_accounts(conn, type_filter=None, contact_filter=None, include_voided=False):
    """
    List trade accounts with computed amounts and status.

    Returns list of dicts with:
    - id, type, contact, document_date, due_date, metadata
    - amount_due (from journal entry)
    - paid_amount (from payments)
    - remaining_balance
    - status (unpaid/partial/paid)
    """
    cursor = conn.cursor()

    # Build WHERE clause
    where_clauses = []
    params = []

    if type_filter:
        where_clauses.append("ta.type = ?")
        params.append(type_filter)

    if contact_filter:
        where_clauses.append("ta.contact = ?")
        params.append(contact_filter)

    if not include_voided:
        where_clauses.append("ta.voided_at IS NULL")

    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

    # Query trade accounts
    query = f"""
        SELECT
            ta.id,
            ta.type,
            ta.contact,
            ta.document_date,
            ta.due_date,
            ta.journal_entry_id,
            ta.voided_at,
            ta.metadata,
            ta.created_at
        FROM trade_accounts ta
        WHERE {where_sql}
        ORDER BY ta.document_date DESC, ta.created_at DESC
    """

    cursor.execute(query, params)
    rows = cursor.fetchall()

    results = []
    for row in rows:
        trade_account_id = row[0]
        journal_entry_id = row[5]
        metadata = json.loads(row[7]) if row[7] else {}

        # Get balance_account_code from metadata
        balance_account_code = metadata.get('balance_account_code')

        # Compute amount_due using shared utility
        if balance_account_code:
            amount_due = compute_amount_due(conn, journal_entry_id, balance_account_code)
        else:
            # Fallback for legacy trade accounts without balance_account_code
            # This shouldn't happen for new records but provides graceful degradation
            amount_due = 0

        ta_type = row[1]

        # First-class credit_memo / vendor_credit: consumption spans BOTH forms —
        # credit applications (source_ta_id) and direct/owner-cleared settlement
        # (trade_account_id with no source_ta_id). See compute_consumed_amount.
        if ta_type in ('credit_memo', 'vendor_credit'):
            paid_amount = compute_consumed_amount(conn, trade_account_id)
            # Display amount_due as negative — credit owed to the customer / from vendor
            amount_due = -amount_due
            remaining_balance = amount_due + paid_amount  # amount_due is negative; applied increases toward 0
            status = compute_status(-amount_due, paid_amount)  # status math wants positive amount_due
        else:
            paid_amount = compute_paid_amount(conn, trade_account_id)

            # Legacy credit-memo detection: TAs stored as 'receivable'/'payable'
            # but with reversed posting direction on the balance account.
            credit_memo = is_credit_memo(conn, journal_entry_id, balance_account_code, ta_type) if balance_account_code else False
            if credit_memo:
                amount_due = -amount_due

            remaining_balance = amount_due - paid_amount
            status = compute_status(amount_due, paid_amount)

        results.append({
            'id': trade_account_id,
            'type': row[1],
            'contact': row[2],
            'document_date': row[3],
            'due_date': row[4],
            'journal_entry_id': journal_entry_id,
            'voided_at': row[6],
            'created_at': row[8],
            'metadata': metadata,
            'amount_due': amount_due,
            'paid_amount': paid_amount,
            'remaining_balance': remaining_balance,
            'status': status,
        })

    return results


def main():
    try:
        args = parse_arguments()

        # Connect to database
        conn = sqlite3.connect(config_loader.get_db_path())

        try:
            # Get all trade accounts matching filters
            trade_accounts = list_trade_accounts(
                conn,
                type_filter=args.type,
                contact_filter=args.contact,
                include_voided=args.include_voided
            )

            # Apply status filter in Python (since it's computed)
            if args.status:
                trade_accounts = [ta for ta in trade_accounts if ta['status'] == args.status]

            # Totals split by type — summing across types is meaningless because
            # CMs/VCs negate amount_due (representing credits owed back).
            totals_by_type = {}
            for ta in trade_accounts:
                t = ta['type']
                bucket = totals_by_type.setdefault(t, {
                    "count": 0, "amount_due": 0, "paid_amount": 0, "remaining_balance": 0
                })
                bucket["count"] += 1
                bucket["amount_due"] += ta['amount_due']
                bucket["paid_amount"] += ta['paid_amount']
                bucket["remaining_balance"] += ta['remaining_balance']

            # Output success
            result = {
                "success": True,
                "count": len(trade_accounts),
                "totals_by_type": totals_by_type,
                "filters": {
                    "type": args.type,
                    "contact": args.contact,
                    "status": args.status,
                    "include_voided": args.include_voided,
                },
                "items": trade_accounts,
            }
            print(json.dumps(result, indent=2))
            sys.exit(0)

        finally:
            conn.close()

    except Exception as e:
        print(json.dumps({
            "success": False,
            "error": f"Unexpected error: {str(e)}"
        }, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
