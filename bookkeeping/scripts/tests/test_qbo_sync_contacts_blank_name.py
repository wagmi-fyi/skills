#!/usr/bin/env python3
"""
Hermetic tests for the blank-name refusal in sync_contacts.py. No real QuickBooks calls.

Contacts are auto-created from whatever name a posting carries, so a source row with an
empty payee leaves a contact whose name is the empty string. QuickBooks has no name for
such a party, and the dual-use split would repoint A/P postings and payable trade accounts
onto " (Vendor)", a ledger rewrite no external system ever sees.

Covers adapters/qbo/sync_contacts.py:

  * An ordinary sweep, unchanged. Creates, local writes, counters and the output keys are
    what they were before the refusal existed, asserted against an explicit key list.
  * A blank or whitespace-only name is refused with a reason, reaches QuickBooks never,
    and does not stop the contacts beside it.
  * The split refuses a blank source before any UPDATE runs, and the refusal lives in
    create_vendor_split so a later caller inherits it.
  * The split still works for an ordinary dual-use contact.
  * A refusal fails the run, the way an error does.

This module needs no SDK guard: it binds fake entity classes over the module's
Customer and Vendor and stubs fetch_all_pages, so no SDK type is exercised and the cases
run on a deployment without the QBO block.

Run:
    python3 -m unittest scripts.tests.test_qbo_sync_contacts_blank_name
"""

import json
import os
import sqlite3
import sys
import tempfile
import types
import unittest

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(THIS_DIR)
SKILL_DIR = os.path.dirname(SCRIPTS_DIR)
QBO_DIR = os.path.join(SKILL_DIR, 'adapters', 'qbo')
SCHEMA_PATH = os.path.join(SKILL_DIR, 'reference', 'schema.sql')

# The output contract as it stood before the refusal was added. A key here that stops
# appearing is a break for a caller that reads it.
PRE_CHANGE_RESULT_KEYS = [
    'classified', 'customers_created', 'customers_existing', 'vendors_created',
    'vendors_existing', 'dual_use_splits', 'skipped', 'errors', 'details',
]

# The names this module replaces in sys.modules, and what was there before.
STUBBED = ('_shared', '_shared.client', 'config_loader', 'sync_contacts')

sync_contacts_module = None
_saved_modules = None
_saved_path = None


def _load_module():
    """Import sync_contacts with config_loader and _shared.client stubbed.

    The script resolves config and loads a .env at import time. Neither is available in a
    hermetic test, so both are supplied as stubs before the import.
    """
    global sync_contacts_module, _saved_modules, _saved_path
    if sync_contacts_module is not None:
        return sync_contacts_module

    names = [k for k in list(sys.modules)
             if k == '_shared' or k.startswith('_shared.')
             or k in ('config_loader', 'sync_contacts')]
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
    client_stub.save_tokens_if_available = lambda *a, **k: None
    client_stub.MAX_RETRIES = 3
    client_stub.QBORateLimiter = lambda **kwargs: _FakeLimiter()
    # Mirrors the real helper's contract: active records only.
    client_stub.fetch_all_pages = lambda cls, qb, **kwargs: list(cls._active)
    sys.modules['_shared.client'] = client_stub
    shared.client = client_stub

    while QBO_DIR in sys.path:
        sys.path.remove(QBO_DIR)
    sys.path.insert(0, QBO_DIR)
    import sync_contacts as module
    sync_contacts_module = module
    return module


def tearDownModule():
    """Put sys.modules and sys.path back.

    The stubs replace names other test modules import for real, so a module running after
    this one in the same process would get the fakes.
    """
    global sync_contacts_module, _saved_modules, _saved_path
    if _saved_modules is None:
        return
    for name in STUBBED:
        sys.modules.pop(name, None)
    sys.modules.update(_saved_modules)
    sys.path[:] = _saved_path
    sync_contacts_module = None
    _saved_modules = None
    _saved_path = None


class _FakeLimiter:
    def wait(self):
        pass

    def trigger_backoff(self, *args):
        pass


class _QBORecord:
    def __init__(self, record_id, display_name):
        self.Id = record_id
        self.DisplayName = display_name


def fake_entity(entity_name, existing=(), next_ids=None):
    """A fake SDK entity class. Serves the records QuickBooks already holds and logs
    every create the sync asks for."""

    class _Fake:
        _active = [_QBORecord(i, n) for i, n in existing]
        _created = []
        _ids = list(next_ids or ['NEW-1', 'NEW-2', 'NEW-3'])

        def __init__(self):
            self.DisplayName = None
            self.CompanyName = None
            self.Id = None

        def save(self, qb=None):
            self.Id = _Fake._ids.pop(0)
            _Fake._created.append(self.DisplayName)

    _Fake.__name__ = entity_name
    return _Fake


