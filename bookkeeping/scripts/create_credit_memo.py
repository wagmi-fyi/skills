#!/usr/bin/env python3
"""
Create Credit Memo
Creates a CM trade account + originating JE in one transaction.

A Credit Memo is a contra-receivable: it has a contact (customer), a document
date, line items posted to revenue/contra accounts, and an open balance until
applied to one or more invoices via apply_credit.py.

Originating JE postings:
  DR contra/return account (per line)
  CR balance account total (the A/R account code passed via --balance_account_code)

Direction is enforced by construction (debit on lines, credit on balance).
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


def parse_arguments():
    p = argparse.ArgumentParser(description='Create a Credit Memo (contra-receivable TA + JE)')
    p.add_argument('--contact', required=True, help='Customer name')
    p.add_argument('--document_date', required=True, help='YYYY-MM-DD')
    p.add_argument('--document_number', default=None, help='CM document number (e.g. CM-1001)')
    p.add_argument('--balance_account_code', required=True, help='A/R account code')
    p.add_argument('--lines', required=True,
                   help='JSON array: [{"account_code","amount_cents","class","description"},...]')
    p.add_argument('--memo', default='', help='JE memo')
    p.add_argument('--metadata', default=None, help='Additional metadata JSON')
    p.add_argument('--changed_by', default='create_credit_memo.py')
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


def build_postings(lines, balance_account_code, contact, ta_kind):
    """Construct JE postings for CM (or VC).

    For credit_memo: each line DRs its contra account; total balance CR.
    For vendor_credit: each line CRs its contra account; total balance DR.
    """
    if ta_kind == 'credit_memo':
        line_direction = 'debit'
        balance_direction = 'credit'
    else:  # vendor_credit
        line_direction = 'credit'
        balance_direction = 'debit'

    postings = []
    total = 0
    for ln in lines:
        amt = int(ln['amount_cents'])
        if amt <= 0:
            raise ValueError(f"line amount_cents must be positive: {ln}")
        total += amt
        p = {
            'account_code': ln['account_code'],
            'direction': line_direction,
            'amount': amt,
            'contact': contact,
            'description': ln.get('description', ''),
        }
        if ln.get('class'):
            # create_journal_entry_direct reads per-posting class from the top-level
            # 'class_name' key (and stores it to postings.metadata.class_name). Emitting
            # p['metadata']={'class_name':...} here is ignored by the engine -> class dropped.
            p['class_name'] = ln['class']
        postings.append(p)

    postings.append({
        'account_code': balance_account_code,
        'direction': balance_direction,
        'amount': total,
        'contact': contact,
        'description': 'Balance offset',
    })
    return postings, total


def insert_trade_account(conn, ta_type, contact, document_date,
                          journal_entry_id, balance_account_code,
                          user_metadata, document_number, changed_by):
    ta_id = str(uuid.uuid4())
    metadata = dict(user_metadata or {})
    metadata['balance_account_code'] = balance_account_code
    if document_number:
        metadata['document_number'] = document_number

    # Auto-create contact if missing (mirrors create_trade_account.py)
    conn.execute(
        "INSERT OR IGNORE INTO contacts (name, remote_id, meta) VALUES (?, NULL, '{}')",
        (contact,)
    )

    conn.execute(
        """
        INSERT INTO trade_accounts (
            id, type, contact, document_date, due_date,
            journal_entry_id, voided_at, sync, metadata, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, NULL, '{"status":"pending"}', ?, CURRENT_TIMESTAMP)
        """,
        (ta_id, ta_type, contact, document_date, document_date,
         journal_entry_id, json.dumps(metadata))
    )
    conn.execute(
        """
        INSERT INTO audit_log (id, table_name, record_id, action,
                               field_changes, reason, changed_by, changed_at)
        VALUES (?, 'trade_accounts', ?, 'insert', NULL, ?, ?, CURRENT_TIMESTAMP)
        """,
        (str(uuid.uuid4()), ta_id, f"{ta_type} created", changed_by)
    )
    return ta_id


def main():
    args = parse_arguments()
    if not args.lines:
        print(json.dumps({"success": False, "error": "lines array is empty"}))
        sys.exit(1)

    conn = sqlite3.connect(config_loader.get_db_path())
    conn.execute("PRAGMA foreign_keys = ON")

    try:
        postings, total_cents = build_postings(
            args.lines, args.balance_account_code, args.contact, 'credit_memo'
        )

        je_metadata = {"source": "credit_memo", "memo": args.memo}
        je_id = journal_engine.create_journal_entry_direct(
            conn, args.document_date, args.memo or f"Credit Memo {args.document_number or ''}".strip(),
            postings, je_metadata
        )

        ta_id = insert_trade_account(
            conn, 'credit_memo', args.contact, args.document_date,
            je_id, args.balance_account_code, args.metadata,
            args.document_number, args.changed_by
        )

        conn.commit()
        print(json.dumps({
            "success": True,
            "trade_account_id": ta_id,
            "journal_entry_id": je_id,
            "amount_due_cents": total_cents,
            "type": "credit_memo",
        }, indent=2))

    except ValueError as e:
        conn.rollback()
        print(json.dumps({"success": False, "error": str(e)}))
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
