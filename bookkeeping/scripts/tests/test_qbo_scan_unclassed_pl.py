#!/usr/bin/env python3
"""
Hermetic tests for scan_unclassed_pl.py. No real QuickBooks calls.

Covers adapters/qbo/scan_unclassed_pl.py:

  * Finding the no-class column by its title, never by its position, over every label
    QuickBooks uses for that bucket.
  * A report with no such column reads clear, and the detail report is never asked for.
  * Activity in that column fails the gate, and every account carrying it is listed.
  * A section total is not counted alongside the lines it totals.
  * A classed transaction is left out of the record list.
  * A detail report that returns no class column raises, so a missing column cannot read
    as no findings.
  * The exit code follows the gate.

The script reaches QuickBooks only through client.get_report, so a stand-in client with
that one method exercises every path. No SDK type is used, so these cases run on a
deployment without the QBO block.

Run:
    python3 -m unittest scripts.tests.test_qbo_scan_unclassed_pl
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

scan_module = None


def _load_module():
    """Import scan_unclassed_pl with config_loader and _shared.client stubbed.

    The script resolves config and loads a .env at import time. Neither is available in a
    hermetic test, so both are supplied as stubs before the import.
    """
    global scan_module
    if scan_module is not None:
        return scan_module

    for name in [k for k in list(sys.modules)
                 if k == '_shared' or k.startswith('_shared.')
                 or k in ('config_loader', 'scan_unclassed_pl')]:
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


def data_row(*values):
    return {'type': 'Data', 'ColData': [{'value': v} for v in values]}


def section(title, rows, total_values):
    """A report section, shaped as QuickBooks shapes one.

    The label and the total live under Header and Summary, which are separate keys from
    the section's own ColData. A data row is the only thing with ColData at its top level.
    """
    return {
        'type': 'Section',
        'Header': {'ColData': [{'value': title}]},
        'Rows': {'Row': rows},
        'Summary': {'ColData': [{'value': 'Total ' + title}]
                    + [{'value': v} for v in total_values]},
    }


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
    row = data_row(date, txn_type, doc_num, name, memo, account, klass, amount)
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

    def _client(self, summary, detail=None):
        reports = {'ProfitAndLoss': summary}
        if detail is not None:
            reports['ProfitAndLossDetail'] = detail
        return _FakeClient(reports)

    def test_a_report_with_no_unclassed_column_reads_clear(self):
        client = self._client(summary_report(
            ['', 'Delivery', 'Retail', 'Total'],
            [data_row('Consulting Income', '1000.00', '500.00', '1500.00')]))
        result = self.scan.scan(client, '2026-08-01', '2026-08-31')

        self.assertTrue(result['success'])
        self.assertIsNone(result['unclassed_column'])
        self.assertEqual(result['unclassed_by_account'], [])
        self.assertEqual(result['unclassed_total'], 0)
        self.assertIn('CLEAR', result['summary'])

    def test_a_clear_report_never_asks_for_the_detail_report(self):
        client = self._client(summary_report(
            ['', 'Delivery', 'Total'],
            [data_row('Consulting Income', '1000.00', '1000.00')]))
        self.scan.scan(client, '2026-08-01', '2026-08-31')
        self.assertEqual([name for name, _ in client.asked], ['ProfitAndLoss'])

    def test_an_unclassed_column_with_only_zeros_reads_clear(self):
        """The column exists because QuickBooks emitted it. With no money in it there is
        nothing for the client to see."""
        client = self._client(summary_report(
            self.COLUMNS,
            [data_row('Consulting Income', '1000.00', '0.00', '1000.00')]))
        result = self.scan.scan(client, '2026-08-01', '2026-08-31')

        self.assertTrue(result['success'])
        self.assertEqual(result['unclassed_by_account'], [])

    def test_activity_in_the_unclassed_column_fails_the_gate(self):
        client = self._client(
            summary_report(self.COLUMNS, [
                data_row('Consulting Income', '1000.00', '250.00', '1250.00'),
                data_row('Software Subscriptions', '0.00', '-40.00', '-40.00'),
            ]),
            detail_report([
                detail_row('7001', '2026-08-04', 'Sales Receipt', '1042',
                           'Northwind Supply', 'August retainer',
                           'Consulting Income', '', '250.00'),
                detail_row('7002', '2026-08-19', 'Expense', '',
                           'Harbor Lane Studio', 'Seat renewal',
                           'Software Subscriptions', '', '-40.00'),
            ]))
        result = self.scan.scan(client, '2026-08-01', '2026-08-31')

        self.assertFalse(result['success'])
        self.assertEqual(result['unclassed_column'], 'Not Specified')
        self.assertEqual(result['unclassed_total'], 210.00)
        self.assertEqual(result['unclassed_by_account'],
                         [{'account': 'Consulting Income', 'amount': 250.00},
                          {'account': 'Software Subscriptions', 'amount': -40.00}])
        self.assertEqual(result['class_columns'], self.COLUMNS)
        self.assertEqual(result['unclassed_record_count'], 2)
        self.assertEqual(
            result['unclassed_records'][0],
            {'txn_type': 'Sales Receipt', 'date': '2026-08-04', 'doc_num': '1042',
             'name': 'Northwind Supply', 'account': 'Consulting Income',
             'amount': 250.00, 'id': '7001'})

    def test_accounts_are_ordered_by_size_whatever_their_sign(self):
        client = self._client(
            summary_report(self.COLUMNS, [
                data_row('Consulting Income', '0.00', '90.00', '90.00'),
                data_row('Software Subscriptions', '0.00', '-400.00', '-400.00'),
            ]),
            detail_report([]))
        result = self.scan.scan(client, '2026-08-01', '2026-08-31')
        self.assertEqual([a['account'] for a in result['unclassed_by_account']],
                         ['Software Subscriptions', 'Consulting Income'])

    def test_a_section_total_is_not_counted_with_the_lines_it_totals(self):
        """A section's label and total live under Header and Summary. Counting either one
        would double every sectioned report."""
        client = self._client(
            summary_report(self.COLUMNS, [
                section('Income',
                        [data_row('Consulting Income', '1000.00', '250.00', '1250.00')],
                        ['1000.00', '250.00', '1250.00']),
            ]),
            detail_report([]))
        result = self.scan.scan(client, '2026-08-01', '2026-08-31')

        self.assertEqual(result['unclassed_total'], 250.00)
        self.assertEqual(len(result['unclassed_by_account']), 1)

    def test_a_classed_transaction_is_left_out_of_the_records(self):
        client = self._client(
            summary_report(self.COLUMNS,
                           [data_row('Consulting Income', '1000.00', '250.00', '1250.00')]),
            detail_report([
                detail_row('7001', '2026-08-04', 'Invoice', '1042', 'Northwind Supply',
                           '', 'Consulting Income', 'Delivery', '1000.00'),
                detail_row('7002', '2026-08-11', 'Invoice', '1043', 'Northwind Supply',
                           '', 'Consulting Income', '', '250.00'),
            ]))
        result = self.scan.scan(client, '2026-08-01', '2026-08-31')

        self.assertEqual([r['id'] for r in result['unclassed_records']], ['7002'])

    def test_a_detail_report_with_no_class_column_raises(self):
        """Reading a missing column as an empty class would report every transaction, and
        reading it as absent would report none. Neither is an answer."""
        client = self._client(
            summary_report(self.COLUMNS,
                           [data_row('Consulting Income', '1000.00', '250.00', '1250.00')]),
            detail_report([], column_types=['tx_date', 'txn_type', 'subt_nat_amount']))
        with self.assertRaises(RuntimeError) as raised:
            self.scan.scan(client, '2026-08-01', '2026-08-31')
        self.assertIn('klass_name', str(raised.exception))

    def test_both_reports_are_read_on_the_accrual_basis(self):
        client = self._client(
            summary_report(self.COLUMNS,
                           [data_row('Consulting Income', '1000.00', '250.00', '1250.00')]),
            detail_report([]))
        self.scan.scan(client, '2026-08-01', '2026-08-31')
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

    def test_a_clear_scan_exits_zero(self):
        code, result = self._main(
            {'ProfitAndLoss': summary_report(['', 'Delivery', 'Total'], [])},
            ['scan_unclassed_pl.py', '--period_start', '2026-08-01',
             '--period_end', '2026-08-31'])
        self.assertEqual(code, 0)
        self.assertTrue(result['success'])

    def test_a_finding_exits_one(self):
        code, result = self._main(
            {'ProfitAndLoss': summary_report(
                self.COLUMNS,
                [data_row('Consulting Income', '1000.00', '250.00', '1250.00')]),
             'ProfitAndLossDetail': detail_report([])},
            ['scan_unclassed_pl.py', '--period_start', '2026-08-01',
             '--period_end', '2026-08-31'])
        self.assertEqual(code, 1)
        self.assertFalse(result['success'])

    def test_a_date_that_is_not_a_date_is_refused_before_any_report(self):
        code, result = self._main(
            {}, ['scan_unclassed_pl.py', '--period_start', 'August',
                 '--period_end', '2026-08-31'])
        self.assertEqual(code, 1)
        self.assertIn('period_start', result['error'])

    def test_a_report_failure_is_reported_and_exits_one(self):
        code, result = self._main(
            {'ProfitAndLoss': RuntimeError('report endpoint refused the request')},
            ['scan_unclassed_pl.py', '--period_start', '2026-08-01',
             '--period_end', '2026-08-31'])
        self.assertEqual(code, 1)
        self.assertIn('scan failed', result['error'])


if __name__ == '__main__':
    unittest.main()