def make_db(contacts, postings=(), trade_accounts=()):
    """Temp database seeded with a chart, contacts, postings and trade accounts."""
    handle, path = tempfile.mkstemp(suffix='.db')
    os.close(handle)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    for code, name, kind, qbo_type in [
        ('1200', 'A/R', 'asset', 'Accounts Receivable'),
        ('2000', 'A/P', 'liability', 'Accounts Payable'),
        ('5000', 'Expense', 'expense', 'Expense'),
        ('4000', 'Sales', 'income', 'Income'),
    ]:
        conn.execute("INSERT INTO chart_of_accounts (code, name, type, meta) "
                     "VALUES (?,?,?,?)",
                     (code, name, kind, json.dumps({'qbo_type': qbo_type})))
    for name in contacts:
        conn.execute("INSERT INTO contacts (name, remote_id, meta) VALUES (?, NULL, '{}')",
                     (name,))
    conn.execute("INSERT INTO journal_entries (id, transaction_date, memo) "
                 "VALUES ('JE1', '2026-08-14', 'seed')")
    for n, (contact, code) in enumerate(postings):
        conn.execute("INSERT INTO postings (id, journal_entry_id, account_code, "
                     "direction, amount, contact) VALUES (?,?,?,?,?,?)",
                     ('P%d' % n, 'JE1', code, 'debit', 10000, contact))
    for n, (contact, ta_type) in enumerate(trade_accounts):
        conn.execute("INSERT INTO trade_accounts (id, type, contact, document_date, "
                     "journal_entry_id) VALUES (?,?,?,?,?)",
                     ('TA%d' % n, ta_type, contact, '2026-08-14', 'JE1'))
    conn.commit()
    return conn, path


