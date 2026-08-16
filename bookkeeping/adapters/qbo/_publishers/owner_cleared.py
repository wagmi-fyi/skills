"""Publish owner-cleared credit_memo / vendor_credit payment-TAPs.

An owner-cleared TAP (no import_id, no source_ta_id, parent type credit_memo
or vendor_credit) records a settlement bucket GL-settled against an
owner-clearing account via a backing clearing JE — owner commingling, no bank
movement. Before this phase existed, no publisher selected them: the TAP sat
pending forever, the clearing JE never published, and the parent CM/VC
floated open in the A/R (A/P) sub-ledger even though the GL was settled —
an AgedReceivableDetail reconciliation gap observed in production.

Model (relay decision A — mirrors the manual remediation pattern, the
`placeholder_cm_to_je` shape):

  1. Publish the backing clearing JE (metadata.clearing_je_id) as a QBO
     JournalEntry — its A/R (A/P) leg becomes a sub-ledger "charge". If
     Phase 1 already published it (it is a standalone pending JE, so the
     std-JE query selects it under --publish_type all/jes), reuse its
     external_id — never publish twice.
  2. Publish a zero-amount Payment (BillPayment for VC parents) linking the
     floating CreditMemo (VendorCredit) to that JE: one LinkedTxn line per
     side at the TAP amount. This nets the credit against the JE's charge in
     the SUB-LEDGER, so the aging is clean — GL-only reconciliation would
     pass the trial balance while AgedReceivableDetail stayed wrong.
  3. Mark the TAP synced with the Payment/BillPayment external_id.

Empirics: QBO accepts Payment.TotalAmt=0 with JournalEntry + CreditMemo
LinkedTxn lines at create (the manual fix posted exactly these);
BillPayment requires CheckPayment.BankAccountRef even at TotalAmt=0 (see
credit_applications.py).
"""

import json
import sqlite3
import sys
from typing import Dict, List, Optional, Tuple

from quickbooks.objects.payment import Payment as QBOPayment
from quickbooks.objects.billpayment import BillPayment, CheckPayment
from quickbooks.objects.base import Ref

from _shared.common import query_owner_cleared_payments, publish_single_qbo_object
from _shared.locate import make_tag
from _shared.sync_status import update_sync_success, update_sync_error
from _publishers.journal_entries import transform_to_qbo_journal_entry, publish_single_entry


def _query_je_postings(conn: sqlite3.Connection, je_id: str) -> List[Dict]:
    """Postings for ONE journal entry, shaped for transform_to_qbo_journal_entry
    (mirrors journal_entries.query_journal_entries minus the bulk filters —
    the caller has already decided THIS JE must publish)."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            je.id as je_id,
            je.transaction_date,
            je.memo,
            COALESCE(
                json_extract(p.metadata, '$.class_name'),
                json_extract(je.metadata, '$.class_name')
            ) as class_name,
            t.remote_id as class_remote_id,
            p.id as posting_id,
            p.account_code,
            p.direction,
            p.amount,
            p.contact,
            p.description,
            coa.remote_id as qbo_account_id,
            coa.meta as account_meta,
            c.remote_id as qbo_contact_id,
            c.meta as contact_meta
        FROM journal_entries je
        INNER JOIN postings p ON je.id = p.journal_entry_id
        INNER JOIN chart_of_accounts coa ON p.account_code = coa.code
        LEFT JOIN contacts c ON p.contact = c.name
        LEFT JOIN tags t ON COALESCE(
            json_extract(p.metadata, '$.class_name'),
            json_extract(je.metadata, '$.class_name')
        ) = t.name AND t.category = 'Class'
        WHERE je.id = ?
        ORDER BY p.id
    """, (je_id,))
    return [dict(row) for row in cursor.fetchall()]


def _ensure_clearing_je_published(client, rate_limiter, conn, je_id: str,
                                  env_path: str) -> Tuple[Optional[str], Optional[str]]:
    """Publish the clearing JE if needed; reuse if Phase 1 already did.
    Returns (je_external_id, error_message)."""
    row = conn.execute(
        "SELECT json_extract(sync, '$.status'), json_extract(sync, '$.external_id') "
        "FROM journal_entries WHERE id = ?", (je_id,)).fetchone()
    if row is None:
        return None, f"clearing JE {je_id} not found"
    status, external_id = row[0], row[1]
    if external_id:
        # Already in QBO (Phase 1, or a prior run of this phase) — reuse.
        return str(external_id), None
    if status not in ('pending', 'error'):
        # 'ignore' would mean some other phase claimed it — refuse to guess.
        return None, f"clearing JE {je_id} has sync status '{status}' and no external_id"

    postings = _query_je_postings(conn, je_id)
    if not postings:
        return None, f"clearing JE {je_id} has no postings"
    qbo_entry, transform_error = transform_to_qbo_journal_entry(je_id, postings)
    if transform_error:
        return None, f"clearing JE {je_id} transform failed: {transform_error}"
    result = publish_single_entry(client, rate_limiter, je_id, qbo_entry, env_path)
    if result['success'] and result['external_id']:
        update_sync_success(conn, 'journal_entries', je_id, result['external_id'])
        return str(result['external_id']), None
    update_sync_error(conn, 'journal_entries', je_id, {
        'error_code': result['error_code'], 'error_message': result['error_message']})
    return None, f"clearing JE {je_id} publish failed: {result['error_message']}"


