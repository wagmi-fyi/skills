#!/usr/bin/env python3
"""
Hermetic tests for the post-then-fail locate guard (NO real QBO calls).

Covers _shared/locate.py and its wiring through publish_single_qbo_object,
journal_entries.publish_single_entry, and payments.py step-2:

  * fault_code coercion — SDK can populate error_code with '' (str) or 0;
    naive comparison would TypeError mid-run.
  * is_post_then_fail gating — 10000/6240 locate; ValidationException
    (2000–4999) and non-Quickbooks exceptions keep today's behavior.
  * locate_posted_object — FOUND on a single tag match (including on page 2,
    proving paging to exhaustion), AMBIGUOUS on multiple matches (never
    first-match), NOT_FOUND only after a delayed second read (read-after-write
    lag), INCONCLUSIVE when the query itself fails.
  * sync routing — LOCATE_AMBIGUOUS/INCONCLUSIVE → status='verify', which
    neither --sync_status pending nor error selects (structurally retry-safe).
  * publisher wiring — singleton Payments carry the [bk:] tag + _bk_locator;
    step-2 faults only succeed when a fresh read verifies the LinkedTxns.

Run:
    python3 -m unittest scripts.tests.test_qbo_publish_idempotency
"""

import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import types
import unittest
import uuid
from unittest import mock

from quickbooks.exceptions import (
    QuickbooksException, ValidationException, SevereException
)

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(THIS_DIR)
SKILL_DIR = os.path.dirname(SCRIPTS_DIR)
QBO_DIR = os.path.join(SKILL_DIR, 'adapters', 'qbo')
SCHEMA_PATH = os.path.join(SKILL_DIR, 'reference', 'schema.sql')

# Lazy publisher import with a stubbed _shared.client, mirroring
# test_qbo_publish_settlement._load_publishers (two distinct `_shared`
# packages exist in this skill; repin just-in-time).
locate_mod = common = sync_status = payments_pub = je_pub = invoices_pub = None


def _load_modules():
    global locate_mod, common, sync_status, payments_pub, je_pub, invoices_pub
    if locate_mod is not None:
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
    from _shared import locate as _locate
    from _shared import common as _common
    from _shared import sync_status as _sync_status
    from _publishers import payments as _payments
    from _publishers import journal_entries as _je
    from _publishers import invoices as _invoices
    locate_mod, common, sync_status = _locate, _common, _sync_status
    payments_pub, je_pub, invoices_pub = _payments, _je, _invoices


class _FakeRL:
    def wait(self):
        pass

    def trigger_backoff(self, *a):
        pass


class _Obj:
    """Minimal stand-in for an SDK object returned by get()/where()."""

    def __init__(self, Id, PrivateNote=None, DocNumber=None, Line=None):
        self.Id = Id
        self.PrivateNote = PrivateNote
        self.DocNumber = DocNumber
        self.Line = Line


def _fake_entity(pages=None, by_id=None, raise_on_where=False):
    """Build a fake SDK entity class: where() serves `pages` by start
    position; get() serves `by_id` or raises (QBO 404/610)."""

    class _Fake:
        @classmethod
        def get(cls, id, qb=None):
            if by_id and id in by_id:
                return by_id[id]
            raise Exception('Object Not Found')

        @classmethod
        def where(cls, clause, start_position=1, max_results=100, qb=None):
            if raise_on_where:
                raise Exception('query failed')
            idx = (int(start_position) - 1) // 100
            all_pages = pages or [[]]
            return all_pages[idx] if idx < len(all_pages) else []

    return _Fake


def make_temp_db():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    for code, name, typ, remote in [
        ('1001', 'Bank', 'asset', 'QBO-BANK'),
        ('1200', 'A/R', 'asset', None),
        ('4000', 'Sales', 'income', None),
    ]:
        conn.execute("INSERT INTO chart_of_accounts (code, name, type, remote_id) VALUES (?,?,?,?)",
                     (code, name, typ, remote))
    conn.execute("INSERT INTO contacts (name, remote_id) VALUES ('CustA', 'QBO-CUST')")
    conn.commit()
    return conn, path


