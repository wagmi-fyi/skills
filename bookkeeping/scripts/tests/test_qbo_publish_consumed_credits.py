#!/usr/bin/env python3
"""Hermetic tests for the consumed-credit publish phase (NO real QBO calls).

A deposit consumes a credit memo when one bank line pays several invoices and a
credit memo reduces the cash. The publisher emits ONE Payment for that deposit:
TotalAmt = sum of invoice face less the credit, Lines = N Invoice + M CreditMemo.

Covers `query_payout_consumed_credits` + `_publishers/payments.publish_payout_consumed_credits`
across the three deposit keys the group key can take (settlement, payout, import),
plus the pre-publish guard `find_bank_funded_payment_gaps`.

Run:
    python3 -m unittest scripts.tests.test_qbo_publish_consumed_credits
"""

import importlib.util
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import types
import unittest
import uuid
from contextlib import redirect_stdout
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


def _drop_adapter_modules():
    """Forget the qbo adapter packages so the next importer gets its own. The stub
    _load_modules installs for `_shared.client` must not reach publish.py."""
    global common, payments_pub
    for m in [k for k in list(sys.modules) if k == '_shared' or k.startswith('_shared.')
              or k == '_publishers' or k.startswith('_publishers.')
              or k == 'publish_under_test']:
        del sys.modules[m]
    common = payments_pub = None


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

    def _bank_amount(self, import_id):
        """What the bank line is worth, in dollars, straight from the imports row."""
        cents = self.conn.execute(
            "SELECT amount FROM imports WHERE id = ?", (import_id,)).fetchone()[0]
        return round(cents / 100.0, 2)

    def _tap_sync(self, tap_id):
        return tuple(self.conn.execute(
            "SELECT json_extract(sync,'$.status'), json_extract(sync,'$.external_id') "
            "FROM trade_account_payments WHERE id = ?", (tap_id,)).fetchone())

    def test_payout_keyed_deposit_publishes_one_net_payment(self):
        """The payout-keyed path, pinned: one Payment, net of the credit, every TAP synced."""
        import_id, taps = build_deposit(self.conn, {'payout_id': 'PO-1'})
        processed, failed, skipped, errors, ext_ids = self._run()

        self.assertEqual((processed, failed, skipped), (3, 0, 0), errors)
        self.assertEqual(len(ext_ids), 1)
        payment = self.captured[0]
        self.assertAlmostEqual(payment.TotalAmt, 900.00, places=2)
        # The Payment is worth exactly what the bank received.
        self.assertAlmostEqual(payment.TotalAmt, self._bank_amount(import_id), places=2)
        self.assertEqual(self._lines(payment), {
            ('Invoice', 'INV-1'): 600.00,
            ('Invoice', 'INV-2'): 400.00,
            ('CreditMemo', 'CM-1'): 100.00,
        })
        self.assertIn('[bk:', payment.PrivateNote)
        self.assertEqual(payment._bk_locator['total'], 900.00)
        for tap_id in taps.values():
            self.assertEqual(self._tap_sync(tap_id), ('synced', ext_ids[0]))


    def test_plain_bank_deposit_publishes_one_net_payment(self):
        """One bank line, invoices at face, a credit memo funded by the same line, and no
        channel key anywhere: still one Payment, net of the credit."""
        import_id, taps = build_deposit(self.conn, {})
        processed, failed, skipped, errors, ext_ids = self._run()

        self.assertEqual((processed, failed, skipped), (3, 0, 0), errors)
        self.assertEqual(len(ext_ids), 1)
        payment = self.captured[0]
        self.assertAlmostEqual(payment.TotalAmt, 900.00, places=2)
        # The Payment is worth exactly what the bank received.
        self.assertAlmostEqual(payment.TotalAmt, self._bank_amount(import_id), places=2)
        self.assertEqual(self._lines(payment), {
            ('Invoice', 'INV-1'): 600.00,
            ('Invoice', 'INV-2'): 400.00,
            ('CreditMemo', 'CM-1'): 100.00,
        })
        for tap_id in taps.values():
            self.assertEqual(self._tap_sync(tap_id), ('synced', ext_ids[0]))

    def test_plain_deposit_invoices_leave_the_singleton_path(self):
        """The same rows must not also post as gross singletons."""
        build_deposit(self.conn, {})
        singletons = common.query_trade_account_payments(
            self.conn, 'pending', None, None, ta_type='receivable')
        self.assertEqual(singletons, [])

    def test_two_customers_on_one_bank_line_publish_separately(self):
        """A QBO Payment carries one customer. One bank line paying two customers nets the
        credit against its own customer's invoice and leaves the other customer alone."""
        import_id = insert_import(self.conn, 90000)
        inv_a = insert_ta(self.conn, 'receivable', 60000, 'INV-A', {},
                          contact='Northwind Supply')
        inv_b = insert_ta(self.conn, 'receivable', 40000, 'INV-B', {},
                          contact='Dockside Freight')
        cm_ta = insert_ta(self.conn, 'credit_memo', 10000, 'CM-1', {},
                          contact='Northwind Supply')
        tap_a = insert_tap(self.conn, inv_a, 60000, import_id=import_id)
        tap_b = insert_tap(self.conn, inv_b, 40000, import_id=import_id)
        tap_cm = insert_tap(self.conn, cm_ta, 10000, import_id=import_id)
        self.conn.commit()

        processed, failed, skipped, errors, ext_ids = self._run()
        self.assertEqual((processed, failed, skipped), (2, 0, 0), errors)
        payment = self.captured[0]
        self.assertAlmostEqual(payment.TotalAmt, 500.00, places=2)
        self.assertEqual(self._lines(payment), {
            ('Invoice', 'INV-A'): 600.00,
            ('CreditMemo', 'CM-1'): 100.00,
        })
        self.assertEqual(self._tap_sync(tap_a), ('synced', ext_ids[0]))
        self.assertEqual(self._tap_sync(tap_cm), ('synced', ext_ids[0]))
        # The other customer's invoice is still the singleton path's.
        self.assertEqual(self._tap_sync(tap_b), ('pending', None))
        singletons = {r['tap_id'] for r in common.query_trade_account_payments(
            self.conn, 'pending', None, None, ta_type='receivable')}
        self.assertEqual(singletons, {tap_b})

    def test_a_credit_memo_with_no_invoice_of_its_own_refuses(self):
        """The credit memo's customer has nothing to net against on this bank line. That
        cannot be published, so it is a named refusal and no Payment is built."""
        import_id = insert_import(self.conn, 50000)
        inv_ta = insert_ta(self.conn, 'receivable', 60000, 'INV-A', {},
                           contact='Northwind Supply')
        cm_ta = insert_ta(self.conn, 'credit_memo', 10000, 'CM-1', {},
                          contact='Dockside Freight')
        insert_tap(self.conn, inv_ta, 60000, import_id=import_id)
        cm_tap = insert_tap(self.conn, cm_ta, 10000, import_id=import_id)
        self.conn.commit()

        processed, failed, skipped, errors, ext_ids = self._run()
        self.assertEqual((processed, failed, skipped), (0, 0, 1))
        self.assertEqual(len(ext_ids), 0)
        self.assertEqual(self.captured, [])
        self.assertEqual([(e['payment_id'], e['error_code']) for e in errors],
                         [(cm_tap, 'PAYOUT_GROUP_INCOMPLETE')])
        # The row carries the refusal, so the next run sees why rather than retrying blind.
        self.assertEqual(self._tap_sync(cm_tap), ('error', None))

    def test_a_credit_larger_than_the_invoices_counts_as_failed(self):
        """The refusal table decides the counters. A deposit that brought in no cash was
        priced and could not be posted, so its rows are failures, not skips."""
        _, taps = build_deposit(self.conn, {}, invoice_faces=(5000,), credit_face=9000)
        processed, failed, skipped, errors, ext_ids = self._run()
        self.assertEqual((processed, failed, skipped), (0, 2, 0))
        self.assertEqual({e['error_code'] for e in errors}, {'PAYOUT_NEGATIVE_NET'})
        for tap_id in taps.values():
            self.assertEqual(self._tap_sync(tap_id), ('error', None))

    def test_an_empty_payout_id_does_not_join_two_bank_lines(self):
        """An empty payout id is no payout id. Two bank lines carrying a blank string are
        two deposits, not one."""
        first = insert_import(self.conn, 50000)
        second = insert_import(self.conn, 30000, date='2026-07-15')
        inv_1 = insert_ta(self.conn, 'receivable', 60000, 'INV-1', {'payout_id': ''})
        cm_ta = insert_ta(self.conn, 'credit_memo', 10000, 'CM-1', {'payout_id': ''})
        inv_2 = insert_ta(self.conn, 'receivable', 30000, 'INV-2', {'payout_id': ''})
        tap_1 = insert_tap(self.conn, inv_1, 60000, import_id=first)
        tap_cm = insert_tap(self.conn, cm_ta, 10000, import_id=first)
        tap_2 = insert_tap(self.conn, inv_2, 30000, import_id=second, date='2026-07-15')
        self.conn.commit()

        consumed = {r['tap_id'] for r in common.query_payout_consumed_credits(self.conn, 'pending')}
        self.assertEqual(consumed, {tap_1, tap_cm})
        singletons = {r['tap_id'] for r in common.query_trade_account_payments(
            self.conn, 'pending', None, None, ta_type='receivable')}
        self.assertEqual(singletons, {tap_2})

    def test_settlement_deposit_stays_with_publish_payments(self):
        """A settlement's payments carry cash already net of its credit. The consumed-credit
        selection must not take them, and the singleton path must still see them."""
        import_id = insert_import(self.conn, 100000)
        taps = []
        for n, face in ((1, 60000), (2, 40000)):
            ta_id = insert_ta(self.conn, 'receivable', face, f'INV-{n}', {})
            taps.append(insert_tap(self.conn, ta_id, face, import_id=import_id,
                                   metadata={'settlement_id': 'SET-1'}))
        self.conn.commit()

        self.assertEqual(common.query_payout_consumed_credits(self.conn, 'pending'), [])
        selected = {r['tap_id'] for r in common.query_trade_account_payments(
            self.conn, 'pending', None, None, ta_type='receivable')}
        self.assertEqual(selected, set(taps))
        self.assertEqual(common.find_bank_funded_payment_gaps(
            self.conn, 'pending', None, None), [])


