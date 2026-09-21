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

def deposit_group_key(ta_alias: str, tap_alias: str) -> str:
    """SQL for the key that names one consolidated Payment, given a parent-TA and TAP alias.

    A channel that pays out in batches stamps a payout_id on the parent trade account, and
    that id names the group. It is read first, so a payout-keyed row groups on the payout
    whatever the payment's settlement_id says. An import can span more than one payout.

    A plain ACH or wire deposit carries no channel key. There the import is the bank line,
    since an imports row is one source transaction, and the key is the import id with the
    contact. A QBO Payment carries one customer, so one bank line paying two customers is
    two Payments.

    A settlement-keyed payment with no payout id gets NULL, which matches nothing. Its cash
    is already net of the credit, and publish_payments consolidates it by settlement_id.
    find_bank_funded_payment_gaps reports a bank-funded credit memo in such an import.

    An empty payout id counts as absent.

    Every query that needs this key builds it with this function, so the selection and its
    exclusion stay complements.
    """
    return (f"COALESCE(NULLIF(json_extract({ta_alias}.metadata, '$.payout_id'), ''), "
            f"CASE WHEN json_extract({tap_alias}.metadata, '$.settlement_id') IS NULL "
            f"THEN 'import:' || {tap_alias}.import_id || '|' || {ta_alias}.contact END)")


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
        # Disjointness: exclude bank-funded receivable TAPs that belong to a deposit which
        # consumes a CreditMemo *inside* the deposit. Such a TAP has parent
        # type=credit_memo, source_ta_id NULL and import_id set. Those publish as ONE
        # consolidated mixed-Line Payment NET of the CM via
        # _publishers/payments.publish_payout_consumed_credits — NOT as gross singletons here.
        # No-op for any deposit without such a TAP. The key comes from deposit_group_key(),
        # the same expression query_payout_consumed_credits selects on.
        #
        # The two tests in front of the NOT EXISTS mirror that selection exactly: it takes
        # receivable and credit_memo parents and it requires an import, so only such a row can
        # be excluded here. A payable on the same bank line publishes as a BillPayment and the
        # bank nets across the two objects. A row with no import belongs to no deposit.
        # Excluding either would leave it with no phase at all.
        f"""(ta.type NOT IN ('receivable', 'credit_memo') OR tap.import_id IS NULL OR NOT EXISTS (
            SELECT 1 FROM trade_account_payments cmtap
            JOIN trade_accounts cmta ON cmtap.trade_account_id = cmta.id
            WHERE cmta.type = 'credit_memo'
              AND cmtap.source_ta_id IS NULL
              AND cmtap.import_id IS NOT NULL
              AND cmta.voided_at IS NULL
              AND {deposit_group_key('cmta', 'cmtap')} = {deposit_group_key('ta', 'tap')}
              AND json_extract(cmtap.sync, '$.status') = ?
              AND json_extract(cmtap.sync, '$.external_id') IS NULL
        ))""",
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
    """Fetch bank-funded TAPs for deposits that consume a CreditMemo *inside* the deposit.

    A customer can pay part of an invoice with a credit memo, and a batching channel
    (e.g. Shopify) can settle a chargeback or return inside a payout. Either way the bank
    receives the net and the credit arrives as a **bank-funded CM-consume TAP**, whose parent
    trade_account has type='credit_memo', source_ta_id NULL and import_id set. Such a TAP
    matches no other publish phase (query_trade_account_payments filters parent type
    receivable/payable;
    query_credit_applications needs source_ta_id; query_owner_cleared_payments needs
    import_id NULL; query_settlement_credit_apps needs application_method='settlement_payment'),
    so on its own it sits pending forever while the deposit's invoice Payments post GROSS and
    the CM floats.

    Returns ALL bank-funded TAPs (parent receivable + credit_memo) for every deposit that
    holds >=1 such CM-consume TAP, so payments.publish_payout_consumed_credits can emit ONE
    consolidated mixed-Line Payment per deposit: TotalAmt = SUM(gross R) - SUM(CM), Lines =
    N Invoice (at gross face) + M CreditMemo. **The R-TAPs here are GROSS (full invoice face)**.
    The CM is not pre-attributed to any invoice. It nets the cash at the deposit level, so the
    deposit = SUM R - SUM CM. (This differs from query_settlement_credit_apps, where the R-TAPs
    are already net-of-CM cash and the credit is a separate settlement_payment credit-app TAP.)

    'role' tags each row 'invoice' (parent receivable) or 'credit' (parent credit_memo).
    ta_external_id is the parent's QBO id (Invoice id for 'invoice', CreditMemo id for 'credit');
    a NULL signals an unsynced parent -> the publisher pre-flight fails loud.

    Group key = deposit_group_key(), returned as 'group_key'. Scoped by sync_status only
    (not date): the row set must stay identical to the query_trade_account_payments
    disjointness exclusion above, so no TAP is ever both consolidated here and posted as a
    singleton. A deposit whose rows split across two keys publishes nothing:
    find_bank_funded_payment_gaps refuses the run before any of it posts.
    """
    cursor = conn.cursor()
    key = deposit_group_key('ta', 'tap')
    cm_key = deposit_group_key('cmta', 'cmtap')
    query = f"""
        SELECT
            tap.id AS tap_id,
            tap.amount,
            tap.payment_date,
            tap.metadata AS tap_metadata,
            ta.type AS parent_type,
            CASE ta.type WHEN 'credit_memo' THEN 'credit' ELSE 'invoice' END AS role,
            ta.contact AS ta_contact,
            {key} AS group_key,
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
          AND {key} IN (
                SELECT {cm_key}
                FROM trade_account_payments cmtap
                JOIN trade_accounts cmta ON cmtap.trade_account_id = cmta.id
                WHERE cmta.type = 'credit_memo'
                  AND cmtap.source_ta_id IS NULL
                  AND cmtap.import_id IS NOT NULL
                  AND cmta.voided_at IS NULL
                  AND json_extract(cmtap.sync, '$.status') = ?
                  AND json_extract(cmtap.sync, '$.external_id') IS NULL
          )
        ORDER BY group_key, role, tap.id
    """
    cursor.execute(query, (sync_status, sync_status))
    return [dict(row) for row in cursor.fetchall()]


