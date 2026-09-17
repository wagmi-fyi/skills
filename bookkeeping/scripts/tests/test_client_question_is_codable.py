#!/usr/bin/env python3
"""
Tests that a client question can be coded once it is answered. The engine
refuses an import that already has a journal entry (processed=1). It accepts an
unprocessed import (0) and a client question (2). The payment script's import
lookup follows the same rule.

Run:
    python3 -m unittest scripts.tests.test_client_question_is_codable
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

import journal_engine  # noqa: E402
import apply_payment  # noqa: E402

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
    conn.execute("INSERT INTO chart_of_accounts (code, name, type) VALUES ('1000', 'Checking', 'asset')")
    conn.execute("INSERT INTO chart_of_accounts (code, name, type) VALUES ('5000', 'Supplies', 'expense')")
    conn.commit()
    return conn, path


def add_import(conn, processed, amount_cents=-1000):
    """Insert a bank-feed import with the given processed flag; return its id."""
    iid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO imports (id, source, type, banking_date, amount, raw_data, processed) "
        "VALUES (?, ?, 'bank_feed', '2026-05-01', ?, ?, ?)",
        (iid, '1000 - Checking', amount_cents, json.dumps({'Balance Type': 'cash'}), processed),
    )
    conn.commit()
    return iid


class ClientQuestionCodableTests(unittest.TestCase):

    def setUp(self):
        self.conn, self.path = make_temp_db()

    def tearDown(self):
        self.conn.close()
        os.remove(self.path)

    def _code(self, import_id):
        import_data = journal_engine.get_import_data(self.conn, import_id)
        return journal_engine.create_journal_entry(
            self.conn,
            {'import_id': import_id,
             'postings': [{'account_code': '5000', 'contact': 'Acme', 'description': 'supplies'}]},
            import_data,
        )

    def _flag(self, import_id):
        return self.conn.execute(
            "SELECT processed FROM imports WHERE id=?", (import_id,)
        ).fetchone()[0]

    def test_unprocessed_import_passes(self):
        iid = add_import(self.conn, 0)
        data = journal_engine.get_import_data(self.conn, iid)
        self.assertEqual(data['id'], iid)

    def test_client_question_passes(self):
        iid = add_import(self.conn, 2)
        data = journal_engine.get_import_data(self.conn, iid)
        self.assertEqual(data['id'], iid)

    def test_import_with_entry_is_refused(self):
        iid = add_import(self.conn, 1)
        with self.assertRaises(ValueError) as cm:
            journal_engine.get_import_data(self.conn, iid)
        self.assertIn('already has a journal entry', str(cm.exception))

    def test_answered_client_question_is_coded(self):
        iid = add_import(self.conn, 2)
        ok, je = self._code(iid)
        self.assertTrue(ok, je)
        rows = self.conn.execute(
            "SELECT je.id FROM journal_entries je WHERE je.import_id=?", (iid,)
        ).fetchall()
        self.assertEqual(rows, [(je,)])
        totals = dict(self.conn.execute(
            "SELECT direction, SUM(amount) FROM postings WHERE journal_entry_id=? GROUP BY direction",
            (je,),
        ).fetchall())
        self.assertEqual(totals, {'debit': 1000, 'credit': 1000})
        self.assertEqual(self._flag(iid), 1)

    def test_coded_client_question_offered_again_is_refused(self):
        iid = add_import(self.conn, 2)
        ok, je = self._code(iid)
        self.assertTrue(ok, je)
        with self.assertRaises(ValueError) as cm:
            self._code(iid)
        self.assertIn('already has a journal entry', str(cm.exception))
        count = self.conn.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0]
        self.assertEqual(count, 1)

    def test_payment_lookup_accepts_client_question(self):
        iid = add_import(self.conn, 2)
        self.assertEqual(apply_payment.get_import_account_code(self.conn, iid), '1000')

    def test_payment_lookup_refuses_import_with_entry(self):
        iid = add_import(self.conn, 1)
        with self.assertRaises(ValueError) as cm:
            apply_payment.get_import_account_code(self.conn, iid)
        self.assertIn('already has a journal entry', str(cm.exception))


if __name__ == '__main__':
    unittest.main()