@unittest.skipUnless(QBO_SDK_PRESENT, SOR_SKIP_REASON)
class DepositGroupKeyTests(unittest.TestCase):
    """The consumed-credit selection and the singleton path must partition the bank-funded
    rows on every key the group key can take."""

    def setUp(self):
        _load_modules()
        self.conn, self.path = make_temp_db()

    def tearDown(self):
        self.conn.close()
        os.remove(self.path)

    def _split(self):
        consumed = {r['tap_id'] for r in common.query_payout_consumed_credits(self.conn, 'pending')}
        singleton = set()
        for ta_type in ('receivable', 'payable'):
            singleton |= {r['tap_id'] for r in common.query_trade_account_payments(
                self.conn, 'pending', None, None, ta_type=ta_type)}
        return consumed, singleton

    def test_three_key_types_partition_the_bank_funded_rows(self):
        _, payout = build_deposit(self.conn, {'payout_id': 'PO-1'})
        _, plain = build_deposit(self.conn, {})
        settlement_import = insert_import(self.conn, 50000)
        set_ta = insert_ta(self.conn, 'receivable', 50000, 'INV-S', {})
        set_tap = insert_tap(self.conn, set_ta, 50000, import_id=settlement_import,
                             metadata={'settlement_id': 'SET-1'})
        self.conn.commit()

        consumed, singleton = self._split()
        self.assertEqual(consumed, set(payout.values()) | set(plain.values()))
        self.assertEqual(singleton, {set_tap})
        self.assertEqual(consumed & singleton, set())

        all_pending = {r[0] for r in self.conn.execute(
            "SELECT id FROM trade_account_payments WHERE json_extract(sync,'$.status')='pending'")}
        self.assertEqual(consumed | singleton, all_pending)
        self.assertEqual(common.find_bank_funded_payment_gaps(
            self.conn, 'pending', None, None), [])

    def test_a_payable_on_the_same_bank_line_keeps_its_own_path(self):
        """A bank line that pays invoices, nets a credit memo and pays a bill: the bill is a
        BillPayment, the bank nets across the two objects, and no row is left unclaimed."""
        import_id = insert_import(self.conn, 70000)
        inv_ta = insert_ta(self.conn, 'receivable', 60000, 'INV-1', {})
        cm_ta = insert_ta(self.conn, 'credit_memo', 10000, 'CM-1', {})
        bill_ta = insert_ta(self.conn, 'payable', 20000, 'BILL-1', {},
                            contact='Dockside Freight')
        inv_tap = insert_tap(self.conn, inv_ta, 60000, import_id=import_id)
        cm_tap = insert_tap(self.conn, cm_ta, 10000, import_id=import_id)
        bill_tap = insert_tap(self.conn, bill_ta, 20000, import_id=import_id)
        self.conn.commit()

        consumed, singleton = self._split()
        self.assertEqual(consumed, {inv_tap, cm_tap})
        self.assertEqual(singleton, {bill_tap})
        self.assertEqual(common.find_bank_funded_payment_gaps(
            self.conn, 'pending', None, None), [])

    def test_a_payment_with_no_import_keeps_its_own_path(self):
        """A payment with no bank line behind it is nobody's deposit. It must not be pulled
        out of the singleton path by a payout that consumes a credit."""
        import_id = insert_import(self.conn, 50000)
        inv_1 = insert_ta(self.conn, 'receivable', 60000, 'INV-1', {'payout_id': 'PO-1'})
        cm_ta = insert_ta(self.conn, 'credit_memo', 10000, 'CM-1', {'payout_id': 'PO-1'})
        inv_2 = insert_ta(self.conn, 'receivable', 40000, 'INV-2', {'payout_id': 'PO-1'})
        tap_1 = insert_tap(self.conn, inv_1, 60000, import_id=import_id)
        tap_cm = insert_tap(self.conn, cm_ta, 10000, import_id=import_id)
        tap_no_import = insert_tap(self.conn, inv_2, 40000)
        self.conn.commit()

        consumed, singleton = self._split()
        self.assertEqual(consumed, {tap_1, tap_cm})
        self.assertEqual(singleton, {tap_no_import})

    def test_one_import_spanning_two_payouts_is_unaffected(self):
        """A payout that consumes a credit consolidates; another payout on the same bank
        line still posts its own way. Both are right, so neither is a gap."""
        import_id = insert_import(self.conn, 90000)
        inv_a = insert_ta(self.conn, 'receivable', 60000, 'INV-1', {'payout_id': 'PO-1'})
        cm_a = insert_ta(self.conn, 'credit_memo', 10000, 'CM-1', {'payout_id': 'PO-1'})
        inv_b = insert_ta(self.conn, 'receivable', 40000, 'INV-2', {'payout_id': 'PO-2'})
        tap_a = insert_tap(self.conn, inv_a, 60000, import_id=import_id)
        tap_cm = insert_tap(self.conn, cm_a, 10000, import_id=import_id)
        tap_b = insert_tap(self.conn, inv_b, 40000, import_id=import_id)
        self.conn.commit()

        consumed, singleton = self._split()
        self.assertEqual(consumed, {tap_a, tap_cm})
        self.assertEqual(singleton, {tap_b})
        self.assertEqual(common.find_bank_funded_payment_gaps(
            self.conn, 'pending', None, None), [])


