#!/usr/bin/env python3
"""
Hermetic tests for scan_sor_direct_records.py. No real QuickBooks calls.

Covers adapters/qbo/scan_sor_direct_records.py:

  * The entity map. Every name maps to the SDK class of that name, and the types the
    publisher never creates are absent from TAGGED_BY_PUBLISHER, which is what makes each
    one surface as a direct record.
  * SalesReceipt and RefundReceipt driven through classify_record, which is the decision
    the scan makes per record. An untagged one lands in the gate, a voided one does not,
    and no local link can claim either, because no trade-account type maps to them.
  * has_bk_tag over both places the publisher stamps the tag.
  * is_voided_benign, including the paid record that reads Balance 0 and is not voided.
  * load_local_links, whose link check is type-aware because a QuickBooks id is unique only
    within its own entity type.
  * query_window, which pages to exhaustion so a truncated page cannot read as clean.

Run:
    uv run --no-project --with-requirements requirements.txt python3 -m unittest scripts.tests.test_qbo_scan_sor_direct_records
"""

import json
import os
import sqlite3
import sys
import tempfile
import types
import unittest

# The QBO SDK is adapter-tier (requirements.txt, QBO block) and reaches this module
# through the scan it loads. Without it the subject cannot be exercised, so its cases skip
# rather than error and a non-QBO deployment still runs a green core suite. The guard is a
# class decorator, not a module-level SkipTest: unittest only converts the latter to a skip
# under discover(), and raises it uncaught when a module is named directly.
SOR_SKIP_REASON = (
    "QBO SDK absent (python-quickbooks); SoR scan tests skipped. "
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

# The names this module replaces in sys.modules, and what was there before.
STUBBED = ('_shared', '_shared.client', 'config_loader', 'scan_sor_direct_records')

scan_module = None
_saved_modules = None
_saved_path = None

# The types the publisher never creates. Every one of them in the window is a direct
# record, whatever else the scan finds.
NEVER_PUBLISHED = ('Deposit', 'Purchase', 'SalesReceipt', 'RefundReceipt')


def _load_module():
    """Import scan_sor_direct_records with config_loader and _shared.client stubbed.

    The script resolves config and loads a .env at import time. Neither is available in a
    hermetic test, so both are supplied as stubs before the import. _shared.client reaches
    the qbo skill's client, which exits the process on a machine with no QuickBooks
    credentials, and nothing here calls it.
    """
    global scan_module, _saved_modules, _saved_path
    if scan_module is not None:
        return scan_module

    names = [k for k in list(sys.modules)
             if k == '_shared' or k.startswith('_shared.')
             or k in ('config_loader', 'scan_sor_direct_records')]
    _saved_modules = {k: sys.modules[k] for k in names}
    _saved_path = list(sys.path)
    for name in names:
        del sys.modules[name]

    tmpdir = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmpdir, 'adapters'), exist_ok=True)
    open(os.path.join(tmpdir, 'adapters', '.env'), 'w').close()

    loader = types.ModuleType('config_loader')
    loader.load_config = lambda: {'local_dir': tmpdir}
    loader.get_db_path = lambda: os.path.join(tmpdir, 'unused.db')
    sys.modules['config_loader'] = loader

    shared = types.ModuleType('_shared')
    shared.__path__ = []
    sys.modules['_shared'] = shared
    client_stub = types.ModuleType('_shared.client')
    client_stub.validate_qbo_env_vars = lambda: {}
    client_stub.create_qbo_client = lambda credentials: (object(), None)
    client_stub.test_qbo_connection = lambda client, path: (True, 'Connected to: TestCo')
    client_stub.refresh_client = lambda client: (client, None)
    client_stub.save_tokens_if_available = lambda *a, **k: None
    client_stub.QBORateLimiter = lambda **kwargs: _FakeLimiter()
    sys.modules['_shared.client'] = client_stub
    shared.client = client_stub

    while QBO_DIR in sys.path:
        sys.path.remove(QBO_DIR)
    sys.path.insert(0, QBO_DIR)
    import scan_sor_direct_records as module
    scan_module = module
    return module


