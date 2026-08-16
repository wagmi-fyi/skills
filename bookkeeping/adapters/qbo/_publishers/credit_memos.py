"""Publish standalone credit_memo trade accounts as QBO CreditMemo objects.

Each CM publishes with its line items but no LinkedTxn — i.e., as a standalone
open-balance credit document. Applications to invoices are published separately
by credit_applications.py as zero-amount Payment objects (Pattern B).

Note: ARAccountRef is intentionally NOT set on CreditMemo. QBO Online routes
to the customer's default A/R regardless of any document-level ARAccountRef —
the API accepts the field but silently discards it (same behavior as Invoices).
For clients with multiple A/R accounts the supported pattern is to consolidate
at the QBO level, not to override per-document.
"""

import json
import sqlite3
from typing import Dict, List, Optional, Tuple

from quickbooks.objects.creditmemo import CreditMemo
from quickbooks.objects.base import Ref

from _shared.common import (
    query_trade_accounts, group_postings_by_ta, publish_single_qbo_object
)
from _shared.locate import make_tag
from _shared.sync_status import update_sync_success, update_sync_error


def publish_credit_memos(
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
    Publish credit_memo trade accounts as standalone QBO CreditMemo objects.
    Returns (processed, failed, skipped, errors, external_ids).
    """
    postings = query_trade_accounts(conn, sync_status, start_date, end_date, ta_type='credit_memo')
    grouped = group_postings_by_ta(postings)

    processed = 0
    failed = 0
    skipped = 0
    errors: List[Dict] = []
    external_ids: List[str] = []

    for ta_id, ta_postings in grouped.items():
        first = ta_postings[0]

        if not first.get('contact_remote_id'):
            errors.append({
                'trade_account_id': ta_id,
                'error_code': 'CONTACT_REF_MISSING',
                'error_message': f"Contact '{first['ta_contact']}' has no remote_id"
            })
            update_sync_error(conn, 'trade_accounts', ta_id, 'CONTACT_REF_MISSING')
            skipped += 1
            continue

        ta_meta = json.loads(first['ta_metadata']) if first.get('ta_metadata') else {}
        balance_account_code = ta_meta.get('balance_account_code')

        cm = CreditMemo()
        cm.TxnDate = first['document_date']
        tag = make_tag(ta_id[:8])
        if ta_meta.get('document_number'):
            cm.DocNumber = ta_meta['document_number']
        else:
            # No adapter-provided number: stamp the idempotency tag — QBO's
            # duplicate-DocNumber enforcement (6140) hard-blocks a double-post
            # and the blocked retry self-heals via the tag locate.
            cm.DocNumber = tag

        customer_ref = Ref()
        customer_ref.value = first['contact_remote_id']
        cm.CustomerRef = customer_ref

        # Idempotency tag + locator for the post-then-fail read-back (see
        # _shared/locate.py). _bk_locator never serializes to QBO.
        cm.PrivateNote = f"{first['memo']} {tag}" if first.get('memo') else tag
        cm._bk_locator = {'entity': 'CreditMemo', 'tag': tag,
                          'txn_date': first['document_date'],
                          'doc_number': cm.DocNumber}

        # Build lines as SalesItemLineDetail with per-account Item routing.
        # CM polarity (mirror of invoices.py): DR-direction posting → positive Line.Amount,
        # CR-direction posting → negative. Sum of Line.Amounts equals the balance leg, so
        # CM TotalAmt = JE's net A/R reduction. Mixed-direction operational postings
        # (refunds DR + reimbursements CR on the same JE) round-trip correctly.
        invoice_item_map = config.get('qbo_invoice_item_map', {})
        cm.Line = []
        broke = False
        for p in ta_postings:
            if balance_account_code and p['account_code'] == balance_account_code:
                continue

            raw_amount = round(p['amount'] / 100.0, 2)
            amount = raw_amount if p['direction'] == 'debit' else -raw_amount
            item_id = invoice_item_map.get(str(p['qbo_account_id']))
            if not item_id:
                default_item = config.get('qbo_default_invoice_item')
                if default_item:
                    item_id = str(default_item)
                else:
                    error_msg = (
                        f"No QBO Item mapping for account {p['account_code']} "
                        f"(remote_id={p['qbo_account_id']}). "
                        f"Add it to qbo_invoice_item_map in config.yaml."
                    )
                    errors.append({
                        'trade_account_id': ta_id,
                        'error_code': 'ITEM_MAPPING_MISSING',
                        'error_message': error_msg
                    })
                    update_sync_error(conn, 'trade_accounts', ta_id, 'ITEM_MAPPING_MISSING')
                    failed += 1
                    broke = True
                    break

            line_detail = {
                "ItemRef": {"value": item_id},
                "Qty": 1,
                "UnitPrice": amount,
            }
            if p.get('class_remote_id'):
                line_detail["ClassRef"] = {"value": p['class_remote_id']}

            line = {
                "Amount": amount,
                "DetailType": "SalesItemLineDetail",
                "SalesItemLineDetail": line_detail,
            }
            if p.get('description'):
                line["Description"] = p['description']
            cm.Line.append(line)

        if broke:
            continue

        ext_id, error = publish_single_qbo_object(client, rate_limiter, cm, env_path)
        if ext_id:
            update_sync_success(conn, 'trade_accounts', ta_id, ext_id)
            # Mark the originating JE as synced too — but only if it's currently pending.
            # If marked 'ignore' at creation (e.g. local-only CM for a rectification follow-up),
            # leave it alone; the JE has no local GL impact and shouldn't get a remote id.
            row = conn.execute(
                "SELECT json_extract(sync, '$.status') FROM journal_entries WHERE id = ?",
                (first['journal_entry_id'],)
            ).fetchone()
            if row and row[0] == 'pending':
                update_sync_success(conn, 'journal_entries', first['journal_entry_id'], ext_id)
            external_ids.append(ext_id)
            processed += 1
        else:
            errors.append({
                'trade_account_id': ta_id,
                'error_code': 'API_ERROR',
                'error_message': error
            })
            update_sync_error(conn, 'trade_accounts', ta_id, error)
            failed += 1

    conn.commit()
    return processed, failed, skipped, errors, external_ids