@unittest.skipUnless(QBO_SDK_PRESENT, SOR_SKIP_REASON)
class BankFundedGapTests(unittest.TestCase):
    """find_bank_funded_payment_gaps turns a row no phase can post whole into a stop."""

    def setUp(self):
        _load_modules()
        self.conn, self.path = make_temp_db()

    def tearDown(self):
        self.conn.close()
        os.remove(self.path)

    def _gaps(self):
        return common.find_bank_funded_payment_gaps(self.conn, 'pending', None, None)

    def _codes(self):
        return sorted(g['error_code'] for g in self._gaps())

    def test_a_deposit_every_phase_can_post_reports_no_gap(self):
        build_deposit(self.conn, {})
        self.assertEqual(self._gaps(), [])

    def test_a_parent_type_no_phase_reads_is_a_gap(self):
        """A bank-funded vendor credit matches no selection. It must stop the run rather
        than sit pending while the rest of the deposit posts."""
        import_id = insert_import(self.conn, 25000)
        vc_ta = insert_ta(self.conn, 'vendor_credit', 25000, 'VC-1', {},
                          contact='Dockside Freight')
        tap_id = insert_tap(self.conn, vc_ta, 25000, import_id=import_id)
        self.conn.commit()

        gaps = self._gaps()
        self.assertEqual([g['payment_id'] for g in gaps], [tap_id])
        self.assertEqual(gaps[0]['error_code'], 'PAYMENT_MATCHES_NO_PHASE')

    def test_a_credit_keyed_apart_from_its_invoices_is_a_gap(self):
        """Invoices carrying a payout id, a credit memo under the same bank line carrying
        none: the two would group apart and the invoices would post at full face."""
        import_id = insert_import(self.conn, 90000)
        inv_1 = insert_ta(self.conn, 'receivable', 60000, 'INV-1', {'payout_id': 'PO-1'})
        inv_2 = insert_ta(self.conn, 'receivable', 40000, 'INV-2', {'payout_id': 'PO-1'})
        cm_ta = insert_ta(self.conn, 'credit_memo', 10000, 'CM-1', {})
        tap_1 = insert_tap(self.conn, inv_1, 60000, import_id=import_id)
        tap_2 = insert_tap(self.conn, inv_2, 40000, import_id=import_id)
        tap_cm = insert_tap(self.conn, cm_ta, 10000, import_id=import_id)
        self.conn.commit()

        gaps = self._gaps()
        by_row = {g['payment_id']: g['error_code'] for g in gaps}
        self.assertEqual(by_row, {
            tap_1: 'DEPOSIT_GROUP_SPLIT',
            tap_2: 'DEPOSIT_GROUP_SPLIT',
            # The credit is left in a group of its own, which is the other half of the
            # same fault and is refused on its own terms.
            tap_cm: 'PAYOUT_GROUP_INCOMPLETE',
        })

    def test_a_bank_funded_credit_inside_a_settlement_is_a_gap(self):
        """A settlement's cash is already net of its credit, so a bank-funded credit memo
        on the same bank line cannot be netted again. Report it instead of guessing."""
        import_id = insert_import(self.conn, 90000)
        inv_ta = insert_ta(self.conn, 'receivable', 100000, 'INV-1', {})
        insert_tap(self.conn, inv_ta, 90000, import_id=import_id,
                   metadata={'settlement_id': 'SET-1'})
        cm_ta = insert_ta(self.conn, 'credit_memo', 10000, 'CM-1', {})
        cm_tap = insert_tap(self.conn, cm_ta, 10000, import_id=import_id,
                            metadata={'settlement_id': 'SET-1'})
        self.conn.commit()

        gaps = self._gaps()
        self.assertEqual([g['payment_id'] for g in gaps], [cm_tap])
        self.assertEqual(gaps[0]['error_code'], 'PAYMENT_MATCHES_NO_PHASE')

    def test_a_group_the_publisher_would_refuse_is_a_gap(self):
        """The gate asks the publisher's own question. A deposit whose credit memo has no
        invoice of its own is refused before anything posts, not after."""
        import_id = insert_import(self.conn, 50000)
        inv_ta = insert_ta(self.conn, 'receivable', 60000, 'INV-A', {},
                           contact='Northwind Supply')
        cm_ta = insert_ta(self.conn, 'credit_memo', 10000, 'CM-1', {},
                          contact='Dockside Freight')
        insert_tap(self.conn, inv_ta, 60000, import_id=import_id)
        cm_tap = insert_tap(self.conn, cm_ta, 10000, import_id=import_id)
        self.conn.commit()

        gaps = self._gaps()
        self.assertEqual([(g['payment_id'], g['error_code']) for g in gaps],
                         [(cm_tap, 'PAYOUT_GROUP_INCOMPLETE')])

    def test_a_part_published_deposit_is_a_gap(self):
        """A prior run posted some of this deposit. Consolidating the rest would emit less
        than the bank line, so the gate stops the run."""
        _, taps = build_deposit(self.conn, {})
        self.conn.execute("UPDATE trade_account_payments SET sync = ? WHERE id = ?",
                          (json.dumps({"status": "synced", "external_id": "PMT-1"}), taps['R1']))
        self.conn.commit()

        codes = {g['error_code'] for g in self._gaps()}
        self.assertEqual(codes, {'PAYOUT_PARTIALLY_PUBLISHED'})

    def test_a_credit_larger_than_the_invoices_is_a_gap(self):
        """No cash reached the bank, so there is no Payment to post."""
        build_deposit(self.conn, {}, invoice_faces=(5000,), credit_face=9000)
        codes = {g['error_code'] for g in self._gaps()}
        self.assertEqual(codes, {'PAYOUT_NEGATIVE_NET'})

    def test_two_dates_in_one_group_is_a_gap(self):
        """The Payment takes one date from the group, so two dates cannot consolidate."""
        import_id = insert_import(self.conn, 50000)
        inv_ta = insert_ta(self.conn, 'receivable', 60000, 'INV-1', {})
        cm_ta = insert_ta(self.conn, 'credit_memo', 10000, 'CM-1', {})
        insert_tap(self.conn, inv_ta, 60000, import_id=import_id)
        insert_tap(self.conn, cm_ta, 10000, import_id=import_id, date='2026-05-01')
        self.conn.commit()

        codes = {g['error_code'] for g in self._gaps()}
        self.assertEqual(codes, {'PAYOUT_GROUP_HETEROGENEOUS'})

    def test_an_unpublished_parent_invoice_is_not_a_gap(self):
        """The gate runs before the invoice phase, so an unsynced parent is the ordinary
        state at that moment. It is retryable, not a refusal."""
        import_id = insert_import(self.conn, 50000)
        inv_ta = insert_ta(self.conn, 'receivable', 60000, None, {})
        cm_ta = insert_ta(self.conn, 'credit_memo', 10000, None, {})
        insert_tap(self.conn, inv_ta, 60000, import_id=import_id)
        insert_tap(self.conn, cm_ta, 10000, import_id=import_id)
        self.conn.commit()

        self.assertEqual(self._gaps(), [])

    def test_two_deposits_on_one_bank_line_and_contact_are_not_a_split(self):
        """A payout deposit and a plain deposit can share a bank line and a customer. Each is
        posted whole in its own group, so neither is a gap."""
        import_id = insert_import(self.conn, 140000)
        pay_inv = insert_ta(self.conn, 'receivable', 60000, 'INV-1', {'payout_id': 'PO-1'})
        pay_cm = insert_ta(self.conn, 'credit_memo', 10000, 'CM-1', {'payout_id': 'PO-1'})
        plain_inv = insert_ta(self.conn, 'receivable', 100000, 'INV-2', {})
        plain_cm = insert_ta(self.conn, 'credit_memo', 10000, 'CM-2', {})
        for ta_id, amount in ((pay_inv, 60000), (pay_cm, 10000),
                              (plain_inv, 100000), (plain_cm, 10000)):
            insert_tap(self.conn, ta_id, amount, import_id=import_id)
        self.conn.commit()

        self.assertEqual(self._gaps(), [])

    def test_a_deposit_outside_the_window_is_another_run_s_business(self):
        """The consumed-credit selection carries no date window. A broken deposit in another
        period must not fail this period's run."""
        december = insert_import(self.conn, 1000, date='2025-12-15')
        inv_ta = insert_ta(self.conn, 'receivable', 5000, 'INV-D', {}, date='2025-12-01')
        cm_ta = insert_ta(self.conn, 'credit_memo', 9000, 'CM-D', {}, date='2025-12-01')
        insert_tap(self.conn, inv_ta, 5000, import_id=december, date='2025-12-15')
        insert_tap(self.conn, cm_ta, 9000, import_id=december, date='2025-12-15')
        self.conn.commit()

        # Unscoped, the credit outweighs the invoice and the deposit is refused.
        self.assertEqual({g['error_code'] for g in self._gaps()}, {'PAYOUT_NEGATIVE_NET'})
        # Scoped to April, it is out of sight.
        self.assertEqual(common.find_bank_funded_payment_gaps(
            self.conn, 'pending', '2026-04-01', '2026-04-30'), [])

    def test_a_synced_row_is_not_a_gap(self):
        """The check reads the same population the phases do: already-published rows are
        out of scope, so a finished deposit does not stop the next run."""
        _, taps = build_deposit(self.conn, {})
        for tap_id in taps.values():
            self.conn.execute("UPDATE trade_account_payments SET sync = ? WHERE id = ?",
                              (json.dumps({"status": "synced", "external_id": "PMT-1"}), tap_id))
        self.conn.commit()
        self.assertEqual(self._gaps(), [])