def tearDownModule():
    """Put sys.modules and sys.path back.

    The stubs replace names other test modules import for real, so a module running after
    this one in the same process would get the fakes.
    """
    global scan_module, _saved_modules, _saved_path
    if _saved_modules is None:
        return
    for name in STUBBED:
        sys.modules.pop(name, None)
    sys.modules.update(_saved_modules)
    sys.path[:] = _saved_path
    scan_module = None
    _saved_modules = None
    _saved_path = None


class _Record:
    """A QuickBooks record as the scan reads one: attributes and nothing else."""

    def __init__(self, **fields):
        for name, value in fields.items():
            setattr(self, name, value)


def _make_db(trade_accounts=(), payments=(), journal_entries=()):
    """Temp database seeded with the rows the link check reads.

    Each trade_accounts entry is (id, type, external_id, status, journal_entry_id). A
    trade account must carry a journal entry, so one with no id named gets a plain entry
    of its own that claims nothing.
    Each payments entry is (id, trade_account_id, external_id, status).
    Each journal_entries entry is (id, external_id, status).
    """
    handle, path = tempfile.mkstemp(suffix='.db')
    os.close(handle)
    conn = sqlite3.connect(path)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())

    def sync(external_id, status):
        return json.dumps({'status': status, 'external_id': external_id})

    for je_id, external_id, status in journal_entries:
        conn.execute("INSERT INTO journal_entries (id, transaction_date, memo, sync) "
                     "VALUES (?,?,?,?)",
                     (je_id, '2026-08-14', 'seed', sync(external_id, status)))
    for ta_id, ta_type, external_id, status, je_id in trade_accounts:
        if je_id is None:
            je_id = 'JE-for-%s' % ta_id
            conn.execute("INSERT INTO journal_entries (id, transaction_date, memo) "
                         "VALUES (?,?,?)", (je_id, '2026-08-14', 'origin'))
        conn.execute("INSERT INTO trade_accounts (id, type, contact, document_date, "
                     "journal_entry_id, sync) VALUES (?,?,?,?,?,?)",
                     (ta_id, ta_type, 'Northwind Supply', '2026-08-14', je_id,
                      sync(external_id, status)))
    for tap_id, ta_id, external_id, status in payments:
        conn.execute("INSERT INTO trade_account_payments (id, trade_account_id, "
                     "payment_date, amount, sync) VALUES (?,?,?,?,?)",
                     (tap_id, ta_id, '2026-08-14', 1000, sync(external_id, status)))
    conn.commit()
    conn.close()
    return path


@unittest.skipUnless(QBO_SDK_PRESENT, SOR_SKIP_REASON)
class EntityCoverageTests(unittest.TestCase):

    def setUp(self):
        self.scan = _load_module()

    def test_every_name_maps_to_the_sdk_class_of_that_name(self):
        for name, entity_class in self.scan.ENTITY_MAP.items():
            self.assertEqual(entity_class.__name__, name)
            self.assertEqual(entity_class.qbo_object_name, name)

    def test_the_types_the_publisher_never_creates_are_scanned(self):
        for name in NEVER_PUBLISHED:
            self.assertIn(name, self.scan.ENTITY_MAP, name)

    def test_the_types_the_publisher_never_creates_are_not_in_the_tagged_set(self):
        """A type in the tagged set is only a direct record when it lacks the tag. These
        four carry no tag ever, so listing one here would read every record as a fault of
        the publisher rather than a direct entry."""
        for name in NEVER_PUBLISHED:
            self.assertNotIn(name, self.scan.TAGGED_BY_PUBLISHER, name)

    def test_the_tagged_set_is_covered_by_the_entity_map(self):
        for name in self.scan.TAGGED_BY_PUBLISHER:
            self.assertIn(name, self.scan.ENTITY_MAP, name)

    def test_no_local_trade_account_type_can_vouch_for_a_receipt(self):
        """The link check lets a local row vouch only for a record of its own type. No
        trade account maps to SalesReceipt or RefundReceipt, so one of those is never
        suppressed by a local link."""
        claimed = set(self.scan._TA_TYPE_TO_QBO.values())
        self.assertNotIn('SalesReceipt', claimed)
        self.assertNotIn('RefundReceipt', claimed)

    def test_a_receipt_carries_every_field_the_scan_reads(self):
        for name in ('SalesReceipt', 'RefundReceipt'):
            record = self.scan.ENTITY_MAP[name]()
            for field in ('TxnDate', 'PrivateNote', 'DocNumber', 'TotalAmt',
                          'CustomerRef', 'LinkedTxn'):
                self.assertTrue(hasattr(record, field), '%s.%s' % (name, field))


