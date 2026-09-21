#!/usr/bin/env python3
"""
Hermetic tests for scan_unclassed_pl.py. No real QuickBooks calls.

The reports here carry the shape QuickBooks returns: a column's identity in its MetaData
under ColKey, and the detail report's accounts as the sections its transactions sit in.
Every account, amount, name and date is made up.

Covers adapters/qbo/scan_unclassed_pl.py:

  * The class-tracking preference. Off means there is nothing to check, and no report is
    read.
  * Finding the no-class column by the key QuickBooks gives it, and by its title where a
    report carries no column metadata.
  * The leaf-account test. A report row with no account id is a total or a label, and
    counting one adds the same money to the column twice.
  * The account a detail transaction posts to, which is the section it sits under.
  * Money on either side fails the gate, and a disagreement between the two reports is
    reported in the summary line.
  * A cell that is not a number raises, so a row cannot drop out of a finding in silence.
  * A classed transaction is left out of the record list.
  * A detail report with no class column raises, so a missing column cannot read as no
    findings.
  * The exit code follows the gate, and an inverted window is refused.

The script reaches QuickBooks through client.get_report and the SDK's Preferences. A
stand-in for each exercises every path, and both are stubbed, so these cases run on a
deployment without the QBO block.

Run:
    uv run --no-project --with-requirements requirements.txt python3 -m unittest scripts.tests.test_qbo_scan_unclassed_pl
"""

import json
import os
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(THIS_DIR)
SKILL_DIR = os.path.dirname(SCRIPTS_DIR)
QBO_DIR = os.path.join(SKILL_DIR, 'adapters', 'qbo')

# The names this module replaces in sys.modules, and what was there before.
STUBBED = ('_shared', '_shared.client', 'config_loader', 'scan_unclassed_pl',
           'quickbooks', 'quickbooks.objects')

scan_module = None
_saved_modules = None
_saved_path = None


def preferences(per_txn=False, per_line=True, info=True):
    """A stand-in for the SDK's Preferences, answering one company's class setting.

    info=False is the company whose Preferences come back with no AccountingInfoPrefs at
    all, which is a shape the scan cannot read an answer out of.
    """

    class Fake:
        asked = []

        @classmethod
        def get(cls, qb=None):
            cls.asked.append(qb)
            if not info:
                return types.SimpleNamespace(AccountingInfoPrefs=None)
            return types.SimpleNamespace(
                AccountingInfoPrefs=types.SimpleNamespace(
                    ClassTrackingPerTxn=per_txn,
                    ClassTrackingPerTxnLine=per_line))

    return Fake


def _load_module():
    """Import scan_unclassed_pl with config_loader, _shared.client and the SDK stubbed.

    The script resolves config and loads a .env at import time, and it imports one SDK
    class. None of that is available in a hermetic test, so all three are supplied as
    stubs before the import. They are names other test modules import for real, so what
    was there is kept and tearDownModule puts it back.
    """
    global scan_module, _saved_modules, _saved_path
    if scan_module is not None:
        return scan_module

    names = [k for k in list(sys.modules)
             if k == '_shared' or k.startswith('_shared.')
             or k == 'quickbooks' or k.startswith('quickbooks.')
             or k in ('config_loader', 'scan_unclassed_pl')]
    _saved_modules = {k: sys.modules[k] for k in names}
    _saved_path = list(sys.path)
    for name in names:
        del sys.modules[name]

    tmpdir = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmpdir, 'adapters'), exist_ok=True)
    open(os.path.join(tmpdir, 'adapters', '.env'), 'w').close()

    loader = types.ModuleType('config_loader')
    loader.load_config = lambda: {'local_dir': tmpdir}
    sys.modules['config_loader'] = loader

    shared = types.ModuleType('_shared')
    shared.__path__ = []
    sys.modules['_shared'] = shared
    client_stub = types.ModuleType('_shared.client')
    client_stub.validate_qbo_env_vars = lambda: {}
    client_stub.create_qbo_client = lambda credentials: (_FakeClient(), None)
    client_stub.test_qbo_connection = lambda client, path: (True, 'Connected to: TestCo')
    client_stub.refresh_client = lambda client: (client, None)
    client_stub.save_tokens_if_available = lambda *a, **k: None
    sys.modules['_shared.client'] = client_stub
    shared.client = client_stub

    sdk = types.ModuleType('quickbooks')
    sdk.__path__ = []
    sdk_objects = types.ModuleType('quickbooks.objects')
    sdk_objects.Preferences = preferences()
    sdk.objects = sdk_objects
    sys.modules['quickbooks'] = sdk
    sys.modules['quickbooks.objects'] = sdk_objects

    while QBO_DIR in sys.path:
        sys.path.remove(QBO_DIR)
    sys.path.insert(0, QBO_DIR)
    import scan_unclassed_pl as module
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


