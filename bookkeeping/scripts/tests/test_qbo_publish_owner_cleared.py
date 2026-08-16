#!/usr/bin/env python3
"""
Hermetic tests for the owner-cleared CM/VC publish phase (NO real QBO calls).

Covers _publishers/owner_cleared.py + query_owner_cleared_payments:

  * Happy path — the clearing JE publishes (real _query_je_postings + real
    transform; network mocked) and a zero-$ Payment links the floating
    CreditMemo to the JE's sub-ledger charge; the TAP is marked synced with
    the PAYMENT external_id (a payment-type id — never the JE id).
  * Phase-1 reuse — a clearing JE already published by the std-JE phase is
    NEVER published twice; its external_id is reused.
  * Idempotency — a second run selects nothing (TAP external_id set).
  * Pre-flights — unsynced parent CM skips (pending, retryable); missing
    clearing_je_id fails loud.
  * Disjointness — owner-cleared CM/VC TAPs are selected by EXACTLY one
    query; the three pre-existing TAP queries (as invoked by their
    publishers) neither gain nor lose rows. Receivable-parent owner-cleared
    TAPs stay with publish_payments (the receivable-parent set — already correct).

Run:
    python3 -m unittest scripts.tests.test_qbo_publish_owner_cleared
"""

import json
import os
import sqlite3
import sys
import tempfile
import types
import unittest
import uuid
from unittest import mock

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(THIS_DIR)
SKILL_DIR = os.path.dirname(SCRIPTS_DIR)
QBO_DIR = os.path.join(SKILL_DIR, 'adapters', 'qbo')
SCHEMA_PATH = os.path.join(SKILL_DIR, 'reference', 'schema.sql')

common = oc_pub = None


def _load_modules():
    global common, oc_pub
    if oc_pub is not None:
        return
    for m in [k for k in list(sys.modules) if k == '_shared' or k.startswith('_shared.')
              or k == '_publishers' or k.startswith('_publishers.')]:
        del sys.modules[m]
    while QBO_DIR in sys.path:
        sys.path.remove(QBO_DIR)
    sys.path.insert(0, QBO_DIR)
    stub = types.ModuleType('_shared.client')
    stub.save_tokens_if_available = lambda *a, **k: None
    stub.MAX_RETRIES = 3
    stub.MIN_REQUEST_INTERVAL = 0
    sys.modules['_shared.client'] = stub
    from _shared import common as _common
    from _publishers import owner_cleared as _oc
    common, oc_pub = _common, _oc


class _FakeRL:
    def wait(self):
        pass

    def trigger_backoff(self, *a):
        pass


def make_temp_db():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    for code, name, typ, remote in [
        ('1001', 'Bank', 'asset', 'QBO-BANK'),
        ('1200', 'A/R', 'asset', 'QBO-AR'),
        ('10150', 'Owner Clearing', 'asset', 'QBO-CLR'),
        ('4000', 'Sales', 'income', 'QBO-SALES'),
    ]:
        conn.execute("INSERT INTO chart_of_accounts (code, name, type, remote_id) VALUES (?,?,?,?)",
                     (code, name, typ, remote))
    conn.execute("INSERT INTO contacts (name, remote_id) VALUES ('CustA', 'QBO-CUST')")
    conn.commit()
    return conn, path


def _insert_je(conn, postings, memo, date='2025-03-15', sync='{"status":"pending"}'):
    je_id = str(uuid.uuid4())
    conn.execute("INSERT INTO journal_entries (id, transaction_date, memo, sync) VALUES (?,?,?,?)",
                 (je_id, date, memo, sync))
    for acct, direction, amount in postings:
        conn.execute("INSERT INTO postings (id, journal_entry_id, account_code, direction, amount) "
                     "VALUES (?,?,?,?,?)", (str(uuid.uuid4()), je_id, acct, direction, amount))
    return je_id


def _insert_ta(conn, ta_type, face, sync_obj, date='2025-03-15'):
    je_id = _insert_je(conn, [('4000', 'debit', face), ('1200', 'credit', face)], 'doc je', date)
    ta_id = str(uuid.uuid4())
    conn.execute("INSERT INTO trade_accounts (id, type, contact, document_date, journal_entry_id, sync, metadata) "
                 "VALUES (?,?,?,?,?,?,?)",
                 (ta_id, ta_type, 'CustA', date, je_id, json.dumps(sync_obj),
                  json.dumps({"balance_account_code": "1200"})))
    return ta_id


def _insert_tap(conn, ta_id, amount, metadata=None, import_id=None, source_ta_id=None,
                date='2025-03-20'):
    tap_id = str(uuid.uuid4())
    conn.execute("INSERT INTO trade_account_payments "
                 "(id, trade_account_id, import_id, source_ta_id, payment_date, amount, sync, metadata) "
                 "VALUES (?,?,?,?,?,?,?,?)",
                 (tap_id, ta_id, import_id, source_ta_id, date, amount,
                  '{"status":"pending"}', json.dumps(metadata or {})))
    return tap_id


