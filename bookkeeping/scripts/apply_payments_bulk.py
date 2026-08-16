#!/usr/bin/env python3
"""
Apply Payments Bulk Tool
Applies a bank deposit to multiple trade accounts (A/R and A/P) in one atomic operation.
Creates a compound clearing journal entry where the bank line matches the import amount exactly.
Supports mixed receivable/payable TAs and optional adjustment postings for variance resolution.
"""

import argparse
import json
import sqlite3
import sys
import uuid
from collections import defaultdict

from _shared.trade_account_utils import (
    compute_amount_due,
    compute_applied_amount,
    compute_paid_amount,
    compute_status,
    get_balance_account_code,
    is_credit_memo,
)
from _shared.journal_engine import (
    create_journal_entry_direct,
    determine_direction,
    get_import_data,
)
from _shared import config_loader


def hamilton_split(amount_total: int, weights: list) -> list:
    """Largest-remainder distribution. Splits `amount_total` across positions
    in proportion to `weights`. All inputs and outputs are integers.

    Returns a list of len(weights) ints summing exactly to amount_total.

    Raises ValueError on degenerate inputs (empty weights, non-positive sum).
    """
    if not weights:
        raise ValueError("hamilton_split: weights must be non-empty")
    sum_w = sum(weights)
    if sum_w <= 0:
        raise ValueError(f"hamilton_split: sum(weights)={sum_w} must be > 0")
    floors = [(amount_total * w) // sum_w for w in weights]
    residual = amount_total - sum(floors)
    # Fractional remainders, scaled to integer for ordering
    rems = [(amount_total * w) - floors[i] * sum_w for i, w in enumerate(weights)]
    # Sort by largest remainder; stable tie-break by index
    order = sorted(range(len(weights)), key=lambda i: (-rems[i], i))
    out = list(floors)
    # Residual can be negative if amount_total < 0; same algorithm distributes it
    step = 1 if residual >= 0 else -1
    for k in range(abs(residual)):
        out[order[k]] += step
    return out


def allocate_settlement_2d(r_remaining: list, cm_remaining: list) -> tuple:
    """Allocate a mixed-credit settlement across R-TAs and CMs.

    Per-CM column distribution via Hamilton (each CM_j's remaining split across
    R-TAs proportional to r_remaining). Per-R cash derived by row subtraction —
    row closure is exact by construction.

    Returns (cash, cm_apps):
      cash[i]       = cash TAP amount for R_i
      cm_apps[i][j] = credit-app TAP amount for (R_i, CM_j)

    Invariants:
      Σ_i cm_apps[i][j] == cm_remaining[j] for each j  (Hamilton column closure)
      cash[i] + Σ_j cm_apps[i][j] == r_remaining[i]   for each i  (row closure)
      Σ_i cash[i] == Σ_i r_remaining[i] − Σ_j cm_remaining[j]  (= deposit)

    Raises ValueError on negative cash (malformed settlement: CMs exceed an R's
    remaining), or on degenerate inputs propagated from hamilton_split.
    """
    n_r = len(r_remaining)
    n_cm = len(cm_remaining)
    if n_r == 0:
        raise ValueError("allocate_settlement_2d: r_remaining must be non-empty")
    if n_cm == 0:
        raise ValueError("allocate_settlement_2d: cm_remaining must be non-empty (use bypass path for pure-R)")
    cm_apps = [[0] * n_cm for _ in range(n_r)]
    for j, c_j in enumerate(cm_remaining):
        col = hamilton_split(c_j, r_remaining)
        for i in range(n_r):
            cm_apps[i][j] = col[i]
    cash = [r_remaining[i] - sum(cm_apps[i]) for i in range(n_r)]
    for i, c in enumerate(cash):
        if c < 0:
            raise ValueError(
                f"allocate_settlement_2d: R[{i}] cash={c} negative — "
                f"CM total {sum(cm_apps[i])} exceeds remaining {r_remaining[i]}"
            )
    return cash, cm_apps


def parse_arguments():
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description='Apply bulk payments to multiple trade accounts')
    parser.add_argument('--import_id', required=True, help='Bank deposit import ID')
    parser.add_argument('--payments', default=None,
                        help='JSON array: [{"trade_account_id": "...", "amount": 1000}, ...]. '
                             'Omit when --auto_resolve_settlement is set.')
    parser.add_argument('--adjustments', default='[]',
                        help='DEPRECATED. Retired in favor of --standalone_lines (separate postings '
                             'sharing the wire) and first-class CreditMemo/VendorCredit TAs batched '
                             'with --allow_mixed_credit (credits that reduce an invoice/bill). '
                             'A non-empty value is now a hard error.')
    parser.add_argument('--standalone_lines', default='[]',
                        help='JSON array of postings that ride the same bank wire but are NOT part of '
                             'any settlement (e.g. a co-disbursement to another party). Each publishes '
                             'as its own QBO JournalEntry. Triggers an import-split: the clearing JE '
                             'keeps the settlement cash and a second pending JE carries these lines plus '
                             'a balancing bank slice; the two bank slices net to the import. Shape: '
                             '[{"account_code","amount"(int cents),"direction","contact"?,"description"?,'
                             '"class_name"?}].')
    parser.add_argument('--payment_date', required=True, help='Payment date (YYYY-MM-DD)')
    parser.add_argument('--changed_by', default='apply_payments_bulk.py', help='Audit log changed_by value')
    # Settlement-grouped matching: adapters emit per-day TAs tagged with
    # metadata.settlement_id (the canonical key for settlement grouping).
    # Mixed receivable + credit_memo groups are resolved together so the deposit
    # closes the net via one mixed-Line Payment.
    parser.add_argument('--auto_resolve_settlement', action='store_true',
                        help='Resolve TAs from metadata.settlement_id instead of --payments.')
    parser.add_argument('--settlement_id', default=None,
                        help='Settlement id to match (e.g., a payout reference number)')
    parser.add_argument('--allow_mixed_credit', action='store_true',
                        help='Permit credit_memo / vendor_credit TAs in the batch '
                             '(required when net = sum_R - sum_CM)')
    return parser.parse_args()


