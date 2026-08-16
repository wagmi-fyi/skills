#!/usr/bin/env python3
"""
Create Vendor Credit
Creates a VC trade account + originating JE in one transaction.

A Vendor Credit is a contra-payable: it has a contact (vendor), a document
date, line items posted to expense/contra accounts, and an open balance until
applied to one or more bills via apply_credit.py.

Originating JE postings:
  CR contra account (per line — typically an expense reversal)
  DR balance account total (the A/P account code passed via --balance_account_code)
"""

import argparse
import json
import os
import sqlite3
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '_shared'))
import config_loader
import journal_engine

# Reuse the helpers from create_credit_memo (same file, same module).
from create_credit_memo import build_postings, insert_trade_account


def parse_arguments():
    p = argparse.ArgumentParser(description='Create a Vendor Credit (contra-payable TA + JE)')
    p.add_argument('--contact', required=True, help='Vendor name')
    p.add_argument('--document_date', required=True, help='YYYY-MM-DD')
    p.add_argument('--document_number', default=None, help='VC document number')
    p.add_argument('--balance_account_code', required=True, help='A/P account code')
    p.add_argument('--lines', required=True,
                   help='JSON array: [{"account_code","amount_cents","class","description"},...]')
    p.add_argument('--memo', default='', help='JE memo')
    p.add_argument('--metadata', default=None, help='Additional metadata JSON')
    p.add_argument('--changed_by', default='create_vendor_credit.py')
    args = p.parse_args()

    try:
        args.lines = json.loads(args.lines)
    except json.JSONDecodeError as e:
        print(json.dumps({"success": False, "error": f"Invalid lines JSON: {e}"}))
        sys.exit(1)
    if args.metadata:
        try:
            args.metadata = json.loads(args.metadata)
        except json.JSONDecodeError as e:
            print(json.dumps({"success": False, "error": f"Invalid metadata JSON: {e}"}))
            sys.exit(1)
    return args


def main():
    args = parse_arguments()
    if not args.lines:
        print(json.dumps({"success": False, "error": "lines array is empty"}))
        sys.exit(1)

    conn = sqlite3.connect(config_loader.get_db_path())
    conn.execute("PRAGMA foreign_keys = ON")

    try:
        postings, total_cents = build_postings(
            args.lines, args.balance_account_code, args.contact, 'vendor_credit'
        )

        je_metadata = {"source": "vendor_credit", "memo": args.memo}
        je_id = journal_engine.create_journal_entry_direct(
            conn, args.document_date, args.memo or f"Vendor Credit {args.document_number or ''}".strip(),
            postings, je_metadata
        )

        ta_id = insert_trade_account(
            conn, 'vendor_credit', args.contact, args.document_date,
            je_id, args.balance_account_code, args.metadata,
            args.document_number, args.changed_by
        )

        conn.commit()
        print(json.dumps({
            "success": True,
            "trade_account_id": ta_id,
            "journal_entry_id": je_id,
            "amount_due_cents": total_cents,
            "type": "vendor_credit",
        }, indent=2))

    except ValueError as e:
        conn.rollback()
        print(json.dumps({"success": False, "error": str(e)}))
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