# How the publisher counts each refusal. A skipped row stays retryable. A failed row is one
# the publisher priced and could not post. Each refusal needs a person to change the data
# before the next run selects the group again.
CONSUMED_CREDIT_REFUSALS = {
    'PAYOUT_GROUP_INCOMPLETE': 'skipped',
    'PAYOUT_PARTIALLY_PUBLISHED': 'skipped',
    'PAYOUT_GROUP_HETEROGENEOUS': 'skipped',
    'PAYOUT_NEGATIVE_NET': 'failed',
}


def check_consumed_credit_group(
    conn: sqlite3.Connection,
    group_key: str,
    group: List[Dict],
) -> Optional[Tuple[str, str]]:
    """Return the first refusal for one consumed-credit group, or None.

    Covers the refusals that do not depend on how far a publish run has got. The pre-publish
    gate can therefore ask before anything posts and get the answer the publisher would give.

    This function does not test for a parent trade account that is not published yet. The
    gate runs before the invoice phase, so an unsynced parent is the ordinary state at that
    moment, and the publisher treats it as retryable.
    """
    inv_rows = [r for r in group if r['role'] == 'invoice']
    cm_rows = [r for r in group if r['role'] == 'credit']

    # Partially published: consolidating the remainder would emit a deposit smaller than
    # the real bank line. The settlement_id guard tests the same thing. It runs before the
    # completeness test below, because a deposit whose invoices published at full face
    # leaves a group of one credit row, and that is half posted rather than incomplete.
    already_synced = conn.execute(f"""
        SELECT COUNT(*) FROM trade_account_payments tap
        JOIN trade_accounts ta ON tap.trade_account_id = ta.id AND ta.voided_at IS NULL
        WHERE tap.source_ta_id IS NULL AND tap.import_id IS NOT NULL
          AND ta.type IN ('receivable', 'credit_memo')
          AND {deposit_group_key('ta', 'tap')} = ?
          AND json_extract(tap.sync, '$.external_id') IS NOT NULL
    """, (group_key,)).fetchone()[0]
    if already_synced > 0:
        return ('PAYOUT_PARTIALLY_PUBLISHED',
                f'Deposit {group_key}: {already_synced} bank-funded TAP(s) already synced; '
                f'cannot consolidate remainder. Manual reconciliation required.')

    # Completeness: a consumed-credit deposit must carry >=1 invoice AND >=1 credit TAP.
    # The selection guarantees a credit. This test catches a group whose invoices went
    # elsewhere, so no CM-only Payment is ever built. Nothing on this bank line has
    # published, or the test above would have answered first.
    if not inv_rows or not cm_rows:
        return ('PAYOUT_GROUP_INCOMPLETE',
                f'Deposit {group_key}: incomplete consumed-credit group '
                f'({len(inv_rows)} invoice / {len(cm_rows)} credit TAP). Refusing to publish.')

    # Uniformity: the Payment takes its bank, customer and date from one row of the group.
    banks = {r['payment_account_remote_id'] for r in group if r.get('payment_account_remote_id')}
    customers = {r['contact_remote_id'] for r in group if r.get('contact_remote_id')}
    dates = {r['payment_date'] for r in group}
    if len(banks) > 1 or len(customers) > 1 or len(dates) > 1:
        return ('PAYOUT_GROUP_HETEROGENEOUS',
                f'Deposit {group_key}: group has {len(banks)} bank(s), '
                f'{len(customers)} customer(s), {len(dates)} date(s). Refusing to consolidate.')

    deposit_cents = sum(r['amount'] for r in inv_rows) - sum(r['amount'] for r in cm_rows)
    if deposit_cents <= 0:
        return ('PAYOUT_NEGATIVE_NET',
                f'Deposit {group_key}: deposit_cents={deposit_cents} not > 0 (SUM CM >= SUM R)')

    return None