def insert_synced_receivable_with_tap(conn, face=1000, ext_id='INV-1'):
    """One synced receivable TA + one pending singleton TAP (no settlement_id)."""
    je_id = str(uuid.uuid4())
    conn.execute("INSERT INTO journal_entries (id, transaction_date, memo, sync) VALUES (?,?,?,?)",
                 (je_id, '2026-04-01', 'test', '{"status":"pending"}'))
    for acct, direction in [('1200', 'debit'), ('4000', 'credit')]:
        conn.execute("INSERT INTO postings (id, journal_entry_id, account_code, direction, amount, contact) "
                     "VALUES (?,?,?,?,?,?)", (str(uuid.uuid4()), je_id, acct, direction, face, 'CustA'))
    ta_id = str(uuid.uuid4())
    conn.execute("INSERT INTO trade_accounts (id, type, contact, document_date, journal_entry_id, sync, metadata) "
                 "VALUES (?,?,?,?,?,?,?)",
                 (ta_id, 'receivable', 'CustA', '2026-04-01', je_id,
                  json.dumps({"status": "synced", "external_id": ext_id}),
                  json.dumps({"balance_account_code": "1200"})))
    tap_id = str(uuid.uuid4())
    conn.execute("INSERT INTO trade_account_payments (id, trade_account_id, payment_date, amount, sync, metadata) "
                 "VALUES (?,?,?,?,?,?)",
                 (tap_id, ta_id, '2026-04-15', face, '{"status":"pending"}', '{}'))
    conn.commit()
    return ta_id, tap_id


# --------------------------- fault classification ---------------------------

class FaultCodeTests(unittest.TestCase):

    def setUp(self):
        _load_modules()

    def test_coercion_never_raises(self):
        # SDK sets error_code='' when the Fault has no <code> — must not TypeError.
        self.assertEqual(locate_mod.fault_code(QuickbooksException('m', '')), 0)
        self.assertEqual(locate_mod.fault_code(QuickbooksException('m', 0)), 0)
        self.assertEqual(locate_mod.fault_code(QuickbooksException('m', '6240')), 6240)
        self.assertEqual(locate_mod.fault_code(QuickbooksException('m', 10001)), 10001)
        self.assertEqual(locate_mod.fault_code(Exception('no attr')), 0)

    def test_gate(self):
        self.assertTrue(locate_mod.is_post_then_fail(SevereException('m', 10000)))
        # 6140 = the live duplicate-doc code (verified against a production realm);
        # 6240 = the documented alternate.
        self.assertTrue(locate_mod.is_post_then_fail(QuickbooksException('dup', 6140)))
        self.assertTrue(locate_mod.is_post_then_fail(QuickbooksException('dup', 6240)))
        # Pre-commit validation (2000–4999): nothing posted — no locate.
        self.assertFalse(locate_mod.is_post_then_fail(ValidationException('m', 2010)))
        # Other 6xxx business errors are NOT in the documented post-then-fail set.
        self.assertFalse(locate_mod.is_post_then_fail(QuickbooksException('m', 6000)))
        self.assertFalse(locate_mod.is_post_then_fail(Exception('500 boom')))
        self.assertFalse(locate_mod.is_post_then_fail(QuickbooksException('m', '')))


# --------------------------- locate_posted_object ---------------------------