class _FakeClient:
    """A client with the one method the script calls. Records what it was asked for."""

    def __init__(self, reports=None):
        self.reports = reports or {}
        self.asked = []

    def get_report(self, report_type, qs=None):
        self.asked.append((report_type, dict(qs or {})))
        if report_type not in self.reports:
            raise AssertionError('the script asked for an unexpected report: '
                                 + report_type)
        answer = self.reports[report_type]
        if isinstance(answer, Exception):
            raise answer
        return answer


# ---------------- report fixtures ----------------

def column(title, col_type, key):
    """One report column, as QuickBooks shapes one.

    ColType is a data type and ColTitle is a display label. The name a report request
    asks for is the MetaData entry named ColKey, and nowhere else.
    """
    return {'ColTitle': title, 'ColType': col_type,
            'MetaData': [{'Name': 'ColKey', 'Value': key}]}


def summary_report(class_columns, rows):
    """Profit and Loss summarized by Classes.

    class_columns is one (title, key) pair per class column. Column 0 holds the account
    name under a blank title, and the last column is the row total. QuickBooks keys the
    no-class column `not_specified`. The key a real class column carries is made up here,
    and nothing in the script reads it.
    """
    columns = ([column('', 'String', 'account')]
               + [column(title, 'Money', key) for title, key in class_columns]
               + [column('Total', 'Money', 'total')])
    return {'Columns': {'Column': columns}, 'Rows': {'Row': rows}}


def account_row(account_id, *values):
    """A leaf account row. QuickBooks puts the account's id on the first cell."""
    row = {'type': 'Data', 'ColData': [{'value': v} for v in values]}
    row['ColData'][0]['id'] = account_id
    return row


def total_row(*values):
    """A report row with no account id. QuickBooks shapes a total or a label this way."""
    return {'type': 'Data', 'ColData': [{'value': v} for v in values]}


def section(title, rows, header_values=None, total_values=None, section_id=None):
    """A report section, shaped as QuickBooks shapes one.

    The section's label and total live under Header and Summary, which are keys separate
    from a data row's own ColData. header_values fills the Header's amount cells, which is
    the shape a parent account with its own postings would take. section_id is what the
    detail report puts on the header of a section that is an account.
    """
    header = [{'value': title}] + [{'value': v} for v in (header_values or [])]
    if section_id is not None:
        header[0]['id'] = section_id
    summary = [{'value': 'Total ' + title}] + [{'value': v} for v in (total_values or [])]
    return {'type': 'Section',
            'Header': {'ColData': header},
            'Rows': {'Row': rows},
            'Summary': {'ColData': summary}}


DETAIL_COLUMNS = [
    ('Date', 'Date', 'tx_date'),
    ('Transaction Type', 'String', 'txn_type'),
    ('Num', 'String', 'doc_num'),
    ('Name', 'String', 'name'),
    ('Memo/Description', 'String', 'memo'),
    ('Class', 'String', 'klass_name'),
    ('Amount', 'Money', 'subt_nat_amount'),
]


def detail_report(rows, columns=None):
    return {'Columns': {'Column': [column(t, c, k)
                                   for t, c, k in (columns or DETAIL_COLUMNS)]},
            'Rows': {'Row': rows}}


