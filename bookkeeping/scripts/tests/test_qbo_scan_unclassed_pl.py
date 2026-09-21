#!/usr/bin/env python3
"""
Hermetic tests for scan_unclassed_pl.py. No real QuickBooks calls.

Covers adapters/qbo/scan_unclassed_pl.py:

  * Finding the no-class column by its title, over every label QuickBooks uses for that
    bucket.
  * The leaf-account test. A report row with no account id is a total or a label, and
    counting one adds the same money to the column twice.
  * The tie-out between the two reports. A summary that finds a column the detail does not
    corroborate, or the other way about, raises.
  * A cell that is not a number raises, so a row cannot drop out of a finding in silence.
  * A classed transaction is left out of the record list.
  * A detail report with no class column raises, so a missing column cannot read as no
    findings.
  * The exit code follows the gate, and an inverted window is refused.

The script reaches QuickBooks only through client.get_report, so a stand-in client with
that one method exercises every path. No SDK type is used, so these cases run on a
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
STUBBED = ('_shared', '_shared.client', 'config_loader', 'scan_unclassed_pl')

scan_module = None
_saved_modules = None
_saved_path = None


def _load_module():
    """Import scan_unclassed_pl with config_loader and _shared.client stubbed.

    The script resolves config and loads a .env at import time. Neither is available in a
    hermetic test, so both are supplied as stubs before the import. _shared and
    config_loader are names other test modules import for real, so what was there is kept
    and tearDownModule puts it back.
    """
    global scan_module, _saved_modules, _saved_path
    if scan_module is not None:
        return scan_module

    names = [k for k in list(sys.modules)
             if k == '_shared' or k.startswith('_shared.')
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


def account_row(account_id, *values):
    """A leaf account row. QuickBooks puts the account's id on the first cell."""
    row = {'type': 'Data', 'ColData': [{'value': v} for v in values]}
    row['ColData'][0]['id'] = account_id
    return row


def total_row(*values):
    """A report row with no account id. QuickBooks shapes a total or a label this way."""
    return {'type': 'Data', 'ColData': [{'value': v} for v in values]}


def section(title, rows, header_values=None, total_values=None):
    """A report section, shaped as QuickBooks shapes one.

    The section's label and total live under Header and Summary, which are keys separate
    from a data row's own ColData. header_values fills the Header's amount cells, which is
    the shape a parent account with its own postings would take.
    """
    header = [{'value': title}] + [{'value': v} for v in (header_values or [])]
    summary = [{'value': 'Total ' + title}] + [{'value': v} for v in (total_values or [])]
    return {'type': 'Section',
            'Header': {'ColData': header},
            'Rows': {'Row': rows},
            'Summary': {'ColData': summary}}


def summary_report(column_titles, rows):
    return {'Columns': {'Column': [{'ColTitle': t} for t in column_titles]},
            'Rows': {'Row': rows}}


DETAIL_COLUMN_TYPES = ['tx_date', 'txn_type', 'doc_num', 'name', 'memo',
                       'account_name', 'klass_name', 'subt_nat_amount']


def detail_report(rows, column_types=None):
    return {'Columns': {'Column': [{'ColType': t}
                                   for t in (column_types or DETAIL_COLUMN_TYPES)]},
            'Rows': {'Row': rows}}


def detail_row(txn_id, date, txn_type, doc_num, name, memo, account, klass, amount):
    row = {'type': 'Data',
           'ColData': [{'value': v} for v in
                       (date, txn_type, doc_num, name, memo, account, klass, amount)]}
    row['ColData'][0]['id'] = txn_id
    return row


class FindUnclassedColumnTests(unittest.TestCase):

    def setUp(self):
        self.scan = _load_module()

    def test_every_label_quickbooks_uses_is_recognized(self):
        for title in ('Not Specified', 'Unclassified', 'No Class', ''):
            report = summary_report(['', 'Delivery', title, 'Total'], [])
            index, label = self.scan.find_unclassed_column(report)
            self.assertEqual(index, 2, title)
            self.assertEqual(label, title or '(blank)')

    def test_the_label_is_matched_whatever_its_case_and_padding(self):
        report = summary_report(['', '  NOT SPECIFIED  ', 'Total'], [])
        index, label = self.scan.find_unclassed_column(report)
        self.assertEqual(index, 1)
        self.assertEqual(label, '  NOT SPECIFIED  ')

    def test_the_account_column_is_never_the_answer(self):
        """Column 0 holds the account name and its title is blank, which is also one of
        the labels. Reading it as the no-class column would fail every report."""
        report = summary_report(['', 'Delivery', 'Total'], [])
        self.assertEqual(self.scan.find_unclassed_column(report), (None, None))

    def test_the_total_column_is_never_the_answer(self):
        report = summary_report(['', 'Delivery', 'total'], [])
        self.assertEqual(self.scan.find_unclassed_column(report), (None, None))

    def test_a_report_with_every_class_named_has_no_such_column(self):
        report = summary_report(['', 'Delivery', 'Retail', 'Total'], [])
        self.assertEqual(self.scan.find_unclassed_column(report), (None, None))


