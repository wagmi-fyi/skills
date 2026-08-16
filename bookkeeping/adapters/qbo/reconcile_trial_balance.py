#!/usr/bin/env python3
"""
Reconcile Trial Balance — Local DB vs QBO (as-of, RE-rollup-aware)

Verifies that everything in the local staging DB has been faithfully published to
QBO **as of a given date**, with no variances — seeing through QBO's automatic
fiscal-year close (prior-year net income swept into Retained Earnings) by comparing
on the axis where that sweep cannot hide a real discrepancy:

  • Balance-sheet + equity accounts  -> CUMULATIVE balance as of --as_of_date
        (via QBO TrialBalance; unaffected by the P&L->RE sweep)
  • P&L accounts                     -> PER-ACCOUNT activity vs QBO ProfitAndLoss
        (non-rolling report; honors arbitrary date ranges), split into:
          - prior fiscal years   [earliest .. current-FY-start - 1]
          - current fiscal year  [current-FY-start .. as_of]
  • Retained Earnings (the sweep)    -> DERIVED check: the RE divergence must equal
        the prior-years' net income, so the sweep is proven, never assumed.

A balanced, prior-period, P&L-internal discrepancy (a duplicate-published reclass,
or a DR-expense/CR-income that failed to publish) — the class a net-zero or
cumulative-TB compare would hide — surfaces here as a per-account variance.

Fiscal-year start is an INPUT (--fiscal_year_start), not auto-detected: this script
is a pure function of its arguments and the calling agent owns discovery (research
via Preferences.AccountingInfoPrefs.FirstMonthOfFiscalYear, or client config). A
wrong value cannot cause a false PASS — it only moves the sweep boundary, which
surfaces as a loud Retained-Earnings check failure.

Each P&L pull is self-validated: the per-account leaf sum must reconstruct the
report's own Net Income line, or the run FAILS rather than trust a misparse.

Usage:
    BOOKKEEPING_CONFIG_PATH=_local-bookkeeping/config.yaml \
      .venv/bin/python3 ~/.claude/skills/bookkeeping/adapters/qbo/reconcile_trial_balance.py \
      --as_of_date 2026-05-31 --fiscal_year_start January
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from typing import Dict, List

# Bootstrap config — resolve paths relative to the qbo/ adapter directory
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(script_dir, '..', '..', 'scripts', '_shared'))
sys.path.insert(0, script_dir)  # For _shared.client imports
import config_loader

from _shared.client import (
    validate_qbo_env_vars, create_qbo_client, test_qbo_connection,
    refresh_client, save_tokens_if_available,
)
from dotenv import load_dotenv

_config = config_loader.load_config()
ENV_PATH = os.path.join(_config['local_dir'], 'adapters', '.env')
DB_PATH = config_loader.get_db_path()
load_dotenv(ENV_PATH)

EPS = 0.01
EARLIEST = '2000-01-01'  # as-of TB start (ignored for BS) and prior-years P&L floor
_MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july',
           'august', 'september', 'october', 'november', 'december']


def parse_args():
    p = argparse.ArgumentParser(
        description='Reconcile local DB vs QBO as of a date (RE-rollup-aware).')
    p.add_argument('--as_of_date', '--end_date', dest='as_of_date', required=True,
                   help='Reconciliation frontier YYYY-MM-DD: verify everything in local '
                        'is published to QBO as of this date. (--end_date is an alias.)')
    p.add_argument('--fiscal_year_start', default=None,
                   help="Client's fiscal-year start month (name or 1-12). Default "
                        "January (calendar). Pass the real value if the client differs; "
                        "research via QBO Preferences.AccountingInfoPrefs."
                        "FirstMonthOfFiscalYear, or client config.")
    p.add_argument('--start_date', default=None,
                   help='Optional: also report P&L activity over [start_date, as_of] as a '
                        'focused secondary section (e.g. the just-closed period).')
    p.add_argument('--retained_earnings_code', default=None,
                   help='Optional: local account code of Retained Earnings. If omitted, '
                        'auto-detect equity accounts named "Retained Earnings".')
    return p.parse_args()


def month_num(s) -> int:
    s = str(s).strip().lower()
    if s.isdigit() and 1 <= int(s) <= 12:
        return int(s)
    for i, m in enumerate(_MONTHS):
        if m == s or m.startswith(s):
            return i + 1
    raise ValueError(f"Unrecognized fiscal_year_start: {s!r}")


def current_fy_start(as_of: str, fy_month: int) -> str:
    d = datetime.strptime(as_of, '%Y-%m-%d')
    year = d.year if d.month >= fy_month else d.year - 1
    return f"{year:04d}-{fy_month:02d}-01"


def day_before(date_str: str) -> str:
    return (datetime.strptime(date_str, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')


def get_id_maps(conn):
    id_to_code, code_to_type, code_to_name = {}, {}, {}
    for code, rid, typ, name in conn.execute(
            "SELECT code, remote_id, type, name FROM chart_of_accounts"):
        code_to_type[code] = typ
        code_to_name[code] = name or ''
        if rid:
            id_to_code[str(rid)] = code
    return id_to_code, code_to_type, code_to_name


def parse_qbo_tb(report: dict, id_to_code: Dict[str, str]) -> Dict[str, float]:
    """QBO TrialBalance -> {account_code: signed_balance (debit - credit)}.

    Identifies the account by the QBO entity id in ColData[0].id, mapped to the local
    code via chart_of_accounts.remote_id (the 'show account numbers' preference is off
    for many clients, so the name prefix is not reliable).
    """
    balances: Dict[str, float] = {}

    def walk(rows):
        for row in rows:
            nested = row.get('Rows', {})
            if isinstance(nested, dict) and nested.get('Row'):
                walk(nested['Row'])
            cd = row.get('ColData', [])
            if not cd or len(cd) < 3:
                continue
            acct = cd[0].get('value', '')
            if not acct or acct == 'Total':
                continue
            qid = cd[0].get('id', '')
            code = id_to_code.get(str(qid))
            if not code:
                first = acct.split(' ', 1)[0].strip()
                if any(c.isdigit() for c in first):
                    code = first
                elif qid:
                    code = f"QBO-{qid}"
                else:
                    continue
            try:
                debit = float((cd[1].get('value') or '0').replace(',', ''))
                credit = float((cd[2].get('value') or '0').replace(',', ''))
            except ValueError:
                continue
            balances[code] = round(debit - credit, 2)

    walk(report.get('Rows', {}).get('Row', []))
    return balances


def get_local_cumulative(conn, as_of: str) -> Dict[str, float]:
    """Local cumulative signed balance (debit - credit, dollars) per account <= as_of."""
    rows = conn.execute("""
        SELECT p.account_code,
               SUM(CASE WHEN p.direction = 'debit' THEN p.amount ELSE -p.amount END)
        FROM postings p JOIN journal_entries je ON p.journal_entry_id = je.id
        WHERE je.transaction_date <= ?
        GROUP BY p.account_code
    """, (as_of,)).fetchall()
    return {r[0]: round(r[1] / 100.0, 2) for r in rows if r[1]}


def local_pl_contrib(conn, d1: str, d2: str) -> Dict[str, float]:
    """Local P&L net-income contribution per account over [d1, d2].

    NI contribution = credit - debit = -(debit - credit), uniform across income and
    expense — so it is sign-safe even for contra accounts (e.g. contra-revenue 45xxx).
    """
    rows = conn.execute("""
        SELECT p.account_code,
               SUM(CASE WHEN p.direction = 'debit' THEN p.amount ELSE -p.amount END)
        FROM postings p
        JOIN journal_entries je ON p.journal_entry_id = je.id
        JOIN chart_of_accounts c ON p.account_code = c.code
        WHERE c.type IN ('income', 'expense') AND je.transaction_date BETWEEN ? AND ?
        GROUP BY p.account_code
    """, (d1, d2)).fetchall()
    return {r[0]: round(-r[1] / 100.0, 2) for r in rows}


def find_report_net_income(report: dict) -> float:
    """Return the report's own 'Net Income' total, or NaN if absent (empty window)."""
    out = []

    def walk(rows):
        for row in rows:
            for blob in (row.get('ColData', []), (row.get('Summary') or {}).get('ColData', [])):
                if blob and blob[0].get('value', '').strip() == 'Net Income':
                    try:
                        out.append(float((blob[-1].get('value') or 'nan').replace(',', '')))
                    except ValueError:
                        pass
            nested = row.get('Rows', {})
            if isinstance(nested, dict) and nested.get('Row'):
                walk(nested['Row'])

    walk(report.get('Rows', {}).get('Row', []))
    return out[-1] if out else float('nan')


