#!/usr/bin/env python3
"""
Scan the system of record for P&L activity that carries no class.

The client reads "Profit and Loss by Class" in QuickBooks. Money in that report's unclassed
column belongs to no class, and this script reads the same report and says what is in it.
Why staging cannot answer the question and what to record are in
reference/review-checks.md, Check 12.

## What it reads

The company's accounting preferences first. A company with class tracking switched off
carries no classes, so there is nothing to check and the scan says so and stops. Without
that read, QuickBooks answers such a company with an unclassed column holding the whole
statement, and the scan would call every dollar in the books unclassed. True, and no use
to anybody.

Then two reports, both on the accrual basis, which is the basis the local ledger keeps.

ProfitAndLoss summarized by Classes gives the column the client sees. QuickBooks keys that
column `not_specified`.

ProfitAndLossDetail names every transaction whose class is empty. The account a
transaction posts to is the section header it sits under. Asking for the account as a
column returns nothing and no error.

## Reading a column

A column's identity is the entry named `ColKey` in its `MetaData`. `ColType` holds a data
type and `ColTitle` holds a display label, so the names this script asks for are in
neither.

## The two sides

The summary is the account rollup the client reads. The detail names the records somebody
has to fix. Money on either side fails the gate, so no shape difference between the two
reports can read as a clear gate over money with no class.

Their totals go out side by side with `totals_agree`. A difference leaves the run
standing, because an account row aggregates transactions of both signs, and because the
sign conventions of the two reports have not been checked against a company that tracks
classes. When the two disagree over whether there is any unclassed activity at all, the
summary line says so.

A company that tracks classes and whose detail report carries no class column is a wall,
and the scan raises. Read as an empty class, a missing column reports every transaction.
Read as absent, it reports none.

## Gate semantics

success=False when unclassed P&L activity exists, and exit 1 goes with it, as in
scan_sor_direct_records.py. The gate flags; a firm that wants it to stop a close says so in
its firm files.

READ-ONLY against the books: one preference read, two report reads, no QuickBooks writes,
no local database. It does write `{local_dir}/adapters/.env` when the shared client rotates
an OAuth token, which is the housekeeping every QBO adapter here does.

Usage:
    BOOKKEEPING_CONFIG_PATH=_local-bookkeeping/config.yaml \
      uv run --no-project --with-requirements {module_root}/requirements.txt \
      {module_root}/adapters/qbo/scan_unclassed_pl.py \
      --period_start 2026-07-19 --period_end 2026-08-15
"""

import argparse
import json
import os
import sys
from datetime import datetime

# Bootstrap config. Resolve paths relative to the qbo/ adapter directory, as
# scan_sor_direct_records.py does.
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(script_dir, '..', '..', 'scripts', '_shared'))
sys.path.insert(0, script_dir)
import config_loader

from _shared.client import (
    validate_qbo_env_vars, create_qbo_client, test_qbo_connection,
    refresh_client, save_tokens_if_available,
)
from dotenv import load_dotenv

# SDK entity class — re-exported from the package top level (as qbo_client.py imports it).
from quickbooks.objects import Preferences

_config = config_loader.load_config()
ENV_PATH = os.path.join(_config['local_dir'], 'adapters', '.env')
load_dotenv(ENV_PATH)

# QuickBooks keys the no-class column `not_specified` whatever it renders as its title.
UNCLASSED_KEY = 'not_specified'

# The titles QuickBooks renders over that column, across report versions and locales. The
# fallback for a report that carries no column metadata.
UNCLASSED_LABELS = {'not specified', 'unclassified', 'no class', ''}

# The local ledger is accrual, so the report is read on that basis. Leaving it to the
# company preference would make the answer depend on a setting nobody declared.
ACCOUNTING_METHOD = 'Accrual'

# The detail report's columns, by key. account_name is not among them: the account is the
# section a row sits in, and QuickBooks drops the column from the answer without a word.
DETAIL_COLUMNS = 'tx_date,txn_type,doc_num,name,memo,klass_name,subt_nat_amount'

# Anything under half a cent is a rounding artifact of the report.
CENT = 0.005


def class_tracking(client):
    """Whether this company tracks classes at all.

    Either switch counts. Per-transaction tracking puts a class on a whole record, and
    per-line tracking puts one on a line of an invoice.
    """
    prefs = Preferences.get(qb=client)
    info = getattr(prefs, 'AccountingInfoPrefs', None)
    if info is None:
        raise RuntimeError('Preferences returned no AccountingInfoPrefs, so whether '
                           'this company tracks classes cannot be read')
    return (bool(getattr(info, 'ClassTrackingPerTxn', False))
            or bool(getattr(info, 'ClassTrackingPerTxnLine', False)))