def detail_row(txn_id, date, txn_type, doc_num, name, memo, klass, amount):
    row = {'type': 'Data',
           'ColData': [{'value': v} for v in
                       (date, txn_type, doc_num, name, memo, klass, amount)]}
    row['ColData'][0]['id'] = txn_id
    return row


def under_account(account_id, account, *rows):
    """The detail report's transactions for one account, inside the section that names it."""
    return section(account, list(rows), section_id=account_id)


def under_group(name, *sections):
    """A classification group: Income, Expenses. Its header carries no id."""
    return section(name, list(sections))


class ClassTrackingTests(unittest.TestCase):

    def setUp(self):
        self.scan = _load_module()

    def _result(self, per_txn=False, per_line=False):
        self.scan.Preferences = preferences(per_txn=per_txn, per_line=per_line)
        client = _FakeClient()
        return client, self.scan.scan(client, '2026-08-01', '2026-08-31')

    def test_tracking_off_reads_no_report_and_says_there_is_nothing_to_check(self):
        client, result = self._result()
        self.assertTrue(result['success'])
        self.assertFalse(result['class_tracking'])
        self.assertEqual(client.asked, [])
        self.assertIn('NOT APPLICABLE', result['summary'])
        self.assertIn('nothing to check', result['summary'])

    def test_tracking_off_still_answers_with_the_full_shape(self):
        """Check 12 reads one output contract whatever the company does."""
        _, result = self._result()
        self.assertEqual(result['unclassed_by_account'], [])
        self.assertEqual(result['unclassed_records'], [])
        self.assertEqual(result['unclassed_account_total'], 0)
        self.assertEqual(result['unclassed_record_count'], 0)
        self.assertEqual(result['period'], ['2026-08-01', '2026-08-31'])

    def test_either_switch_on_is_a_company_that_tracks_classes(self):
        for per_txn, per_line in ((True, False), (False, True), (True, True)):
            self.scan.Preferences = preferences(per_txn=per_txn, per_line=per_line)
            client = _FakeClient({'ProfitAndLoss': summary_report([], []),
                                  'ProfitAndLossDetail': detail_report([])})
            result = self.scan.scan(client, '2026-08-01', '2026-08-31')
            self.assertTrue(result['class_tracking'], (per_txn, per_line))
            self.assertEqual(len(client.asked), 2)

    def test_preferences_with_no_accounting_block_raises(self):
        """Guessing either way decides whether the whole statement reads as a finding."""
        self.scan.Preferences = preferences(info=False)
        with self.assertRaises(RuntimeError) as raised:
            self.scan.scan(_FakeClient(), '2026-08-01', '2026-08-31')
        self.assertIn('AccountingInfoPrefs', str(raised.exception))