class LocateTests(unittest.TestCase):

    TAG = '[bk:abc12345]'

    def setUp(self):
        _load_modules()
        self.rl = _FakeRL()
        self.naps = []
        self.sleep = lambda s: self.naps.append(s)

    def _locate(self, fake_cls, locator=None, fault=None):
        loc = {'entity': 'Invoice', 'tag': self.TAG, 'txn_date': '2026-04-01'}
        loc.update(locator or {})
        with mock.patch.dict(locate_mod._ENTITY_MAP, {'Invoice': fake_cls}):
            return locate_mod.locate_posted_object(None, self.rl, loc, fault=fault, sleep=self.sleep)

    def test_found_via_fault_payload_id(self):
        fake = _fake_entity(by_id={'77': _Obj('77', PrivateNote=f'memo {self.TAG}')})
        res = self._locate(fake, fault=QuickbooksException('Id=77 created', 10000))
        self.assertEqual((res.state, res.qbo_id), (locate_mod.FOUND, '77'))

    def test_fault_payload_id_with_foreign_tag_is_ambiguous(self):
        # The id exists but is NOT our object — never link it.
        fake = _fake_entity(by_id={'77': _Obj('77', PrivateNote='[bk:someoneelse]')},
                            pages=[[]])
        res = self._locate(fake, fault=QuickbooksException('Id=77', 10000))
        self.assertEqual(res.state, locate_mod.AMBIGUOUS)

    def test_found_on_second_page(self):
        # Paging to exhaustion: a truncated single page would false-NOT_FOUND.
        page1 = [_Obj(str(i)) for i in range(100)]
        page2 = [_Obj('200', PrivateNote=self.TAG)]
        res = self._locate(_fake_entity(pages=[page1, page2]))
        self.assertEqual((res.state, res.qbo_id), (locate_mod.FOUND, '200'))

    def test_docnumber_collision_resolved_by_tag(self):
        # Two candidates share DocNumber+date; only the tag decides — and a
        # single tag match must NOT be reported ambiguous.
        objs = [_Obj('1', DocNumber='123'), _Obj('2', DocNumber='123', PrivateNote=self.TAG)]
        res = self._locate(_fake_entity(pages=[objs]), locator={'doc_number': '123'})
        self.assertEqual((res.state, res.qbo_id), (locate_mod.FOUND, '2'))

    def test_multiple_tag_matches_ambiguous(self):
        objs = [_Obj('1', PrivateNote=self.TAG), _Obj('2', PrivateNote=self.TAG)]
        res = self._locate(_fake_entity(pages=[objs]))
        self.assertEqual(res.state, locate_mod.AMBIGUOUS)
        self.assertIsNone(res.qbo_id)

    def test_not_found_requires_two_passes(self):
        # Read-after-write lag: one delayed re-read before concluding NOT_FOUND.
        res = self._locate(_fake_entity(pages=[[_Obj('1')]]))
        self.assertEqual(res.state, locate_mod.NOT_FOUND)
        self.assertEqual(len(self.naps), 1)

    def test_query_failure_inconclusive(self):
        res = self._locate(_fake_entity(raise_on_where=True))
        self.assertEqual(res.state, locate_mod.INCONCLUSIVE)

    def test_unusable_locator_inconclusive(self):
        res = locate_mod.locate_posted_object(None, self.rl, {'entity': 'Nope', 'tag': 'x'})
        self.assertEqual(res.state, locate_mod.INCONCLUSIVE)


# ----------------------- chokepoint wiring (common.py) -----------------------

class _FaultyObj:
    def __init__(self, exc):
        self._exc = exc
        self.Id = None

    def save(self, qb=None):
        raise self._exc