def column_keys(report):
    """Every column's QuickBooks key, in report order.

    The key is the `MetaData` entry named `ColKey`. A column carrying no such entry gets
    the empty string, which matches no name this script asks for.
    """
    keys = []
    for column in report.get('Columns', {}).get('Column', []):
        key = ''
        for entry in column.get('MetaData') or []:
            if entry.get('Name') == 'ColKey':
                key = entry.get('Value') or ''
        keys.append(key)
    return keys


def walk_data_rows(rows, fn, section=None):
    """Call fn(col_data, section) on every data row, nested sections included.

    A QuickBooks report section carries its own labels under Header and Summary, which are
    separate keys. A data row is the only kind with ColData at its top level, so a section
    total stays out of the count alongside the lines it totals.

    `section` is the ColData of the nearest enclosing section header that names something
    with a QuickBooks id. On the detail report that header is the account. A header with
    no id is a classification group, Income or Expenses, and the account it encloses
    stands.
    """
    for row in rows or []:
        header = (row.get('Header') or {}).get('ColData') or []
        inner = header if header and header[0].get('id') else section
        nested = row.get('Rows')
        if isinstance(nested, dict) and nested.get('Row'):
            walk_data_rows(nested['Row'], fn, inner)
        if row.get('ColData'):
            fn(row['ColData'], section)


def parse_amount(raw, where):
    """Report money as a float, or None when the cell is empty.

    An empty cell means no activity in that class. A cell holding anything else means the
    report has a shape this script does not read, so it raises. Read as no activity, the
    row would drop out of the finding without a word.
    """
    text = (raw or '').replace(',', '').strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        raise RuntimeError(f"{where}: cannot read {text!r} as an amount")


def find_unclassed_column(report):
    """Return (index, label) of the no-class column, or (None, None).

    The key is the answer. The title match behind it is the fallback for a report with no
    column metadata, and it skips column 0, which holds the account name under a blank
    title that is itself one of the labels. The last column is the row total.
    """
    columns = report.get('Columns', {}).get('Column', [])

    for i, key in enumerate(column_keys(report)):
        if key == UNCLASSED_KEY:
            return i, (columns[i].get('ColTitle') or '(blank)')

    for i, column in enumerate(columns):
        title = (column.get('ColTitle') or '').strip().lower()
        if i == 0 or title == 'total':
            continue
        if title in UNCLASSED_LABELS:
            return i, (column.get('ColTitle') or '(blank)')
    return None, None


def unclassed_by_account(report, index):
    """Every account with activity in the no-class column.

    A leaf account row carries the account's QuickBooks id in its first cell, and a report
    row with no id is a total or a label. reconcile_trial_balance.py reads the same report
    family the same way. Without the id test, a top-level "Net Income" row counts as an
    account and adds its money to the column a second time.
    """
    found = []

    def collect(col_data, _section):
        if len(col_data) <= index or not col_data[0].get('id'):
            return
        amount = parse_amount(col_data[index].get('value'),
                              'ProfitAndLoss, account ' + (col_data[0].get('value') or '?'))
        if amount is None or abs(amount) < CENT:
            return
        found.append({'account': col_data[0].get('value', ''),
                      'amount': round(amount, 2)})

    walk_data_rows(report.get('Rows', {}).get('Row', []), collect)
    return found


def unclassed_records(report):
    """Every transaction in the detail report whose class cell is empty."""
    index = {key: i for i, key in enumerate(column_keys(report)) if key}
    class_at = index.get('klass_name')
    if class_at is None:
        raise RuntimeError(
            "ProfitAndLossDetail returned no klass_name column on a company that tracks "
            f"classes; asked for: {DETAIL_COLUMNS}")

    found = []

    def cell(col_data, name):
        at = index.get(name)
        if at is None or len(col_data) <= at:
            return ''
        return col_data[at].get('value') or ''

    def collect(col_data, section):
        if len(col_data) <= class_at:
            return
        if (col_data[class_at].get('value') or '').strip():
            return
        amount = parse_amount(cell(col_data, 'subt_nat_amount'),
                              'ProfitAndLossDetail, transaction '
                              + (cell(col_data, 'doc_num') or cell(col_data, 'tx_date') or '?'))
        if amount is None or abs(amount) < CENT:
            return
        date_at = index.get('tx_date')
        found.append({
            'txn_type': cell(col_data, 'txn_type'),
            'date': cell(col_data, 'tx_date'),
            'doc_num': cell(col_data, 'doc_num'),
            'name': cell(col_data, 'name'),
            'account': (section[0].get('value') or '') if section else '',
            'amount': round(amount, 2),
            'id': (col_data[date_at].get('id')
                   if date_at is not None and len(col_data) > date_at else None),
        })

    walk_data_rows(report.get('Rows', {}).get('Row', []), collect)
    return found