def insert_owner_cleared_cm(conn, face=14037):
    """A synced CM + pending clearing JE (DR A/R / CR owner clearing) + the
    owner-cleared TAP carrying clearing_je_id — the owner-cleared shape."""
    cm_ta = _insert_ta(conn, 'credit_memo', face, {"status": "synced", "external_id": "CM-9"})
    clearing_je = _insert_je(conn, [('1200', 'debit', face), ('10150', 'credit', face)],
                             'owner clear', date='2025-03-20')
    tap = _insert_tap(conn, cm_ta, face, metadata={'clearing_je_id': clearing_je})
    conn.commit()
    return cm_ta, clearing_je, tap


class OwnerClearedPhaseTests(unittest.TestCase):

    def setUp(self):
        _load_modules()
        self.conn, self.path = make_temp_db()
        self.captured = []
        self.je_publishes = []

        def fake_publish_obj(client, rate_limiter, obj, env_path):
            obj.Id = 'PMT-500'
            self.captured.append(obj)
            return 'PMT-500', None

        def fake_publish_je(client, rate_limiter, je_id, qbo_entry, env_path):
            self.je_publishes.append((je_id, qbo_entry))
            return {'je_id': je_id, 'success': True, 'external_id': 'JE-77',
                    'error_code': None, 'error_message': None}

        self._patches = [
            mock.patch.object(oc_pub, 'publish_single_qbo_object', fake_publish_obj),
            mock.patch.object(oc_pub, 'publish_single_entry', fake_publish_je),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.conn.close()
        os.remove(self.path)

    def _run(self):
        return oc_pub.publish_owner_cleared(
            None, _FakeRL(), self.conn, {}, 'pending', None, None, '')

    def _tap_sync(self, tap_id):
        return tuple(self.conn.execute(
            "SELECT json_extract(sync,'$.status'), json_extract(sync,'$.external_id') "
            "FROM trade_account_payments WHERE id = ?", (tap_id,)).fetchone())

    def _lines(self, obj):
        out = {}
        for ln in obj.Line:
            lt = ln['LinkedTxn'][0]
            out[(lt['TxnType'], str(lt['TxnId']))] = ln['Amount']
        return out

    def test_happy_path_je_plus_linking_payment(self):
        cm_ta, clearing_je, tap = insert_owner_cleared_cm(self.conn)
        processed, failed, skipped, errors, ext = self._run()
        self.assertEqual((processed, failed, skipped), (1, 0, 0), errors)

        # Clearing JE went through the real transform (memo + [bk:] tag) and
        # was marked synced with ITS id.
        self.assertEqual(len(self.je_publishes), 1)
        je_id, qbo_entry = self.je_publishes[0]
        self.assertEqual(je_id, clearing_je)
        self.assertEqual(len(qbo_entry['Line']), 2)
        self.assertIn('[bk:', qbo_entry['PrivateNote'])
        je_sync = self.conn.execute(
            "SELECT json_extract(sync,'$.external_id') FROM journal_entries WHERE id = ?",
            (clearing_je,)).fetchone()[0]
        self.assertEqual(je_sync, 'JE-77')

        # Zero-$ Payment nets the floating CM against the JE's charge —
        # exactly the manual fix shape (placeholder_cm_to_je).
        pmt = self.captured[0]
        self.assertEqual(pmt.TotalAmt, 0)
        self.assertEqual(self._lines(pmt), {
            ('JournalEntry', 'JE-77'): 140.37,
            ('CreditMemo', 'CM-9'): 140.37,
        })
        self.assertIn('[bk:', pmt.PrivateNote)
        self.assertEqual(pmt._bk_locator['entity'], 'Payment')

        # TAP carries the PAYMENT external_id — a payment-type id, never the JE's.
        self.assertEqual(self._tap_sync(tap), ('synced', 'PMT-500'))

    def test_phase1_published_je_is_reused_not_republished(self):
        cm_ta, clearing_je, tap = insert_owner_cleared_cm(self.conn)
        # Phase 1 (std-JE publish) already pushed the clearing JE.
        self.conn.execute("UPDATE journal_entries SET sync = ? WHERE id = ?",
                          (json.dumps({"status": "synced", "external_id": "JE-55"}), clearing_je))
        self.conn.commit()
        processed, failed, skipped, errors, ext = self._run()
        self.assertEqual((processed, failed), (1, 0), errors)
        self.assertEqual(self.je_publishes, [], "clearing JE must never publish twice")
        self.assertEqual(self._lines(self.captured[0]), {
            ('JournalEntry', 'JE-55'): 140.37,
            ('CreditMemo', 'CM-9'): 140.37,
        })

    def test_second_run_is_idempotent(self):
        insert_owner_cleared_cm(self.conn)
        self._run()
        self.captured.clear()
        self.je_publishes.clear()
        processed, failed, skipped, errors, ext = self._run()
        self.assertEqual((processed, failed, skipped), (0, 0, 0))
        self.assertEqual(self.captured, [])
        self.assertEqual(self.je_publishes, [])

    def test_unsynced_parent_cm_skips_and_stays_pending(self):
        cm_ta, clearing_je, tap = insert_owner_cleared_cm(self.conn)
        self.conn.execute("UPDATE trade_accounts SET sync = '{\"status\":\"pending\"}' WHERE id = ?",
                          (cm_ta,))
        self.conn.commit()
        processed, failed, skipped, errors, ext = self._run()
        self.assertEqual((processed, failed, skipped), (0, 0, 1))
        self.assertEqual(self._tap_sync(tap)[0], 'pending')  # retryable next run

    def test_missing_clearing_je_id_fails_loud(self):
        cm_ta = _insert_ta(self.conn, 'credit_memo', 500,
                           {"status": "synced", "external_id": "CM-9"})
        tap = _insert_tap(self.conn, cm_ta, 500, metadata={})
        self.conn.commit()
        processed, failed, skipped, errors, ext = self._run()
        self.assertEqual((processed, failed), (0, 1))
        self.assertTrue(any(e['error_code'] == 'OWNER_CLEARING_JE_MISSING' for e in errors), errors)
        self.assertEqual(self._tap_sync(tap)[0], 'error')


class DisjointnessTests(unittest.TestCase):
    """The new query and the three existing TAP queries (as their publishers
    invoke them) must partition the pending-TAP population — no TAP selected
    twice, and only genuinely-unroutable TAPs selected by none."""

    def setUp(self):
        _load_modules()
        self.conn, self.path = make_temp_db()

    def tearDown(self):
        self.conn.close()
        os.remove(self.path)

    def test_partition_of_pending_taps(self):
        conn = self.conn
        # (a) owner-cleared CM TAP — the new phase's population
        _, _, tap_a = insert_owner_cleared_cm(conn)
        # (b) receivable-parent owner-cleared TAP (the receivable-parent set): import_id NULL,
        # handled correctly TODAY by publish_payments (deposit to 10150).
        r_ta = _insert_ta(conn, 'receivable', 900, {"status": "synced", "external_id": "INV-1"})
        tap_b = _insert_tap(conn, r_ta, 900, metadata={'payment_account_code': '10150'})
        # (c) bank-funded receivable TAP
        imp_id = str(uuid.uuid4())
        conn.execute("INSERT INTO imports (id, source, type, banking_date, amount, raw_data, processed) "
                     "VALUES (?, '1001 - Bank', 'feed', '2025-03-21', 700, '{}', 0)", (imp_id,))
        r_ta2 = _insert_ta(conn, 'receivable', 700, {"status": "synced", "external_id": "INV-2"})
        tap_c = _insert_tap(conn, r_ta2, 700, metadata={'payment_account_code': '1001'},
                            import_id=imp_id)
        # (d) credit application: CM applied to an invoice
        cm_src = _insert_ta(conn, 'credit_memo', 300, {"status": "synced", "external_id": "CM-2"})
        r_ta3 = _insert_ta(conn, 'receivable', 300, {"status": "synced", "external_id": "INV-3"})
        tap_d = _insert_tap(conn, r_ta3, 300, source_ta_id=cm_src)
        conn.commit()

        oc = {r['tap_id'] for r in common.query_owner_cleared_payments(conn, 'pending', None, None)}
        # As invoked: payments.py passes ta_type='receivable', bill_payments 'payable'.
        recv = {r['tap_id'] for r in common.query_trade_account_payments(
            conn, 'pending', None, None, ta_type='receivable')}
        pay = {r['tap_id'] for r in common.query_trade_account_payments(
            conn, 'pending', None, None, ta_type='payable')}
        capp = {r['tap_id'] for r in common.query_credit_applications(conn, 'pending', None, None)}

        self.assertEqual(oc, {tap_a})
        self.assertEqual(recv, {tap_b, tap_c})
        self.assertEqual(pay, set())
        self.assertEqual(capp, {tap_d})

        # Pairwise disjoint, and the union covers every pending TAP.
        sets = [oc, recv, pay, capp]
        for i in range(len(sets)):
            for j in range(i + 1, len(sets)):
                self.assertEqual(sets[i] & sets[j], set(),
                                 f"query overlap between set {i} and {j}")
        all_pending = {r[0] for r in self.conn.execute(
            "SELECT id FROM trade_account_payments WHERE json_extract(sync,'$.status')='pending'")}
        self.assertEqual(oc | recv | pay | capp, all_pending,
                         "a pending TAP is selected by NO phase — new fall-through gap")


if __name__ == '__main__':
    unittest.main()