@unittest.skipUnless(QBO_SDK_PRESENT, SOR_SKIP_REASON)
class DryRunStopTests(unittest.TestCase):
    """The dry run is where a bookkeeper looks before publishing, so the gap has to
    fail it. publish.py is loaded with a scratch config; no credentials, no network."""

    CONFIG = """\
local_dir: "{project-root}/_local-bookkeeping"
database_dir: "%s"
database_name: "%s"
"""

    PLACEHOLDERS = {
        'QBO_CLIENT_ID': 'placeholder-id',
        'QBO_CLIENT_SECRET': 'placeholder-secret',
        'QBO_ACCESS_TOKEN': 'placeholder-access',
        'QBO_REFRESH_TOKEN': 'placeholder-refresh',
        'QBO_REALM_ID': '4620816365000000000',
        'QBO_ENVIRONMENT': 'sandbox',
    }

    def setUp(self):
        # publish.py needs the REAL _shared.client, so drop the stub the other classes
        # install and let it load against placeholder credentials.
        _drop_adapter_modules()
        self.addCleanup(_drop_adapter_modules)
        self.conn, self.path = make_temp_db()
        self.addCleanup(self.conn.close)
        self.addCleanup(os.remove, self.path)
        self.root = tempfile.mkdtemp(prefix='consumed-credit-dry-run-')
        self.addCleanup(shutil.rmtree, self.root)
        self.config = os.path.join(self.root, 'config.yaml')
        config = self.config
        with open(config, 'w') as f:
            f.write(self.CONFIG % (os.path.dirname(self.path), os.path.basename(self.path)))
        publish_py = os.path.join(SKILL_DIR, 'adapters', 'qbo', 'publish.py')
        spec = importlib.util.spec_from_file_location('publish_under_test', publish_py)
        self.publish = importlib.util.module_from_spec(spec)
        env = dict(self.PLACEHOLDERS, BOOKKEEPING_CONFIG_PATH=config)
        with mock.patch.dict(os.environ, env), mock.patch.object(sys, 'path', list(sys.path)):
            spec.loader.exec_module(self.publish)

    def _dry_run(self, publish_type='payments'):
        """Run publish.py --dry_run. Credentials are refused, so no client is built and the
        OAuth check stays out of the verdict."""
        argv = ['publish.py', '--dry_run', '--publish_type', publish_type]
        out = io.StringIO()
        with mock.patch.dict(os.environ, {'BOOKKEEPING_CONFIG_PATH': self.config}), \
                mock.patch.object(sys, 'argv', argv), \
                mock.patch.object(self.publish, 'validate_qbo_env_vars',
                                  side_effect=ValueError('no credentials in this test')), \
                redirect_stdout(out):
            with self.assertRaises(SystemExit) as exit_ctx:
                self.publish.main()
        return exit_ctx.exception.code, json.loads(out.getvalue())

    def test_a_deposit_every_phase_can_post_passes_the_dry_run(self):
        build_deposit(self.conn, {})
        self.conn.commit()
        code, result = self._dry_run()
        self.assertEqual(code, 0, result)
        self.assertTrue(result['success'])
        self.assertEqual(result['validation']['bank_funded_payment_gaps'], [])
        self.assertEqual(result['validation']['payout_consumed_credit_count'], 1)

    def test_a_row_no_phase_can_post_fails_the_dry_run(self):
        import_id = insert_import(self.conn, 25000)
        vc_ta = insert_ta(self.conn, 'vendor_credit', 25000, 'VC-1', {},
                          contact='Dockside Freight')
        tap_id = insert_tap(self.conn, vc_ta, 25000, import_id=import_id)
        self.conn.commit()

        code, result = self._dry_run()
        self.assertEqual(code, 1)
        self.assertFalse(result['success'])
        self.assertIn({'payment_id': tap_id, 'error_code': 'PAYMENT_MATCHES_NO_PHASE',
                       'error_message': mock.ANY}, result['errors'])


    def test_owner_cleared_alone_does_not_gate_on_bank_funded_rows(self):
        """The owner-cleared phase touches no bank-funded row, so it must not be stopped by
        one, in the dry run or the live run."""
        import_id = insert_import(self.conn, 25000)
        vc_ta = insert_ta(self.conn, 'vendor_credit', 25000, 'VC-1', {},
                          contact='Dockside Freight')
        insert_tap(self.conn, vc_ta, 25000, import_id=import_id)
        self.conn.commit()

        code, result = self._dry_run(publish_type='owner_cleared')
        self.assertEqual(code, 0, result)
        self.assertEqual(result['validation']['bank_funded_payment_gaps'], [])

    def _live_run(self):
        """Run publish.py with no --dry_run. The client is a stand-in and the database holds
        nothing any phase would send, so nothing reaches the network."""
        argv = ['publish.py', '--publish_type', 'all']
        out = io.StringIO()
        with mock.patch.dict(os.environ, dict(self.PLACEHOLDERS,
                                              BOOKKEEPING_CONFIG_PATH=self.config)), \
                mock.patch.object(sys, 'argv', argv), \
                mock.patch.object(self.publish, 'create_qbo_client',
                                  return_value=(object(), None)), \
                redirect_stdout(out):
            with self.assertRaises(SystemExit) as exit_ctx:
                self.publish.main()
        return exit_ctx.exception.code, json.loads(out.getvalue())

    def test_the_live_stop_keeps_the_documented_shape_and_the_other_phases(self):
        """A row no phase can post holds back the bank-funded payment phases. Journal
        entries, invoices, bills and the credit documents still run, and the result carries
        every documented key so a caller can read the error."""
        import_id = insert_import(self.conn, 25000)
        vc_ta = insert_ta(self.conn, 'vendor_credit', 25000, 'VC-1', {},
                          contact='Dockside Freight')
        tap_id = insert_tap(self.conn, vc_ta, 25000, import_id=import_id)
        self.conn.commit()

        code, result = self._live_run()
        self.assertEqual(code, 1)
        self.assertFalse(result['success'])
        for key in ('jes', 'invoices', 'bills', 'credit_memos', 'vendor_credits',
                    'credit_applications', 'payments', 'payout_consumed_credits',
                    'bill_payments', 'owner_cleared', 'errors', 'external_ids',
                    'date_range'):
            self.assertIn(key, result)
        self.assertEqual(result['payments'], {'processed': 0, 'failed': 0, 'skipped': 0})
        self.assertIn({'payment_id': tap_id, 'error_code': 'PAYMENT_MATCHES_NO_PHASE',
                       'error_message': mock.ANY}, result['errors'])


if __name__ == '__main__':
    unittest.main()
