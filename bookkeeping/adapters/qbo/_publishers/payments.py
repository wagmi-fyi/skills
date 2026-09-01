"""
Publish receivable payments as QBO Payment objects.

Two paths: consolidated settlement groups (one Payment with N Invoice + M
CreditMemo LinkedTxn lines, grouped by metadata.settlement_id) and singleton
per-row Payments. Settlement-reducing credits arrive as first-class credit_memo
TAs in the batch (see credit_memos.py / credit_applications.py); this module no
longer synthesizes Credit Memos from clearing-JE adjustment postings.
"""

import json
import sqlite3
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from quickbooks.objects.payment import Payment as QBOPayment
from quickbooks.objects.base import Ref

from _shared.auth import resolve_client
from _shared.client import save_tokens_if_available
from _shared.common import (
    query_trade_account_payments, publish_single_qbo_object,
    query_settlement_credit_apps, query_payout_consumed_credits
)
from _shared.locate import make_tag, confirm_payment_lines_applied
from _shared.sync_status import update_sync_success, update_sync_error, update_sync_ignore


def publish_payments(
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
    Publish receivable payments as QBO Payment objects.
    Returns (processed, failed, skipped, errors, external_ids).
    """
    rows = query_trade_account_payments(conn, sync_status, start_date, end_date, ta_type='receivable')

    processed = 0
    failed = 0
    skipped = 0
    errors = []
    external_ids = []

    # Group bank-funded TAPs by metadata.settlement_id so that settlement
    # deposits publish as ONE Payment with all Invoice (and optional CM)
    # LinkedTxn lines. Singletons (no settlement_id) flow through the
    # existing per-row path.
    settlement_groups = {}  # settlement_id -> list of bank-funded R-TAP rows
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

    # Process each settlement group as one consolidated Payment.
    # Pure-R: TotalAmt = Σ R, Lines = N Invoice LinkedTxn.
    # Mixed R+CM: TotalAmt = Σ R − Σ CM, Lines = N Invoice + M CreditMemo
    # LinkedTxn. Empty cm_taps makes the CM branches below no-ops.
    for sid, group_rows in settlement_groups.items():
        cm_taps = query_settlement_credit_apps(conn, sid)

        # Pre-flight: detect partially-published settlement.
        # query_trade_account_payments excludes already-synced TAPs. If any
        # other TAP for this sid is already synced, the group above is a
        # partial set → consolidated Payment would emit TotalAmt < deposit
        # and silently mismatch the bank. Fail loud.
        # Restrict the count to the A/R side (bank-funded receivable TAPs + CM
        # credit-apps): a four-type settlement shares one settlement_id across the
        # Payment (R/CM) and the BillPayment (P/VC), which publish independently, so
        # the payable side being synced must NOT trip the receivable guard.
        already_synced = conn.execute("""
            SELECT COUNT(*) FROM trade_account_payments tap
            LEFT JOIN trade_accounts ta ON tap.trade_account_id = ta.id
            LEFT JOIN trade_accounts src ON tap.source_ta_id = src.id
            WHERE json_extract(tap.metadata, '$.settlement_id') = ?
              AND json_extract(tap.sync, '$.external_id') IS NOT NULL
              AND (
                    (tap.source_ta_id IS NULL AND ta.type = 'receivable')
                 OR (src.type = 'credit_memo')
              )
        """, (sid,)).fetchone()[0]
        if already_synced > 0:
            err_msg = (f'Settlement {sid}: {already_synced} TAP(s) already synced; '
                       f'cannot consolidate remainder. Manual reconciliation required.')
            for r in group_rows:
                errors.append({'payment_id': r['tap_id'],
                               'error_code': 'SETTLEMENT_PARTIALLY_PUBLISHED',
                               'error_message': err_msg})
                update_sync_error(conn, 'trade_account_payments', r['tap_id'],
                                  'SETTLEMENT_PARTIALLY_PUBLISHED')
                skipped += 1
            continue

        # Pre-flight: uniformity across the group. Consolidated Payment uses
        # first_row for payment_account_remote_id, contact_remote_id, and
        # payment_date — if a group is heterogeneous (config drift, mistag),
        # the GL is silently wrong. Refuse to consolidate.
        banks = {r['payment_account_remote_id'] for r in group_rows if r.get('payment_account_remote_id')}
        customers = {r['contact_remote_id'] for r in group_rows if r.get('contact_remote_id')}
        dates = {r['payment_date'] for r in group_rows}
        if len(banks) > 1 or len(customers) > 1 or len(dates) > 1:
            err_msg = (f'Settlement {sid}: group has {len(banks)} bank(s), '
                       f'{len(customers)} customer(s), {len(dates)} date(s) — '
                       f'refusing to consolidate.')
            for r in group_rows:
                errors.append({'payment_id': r['tap_id'],
                               'error_code': 'SETTLEMENT_GROUP_HETEROGENEOUS',
                               'error_message': err_msg})
                update_sync_error(conn, 'trade_account_payments', r['tap_id'],
                                  'SETTLEMENT_GROUP_HETEROGENEOUS')
                skipped += 1
            continue

        # Pre-flight: every R Invoice (target + parent) and every source CM must have external_ids.
        # Three checks attribute errors per-TAP for agent diagnosis:
        #   PARENT_TA_NOT_SYNCED   — bank-funded R-TAP whose R-TA isn't synced yet
        #   SOURCE_CM_NOT_SYNCED   — credit-app TAP whose source CM isn't synced yet
        #   TARGET_R_NOT_SYNCED    — credit-app TAP whose target R-TA isn't synced yet
        any_missing = False
        for r in group_rows:
            if not r.get('ta_external_id'):
                errors.append({'payment_id': r['tap_id'], 'error_code': 'PARENT_TA_NOT_SYNCED',
                               'error_message': f'Settlement {sid}: R-TA not yet published'})
                skipped += 1
                any_missing = True
        for c in cm_taps:
            if not c.get('source_external_id'):
                errors.append({'payment_id': c['tap_id'], 'error_code': 'SOURCE_CM_NOT_SYNCED',
                               'error_message': f'Settlement {sid}: source CM not yet published'})
                skipped += 1
                any_missing = True
            if not c.get('target_ta_external_id'):
                errors.append({'payment_id': c['tap_id'], 'error_code': 'TARGET_R_NOT_SYNCED',
                               'error_message': f'Settlement {sid}: credit-app target R-TA not yet published'})
                skipped += 1
                any_missing = True
        if any_missing:
            continue

        # Build mixed-Line Payment
        first_row = group_rows[0]
        first_meta = json.loads(first_row['tap_metadata']) if first_row.get('tap_metadata') else {}
        # Cash deposit = sum of bank-funded R-TAPs (group_rows are source_ta_id IS NULL).
        deposit_cents = sum(r['amount'] for r in group_rows)
        sum_cm_cents = sum(c['amount'] for c in cm_taps)

        # Aggregate Lines per QBO TxnId. With multiplied TAPs (1 cash + N_CM cm-app TAPs per
        # R-TA), the publisher must collapse N×M cm-app TAPs into 1 Line per source CM,
        # and sum each R-TA's cash + cm-app TAPs into 1 Invoice Line at face. Per the
        # QBO Payment 1650 empirical: each LinkedTxn TxnId appears once with Amount=face.
        invoice_amounts = defaultdict(int)   # ta_external_id -> cents
        for r in group_rows:
            invoice_amounts[r['ta_external_id']] += r['amount']
        for c in cm_taps:
            invoice_amounts[c['target_ta_external_id']] += c['amount']
        cm_amounts = defaultdict(int)        # source_external_id -> cents
        for c in cm_taps:
            cm_amounts[c['source_external_id']] += c['amount']

        sum_invoice_cents = sum(invoice_amounts.values())

        # Pre-flight invariant: Σ Invoice Lines == deposit + Σ CM Lines.
        # QBO is an external boundary; bad arithmetic here = malformed Payment we
        # can't easily back out of. Fail loud before save().
        if sum_invoice_cents != deposit_cents + sum_cm_cents:
            err_msg = (
                f'Settlement {sid}: Σ Invoice ({sum_invoice_cents}) != '
                f'deposit ({deposit_cents}) + Σ CM ({sum_cm_cents})'
            )
            for r in group_rows:
                errors.append({'payment_id': r['tap_id'], 'error_code': 'LINE_SUM_MISMATCH',
                               'error_message': err_msg})
                update_sync_error(conn, 'trade_account_payments', r['tap_id'], 'LINE_SUM_MISMATCH')
            for c in cm_taps:
                errors.append({'payment_id': c['tap_id'], 'error_code': 'LINE_SUM_MISMATCH',
                               'error_message': err_msg})
                update_sync_error(conn, 'trade_account_payments', c['tap_id'], 'LINE_SUM_MISMATCH')
            failed += len(group_rows) + len(cm_taps)
            continue

        if deposit_cents <= 0:
            errors.append({'payment_id': first_row['tap_id'], 'error_code': 'SETTLEMENT_NEGATIVE_NET',
                           'error_message': f'Settlement {sid}: deposit_cents={deposit_cents} not > 0'})
            failed += 1
            continue

        if not first_row.get('contact_remote_id'):
            errors.append({'payment_id': first_row['tap_id'], 'error_code': 'CONTACT_REF_MISSING',
                           'error_message': f"Contact '{first_row['ta_contact']}' has no remote_id"})
            skipped += 1
            continue

        payment = QBOPayment()
        payment.TotalAmt = round(deposit_cents / 100.0, 2)
        payment.TxnDate = first_row['payment_date']
        cust = Ref(); cust.value = first_row['contact_remote_id']
        payment.CustomerRef = cust

        if first_row.get('payment_account_remote_id'):
            dep = Ref(); dep.value = first_row['payment_account_remote_id']
            payment.DepositToAccountRef = dep
        elif first_meta.get('payment_account_code'):
            errors.append({'payment_id': first_row['tap_id'], 'error_code': 'DEPOSIT_ACCOUNT_MISSING',
                           'error_message': f"payment_account_code '{first_meta['payment_account_code']}' not found"})
            failed += 1
            continue

        n_invoices = len(invoice_amounts)
        n_cms = len(cm_amounts)
        # Idempotency tag + locator for the post-then-fail read-back (see
        # _shared/locate.py). The _bk_locator attr never serializes to QBO
        # (the SDK's json_filter drops _-prefixed attrs).
        payment.PrivateNote = (f"Settlement {sid} — {n_invoices} invoices + "
                               f"{n_cms} credit memos {make_tag(sid)}")
        payment._bk_locator = {'entity': 'Payment', 'tag': make_tag(sid),
                               'txn_date': first_row['payment_date'],
                               'total': round(deposit_cents / 100.0, 2)}

        # Step 1: create Payment without Line[]
        ext_id, error = publish_single_qbo_object(client, rate_limiter, payment, env_path)
        if not ext_id:
            for r in group_rows:
                errors.append({'payment_id': r['tap_id'], 'error_code': 'API_ERROR', 'error_message': error})
                update_sync_error(conn, 'trade_account_payments', r['tap_id'], error)
                failed += 1
            for c in cm_taps:
                update_sync_error(conn, 'trade_account_payments', c['tap_id'], error)
                failed += 1
            continue

        # Step 2: update Payment with mixed Line[] — one Line per unique TxnId
        payment.Id = ext_id
        payment.SyncToken = "0"
        payment.sparse = True
        lines = []
        for ext, amt in invoice_amounts.items():
            lines.append({
                "Amount": round(amt / 100.0, 2),
                "LinkedTxn": [{"TxnId": str(ext), "TxnType": "Invoice"}],
            })
        for ext, amt in cm_amounts.items():
            lines.append({
                "Amount": round(amt / 100.0, 2),
                "LinkedTxn": [{"TxnId": str(ext), "TxnType": "CreditMemo"}],
            })
        payment.Line = lines
        try:
            rate_limiter.wait()
            payment.save(qb=resolve_client(client))
        except Exception as e:
            # Step 2 (Line update) faulted — but QBO sometimes persists the
            # application anyway (observed in production: a 6000 came back while the
            # Invoice already read Balance $0). Fresh-GET the Payment and
            # verify the persisted LinkedTxns — the save-response echo is not
            # evidence. On any doubt, keep the loud error path: the Payment
            # exists with no (verified) LinkedTxn and the Invoices/CMs are
            # still open. Do NOT claim unverified success.
            expected_links = ({(str(x), 'Invoice') for x in invoice_amounts} |
                              {(str(x), 'CreditMemo') for x in cm_amounts})
            if not confirm_payment_lines_applied(client, rate_limiter, 'Payment',
                                                 ext_id, expected_links):
                err_msg = f"PAYMENT_LINE_UPDATE_FAILED qbo_payment_id={ext_id} err={e}"
                for r in group_rows:
                    errors.append({'payment_id': r['tap_id'], 'error_code': 'LINE_UPDATE_FAILED', 'error_message': err_msg})
                    update_sync_error(conn, 'trade_account_payments', r['tap_id'], err_msg)
                for c in cm_taps:
                    errors.append({'payment_id': c['tap_id'], 'error_code': 'LINE_UPDATE_FAILED', 'error_message': err_msg})
                    update_sync_error(conn, 'trade_account_payments', c['tap_id'], err_msg)
                failed += len(group_rows) + len(cm_taps)
                continue
            # Lines verified applied on a fresh read — fall through to success.

        # Mark all TAPs in the group synced to the same external_id
        for r in group_rows:
            update_sync_success(conn, 'trade_account_payments', r['tap_id'], ext_id)
        for c in cm_taps:
            update_sync_success(conn, 'trade_account_payments', c['tap_id'], ext_id)
        external_ids.append(ext_id)
        processed += len(group_rows) + len(cm_taps)

        # Mark all clearing JEs in the group as sync='ignore'. Pure-R groups
        # may have one clearing JE per TAP rather than one master JE for the
        # whole settlement, so iterate over the union of clearing_je_ids
        # across both R-TAPs and CM-TAPs.
        clearing_je_ids = {_meta(r).get('clearing_je_id') for r in group_rows}
        clearing_je_ids |= {_meta(c).get('clearing_je_id') for c in cm_taps}
        for cje in clearing_je_ids:
            if cje:
                update_sync_ignore(conn, 'journal_entries', cje)

    # Per-row singleton path (existing behavior)
    rows = singleton_rows
    for row in rows:
        tap_id = row['tap_id']

        if not row.get('ta_external_id'):
            print(json.dumps({
                'warning': f"Skipping payment {tap_id}: parent trade account not synced yet"
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

        payment = QBOPayment()
        payment.TotalAmt = round(row['amount'] / 100.0, 2)
        payment.TxnDate = row['payment_date']
        # Idempotency tag + locator (see _shared/locate.py). Singletons
        # previously carried no PrivateNote at all — un-locatable after a
        # post-then-fail fault.
        payment.PrivateNote = make_tag(tap_id[:8])
        payment._bk_locator = {'entity': 'Payment', 'tag': make_tag(tap_id[:8]),
                               'txn_date': row['payment_date'],
                               'total': round(row['amount'] / 100.0, 2)}

        customer_ref = Ref()
        customer_ref.value = row['contact_remote_id']
        payment.CustomerRef = customer_ref

        # Deposit directly to bank account — fail if payment_account_code can't be resolved
        tap_meta_check = json.loads(row['tap_metadata']) if row.get('tap_metadata') else {}
        pmt_acct_code = tap_meta_check.get('payment_account_code')
        if row.get('payment_account_remote_id'):
            deposit_ref = Ref()
            deposit_ref.value = row['payment_account_remote_id']
            payment.DepositToAccountRef = deposit_ref
        elif pmt_acct_code:
            errors.append({
                'payment_id': tap_id,
                'error_code': 'DEPOSIT_ACCOUNT_MISSING',
                'error_message': f"payment_account_code '{pmt_acct_code}' not found in chart_of_accounts — "
                                 f"QBO would default to Undeposited Funds"
            })
            update_sync_error(conn, 'trade_account_payments', tap_id, 'DEPOSIT_ACCOUNT_MISSING')
            failed += 1
            continue

        # Step 1: Create payment WITHOUT Line — QBO Payment CREATE silently
        # ignores LinkedTxn. Line must be added via a subsequent update.
        ext_id, error = publish_single_qbo_object(client, rate_limiter, payment, env_path)
        if ext_id:
            # Step 2: Update payment with LinkedTxn to link to Invoice.
            payment.Id = ext_id
            payment.SyncToken = "0"
            payment.sparse = True
            payment.Line = [{
                "Amount": round(row['amount'] / 100.0, 2),
                "LinkedTxn": [{"TxnId": str(row['ta_external_id']), "TxnType": "Invoice"}]
            }]
            try:
                rate_limiter.wait()
                payment.save(qb=resolve_client(client))
            except Exception as e:
                # Step 2 (Line update) faulted — fresh-GET the Payment and
                # verify the persisted LinkedTxn before deciding (QBO sometimes
                # applies the line and still errors). On any doubt, fail loud:
                # the Payment exists with no verified LinkedTxn and the Invoice
                # is still open.
                expected_links = {(str(row['ta_external_id']), 'Invoice')}
                if not confirm_payment_lines_applied(client, rate_limiter, 'Payment',
                                                     ext_id, expected_links):
                    err_msg = f"PAYMENT_LINE_UPDATE_FAILED qbo_payment_id={ext_id} err={e}"
                    errors.append({'payment_id': tap_id, 'error_code': 'LINE_UPDATE_FAILED', 'error_message': err_msg})
                    update_sync_error(conn, 'trade_account_payments', tap_id, err_msg)
                    failed += 1
                    continue
                # Line verified applied on a fresh read — fall through to success.

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


def publish_payout_consumed_credits(
    client,
    rate_limiter,
    conn: sqlite3.Connection,
    config: Dict,
    sync_status: str,
    start_date: Optional[str],
    end_date: Optional[str],
    env_path: str
) -> Tuple[int, int, int, List[Dict], List[str]]:
    """Publish payout-keyed settlements that consume a CreditMemo within the payout.

    Design (b), the bank-funded CM-consume fix (Tiger Sauce $197.91, SB4U P06). A payout-keyed
    channel (e.g. Shopify) settles a chargeback/return CreditMemo *inside* the payout deposit:
    the payout's invoice-collection TAPs are GROSS (full invoice face) and a separate bank-funded
    CM-consume TAP (parent type=credit_memo) carries the credit. Emit ONE consolidated mixed-Line
    Payment per payout — TotalAmt = SUM(gross R) - SUM(CM), Line = N Invoice (face) + M CreditMemo —
    so the bank nets and the CM applies (Balance 0). Detection + grouping live in
    common.query_payout_consumed_credits (group key = parent-TA payout_id; the rows are made
    disjoint from publish_payments' singleton/settlement path by the NOT EXISTS clause in
    query_trade_account_payments, so nothing double-publishes).

    Reuses the proven mixed-Line Payment builder (create without Line[] -> sparse Line[] update ->
    confirm_payment_lines_applied post-then-fail guard) used by the settlement_id path above, but
    nets at the PAYOUT level: the CM is not pre-attributed to any invoice, so deposit = SUM R - SUM CM
    (the existing settlement path's R-TAPs are already net-of-CM cash). Uses the real CM TA via its
    parent's external_id — never synthesizes a credit from clearing-JE postings (principle 11).

    Returns (processed, failed, skipped, errors, external_ids).
    """
    rows = query_payout_consumed_credits(conn, sync_status)

    processed = 0
    failed = 0
    skipped = 0
    errors = []
    external_ids = []

    def _meta(row):
        return json.loads(row['tap_metadata']) if row.get('tap_metadata') else {}

    # Group by payout_id (parent-TA metadata). Each group = one consolidated Payment.
    groups = defaultdict(list)
    for row in rows:
        groups[row['payout_id']].append(row)

    for payout_id, group in groups.items():
        inv_rows = [r for r in group if r['role'] == 'invoice']
        cm_rows = [r for r in group if r['role'] == 'credit']

        # Completeness: a consumed-credit payout must carry >=1 invoice AND >=1 credit TAP.
        # (The selection guarantees a credit exists; this also catches a date/sync split that
        # left the group without invoices — fail loud rather than emit a CM-only Payment.)
        if not inv_rows or not cm_rows:
            err_msg = (f'Payout {payout_id}: incomplete consumed-credit group '
                       f'({len(inv_rows)} invoice / {len(cm_rows)} credit TAP) — refusing to publish')
            for r in group:
                errors.append({'payment_id': r['tap_id'], 'error_code': 'PAYOUT_GROUP_INCOMPLETE',
                               'error_message': err_msg})
                update_sync_error(conn, 'trade_account_payments', r['tap_id'], 'PAYOUT_GROUP_INCOMPLETE')
                skipped += 1
            continue

        # Pre-flight: partially-published payout. If any bank-funded TAP for this payout is
        # already synced (a prior partial run), consolidating the remainder would emit a deposit
        # smaller than the real bank line. Fail loud — mirrors the settlement_id guard.
        already_synced = conn.execute("""
            SELECT COUNT(*) FROM trade_account_payments tap
            JOIN trade_accounts ta ON tap.trade_account_id = ta.id
            WHERE tap.source_ta_id IS NULL AND tap.import_id IS NOT NULL
              AND ta.type IN ('receivable', 'credit_memo')
              AND json_extract(ta.metadata, '$.payout_id') = ?
              AND json_extract(tap.sync, '$.external_id') IS NOT NULL
        """, (payout_id,)).fetchone()[0]
        if already_synced > 0:
            err_msg = (f'Payout {payout_id}: {already_synced} bank-funded TAP(s) already synced; '
                       f'cannot consolidate remainder. Manual reconciliation required.')
            for r in group:
                errors.append({'payment_id': r['tap_id'], 'error_code': 'PAYOUT_PARTIALLY_PUBLISHED',
                               'error_message': err_msg})
                update_sync_error(conn, 'trade_account_payments', r['tap_id'], 'PAYOUT_PARTIALLY_PUBLISHED')
                skipped += 1
            continue

        # Pre-flight: uniformity. The consolidated Payment uses first_row for bank, customer and
        # date — a heterogeneous group (config drift, mistag) would silently post the GL wrong.
        banks = {r['payment_account_remote_id'] for r in group if r.get('payment_account_remote_id')}
        customers = {r['contact_remote_id'] for r in group if r.get('contact_remote_id')}
        dates = {r['payment_date'] for r in group}
        if len(banks) > 1 or len(customers) > 1 or len(dates) > 1:
            err_msg = (f'Payout {payout_id}: group has {len(banks)} bank(s), '
                       f'{len(customers)} customer(s), {len(dates)} date(s) — refusing to consolidate.')
            for r in group:
                errors.append({'payment_id': r['tap_id'], 'error_code': 'PAYOUT_GROUP_HETEROGENEOUS',
                               'error_message': err_msg})
                update_sync_error(conn, 'trade_account_payments', r['tap_id'], 'PAYOUT_GROUP_HETEROGENEOUS')
                skipped += 1
            continue

        # Pre-flight: every Invoice (R parent) and CreditMemo (credit parent) must have an external_id.
        any_missing = False
        for r in inv_rows:
            if not r.get('ta_external_id'):
                errors.append({'payment_id': r['tap_id'], 'error_code': 'PARENT_TA_NOT_SYNCED',
                               'error_message': f'Payout {payout_id}: invoice TA not yet published'})
                skipped += 1
                any_missing = True
        for r in cm_rows:
            if not r.get('ta_external_id'):
                errors.append({'payment_id': r['tap_id'], 'error_code': 'SOURCE_CM_NOT_SYNCED',
                               'error_message': f'Payout {payout_id}: CreditMemo TA not yet published'})
                skipped += 1
                any_missing = True
        if any_missing:
            continue

        first_row = inv_rows[0]
        if not first_row.get('contact_remote_id'):
            errors.append({'payment_id': first_row['tap_id'], 'error_code': 'CONTACT_REF_MISSING',
                           'error_message': f"Contact '{first_row['ta_contact']}' has no remote_id"})
            skipped += 1
            continue

        # Aggregate Lines per QBO TxnId. R-TAPs are GROSS (full invoice face); the CM is NOT
        # pre-attributed to any invoice — it nets the cash at the payout level. So each Invoice
        # Line = SUM of that invoice's R-TAP face, and deposit (cash) = SUM Invoice - SUM CM.
        invoice_amounts = defaultdict(int)   # invoice external_id -> cents (face)
        for r in inv_rows:
            invoice_amounts[r['ta_external_id']] += r['amount']
        cm_amounts = defaultdict(int)        # CreditMemo external_id -> cents
        for r in cm_rows:
            cm_amounts[r['ta_external_id']] += r['amount']

        sum_invoice_cents = sum(invoice_amounts.values())   # = SUM gross R
        sum_cm_cents = sum(cm_amounts.values())
        deposit_cents = sum_invoice_cents - sum_cm_cents     # net cash to the bank

        # Pre-flight invariant: SUM Invoice Lines == deposit + SUM CM Lines. QBO is an external
        # boundary; bad arithmetic = a malformed Payment we can't easily back out of.
        if sum_invoice_cents != deposit_cents + sum_cm_cents:
            err_msg = (f'Payout {payout_id}: SUM Invoice ({sum_invoice_cents}) != '
                       f'deposit ({deposit_cents}) + SUM CM ({sum_cm_cents})')
            for r in group:
                errors.append({'payment_id': r['tap_id'], 'error_code': 'LINE_SUM_MISMATCH',
                               'error_message': err_msg})
                update_sync_error(conn, 'trade_account_payments', r['tap_id'], 'LINE_SUM_MISMATCH')
            failed += len(group)
            continue

        if deposit_cents <= 0:
            err_msg = f'Payout {payout_id}: deposit_cents={deposit_cents} not > 0 (SUM CM >= SUM R)'
            errors.append({'payment_id': first_row['tap_id'], 'error_code': 'PAYOUT_NEGATIVE_NET',
                           'error_message': err_msg})
            for r in group:
                update_sync_error(conn, 'trade_account_payments', r['tap_id'], 'PAYOUT_NEGATIVE_NET')
            failed += len(group)
            continue

        payment = QBOPayment()
        payment.TotalAmt = round(deposit_cents / 100.0, 2)
        payment.TxnDate = first_row['payment_date']
        cust = Ref(); cust.value = first_row['contact_remote_id']
        payment.CustomerRef = cust

        if first_row.get('payment_account_remote_id'):
            dep = Ref(); dep.value = first_row['payment_account_remote_id']
            payment.DepositToAccountRef = dep
        else:
            errors.append({'payment_id': first_row['tap_id'], 'error_code': 'DEPOSIT_ACCOUNT_MISSING',
                           'error_message': f'Payout {payout_id}: deposit bank account not resolved'})
            failed += len(group)
            continue

        n_invoices = len(invoice_amounts)
        n_cms = len(cm_amounts)
        # Idempotency tag + locator for the post-then-fail read-back (see _shared/locate.py).
        payment.PrivateNote = (f"Payout {payout_id} — {n_invoices} invoices + "
                               f"{n_cms} credit memos (CM consumed within payout) {make_tag(payout_id)}")
        payment._bk_locator = {'entity': 'Payment', 'tag': make_tag(payout_id),
                               'txn_date': first_row['payment_date'],
                               'total': round(deposit_cents / 100.0, 2)}

        # Step 1: create Payment without Line[]
        ext_id, error = publish_single_qbo_object(client, rate_limiter, payment, env_path)
        if not ext_id:
            for r in group:
                errors.append({'payment_id': r['tap_id'], 'error_code': 'API_ERROR', 'error_message': error})
                update_sync_error(conn, 'trade_account_payments', r['tap_id'], error)
                failed += 1
            continue

        # Step 2: update Payment with mixed Line[] — one Line per unique TxnId
        payment.Id = ext_id
        payment.SyncToken = "0"
        payment.sparse = True
        lines = []
        for ext, amt in invoice_amounts.items():
            lines.append({
                "Amount": round(amt / 100.0, 2),
                "LinkedTxn": [{"TxnId": str(ext), "TxnType": "Invoice"}],
            })
        for ext, amt in cm_amounts.items():
            lines.append({
                "Amount": round(amt / 100.0, 2),
                "LinkedTxn": [{"TxnId": str(ext), "TxnType": "CreditMemo"}],
            })
        payment.Line = lines
        try:
            rate_limiter.wait()
            payment.save(qb=resolve_client(client))
        except Exception as e:
            # Step 2 faulted — QBO sometimes persists the application anyway. Fresh-GET and verify
            # the persisted LinkedTxns before deciding; the save-response echo is not evidence.
            expected_links = ({(str(x), 'Invoice') for x in invoice_amounts} |
                              {(str(x), 'CreditMemo') for x in cm_amounts})
            if not confirm_payment_lines_applied(client, rate_limiter, 'Payment',
                                                 ext_id, expected_links):
                err_msg = f"PAYMENT_LINE_UPDATE_FAILED qbo_payment_id={ext_id} err={e}"
                for r in group:
                    errors.append({'payment_id': r['tap_id'], 'error_code': 'LINE_UPDATE_FAILED',
                                   'error_message': err_msg})
                    update_sync_error(conn, 'trade_account_payments', r['tap_id'], err_msg)
                failed += len(group)
                continue
            # Lines verified applied on a fresh read — fall through to success.

        # Mark all TAPs in the group (invoice + credit) synced to the same external_id
        for r in group:
            update_sync_success(conn, 'trade_account_payments', r['tap_id'], ext_id)
        external_ids.append(ext_id)
        processed += len(group)

        # Mark the payout's clearing JE(s) sync='ignore' — the QBO Payment carries the same
        # accounting (the local clearing JE already nets the bank).
        clearing_je_ids = {_meta(r).get('clearing_je_id') for r in group}
        for cje in clearing_je_ids:
            if cje:
                update_sync_ignore(conn, 'journal_entries', cje)

    conn.commit()
    return processed, failed, skipped, errors, external_ids