class FindUnclassedColumnTests(unittest.TestCase):

    def setUp(self):
        self.scan = _load_module()

    def test_the_column_is_found_by_its_key_whatever_its_title(self):
        """A locale or a report version renders that column with whatever words it likes.
        The key does not move."""
        report = summary_report([('Delivery', '5000000001'),
                                 ('Nicht angegeben', 'not_specified')], [])
        index, label = self.scan.find_unclassed_column(report)
        self.assertEqual(index, 2)
        self.assertEqual(label, 'Nicht angegeben')

    def test_a_report_with_no_column_metadata_falls_back_to_the_title(self):
        report = {'Columns': {'Column': [{'ColTitle': ''}, {'ColTitle': 'Delivery'},
                                         {'ColTitle': 'Not Specified'},
                                         {'ColTitle': 'Total'}]},
                  'Rows': {'Row': []}}
        index, label = self.scan.find_unclassed_column(report)
        self.assertEqual(index, 2)
        self.assertEqual(label, 'Not Specified')

    def test_every_label_the_fallback_knows(self):
        for title in ('Not Specified', 'Unclassified', 'No Class', '  NO CLASS  '):
            report = {'Columns': {'Column': [{'ColTitle': ''}, {'ColTitle': 'Delivery'},
                                             {'ColTitle': title}, {'ColTitle': 'Total'}]},
                      'Rows': {'Row': []}}
            index, label = self.scan.find_unclassed_column(report)
            self.assertEqual(index, 2, title)
            self.assertEqual(label, title)

    def test_the_account_column_is_never_the_answer(self):
        """Column 0 holds the account name and its title is blank, which the fallback's
        label set also holds. Reading it as the no-class column would fail every report."""
        report = {'Columns': {'Column': [{'ColTitle': ''}, {'ColTitle': 'Delivery'},
                                         {'ColTitle': 'Total'}]},
                  'Rows': {'Row': []}}
        self.assertEqual(self.scan.find_unclassed_column(report), (None, None))

    def test_the_total_column_is_never_the_answer(self):
        report = {'Columns': {'Column': [{'ColTitle': ''}, {'ColTitle': 'Delivery'},
                                         {'ColTitle': 'total'}]},
                  'Rows': {'Row': []}}
        self.assertEqual(self.scan.find_unclassed_column(report), (None, None))

    def test_a_report_with_every_class_named_has_no_such_column(self):
        report = summary_report([('Delivery', '5000000001'),
                                 ('Retail', '5000000002')], [])
        self.assertEqual(self.scan.find_unclassed_column(report), (None, None))


