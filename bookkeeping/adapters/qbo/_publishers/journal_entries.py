"""
Publish Journal Entries to QBO.

Query, transform, validate, and batch-publish journal entries.
"""

import json
import sqlite3
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from quickbooks.objects.journalentry import JournalEntry, JournalEntryLine, JournalEntryLineDetail, Entity
from quickbooks.objects.base import Ref

from _shared.auth import (
    resolve_client, maybe_proactive_refresh, try_reactive_refresh,
    is_auth_fault, auth_dead_error
)
from _shared.client import save_tokens_if_available, MAX_RETRIES
from _shared.common import get_entity_type
from _shared.locate import (
    make_tag, is_post_then_fail, locate_posted_object, FOUND, AMBIGUOUS, INCONCLUSIVE
)
from _shared.sync_status import update_sync_success, update_sync_error

BATCH_CHUNK_SIZE = 25


# =============================================================================
# Query & Grouping
# =============================================================================

def query_journal_entries(
    conn: sqlite3.Connection,
    sync_status: str,
    start_date: Optional[str],
    end_date: Optional[str]
) -> List[Dict]:
    """
    Query journal entries with postings, joined to chart_of_accounts and contacts.
    Excludes entries that already have external_id (idempotency) and JEs backing trade accounts.
    """
    cursor = conn.cursor()

    where_conditions = [
        "json_extract(je.sync, '$.status') = ?",
        "json_extract(je.sync, '$.external_id') IS NULL",
        "ta_check.id IS NULL"
    ]
    params = [sync_status]

    if start_date:
        where_conditions.append("je.transaction_date >= ?")
        params.append(start_date)

    if end_date:
        where_conditions.append("je.transaction_date <= ?")
        params.append(end_date)

    where_clause = " AND ".join(where_conditions)

    query = f"""
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
        LEFT JOIN trade_accounts ta_check ON je.id = ta_check.journal_entry_id
        WHERE {where_clause}
        ORDER BY je.transaction_date, je.id, p.id
    """

    cursor.execute(query, params)
    return [dict(row) for row in cursor.fetchall()]


def group_postings_by_je(postings: List[Dict]) -> Dict[str, List[Dict]]:
    """Group postings by journal_entry_id."""
    grouped = defaultdict(list)
    for posting in postings:
        grouped[posting['je_id']].append(posting)
    return grouped


# =============================================================================
# Validation
# =============================================================================

def validate_account_mappings(postings: List[Dict]) -> List[Dict]:
    """Validate all account_codes have non-null remote_id."""
    errors = []
    seen_accounts = set()
    for posting in postings:
        account_code = posting['account_code']
        if account_code in seen_accounts:
            continue
        seen_accounts.add(account_code)
        if not posting.get('qbo_account_id'):
            errors.append({
                'account_code': account_code,
                'error': f"Account {account_code} has no remote_id (QBO mapping)"
            })
    return errors


def validate_contact_mappings(postings: List[Dict], conn) -> List[Dict]:
    """Validate all contacts referenced in postings have a remote_id."""
    errors = []
    seen_contacts = set()
    for posting in postings:
        contact = posting.get('contact')
        if not contact or contact in seen_contacts:
            continue
        seen_contacts.add(contact)
        row = conn.execute(
            "SELECT remote_id FROM contacts WHERE name = ?", (contact,)
        ).fetchone()
        if not row or not row[0]:
            errors.append({
                'contact': contact,
                'error': f"Contact '{contact}' has no remote_id (QBO Customer/Vendor mapping)"
            })
    return errors


def validate_journal_balance(postings: List[Dict]) -> Optional[str]:
    """Validate debits equal credits. Returns error message if unbalanced."""
    total_debits = sum(p['amount'] for p in postings if p['direction'] == 'debit')
    total_credits = sum(p['amount'] for p in postings if p['direction'] == 'credit')
    if total_debits != total_credits:
        return f"Unbalanced entry: debits={total_debits} credits={total_credits}"
    return None


# =============================================================================
# Transformation
# =============================================================================