def _stranded_credit_reason(status, external_id, has_key: bool, sync_status: str) -> str:
    """Why a bank-funded credit memo's payment row is outside this run's selection."""
    if external_id is not None:
        return (f"it carries external id {external_id}, and no invoice on this line has "
                f"published")
    if status == 'ignore':
        return "it is set to ignore, and no invoice on this line has published"
    if not has_key:
        return ("its bank line is settled through a channel, and the consumed-credit phase "
                "reads no settled line")
    if status is None:
        return "it carries no sync status"
    if status != sync_status:
        return f"its sync status is {status}, and this run publishes {sync_status}"
    return "the consumed-credit selection does not reach it"


def find_bank_funded_payment_gaps(
    conn: sqlite3.Connection,
    sync_status: str,
    start_date: Optional[str],
    end_date: Optional[str],
) -> List[Dict]:
    """Report every bank-funded TAP the publish phases would not post whole.

    A bank-funded TAP (source_ta_id NULL, import_id set) reaches QBO through exactly two
    selections: query_trade_account_payments, once per parent type, and
    query_payout_consumed_credits. A row neither one takes stays pending and no count
    includes it. The run still reports success. Both selections read the one sync status this
    run was given. A bank-funded credit memo outside that status still reduces its bank line,
    so it changes what the other rows on that line should publish. The publish completeness
    rule in reference/quality-guidelines.md calls that a failure, so this turns it into a
    stop.

    The gaps, each in the publisher's error form:

    PAYMENT_MATCHES_NO_PHASE means the row matches no selection. It is a parent type or a
    metadata shape no phase reads, such as a bank-funded vendor credit.

    DEPOSIT_GROUP_SPLIT means a consumed-credit group keyed on its import shares that import
    and contact with a bank-funded receivable or credit-memo row that no consumed-credit group
    holds. The credit and the invoices it reduces would land in different groups, so the
    invoices would post at gross and the bank would be over by the credit. The data does not
    say which key is right, so the run stops and a person decides.

    DEPOSIT_CREDIT_OFF_STATUS means a bank-funded invoice row this run would publish sits on
    the same bank line and contact as a bank-funded credit memo the consumed-credit selection
    does not take. That selection reads one sync status, so a credit row in another status,
    or one already carrying an external id, falls outside it and no phase posts the credit.
    The line then publishes payments that do not agree with the money the bank received.
    This check reads the credit rows whatever status they carry. It pairs them on the bank
    line and the contact, which reaches a settled line, whose deposit key is NULL by design.
    A credit row carrying an external id, or set to ignore, has been dealt with. That line
    passes once an invoice on it has published, which is where the repair recipe in
    gotchas.md leaves a book. Until then the invoice rows are named.

    Every code in CONSUMED_CREDIT_REFUSALS is a gap too. check_consumed_credit_group tests
    them, and the publisher calls the same function, so a clean report means the
    consumed-credit phase will not refuse.

    Call it before anything publishes. Returns [] when every bank-funded row is accounted
    for and every deposit can be posted whole.
    """
    cursor = conn.cursor()
    where = [
        "json_extract(tap.sync, '$.status') = ?",
        "json_extract(tap.sync, '$.external_id') IS NULL",
        "tap.source_ta_id IS NULL",
        "tap.import_id IS NOT NULL",
    ]
    params = [sync_status]
    if start_date:
        where.append("tap.payment_date >= ?")
        params.append(start_date)
    if end_date:
        where.append("tap.payment_date <= ?")
        params.append(end_date)

    eligible = {
        row['tap_id']: row
        for row in (dict(r) for r in cursor.execute(f"""
            SELECT tap.id AS tap_id, tap.import_id, ta.type AS parent_type, ta.contact
            FROM trade_account_payments tap
            INNER JOIN trade_accounts ta ON tap.trade_account_id = ta.id AND ta.voided_at IS NULL
            WHERE {" AND ".join(where)}
        """, params).fetchall())
    }

    consumed = query_payout_consumed_credits(conn, sync_status)
    selected_consumed = {r['tap_id'] for r in consumed}
    selected = set(selected_consumed)
    for ta_type in ('receivable', 'payable'):
        selected |= {r['tap_id'] for r in query_trade_account_payments(
            conn, sync_status, start_date, end_date, ta_type=ta_type)}

    gaps = [
        {'payment_id': tap_id,
         'error_code': 'PAYMENT_MATCHES_NO_PHASE',
         'error_message': (f"Bank-funded payment {tap_id} (parent type '{row['parent_type']}') "
                           f"matches no publish phase. It would stay pending and the deposit "
                           f"would post short.")}
        for tap_id, row in sorted(eligible.items())
        if tap_id not in selected
    ]

    # An import-keyed group must hold every bank-funded receivable and credit-memo TAP of
    # its import and contact that no other consumed group already holds. A row in a group of
    # its own is posted whole there, so two deposits on one bank line pass. A payable sibling
    # passes. It publishes as a BillPayment, and the bank nets across the two objects.
    # Another customer's row passes, since it is its own Payment.
    import_keys = set()
    for row in consumed:
        key = row['group_key'] or ''
        if key.startswith('import:') and row['tap_id'] in eligible:
            import_id, _, contact = key[len('import:'):].partition('|')
            import_keys.add((import_id, contact))
    by_import_contact = defaultdict(list)
    for tap_id, row in sorted(eligible.items()):
        if row['parent_type'] in ('receivable', 'credit_memo'):
            by_import_contact[(row['import_id'], row['contact'])].append(tap_id)
    for import_id, contact in sorted(import_keys):
        for tap_id in by_import_contact.get((import_id, contact), ()):
            if tap_id not in selected_consumed:
                gaps.append({
                    'payment_id': tap_id,
                    'error_code': 'DEPOSIT_GROUP_SPLIT',
                    'error_message': (
                        f"Bank-funded payment {tap_id} belongs to the same bank line and "
                        f"contact as a consumed-credit deposit keyed on import {import_id}, "
                        f"but it is keyed differently, so it sits outside the group. The "
                        f"credit and the invoices it reduces must share one key.")})

    # A bank-funded credit memo reduces what the bank received on its line. Nothing posts
    # one unless the consumed-credit selection takes it, and that selection reads the one
    # sync status this run was given. A credit row it leaves behind puts that bank line's
    # payments out of step with the money that arrived. The invoices on a plain deposit
    # would publish at full face. Where the line is a settlement, whose cash is already
    # net, the credit becomes a second claim on money the settlement accounted for. Either
    # way the invoice rows are named.
    #
    # A credit row carrying an external id is in QBO, and one set to ignore is a person's
    # decision that it never will be. Both say somebody has dealt with the credit, and the
    # recipe in gotchas.md ends that way. What tells a repaired line from one where the
    # credit was put aside before anything posted is whether an invoice on that line has
    # published. Where one has, the line is past what this check can help with. Where none
    # has, the eligible invoice rows would still publish for more than the bank received,
    # so they are named.
    #
    # Rows pair on the bank line and the contact, which is what reaches a settled line,
    # whose deposit key is NULL. A key on both sides that disagrees is two deposits sharing
    # one bank line, and neither reduces the other. The published-invoice test reads the
    # key the same way, so one payout's posted invoice cannot vouch for another's.
    inv_key = deposit_group_key('ta', 'tap')
    cm_key = deposit_group_key('cmta', 'cmtap')
    stranded = {}
    for import_id, contact, cm_tap_id, cm_status, cm_ext, cm_group_key in cursor.execute(f"""
        SELECT cmtap.import_id, cmta.contact, cmtap.id,
               json_extract(cmtap.sync, '$.status'),
               json_extract(cmtap.sync, '$.external_id'), {cm_key}
        FROM trade_account_payments cmtap
        INNER JOIN trade_accounts cmta
            ON cmtap.trade_account_id = cmta.id AND cmta.voided_at IS NULL
        WHERE cmta.type = 'credit_memo' AND cmtap.source_ta_id IS NULL
          AND cmtap.import_id IS NOT NULL
        ORDER BY cmtap.id
    """).fetchall():
        if cm_tap_id in selected_consumed:
            continue
        dealt_with = cm_ext is not None or cm_status == 'ignore'
        seen = stranded.get((import_id, contact))
        # A credit nobody has dealt with is the more useful cause to report.
        if seen is None or (seen[3] and not dealt_with):
            stranded[(import_id, contact)] = (cm_tap_id, cm_status, cm_ext, dealt_with,
                                              cm_group_key)
    if stranded:
        published = defaultdict(list)
        for import_id, contact, group_key in cursor.execute(f"""
            SELECT tap.import_id, ta.contact, {inv_key}
            FROM trade_account_payments tap
            INNER JOIN trade_accounts ta ON tap.trade_account_id = ta.id AND ta.voided_at IS NULL
            WHERE ta.type = 'receivable' AND tap.source_ta_id IS NULL
              AND tap.import_id IS NOT NULL
              AND json_extract(tap.sync, '$.external_id') IS NOT NULL
        """).fetchall():
            published[(import_id, contact)].append(group_key)
        for tap_id, import_id, contact, group_key in cursor.execute(f"""
            SELECT tap.id, tap.import_id, ta.contact, {inv_key}
            FROM trade_account_payments tap
            INNER JOIN trade_accounts ta ON tap.trade_account_id = ta.id AND ta.voided_at IS NULL
            WHERE ta.type = 'receivable' AND {" AND ".join(where)}
            ORDER BY tap.id
        """, params).fetchall():
            found = stranded.get((import_id, contact))
            if found is None:
                continue
            cm_tap_id, cm_status, cm_ext, dealt_with, cm_group_key = found
            if group_key is not None and cm_group_key is not None and group_key != cm_group_key:
                continue
            if dealt_with and any(
                    key is None or cm_group_key is None or key == cm_group_key
                    for key in published[(import_id, contact)]):
                continue
            reason = _stranded_credit_reason(cm_status, cm_ext,
                                             cm_group_key is not None, sync_status)
            gaps.append({
                'payment_id': tap_id,
                'error_code': 'DEPOSIT_CREDIT_OFF_STATUS',
                'error_message': (
                    f"Bank-funded payment {tap_id} sits on the bank line that funds credit "
                    f"memo payment {cm_tap_id}, which nothing will post: {reason}. This "
                    f"line's payments and the money that arrived do not agree, so the row "
                    f"is held back. gotchas.md says what to do.")})

    # The gate tests each group with the function the publisher uses. The selection carries
    # no date window, so the gate tests only a group that holds a row this run would publish.
    by_group = defaultdict(list)
    for row in consumed:
        by_group[row['group_key']].append(row)
    for group_key, group in sorted(by_group.items(), key=lambda kv: str(kv[0])):
        if not any(r['tap_id'] in eligible for r in group):
            continue
        refusal = check_consumed_credit_group(conn, group_key, group)
        if refusal:
            code, message = refusal
            for row in group:
                gaps.append({'payment_id': row['tap_id'], 'error_code': code,
                             'error_message': message})

    return gaps


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