def publish_owner_cleared(
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
    Publish owner-cleared CM/VC payment-TAPs: clearing JE + zero-amount
    linking Payment/BillPayment (see module docstring).
    Returns (processed, failed, skipped, errors, external_ids).
    """
    rows = query_owner_cleared_payments(conn, sync_status, start_date, end_date)

    processed = 0
    failed = 0
    skipped = 0
    errors: List[Dict] = []
    external_ids: List[str] = []

    default_bank_remote_id = config.get('qbo_default_bank_remote_id')

    for row in rows:
        tap_id = row['tap_id']
        tap_meta = json.loads(row['tap_metadata']) if row.get('tap_metadata') else {}
        clearing_je_id = tap_meta.get('clearing_je_id')

        # Pre-flight: the parent CM/VC must already be in QBO (Phase 2b/2c).
        if not row.get('ta_external_id'):
            print(json.dumps({
                'warning': f"Skipping owner-cleared TAP {tap_id}: parent {row['ta_type']} not synced yet"
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
        if not clearing_je_id:
            errors.append({
                'payment_id': tap_id,
                'error_code': 'OWNER_CLEARING_JE_MISSING',
                'error_message': "TAP metadata has no clearing_je_id — cannot reconcile the sub-ledger"
            })
            update_sync_error(conn, 'trade_account_payments', tap_id, 'OWNER_CLEARING_JE_MISSING')
            failed += 1
            continue

        # Step 1: clearing JE in QBO (publish here, or reuse Phase 1's id).
        je_ext, je_err = _ensure_clearing_je_published(
            client, rate_limiter, conn, clearing_je_id, env_path)
        if not je_ext:
            errors.append({
                'payment_id': tap_id,
                'error_code': 'OWNER_CLEARING_JE_UNAVAILABLE',
                'error_message': je_err
            })
            update_sync_error(conn, 'trade_account_payments', tap_id,
                              f"OWNER_CLEARING_JE_UNAVAILABLE: {je_err}")
            failed += 1
            continue

        # Step 2: zero-amount application linking the floating credit to the
        # JE's sub-ledger charge.
        amount_dollars = round(row['amount'] / 100.0, 2)
        tag = make_tag(tap_id[:8])

        if row['ta_type'] == 'credit_memo':
            obj = QBOPayment()
            obj.TxnDate = row['payment_date']
            obj.TotalAmt = 0
            cref = Ref(); cref.value = row['contact_remote_id']
            obj.CustomerRef = cref
            obj.PrivateNote = f"Owner-cleared application — TAP {tap_id[:8]} {tag}"
            obj.Line = [
                {"Amount": amount_dollars,
                 "LinkedTxn": [{"TxnId": str(je_ext), "TxnType": "JournalEntry"}]},
                {"Amount": amount_dollars,
                 "LinkedTxn": [{"TxnId": str(row['ta_external_id']), "TxnType": "CreditMemo"}]},
            ]
        else:  # vendor_credit
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
            obj = BillPayment()
            obj.TxnDate = row['payment_date']
            obj.TotalAmt = 0
            vref = Ref(); vref.value = row['contact_remote_id']
            obj.VendorRef = vref
            obj.PayType = "Check"
            obj.CheckPayment = CheckPayment()
            bank_ref = Ref(); bank_ref.value = str(default_bank_remote_id)
            obj.CheckPayment.BankAccountRef = bank_ref
            obj.PrivateNote = f"Owner-cleared application — TAP {tap_id[:8]} {tag}"
            obj.Line = [
                {"Amount": amount_dollars,
                 "LinkedTxn": [{"TxnId": str(je_ext), "TxnType": "JournalEntry"}]},
                {"Amount": amount_dollars,
                 "LinkedTxn": [{"TxnId": str(row['ta_external_id']), "TxnType": "VendorCredit"}]},
            ]

        entity = 'Payment' if row['ta_type'] == 'credit_memo' else 'BillPayment'
        obj._bk_locator = {'entity': entity, 'tag': tag,
                           'txn_date': row['payment_date'], 'total': 0}

        ext_id, error = publish_single_qbo_object(client, rate_limiter, obj, env_path)
        if ext_id:
            update_sync_success(conn, 'trade_account_payments', tap_id, ext_id)
            external_ids.append(ext_id)
            processed += 1
            # Per-row commit: the JE and the application for THIS TAP land
            # together, shrinking the crash window between a QBO create and
            # its local external_id to microseconds (a lost id = double-post
            # setup on the next run).
            conn.commit()
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