def transform_to_qbo_journal_entry(je_id: str, postings: List[Dict]) -> Tuple[Optional[Dict], Optional[str]]:
    """
    Transform local journal entry postings to QBO JournalEntry format.
    Returns (qbo_entry, error_message) tuple.
    """
    if not postings:
        return None, "No postings provided"

    balance_error = validate_journal_balance(postings)
    if balance_error:
        return None, balance_error

    for posting in postings:
        acct_meta = json.loads(posting.get('account_meta') or '{}')
        qbo_type = acct_meta.get('qbo_type', '')
        entity_type = get_entity_type(posting.get('contact_meta'))
        contact = posting.get('contact', 'None')

        if qbo_type == 'Accounts Receivable':
            if not posting.get('qbo_contact_id'):
                return None, f"A/R account {posting['account_code']} requires a Customer entity but posting has no contact with a remote_id (contact='{contact}')"
            if entity_type != 'Customer':
                return None, f"A/R account {posting['account_code']} requires a Customer entity but contact '{contact}' is type '{entity_type}'"
        elif qbo_type == 'Accounts Payable':
            if not posting.get('qbo_contact_id'):
                return None, f"A/P account {posting['account_code']} requires a Vendor entity but posting has no contact with a remote_id (contact='{contact}')"
            if entity_type != 'Vendor':
                return None, f"A/P account {posting['account_code']} requires a Vendor entity but contact '{contact}' is type '{entity_type}'"

    first_posting = postings[0]
    lines = []
    for posting in postings:
        amount_dollars = posting['amount'] / 100.0
        posting_type = "Debit" if posting['direction'] == 'debit' else "Credit"

        line_detail = {
            "PostingType": posting_type,
            "AccountRef": {"value": posting['qbo_account_id']}
        }

        if posting.get('qbo_contact_id'):
            entity_type = get_entity_type(posting.get('contact_meta'))
            line_detail["Entity"] = {
                "Type": entity_type,
                "EntityRef": {"value": posting['qbo_contact_id']}
            }

        if posting.get('class_remote_id'):
            line_detail["ClassRef"] = {"value": posting['class_remote_id']}

        line = {
            "Amount": round(amount_dollars, 2),
            "DetailType": "JournalEntryLineDetail",
            "JournalEntryLineDetail": line_detail
        }
        if posting.get('description'):
            line["Description"] = posting['description']
        lines.append(line)

    journal_entry = {"TxnDate": first_posting['transaction_date'], "Line": lines}
    # Unconditional idempotency tag (see _shared/locate.py): JEs carry no
    # DocNumber and memos repeat across entries, so the tag is the only
    # reliable key for the post-then-fail read-back.
    tag = make_tag(je_id[:8])
    memo = first_posting.get('memo')
    journal_entry["PrivateNote"] = f"{memo} {tag}" if memo else tag

    for p in postings:
        if p.get('class_name') and not p.get('class_remote_id'):
            journal_entry["_class_name_missing_ref"] = p['class_name']
            break

    return journal_entry, None


# =============================================================================
# Publishing
# =============================================================================

def publish_single_entry(client, rate_limiter, je_id: str, qbo_entry: Dict, env_path: str) -> Dict:
    """Publish a single journal entry to QBO. Returns result dict.

    `client` may be a raw QuickBooks client or a _shared.auth.ClientHolder
    (proactive token refresh + one typed-401 retry on long runs).
    """
    result = {
        'je_id': je_id, 'success': False, 'external_id': None,
        'error_code': None, 'error_message': None
    }

    auth_retried = False
    for retry in range(MAX_RETRIES):
        dead = auth_dead_error(client)
        if dead:
            # Refresh token is gone — fail fast and loud, no network call.
            result['error_code'] = 'AUTH_DEAD'
            result['error_message'] = dead
            return result
        maybe_proactive_refresh(client, env_path)
        c = resolve_client(client)
        try:
            rate_limiter.wait()

            je = JournalEntry()
            je.TxnDate = qbo_entry['TxnDate']
            if qbo_entry.get('PrivateNote'):
                je.PrivateNote = qbo_entry['PrivateNote']

            je.Line = []
            for line_data in qbo_entry['Line']:
                line = JournalEntryLine()
                line.Amount = line_data['Amount']
                line.DetailType = "JournalEntryLineDetail"
                if line_data.get('Description'):
                    line.Description = line_data['Description']

                detail = JournalEntryLineDetail()
                detail.PostingType = line_data['JournalEntryLineDetail']['PostingType']

                account_ref = Ref()
                account_ref.value = line_data['JournalEntryLineDetail']['AccountRef']['value']
                detail.AccountRef = account_ref

                entity_data = line_data['JournalEntryLineDetail'].get('Entity')
                if entity_data:
                    entity = Entity()
                    entity.Type = entity_data['Type']
                    entity_ref = Ref()
                    entity_ref.value = entity_data['EntityRef']['value']
                    entity.EntityRef = entity_ref
                    detail.Entity = entity

                class_data = line_data['JournalEntryLineDetail'].get('ClassRef')
                if class_data:
                    class_ref = Ref()
                    class_ref.value = class_data['value']
                    detail.ClassRef = class_ref

                line.JournalEntryLineDetail = detail
                je.Line.append(line)

            je.save(qb=c)

            if je.Id:
                result['success'] = True
                result['external_id'] = je.Id
                save_tokens_if_available(c, env_path)
            else:
                result['error_code'] = 'NO_ID_RETURNED'
                result['error_message'] = 'QBO did not return a JournalEntry ID'

            return result

        except Exception as e:
            error_str = str(e)
            # Typed 401 → refresh + retry ONCE with the swapped client
            # (mirrors _shared/common.py; business faults never retry).
            if is_auth_fault(e) and not auth_retried and try_reactive_refresh(client, env_path):
                print(json.dumps({
                    'warning': 'AUTH_RETRY: access token rejected mid-run; refreshed and retrying the save once'
                }), file=sys.stderr)
                auth_retried = True
                continue
            if '429' in error_str or 'rate' in error_str.lower():
                rate_limiter.trigger_backoff(retry)
                if retry < MAX_RETRIES - 1:
                    continue
            # Post-then-fail guard (mirrors _shared/common.py): a 10000/6240
            # fault can mean the JE WAS created. Read it back before marking
            # failed — a blind re-publish double-posts.
            if is_post_then_fail(e):
                locator = {'entity': 'JournalEntry', 'tag': make_tag(je_id[:8]),
                           'txn_date': qbo_entry['TxnDate']}
                res = locate_posted_object(c, rate_limiter, locator, fault=e)
                if res.state == FOUND:
                    print(json.dumps({
                        'warning': f'LOCATE_RECOVERED: QBO faulted but JE {je_id} had posted — '
                                   f'linked external_id {res.qbo_id} instead of retrying',
                        'original_error': error_str,
                    }), file=sys.stderr)
                    result['success'] = True
                    result['external_id'] = res.qbo_id
                    return result
                if res.state in (AMBIGUOUS, INCONCLUSIVE):
                    # update_sync_error routes LOCATE_* codes to status='verify'
                    # — structurally excluded from re-publish until a human checks.
                    result['error_code'] = f'LOCATE_{res.state.upper()}'
                    result['error_message'] = (
                        f"posted-state unknown — verify in QBO before any retry. "
                        f"{res.detail}; original_error: {error_str}")
                    return result
            result['error_code'] = 'API_ERROR'
            result['error_message'] = error_str
            return result

    result['error_code'] = 'MAX_RETRIES'
    result['error_message'] = 'Max retries exceeded'
    return result