class ScanTests(unittest.TestCase):

    CLASSES = [('Delivery', '5000000001'), ('Not Specified', 'not_specified')]
    TITLES = ['', 'Delivery', 'Not Specified', 'Total']

    def setUp(self):
        self.scan = _load_module()
        self.scan.Preferences = preferences(per_txn=True)

    def _client(self, summary, detail):
        return _FakeClient({'ProfitAndLoss': summary, 'ProfitAndLossDetail': detail})

    def _one_finding(self, summary_rows, detail_rows=None, classes=None):
        if detail_rows is None:
            detail_rows = [under_group('Income', under_account(
                '40', 'Consulting Income',
                detail_row('7001', '2026-08-04', 'Sales Receipt', '1042',
                           'Northwind Supply', 'August retainer', '', '250.00')))]
        client = self._client(summary_report(classes or self.CLASSES, summary_rows),
                              detail_report(detail_rows))
        return self.scan.scan(client, '2026-08-01', '2026-08-31')

    def test_a_report_with_no_unclassed_column_reads_clear(self):
        result = self._one_finding(
            [account_row('40', 'Consulting Income', '1000.00', '500.00', '1500.00')],
            detail_rows=[under_group('Income', under_account(
                '40', 'Consulting Income',
                detail_row('7001', '2026-08-04', 'Invoice', '1042', 'Northwind Supply',
                           '', 'Delivery', '1000.00')))],
            classes=[('Delivery', '5000000001'), ('Retail', '5000000002')])

        self.assertTrue(result['success'])
        self.assertIsNone(result['unclassed_column'])
        self.assertEqual(result['unclassed_by_account'], [])
        self.assertEqual(result['unclassed_records'], [])
        self.assertIn('CLEAR', result['summary'])

    def test_a_clear_report_still_reads_the_detail_report(self):
        """The detail report is the side that names the records. Skipping it would let a
        summary column nobody found read as no findings."""
        client = self._client(summary_report([('Delivery', '5000000001')], []),
                              detail_report([]))
        self.scan.scan(client, '2026-08-01', '2026-08-31')
        self.assertEqual([name for name, _ in client.asked],
                         ['ProfitAndLoss', 'ProfitAndLossDetail'])

    def test_an_unclassed_column_with_only_zeros_reads_clear(self):
        """The column exists because QuickBooks emitted it. With no money in it there is
        nothing for the client to see."""
        result = self._one_finding(
            [account_row('40', 'Consulting Income', '1000.00', '0.00', '1000.00')],
            detail_rows=[])
        self.assertTrue(result['success'])
        self.assertEqual(result['unclassed_by_account'], [])

    def test_activity_in_the_unclassed_column_fails_the_gate(self):
        result = self._one_finding(
            [account_row('40', 'Consulting Income', '1000.00', '250.00', '1250.00'),
             account_row('50', 'Software Subscriptions', '0.00', '40.00', '40.00')],
            detail_rows=[
                under_group('Income', under_account(
                    '40', 'Consulting Income',
                    detail_row('7001', '2026-08-04', 'Sales Receipt', '1042',
                               'Northwind Supply', 'August retainer', '', '250.00'))),
                under_group('Expenses', under_account(
                    '50', 'Software Subscriptions',
                    detail_row('7002', '2026-08-19', 'Expense', '',
                               'Harbor Lane Studio', 'Seat renewal', '', '40.00'))),
            ])

        self.assertFalse(result['success'])
        self.assertTrue(result['class_tracking'])
        self.assertEqual(result['unclassed_column'], 'Not Specified')
        self.assertEqual(result['unclassed_account_count'], 2)
        self.assertEqual(result['unclassed_account_total'], 290.00)
        self.assertEqual(result['unclassed_record_total'], 290.00)
        self.assertTrue(result['totals_agree'])
        self.assertEqual(result['unclassed_by_account'],
                         [{'account': 'Consulting Income', 'amount': 250.00},
                          {'account': 'Software Subscriptions', 'amount': 40.00}])
        self.assertEqual(result['class_columns'], self.TITLES)
        self.assertEqual(result['unclassed_record_count'], 2)
        self.assertEqual(
            result['unclassed_records'][0],
            {'txn_type': 'Sales Receipt', 'date': '2026-08-04', 'doc_num': '1042',
             'name': 'Northwind Supply', 'account': 'Consulting Income',
             'amount': 250.00, 'id': '7001'})

    def test_accounts_are_ordered_by_size_whatever_their_sign(self):
        result = self._one_finding(
            [account_row('40', 'Consulting Income', '0.00', '90.00', '90.00'),
             account_row('49', 'Sales Discounts', '0.00', '-400.00', '-400.00')],
            detail_rows=[
                under_group('Income',
                            under_account('40', 'Consulting Income',
                                          detail_row('7001', '2026-08-04', 'Invoice', '1',
                                                     'Northwind Supply', '', '', '90.00')),
                            under_account('49', 'Sales Discounts',
                                          detail_row('7002', '2026-08-05', 'Credit Memo',
                                                     '2', 'Northwind Supply', '', '',
                                                     '-400.00'))),
            ])
        self.assertEqual([a['account'] for a in result['unclassed_by_account']],
                         ['Sales Discounts', 'Consulting Income'])
        self.assertEqual(result['unclassed_account_total'], -310.00)

    # ---------------- the account a transaction posts to ----------------

    def test_the_account_is_the_section_the_transaction_sits_in(self):
        """It is not a column. QuickBooks drops account_name from the answer and returns
        no error, so a parser reading it as a column names no account at all."""
        result = self._one_finding(
            [account_row('40', 'Consulting Income', '0.00', '250.00', '250.00')],
            detail_rows=[under_group('Income', under_account(
                '40', 'Consulting Income',
                detail_row('7001', '2026-08-04', 'Invoice', '1042', 'Northwind Supply',
                           '', '', '250.00')))])
        self.assertEqual(result['unclassed_records'][0]['account'], 'Consulting Income')

    def test_the_account_column_is_not_asked_for(self):
        client = self._client(summary_report([('Delivery', '5000000001')], []),
                              detail_report([]))
        self.scan.scan(client, '2026-08-01', '2026-08-31')
        detail = [params for name, params in client.asked
                  if name == 'ProfitAndLossDetail'][0]
        self.assertNotIn('account_name', detail['columns'])
        self.assertIn('klass_name', detail['columns'])

    def test_a_group_header_with_no_id_leaves_the_account_standing(self):
        """Income and Expenses are sections too, and theirs carry no id. Taking one for
        an account would name every transaction after its classification."""
        result = self._one_finding(
            [account_row('50', 'Software Subscriptions', '0.00', '40.00', '40.00')],
            detail_rows=[under_group('Expenses', under_account(
                '50', 'Software Subscriptions',
                detail_row('7002', '2026-08-19', 'Expense', '', 'Harbor Lane Studio',
                           '', '', '40.00')))])
        self.assertEqual([r['account'] for r in result['unclassed_records']],
                         ['Software Subscriptions'])

    def test_a_transaction_in_no_section_reports_no_account(self):
        """A shape with no enclosing account is reported as it is. Inventing one would
        send somebody to the wrong record."""
        result = self._one_finding(
            [account_row('40', 'Consulting Income', '0.00', '250.00', '250.00')],
            detail_rows=[detail_row('7001', '2026-08-04', 'Invoice', '1042',
                                    'Northwind Supply', '', '', '250.00')])
        self.assertEqual(result['unclassed_records'][0]['account'], '')

    # ---------------- the leaf-account test ----------------

    def test_a_row_with_no_account_id_is_not_an_account(self):
        """A top-level "Net Income" row carries amounts and no id. Counting it adds the
        same money to the column a second time."""
        result = self._one_finding([
            account_row('40', 'Consulting Income', '1000.00', '250.00', '1250.00'),
            total_row('Net Income', '1000.00', '250.00', '1250.00'),
        ])
        self.assertEqual([a['account'] for a in result['unclassed_by_account']],
                         ['Consulting Income'])
        self.assertEqual(result['unclassed_account_total'], 250.00)

    def test_a_section_total_is_not_counted_with_the_lines_it_totals(self):
        result = self._one_finding([
            section('Income',
                    [account_row('40', 'Consulting Income', '1000.00', '250.00', '1250.00')],
                    total_values=['1000.00', '250.00', '1250.00']),
        ])
        self.assertEqual(result['unclassed_account_total'], 250.00)
        self.assertEqual(len(result['unclassed_by_account']), 1)

    def test_a_section_header_carrying_amounts_is_not_counted(self):
        """A parent account with its own postings puts them on the Header row. The walk
        reads a row's own ColData, and a Header sits under its own key."""
        result = self._one_finding([
            section('Consulting Income',
                    [account_row('41', 'Retainers', '1000.00', '250.00', '1250.00')],
                    header_values=['9999.00', '8888.00', '17887.00'],
                    total_values=['10999.00', '9138.00', '20137.00'],
                    section_id='40'),
        ])
        self.assertEqual([a['account'] for a in result['unclassed_by_account']],
                         ['Retainers'])
        self.assertEqual(result['unclassed_account_total'], 250.00)

    # ---------------- the two sides ----------------

    def test_a_summary_column_the_detail_does_not_corroborate_fails_the_gate(self):
        """Money on either side is money somebody has to look at. Raising here would kill
        a run over a shape difference and report nothing at all."""
        result = self._one_finding(
            [account_row('40', 'Consulting Income', '1000.00', '250.00', '1250.00')],
            detail_rows=[under_group('Income', under_account(
                '40', 'Consulting Income',
                detail_row('7001', '2026-08-04', 'Invoice', '1042', 'Northwind Supply',
                           '', 'Delivery', '250.00')))])
        self.assertFalse(result['success'])
        self.assertEqual(result['unclassed_account_count'], 1)
        self.assertEqual(result['unclassed_record_count'], 0)
        self.assertIn('disagree', result['summary'])

    def test_unclassed_transactions_with_no_summary_column_fail_the_gate(self):
        result = self._one_finding(
            [account_row('40', 'Consulting Income', '1000.00', '250.00', '1250.00')],
            classes=[('Delivery', '5000000001'), ('Retail', '5000000002')])
        self.assertFalse(result['success'])
        self.assertIsNone(result['unclassed_column'])
        self.assertEqual(result['unclassed_record_count'], 1)
        self.assertIn('disagree', result['summary'])

    def test_two_sides_that_agree_say_nothing_about_disagreeing(self):
        result = self._one_finding(
            [account_row('40', 'Consulting Income', '0.00', '250.00', '250.00')])
        self.assertFalse(result['success'])
        self.assertNotIn('disagree', result['summary'])

    def test_the_two_totals_are_reported_when_they_differ(self):
        """An account row aggregates transactions of both signs, so the sums need not
        match. The difference is reported and does not fail the run."""
        result = self._one_finding(
            [account_row('40', 'Consulting Income', '0.00', '100.00', '100.00')],
            detail_rows=[under_group('Income', under_account(
                '40', 'Consulting Income',
                detail_row('7001', '2026-08-04', 'Invoice', '1', 'Northwind Supply', '',
                           '', '300.00'),
                detail_row('7002', '2026-08-05', 'Credit Memo', '2', 'Northwind Supply',
                           '', '', '-200.00')))])
        self.assertFalse(result['success'])
        self.assertEqual(result['unclassed_account_total'], 100.00)
        self.assertEqual(result['unclassed_record_total'], 100.00)
        self.assertTrue(result['totals_agree'])

    def test_totals_agree_is_false_when_the_sums_differ(self):
        result = self._one_finding(
            [account_row('40', 'Consulting Income', '0.00', '100.00', '100.00')])
        self.assertFalse(result['totals_agree'])
        self.assertEqual(result['unclassed_account_total'], 100.00)
        self.assertEqual(result['unclassed_record_total'], 250.00)

    # ---------------- reading a cell ----------------

    def test_a_cell_that_is_not_a_number_raises(self):
        """Reading it as no activity would drop the row out of the finding in silence."""
        with self.assertRaises(RuntimeError) as raised:
            self._one_finding(
                [account_row('40', 'Consulting Income', '1000.00', '(250.00)', '1250.00')])
        self.assertIn('(250.00)', str(raised.exception))

    def test_an_empty_cell_means_no_activity_in_that_class(self):
        result = self._one_finding(
            [account_row('40', 'Consulting Income', '1000.00', '', '1000.00'),
             account_row('50', 'Software Subscriptions', '0.00', '250.00', '250.00')],
            detail_rows=[under_group('Expenses', under_account(
                '50', 'Software Subscriptions',
                detail_row('7001', '2026-08-04', 'Expense', '', 'Harbor Lane Studio',
                           '', '', '250.00')))])
        self.assertEqual([a['account'] for a in result['unclassed_by_account']],
                         ['Software Subscriptions'])

    def test_a_classed_transaction_is_left_out_of_the_records(self):
        result = self._one_finding(
            [account_row('40', 'Consulting Income', '1000.00', '250.00', '1250.00')],
            detail_rows=[under_group('Income', under_account(
                '40', 'Consulting Income',
                detail_row('7001', '2026-08-04', 'Invoice', '1042', 'Northwind Supply',
                           '', 'Delivery', '1000.00'),
                detail_row('7002', '2026-08-11', 'Invoice', '1043', 'Northwind Supply',
                           '', '', '250.00')))])
        self.assertEqual([r['id'] for r in result['unclassed_records']], ['7002'])

    def test_a_detail_report_with_no_class_column_raises(self):
        """The company tracks classes, so the column was expected. Reading a missing one
        as an empty class reports every transaction; reading it as absent reports none."""
        client = self._client(
            summary_report(self.CLASSES,
                           [account_row('40', 'Consulting Income', '1000.00', '250.00',
                                        '1250.00')]),
            detail_report([], columns=[('Date', 'Date', 'tx_date'),
                                       ('Transaction Type', 'String', 'txn_type'),
                                       ('Amount', 'Money', 'subt_nat_amount')]))
        with self.assertRaises(RuntimeError) as raised:
            self.scan.scan(client, '2026-08-01', '2026-08-31')
        self.assertIn('klass_name', str(raised.exception))

    def test_both_reports_are_read_on_the_accrual_basis(self):
        client = self._client(summary_report([('Delivery', '5000000001')], []),
                              detail_report([]))
        self.scan.scan(client, '2026-08-01', '2026-08-31')
        self.assertEqual(len(client.asked), 2)
        for _, params in client.asked:
            self.assertEqual(params['accounting_method'], 'Accrual')
            self.assertEqual(params['start_date'], '2026-08-01')
            self.assertEqual(params['end_date'], '2026-08-31')


