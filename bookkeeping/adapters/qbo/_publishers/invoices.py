"""Publish receivable trade accounts as QBO Invoices."""

import json
import sqlite3
from typing import Dict, List, Optional, Tuple

from quickbooks.objects.invoice import Invoice
from quickbooks.objects.base import Ref

from _shared.common import (
    query_trade_accounts, group_postings_by_ta, publish_single_qbo_object
)
from _shared.locate import make_tag
from _shared.sync_status import update_sync_success, update_sync_error


def publish_invoices(
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
    Publish receivable trade accounts as QBO Invoices.
    Returns (processed, failed, skipped, errors, external_ids).
    """
    postings = query_trade_accounts(conn, sync_status, start_date, end_date, ta_type='receivable')
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

        invoice = Invoice()
        invoice.TxnDate = first['document_date']

        tag = make_tag(ta_id[:8])
        if ta_meta.get('doc_number'):
            invoice.DocNumber = ta_meta['doc_number']
        else:
            # No adapter-provided number: stamp the idempotency tag so QBO's
            # duplicate-DocNumber enforcement (6140 — verified live 2026-06-10)
            # hard-blocks a double-post even when the immediate locate misses,
            # and the blocked retry self-heals by locating OUR object by tag.
            invoice.DocNumber = tag

        # Map TA metadata to QBO custom fields via config
        custom_field_map = config.get('qbo_custom_field_map', {})
        for meta_key, field_def in custom_field_map.items():
            meta_value = ta_meta.get(meta_key, '')
            if meta_value:
                from quickbooks.objects.invoice import CustomField
                cf = CustomField()
                cf.DefinitionId = str(field_def['definition_id'])
                cf.Name = field_def.get('name', '')
                cf.Type = field_def.get('type', 'StringType')
                cf.StringValue = str(meta_value)
                invoice.CustomField.append(cf)
        if first.get('due_date'):
            invoice.DueDate = first['due_date']

        customer_ref = Ref()
        customer_ref.value = first['contact_remote_id']
        invoice.CustomerRef = customer_ref

        # Idempotency tag + locator for the post-then-fail read-back (see
        # _shared/locate.py). _bk_locator never serializes to QBO.
        invoice.PrivateNote = f"{first['memo']} {tag}" if first.get('memo') else tag
        invoice._bk_locator = {'entity': 'Invoice', 'tag': tag,
                               'txn_date': first['document_date'],
                               'doc_number': invoice.DocNumber}

        # Build lines as SalesItemLineDetail with per-account Item routing
        invoice_item_map = config.get('qbo_invoice_item_map', {})
        invoice.Line = []
        for p in ta_postings:
            if balance_account_code and p['account_code'] == balance_account_code:
                continue

            raw_amount = round(p['amount'] / 100.0, 2)
            amount = raw_amount if p['direction'] == 'credit' else -raw_amount
            item_id = invoice_item_map.get(str(p['qbo_account_id']))
            if not item_id:
                default_item = config.get('qbo_default_invoice_item')
                if default_item:
                    item_id = str(default_item)
                else:
                    error_msg = (
                        f"No QBO Item mapping for account {p['account_code']} (remote_id={p['qbo_account_id']}). "
                        f"Add it to qbo_invoice_item_map in config.yaml."
                    )
                    errors.append({
                        'trade_account_id': ta_id,
                        'error_code': 'ITEM_MAPPING_MISSING',
                        'error_message': error_msg
                    })
                    update_sync_error(conn, 'trade_accounts', ta_id, 'ITEM_MAPPING_MISSING')
                    failed += 1
                    break  # skip remaining postings for this TA
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

            invoice.Line.append(line)
        else:
            # for-loop completed without break — all postings valid, publish
            ext_id, error = publish_single_qbo_object(client, rate_limiter, invoice, env_path)
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