def publish_batch(
    client,
    rate_limiter,
    grouped_jes: Dict[str, List[Dict]],
    conn: sqlite3.Connection,
    env_path: str
) -> Tuple[int, int, List[Dict], List[str]]:
    """Publish journal entries in chunks. Returns (processed, failed, errors, external_ids)."""
    processed = 0
    failed = 0
    errors = []
    external_ids = []

    je_items = list(grouped_jes.items())

    for chunk_start in range(0, len(je_items), BATCH_CHUNK_SIZE):
        chunk = je_items[chunk_start:chunk_start + BATCH_CHUNK_SIZE]

        for je_id, postings in chunk:
            idempotency_key = f"qbo-publish-{je_id}"

            qbo_entry, transform_error = transform_to_qbo_journal_entry(je_id, postings)
            if transform_error:
                errors.append({
                    'journal_entry_id': je_id,
                    'error_code': 'TRANSFORM_ERROR',
                    'error_message': transform_error
                })
                update_sync_error(conn, 'journal_entries', je_id, {
                    'error_code': 'TRANSFORM_ERROR', 'error_message': transform_error
                })
                failed += 1
                continue

            if qbo_entry.get('_class_name_missing_ref'):
                missing_class = qbo_entry['_class_name_missing_ref']
                error_msg = f"class_name '{missing_class}' has no matching remote_id in tags table — cannot publish without ClassRef"
                errors.append({
                    'journal_entry_id': je_id,
                    'error_code': 'CLASS_REF_MISSING',
                    'error_message': error_msg
                })
                update_sync_error(conn, 'journal_entries', je_id, {
                    'error_code': 'CLASS_REF_MISSING', 'error_message': error_msg
                })
                failed += 1
                continue

            result = publish_single_entry(client, rate_limiter, je_id, qbo_entry, env_path)

            if result['success'] and result['external_id']:
                update_sync_success(conn, 'journal_entries', je_id, result['external_id'])
                external_ids.append(result['external_id'])
                processed += 1
            else:
                errors.append({
                    'journal_entry_id': je_id,
                    'error_code': result['error_code'] or 'UNKNOWN',
                    'error_message': result['error_message'] or 'Unknown error'
                })
                update_sync_error(conn, 'journal_entries', je_id, {
                    'error_code': result['error_code'],
                    'error_message': result['error_message']
                })
                failed += 1

        conn.commit()

    return processed, failed, errors, external_ids
