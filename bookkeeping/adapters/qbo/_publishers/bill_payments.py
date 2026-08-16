"""
Publish payable payments as QBO BillPayment objects.

Two paths: consolidated settlement groups (one BillPayment with N Bill + M
VendorCredit LinkedTxn lines, grouped by metadata.settlement_id) and singleton
per-row BillPayments. Settlement-reducing credits arrive as first-class
vendor_credit TAs in the batch (see vendor_credits.py / credit_applications.py);
this module never synthesizes Vendor Credits from clearing-JE postings.

The consolidated BillPayment is built SINGLE-STEP with its full mixed Line[].
QBO rejects a BillPayment created with an empty Line[] (ValidationException 2020:
"Required parameter Line is missing"), so the two-step create-then-add-lines
pattern used by payments.py for the receivable Payment is NOT available here —
this was confirmed against the production realm. The single-step shape mirrors
the per-row path below and credit_applications.py's VC→Bill BillPayment.
"""

import json
import sqlite3
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from quickbooks.objects.billpayment import BillPayment, CheckPayment
from quickbooks.objects.base import Ref

from _shared.common import (
    query_trade_account_payments, publish_single_qbo_object,
    query_settlement_vendor_credit_apps
)
from _shared.locate import make_tag
from _shared.sync_status import update_sync_success, update_sync_error, update_sync_ignore


