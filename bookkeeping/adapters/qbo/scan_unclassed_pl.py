#!/usr/bin/env python3
"""
Scan the system of record for P&L activity that carries no class.

The client reads "Profit and Loss by Class" in QuickBooks. When that report shows an
unclassed column, the money in it belongs to no class, and the client sees it. This
script reads the same report and reports what is in that column.

## Why the staging database cannot answer this

Staging is not the system of record. A record can exist in QuickBooks that staging never
created: a bank-feed entry, a hand-keyed transaction, a payment processor's auto-post. A
record can also be adopted into staging during a close, and the adoption stamps a class on
the local row while the QuickBooks original still has none. In both cases staging reads
clean and QuickBooks is still wrong.

## What it reads

Two reports, both on the accrual basis, which is the basis the local ledger keeps.

ProfitAndLoss summarized by Classes gives the column the client sees. The no-class column
is found by its title ("Not Specified", "Unclassified", "No Class", or blank), never by its
position. A title match survives a column moving. It does not survive a relabel, and a
client may also name a real class one of those words, so the second report is what makes
the answer safe.

ProfitAndLossDetail with its class column names every transaction whose class is empty.

## The tie-out

The two reports have to agree that there is unclassed activity, or that there is none. A
disagreement means the column lookup found the wrong column, or missed the right one, or
fired on a real class a client happened to name "Unclassified". The scan raises rather than
answer, because both failure directions are silent: one reports a client's properly classed
money as a fault, the other reports a clear gate over money with no class.

Their amounts are reported side by side with `totals_agree`, and a difference does not fail
the run. An account row aggregates transactions of both signs, so the two sums are not
required to match line for line, and the sign conventions of the two reports have not been
checked against a live company.

## Gate semantics

success=False when unclassed P&L activity exists. Exit 1 goes with it, as in
scan_sor_direct_records.py. This is a Review check, not a Hard Stop: a client that uses no
classes has every P&L line in that column and nothing to resolve. See
reference/review-checks.md, Check 12.

The fix is to stamp the class on the QuickBooks record. The staging row already holds the
right class, so reclassifying locally would change the wrong side.

READ-ONLY against the books: two report reads, no QuickBooks writes, no local database. It
does write `{local_dir}/adapters/.env` when the shared client rotates an OAuth token, which
is the housekeeping every QBO adapter here does.

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

_config = config_loader.load_config()
ENV_PATH = os.path.join(_config['local_dir'], 'adapters', '.env')
load_dotenv(ENV_PATH)

# QuickBooks labels the no-class bucket differently across report versions and locales. A
# label outside this set reads as no unclassed column at all, which the detail report then
# contradicts, so the tie-out catches it.
UNCLASSED_LABELS = {'not specified', 'unclassified', 'no class', ''}

# The local ledger is accrual, so the report is read on that basis. Leaving it to the
# company preference would make the answer depend on a setting nobody declared.
ACCOUNTING_METHOD = 'Accrual'

# The detail report's columns, in the report's own column names.
DETAIL_COLUMNS = ('tx_date,txn_type,doc_num,name,memo,account_name,'
                  'klass_name,subt_nat_amount')

# Anything under half a cent is a rounding artifact of the report, not activity.
CENT = 0.005


def walk_data_rows(rows, fn):
    """Call fn on the ColData of every data row, nested sections included.

    A QuickBooks report section carries its own labels under Header and Summary, which are
    separate keys. Only a data row has ColData at its top level, so a section total is
    never counted alongside the lines it totals.
    """
    for row in rows or []:
        nested = row.get('Rows')
        if isinstance(nested, dict) and nested.get('Row'):
            walk_data_rows(nested['Row'], fn)
        if row.get('ColData'):
            fn(row['ColData'])


def parse_amount(raw, where):
    """Report money as a float, or None when the cell is empty.

    An empty cell means no activity. A cell holding something that is not a number means
    the report is not the shape this script reads, so it raises. Reading it as no activity
    would drop the row out of the finding without a word.
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

    The column is found by its title. Column 0 is the account name and the last column is
    usually the total, so neither can be the answer, and no position is assumed.
    """
    columns = report.get('Columns', {}).get('Column', [])
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
    row that carries no id is a total or a label. reconcile_trial_balance.py reads the same
    report family the same way. Without the id test a top-level "Net Income" row counts as
    an account and its money is added to the column a second time.
    """
    found = []

    def collect(col_data):
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
    columns = [(c.get('ColType') or c.get('ColTitle') or '')
               for c in report.get('Columns', {}).get('Column', [])]
    index = {name: i for i, name in enumerate(columns)}
    class_at = index.get('klass_name')
    if class_at is None:
        raise RuntimeError(
            "ProfitAndLossDetail returned no klass_name column; asked for: "
            f"{DETAIL_COLUMNS}")

    found = []

    def cell(col_data, name):
        at = index.get(name)
        if at is None or len(col_data) <= at:
            return ''
        return col_data[at].get('value') or ''

    def collect(col_data):
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
            'account': cell(col_data, 'account_name'),
            'amount': round(amount, 2),
            'id': (col_data[date_at].get('id')
                   if date_at is not None and len(col_data) > date_at else None),
        })

    walk_data_rows(report.get('Rows', {}).get('Row', []), collect)
    return found


def scan(client, period_start, period_end):
    """Read both reports, tie them out, and return the result the caller prints."""
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

    if bool(accounts) != bool(records):
        raise RuntimeError(
            f"the two reports disagree for {period_start}..{period_end}. "
            f"ProfitAndLoss by Class found {len(accounts)} account(s) in the "
            f"{label!r} column and ProfitAndLossDetail found {len(records)} "
            f"transaction(s) with an empty class. The class columns are "
            f"{class_columns}. Either the column lookup took a real class for the "
            f"no-class bucket, or QuickBooks labels that bucket with a title this "
            f"scan does not know.")

    clear = not accounts
    if clear:
        summary = (f"CLEAR. No unclassed P&L activity in {period_start}..{period_end}. "
                   f"Profit and Loss by Class has no unclassed column, and no "
                   f"transaction in the period has an empty class.")
    else:
        summary = (f"UNCLASSED P&L ACTIVITY. {len(accounts)} account(s) and "
                   f"{len(records)} transaction(s) carry no class in "
                   f"{period_start}..{period_end}, and the client sees them in the "
                   f"'{label}' column of Profit and Loss by Class. The column reads "
                   f"{account_total:,.2f} as the report renders it. Stamp the class on "
                   f"the QuickBooks record.")

    return {
        'success': clear,
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