def scan(client, period_start, period_end):
    """Read the preference, then both reports, and return the result the caller prints."""
    tracking = class_tracking(client)
    accounts, records, class_columns, label = [], [], [], None

    if tracking:
        summary_report = client.get_report('ProfitAndLoss', qs={
            'start_date': period_start,
            'end_date': period_end,
            'summarize_column_by': 'Classes',
            'accounting_method': ACCOUNTING_METHOD,
        })

        index, label = find_unclassed_column(summary_report)
        class_columns = [(c.get('ColTitle') or '')
                         for c in summary_report.get('Columns', {}).get('Column', [])]
        accounts = unclassed_by_account(summary_report, index) if index is not None else []

        detail_report = client.get_report('ProfitAndLossDetail', qs={
            'start_date': period_start,
            'end_date': period_end,
            'columns': DETAIL_COLUMNS,
            'accounting_method': ACCOUNTING_METHOD,
        })
        records = unclassed_records(detail_report)

    account_total = round(sum(a['amount'] for a in accounts), 2)
    record_total = round(sum(r['amount'] for r in records), 2)
    clear = not accounts and not records

    if not tracking:
        summary = ('NOT APPLICABLE. Class tracking is off in QuickBooks; nothing to '
                   'check.')
    elif clear:
        summary = (f"CLEAR. No unclassed P&L activity in {period_start}..{period_end}. "
                   f"Profit and Loss by Class holds no money in an unclassed column, "
                   f"and every transaction in the period carries a class.")
    else:
        summary = (f"UNCLASSED P&L ACTIVITY in {period_start}..{period_end}. "
                   f"{len(accounts)} account(s) hold {account_total:,.2f} in the "
                   f"{label or 'unclassed'} column of Profit and Loss by Class. The "
                   f"detail report names {len(records)} transaction(s) carrying no "
                   f"class, {record_total:,.2f} in all. Stamp the class on the "
                   f"QuickBooks record.")
        if bool(accounts) != bool(records):
            summary += (' The two reports disagree over whether any exists. Read '
                        'unclassed_by_account against unclassed_records before acting.')

    return {
        'success': clear,
        'class_tracking': tracking,
        'period': [period_start, period_end],
        'accounting_method': ACCOUNTING_METHOD,
        'class_columns': class_columns,
        'unclassed_column': label,
        'unclassed_account_count': len(accounts),
        'unclassed_account_total': account_total,
        'unclassed_by_account': sorted(accounts, key=lambda a: -abs(a['amount'])),
        'unclassed_record_count': len(records),
        'unclassed_record_total': record_total,
        'totals_agree': account_total == record_total,
        'unclassed_records': records,
        'summary': summary,
    }


def parse_args():
    parser = argparse.ArgumentParser(
        description='Scan QuickBooks for P&L activity that carries no class.')
    parser.add_argument('--period_start', required=True,
                        help='Period start YYYY-MM-DD (inclusive).')
    parser.add_argument('--period_end', required=True,
                        help='Period end YYYY-MM-DD (inclusive).')
    return parser.parse_args()


def main():
    args = parse_args()

    for value, label in ((args.period_start, 'period_start'),
                         (args.period_end, 'period_end')):
        try:
            datetime.strptime(value, '%Y-%m-%d')
        except ValueError:
            print(json.dumps({'success': False,
                              'error': f"{label} must be YYYY-MM-DD, got {value!r}"}))
            return 1

    if args.period_start > args.period_end:
        print(json.dumps({'success': False,
                          'error': f"period_start {args.period_start} is after period_end "
                                   f"{args.period_end}; QuickBooks answers an inverted "
                                   f"window with an empty report, which reads as clear"}))
        return 1

    log = lambda message: print(message, file=sys.stderr)
    log(f"Unclassed P&L scan {args.period_start}..{args.period_end}")

    try:
        credentials = validate_qbo_env_vars()
        client, error = create_qbo_client(credentials)
        if error:
            print(json.dumps({'success': False, 'error': error}))
            return 1
        client, error = refresh_client(client)
        if error:
            print(json.dumps({'success': False,
                              'error': f"token refresh failed: {error}"}))
            return 1
        save_tokens_if_available(client, ENV_PATH)
        connected, message = test_qbo_connection(client, ENV_PATH)
        if not connected:
            print(json.dumps({'success': False, 'error': message}))
            return 1
        log(message)
    except Exception as e:
        print(json.dumps({'success': False, 'error': f"auth/setup failed: {e}"}))
        return 1

    try:
        result = scan(client, args.period_start, args.period_end)
    except Exception as e:
        print(json.dumps({'success': False, 'error': f"scan failed: {e}"}))
        return 1
    finally:
        save_tokens_if_available(client, ENV_PATH)

    log(result['summary'])
    print(json.dumps(result, indent=2))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    sys.exit(main())