class ChokepointTests(unittest.TestCase):

    def setUp(self):
        _load_modules()
        self.rl = _FakeRL()

    def _publish(self, exc, locator='default', locate_result=None):
        obj = _FaultyObj(exc)
        if locator == 'default':
            obj._bk_locator = {'entity': 'Invoice', 'tag': '[bk:x]', 'txn_date': '2026-04-01'}
        called = []

        def fake_locate(client, rl, loc, fault=None, sleep=None):
            called.append(loc)
            return locate_result

        with mock.patch.object(common, 'locate_posted_object', fake_locate):
            ext_id, error = common.publish_single_qbo_object(None, self.rl, obj, '')
        return ext_id, error, called

    def test_severe_fault_found_links_real_id_and_warns_loudly(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            ext_id, error, called = self._publish(
                SevereException('boom', 10000),
                locate_result=locate_mod.LocateResult(locate_mod.FOUND, '77', 'matched'))
        self.assertEqual((ext_id, error), ('77', None))
        self.assertEqual(len(called), 1)
        # Recovered ≠ silent: agents drive these runs and must see the fault.
        self.assertIn('LOCATE_RECOVERED', buf.getvalue())
        self.assertIn('boom', buf.getvalue())

    def test_severe_fault_not_found_keeps_retryable_error(self):
        ext_id, error, called = self._publish(
            SevereException('boom', 10000),
            locate_result=locate_mod.LocateResult(locate_mod.NOT_FOUND, None, 'absent'))
        self.assertIsNone(ext_id)
        self.assertIn('boom', error)
        self.assertFalse(error.startswith('LOCATE_'))

    def test_severe_fault_ambiguous_is_loud_do_not_retry(self):
        ext_id, error, called = self._publish(
            SevereException('boom', 10000),
            locate_result=locate_mod.LocateResult(locate_mod.AMBIGUOUS, None, '2 tagged'))
        self.assertIsNone(ext_id)
        self.assertTrue(error.startswith('LOCATE_AMBIGUOUS'))
        self.assertIn('boom', error)  # original error preserved for diagnosis

    def test_no_locator_means_todays_behavior(self):
        ext_id, error, called = self._publish(
            SevereException('boom', 10000), locator=None,
            locate_result=locate_mod.LocateResult(locate_mod.FOUND, '77', ''))
        self.assertIsNone(ext_id)
        self.assertIn('boom', error)
        self.assertEqual(called, [])

    def test_validation_error_never_locates(self):
        ext_id, error, called = self._publish(
            ValidationException('bad ref', 2500),
            locate_result=locate_mod.LocateResult(locate_mod.FOUND, '77', ''))
        self.assertIsNone(ext_id)
        self.assertIn('bad ref', error)
        self.assertEqual(called, [])

    def test_blank_error_code_no_typeerror(self):
        # SDK raises QuickbooksException(message, '') when Fault has no code.
        ext_id, error, called = self._publish(
            QuickbooksException('weird', ''),
            locate_result=locate_mod.LocateResult(locate_mod.FOUND, '77', ''))
        self.assertIsNone(ext_id)
        self.assertIn('weird', error)
        self.assertEqual(called, [])


# --------------------------- 'verify' sync routing ---------------------------

class VerifyStatusRoutingTests(unittest.TestCase):

    def setUp(self):
        _load_modules()
        self.conn, self.path = make_temp_db()
        self.ta_id, self.tap_id = insert_synced_receivable_with_tap(self.conn)

    def tearDown(self):
        self.conn.close()
        os.remove(self.path)

    def _status(self):
        return self.conn.execute(
            "SELECT json_extract(sync,'$.status') FROM trade_account_payments WHERE id = ?",
            (self.tap_id,)).fetchone()[0]

    def test_locate_ambiguous_routes_to_verify(self):
        sync_status.update_sync_error(self.conn, 'trade_account_payments', self.tap_id,
                                      'LOCATE_AMBIGUOUS: 2 objects carry tag')
        self.assertEqual(self._status(), 'verify')

    def test_dict_error_code_routes_to_verify(self):
        sync_status.update_sync_error(self.conn, 'trade_account_payments', self.tap_id,
                                      {'error_code': 'LOCATE_INCONCLUSIVE', 'error_message': 'x'})
        self.assertEqual(self._status(), 'verify')

    def test_ordinary_error_unchanged(self):
        sync_status.update_sync_error(self.conn, 'trade_account_payments', self.tap_id,
                                      'CONTACT_REF_MISSING')
        self.assertEqual(self._status(), 'error')

    def test_verify_rows_excluded_from_publish_queries(self):
        # Structural retry-safety: neither pending nor error selects 'verify'.
        sync_status.update_sync_error(self.conn, 'trade_account_payments', self.tap_id,
                                      'LOCATE_AMBIGUOUS: x')
        for status in ('pending', 'error'):
            rows = common.query_trade_account_payments(self.conn, status, None, None,
                                                       ta_type='receivable')
            self.assertEqual(rows, [], f"verify row leaked into --sync_status {status}")


# ------------------------- publisher wiring (payments) -------------------------

class PaymentPublisherWiringTests(unittest.TestCase):

    def setUp(self):
        _load_modules()
        self.conn, self.path = make_temp_db()
        self.ta_id, self.tap_id = insert_synced_receivable_with_tap(self.conn)
        self.captured = []

        def fake_publish(client, rate_limiter, obj, env_path):
            obj.Id = '500'
            self.captured.append(obj)
            return '500', None

        self._patches = [
            mock.patch.object(payments_pub, 'publish_single_qbo_object', fake_publish),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.conn.close()
        os.remove(self.path)

    def _run(self):
        return payments_pub.publish_payments(None, _FakeRL(), self.conn, {}, 'pending', None, None, '')

    def test_singleton_carries_tag_and_locator(self):
        with mock.patch.object(payments_pub.QBOPayment, 'save', lambda s, qb=None: s):
            processed, failed, skipped, errors, ext = self._run()
        self.assertEqual((processed, failed), (1, 0), errors)
        pmt = self.captured[0]
        self.assertIn('[bk:', pmt.PrivateNote)
        self.assertEqual(pmt._bk_locator['entity'], 'Payment')
        self.assertEqual(pmt._bk_locator['txn_date'], '2026-04-15')
        self.assertEqual(pmt._bk_locator['total'], 10.0)
        self.assertIn(pmt._bk_locator['tag'], pmt.PrivateNote)

    def test_step2_fault_unverified_fails_loud(self):
        def boom(self, qb=None):
            raise Exception('6000 line update failed')

        with mock.patch.object(payments_pub.QBOPayment, 'save', boom), \
             mock.patch.object(payments_pub, 'confirm_payment_lines_applied',
                               lambda *a, **k: False):
            processed, failed, skipped, errors, ext = self._run()
        self.assertEqual((processed, failed), (0, 1))
        self.assertTrue(any(e['error_code'] == 'LINE_UPDATE_FAILED' for e in errors), errors)
        status = self.conn.execute(
            "SELECT json_extract(sync,'$.status') FROM trade_account_payments WHERE id = ?",
            (self.tap_id,)).fetchone()[0]
        self.assertEqual(status, 'error')

    def test_step2_fault_verified_by_fresh_read_succeeds(self):
        def boom(self, qb=None):
            raise Exception('6000 line update failed')

        confirmed = []

        def fake_confirm(client, rl, entity, qbo_id, expected):
            confirmed.append((entity, qbo_id, expected))
            return True

        with mock.patch.object(payments_pub.QBOPayment, 'save', boom), \
             mock.patch.object(payments_pub, 'confirm_payment_lines_applied', fake_confirm):
            processed, failed, skipped, errors, ext = self._run()
        self.assertEqual((processed, failed), (1, 0), errors)
        self.assertEqual(confirmed, [('Payment', '500', {('INV-1', 'Invoice')})])
        status, ext_id = self.conn.execute(
            "SELECT json_extract(sync,'$.status'), json_extract(sync,'$.external_id') "
            "FROM trade_account_payments WHERE id = ?", (self.tap_id,)).fetchone()
        self.assertEqual((status, ext_id), ('synced', '500'))


# --------------------------- DocNumber stamping ---------------------------

class DocNumberStampingTests(unittest.TestCase):
    """Hybrid tag stamping (decision B, 2026-06-10): when the adapter provides
    no doc number, DocNumber = the [bk:] tag — QBO's duplicate-DocNumber
    enforcement (6140, verified live) then hard-blocks double-posts and a
    blocked retry self-heals via the tag locate. Adapter-provided numbers
    always win."""

    def setUp(self):
        _load_modules()
        self.conn, self.path = make_temp_db()
        self.captured = []

        def fake_publish(client, rate_limiter, obj, env_path):
            obj.Id = '900'
            self.captured.append(obj)
            return '900', None

        self._patch = mock.patch.object(invoices_pub, 'publish_single_qbo_object', fake_publish)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self.conn.close()
        os.remove(self.path)

    def _insert_pending_receivable(self, ta_meta_extra=None):
        je_id = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO journal_entries (id, transaction_date, memo, sync) VALUES (?,?,?,?)",
            (je_id, '2026-04-01', 'sale', '{"status":"pending"}'))
        for acct, direction in [('1200', 'debit'), ('4000', 'credit')]:
            self.conn.execute(
                "INSERT INTO postings (id, journal_entry_id, account_code, direction, amount, contact) "
                "VALUES (?,?,?,?,?,?)", (str(uuid.uuid4()), je_id, acct, direction, 1000, 'CustA'))
        ta_id = str(uuid.uuid4())
        meta = {"balance_account_code": "1200"}
        meta.update(ta_meta_extra or {})
        self.conn.execute(
            "INSERT INTO trade_accounts (id, type, contact, document_date, journal_entry_id, sync, metadata) "
            "VALUES (?,?,?,?,?,'{\"status\":\"pending\"}',?)",
            (ta_id, 'receivable', 'CustA', '2026-04-01', je_id, json.dumps(meta)))
        self.conn.commit()
        return ta_id

    def _run(self):
        return invoices_pub.publish_invoices(
            None, _FakeRL(), self.conn, {'qbo_default_invoice_item': '6'},
            'pending', None, None, '')

    def test_tag_fills_missing_doc_number(self):
        ta_id = self._insert_pending_receivable()
        processed, failed, skipped, errors, ext = self._run()
        self.assertEqual((processed, failed), (1, 0), errors)
        inv = self.captured[0]
        tag = locate_mod.make_tag(ta_id[:8])
        self.assertEqual(inv.DocNumber, tag)
        self.assertEqual(inv._bk_locator['doc_number'], tag)
        self.assertIn(tag, inv.PrivateNote)

    def test_adapter_doc_number_wins(self):
        self._insert_pending_receivable({'doc_number': 'AMZ-123'})
        processed, failed, skipped, errors, ext = self._run()
        self.assertEqual((processed, failed), (1, 0), errors)
        inv = self.captured[0]
        self.assertEqual(inv.DocNumber, 'AMZ-123')
        self.assertEqual(inv._bk_locator['doc_number'], 'AMZ-123')
        self.assertIn('[bk:', inv.PrivateNote)  # tag still present for locate


# ------------------------ journal entry tag + fault path ------------------------

class JournalEntryIdempotencyTests(unittest.TestCase):

    def setUp(self):
        _load_modules()

    def _postings(self, memo='COGS accrual'):
        base = {'transaction_date': '2026-04-30', 'memo': memo, 'account_meta': None,
                'contact': None, 'contact_meta': None, 'qbo_contact_id': None,
                'description': None, 'class_name': None, 'class_remote_id': None}
        return [dict(base, amount=100, direction='debit', account_code='5000', qbo_account_id='9'),
                dict(base, amount=100, direction='credit', account_code='1300', qbo_account_id='10')]

    def test_transform_stamps_tag_after_memo(self):
        je_id = 'abcdefab-0000-0000-0000-000000000000'
        entry, err = je_pub.transform_to_qbo_journal_entry(je_id, self._postings())
        self.assertIsNone(err)
        self.assertEqual(entry['PrivateNote'], 'COGS accrual [bk:abcdefab]')

    def test_transform_stamps_tag_without_memo(self):
        je_id = 'abcdefab-0000-0000-0000-000000000000'
        entry, err = je_pub.transform_to_qbo_journal_entry(je_id, self._postings(memo=None))
        self.assertIsNone(err)
        self.assertEqual(entry['PrivateNote'], '[bk:abcdefab]')

    def test_fault_path_locates_and_succeeds(self):
        class _FakeJE:
            def __init__(self):
                self.Line = []
                self.Id = None

            def save(self, qb=None):
                raise SevereException('boom', 10000)

        fake_result = locate_mod.LocateResult(locate_mod.FOUND, '88', 'matched')
        with mock.patch.object(je_pub, 'JournalEntry', _FakeJE), \
             mock.patch.object(je_pub, 'locate_posted_object', lambda *a, **k: fake_result):
            result = je_pub.publish_single_entry(
                None, _FakeRL(), 'abcdefab-0000', {'TxnDate': '2026-04-30', 'Line': []}, '')
        self.assertTrue(result['success'])
        self.assertEqual(result['external_id'], '88')

    def test_fault_path_ambiguous_is_loud(self):
        class _FakeJE:
            def __init__(self):
                self.Line = []
                self.Id = None

            def save(self, qb=None):
                raise SevereException('boom', 10000)

        fake_result = locate_mod.LocateResult(locate_mod.AMBIGUOUS, None, '2 tagged')
        with mock.patch.object(je_pub, 'JournalEntry', _FakeJE), \
             mock.patch.object(je_pub, 'locate_posted_object', lambda *a, **k: fake_result):
            result = je_pub.publish_single_entry(
                None, _FakeRL(), 'abcdefab-0000', {'TxnDate': '2026-04-30', 'Line': []}, '')
        self.assertFalse(result['success'])
        self.assertEqual(result['error_code'], 'LOCATE_AMBIGUOUS')


if __name__ == '__main__':
    unittest.main()