class BlankNameTests(unittest.TestCase):

    def setUp(self):
        self.sync = _load_module()
        self.limiter = _FakeLimiter()

    def _db(self, *args, **kwargs):
        conn, path = make_db(*args, **kwargs)
        self.addCleanup(os.remove, path)
        self.addCleanup(conn.close)
        return conn

    def _run(self, conn, dry_run=False, customer=None, vendor=None):
        self.sync.Customer = customer or fake_entity('Customer')
        self.sync.Vendor = vendor or fake_entity('Vendor')
        return self.sync.sync_contacts(object(), self.limiter, conn, dry_run)

    def _contacts(self, conn):
        return {r[0]: (r[1], r[2]) for r in
                conn.execute("SELECT name, remote_id, meta FROM contacts")}

    # ---------------- the predicate ----------------

    def test_is_blank_name_reads_every_empty_form(self):
        for value in (None, '', ' ', '\t', '\n  '):
            self.assertTrue(self.sync.is_blank_name(value), repr(value))
        for value in ('Northwind Supply', ' x ', '0'):
            self.assertFalse(self.sync.is_blank_name(value), repr(value))

    # ---------------- an ordinary sweep, unchanged ----------------

    def test_a_sweep_creates_every_named_contact_as_before(self):
        conn = self._db(['Northwind Supply', 'Harbor Lane Studio'],
                        postings=[('Northwind Supply', '5000'),
                                  ('Harbor Lane Studio', '5000')])
        vendor = fake_entity('Vendor', next_ids=['V1', 'V2'])
        result = self._run(conn, vendor=vendor)

        self.assertEqual(result['vendors_created'], 2)
        self.assertEqual(sorted(vendor._created),
                         ['Harbor Lane Studio', 'Northwind Supply'])
        self.assertEqual(result['classified'], 2)
        self.assertEqual(result['refused'], [])
        rows = self._contacts(conn)
        self.assertEqual(rows['Northwind Supply'][0], 'V1')
        self.assertEqual(json.loads(rows['Northwind Supply'][1]), {'type': 'vendor'})

    def test_the_result_still_carries_every_pre_change_key(self):
        conn = self._db(['Northwind Supply'], postings=[('Northwind Supply', '5000')])
        result = self._run(conn)
        for key in PRE_CHANGE_RESULT_KEYS:
            self.assertIn(key, result, 'pre-change key %r dropped' % key)
        self.assertIn('refused', result)

    def test_a_contact_quickbooks_already_holds_is_linked_not_created(self):
        conn = self._db(['Northwind Supply'], postings=[('Northwind Supply', '5000')])
        vendor = fake_entity('Vendor', existing=[('7', 'Northwind Supply')])
        result = self._run(conn, vendor=vendor)

        self.assertEqual(vendor._created, [])
        self.assertEqual(result['vendors_existing'], 1)
        self.assertEqual(self._contacts(conn)['Northwind Supply'][0], '7')

    # ---------------- the refusal ----------------

    def test_a_blank_name_is_refused_and_its_neighbour_still_syncs(self):
        conn = self._db(['', 'Northwind Supply'],
                        postings=[('', '5000'), ('Northwind Supply', '5000')])
        vendor = fake_entity('Vendor', next_ids=['V1'])
        result = self._run(conn, vendor=vendor)

        self.assertEqual(vendor._created, ['Northwind Supply'])
        self.assertEqual([r['reason'] for r in result['refused']], ['blank_name'])
        self.assertIsNone(self._contacts(conn)[''][0])
        self.assertEqual(self._contacts(conn)['Northwind Supply'][0], 'V1')

    def test_a_whitespace_only_name_is_refused(self):
        conn = self._db(['   '], postings=[('   ', '5000')])
        vendor = fake_entity('Vendor')
        result = self._run(conn, vendor=vendor)

        self.assertEqual(vendor._created, [])
        self.assertEqual([r['reason'] for r in result['refused']], ['blank_name'])

    def test_a_blank_name_is_counted_as_classified(self):
        """contacts_analyzed reports what the sync looked at, refusals included."""
        conn = self._db(['', 'Northwind Supply'],
                        postings=[('', '5000'), ('Northwind Supply', '5000')])
        self.assertEqual(self._run(conn)['classified'], 2)

    def test_a_blank_name_is_refused_once(self):
        """The classification is read twice, before and after the splits. The second read
        must not record the same refusal again."""
        conn = self._db([''], postings=[('', '5000')])
        self.assertEqual(len(self._run(conn)['refused']), 1)

    # ---------------- the split ----------------

    def test_the_split_refuses_a_blank_source_and_repoints_nothing(self):
        """The split reassigns existing ledger rows, so a blank source has to be refused
        before any UPDATE runs."""
        conn = self._db([''],
                        postings=[('', '1200'), ('', '2000')],
                        trade_accounts=[('', 'payable')])
        vendor = fake_entity('Vendor')
        result = self._run(conn, vendor=vendor)

        moved = conn.execute(
            "SELECT COUNT(*) FROM postings WHERE contact != ''").fetchone()[0]
        self.assertEqual(moved, 0, 'a posting was repointed off the blank name')
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM trade_accounts WHERE contact != ''"
        ).fetchone()[0], 0, 'a trade account was repointed')

        self.assertNotIn(' (Vendor)', self._contacts(conn))
        self.assertEqual(vendor._created, [])
        self.assertEqual(result['dual_use_splits'], [])
        self.assertEqual([r['reason'] for r in result['refused']], ['blank_name'])

    def test_create_vendor_split_refuses_a_blank_source_when_called_directly(self):
        """The refusal lives in the function, so a later caller inherits it."""
        conn = self._db([''], postings=[('', '2000')])
        out = self.sync.create_vendor_split(conn, '', dry_run=False)

        self.assertTrue(out['refused'])
        self.assertEqual(out['reason'], 'blank_name_source')
        self.assertEqual(out['ap_postings_repointed'], 0)
        self.assertEqual(out['payable_tas_repointed'], 0)
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM postings WHERE contact != ''").fetchone()[0], 0)

    def test_the_split_still_works_for_an_ordinary_dual_use_contact(self):
        conn = self._db(['Northwind Supply'],
                        postings=[('Northwind Supply', '1200'),
                                  ('Northwind Supply', '2000')],
                        trade_accounts=[('Northwind Supply', 'payable')])
        result = self._run(conn)

        self.assertEqual(len(result['dual_use_splits']), 1)
        split = result['dual_use_splits'][0]
        self.assertEqual(split['vendor_name'], 'Northwind Supply (Vendor)')
        self.assertEqual(split['ap_postings_repointed'], 1)
        self.assertEqual(split['payable_tas_repointed'], 1)
        self.assertIn('Northwind Supply (Vendor)', self._contacts(conn))
        self.assertEqual(conn.execute(
            "SELECT contact FROM postings WHERE account_code = '2000'"
        ).fetchone()[0], 'Northwind Supply (Vendor)')
        self.assertEqual(result['refused'], [])

    # ---------------- dry run ----------------

    def test_a_dry_run_creates_nothing_and_writes_nothing(self):
        conn = self._db(['Northwind Supply'], postings=[('Northwind Supply', '5000')])
        vendor = fake_entity('Vendor')
        result = self._run(conn, dry_run=True, vendor=vendor)

        self.assertEqual(vendor._created, [])
        self.assertEqual(result['vendors_created'], 1)
        self.assertEqual(result['details'][0]['action'], 'would_create')
        self.assertIsNone(self._contacts(conn)['Northwind Supply'][0])

    def test_a_dry_run_still_refuses_a_blank_name(self):
        conn = self._db([''], postings=[('', '5000')])
        result = self._run(conn, dry_run=True)
        self.assertEqual([r['reason'] for r in result['refused']], ['blank_name'])


if __name__ == '__main__':
    unittest.main()