def get_trade_account(conn, trade_account_id):
    """Fetch trade account with validation."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, type, contact, document_date, due_date,
               journal_entry_id, voided_at, metadata
        FROM trade_accounts
        WHERE id = ?
        """,
        (trade_account_id,)
    )
    row = cursor.fetchone()

    if not row:
        raise ValueError(f"Trade account not found: {trade_account_id}")

    trade_account = {
        'id': row[0],
        'type': row[1],
        'contact': row[2],
        'document_date': row[3],
        'due_date': row[4],
        'journal_entry_id': row[5],
        'voided_at': row[6],
        'metadata': json.loads(row[7]) if row[7] else {},
    }

    if trade_account['voided_at']:
        raise ValueError(f"Cannot apply payment to voided trade account: {trade_account_id}")

    return trade_account


def create_payment(conn, payment_data, changed_by):
    """Create payment record and audit log entry.

    payment_data fields:
      trade_account_id, payment_date, amount, clearing_je_id, payment_account_code (required)
      import_id (required for bank-funded TAPs; None for credit-application TAPs)
      source_ta_id (required for credit-application TAPs; None otherwise)
      extra_metadata (optional dict merged into TAP.metadata — used by
        settlement-grouped flows to set application_method='settlement_payment',
        settlement_id, settlement_import_id, etc.)
    """
    payment_id = str(uuid.uuid4())

    metadata_dict = {
        'clearing_je_id': payment_data['clearing_je_id'],
        'payment_account_code': payment_data['payment_account_code'],
    }
    if payment_data.get('extra_metadata'):
        metadata_dict.update(payment_data['extra_metadata'])
    metadata = json.dumps(metadata_dict)

    conn.execute(
        """
        INSERT INTO trade_account_payments (
            id, trade_account_id, import_id, source_ta_id, payment_date,
            amount, sync, metadata, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, '{"status":"pending"}', ?, CURRENT_TIMESTAMP)
        """,
        (
            payment_id,
            payment_data['trade_account_id'],
            payment_data.get('import_id'),
            payment_data.get('source_ta_id'),
            payment_data['payment_date'],
            payment_data['amount'],
            metadata,
        )
    )

    conn.execute(
        """
        INSERT INTO audit_log (
            id, table_name, record_id, action,
            field_changes, reason, changed_by, changed_at
        )
        VALUES (?, 'trade_account_payments', ?, 'insert', ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            str(uuid.uuid4()),
            payment_id,
            json.dumps({
                'trade_account_id': payment_data['trade_account_id'],
                'amount': payment_data['amount'],
                'import_id': payment_data.get('import_id'),
                'source_ta_id': payment_data.get('source_ta_id'),
                'clearing_je_id': payment_data['clearing_je_id'],
            }),
            "Bulk payment applied to trade account",
            changed_by,
        )
    )

    return payment_id


def resolve_settlement_payments(conn, settlement_id, deposit_signed):
    """Resolve a settlement group into bank-funded payments + credit-application TAPs.

    Handles all four trade-account types in one settlement group:
      receivable (R) / credit_memo (CM)   → the A/R side (one Customer, a QBO Payment)
      payable (P)    / vendor_credit (VC)  → the A/P side (one Vendor, a QBO BillPayment)

    The settlement closes  net = ΣR − ΣCM − ΣP + ΣVC  against the SIGNED deposit
    (positive = bank deposit, negative = bank withdrawal). Each side's cash
    (ΣR−ΣCM and ΣP−ΣVC) is ≥ 0 by construction (the allocator raises otherwise);
    the two bank slices — A/R-side cash in, A/P-side cash out — net to the deposit.

    Returns a tuple (bank_payments, credit_apps, contact_by_side, summary):
      bank_payments  : [{'trade_account_id': <R|P id>, 'amount': cash_cents}]  (R cash, then P cash)
      credit_apps    : [{'source_ta_id': <CM|VC id>, 'target_ta_id': <R|P id>, 'amount': cents}]
      contact_by_side: {'customer': <name|None>, 'vendor': <name|None>}
      summary        : dict with per-type sums/counts, expected_net, anchors

    Validates: ≤1 contact PER SIDE (a different Customer and Vendor in one
    settlement is allowed); CMs need a receivable to anchor and VCs need a
    payable; expected_net == deposit_signed.

    Note: a fully-offset target (e.g. an R wholly consumed by CMs) yields a 0-cash
    bank_payment entry — kept in the list so the target is still cleared in the JE;
    the caller skips creating the empty TAP.
    """
    rows = conn.execute("""
        SELECT id, type, contact, document_date, journal_entry_id, metadata
        FROM trade_accounts
        WHERE json_extract(metadata, '$.settlement_id') = ?
          AND voided_at IS NULL
        ORDER BY document_date, id
    """, (settlement_id,)).fetchall()

    if not rows:
        raise ValueError(f"No TAs found with metadata.settlement_id = {settlement_id!r}")

    r_tas, cm_tas, p_tas, vc_tas = [], [], [], []
    for ta_id, ta_type, contact_name, doc_date, je_id, meta in rows:
        ta_meta = json.loads(meta) if meta else {}
        balance_account = ta_meta.get('balance_account_code')
        if not balance_account:
            raise ValueError(f"TA {ta_id}: missing balance_account_code")
        amt_due = compute_amount_due(conn, je_id, balance_account)
        # Bank-funded targets (R/P) track via compute_paid_amount; credit sources
        # (CM/VC) track consumption via compute_applied_amount.
        if ta_type in ('credit_memo', 'vendor_credit'):
            remaining = amt_due - compute_applied_amount(conn, ta_id)
        else:
            remaining = amt_due - compute_paid_amount(conn, ta_id)
        if remaining <= 0:
            continue  # already settled; skip
        rec = {'id': ta_id, 'remaining': remaining, 'doc_date': doc_date, 'contact': contact_name}
        if ta_type == 'receivable':
            r_tas.append(rec)
        elif ta_type == 'credit_memo':
            cm_tas.append(rec)
        elif ta_type == 'payable':
            p_tas.append(rec)
        elif ta_type == 'vendor_credit':
            vc_tas.append(rec)
        else:
            raise ValueError(
                f"TA {ta_id} type={ta_type!r} not supported in settlement-grouped apply-payment "
                f"(expected receivable / credit_memo / payable / vendor_credit)"
            )

    if not (r_tas or cm_tas or p_tas or vc_tas):
        raise ValueError(f"Settlement {settlement_id!r}: no open trade accounts (all already settled)")

    # Single contact PER SIDE: the Payment needs one Customer (A/R side), the
    # BillPayment needs one Vendor (A/P side). A different Customer and Vendor in
    # one settlement is allowed; fail loud if EITHER side spans >1 contact.
    customer_contacts = {t['contact'] for t in r_tas + cm_tas}
    vendor_contacts = {t['contact'] for t in p_tas + vc_tas}
    if len(customer_contacts) > 1:
        raise ValueError(
            f"Settlement A/R side (receivable/credit_memo) spans multiple contacts: "
            f"{sorted(customer_contacts)} — a Payment requires one Customer"
        )
    if len(vendor_contacts) > 1:
        raise ValueError(
            f"Settlement A/P side (payable/vendor_credit) spans multiple contacts: "
            f"{sorted(vendor_contacts)} — a BillPayment requires one Vendor"
        )
    customer = customer_contacts.pop() if customer_contacts else None
    vendor = vendor_contacts.pop() if vendor_contacts else None

    # Credits need a same-side target to anchor their application TAPs.
    if cm_tas and not r_tas:
        raise ValueError("Settlement has credit_memo(s) but no receivable to anchor the credit application")
    if vc_tas and not p_tas:
        raise ValueError("Settlement has vendor_credit(s) but no payable to anchor the vendor-credit application")

    sum_r = sum(t['remaining'] for t in r_tas)
    sum_cm = sum(t['remaining'] for t in cm_tas)
    sum_p = sum(t['remaining'] for t in p_tas)
    sum_vc = sum(t['remaining'] for t in vc_tas)
    expected_net = sum_r - sum_cm - sum_p + sum_vc
    if expected_net != deposit_signed:
        raise ValueError(
            f"Settlement net mismatch: sum_R={sum_r} sum_CM={sum_cm} sum_P={sum_p} "
            f"sum_VC={sum_vc} cents → expected_net={expected_net} cents, "
            f"deposit(signed)={deposit_signed} cents"
        )

    bank_payments = []
    credit_apps = []

    def _allocate_side(target_tas, credit_tas):
        """Distribute each target's cash after credit offsets onto bank_payments/credit_apps.

        Pure-target (no credits): one cash TAP per target at its full remaining.
        Mixed: the 2D largest-remainder allocator, reused verbatim for both the
        A/R side (R after CM) and the A/P side (P after VC).
        """
        if not target_tas:
            return
        if not credit_tas:
            for t in target_tas:
                bank_payments.append({'trade_account_id': t['id'], 'amount': t['remaining']})
            return
        tgt_rem = [t['remaining'] for t in target_tas]
        cr_rem = [c['remaining'] for c in credit_tas]
        cash, apps = allocate_settlement_2d(tgt_rem, cr_rem)
        for i, t in enumerate(target_tas):
            bank_payments.append({'trade_account_id': t['id'], 'amount': cash[i]})
        for i in range(len(target_tas)):
            for j in range(len(credit_tas)):
                credit_apps.append({
                    'source_ta_id': credit_tas[j]['id'],
                    'target_ta_id': target_tas[i]['id'],
                    'amount': apps[i][j],
                })

    _allocate_side(r_tas, cm_tas)   # A/R side: R cash after CM offsets
    _allocate_side(p_tas, vc_tas)   # A/P side: P cash after VC offsets

    summary = {
        'sum_r_cents': sum_r,
        'sum_cm_cents': sum_cm,
        'sum_p_cents': sum_p,
        'sum_vc_cents': sum_vc,
        'expected_net_cents': expected_net,
        'n_r': len(r_tas),
        'n_cm': len(cm_tas),
        'n_p': len(p_tas),
        'n_vc': len(vc_tas),
        'anchor_r_id': r_tas[0]['id'] if r_tas else None,
        'anchor_p_id': p_tas[0]['id'] if p_tas else None,
        'customer': customer,
        'vendor': vendor,
    }
    return bank_payments, credit_apps, {'customer': customer, 'vendor': vendor}, summary


def main():
    try:
        args = parse_arguments()

        try:
            adjustments = json.loads(args.adjustments)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid --adjustments JSON: {e}")
        if adjustments:
            raise ValueError(
                "--adjustments is retired. Use --standalone_lines for a separate posting that "
                "shares the wire, or create a first-class CreditMemo/VendorCredit "
                "(create_credit_memo.py / create_vendor_credit.py) and batch it with "
                "--allow_mixed_credit to reduce an invoice/bill."
            )

        try:
            standalone_lines = json.loads(args.standalone_lines)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid --standalone_lines JSON: {e}")
        if not isinstance(standalone_lines, list):
            raise ValueError("--standalone_lines must be a JSON array")
        for i, line in enumerate(standalone_lines):
            if not line.get('account_code'):
                raise ValueError(f"standalone_line {i+1}: missing account_code")
            amt = line.get('amount')
            if not isinstance(amt, int) or amt <= 0:
                raise ValueError(f"standalone_line {i+1}: amount must be a positive integer (cents)")
            if line.get('direction') not in ('debit', 'credit'):
                raise ValueError(f"standalone_line {i+1}: direction must be 'debit' or 'credit'")

        # Connect to database
        conn = sqlite3.connect(config_loader.get_db_path())
        conn.execute("PRAGMA foreign_keys = ON")

        try:
            # Validate import (checks exists, not processed, parses source)
            import_data = get_import_data(conn, args.import_id)

            # Standalone lines split this import across two JEs. Compute their net bank
            # impact now, so the settlement net-check and the clearing bank line both use
            # the SETTLEMENT cash (import minus the standalone slice), not the raw import.
            bank_account_code = import_data['bank_account_code']
            bank_direction = determine_direction(
                import_data['balance_type'], import_data['amount'], is_bank_account=True
            )
            import_signed = (abs(import_data['amount']) if bank_direction == 'debit'
                             else -abs(import_data['amount']))
            for i, line in enumerate(standalone_lines):
                if line['account_code'] == bank_account_code:
                    raise ValueError(
                        f"standalone_line {i+1}: cannot post to the bank account "
                        f"{bank_account_code} (that is the wire being split)"
                    )
            sl_debits = sum(l['amount'] for l in standalone_lines if l['direction'] == 'debit')
            sl_credits = sum(l['amount'] for l in standalone_lines if l['direction'] == 'credit')
            standalone_D = sl_debits - sl_credits  # the standalone JE's bank slice offsets this
            clearing_signed = import_signed + standalone_D  # settlement cash (signed)
            if standalone_lines and (
                clearing_signed == 0 or (clearing_signed > 0) != (import_signed > 0)
            ):
                raise ValueError(
                    "Standalone lines leave no settlement cash in the wire's direction "
                    f"(import {import_signed} cents, standalone net debit {standalone_D} cents). "
                    "For a bank entry with no trade-account settlement, use create_manual_journal.py."
                )

            # Settlement-grouped resolution: build payments[] + credit_apps[] from metadata.
            # In manual mode, payments[] comes from --payments and credit_apps is empty.
            credit_apps = []
            settlement_summary = None
            if args.auto_resolve_settlement:
                if not args.settlement_id:
                    raise ValueError("--settlement_id is required when --auto_resolve_settlement is set")
                if args.payments:
                    raise ValueError("--payments must NOT be provided when --auto_resolve_settlement is set")
                if not args.allow_mixed_credit:
                    raise ValueError("--allow_mixed_credit must be set with --auto_resolve_settlement (mixed R/CM/P/VC settlement net)")
                # Pass the SIGNED settlement cash: expected_net is compared signed, so a
                # net-withdrawal settlement (ΣP+ΣCM > ΣR+ΣVC) resolves as well as a deposit.
                bank_payments, credit_apps, contact_by_side, settlement_summary = (
                    resolve_settlement_payments(conn, args.settlement_id, clearing_signed)
                )
                payments = bank_payments
            else:
                if not args.payments:
                    raise ValueError("--payments is required when --auto_resolve_settlement is not set")
                try:
                    payments = json.loads(args.payments)
                except json.JSONDecodeError as e:
                    raise ValueError(f"Invalid --payments JSON: {e}")
                if not payments:
                    raise ValueError("--payments must be a non-empty array")

            # Validate all TAs — collect all errors before failing
            errors = []
            ta_cache = {}
            batch_totals = defaultdict(int)

            # First pass: validate individual payment entries
            for i, payment in enumerate(payments):
                ta_id = payment.get('trade_account_id')
                amount = payment.get('amount')

                if not ta_id or amount is None:
                    errors.append(f"Payment {i+1}: missing trade_account_id or amount")
                    continue

                # Settlement mode permits a 0-cash entry (a target fully offset by credits):
                # it still anchors the target in the clearing JE; the empty TAP is skipped later.
                min_amount = 0 if args.auto_resolve_settlement else 1
                if not isinstance(amount, int) or amount < min_amount:
                    sign_word = 'non-negative' if args.auto_resolve_settlement else 'positive'
                    errors.append(f"Payment {i+1} (TA {ta_id}): amount must be a {sign_word} integer (cents)")
                    continue

                batch_totals[ta_id] += amount

            # Second pass: validate each unique TA against balances
            for ta_id, batch_total in batch_totals.items():
                try:
                    trade_account = get_trade_account(conn, ta_id)
                    ta_cache[ta_id] = trade_account

                    # First-class credit_memo / vendor_credit TAs are applied to invoices/bills
                    # via apply_credit.py — UNLESS this is a settlement-grouped batch where they
                    # ride alongside R/P TAs and the deposit closes the net (--allow_mixed_credit).
                    if trade_account['type'] in ('credit_memo', 'vendor_credit') and not args.allow_mixed_credit:
                        errors.append(
                            f"TA {ta_id}: type='{trade_account['type']}' cannot be paid via "
                            f"apply_payments_bulk. Use apply_credit.py to apply credits to invoices/bills, "
                            f"or pass --allow_mixed_credit for settlement-grouped batches."
                        )
                        continue

                    balance_account_code = get_balance_account_code(trade_account)
                    if not balance_account_code:
                        errors.append(f"TA {ta_id}: missing balance_account_code in metadata")
                        continue

                    amount_due = compute_amount_due(
                        conn, trade_account['journal_entry_id'], balance_account_code
                    )
                    prior_payments = compute_paid_amount(conn, ta_id)
                    remaining = amount_due - prior_payments

                    if batch_total > remaining:
                        errors.append(
                            f"TA {ta_id}: batch total {batch_total} cents exceeds remaining "
                            f"{remaining} cents (due: {amount_due}, prior: {prior_payments})"
                        )

                    # Detect credit memo TAs for direction flipping during JE construction
                    ta_cache[ta_id]['is_credit_memo'] = is_credit_memo(
                        conn, trade_account['journal_entry_id'],
                        balance_account_code, trade_account['type']
                    )

                except ValueError as e:
                    errors.append(str(e))

            # Validate credit-source TAs (CMs/VCs being consumed by the settlement deposit).
            # Their TAPs are credit-applications: import_id=NULL, source_ta_id=CM|VC.id, and
            # trade_account_id FK to the same-side target (R for CM, P for VC).
            credit_src_cache = {}
            for ca in credit_apps:
                src_id = ca['source_ta_id']
                try:
                    src_ta = get_trade_account(conn, src_id)
                    if src_ta['type'] not in ('credit_memo', 'vendor_credit'):
                        errors.append(f"credit_app source {src_id} is type={src_ta['type']!r}, expected 'credit_memo' or 'vendor_credit'")
                        continue
                    bac = get_balance_account_code(src_ta)
                    if not bac:
                        errors.append(f"credit source {src_id}: missing balance_account_code in metadata")
                        continue
                    # Credit sources track consumption via applied_amount (TAPs keyed by source_ta_id).
                    src_remaining = compute_amount_due(conn, src_ta['journal_entry_id'], bac) - compute_applied_amount(conn, src_id)
                    if ca['amount'] > src_remaining:
                        errors.append(
                            f"credit source {src_id}: credit-app amount {ca['amount']} exceeds remaining {src_remaining}"
                        )
                    credit_src_cache[src_id] = src_ta
                except ValueError as e:
                    errors.append(str(e))

            if errors:
                print(json.dumps({"success": False, "errors": errors}, indent=2))
                sys.exit(1)

            # Build compound clearing JE postings
            postings = []

            # Bank line — the SETTLEMENT cash only (import minus the standalone slice),
            # computed as a signed value above; direction follows its sign.
            clearing_bank_direction = 'debit' if clearing_signed > 0 else 'credit'
            postings.append({
                'account_code': bank_account_code,
                'direction': clearing_bank_direction,
                'amount': abs(clearing_signed),
                'contact': None,
                'description': f"Bulk payment clearing - {len(payments)} trade accounts",
            })

            # JE construction path depends on mode.
            # Settlement mode (any auto-resolved settlement): build TA-level postings —
            # one per bank-funded target (R/P) at its remaining, one per credit source
            # (CM/VC) at its remaining — so the JE shape is independent of per-TAP
            # allocation. With a single net bank line, this balances both signs:
            # DR = ΣCM+ΣP+(net if>0) and CR = ΣR+ΣVC+(-net if<0) both reduce to ΣR+ΣVC.
            # Manual --payments mode keeps the per-TAP construction in the else branch.
            settlement_mode = args.auto_resolve_settlement
            if settlement_mode:
                # Bank-funded targets clear at their full remaining: R → CR A/R, P → DR A/P.
                # ta_cache holds the R/P TAs (incl. any 0-cash, fully-offset target).
                for ta_id, trade_account in ta_cache.items():
                    bac = get_balance_account_code(trade_account)
                    remaining = compute_amount_due(conn, trade_account['journal_entry_id'], bac) - compute_paid_amount(conn, ta_id)
                    if trade_account['type'] == 'payable':
                        direction, label = 'debit', 'Clear A/P'
                    else:  # receivable
                        direction, label = 'credit', 'Clear A/R'
                    postings.append({
                        'account_code': bac,
                        'direction': direction,
                        'amount': remaining,
                        'contact': trade_account['contact'],
                        'description': f"{label} - {trade_account['contact']}",
                    })
                # Credit sources are consumed at their full remaining: CM → DR A/R, VC → CR A/P.
                for src_id, src_ta in credit_src_cache.items():
                    bac = get_balance_account_code(src_ta)
                    remaining = compute_amount_due(conn, src_ta['journal_entry_id'], bac) - compute_applied_amount(conn, src_id)
                    if src_ta['type'] == 'vendor_credit':
                        direction, label = 'credit', 'Apply vendor credit (settlement)'
                    else:  # credit_memo
                        direction, label = 'debit', 'Apply credit memo (settlement)'
                    postings.append({
                        'account_code': bac,
                        'direction': direction,
                        'amount': remaining,
                        'contact': src_ta['contact'],
                        'description': f"{label} - {src_ta['contact']}",
                    })
            else:
                # TA lines — one per payment entry
                for payment in payments:
                    ta_id = payment['trade_account_id']
                    trade_account = ta_cache[ta_id]
                    balance_account_code = get_balance_account_code(trade_account)
                    credit_memo = trade_account.get('is_credit_memo', False)

                    # Per-TA direction:
                    #   receivable      → CR balance (clear A/R)
                    #   payable         → DR balance (clear A/P)
                    #   credit_memo     → DR balance (consume the customer credit, A/R)
                    #   vendor_credit   → CR balance (consume the vendor credit, A/P)
                    #   legacy reverse-direction TAs (is_credit_memo flag): flip
                    ta_type = trade_account['type']
                    if ta_type == 'credit_memo':
                        direction = 'debit'
                        desc = f"Apply credit memo - {trade_account['contact']}"
                    elif ta_type == 'vendor_credit':
                        direction = 'credit'
                        desc = f"Apply vendor credit - {trade_account['contact']}"
                    elif ta_type == 'receivable':
                        direction = 'debit' if credit_memo else 'credit'
                        desc = f"Apply credit memo - {trade_account['contact']}" if credit_memo else f"Clear A/R - {trade_account['contact']}"
                    else:  # payable
                        direction = 'credit' if credit_memo else 'debit'
                        desc = f"Apply credit memo - {trade_account['contact']}" if credit_memo else f"Clear A/P - {trade_account['contact']}"

                    postings.append({
                        'account_code': balance_account_code,
                        'direction': direction,
                        'amount': payment['amount'],
                        'contact': trade_account['contact'],
                        'description': desc,
                    })

                # No credit-application postings here: this branch is manual --payments
                # mode only, where credit_apps is always empty (credits ride a settlement
                # via --auto_resolve_settlement, handled by the settlement_mode branch above).

            # Create the compound clearing JE (raises ValueError if debits != credits)
            memo = f"Bulk payment - {args.import_id}"
            clearing_je_id = create_journal_entry_direct(
                conn, args.payment_date, memo, postings
            )

            # Mark clearing JE as 'ignore' immediately — it should never be published
            # as a standalone JE. The Payment/BillPayment object handles the QBO side.
            conn.execute(
                "UPDATE journal_entries SET sync = ? WHERE id = ?",
                ('{"status":"ignore"}', clearing_je_id)
            )

            # Standalone lines publish as their OWN journal entry (sync=pending), split
            # from the same import. Its bank slice + the clearing bank line net to the import.
            standalone_je_id = None
            if standalone_lines:
                standalone_postings = []
                for line in standalone_lines:
                    p = {
                        'account_code': line['account_code'],
                        'direction': line['direction'],
                        'amount': line['amount'],
                        'contact': line.get('contact'),
                        'description': line.get('description', ''),
                    }
                    if line.get('class_name'):
                        p['class_name'] = line['class_name']
                    standalone_postings.append(p)
                if standalone_D != 0:
                    # Balancing bank slice (offsets the standalone lines' net debit/credit)
                    standalone_postings.append({
                        'account_code': bank_account_code,
                        'direction': 'credit' if standalone_D > 0 else 'debit',
                        'amount': abs(standalone_D),
                        'contact': None,
                        'description': f"Standalone co-disbursement (rode import {args.import_id})",
                    })
                standalone_je_id = create_journal_entry_direct(
                    conn, args.payment_date,
                    f"Standalone co-disbursement - {args.import_id}",
                    standalone_postings,
                    je_metadata={'source_import_id': args.import_id},
                )

            # Create payment records per TA
            payment_results = []
            for payment in payments:
                ta_id = payment['trade_account_id']
                # A 0-cash entry (a target fully offset by credits) is anchored in the
                # clearing JE above but moves no cash — skip creating the empty TAP.
                if payment['amount'] == 0:
                    continue
                trade_account = ta_cache[ta_id]
                balance_account_code = get_balance_account_code(trade_account)

                payment_data = {
                    'trade_account_id': ta_id,
                    'import_id': args.import_id,
                    'payment_date': args.payment_date,
                    'amount': payment['amount'],
                    'clearing_je_id': clearing_je_id,
                    'payment_account_code': import_data['bank_account_code'],
                }
                if args.auto_resolve_settlement and args.settlement_id:
                    # Tag bank-funded R/P TAPs with settlement_id so payments.py (R) and
                    # bill_payments.py (P) can each consolidate them with their sibling
                    # CM/VC credit-app TAPs into one mixed Payment / BillPayment.
                    payment_data['extra_metadata'] = {'settlement_id': args.settlement_id}

                payment_id = create_payment(conn, payment_data, args.changed_by)

                # Compute updated balances (includes payments just inserted in this tx)
                amount_due = compute_amount_due(
                    conn, trade_account['journal_entry_id'], balance_account_code
                )
                new_paid = compute_paid_amount(conn, ta_id)
                remaining = amount_due - new_paid
                status = compute_status(amount_due, new_paid)

                payment_results.append({
                    'payment_id': payment_id,
                    'trade_account_id': ta_id,
                    'amount_applied': payment['amount'],
                    'amount_due': amount_due,
                    'prior_payments': new_paid - payment['amount'],
                    'new_paid_amount': new_paid,
                    'remaining_balance': remaining,
                    'status': status,
                    'is_credit_memo': trade_account.get('is_credit_memo', False),
                })

            # Credit-application TAPs (CMs/VCs consumed by the settlement deposit).
            # These have import_id=NULL and source_ta_id=CM|VC.id. trade_account_id is
            # the specific same-side target this credit-app contributes to (R for a CM,
            # P for a VC; set by the allocator). The publishers discover them via
            # metadata.application_method='settlement_payment' and aggregate by
            # source_external_id when emitting CreditMemo / VendorCredit LinkedTxn lines.
            credit_app_results = []
            if credit_apps:
                for ca in credit_apps:
                    target_ta_id = ca.get('target_ta_id')
                    if not target_ta_id:
                        raise ValueError(
                            f"credit_app for source_ta_id={ca['source_ta_id']} missing target_ta_id "
                            f"(expected from allocate_settlement_2d output shape)"
                        )
                    src_ta = credit_src_cache[ca['source_ta_id']]  # existence-checked in validation
                    payment_data = {
                        'trade_account_id': target_ta_id,
                        'import_id': None,
                        'source_ta_id': ca['source_ta_id'],
                        'payment_date': args.payment_date,
                        'amount': ca['amount'],
                        'clearing_je_id': clearing_je_id,
                        'payment_account_code': import_data['bank_account_code'],
                        'extra_metadata': {
                            'application_method': 'settlement_payment',
                            'settlement_id': args.settlement_id,
                            'settlement_import_id': args.import_id,
                        },
                    }
                    payment_id = create_payment(conn, payment_data, args.changed_by)
                    credit_app_results.append({
                        'payment_id': payment_id,
                        'source_ta_id': ca['source_ta_id'],
                        'target_ta_id': target_ta_id,
                        'amount_applied': ca['amount'],
                    })

            # Mark import processed
            conn.execute(
                "UPDATE imports SET processed = 1 WHERE id = ?",
                (args.import_id,)
            )

            # Commit everything atomically
            conn.commit()

            result = {
                "success": True,
                "clearing_je_id": clearing_je_id,
                "standalone_je_id": standalone_je_id,
                "import_id": args.import_id,
                "import_total_cents": abs(import_data['amount']),
                "clearing_bank_cents": abs(clearing_signed),
                "standalone_bank_cents": abs(standalone_D),
                "payments_created": len(payment_results),
                "payments": payment_results,
                "credit_apps_created": len(credit_app_results),
                "credit_apps": credit_app_results,
            }
            if settlement_summary:
                result["settlement"] = settlement_summary
            print(json.dumps(result, indent=2))
            sys.exit(0)

        except Exception as e:
            conn.rollback()
            raise
        finally:
            conn.close()

    except ValueError as e:
        print(json.dumps({
            "success": False,
            "errors": [str(e)]
        }, indent=2))
        sys.exit(1)

    except Exception as e:
        print(json.dumps({
            "success": False,
            "errors": [f"Unexpected error: {str(e)}"]
        }, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