@unittest.skipUnless(QBO_SDK_PRESENT, SOR_SKIP_REASON)
class TagAndVoidTests(unittest.TestCase):

    def setUp(self):
        self.scan = _load_module()

    def test_the_tag_is_read_from_the_private_note(self):
        self.assertTrue(self.scan.has_bk_tag(
            _Record(PrivateNote='[bk:abc123] published by the pipeline', DocNumber='')))

    def test_the_tag_is_read_from_the_document_number(self):
        self.assertTrue(self.scan.has_bk_tag(
            _Record(PrivateNote='', DocNumber='[bk:abc123]')))

    def test_a_record_with_neither_carries_no_tag(self):
        self.assertFalse(self.scan.has_bk_tag(
            _Record(PrivateNote='Deposit from the July fair', DocNumber='1042')))

    def test_a_record_missing_both_fields_carries_no_tag(self):
        self.assertFalse(self.scan.has_bk_tag(_Record()))

    def test_a_voided_receipt_is_benign(self):
        for name in ('SalesReceipt', 'RefundReceipt'):
            record = self.scan.ENTITY_MAP[name]()
            record.PrivateNote = 'Voided'
            record.TotalAmt = 0
            record.Balance = 0
            record.LinkedTxn = None
            self.assertTrue(self.scan.is_voided_benign(record), name)

    def test_a_voided_record_with_no_balance_field_is_benign(self):
        """Balance is absent on some types. Absent means nothing outstanding."""
        self.assertTrue(self.scan.is_voided_benign(
            _Record(PrivateNote='Voided', TotalAmt=0, LinkedTxn=None)))

    def test_a_paid_record_reading_zero_balance_is_not_voided(self):
        """A paid invoice also reads Balance 0. The Voided marker is the decider."""
        self.assertFalse(self.scan.is_voided_benign(
            _Record(PrivateNote='', TotalAmt=250.00, Balance=0, LinkedTxn=None)))

    def test_a_zero_amount_record_with_no_marker_still_surfaces(self):
        self.assertFalse(self.scan.is_voided_benign(
            _Record(PrivateNote='Auto applied credit', TotalAmt=0, Balance=0,
                    LinkedTxn=None)))

    def test_a_marked_record_with_a_linked_transaction_is_not_a_clean_void(self):
        self.assertFalse(self.scan.is_voided_benign(
            _Record(PrivateNote='Voided', TotalAmt=0, Balance=0,
                    LinkedTxn=[{'TxnId': '9'}])))

    def test_a_marked_record_still_carrying_an_amount_is_not_a_void(self):
        self.assertFalse(self.scan.is_voided_benign(
            _Record(PrivateNote='Voided', TotalAmt=250.00, Balance=0, LinkedTxn=None)))


