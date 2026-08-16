"""Publish payable trade accounts as QBO Bills."""

import json
import sqlite3
from typing import Dict, List, Optional, Tuple

from quickbooks.objects.bill import Bill
from quickbooks.objects.base import Ref

from _shared.common import (
    query_trade_accounts, group_postings_by_ta, publish_single_qbo_object
)
from _shared.locate import make_tag
from _shared.sync_status import update_sync_success, update_sync_error


def publish_bills(
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
    Publish payable trade accounts as QBO Bills.
    Returns (processed, failed, skipped, errors, external_ids).
    """
    postings = query_trade_accounts(conn, sync_status, start_date, end_date, ta_type='payable')
    grouped = group_postings_by_ta(postings)

    processed = 0
    failed = 0
    skipped = 0
    errors = []
    external_ids = []

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

        bill = Bill()
        bill.TxnDate = first['document_date']
        tag = make_tag(ta_id[:8])
        if ta_meta.get('doc_number'):
            bill.DocNumber = ta_meta['doc_number']
        else:
            # No adapter-provided number: stamp the idempotency tag — QBO's
            # duplicate-DocNumber enforcement (6140) hard-blocks a double-post
            # and the blocked retry self-heals via the tag locate.
            bill.DocNumber = tag
        if first.get('due_date'):
            bill.DueDate = first['due_date']

        vendor_ref = Ref()
        vendor_ref.value = first['contact_remote_id']
        bill.VendorRef = vendor_ref

        # Idempotency tag + locator for the post-then-fail read-back (see
        # _shared/locate.py). _bk_locator never serializes to QBO.
        bill.PrivateNote = f"{first['memo']} {tag}" if first.get('memo') else tag
        bill._bk_locator = {'entity': 'Bill', 'tag': tag,
                            'txn_date': first['document_date'],
                            'doc_number': bill.DocNumber}

        # Direction: debit = positive (expense), credit = negative (revenue offset)
        bill.Line = []
        for p in ta_postings:
            if balance_account_code and p['account_code'] == balance_account_code:
                continue

            raw_amount = round(p['amount'] / 100.0, 2)
            amount = raw_amount if p['direction'] == 'debit' else -raw_amount

            line_detail = {"AccountRef": {"value": p['qbo_account_id']}}
            if p.get('class_remote_id'):
                line_detail["ClassRef"] = {"value": p['class_remote_id']}

            line = {
                "Amount": amount,
                "DetailType": "AccountBasedExpenseLineDetail",
                "AccountBasedExpenseLineDetail": line_detail,
            }
            if p.get('description'):
                line["Description"] = p['description']

            bill.Line.append(line)

        ext_id, error = publish_single_qbo_object(client, rate_limiter, bill, env_path)
        if ext_id:
            update_sync_success(conn, 'trade_accounts', ta_id, ext_id)
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
