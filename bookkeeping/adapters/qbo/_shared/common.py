"""
Common functions shared across QBO publisher modules.

Provides: entity type detection, shared DB queries, and the generic QBO object publisher.
"""

import json
import sqlite3
import sys
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from _shared.auth import (
    resolve_client, maybe_proactive_refresh, try_reactive_refresh,
    is_auth_fault, auth_dead_error
)
from _shared.client import save_tokens_if_available, MAX_RETRIES
from _shared.locate import (
    is_post_then_fail, locate_posted_object, FOUND, AMBIGUOUS, INCONCLUSIVE
)


def get_entity_type(contact_meta: Optional[str]) -> str:
    """
    Determine entity type from contact metadata.
    Returns 'Vendor' or 'Customer' based on meta.type field.
    Defaults to 'Vendor' if not specified.
    """
    if not contact_meta:
        return "Vendor"

    try:
        meta = json.loads(contact_meta)
        entity_type = meta.get('type', '').lower()
        if entity_type == 'customer':
            return "Customer"
    except (json.JSONDecodeError, AttributeError):
        pass

    return "Vendor"


def publish_single_qbo_object(client, rate_limiter, qbo_obj, env_path: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Save a single QBO object with rate limiting and retry.
    Returns (external_id, error_message).

    `client` may be a raw QuickBooks client or a _shared.auth.ClientHolder.
    With a holder, long runs get a proactive token refresh and ONE typed-401
    reactive retry (see _shared/auth.py); raw clients behave exactly as before.
    """
    auth_retried = False
    for retry in range(MAX_RETRIES):
        dead = auth_dead_error(client)
        if dead:
            # Refresh token is gone — fail fast and loud, no network call.
            # The row stays error/retryable for a re-publish after re-auth.
            return None, dead
        maybe_proactive_refresh(client, env_path)
        c = resolve_client(client)
        try:
            rate_limiter.wait()
            qbo_obj.save(qb=c)
            save_tokens_if_available(c, env_path)

            if qbo_obj.Id:
                return qbo_obj.Id, None
            else:
                return None, "QBO did not return an object ID"

        except Exception as e:
            error_str = str(e)
            # Typed 401 → refresh + retry ONCE with the swapped client.
            # Checked first so an expired token never reaches the locate
            # path or burns the 429 budget. Business faults never retry.
            # Loud by design: agents drive these runs and must see every
            # recovery, not just every failure.
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
            # Post-then-fail guard: a 10000/6240 fault can mean the object WAS
            # created despite the error. Read it back before marking failed —
            # a blind re-publish of a posted object double-posts. The locator
            # rides on the object as a private attr (the SDK's json_filter
            # drops _-prefixed attrs, so it never serializes to QBO).
            locator = getattr(qbo_obj, '_bk_locator', None)
            if locator is not None and is_post_then_fail(e):
                res = locate_posted_object(c, rate_limiter, locator, fault=e)
                if res.state == FOUND:
                    # Recovered, not clean: warn loudly so the agent sees the
                    # fault rate even though the row resolves correctly.
                    print(json.dumps({
                        'warning': f'LOCATE_RECOVERED: QBO faulted but the object had posted — '
                                   f'linked external_id {res.qbo_id} instead of retrying ({res.detail})',
                        'original_error': error_str,
                    }), file=sys.stderr)
                    return res.qbo_id, None
                if res.state in (AMBIGUOUS, INCONCLUSIVE):
                    # Routed to sync status 'verify' by update_sync_error —
                    # structurally excluded from re-publish until a human checks.
                    return None, (
                        f"LOCATE_{res.state.upper()}: posted-state unknown — verify in QBO "
                        f"before any retry. {res.detail}; original_error: {error_str}"
                    )
                # NOT_FOUND: confirmed absent → today's failed/retryable path.
            return None, error_str

    return None, "Max retries exceeded"


# =============================================================================
# Trade Account Queries
# =============================================================================

def query_trade_accounts(
    conn: sqlite3.Connection,
    sync_status: str,
    start_date: Optional[str],
    end_date: Optional[str],
    ta_type: Optional[str] = None
) -> List[Dict]:
    """Query trade accounts with backing JE postings for publishing."""
    cursor = conn.cursor()

    where_conditions = [
        "json_extract(ta.sync, '$.status') = ?",
        "json_extract(ta.sync, '$.external_id') IS NULL",
        "ta.voided_at IS NULL"
    ]
    params = [sync_status]

    if ta_type:
        where_conditions.append("ta.type = ?")
        params.append(ta_type)

    if start_date:
        where_conditions.append("ta.document_date >= ?")
        params.append(start_date)

    if end_date:
        where_conditions.append("ta.document_date <= ?")
        params.append(end_date)

    where_clause = " AND ".join(where_conditions)

    query = f"""
        SELECT
            ta.id as ta_id,
            ta.type as ta_type,
            ta.contact as ta_contact,
            ta.document_date,
            ta.due_date,
            ta.journal_entry_id,
            ta.metadata as ta_metadata,
            je.memo,
            c.remote_id as contact_remote_id,
            c.meta as contact_meta,
            p.id as posting_id,
            p.account_code,
            p.direction,
            p.amount,
            p.description,
            coa.remote_id as qbo_account_id,
            COALESCE(
                json_extract(p.metadata, '$.class_name'),
                json_extract(je.metadata, '$.class_name')
            ) as class_name,
            t.remote_id as class_remote_id
        FROM trade_accounts ta
        INNER JOIN journal_entries je ON ta.journal_entry_id = je.id
        INNER JOIN postings p ON je.id = p.journal_entry_id
        INNER JOIN chart_of_accounts coa ON p.account_code = coa.code
        LEFT JOIN contacts c ON ta.contact = c.name
        LEFT JOIN tags t ON COALESCE(
            json_extract(p.metadata, '$.class_name'),
            json_extract(je.metadata, '$.class_name')
        ) = t.name AND t.category = 'Class'
        WHERE {where_clause}
        ORDER BY ta.document_date, ta.id, p.id
    """

    cursor.execute(query, params)
    return [dict(row) for row in cursor.fetchall()]


def group_postings_by_ta(postings: List[Dict]) -> Dict[str, List[Dict]]:
    """Group trade account postings by trade_account_id."""
    grouped = defaultdict(list)
    for posting in postings:
        grouped[posting['ta_id']].append(posting)
    return grouped


def query_trade_account_payments(
    conn: sqlite3.Connection,
    sync_status: str,
    start_date: Optional[str],
    end_date: Optional[str],
    ta_type: Optional[str] = None
) -> List[Dict]:
    """Query bank-funded payments with parent TA sync data for publishing.

    Excludes credit applications (TAPs with source_ta_id set) — those are published
    by query_credit_applications + the credit_applications publisher as zero-amount
    Payment/BillPayment objects, not as bank Payments.
    """
    cursor = conn.cursor()

    where_conditions = [
        "json_extract(tap.sync, '$.status') = ?",
        "json_extract(tap.sync, '$.external_id') IS NULL",
        "tap.source_ta_id IS NULL",  # bank-funded only; credit applications go through credit_applications publisher
        # Disjointness: exclude bank-funded receivable TAPs that belong to a payout which
        # consumes a CreditMemo *within* the payout (a bank-funded CM-consume TAP — parent
        # type=credit_memo, source_ta_id NULL, import_id set, no settlement_id). Those publish
        # as ONE consolidated mixed-Line Payment NET of the CM via
        # _publishers/payments.publish_payout_consumed_credits — NOT as gross singletons here.
        # No-op for any payout without such a TAP (the bank-funded CM-consume bug). Group key = payout_id.
        """NOT EXISTS (
            SELECT 1 FROM trade_account_payments cmtap
            JOIN trade_accounts cmta ON cmtap.trade_account_id = cmta.id
            WHERE cmta.type = 'credit_memo'
              AND cmtap.source_ta_id IS NULL
              AND cmtap.import_id IS NOT NULL
              AND cmta.voided_at IS NULL
              AND json_extract(cmta.metadata, '$.payout_id') IS NOT NULL
              AND json_extract(cmta.metadata, '$.payout_id') = json_extract(ta.metadata, '$.payout_id')
              AND json_extract(cmtap.sync, '$.status') = ?
              AND json_extract(cmtap.sync, '$.external_id') IS NULL
        )""",
    ]
    params = [sync_status, sync_status]

    if ta_type:
        where_conditions.append("ta.type = ?")
        params.append(ta_type)

    if start_date:
        where_conditions.append("tap.payment_date >= ?")
        params.append(start_date)

    if end_date:
        where_conditions.append("tap.payment_date <= ?")
        params.append(end_date)

    where_clause = " AND ".join(where_conditions)

    query = f"""
        SELECT
            tap.id as tap_id,
            tap.trade_account_id,
            tap.payment_date,
            tap.amount,
            tap.metadata as tap_metadata,
            ta.type as ta_type,
            ta.contact as ta_contact,
            json_extract(ta.sync, '$.external_id') as ta_external_id,
            c.remote_id as contact_remote_id,
            coa.remote_id as payment_account_remote_id
        FROM trade_account_payments tap
        INNER JOIN trade_accounts ta ON tap.trade_account_id = ta.id AND ta.voided_at IS NULL
        LEFT JOIN contacts c ON ta.contact = c.name
        LEFT JOIN chart_of_accounts coa ON json_extract(tap.metadata, '$.payment_account_code') = coa.code
        WHERE {where_clause}
        ORDER BY tap.payment_date, tap.id
    """

    cursor.execute(query, params)
    return [dict(row) for row in cursor.fetchall()]


def query_credit_applications(
    conn: sqlite3.Connection,
    sync_status: str,
    start_date: Optional[str],
    end_date: Optional[str],
) -> List[Dict]:
    """Query credit applications (TAPs with source_ta_id set) for publishing.

    Each row represents a CM applied to an invoice (target_type='receivable')
    or a VC applied to a bill (target_type='payable'). Both source and target
    must be synced to QBO before the application can publish — the publisher
    handles that pre-flight check.
    """
    cursor = conn.cursor()

    where_conditions = [
        "json_extract(tap.sync, '$.status') = ?",
        "json_extract(tap.sync, '$.external_id') IS NULL",
        "tap.source_ta_id IS NOT NULL",
        "target_ta.voided_at IS NULL",
        "source_ta.voided_at IS NULL",
        # Settlement-applied CM/VC TAPs publish via the mixed-Line settlement Payment
        # path (payments.py), not as standalone zero-amount Payments — exclude them here.
        "(json_extract(tap.metadata, '$.application_method') IS NULL "
        " OR json_extract(tap.metadata, '$.application_method') != 'settlement_payment')",
    ]
    params = [sync_status]

    if start_date:
        where_conditions.append("tap.payment_date >= ?")
        params.append(start_date)

    if end_date:
        where_conditions.append("tap.payment_date <= ?")
        params.append(end_date)

    where_clause = " AND ".join(where_conditions)

    query = f"""
        SELECT
            tap.id as tap_id,
            tap.trade_account_id as target_ta_id,
            tap.source_ta_id as source_ta_id,
            tap.payment_date,
            tap.amount,
            tap.metadata as tap_metadata,
            target_ta.type as target_type,
            target_ta.contact as ta_contact,
            json_extract(target_ta.sync, '$.external_id') as target_external_id,
            json_extract(source_ta.sync, '$.external_id') as source_external_id,
            source_ta.type as source_type,
            c.remote_id as contact_remote_id
        FROM trade_account_payments tap
        INNER JOIN trade_accounts target_ta ON tap.trade_account_id = target_ta.id
        INNER JOIN trade_accounts source_ta ON tap.source_ta_id = source_ta.id
        LEFT JOIN contacts c ON target_ta.contact = c.name
        WHERE {where_clause}
        ORDER BY tap.payment_date, tap.id
    """

    cursor.execute(query, params)
    return [dict(row) for row in cursor.fetchall()]


def query_owner_cleared_payments(
    conn: sqlite3.Connection,
    sync_status: str,
    start_date: Optional[str],
    end_date: Optional[str],
) -> List[Dict]:
    """Query owner-cleared payment-style TAPs for publishing.

    These are TAPs with NO import (not bank-funded) and NO source TA (not a
    credit application) whose PARENT is a credit_memo/vendor_credit —
    settlements cleared through an owner-clearing account via a backing
    clearing JE (metadata.clearing_je_id). They are selected by NONE of the
    other publish phases (publish_payments / publish_bill_payments filter
    parent type receivable/payable; credit_applications needs source_ta_id),
    so before the owner_cleared phase existed they sat pending forever and
    left the A/R sub-ledger unreconciled (a large aging gap observed in production).

    Deliberately DISJOINT from the three existing TAP queries as invoked —
    guarded by test_qbo_publish_owner_cleared.DisjointnessTests.

    NOTE: owner-cleared TAPs whose parent is a RECEIVABLE are not in this
    set — they publish correctly through publish_payments as Payments
    deposited to the owner-clearing account (verified in production: such TAPs
    sync correctly, payment_account_code=10150).
    """
    cursor = conn.cursor()

    where_conditions = [
        "json_extract(tap.sync, '$.status') = ?",
        "json_extract(tap.sync, '$.external_id') IS NULL",
        "tap.source_ta_id IS NULL",
        "tap.import_id IS NULL",
        "ta.type IN ('credit_memo', 'vendor_credit')",
    ]
    params = [sync_status]

    if start_date:
        where_conditions.append("tap.payment_date >= ?")
        params.append(start_date)

    if end_date:
        where_conditions.append("tap.payment_date <= ?")
        params.append(end_date)

    where_clause = " AND ".join(where_conditions)

    query = f"""
        SELECT
            tap.id as tap_id,
            tap.trade_account_id,
            tap.payment_date,
            tap.amount,
            tap.metadata as tap_metadata,
            ta.type as ta_type,
            ta.contact as ta_contact,
            json_extract(ta.sync, '$.external_id') as ta_external_id,
            c.remote_id as contact_remote_id
        FROM trade_account_payments tap
        INNER JOIN trade_accounts ta ON tap.trade_account_id = ta.id AND ta.voided_at IS NULL
        LEFT JOIN contacts c ON ta.contact = c.name
        WHERE {where_clause}
        ORDER BY tap.payment_date, tap.id
    """

    cursor.execute(query, params)
    return [dict(row) for row in cursor.fetchall()]


def query_payout_consumed_credits(
    conn: sqlite3.Connection,
    sync_status: str,
) -> List[Dict]:
    """Fetch bank-funded TAPs for payouts that consume a CreditMemo *within* the payout.

    Earned on a live close (the bank-funded CM-consume bug). A payout-keyed channel (e.g. Shopify)
    can settle a chargeback/return CreditMemo inside the payout deposit. The credit arrives
    as a **bank-funded CM-consume TAP** — parent trade_account type='credit_memo',
    source_ta_id NULL, import_id set — carrying NO settlement_id (the channel groups by
    payout_id, stored on the parent TA's metadata, not the TAP). Such a TAP matches no other
    publish phase (query_trade_account_payments filters parent type receivable/payable;
    query_credit_applications needs source_ta_id; query_owner_cleared_payments needs
    import_id NULL; query_settlement_credit_apps needs application_method='settlement_payment'),
    so it sits pending forever while the payout's invoice Payments post GROSS and the CM floats.

    Returns ALL bank-funded TAPs (parent receivable + credit_memo) for every payout_id that
    contains >=1 such CM-consume TAP, so payments.publish_payout_consumed_credits can emit ONE
    consolidated mixed-Line Payment per payout: TotalAmt = SUM(gross R) - SUM(CM), Lines =
    N Invoice (at gross face) + M CreditMemo. **The R-TAPs here are GROSS (full invoice face)**;
    the CM is not pre-attributed to any invoice — it nets the cash at the payout level, so the
    deposit = SUM R - SUM CM. (This differs from query_settlement_credit_apps, where the R-TAPs
    are already net-of-CM cash and the credit is a separate settlement_payment credit-app TAP.)

    'role' tags each row 'invoice' (parent receivable) or 'credit' (parent credit_memo).
    ta_external_id is the parent's QBO id (Invoice id for 'invoice', CreditMemo id for 'credit');
    a NULL signals an unsynced parent -> the publisher pre-flight fails loud.

    Group key = parent-TA **payout_id**, NOT import_id (an import can span >1 payout). Scoped by
    sync_status only (not date): the row set must stay identical to the query_trade_account_payments
    disjointness exclusion above, so no TAP is ever both consolidated here and posted as a singleton.
    """
    cursor = conn.cursor()
    query = """
        SELECT
            tap.id AS tap_id,
            tap.amount,
            tap.payment_date,
            tap.metadata AS tap_metadata,
            ta.type AS parent_type,
            CASE ta.type WHEN 'credit_memo' THEN 'credit' ELSE 'invoice' END AS role,
            ta.contact AS ta_contact,
            json_extract(ta.metadata, '$.payout_id') AS payout_id,
            json_extract(ta.sync, '$.external_id') AS ta_external_id,
            c.remote_id AS contact_remote_id,
            coa.remote_id AS payment_account_remote_id
        FROM trade_account_payments tap
        INNER JOIN trade_accounts ta ON tap.trade_account_id = ta.id
        LEFT JOIN contacts c ON ta.contact = c.name
        LEFT JOIN chart_of_accounts coa ON json_extract(tap.metadata, '$.payment_account_code') = coa.code
        WHERE tap.source_ta_id IS NULL
          AND tap.import_id IS NOT NULL
          AND ta.type IN ('receivable', 'credit_memo')
          AND ta.voided_at IS NULL
          AND json_extract(tap.sync, '$.status') = ?
          AND json_extract(tap.sync, '$.external_id') IS NULL
          AND json_extract(ta.metadata, '$.payout_id') IS NOT NULL
          AND json_extract(ta.metadata, '$.payout_id') IN (
                SELECT json_extract(cmta.metadata, '$.payout_id')
                FROM trade_account_payments cmtap
                JOIN trade_accounts cmta ON cmtap.trade_account_id = cmta.id
                WHERE cmta.type = 'credit_memo'
                  AND cmtap.source_ta_id IS NULL
                  AND cmtap.import_id IS NOT NULL
                  AND cmta.voided_at IS NULL
                  AND json_extract(cmta.metadata, '$.payout_id') IS NOT NULL
                  AND json_extract(cmtap.sync, '$.status') = ?
                  AND json_extract(cmtap.sync, '$.external_id') IS NULL
          )
        ORDER BY payout_id, role, tap.id
    """
    cursor.execute(query, (sync_status, sync_status))
    return [dict(row) for row in cursor.fetchall()]


def query_settlement_credit_apps(
    conn: sqlite3.Connection,
    settlement_id: str,
) -> List[Dict]:
    """Fetch credit-application TAPs for a settlement-grouped Payment.

    Returns CM TAPs where metadata.application_method='settlement_payment' and
    metadata.settlement_id matches. The publisher uses these to add CreditMemo
    LinkedTxn entries to the mixed-Line settlement Payment.

    Returns target_ta_external_id (target R-TA's QBO Invoice id) so the publisher
    can aggregate CM-app TAP amounts into the per-R-TA Invoice Line. A NULL value
    here signals an unsynced target invoice — the publisher pre-flight must fail
    loud rather than emit a Line with a missing TxnId.
    """
    cursor = conn.cursor()
    query = """
        SELECT
            tap.id as tap_id,
            tap.trade_account_id as target_ta_id,
            tap.source_ta_id as source_ta_id,
            tap.amount,
            tap.metadata as tap_metadata,
            json_extract(target_ta.sync, '$.external_id') as target_ta_external_id,
            json_extract(source_ta.sync, '$.external_id') as source_external_id,
            source_ta.type as source_type
        FROM trade_account_payments tap
        INNER JOIN trade_accounts source_ta ON tap.source_ta_id = source_ta.id
        INNER JOIN trade_accounts target_ta ON tap.trade_account_id = target_ta.id
        WHERE json_extract(tap.metadata, '$.application_method') = 'settlement_payment'
          AND json_extract(tap.metadata, '$.settlement_id') = ?
          AND source_ta.type = 'credit_memo'
          AND json_extract(tap.sync, '$.status') = 'pending'
          AND json_extract(tap.sync, '$.external_id') IS NULL
          AND source_ta.voided_at IS NULL
        ORDER BY tap.payment_date, tap.id
    """
    cursor.execute(query, (settlement_id,))
    return [dict(row) for row in cursor.fetchall()]


def query_settlement_vendor_credit_apps(
    conn: sqlite3.Connection,
    settlement_id: str,
) -> List[Dict]:
    """Fetch vendor-credit-application TAPs for a settlement-grouped BillPayment.

    The A/P-side mirror of query_settlement_credit_apps: returns VC TAPs where
    metadata.application_method='settlement_payment' and metadata.settlement_id
    matches. bill_payments.py uses these to add VendorCredit LinkedTxn entries to
    the mixed-Line settlement BillPayment.

    target_ta_external_id here is the target P-TA's QBO Bill id (the alias name is
    kept identical to query_settlement_credit_apps so the publisher can share the
    Line-aggregation code). A NULL value signals an unsynced target Bill — the
    publisher pre-flight must fail loud (TARGET_BILL_NOT_SYNCED).
    """
    cursor = conn.cursor()
    query = """
        SELECT
            tap.id as tap_id,
            tap.trade_account_id as target_ta_id,
            tap.source_ta_id as source_ta_id,
            tap.amount,
            tap.metadata as tap_metadata,
            json_extract(target_ta.sync, '$.external_id') as target_ta_external_id,
            json_extract(source_ta.sync, '$.external_id') as source_external_id,
            source_ta.type as source_type
        FROM trade_account_payments tap
        INNER JOIN trade_accounts source_ta ON tap.source_ta_id = source_ta.id
        INNER JOIN trade_accounts target_ta ON tap.trade_account_id = target_ta.id
        WHERE json_extract(tap.metadata, '$.application_method') = 'settlement_payment'
          AND json_extract(tap.metadata, '$.settlement_id') = ?
          AND source_ta.type = 'vendor_credit'
          AND json_extract(tap.sync, '$.status') = 'pending'
          AND json_extract(tap.sync, '$.external_id') IS NULL
          AND source_ta.voided_at IS NULL
        ORDER BY tap.payment_date, tap.id
    """
    cursor.execute(query, (settlement_id,))
    return [dict(row) for row in cursor.fetchall()]


# (Removed) detect_clearing_je_adjustments — the legacy inline path that synthesized
# Credit Memos / Vendor Credits from clearing-JE adjustment postings. Retired: standalone
# co-disbursements now publish as their own JE (apply_payments_bulk --standalone_lines),
# and settlement-reducing credits are first-class credit_memo/vendor_credit TAs.