def qbo_pl_contrib(client, d1, d2, id_to_code, code_to_type):
    """QBO ProfitAndLoss [d1,d2] -> ({code: NI contribution}, unmapped, report_net_income)."""
    rep = client.get_report('ProfitAndLoss', qs={
        'start_date': d1, 'end_date': d2, 'accounting_method': 'Accrual'})
    save_tokens_if_available(client, ENV_PATH)
    out, unmapped = {}, []

    def walk(rows):
        for row in rows:
            cd = row.get('ColData', [])
            if cd and len(cd) >= 2 and cd[0].get('id'):  # leaf account rows carry an id
                qid = cd[0].get('id')
                code = id_to_code.get(str(qid))
                vs = (cd[1].get('value') or '').replace(',', '')
                try:
                    val = float(vs) if vs else 0.0
                except ValueError:
                    val = 0.0
                if code:
                    contrib = val if code_to_type.get(code) == 'income' else -val
                    out[code] = round(out.get(code, 0.0) + contrib, 2)
                else:
                    unmapped.append({'qbo_id': qid, 'name': cd[0].get('value', ''), 'amount': vs})
            nested = row.get('Rows', {})
            if isinstance(nested, dict) and nested.get('Row'):
                walk(nested['Row'])

    walk(rep.get('Rows', {}).get('Row', []))
    return out, unmapped, find_report_net_income(rep)