@unittest.skipUnless(QBO_SDK_PRESENT, SOR_SKIP_REASON)
class ClassifyRecordTests(unittest.TestCase):
    """The decision the scan makes for each record it pulls."""

    def setUp(self):
        self.scan = _load_module()

    def _receipt(self, name, **fields):
        record = self.scan.ENTITY_MAP[name]()
        record.Id = fields.pop('Id', '4021')
        record.TxnDate = fields.pop('TxnDate', '2026-08-14')
        record.TotalAmt = fields.pop('TotalAmt', 250.00)
        record.PrivateNote = fields.pop('PrivateNote', '')
        record.DocNumber = fields.pop('DocNumber', '')
        record.LinkedTxn = fields.pop('LinkedTxn', None)
        for key, value in fields.items():
            setattr(record, key, value)
        return record

    def test_a_direct_sales_receipt_reaches_the_gate(self):
        """The publisher never creates one, so an untagged SalesReceipt in the window is a
        direct entry and nothing suppresses it."""
        bucket, rec = self.scan.classify_record(
            self._receipt('SalesReceipt', PrivateNote='Counter sale'),
            'SalesReceipt', {})
        self.assertEqual(bucket, 'untagged')
        self.assertEqual(rec['type'], 'SalesReceipt')
        self.assertEqual(rec['id'], '4021')
        self.assertEqual(rec['txn_date'], '2026-08-14')
        self.assertEqual(rec['amount'], 250.00)
        self.assertEqual(rec['name_or_memo'], 'Counter sale')

    def test_a_direct_refund_receipt_reaches_the_gate(self):
        bucket, rec = self.scan.classify_record(
            self._receipt('RefundReceipt', Id='4022', TotalAmt=40.00),
            'RefundReceipt', {})
        self.assertEqual(bucket, 'untagged')
        self.assertEqual(rec['amount'], 40.00)

    def test_a_voided_sales_receipt_does_not_gate(self):
        bucket, rec = self.scan.classify_record(
            self._receipt('SalesReceipt', PrivateNote='Voided', TotalAmt=0, Balance=0),
            'SalesReceipt', {})
        self.assertEqual(bucket, 'voided')
        self.assertEqual(rec['void_marker'], 'Voided')

    def test_a_same_numbered_link_of_another_type_does_not_claim_a_receipt(self):
        """A QuickBooks id is unique only within its entity type. The links passed in are
        already narrowed to the type being scanned, and no trade-account type maps to a
        receipt, so that map is always empty for one."""
        links = self._links_for_invoices()
        bucket, _ = self.scan.classify_record(
            self._receipt('SalesReceipt', Id='1055'), 'SalesReceipt',
            links.get('SalesReceipt', {}))
        self.assertEqual(bucket, 'untagged')

    def _links_for_invoices(self):
        path = _make_db(trade_accounts=[('TA1', 'receivable', '1055', 'synced', None)])
        self.addCleanup(os.remove, path)
        return self.scan.load_local_links(path)

    def test_a_tagged_record_needs_no_row(self):
        bucket, rec = self.scan.classify_record(
            self._receipt('SalesReceipt', PrivateNote='[bk:abc123]'), 'SalesReceipt', {})
        self.assertEqual(bucket, 'tagged')
        self.assertIsNone(rec)

    def test_a_linked_record_is_reported_and_does_not_gate(self):
        bucket, rec = self.scan.classify_record(
            self._receipt('Invoice', Id='1055'), 'Invoice',
            {'1055': {'trade_accounts(receivable)'}})
        self.assertEqual(bucket, 'linked')
        self.assertEqual(rec['linked_via'], 'trade_accounts(receivable)')


