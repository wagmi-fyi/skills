#!/usr/bin/env python3
"""
Apply Credit
Apply a credit_memo TA to a receivable invoice, or a vendor_credit TA to a payable bill.

Mechanics: insert one row in trade_account_payments with source_ta_id = source TA
and trade_account_id = target TA. No clearing JE — both source and target already
posted to A/R (or A/P) at issuance, so the application is a sub-ledger reallocation
that does not move the GL.

QBO publishes the application as a zero-amount Payment (CM) or BillPayment (VC)
with two LinkedTxn entries. See _publishers/credit_applications.py.
"""

import argparse
import json
import os
import sqlite3
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '_shared'))
import config_loader
from trade_account_utils import (
    compute_amount_due,
    compute_paid_amount,
    compute_applied_amount,
    get_balance_account_code,
)


def parse_arguments():
    p = argparse.ArgumentParser(description='Apply a CM/VC to a target invoice/bill')
    p.add_argument('--source_ta_id', required=True, help='CM or VC trade account id')
    p.add_argument('--target_ta_id', required=True, help='Receivable invoice or payable bill id')
    p.add_argument('--amount_cents', required=True, type=int, help='Amount to apply, in cents')
    p.add_argument('--application_date', required=True, help='YYYY-MM-DD')
    p.add_argument('--changed_by', default='apply_credit.py')
    return p.parse_args()


def fetch_ta(conn, ta_id):
    row = conn.execute(
        """
        SELECT id, type, contact, journal_entry_id, voided_at, metadata
        FROM trade_accounts WHERE id = ?
        """,
        (ta_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"Trade account not found: {ta_id}")
    return {
        'id': row[0], 'type': row[1], 'contact': row[2],
        'journal_entry_id': row[3], 'voided_at': row[4],
        'metadata': json.loads(row[5]) if row[5] else {},
    }


def main():
    args = parse_arguments()

    if args.amount_cents <= 0:
        print(json.dumps({"success": False, "error": "amount_cents must be positive"}))
        sys.exit(1)

    conn = sqlite3.connect(config_loader.get_db_path())
    conn.execute("PRAGMA foreign_keys = ON")

    try:
        source = fetch_ta(conn, args.source_ta_id)
        target = fetch_ta(conn, args.target_ta_id)

        # Type matching
        if source['type'] not in ('credit_memo', 'vendor_credit'):
            raise ValueError(
                f"source TA must be credit_memo or vendor_credit, got '{source['type']}'"
            )
        expected_target = 'receivable' if source['type'] == 'credit_memo' else 'payable'
        if target['type'] != expected_target:
            raise ValueError(
                f"type mismatch: source={source['type']} expects target={expected_target}, "
                f"got target='{target['type']}'"
            )

        # Voided checks
        if source['voided_at']:
            raise ValueError(f"source TA is voided: {source['id']}")
        if target['voided_at']:
            raise ValueError(f"target TA is voided: {target['id']}")

        # Contact match (QBO requires same customer/vendor for CM↔Invoice, VC↔Bill)
        if source['contact'] != target['contact']:
            raise ValueError(
                f"contact mismatch: source='{source['contact']}' target='{target['contact']}'. "
                f"QBO requires same customer/vendor."
            )

        # Remaining-balance validation
        source_due = compute_amount_due(
            conn, source['journal_entry_id'], get_balance_account_code(source)
        )
        source_applied = compute_applied_amount(conn, source['id'])
        source_remaining = source_due - source_applied
        if args.amount_cents > source_remaining:
            raise ValueError(
                f"amount {args.amount_cents} exceeds source remaining {source_remaining} "
                f"(due={source_due}, applied={source_applied})"
            )

        target_due = compute_amount_due(
            conn, target['journal_entry_id'], get_balance_account_code(target)
        )
        target_paid = compute_paid_amount(conn, target['id'])
        target_remaining = target_due - target_paid
        if args.amount_cents > target_remaining:
            raise ValueError(
                f"amount {args.amount_cents} exceeds target remaining {target_remaining} "
                f"(due={target_due}, paid={target_paid})"
            )

        # Insert TAP row — no JE.
        tap_id = str(uuid.uuid4())
        metadata = json.dumps({"application_method": "credit"})
        conn.execute(
            """
            INSERT INTO trade_account_payments (
                id, trade_account_id, source_ta_id, import_id, payment_date,
                amount, sync, metadata, created_at
            )
            VALUES (?, ?, ?, NULL, ?, ?, '{"status":"pending"}', ?, CURRENT_TIMESTAMP)
            """,
            (tap_id, target['id'], source['id'], args.application_date,
             args.amount_cents, metadata)
        )
        conn.execute(
            """
            INSERT INTO audit_log (id, table_name, record_id, action,
                                   field_changes, reason, changed_by, changed_at)
            VALUES (?, 'trade_account_payments', ?, 'insert', ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (str(uuid.uuid4()), tap_id,
             json.dumps({"source_ta_id": source['id'], "target_ta_id": target['id'],
                         "amount": args.amount_cents}),
             "Credit applied", args.changed_by)
        )

        conn.commit()

        result = {
            "success": True,
            "tap_id": tap_id,
            "source_ta_id": source['id'],
            "target_ta_id": target['id'],
            "amount_applied_cents": args.amount_cents,
            "source_remaining_cents": source_remaining - args.amount_cents,
            "target_remaining_cents": target_remaining - args.amount_cents,
        }
        print(json.dumps(result, indent=2))

    except ValueError as e:
        conn.rollback()
        print(json.dumps({"success": False, "error": str(e)}))
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
