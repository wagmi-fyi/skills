"""Publish credit applications as zero-amount QBO Payment / BillPayment objects.

Each TAP with source_ta_id set represents a CM applied to a receivable invoice
or a VC applied to a payable bill. Pattern B: each application becomes a separate
QBO Payment (TotalAmt=0) or BillPayment (TotalAmt=0) with two LinkedTxn entries
(one for the source CM/VC, one for the target invoice/bill). No cash moves; the
credit is applied via the LinkedTxn linking.

Empirically verified in sandbox: QBO accepts Payment.TotalAmt=0 + Line[] with
LinkedTxn at CREATE time (no two-step update needed). BillPayment requires
CheckPayment.BankAccountRef even at TotalAmt=0.
"""

import json
import sqlite3
import sys
from typing import Dict, List, Optional, Tuple

from quickbooks.objects.payment import Payment as QBOPayment
from quickbooks.objects.billpayment import BillPayment, CheckPayment
from quickbooks.objects.base import Ref

from _shared.common import query_credit_applications, publish_single_qbo_object
from _shared.locate import make_tag
from _shared.sync_status import update_sync_success, update_sync_error


def publish_credit_applications(
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
    Publish credit applications (TAPs with source_ta_id set) as zero-amount
    QBO Payment / BillPayment objects.
    Returns (processed, failed, skipped, errors, external_ids).
    """
    rows = query_credit_applications(conn, sync_status, start_date, end_date)

    processed = 0
    failed = 0
    skipped = 0
    errors: List[Dict] = []
    external_ids: List[str] = []

    # Default bank account for BillPayment.CheckPayment.BankAccountRef.
    # QBO requires a BankAccountRef even when TotalAmt=0 (no cash actually moves).
    default_bank_remote_id = config.get('qbo_default_bank_remote_id')

    for row in rows:
        tap_id = row['tap_id']

        # Pre-flight: both source CM/VC and target invoice/bill must be synced.
        if not row.get('source_external_id'):
            print(json.dumps({
                'warning': f"Skipping credit application {tap_id}: source CM/VC not synced yet"
            }), file=sys.stderr)
            skipped += 1
            continue
        if not row.get('target_external_id'):
            print(json.dumps({
                'warning': f"Skipping credit application {tap_id}: target invoice/bill not synced yet"
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

        amount_dollars = round(row['amount'] / 100.0, 2)
        source_type = row['source_type']

        if source_type == 'credit_memo':
            # CM applied to Invoice → zero-amount Payment with two LinkedTxns
            pmt = QBOPayment()
            pmt.TxnDate = row['payment_date']
            pmt.TotalAmt = 0
            customer_ref = Ref()
            customer_ref.value = row['contact_remote_id']
            pmt.CustomerRef = customer_ref
            # The [bk:] token (see _shared/locate.py) makes the zero-$ Payment
            # locatable after a post-then-fail fault — TotalAmt=0 alone would
            # match every zero-dollar payment that day.
            pmt.PrivateNote = f"Credit application — TAP {tap_id[:8]} {make_tag(tap_id[:8])}"
            pmt._bk_locator = {'entity': 'Payment', 'tag': make_tag(tap_id[:8]),
                               'txn_date': row['payment_date'], 'total': 0}
            pmt.Line = [
                {
                    "Amount": amount_dollars,
                    "LinkedTxn": [{"TxnId": str(row['target_external_id']), "TxnType": "Invoice"}]
                },
                {
                    "Amount": amount_dollars,
                    "LinkedTxn": [{"TxnId": str(row['source_external_id']), "TxnType": "CreditMemo"}]
                }
            ]
            ext_id, error = publish_single_qbo_object(client, rate_limiter, pmt, env_path)

        elif source_type == 'vendor_credit':
            # VC applied to Bill → zero-amount BillPayment with two LinkedTxns
            if not default_bank_remote_id:
                errors.append({
                    'payment_id': tap_id,
                    'error_code': 'BANK_ACCOUNT_MISSING',
                    'error_message': "qbo_default_bank_remote_id missing in config — "
                                     "required for BillPayment.CheckPayment.BankAccountRef"
                                     " (even at TotalAmt=0)"
                })
                update_sync_error(conn, 'trade_account_payments', tap_id, 'BANK_ACCOUNT_MISSING')
                failed += 1
                continue

            bp = BillPayment()
            bp.TxnDate = row['payment_date']
            bp.TotalAmt = 0
            vendor_ref = Ref()
            vendor_ref.value = row['contact_remote_id']
            bp.VendorRef = vendor_ref
            bp.PayType = "Check"
            bp.CheckPayment = CheckPayment()
            bank_ref = Ref()
            bank_ref.value = str(default_bank_remote_id)
            bp.CheckPayment.BankAccountRef = bank_ref
            bp.PrivateNote = f"Credit application — TAP {tap_id[:8]} {make_tag(tap_id[:8])}"
            bp._bk_locator = {'entity': 'BillPayment', 'tag': make_tag(tap_id[:8]),
                              'txn_date': row['payment_date'], 'total': 0}
            bp.Line = [
                {
                    "Amount": amount_dollars,
                    "LinkedTxn": [{"TxnId": str(row['target_external_id']), "TxnType": "Bill"}]
                },
                {
                    "Amount": amount_dollars,
                    "LinkedTxn": [{"TxnId": str(row['source_external_id']), "TxnType": "VendorCredit"}]
                }
            ]
            ext_id, error = publish_single_qbo_object(client, rate_limiter, bp, env_path)

        else:
            errors.append({
                'payment_id': tap_id,
                'error_code': 'INVALID_SOURCE_TYPE',
                'error_message': f"source TA type='{source_type}' is not credit_memo or vendor_credit"
            })
            update_sync_error(conn, 'trade_account_payments', tap_id, 'INVALID_SOURCE_TYPE')
            failed += 1
            continue

        if ext_id:
            update_sync_success(conn, 'trade_account_payments', tap_id, ext_id)
            external_ids.append(ext_id)
            processed += 1
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