class ScanTests(unittest.TestCase):

    COLUMNS = ['', 'Delivery', 'Not Specified', 'Total']

    def setUp(self):
        self.scan = _load_module()

    def _client(self, summary, detail):
        return _FakeClient({'ProfitAndLoss': summary, 'ProfitAndLossDetail': detail})

    def _one_finding(self, summary_rows, detail_rows=None, columns=None):
        if detail_rows is None:
            detail_rows = [detail_row('7001', '2026-08-04', 'Sales Receipt', '1042',
                                      'Northwind Supply', 'August retainer',
                                      'Consulting Income', '', '250.00')]
        client = self._client(summary_report(columns or self.COLUMNS, summary_rows),
                              detail_report(detail_rows))
        return self.scan.scan(client, '2026-08-01', '2026-08-31')

    def test_a_report_with_no_unclassed_column_reads_clear(self):
        result = self._one_finding(
            [account_row('40', 'Consulting Income', '1000.00', '500.00', '1500.00')],
            detail_rows=[detail_row('7001', '2026-08-04', 'Invoice', '1042',
                                    'Northwind Supply', '', 'Consulting Income',
                                    'Delivery', '1000.00')],
            columns=['', 'Delivery', 'Retail', 'Total'])

        self.assertTrue(result['success'])
        self.assertIsNone(result['unclassed_column'])
        self.assertEqual(result['unclassed_by_account'], [])
        self.assertEqual(result['unclassed_records'], [])
        self.assertIn('CLEAR', result['summary'])

    def test_a_clear_report_still_reads_the_detail_report(self):
        """The detail report is what corroborates a clear summary. Skipping it would let a
        relabelled column read as no findings."""
        client = self._client(
            summary_report(['', 'Delivery', 'Total'], []), detail_report([]))
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
                detail_row('7001', '2026-08-04', 'Sales Receipt', '1042',
                           'Northwind Supply', 'August retainer',
                           'Consulting Income', '', '250.00'),
                detail_row('7002', '2026-08-19', 'Expense', '',
                           'Harbor Lane Studio', 'Seat renewal',
                           'Software Subscriptions', '', '40.00'),
            ])

        self.assertFalse(result['success'])
        self.assertEqual(result['unclassed_column'], 'Not Specified')
        self.assertEqual(result['unclassed_account_count'], 2)
        self.assertEqual(result['unclassed_account_total'], 290.00)
        self.assertEqual(result['unclassed_record_total'], 290.00)
        self.assertTrue(result['totals_agree'])
        self.assertEqual(result['unclassed_by_account'],
                         [{'account': 'Consulting Income', 'amount': 250.00},
                          {'account': 'Software Subscriptions', 'amount': 40.00}])
        self.assertEqual(result['class_columns'], self.COLUMNS)
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
                detail_row('7001', '2026-08-04', 'Invoice', '1', 'Northwind Supply', '',
                           'Consulting Income', '', '90.00'),
                detail_row('7002', '2026-08-05', 'Credit Memo', '2', 'Northwind Supply',
                           '', 'Sales Discounts', '', '-400.00'),
            ])
        self.assertEqual([a['account'] for a in result['unclassed_by_account']],
                         ['Sales Discounts', 'Consulting Income'])
        self.assertEqual(result['unclassed_account_total'], -310.00)

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
                    total_values=['10999.00', '9138.00', '20137.00']),
        ])
        self.assertEqual([a['account'] for a in result['unclassed_by_account']],
                         ['Retainers'])
        self.assertEqual(result['unclassed_account_total'], 250.00)

    # ---------------- the tie-out ----------------

    def test_a_summary_column_the_detail_does_not_corroborate_raises(self):
        """A client may name a real class "Unclassified". Every properly classed dollar in
        it would then read as a finding, and no transaction would back that up."""
        with self.assertRaises(RuntimeError) as raised:
            self._one_finding(
                [account_row('40', 'Consulting Income', '1000.00', '250.00', '1250.00')],
                detail_rows=[
                    detail_row('7001', '2026-08-04', 'Invoice', '1042',
                               'Northwind Supply', '', 'Consulting Income',
                               'Unclassified', '250.00')],
                columns=['', 'Delivery', 'Unclassified', 'Total'])
        self.assertIn('disagree', str(raised.exception))

    def test_unclassed_transactions_with_no_summary_column_raise(self):
        """QuickBooks labelling the bucket something this scan does not know would
        otherwise print CLEAR over money that has no class."""
        with self.assertRaises(RuntimeError) as raised:
            self._one_finding(
                [account_row('40', 'Consulting Income', '1000.00', '250.00', '1250.00')],
                columns=['', 'Delivery', 'Nicht angegeben', 'Total'])
        self.assertIn('disagree', str(raised.exception))

    def test_the_two_totals_are_reported_when_they_differ(self):
        """An account row aggregates transactions of both signs, so the sums need not
        match. The difference is reported and does not fail the run."""
        result = self._one_finding(
            [account_row('40', 'Consulting Income', '0.00', '100.00', '100.00')],
            detail_rows=[
                detail_row('7001', '2026-08-04', 'Invoice', '1', 'Northwind Supply', '',
                           'Consulting Income', '', '300.00'),
                detail_row('7002', '2026-08-05', 'Credit Memo', '2', 'Northwind Supply',
                           '', 'Consulting Income', '', '-200.00'),
            ])
        self.assertFalse(result['success'])
        self.assertEqual(result['unclassed_account_total'], 100.00)
        self.assertEqual(result['unclassed_record_total'], 100.00)
        self.assertTrue(result['totals_agree'])

    def test_totals_agree_is_false_when_the_sums_differ(self):
        result = self._one_finding(
            [account_row('40', 'Consulting Income', '0.00', '100.00', '100.00')],
            detail_rows=[
                detail_row('7001', '2026-08-04', 'Invoice', '1', 'Northwind Supply', '',
                           'Consulting Income', '', '250.00')])
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
            detail_rows=[detail_row('7001', '2026-08-04', 'Expense', '', 'Harbor Lane '
                                    'Studio', '', 'Software Subscriptions', '', '250.00')])
        self.assertEqual([a['account'] for a in result['unclassed_by_account']],
                         ['Software Subscriptions'])

    def test_a_classed_transaction_is_left_out_of_the_records(self):
        result = self._one_finding(
            [account_row('40', 'Consulting Income', '1000.00', '250.00', '1250.00')],
            detail_rows=[
                detail_row('7001', '2026-08-04', 'Invoice', '1042', 'Northwind Supply',
                           '', 'Consulting Income', 'Delivery', '1000.00'),
                detail_row('7002', '2026-08-11', 'Invoice', '1043', 'Northwind Supply',
                           '', 'Consulting Income', '', '250.00'),
            ])
        self.assertEqual([r['id'] for r in result['unclassed_records']], ['7002'])

    def test_a_detail_report_with_no_class_column_raises(self):
        """Reading a missing column as an empty class would report every transaction, and
        reading it as absent would report none. Neither is an answer."""
        client = self._client(
            summary_report(self.COLUMNS,
                           [account_row('40', 'Consulting Income', '1000.00', '250.00',
                                        '1250.00')]),
            detail_report([], column_types=['tx_date', 'txn_type', 'subt_nat_amount']))
        with self.assertRaises(RuntimeError) as raised:
            self.scan.scan(client, '2026-08-01', '2026-08-31')
        self.assertIn('klass_name', str(raised.exception))

    def test_both_reports_are_read_on_the_accrual_basis(self):
        client = self._client(summary_report(['', 'Delivery', 'Total'], []),
                              detail_report([]))
        self.scan.scan(client, '2026-08-01', '2026-08-31')
        self.assertEqual(len(client.asked), 2)
        for _, params in client.asked:
            self.assertEqual(params['accounting_method'], 'Accrual')
            self.assertEqual(params['start_date'], '2026-08-01')
            self.assertEqual(params['end_date'], '2026-08-31')