@unittest.skipUnless(QBO_SDK_PRESENT, SOR_SKIP_REASON)
class LocalLinkTests(unittest.TestCase):

    def setUp(self):
        self.scan = _load_module()

    def _links(self, **rows):
        path = _make_db(**rows)
        self.addCleanup(os.remove, path)
        return self.scan.load_local_links(path)

    def test_a_trade_account_claims_a_record_of_its_own_type_only(self):
        """A QuickBooks id is unique only within its entity type, so a receivable may
        vouch for Invoice 1055 and never for JournalEntry 1055."""
        links = self._links(trade_accounts=[
            ('TA1', 'receivable', '1055', 'synced', None)])
        self.assertIn('1055', links['Invoice'])
        self.assertEqual(links['JournalEntry'], {})
        self.assertEqual(links['SalesReceipt'], {})

    def test_each_trade_account_type_claims_its_own_quickbooks_type(self):
        links = self._links(trade_accounts=[
            ('TA1', 'receivable', '11', 'synced', None),
            ('TA2', 'payable', '22', 'synced', None),
            ('TA3', 'credit_memo', '33', 'synced', None),
            ('TA4', 'vendor_credit', '44', 'synced', None)])
        self.assertIn('11', links['Invoice'])
        self.assertIn('22', links['Bill'])
        self.assertIn('33', links['CreditMemo'])
        self.assertIn('44', links['VendorCredit'])

    def test_a_payment_follows_the_side_its_parent_sits_on(self):
        links = self._links(
            trade_accounts=[('TA1', 'receivable', '11', 'synced', None),
                            ('TA2', 'payable', '22', 'synced', None)],
            payments=[('P1', 'TA1', '901', 'synced'),
                      ('P2', 'TA2', '902', 'synced')])
        self.assertIn('901', links['Payment'])
        self.assertIn('902', links['BillPayment'])

    def test_a_pending_row_claims_nothing(self):
        """Only a synced or ignored row is in QuickBooks. A pending row is what the
        publisher is about to create."""
        links = self._links(trade_accounts=[
            ('TA1', 'receivable', '1055', 'pending', None)])
        self.assertEqual(links['Invoice'], {})

    def test_an_ignored_row_claims_its_record(self):
        links = self._links(trade_accounts=[
            ('TA1', 'receivable', '1055', 'ignore', None)])
        self.assertIn('1055', links['Invoice'])

    def test_a_trade_account_backed_journal_entry_claims_no_journal_entry(self):
        """Such a journal entry carries its trade account's Invoice id, and its
        QuickBooks object is not a JournalEntry."""
        links = self._links(
            journal_entries=[('JE1', '1055', 'synced')],
            trade_accounts=[('TA1', 'receivable', '1055', 'synced', 'JE1')])
        self.assertEqual(links['JournalEntry'], {})
        self.assertIn('1055', links['Invoice'])

    def test_a_standalone_journal_entry_claims_its_record(self):
        links = self._links(journal_entries=[('JE1', '2077', 'synced')])
        self.assertEqual(links['JournalEntry']['2077'], {'journal_entries'})


@unittest.skipUnless(QBO_SDK_PRESENT, SOR_SKIP_REASON)
class QueryWindowTests(unittest.TestCase):

    def setUp(self):
        self.scan = _load_module()

    def _entity(self, total):
        page_size = self.scan._PAGE_SIZE
        records = [_Record(Id=str(n)) for n in range(total)]
        calls = []

        class _Entity:
            @staticmethod
            def where(clause, start_position=1, max_results=page_size, qb=None):
                calls.append((clause, start_position, max_results))
                return records[start_position - 1: start_position - 1 + max_results]

        return _Entity, calls

    def test_one_short_page_ends_the_walk(self):
        entity, calls = self._entity(3)
        got = self.scan.query_window(entity, '2026-08-01', '2026-08-31', None, _FakeLimiter())
        self.assertEqual(len(got), 3)
        self.assertEqual(len(calls), 1)

    def test_a_record_past_the_first_page_is_still_found(self):
        """A single truncated page would read as a clean gate."""
        entity, calls = self._entity(self.scan._PAGE_SIZE + 1)
        got = self.scan.query_window(entity, '2026-08-01', '2026-08-31', None, _FakeLimiter())
        self.assertEqual(len(got), self.scan._PAGE_SIZE + 1)
        self.assertEqual(len(calls), 2)

    def test_an_exactly_full_page_asks_once_more(self):
        entity, calls = self._entity(self.scan._PAGE_SIZE)
        got = self.scan.query_window(entity, '2026-08-01', '2026-08-31', None, _FakeLimiter())
        self.assertEqual(len(got), self.scan._PAGE_SIZE)
        self.assertEqual(len(calls), 2)

    def test_the_window_is_inclusive_at_both_ends(self):
        entity, calls = self._entity(0)
        self.scan.query_window(entity, '2026-08-01', '2026-08-31', None, _FakeLimiter())
        self.assertEqual(calls[0][0],
                         "TxnDate >= '2026-08-01' AND TxnDate <= '2026-08-31'")


class _FakeLimiter:
    def wait(self):
        pass

    def trigger_backoff(self, *args):
        pass


if __name__ == '__main__':
    unittest.main()
