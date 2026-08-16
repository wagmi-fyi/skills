#!/usr/bin/env python3
"""
Tests for credit_memo / vendor_credit balance math and apply_credit validation.

Covers:
- compute_amount_due, compute_applied_amount, compute_status for new types
- apply_credit validation matrix: type pair, contact match, voided, over-apply

Run:
    .venv/bin/python -m unittest scripts.tests.test_credit_balance
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
import uuid

# Path setup matches how skill scripts import _shared
SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SCRIPTS_DIR, '_shared'))
sys.path.insert(0, SCRIPTS_DIR)

from trade_account_utils import (
    compute_amount_due,
    compute_paid_amount,
    compute_applied_amount,
    compute_status,
    is_credit_memo,
)

SCHEMA_PATH = os.path.join(
    os.path.dirname(SCRIPTS_DIR),  # bookkeeping/
    'reference', 'schema.sql'
)


def make_temp_db():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    conn = sqlite3.connect(path)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    # Seed: chart of accounts + contacts
    conn.execute("INSERT INTO chart_of_accounts (code, name, type) VALUES ('1200', 'A/R', 'asset')")
    conn.execute("INSERT INTO chart_of_accounts (code, name, type) VALUES ('2000', 'A/P', 'liability')")
    conn.execute("INSERT INTO chart_of_accounts (code, name, type) VALUES ('4000', 'Sales Adj', 'income')")
    conn.execute("INSERT INTO chart_of_accounts (code, name, type) VALUES ('5000', 'Selling', 'expense')")
    conn.execute("INSERT INTO contacts (name) VALUES ('CustA')")
    conn.execute("INSERT INTO contacts (name) VALUES ('CustB')")
    conn.execute("INSERT INTO contacts (name) VALUES ('VendA')")
    conn.commit()
    return conn, path


def insert_je_with_postings(conn, postings, contact, txn_date='2026-04-01'):
    je_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO journal_entries (id, transaction_date, memo, sync) VALUES (?, ?, ?, ?)",
        (je_id, txn_date, 'test', '{"status":"pending"}')
    )
    for p in postings:
        conn.execute(
            "INSERT INTO postings (id, journal_entry_id, account_code, direction, amount, contact) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), je_id, p['account_code'], p['direction'], p['amount'], contact)
        )
    return je_id


def insert_ta(conn, ta_type, contact, je_id, balance_account_code, document_date='2026-04-01'):
    ta_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO trade_accounts (id, type, contact, document_date, journal_entry_id, sync, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ta_id, ta_type, contact, document_date, je_id,
         '{"status":"pending"}',
         json.dumps({"balance_account_code": balance_account_code}))
    )
    return ta_id


class CreditBalanceTests(unittest.TestCase):

    def setUp(self):
        self.conn, self.path = make_temp_db()

    def tearDown(self):
        self.conn.close()
        os.remove(self.path)

    def test_amount_due_for_credit_memo(self):
        """CM JE has CR 1200 1000 + DR 4000 1000. Amount due = 1000."""
        je = insert_je_with_postings(self.conn, [
            {'account_code': '4000', 'direction': 'debit', 'amount': 1000},
            {'account_code': '1200', 'direction': 'credit', 'amount': 1000},
        ], 'CustA')
        cm = insert_ta(self.conn, 'credit_memo', 'CustA', je, '1200')
        self.assertEqual(compute_amount_due(self.conn, je, '1200'), 1000)

    def test_amount_due_for_vendor_credit(self):
        """VC JE has DR 2000 500 + CR 5000 500. Amount due = 500."""
        je = insert_je_with_postings(self.conn, [
            {'account_code': '5000', 'direction': 'credit', 'amount': 500},
            {'account_code': '2000', 'direction': 'debit', 'amount': 500},
        ], 'VendA')
        insert_ta(self.conn, 'vendor_credit', 'VendA', je, '2000')
        self.assertEqual(compute_amount_due(self.conn, je, '2000'), 500)

    def test_compute_applied_amount(self):
        """Sum of TAPs by source_ta_id."""
        # Build a CM and an invoice
        cm_je = insert_je_with_postings(self.conn, [
            {'account_code': '4000', 'direction': 'debit', 'amount': 1000},
            {'account_code': '1200', 'direction': 'credit', 'amount': 1000},
        ], 'CustA')
        inv_je = insert_je_with_postings(self.conn, [
            {'account_code': '1200', 'direction': 'debit', 'amount': 5000},
            {'account_code': '4000', 'direction': 'credit', 'amount': 5000},
        ], 'CustA')
        cm = insert_ta(self.conn, 'credit_memo', 'CustA', cm_je, '1200')
        inv = insert_ta(self.conn, 'receivable', 'CustA', inv_je, '1200')

        # Apply 300 then 200 (split across two TAPs, same source)
        self.conn.execute(
            "INSERT INTO trade_account_payments (id, trade_account_id, source_ta_id, payment_date, amount, sync) "
            "VALUES (?, ?, ?, '2026-04-15', 300, '{\"status\":\"pending\"}')",
            (str(uuid.uuid4()), inv, cm)
        )
        self.conn.execute(
            "INSERT INTO trade_account_payments (id, trade_account_id, source_ta_id, payment_date, amount, sync) "
            "VALUES (?, ?, ?, '2026-04-16', 200, '{\"status\":\"pending\"}')",
            (str(uuid.uuid4()), inv, cm)
        )
        self.conn.commit()

        self.assertEqual(compute_applied_amount(self.conn, cm), 500)
        self.assertEqual(compute_paid_amount(self.conn, inv), 500)
        # CM has no payments by trade_account_id, only by source_ta_id
        self.assertEqual(compute_paid_amount(self.conn, cm), 0)

    def test_is_credit_memo_short_circuit(self):
        """Type='credit_memo' or 'vendor_credit' returns True without checking postings."""
        # No JE postings — short-circuit returns True
        self.assertTrue(is_credit_memo(self.conn, 'fake_je', 'fake', 'credit_memo'))
        self.assertTrue(is_credit_memo(self.conn, 'fake_je', 'fake', 'vendor_credit'))
        # Receivable with no postings — returns False
        self.assertFalse(is_credit_memo(self.conn, 'fake_je', 'fake', 'receivable'))

    def test_apply_credit_happy_path(self):
        """apply_credit.py: $300 of $1000 CM applied to $5000 invoice."""
        cm_je = insert_je_with_postings(self.conn, [
            {'account_code': '4000', 'direction': 'debit', 'amount': 1000},
            {'account_code': '1200', 'direction': 'credit', 'amount': 1000},
        ], 'CustA')
        inv_je = insert_je_with_postings(self.conn, [
            {'account_code': '1200', 'direction': 'debit', 'amount': 5000},
            {'account_code': '4000', 'direction': 'credit', 'amount': 5000},
        ], 'CustA')
        cm = insert_ta(self.conn, 'credit_memo', 'CustA', cm_je, '1200')
        inv = insert_ta(self.conn, 'receivable', 'CustA', inv_je, '1200')
        self.conn.commit()

        result = self._run_apply_credit(cm, inv, 300)
        self.assertTrue(result['success'])
        self.assertEqual(result['source_remaining_cents'], 700)
        self.assertEqual(result['target_remaining_cents'], 4700)

    def test_apply_credit_type_mismatch(self):
        """CM applied to a payable bill: rejected."""
        cm_je = insert_je_with_postings(self.conn, [
            {'account_code': '4000', 'direction': 'debit', 'amount': 1000},
            {'account_code': '1200', 'direction': 'credit', 'amount': 1000},
        ], 'CustA')
        bill_je = insert_je_with_postings(self.conn, [
            {'account_code': '5000', 'direction': 'debit', 'amount': 800},
            {'account_code': '2000', 'direction': 'credit', 'amount': 800},
        ], 'CustA')
        cm = insert_ta(self.conn, 'credit_memo', 'CustA', cm_je, '1200')
        bill = insert_ta(self.conn, 'payable', 'CustA', bill_je, '2000')
        self.conn.commit()

        result = self._run_apply_credit(cm, bill, 300)
        self.assertFalse(result['success'])
        self.assertIn('type mismatch', result['error'])

    def test_apply_credit_contact_mismatch(self):
        """CM customer != Invoice customer: rejected."""
        cm_je = insert_je_with_postings(self.conn, [
            {'account_code': '4000', 'direction': 'debit', 'amount': 1000},
            {'account_code': '1200', 'direction': 'credit', 'amount': 1000},
        ], 'CustA')
        inv_je = insert_je_with_postings(self.conn, [
            {'account_code': '1200', 'direction': 'debit', 'amount': 5000},
            {'account_code': '4000', 'direction': 'credit', 'amount': 5000},
        ], 'CustB')
        cm = insert_ta(self.conn, 'credit_memo', 'CustA', cm_je, '1200')
        inv = insert_ta(self.conn, 'receivable', 'CustB', inv_je, '1200')
        self.conn.commit()

        result = self._run_apply_credit(cm, inv, 300)
        self.assertFalse(result['success'])
        self.assertIn('contact mismatch', result['error'])

    def test_apply_credit_over_apply(self):
        """amount > source remaining: rejected."""
        cm_je = insert_je_with_postings(self.conn, [
            {'account_code': '4000', 'direction': 'debit', 'amount': 100},
            {'account_code': '1200', 'direction': 'credit', 'amount': 100},
        ], 'CustA')
        inv_je = insert_je_with_postings(self.conn, [
            {'account_code': '1200', 'direction': 'debit', 'amount': 5000},
            {'account_code': '4000', 'direction': 'credit', 'amount': 5000},
        ], 'CustA')
        cm = insert_ta(self.conn, 'credit_memo', 'CustA', cm_je, '1200')
        inv = insert_ta(self.conn, 'receivable', 'CustA', inv_je, '1200')
        self.conn.commit()

        result = self._run_apply_credit(cm, inv, 500)
        self.assertFalse(result['success'])
        self.assertIn('exceeds source remaining', result['error'])

    def test_apply_credit_voided_source(self):
        cm_je = insert_je_with_postings(self.conn, [
            {'account_code': '4000', 'direction': 'debit', 'amount': 1000},
            {'account_code': '1200', 'direction': 'credit', 'amount': 1000},
        ], 'CustA')
        inv_je = insert_je_with_postings(self.conn, [
            {'account_code': '1200', 'direction': 'debit', 'amount': 5000},
            {'account_code': '4000', 'direction': 'credit', 'amount': 5000},
        ], 'CustA')
        cm = insert_ta(self.conn, 'credit_memo', 'CustA', cm_je, '1200')
        inv = insert_ta(self.conn, 'receivable', 'CustA', inv_je, '1200')
        self.conn.execute(
            "UPDATE trade_accounts SET voided_at = '2026-04-10' WHERE id = ?", (cm,)
        )
        self.conn.commit()

        result = self._run_apply_credit(cm, inv, 300)
        self.assertFalse(result['success'])
        self.assertIn('voided', result['error'])

    def test_apply_credit_no_je_side_effect(self):
        """apply_credit must NOT create a clearing JE — only a TAP row."""
        cm_je = insert_je_with_postings(self.conn, [
            {'account_code': '4000', 'direction': 'debit', 'amount': 1000},
            {'account_code': '1200', 'direction': 'credit', 'amount': 1000},
        ], 'CustA')
        inv_je = insert_je_with_postings(self.conn, [
            {'account_code': '1200', 'direction': 'debit', 'amount': 5000},
            {'account_code': '4000', 'direction': 'credit', 'amount': 5000},
        ], 'CustA')
        cm = insert_ta(self.conn, 'credit_memo', 'CustA', cm_je, '1200')
        inv = insert_ta(self.conn, 'receivable', 'CustA', inv_je, '1200')
        self.conn.commit()

        je_count_before = self.conn.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0]
        result = self._run_apply_credit(cm, inv, 300)
        je_count_after = self.conn.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0]
        self.assertTrue(result['success'])
        self.assertEqual(je_count_before, je_count_after, "apply_credit must not create a JE")

    # ----- helpers -----

    def _run_apply_credit(self, source_ta_id, target_ta_id, amount_cents):
        """Run apply_credit.py as a subprocess against this test DB."""
        import subprocess

        # Make a config that points apply_credit.py at our temp DB
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(f"""
user_name: Test
client_name: Test
client_id: test
module_root: "~/.claude/skills/bookkeeping"
local_dir: "{os.path.dirname(self.path)}"
output_folder: "{os.path.dirname(self.path)}/_bk-output"
database_dir: "{os.path.dirname(self.path)}"
database_name: "{os.path.basename(self.path)}"
default_system_of_record: "QBO"
period_type: "date-range"
fiscal_calendar: ""
coding:
  min_confidence_to_categorize: 5
  min_confidence_to_auto_approve: 9
""")
            cfg = f.name

        # Need to commit any in-memory state to the file before subprocess reads it
        self.conn.commit()

        env = os.environ.copy()
        env['BOOKKEEPING_CONFIG_PATH'] = cfg
        proc = subprocess.run(
            [sys.executable,
             os.path.join(SCRIPTS_DIR, 'apply_credit.py'),
             '--source_ta_id', source_ta_id,
             '--target_ta_id', target_ta_id,
             '--amount_cents', str(amount_cents),
             '--application_date', '2026-04-15'],
            env=env, capture_output=True, text=True
        )
        os.unlink(cfg)
        return json.loads(proc.stdout) if proc.stdout else {'success': False, 'error': proc.stderr}


if __name__ == '__main__':
    unittest.main()