class ExitCodeTests(unittest.TestCase):

    COLUMNS = ['', 'Delivery', 'Not Specified', 'Total']

    def setUp(self):
        self.scan = _load_module()

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

    def test_a_clear_scan_exits_zero(self):
        code, result = self._main(
            {'ProfitAndLoss': summary_report(['', 'Delivery', 'Total'], []),
             'ProfitAndLossDetail': detail_report([])}, self._argv())
        self.assertEqual(code, 0)
        self.assertTrue(result['success'])

    def test_a_finding_exits_one(self):
        code, result = self._main(
            {'ProfitAndLoss': summary_report(
                self.COLUMNS,
                [account_row('40', 'Consulting Income', '1000.00', '250.00', '1250.00')]),
             'ProfitAndLossDetail': detail_report([
                 detail_row('7001', '2026-08-04', 'Invoice', '1042', 'Northwind Supply',
                            '', 'Consulting Income', '', '250.00')])},
            self._argv())
        self.assertEqual(code, 1)
        self.assertFalse(result['success'])

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

    def test_a_tie_out_failure_exits_one_and_names_itself(self):
        code, result = self._main(
            {'ProfitAndLoss': summary_report(
                ['', 'Delivery', 'Nicht angegeben', 'Total'],
                [account_row('40', 'Consulting Income', '1000.00', '250.00', '1250.00')]),
             'ProfitAndLossDetail': detail_report([
                 detail_row('7001', '2026-08-04', 'Invoice', '1042', 'Northwind Supply',
                            '', 'Consulting Income', '', '250.00')])},
            self._argv())
        self.assertEqual(code, 1)
        self.assertIn('disagree', result['error'])


if __name__ == '__main__':
    unittest.main()
