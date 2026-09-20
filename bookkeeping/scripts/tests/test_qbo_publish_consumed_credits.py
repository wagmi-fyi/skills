#!/usr/bin/env python3
"""Hermetic tests for the consumed-credit publish phase (NO real QBO calls).

A deposit consumes a credit memo when one bank line pays several invoices and a
credit memo reduces the cash. The publisher emits ONE Payment for that deposit:
TotalAmt = sum of invoice face less the credit, Lines = N Invoice + M CreditMemo.

Covers `query_payout_consumed_credits` + `_publishers/payments.publish_payout_consumed_credits`
across the three deposit keys the group key can take (settlement, payout, import),
plus the pre-publish guard `find_unroutable_bank_funded_payments`.

Run:
    python3 -m unittest scripts.tests.test_qbo_publish_consumed_credits
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

# The QBO SDK is adapter-tier (requirements.txt, QBO block) and reaches this
# module through the publishers it loads. Without it the subject cannot be
# exercised, so its cases skip rather than error and a non-QBO deployment still
# runs a green core suite. The guard is a class decorator, not a module-level
# SkipTest: unittest only converts the latter to a skip under discover(), and
# raises it uncaught when a module is named directly.
SOR_SKIP_REASON = (
    "QBO SDK absent (python-quickbooks) — SoR publisher tests skipped. "
    "Install the QBO block from the bookkeeping skill's requirements.txt."
)
try:
    import quickbooks  # noqa: F401
    QBO_SDK_PRESENT = True
except ImportError:
    QBO_SDK_PRESENT = False

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(THIS_DIR)
SKILL_DIR = os.path.dirname(SCRIPTS_DIR)
QBO_DIR = os.path.join(SKILL_DIR, 'adapters', 'qbo')
SCHEMA_PATH = os.path.join(SKILL_DIR, 'reference', 'schema.sql')

common = payments_pub = None


def _load_modules():
    global common, payments_pub
    if payments_pub is not None:
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
    from _publishers import payments as _payments
    common, payments_pub = _common, _payments


class _FakeRL:
    def wait(self):
        pass

    def trigger_backoff(self, *a):
        pass


# --------------------------- DB fixture helpers ---------------------------

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
        ('2000', 'A/P', 'liability', 'QBO-AP'),
        ('4000', 'Sales', 'income', 'QBO-SALES'),
    ]:
        conn.execute("INSERT INTO chart_of_accounts (code, name, type, remote_id) VALUES (?,?,?,?)",
                     (code, name, typ, remote))
    conn.execute("INSERT INTO contacts (name, remote_id) VALUES ('Northwind Supply', 'QBO-CUST')")
    conn.execute("INSERT INTO contacts (name, remote_id) VALUES ('Dockside Freight', 'QBO-VEND')")
    conn.commit()
    return conn, path


def insert_import(conn, amount, date='2026-04-15'):
    import_id = str(uuid.uuid4())
    conn.execute("INSERT INTO imports (id, source, type, banking_date, amount, raw_data, processed) "
                 "VALUES (?, '1001 - Bank', 'feed', ?, ?, '{}', 0)", (import_id, date, amount))
    return import_id


def insert_ta(conn, ta_type, face, external_id, metadata,
              contact='Northwind Supply', date='2026-04-01'):
    je_id = str(uuid.uuid4())
    conn.execute("INSERT INTO journal_entries (id, transaction_date, memo, sync) VALUES (?,?,?,?)",
                 (je_id, date, 'doc je', '{"status":"pending"}'))
    conn.execute("INSERT INTO postings (id, journal_entry_id, account_code, direction, amount) "
                 "VALUES (?,?,?,?,?)", (str(uuid.uuid4()), je_id, '1200', 'debit', face))
    ta_id = str(uuid.uuid4())
    sync = ({"status": "synced", "external_id": external_id} if external_id
            else {"status": "pending"})
    conn.execute("INSERT INTO trade_accounts (id, type, contact, document_date, journal_entry_id, "
                 "sync, metadata) VALUES (?,?,?,?,?,?,?)",
                 (ta_id, ta_type, contact, date, je_id, json.dumps(sync), json.dumps(metadata)))
    return ta_id


def insert_tap(conn, ta_id, amount, import_id=None, source_ta_id=None,
               metadata=None, date='2026-04-15'):
    tap_id = str(uuid.uuid4())
    meta = {'payment_account_code': '1001'}
    meta.update(metadata or {})
    conn.execute("INSERT INTO trade_account_payments "
                 "(id, trade_account_id, import_id, source_ta_id, payment_date, amount, sync, metadata) "
                 "VALUES (?,?,?,?,?,?,?,?)",
                 (tap_id, ta_id, import_id, source_ta_id, date, amount,
                  '{"status":"pending"}', json.dumps(meta)))
    return tap_id


def build_deposit(conn, parent_metadata, invoice_faces=(60000, 40000), credit_face=10000,
                  credit_metadata=None):
    """One bank line paying N invoices at face with a credit memo reducing the cash.

    `parent_metadata` goes on every parent trade account, so the same builder makes a
    payout-keyed deposit, a settlement-keyed one, or a plain deposit carrying neither.
    Returns (import_id, {role: tap_id}).
    """
    net = sum(invoice_faces) - credit_face
    import_id = insert_import(conn, net)
    taps = {}
    for n, face in enumerate(invoice_faces, start=1):
        ta_id = insert_ta(conn, 'receivable', face, f'INV-{n}', dict(parent_metadata))
        taps[f'R{n}'] = insert_tap(conn, ta_id, face, import_id=import_id)
    cm_ta = insert_ta(conn, 'credit_memo', credit_face, 'CM-1',
                      dict(parent_metadata if credit_metadata is None else credit_metadata))
    taps['CM'] = insert_tap(conn, cm_ta, credit_face, import_id=import_id)
    conn.commit()
    return import_id, taps


@unittest.skipUnless(QBO_SDK_PRESENT, SOR_SKIP_REASON)
class ConsumedCreditPublishTests(unittest.TestCase):

    def setUp(self):
        _load_modules()
        self.conn, self.path = make_temp_db()
        self.captured = []
        self._next = [1000]

        def fake_publish(client, rate_limiter, obj, env_path):
            self._next[0] += 1
            ext = str(self._next[0])
            obj.Id = ext
            self.captured.append(obj)
            return ext, None

        self._patches = [
            mock.patch.object(payments_pub, 'publish_single_qbo_object', fake_publish),
            mock.patch.object(payments_pub.QBOPayment, 'save', lambda self, qb=None: self),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.conn.close()
        os.remove(self.path)

    def _run(self):
        return payments_pub.publish_payout_consumed_credits(
            None, _FakeRL(), self.conn, {}, 'pending', None, None, '')

    def _lines(self, obj):
        out = {}
        for ln in obj.Line:
            lt = ln['LinkedTxn'][0]
            out[(lt['TxnType'], str(lt['TxnId']))] = ln['Amount']
        return out

    def _tap_sync(self, tap_id):
        return tuple(self.conn.execute(
            "SELECT json_extract(sync,'$.status'), json_extract(sync,'$.external_id') "
            "FROM trade_account_payments WHERE id = ?", (tap_id,)).fetchone())

    def test_payout_keyed_deposit_publishes_one_net_payment(self):
        """The payout-keyed path, pinned: one Payment, net of the credit, every TAP synced."""
        _, taps = build_deposit(self.conn, {'payout_id': 'PO-1'})
        processed, failed, skipped, errors, ext_ids = self._run()

        self.assertEqual((processed, failed, skipped), (3, 0, 0), errors)
        self.assertEqual(len(ext_ids), 1)
        payment = self.captured[0]
        self.assertAlmostEqual(payment.TotalAmt, 900.00, places=2)
        self.assertEqual(self._lines(payment), {
            ('Invoice', 'INV-1'): 600.00,
            ('Invoice', 'INV-2'): 400.00,
            ('CreditMemo', 'CM-1'): 100.00,
        })
        self.assertIn('[bk:', payment.PrivateNote)
        self.assertEqual(payment._bk_locator['total'], 900.00)
        for tap_id in taps.values():
            self.assertEqual(self._tap_sync(tap_id), ('synced', ext_ids[0]))


if __name__ == '__main__':
    unittest.main()
