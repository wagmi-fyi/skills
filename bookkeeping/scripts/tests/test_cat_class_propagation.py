#!/usr/bin/env python3
"""
Tests for per-posting QBO class propagation in journal_engine.create_journal_entry().

Regression coverage for the defect where a rule/bulk_cat categorization's class
landed only on the journal-entry metadata and was dropped from the individual P&L
postings — silently failing the downstream "no unclassed P&L posting" gate.

Covers:
- Single-class (rule + bulk) → P&L posting carries class_name; bank-offset stays class-less
- Mixed-class → explicit per-posting class wins; JE-level class is a fallback only
- Balance-sheet category postings never receive the fallback class
- The shared rule-metadata dict is not mutated
- Invalid per-posting class is rejected

Run:
    python3 -m unittest scripts.tests.test_cat_class_propagation
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
    # Chart of accounts: bank (asset), two income, one expense, one liability
    conn.execute("INSERT INTO chart_of_accounts (code, name, type) VALUES ('1000', 'Checking', 'asset')")
    conn.execute("INSERT INTO chart_of_accounts (code, name, type) VALUES ('4000', 'Product Sales', 'income')")
    conn.execute("INSERT INTO chart_of_accounts (code, name, type) VALUES ('4100', 'Other Sales', 'income')")
    conn.execute("INSERT INTO chart_of_accounts (code, name, type) VALUES ('5000', 'Supplies', 'expense')")
    conn.execute("INSERT INTO chart_of_accounts (code, name, type) VALUES ('2200', 'Sales Tax Payable', 'liability')")
    # Classes (tags with category='Class')
    conn.execute("INSERT INTO tags (name, category) VALUES ('ClassA', 'Class')")
    conn.execute("INSERT INTO tags (name, category) VALUES ('ClassB', 'Class')")
    conn.commit()
    return conn, path


def add_import(conn, amount_cents, balance_type='cash'):
    """Insert an unprocessed bank-feed import; return its id."""
    iid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO imports (id, source, type, banking_date, amount, raw_data, processed) "
        "VALUES (?, ?, 'bank_feed', '2026-05-01', ?, ?, 0)",
        (iid, '1000 - Checking', amount_cents, json.dumps({'Balance Type': balance_type})),
    )
    conn.commit()
    return iid


class ClassPropagationTests(unittest.TestCase):

    def setUp(self):
        self.conn, self.path = make_temp_db()

    def tearDown(self):
        self.conn.close()
        os.remove(self.path)

    # ----- helpers -----

    def _create(self, import_id, postings, metadata=None, class_name=None):
        import_data = journal_engine.get_import_data(self.conn, import_id)
        return journal_engine.create_journal_entry(
            self.conn, {'import_id': import_id, 'postings': postings}, import_data,
            metadata=metadata, class_name=class_name,
        )

    def _meta(self, je_id, account_code):
        row = self.conn.execute(
            "SELECT metadata FROM postings WHERE journal_entry_id=? AND account_code=?",
            (je_id, account_code),
        ).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    # ----- tests -----

    def test_rule_single_class_pl_gets_class_bank_stays_classless(self):
        """Rule categorization: expense posting carries class; rule keys survive; bank class-less."""
        iid = add_import(self.conn, -1000)  # withdrawal
        ok, je = self._create(
            iid, [{'account_code': '5000', 'contact': 'Acme', 'description': 'supplies'}],
            metadata={'rule_id': 'r1', 'rule_name': 'Office Supplies'}, class_name='ClassA',
        )
        self.assertTrue(ok, je)
        pl = self._meta(je, '5000')
        self.assertEqual(pl['class_name'], 'ClassA')
        self.assertEqual(pl['rule_id'], 'r1')          # rule metadata preserved
        self.assertEqual(pl['rule_name'], 'Office Supplies')
        bank = self._meta(je, '1000')
        self.assertNotIn('class_name', bank)           # bank-offset (BS) stays class-less
        self.assertEqual(bank['rule_id'], 'r1')

    def test_bulk_single_class_metadata_none(self):
        """AI categorization (metadata=None): income posting gets class; bank metadata stays NULL."""
        iid = add_import(self.conn, 1000)  # deposit
        ok, je = self._create(
            iid, [{'account_code': '4000', 'contact': 'Cust', 'description': 'sale'}],
            metadata=None, class_name='ClassA',
        )
        self.assertTrue(ok, je)
        self.assertEqual(self._meta(je, '4000'), {'class_name': 'ClassA'})
        self.assertIsNone(self._meta(je, '1000'))      # bank metadata column is NULL

    def test_mixed_class_per_posting_wins_over_fallback(self):
        """Explicit per-posting class is honored; JE-level class only fills the gaps."""
        iid = add_import(self.conn, 1000)  # deposit, split 600/400
        ok, je = self._create(
            iid,
            [
                {'account_code': '4000', 'contact': 'C', 'amount': 600, 'class_name': 'ClassB'},
                {'account_code': '4100', 'contact': 'C', 'amount': 400},
            ],
            metadata=None, class_name='ClassA',
        )
        self.assertTrue(ok, je)
        self.assertEqual(self._meta(je, '4000')['class_name'], 'ClassB')   # explicit, not overwritten
        self.assertEqual(self._meta(je, '4100')['class_name'], 'ClassA')  # fallback applied

    def test_balance_sheet_posting_stays_classless_under_fallback(self):
        """A liability category line gets no fallback class; the sibling P&L line does."""
        iid = add_import(self.conn, -1000)  # withdrawal, split 800 expense / 200 liability
        ok, je = self._create(
            iid,
            [
                {'account_code': '5000', 'contact': 'V', 'amount': 800},
                {'account_code': '2200', 'contact': 'V', 'amount': 200},
            ],
            metadata=None, class_name='ClassA',
        )
        self.assertTrue(ok, je)
        self.assertEqual(self._meta(je, '5000')['class_name'], 'ClassA')  # P&L classed
        self.assertIsNone(self._meta(je, '2200'))                       # BS stays class-less
        self.assertIsNone(self._meta(je, '1000'))                       # bank stays class-less

    def test_shared_metadata_dict_not_mutated(self):
        """The caller's rule-metadata dict must not gain a class_name key."""
        iid = add_import(self.conn, 1000)
        shared = {'rule_id': 'r1'}
        ok, je = self._create(
            iid,
            [
                {'account_code': '4000', 'contact': 'C', 'amount': 600},
                {'account_code': '4100', 'contact': 'C', 'amount': 400},
            ],
            metadata=shared, class_name='ClassA',
        )
        self.assertTrue(ok, je)
        self.assertEqual(self._meta(je, '4000')['class_name'], 'ClassA')
        self.assertEqual(self._meta(je, '4100')['class_name'], 'ClassA')
        self.assertNotIn('class_name', shared)         # original dict untouched

    def test_invalid_per_posting_class_rejected(self):
        """A per-posting class not present in tags is rejected before JE creation."""
        iid = add_import(self.conn, 1000)
        ok, res = self._create(
            iid, [{'account_code': '4000', 'contact': 'C', 'class_name': 'NOPE'}],
            metadata=None, class_name=None,
        )
        self.assertFalse(ok)
        self.assertIn('Invalid class', res)
        # No JE/postings should have been created
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0], 0)

    def test_no_class_preserves_prior_behavior(self):
        """With no class anywhere, posting metadata is unchanged (NULL) — no regression."""
        iid = add_import(self.conn, 1000)
        ok, je = self._create(
            iid, [{'account_code': '4000', 'contact': 'C', 'description': 'sale'}],
            metadata=None, class_name=None,
        )
        self.assertTrue(ok, je)
        self.assertIsNone(self._meta(je, '4000'))
        self.assertIsNone(self._meta(je, '1000'))


if __name__ == '__main__':
    unittest.main()