def publish_bill_payments(
    client,
    rate_limiter,
    conn: sqlite3.Connection,
    config: Dict,
    sync_status: str,
    start_date: Optional[str],
    end_date: Optional[str],
    env_path: str
) -> Tuple[int, int, int, List[Dict], List[str]]:
    """
    Publish payable payments as QBO BillPayment objects.
    Returns (processed, failed, skipped, errors, external_ids).
    """
    rows = query_trade_account_payments(conn, sync_status, start_date, end_date, ta_type='payable')

    processed = 0
    failed = 0
    skipped = 0
    errors = []
    external_ids = []

    # Group bank-funded P-TAPs by metadata.settlement_id so a settlement
    # disbursement publishes as ONE BillPayment with all Bill (and optional
    # VendorCredit) LinkedTxn lines. Singletons flow through the per-row path.
    settlement_groups = {}  # settlement_id -> list of bank-funded P-TAP rows
    singleton_rows = []
    for row in rows:
        tap_meta = json.loads(row['tap_metadata']) if row.get('tap_metadata') else {}
        sid = tap_meta.get('settlement_id')
        if sid:
            settlement_groups.setdefault(sid, []).append(row)
        else:
            singleton_rows.append(row)

    def _meta(row):
        return json.loads(row['tap_metadata']) if row.get('tap_metadata') else {}

    # ---- Consolidated settlement BillPayments ----
    for sid, group_rows in settlement_groups.items():
        vc_taps = query_settlement_vendor_credit_apps(conn, sid)

        # Pre-flight: detect partially-published settlement — A/P side only.
        # A four-type settlement shares one settlement_id across the Payment (R/CM)
        # and this BillPayment (P/VC), which publish independently, so restrict the
        # already-synced count to payable-side TAPs (bank-funded P + VC credit-apps).
        already_synced = conn.execute("""
            SELECT COUNT(*) FROM trade_account_payments tap
            LEFT JOIN trade_accounts ta ON tap.trade_account_id = ta.id
            LEFT JOIN trade_accounts src ON tap.source_ta_id = src.id
            WHERE json_extract(tap.metadata, '$.settlement_id') = ?
              AND json_extract(tap.sync, '$.external_id') IS NOT NULL
              AND (
                    (tap.source_ta_id IS NULL AND ta.type = 'payable')
                 OR (src.type = 'vendor_credit')
              )
        """, (sid,)).fetchone()[0]
        if already_synced > 0:
            err_msg = (f'Settlement {sid}: {already_synced} payable-side TAP(s) already synced; '
                       f'cannot consolidate remainder. Manual reconciliation required.')
            for r in group_rows:
                errors.append({'payment_id': r['tap_id'],
                               'error_code': 'SETTLEMENT_PARTIALLY_PUBLISHED',
                               'error_message': err_msg})
                update_sync_error(conn, 'trade_account_payments', r['tap_id'],
                                  'SETTLEMENT_PARTIALLY_PUBLISHED')
                skipped += 1
            continue

        # Pre-flight: uniformity across the group. The consolidated BillPayment uses
        # first_row for bank, vendor, and date — refuse a heterogeneous group.
        banks = {r['payment_account_remote_id'] for r in group_rows if r.get('payment_account_remote_id')}
        vendors = {r['contact_remote_id'] for r in group_rows if r.get('contact_remote_id')}
        dates = {r['payment_date'] for r in group_rows}
        if len(banks) > 1 or len(vendors) > 1 or len(dates) > 1:
            err_msg = (f'Settlement {sid}: group has {len(banks)} bank(s), '
                       f'{len(vendors)} vendor(s), {len(dates)} date(s) — '
                       f'refusing to consolidate.')
            for r in group_rows:
                errors.append({'payment_id': r['tap_id'],
                               'error_code': 'SETTLEMENT_GROUP_HETEROGENEOUS',
                               'error_message': err_msg})
                update_sync_error(conn, 'trade_account_payments', r['tap_id'],
                                  'SETTLEMENT_GROUP_HETEROGENEOUS')
                skipped += 1
            continue

        # Pre-flight: every target Bill and every source VC must have external_ids.
        #   BILL_NOT_SYNCED       — bank-funded P-TAP whose Bill isn't synced yet
        #   SOURCE_VC_NOT_SYNCED  — credit-app TAP whose source VendorCredit isn't synced
        #   TARGET_BILL_NOT_SYNCED— credit-app TAP whose target Bill isn't synced
        any_missing = False
        for r in group_rows:
            if not r.get('ta_external_id'):
                errors.append({'payment_id': r['tap_id'], 'error_code': 'BILL_NOT_SYNCED',
                               'error_message': f'Settlement {sid}: P-TA (Bill) not yet published'})
                skipped += 1
                any_missing = True
        for c in vc_taps:
            if not c.get('source_external_id'):
                errors.append({'payment_id': c['tap_id'], 'error_code': 'SOURCE_VC_NOT_SYNCED',
                               'error_message': f'Settlement {sid}: source VendorCredit not yet published'})
                skipped += 1
                any_missing = True
            if not c.get('target_ta_external_id'):
                errors.append({'payment_id': c['tap_id'], 'error_code': 'TARGET_BILL_NOT_SYNCED',
                               'error_message': f'Settlement {sid}: credit-app target Bill not yet published'})
                skipped += 1
                any_missing = True
        if any_missing:
            continue

        first_row = group_rows[0]
        first_meta = _meta(first_row)
        # Cash disbursement = sum of bank-funded P-TAPs (group_rows are source_ta_id IS NULL).
        billpayment_cash_cents = sum(r['amount'] for r in group_rows)
        sum_vc_cents = sum(c['amount'] for c in vc_taps)

        # Aggregate Lines per QBO TxnId: each Bill once at face (its P-TAP cash + the
        # VC-app amounts targeting it), each VendorCredit once at its applied total.
        bill_amounts = defaultdict(int)   # target Bill external_id -> cents
        for r in group_rows:
            bill_amounts[r['ta_external_id']] += r['amount']
        for c in vc_taps:
            bill_amounts[c['target_ta_external_id']] += c['amount']
        vc_amounts = defaultdict(int)     # source VC external_id -> cents
        for c in vc_taps:
            vc_amounts[c['source_external_id']] += c['amount']

        sum_bill_cents = sum(bill_amounts.values())

        # Pre-flight invariant: Σ Bill == billpayment_cash + Σ VC.
        # QBO is an external boundary; bad arithmetic here = a malformed BillPayment.
        # Fail loud before save().
        if sum_bill_cents != billpayment_cash_cents + sum_vc_cents:
            err_msg = (f'Settlement {sid}: Σ Bill ({sum_bill_cents}) != '
                       f'billpayment_cash ({billpayment_cash_cents}) + Σ VC ({sum_vc_cents})')
            for r in group_rows:
                errors.append({'payment_id': r['tap_id'], 'error_code': 'LINE_SUM_MISMATCH',
                               'error_message': err_msg})
                update_sync_error(conn, 'trade_account_payments', r['tap_id'], 'LINE_SUM_MISMATCH')
            for c in vc_taps:
                errors.append({'payment_id': c['tap_id'], 'error_code': 'LINE_SUM_MISMATCH',
                               'error_message': err_msg})
                update_sync_error(conn, 'trade_account_payments', c['tap_id'], 'LINE_SUM_MISMATCH')
            failed += len(group_rows) + len(vc_taps)
            continue

        if billpayment_cash_cents <= 0:
            errors.append({'payment_id': first_row['tap_id'], 'error_code': 'SETTLEMENT_NEGATIVE_NET',
                           'error_message': f'Settlement {sid}: billpayment_cash_cents={billpayment_cash_cents} not > 0'})
            failed += 1
            continue

        if not first_row.get('contact_remote_id'):
            errors.append({'payment_id': first_row['tap_id'], 'error_code': 'CONTACT_REF_MISSING',
                           'error_message': f"Contact '{first_row['ta_contact']}' has no remote_id"})
            skipped += 1
            continue

        # Build the consolidated BillPayment (single-step: full mixed Line[] at create).
        bp = BillPayment()
        bp.TotalAmt = round(billpayment_cash_cents / 100.0, 2)
        bp.TxnDate = first_row['payment_date']
        bp.PayType = 'Check'
        vref = Ref(); vref.value = first_row['contact_remote_id']
        bp.VendorRef = vref

        check_payment = CheckPayment()
        if first_row.get('payment_account_remote_id'):
            bank_ref = Ref(); bank_ref.value = first_row['payment_account_remote_id']
            check_payment.BankAccountRef = bank_ref
        elif first_meta.get('payment_account_code'):
            errors.append({'payment_id': first_row['tap_id'], 'error_code': 'BANK_ACCOUNT_MISSING',
                           'error_message': f"payment_account_code '{first_meta['payment_account_code']}' not found"})
            failed += 1
            continue
        bp.CheckPayment = check_payment

        lines = []
        for ext, amt in bill_amounts.items():
            lines.append({"Amount": round(amt / 100.0, 2),
                          "LinkedTxn": [{"TxnId": str(ext), "TxnType": "Bill"}]})
        for ext, amt in vc_amounts.items():
            lines.append({"Amount": round(amt / 100.0, 2),
                          "LinkedTxn": [{"TxnId": str(ext), "TxnType": "VendorCredit"}]})
        bp.Line = lines

        n_bills = len(bill_amounts)
        n_vcs = len(vc_amounts)
        # Idempotency tag + locator for the post-then-fail read-back (see
        # _shared/locate.py). _bk_locator never serializes to QBO.
        bp.PrivateNote = (f"Settlement {sid} — {n_bills} bills + "
                          f"{n_vcs} vendor credits {make_tag(sid)}")
        bp._bk_locator = {'entity': 'BillPayment', 'tag': make_tag(sid),
                          'txn_date': first_row['payment_date'],
                          'total': round(billpayment_cash_cents / 100.0, 2)}

        ext_id, error = publish_single_qbo_object(client, rate_limiter, bp, env_path)
        if not ext_id:
            for r in group_rows:
                errors.append({'payment_id': r['tap_id'], 'error_code': 'API_ERROR', 'error_message': error})
                update_sync_error(conn, 'trade_account_payments', r['tap_id'], error)
                failed += 1
            for c in vc_taps:
                update_sync_error(conn, 'trade_account_payments', c['tap_id'], error)
                failed += 1
            continue

        # Mark all group P-TAPs + VC-app TAPs synced to the same external BillPayment id.
        for r in group_rows:
            update_sync_success(conn, 'trade_account_payments', r['tap_id'], ext_id)
        for c in vc_taps:
            update_sync_success(conn, 'trade_account_payments', c['tap_id'], ext_id)
        external_ids.append(ext_id)
        processed += len(group_rows) + len(vc_taps)

        clearing_je_ids = {_meta(r).get('clearing_je_id') for r in group_rows}
        clearing_je_ids |= {_meta(c).get('clearing_je_id') for c in vc_taps}
        for cje in clearing_je_ids:
            if cje:
                update_sync_ignore(conn, 'journal_entries', cje)

    # ---- Per-row singleton path (existing behavior, payable TAPs with no settlement_id) ----
    for row in singleton_rows:
        tap_id = row['tap_id']

        if not row.get('ta_external_id'):
            print(json.dumps({
                'warning': f"Skipping bill payment {tap_id}: parent trade account not synced yet"
            }), file=sys.stderr)
            skipped += 1
            continue

        if not row.get('contact_remote_id'):
            errors.append({
                'payment_id': tap_id,
                'error_code': 'CONTACT_REF_MISSING',
                'error_message': f"Contact '{row['ta_contact']}' has no remote_id"
            })
            update_sync_error(conn, 'trade_account_payments', tap_id, 'CONTACT_REF_MISSING')
            skipped += 1
            continue

        bill_payment = BillPayment()
        bill_payment.TotalAmt = round(row['amount'] / 100.0, 2)
        bill_payment.TxnDate = row['payment_date']
        bill_payment.PayType = 'Check'

        vendor_ref = Ref()
        vendor_ref.value = row['contact_remote_id']
        bill_payment.VendorRef = vendor_ref

        # CheckPayment detail with BankAccountRef — fail if payment_account_code can't be resolved
        tap_meta_check = json.loads(row['tap_metadata']) if row.get('tap_metadata') else {}
        pmt_acct_code = tap_meta_check.get('payment_account_code')
        check_payment = CheckPayment()
        if row.get('payment_account_remote_id'):
            bank_ref = Ref()
            bank_ref.value = row['payment_account_remote_id']
            check_payment.BankAccountRef = bank_ref
        elif pmt_acct_code:
            errors.append({
                'payment_id': tap_id,
                'error_code': 'BANK_ACCOUNT_MISSING',
                'error_message': f"payment_account_code '{pmt_acct_code}' not found in chart_of_accounts"
            })
            update_sync_error(conn, 'trade_account_payments', tap_id, 'BANK_ACCOUNT_MISSING')
            failed += 1
            continue
        bill_payment.CheckPayment = check_payment

        bill_payment.Line = [{
            "Amount": round(row['amount'] / 100.0, 2),
            "LinkedTxn": [{"TxnId": row['ta_external_id'], "TxnType": "Bill"}]
        }]
        # Idempotency tag + locator (see _shared/locate.py). Singletons
        # previously carried no PrivateNote — un-locatable after a fault.
        bill_payment.PrivateNote = make_tag(tap_id[:8])
        bill_payment._bk_locator = {'entity': 'BillPayment', 'tag': make_tag(tap_id[:8]),
                                    'txn_date': row['payment_date'],
                                    'total': round(row['amount'] / 100.0, 2)}

        ext_id, error = publish_single_qbo_object(client, rate_limiter, bill_payment, env_path)
        if ext_id:
            update_sync_success(conn, 'trade_account_payments', tap_id, ext_id)
            external_ids.append(ext_id)
            processed += 1

            tap_meta = json.loads(row['tap_metadata']) if row.get('tap_metadata') else {}
            clearing_je_id = tap_meta.get('clearing_je_id')
            if clearing_je_id:
                update_sync_ignore(conn, 'journal_entries', clearing_je_id)
        else:
            errors.append({
                'payment_id': tap_id,
                'error_code': 'API_ERROR',
                'error_message': error
            })
            update_sync_error(conn, 'trade_account_payments', tap_id, error)
            failed += 1

    conn.commit()
    return processed, failed, skipped, errors, external_ids