class ExitCodeTests(unittest.TestCase):

    CLASSES = [('Delivery', '5000000001'), ('Not Specified', 'not_specified')]

    def setUp(self):
        self.scan = _load_module()
        self.scan.Preferences = preferences(per_txn=True)

    def _main(self, reports, argv):
        client = _FakeClient(reports)
        original_argv = sys.argv
        original_create = self.scan.create_qbo_client
        self.scan.create_qbo_client = lambda credentials: (client, None)
        out, err = StringIO(), StringIO()
        try:
            sys.argv = argv
            with redirect_stdout(out), redirect_stderr(err):
                code = self.scan.main()
        finally:
            sys.argv = original_argv
            self.scan.create_qbo_client = original_create
        return code, json.loads(out.getvalue())

    def _argv(self, start='2026-08-01', end='2026-08-31'):
        return ['scan_unclassed_pl.py', '--period_start', start, '--period_end', end]

    def test_a_company_that_tracks_no_classes_exits_zero(self):
        self.scan.Preferences = preferences(per_txn=False, per_line=False)
        code, result = self._main({}, self._argv())
        self.assertEqual(code, 0)
        self.assertTrue(result['success'])
        self.assertFalse(result['class_tracking'])

    def test_a_clear_scan_exits_zero(self):
        code, result = self._main(
            {'ProfitAndLoss': summary_report([('Delivery', '5000000001')], []),
             'ProfitAndLossDetail': detail_report([])}, self._argv())
        self.assertEqual(code, 0)
        self.assertTrue(result['success'])

    def test_a_finding_exits_one(self):
        code, result = self._main(
            {'ProfitAndLoss': summary_report(
                self.CLASSES,
                [account_row('40', 'Consulting Income', '1000.00', '250.00', '1250.00')]),
             'ProfitAndLossDetail': detail_report([under_group('Income', under_account(
                 '40', 'Consulting Income',
                 detail_row('7001', '2026-08-04', 'Invoice', '1042', 'Northwind Supply',
                            '', '', '250.00')))])},
            self._argv())
        self.assertEqual(code, 1)
        self.assertFalse(result['success'])
        self.assertNotIn('error', result)
        self.assertEqual(result['unclassed_record_count'], 1)

    def test_a_date_that_is_not_a_date_is_refused_before_any_report(self):
        code, result = self._main({}, self._argv(start='August'))
        self.assertEqual(code, 1)
        self.assertIn('period_start', result['error'])

    def test_an_inverted_window_is_refused(self):
        """QuickBooks answers an inverted window with an empty report, which reads as a
        clear gate over a period nobody scanned."""
        code, result = self._main({}, self._argv(start='2026-08-31', end='2026-08-01'))
        self.assertEqual(code, 1)
        self.assertIn('after period_end', result['error'])

    def test_a_report_failure_is_reported_and_exits_one(self):
        code, result = self._main(
            {'ProfitAndLoss': RuntimeError('report endpoint refused the request')},
            self._argv())
        self.assertEqual(code, 1)
        self.assertIn('scan failed', result['error'])

    def test_a_missing_class_column_exits_one_and_names_itself(self):
        code, result = self._main(
            {'ProfitAndLoss': summary_report(
                self.CLASSES,
                [account_row('40', 'Consulting Income', '1000.00', '250.00', '1250.00')]),
             'ProfitAndLossDetail': detail_report(
                 [], columns=[('Date', 'Date', 'tx_date'),
                              ('Amount', 'Money', 'subt_nat_amount')])},
            self._argv())
        self.assertEqual(code, 1)
        self.assertIn('klass_name', result['error'])
        self.assertIn('tracks classes', result['error'])


if __name__ == '__main__':
    unittest.main()