def diff_accounts(local: Dict[str, float], qbo: Dict[str, float], codes=None) -> List[dict]:
    if codes is None:
        codes = set(local) | set(qbo)
    out = []
    for c in sorted(codes, key=lambda x: (len(str(x)), str(x))):
        lv, qv = local.get(c, 0.0), qbo.get(c, 0.0)
        d = round(lv - qv, 2)
        if abs(d) >= EPS:
            out.append({'account': c, 'local': lv, 'qbo': qv, 'diff': d})
    return out


def validated(local_c, qbo_c, rep_ni, label):
    """Parse guard: the QBO leaf sum must reconstruct the report's own Net Income."""
    lsum = round(sum(local_c.values()), 2)
    qsum = round(sum(qbo_c.values()), 2)
    if rep_ni != rep_ni:  # NaN -> report had no Net Income line (empty window)
        ok, rep_out = abs(qsum) < EPS, 0.0
    else:
        ok, rep_out = abs(qsum - round(rep_ni, 2)) < EPS, round(rep_ni, 2)
    return {'label': label, 'local_ni': lsum, 'qbo_leaf_ni': qsum,
            'qbo_report_ni': rep_out, 'parse_ok': ok}


def main():
    args = parse_args()
    as_of = args.as_of_date
    fy_defaulted = args.fiscal_year_start is None
    fy_month = month_num(args.fiscal_year_start) if args.fiscal_year_start else 1
    cfy_start = current_fy_start(as_of, fy_month)
    prior_end = day_before(cfy_start)

    log = lambda m: print(m, file=sys.stderr)
    log(f"Reconcile as of {as_of}  (fiscal year starts {_MONTHS[fy_month - 1].title()}"
        f"{'  [DEFAULT — pass --fiscal_year_start if this client differs]' if fy_defaulted else ''})")
    log(f"  current fiscal year:        {cfy_start} .. {as_of}")
    log(f"  prior years (swept to RE):  {EARLIEST} .. {prior_end}")

    conn = sqlite3.connect(DB_PATH)
    id_to_code, code_to_type, code_to_name = get_id_maps(conn)

    if args.retained_earnings_code:
        re_codes = [args.retained_earnings_code]
    else:
        re_codes = [c for c, n in code_to_name.items()
                    if code_to_type.get(c) == 'equity' and 'retained earnings' in n.lower()]

    credentials = validate_qbo_env_vars()
    client, error = create_qbo_client(credentials)
    if error:
        print(json.dumps({"success": False, "error": error})); sys.exit(1)
    client, error = refresh_client(client)  # force a fresh token up front (multiple pulls)
    if error:
        print(json.dumps({"success": False, "error": f"token refresh failed: {error}"})); sys.exit(1)
    save_tokens_if_available(client, ENV_PATH)
    ok, msg = test_qbo_connection(client, ENV_PATH)
    if not ok:
        print(json.dumps({"success": False, "error": msg})); sys.exit(1)
    log(msg)

    # --- QBO pulls (1 TrialBalance + 2 ProfitAndLoss) ---
    log(f"Pulling QBO TrialBalance as of {as_of} (balance sheet + equity)...")
    tb = client.get_report('TrialBalance', qs={
        'start_date': EARLIEST, 'end_date': as_of, 'accounting_method': 'Accrual'})
    save_tokens_if_available(client, ENV_PATH)
    qbo_cum = parse_qbo_tb(tb, id_to_code)

    log(f"Pulling QBO ProfitAndLoss prior years {EARLIEST}..{prior_end}...")
    qbo_prior, unmapped_prior, ni_prior = qbo_pl_contrib(client, EARLIEST, prior_end, id_to_code, code_to_type)
    log(f"Pulling QBO ProfitAndLoss current FY {cfy_start}..{as_of}...")
    qbo_curr, unmapped_curr, ni_curr = qbo_pl_contrib(client, cfy_start, as_of, id_to_code, code_to_type)

    # --- Local equivalents ---
    local_cum = get_local_cumulative(conn, as_of)
    local_prior = local_pl_contrib(conn, EARLIEST, prior_end)
    local_curr = local_pl_contrib(conn, cfy_start, as_of)

    period = None
    if args.start_date:
        qbo_period, unmapped_period, ni_period = qbo_pl_contrib(
            client, args.start_date, as_of, id_to_code, code_to_type)
        local_period = local_pl_contrib(conn, args.start_date, as_of)
        period = {'window': [args.start_date, as_of],
                  'variances': diff_accounts(local_period, qbo_period),
                  'validation': validated(local_period, qbo_period, ni_period, 'period')}
    conn.close()

    # --- Checks ---
    pl_types = {'income', 'expense'}
    bs_codes = {c for c in set(local_cum) | set(qbo_cum)
                if code_to_type.get(c) not in pl_types and c not in re_codes}
    bs_variances = diff_accounts(local_cum, qbo_cum, bs_codes)
    equity_codes = {c for c in set(local_cum) | set(qbo_cum)
                    if code_to_type.get(c) == 'equity' and c not in re_codes}
    equity_variances = diff_accounts(local_cum, qbo_cum, equity_codes)
    prior_variances = diff_accounts(local_prior, qbo_prior)
    curr_variances = diff_accounts(local_curr, qbo_curr)

    val_prior = validated(local_prior, qbo_prior, ni_prior, 'prior_years')
    val_curr = validated(local_curr, qbo_curr, ni_curr, 'current_fy')
    parse_failed = not (val_prior['parse_ok'] and val_curr['parse_ok'])

    # Retained-Earnings sweep: RE divergence (qbo - local, signed) must equal -(prior NI)
    expected_sweep = round(sum(qbo_prior.values()), 2)  # QBO prior-years net income
    re_qbo = round(sum(qbo_cum.get(c, 0.0) for c in re_codes), 2)
    re_local = round(sum(local_cum.get(c, 0.0) for c in re_codes), 2)
    re_divergence = round(re_qbo - re_local, 2)
    if abs(expected_sweep) < EPS:
        re_check_ok = abs(re_divergence) < EPS                       # no sweep expected
    else:
        re_check_ok = bool(re_codes) and abs(re_divergence + expected_sweep) < EPS

    unmapped = unmapped_prior + unmapped_curr
    real_problems = bs_variances + equity_variances + prior_variances + curr_variances
    success = bool(not real_problems and re_check_ok and not parse_failed and not unmapped)

    # --- Human-readable report (stderr) ---
    def show(title, vs):
        log(f"\n{title}: {'PASS' if not vs else 'FAIL (' + str(len(vs)) + ')'}")
        for v in vs:
            log(f"    {v['account']:<8} {code_to_name.get(v['account'], '')[:30]:<30}"
                f" local {v['local']:>14,.2f}  qbo {v['qbo']:>14,.2f}  diff {v['diff']:>12,.2f}")

    show("Balance sheet (cumulative as-of)", bs_variances)
    show("Other equity (cumulative as-of)", equity_variances)
    show("Prior-years P&L (per account)", prior_variances)
    show("Current-FY P&L (per account)", curr_variances)
    log(f"\nRetained-Earnings sweep check: {'PASS' if re_check_ok else 'FAIL'}")
    log(f"    RE accounts {re_codes or '(NONE FOUND — pass --retained_earnings_code)'}; "
        f"divergence(qbo-local) {re_divergence:,.2f}; expected -(prior NI) {-expected_sweep:,.2f}")
    log(f"\nParse self-validation: "
        f"{'PASS' if not parse_failed else 'FAIL — leaf sum != report Net Income; compare NOT trusted'}")
    for v in (val_prior, val_curr):
        log(f"    {v['label']:<12} localNI {v['local_ni']:>13,.2f}  qboLeafNI {v['qbo_leaf_ni']:>13,.2f}"
            f"  qboReportNI {v['qbo_report_ni']:>13,.2f}  {'ok' if v['parse_ok'] else '*** MISMATCH ***'}")
    if unmapped:
        log(f"\nUNMAPPED QBO P&L accounts ({len(unmapped)}) — COA out of sync, investigate:")
        for u in unmapped:
            log(f"    qbo_id {u['qbo_id']} {u['name']!r} amount {u['amount']}")
    if period:
        show(f"[secondary] Period activity {period['window'][0]}..{period['window'][1]}", period['variances'])

    log("\n" + "=" * 64)
    log(f"PASS — local DB faithfully published to QBO as of {as_of}" if success
        else "FAIL — variances detected (see above)")

    result = {
        'success': success,
        'as_of_date': as_of,
        'fiscal_year_start': _MONTHS[fy_month - 1].title(),
        'fiscal_year_start_defaulted': fy_defaulted,
        'current_fy': [cfy_start, as_of],
        'prior_years': [EARLIEST, prior_end],
        'checks': {
            'balance_sheet': {'pass': not bs_variances, 'variances': bs_variances},
            'other_equity': {'pass': not equity_variances, 'variances': equity_variances},
            'prior_years_pl': {'pass': not prior_variances, 'variances': prior_variances},
            'current_fy_pl': {'pass': not curr_variances, 'variances': curr_variances},
            'retained_earnings_sweep': {
                'pass': re_check_ok, 're_codes': re_codes, 're_divergence': re_divergence,
                'expected': -expected_sweep, 'prior_years_net_income': expected_sweep,
            },
            'parse_validation': {'pass': not parse_failed, 'prior': val_prior, 'current': val_curr},
            'unmapped_qbo_accounts': unmapped,
        },
        'period_activity': period,
        'summary': ('PASS' if success else 'FAIL') + f" as-of {as_of}",
    }
    print(json.dumps(result, indent=2))
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
