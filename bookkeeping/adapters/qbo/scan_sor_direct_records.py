#!/usr/bin/env python3
"""
Scan SoR (QBO) for direct records — the CLOSING bracket of the SoR↔staging reconcile.

PRE-PUBLISH detector. Finds QBO transaction records dated in [--period_start, --period_end]
that LACK our "[bk:<key>]" idempotency tag — records created directly in QBO (manual entry,
bank feed) or by a sync that failed to stamp, i.e. things our staging pipeline did NOT create.
Surfacing them BEFORE Publish is what stops the publisher from creating a DUPLICATE of a
transaction QBO already holds.

This is the closing half of the period's SoR↔staging reconciliation bracket:
  • OPENING  (pre-Ingest):  reconcile_trial_balance.py --as_of <prior close>  — catches drift
                            since the last seal (e.g. a post-seal account reclass).
  • CLOSING  (pre-Publish): THIS script — detect SoR records the pipeline never made, before
                            we commit, so we never double-post. (Then the post-publish
                            0-variance reconcile_trial_balance.py --as_of <period end> proves
                            the commit landed exactly.)

## How "ours" is recognized — the [bk:] tag convention
The publisher stamps every object it CREATES with a deterministic idempotency tag
"[bk:<key>]" (see _shared/locate.py::make_tag; <key> is a stable local record id). It is
written to **PrivateNote** on every entity type the publisher creates — JournalEntry,
Invoice, Bill, CreditMemo, VendorCredit, Payment, BillPayment — and ADDITIONALLY to
**DocNumber** on Invoice/Bill/CreditMemo/VendorCredit when the adapter supplies no document
number (QBO's duplicate-DocNumber enforcement then itself hard-blocks a double-post). So a
record carries our publish tag iff the substring "[bk:" appears in its PrivateNote OR its
DocNumber.

PrivateNote is NOT queryable in QBO's query language, so — exactly as locate.py does — we
query each entity over the queryable TxnDate window and match the tag CLIENT-SIDE, paging to
exhaustion (a single truncated page would read as a false "clean").

## "Accounted" = the publish tag OR a local link OR a void (three signals)
The tag is not the only way a QBO record can already be ours. Records created by
/wholesale-invoice, or ADOPTED from a direct QBO post during a close, carry no "[bk:]" tag
yet are fully accounted for: a local trade_account / trade_account_payment / journal_entry
already points at them via sync.external_id (status synced|ignore — the same already-synced
linkage the publisher's guard relies on: publish.py only ever selects pending|error rows, so
a synced|ignore row is in QBO and will never be re-created). Such a record is NOT a
double-post risk, so re-flagging it every close is noise. A record therefore counts as
ACCOUNTED iff it carries the "[bk:]" tag OR is claimed by a local external_id link OR is
VOIDED (below); everything else in the window is a genuine direct record to reconcile before
publishing.

A VOIDED QBO record is also accounted-benign. QBO voids a transaction by zeroing its amounts
and writing "Voided" into PrivateNote (its convention); the publisher can never duplicate a
void and a void has zero GL impact, so it is not a Hard Stop 7 concern. Recognized by the
CONJUNCTION — "Voided" in PrivateNote AND TotalAmt == 0 AND Balance in {0, absent} AND no
LinkedTxn (see is_voided_benign). This is a nullification STATUS, NOT a $0-amount blanket: an
active paid invoice also reads Balance 0 (verified live on Inv 3057), so the "Voided" marker —
not the zero — is the decider; the zero/no-link conjuncts only guard against a stray "Voided"
note on a real record. A non-voided $0 record STILL surfaces.

The link check is TYPE-AWARE: QBO ids are unique only WITHIN an entity type (a Payment and a
JournalEntry both carry id "1055" in this realm — a real collision), so a local row may vouch
for a QBO record only of its OWN entity type, never a same-numbered record of a different
type. trade_account.type maps to the QBO entity (receivable→Invoice, payable→Bill,
credit_memo→CreditMemo, vendor_credit→VendorCredit); a payment's QBO type follows its parent
TA (customer-side→Payment, vendor-side→BillPayment); journal_entries map to JournalEntry, but
TA-linked JEs are EXCLUDED (they share their TA's Invoice/Bill external_id — already covered
by the TA, and their QBO object is not a JournalEntry). Accounted-by-link records are
suppressed from the gate but reported in `linked_untagged_records` (and voided records in
`voided_benign_records`) for transparency. A truly orphan record (no tag, no local link, not
voided) STILL surfaces — that is the whole point of HS7.

## Entity coverage
Scans every transaction entity the publisher tags, PLUS Deposit and Purchase — two common
direct-entry types the publisher NEVER creates, so any of them in the window is inherently a
direct record (they always surface; that is the intended signal — a bank-feed Deposit or a
directly-entered check/expense the pipeline would otherwise double-book). Narrow with
--entity_types when a client's workflow calls for it.

READ-ONLY: no QBO writes; the local staging DB is opened read-only (sqlite mode=ro) solely to
read sync.external_id links — no local writes. Reuses the /qbo OAuth client and the
{local_dir}/adapters/.env auth pattern from reconcile_trial_balance.py, and
config_loader.get_db_path() for the staging DB (as publish.py does).

Output (stdout JSON):
  {success, period, entity_types_scanned, scanned_counts_by_type, untagged_count,
   untagged_records: [{type, id, txn_date, amount, name_or_memo}],
   linked_untagged_count,
   linked_untagged_records: [{type, id, txn_date, amount, name_or_memo, linked_via}],
   voided_benign_count,
   voided_benign_records: [{type, id, txn_date, amount, name_or_memo, void_marker}],
   summary}
  untagged_records        = genuine direct records (no [bk:] tag, no local link, not voided) —
                            the GATE.
  linked_untagged_records = no [bk:] tag but claimed by a local external_id link (e.g.
                            /wholesale-invoice or adopted records); accounted — informational,
                            NOT a gate. linked_via names the claiming local table(s).
  voided_benign_records   = no [bk:] tag, not linked, but VOIDED in QBO (nullified, zero
                            impact); accounted — informational, NOT a gate. void_marker is the
                            PrivateNote marker that classified it.
  success = scan completed AND the closing gate is clear (zero genuine direct records).

Exit code: 0 = gate clear; 1 = untagged records found (Hard Stop — resolve before Publish)
           OR the scan itself failed (then an `error` key is present, untagged_records absent).

Usage:
    BOOKKEEPING_CONFIG_PATH=_local-bookkeeping/config.yaml \
      {python} {module_root}/adapters/qbo/scan_sor_direct_records.py \
      --period_start 2026-05-24 --period_end 2026-06-20 [--entity_types JournalEntry,Invoice]
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime

# Bootstrap config — resolve paths relative to the qbo/ adapter directory (mirrors
# reconcile_trial_balance.py).
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(script_dir, '..', '..', 'scripts', '_shared'))
sys.path.insert(0, script_dir)  # For _shared.client imports
import config_loader

from _shared.client import (
    validate_qbo_env_vars, create_qbo_client, test_qbo_connection,
    refresh_client, save_tokens_if_available, QBORateLimiter,
)
from dotenv import load_dotenv

# SDK entity classes — re-exported from the package top level (as qbo_client.py imports them).
from quickbooks.objects import (
    JournalEntry, Invoice, Bill, CreditMemo, VendorCredit,
    Payment, BillPayment, Deposit, Purchase,
)

_config = config_loader.load_config()
ENV_PATH = os.path.join(_config['local_dir'], 'adapters', '.env')
load_dotenv(ENV_PATH)

TAG_TOKEN = '[bk:'   # the publisher's idempotency-tag prefix (see _shared/locate.py)
_PAGE_SIZE = 100

# Entity types the publisher TAGS — a record here WITHOUT [bk: is a direct entry. Plus the two
# direct-entry-prone types the publisher NEVER creates (Deposit, Purchase — always surfaced).
TAGGED_BY_PUBLISHER = ('JournalEntry', 'Invoice', 'Bill', 'CreditMemo',
                       'VendorCredit', 'Payment', 'BillPayment')
ENTITY_MAP = {
    'JournalEntry': JournalEntry,
    'Invoice': Invoice,
    'Bill': Bill,
    'CreditMemo': CreditMemo,
    'VendorCredit': VendorCredit,
    'Payment': Payment,
    'BillPayment': BillPayment,
    'Deposit': Deposit,      # never created by the publisher
    'Purchase': Purchase,    # never created by the publisher
}

# A local trade_account.type identifies the QBO entity its external_id refers to. The link
# check MUST be type-aware: QBO ids are unique only within an entity type (a Payment and a
# JournalEntry both carry id "1055" in this realm), so a local row may vouch for a QBO record
# only of its OWN entity type — never a same-numbered record of a different type.
_TA_TYPE_TO_QBO = {
    'receivable': 'Invoice',
    'payable': 'Bill',
    'credit_memo': 'CreditMemo',
    'vendor_credit': 'VendorCredit',
}


def load_local_links(db_path):
    """Build {qbo_entity_type: {external_id: {source_label, ...}}} from the staging DB.

    A QBO record is already ours — accounted, never to be re-published — when a local
    trade_account / trade_account_payment / journal_entry carries its id in sync.external_id
    with status synced|ignore. That is the same already-synced linkage the publisher's guard
    relies on (publish.py only ever selects pending|error rows), so a synced|ignore row is in
    QBO and will never be re-created. READ-ONLY: opens the DB in sqlite mode=ro.

    Type-aware (see _TA_TYPE_TO_QBO): trade_accounts → Invoice/Bill/CreditMemo/VendorCredit by
    type; trade_account_payments → Payment (customer-side parent) or BillPayment (vendor-side);
    journal_entries → JournalEntry, EXCLUDING TA-linked JEs (they share their TA's Invoice/Bill
    external_id — already covered by the TA, and their QBO object is not a JournalEntry).
    """
    links = {t: {} for t in ENTITY_MAP}

    def _add(qbo_type, ext_id, label):
        if not ext_id:
            return
        links[qbo_type].setdefault(str(ext_id), set()).add(label)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        # trade_accounts → Invoice / Bill / CreditMemo / VendorCredit (by ta.type)
        for r in conn.execute(
                "SELECT type, json_extract(sync,'$.external_id') AS ext "
                "FROM trade_accounts "
                "WHERE json_extract(sync,'$.external_id') IS NOT NULL "
                "  AND json_extract(sync,'$.status') IN ('synced','ignore')"):
            qbo_type = _TA_TYPE_TO_QBO.get(r['type'])
            if qbo_type:
                _add(qbo_type, r['ext'], f"trade_accounts({r['type']})")
        # trade_account_payments → Payment (customer-side) / BillPayment (vendor-side parent)
        for r in conn.execute(
                "SELECT ta.type AS parent_type, json_extract(p.sync,'$.external_id') AS ext "
                "FROM trade_account_payments p "
                "JOIN trade_accounts ta ON ta.id = p.trade_account_id "
                "WHERE json_extract(p.sync,'$.external_id') IS NOT NULL "
                "  AND json_extract(p.sync,'$.status') IN ('synced','ignore')"):
            qbo_type = 'BillPayment' if r['parent_type'] in ('payable', 'vendor_credit') else 'Payment'
            _add(qbo_type, r['ext'], 'trade_account_payments')
        # journal_entries → JournalEntry, EXCLUDING TA-linked JEs (covered by their TA above)
        for r in conn.execute(
                "SELECT json_extract(sync,'$.external_id') AS ext "
                "FROM journal_entries "
                "WHERE json_extract(sync,'$.external_id') IS NOT NULL "
                "  AND json_extract(sync,'$.status') IN ('synced','ignore') "
                "  AND id NOT IN (SELECT journal_entry_id FROM trade_accounts "
                "                 WHERE journal_entry_id IS NOT NULL)"):
            _add('JournalEntry', r['ext'], 'journal_entries')
        return links
    finally:
        conn.close()


def parse_args():
    p = argparse.ArgumentParser(
        description='Pre-Publish scan for QBO records lacking our [bk:] publish tag.')
    p.add_argument('--period_start', required=True,
                   help='Period start YYYY-MM-DD (inclusive TxnDate floor).')
    p.add_argument('--period_end', required=True,
                   help='Period end YYYY-MM-DD (inclusive TxnDate ceiling).')
    p.add_argument('--entity_types', default=None,
                   help='Comma-separated subset to scan (default: all). Valid: '
                        + ', '.join(ENTITY_MAP))
    return p.parse_args()


def has_bk_tag(obj) -> bool:
    """A record is ours iff the [bk:] tag appears in PrivateNote or DocNumber."""
    note = getattr(obj, 'PrivateNote', None) or ''
    doc = getattr(obj, 'DocNumber', None) or ''
    return TAG_TOKEN in note or TAG_TOKEN in doc


def is_voided_benign(obj) -> bool:
    """A VOIDED QBO record is accounted-benign — it can't be duplicated and has zero GL impact,
    so it is not a Hard Stop 7 concern.

    QBO voids a transaction by zeroing its amounts and writing "Voided" into PrivateNote (its
    convention). We require the CONJUNCTION, because the marker is a nullification STATUS, not a
    $0-amount blanket (an active *paid* invoice also reads Balance 0 — verified live on Inv
    3057): "Voided" in PrivateNote AND TotalAmt == 0 AND Balance in {0, absent} AND no
    LinkedTxn. The "Voided" marker is the decider; the zero/no-link conjuncts only guard against
    a stray "Voided" note left on a real record. Balance is absent on some entity types
    (Payment/JournalEntry/Deposit) → absent counts as "nothing outstanding"; a Balance-bearing
    type (Invoice/Bill/CreditMemo) must read exactly 0. A non-voided $0 record returns False
    (no marker) and STILL surfaces.
    """
    note = getattr(obj, 'PrivateNote', None) or ''
    if 'Voided' not in note:
        return False
    total = getattr(obj, 'TotalAmt', None)
    try:
        if total is None or float(total) != 0:
            return False
    except (TypeError, ValueError):
        return False
    balance = getattr(obj, 'Balance', None)
    if balance is not None:
        try:
            if float(balance) != 0:
                return False
        except (TypeError, ValueError):
            return False
    if getattr(obj, 'LinkedTxn', None):  # any applied/linking txn → not a clean void
        return False
    return True


def _ref_name(obj, attr) -> str:
    ref = getattr(obj, attr, None)
    return (getattr(ref, 'name', None) or '') if ref is not None else ''


def _name_or_memo(obj) -> str:
    """Best human identifier: counterparty name, else memo (PrivateNote), else DocNumber."""
    for attr in ('CustomerRef', 'VendorRef', 'EntityRef'):
        nm = _ref_name(obj, attr)
        if nm:
            return nm
    note = (getattr(obj, 'PrivateNote', None) or '').strip()
    if note:
        return note
    return (getattr(obj, 'DocNumber', None) or '').strip()


def _amount(obj, type_name):
    """Magnitude for display. TotalAmt for most entities; JournalEntry carries none, so sum
    its debit legs (its balanced magnitude)."""
    total = getattr(obj, 'TotalAmt', None)
    if total is not None:
        try:
            return round(float(total), 2)
        except (TypeError, ValueError):
            pass
    if type_name == 'JournalEntry':
        debit = 0.0
        for line in (getattr(obj, 'Line', None) or []):
            detail = getattr(line, 'JournalEntryLineDetail', None)
            if detail is not None and getattr(detail, 'PostingType', None) == 'Debit':
                try:
                    debit += float(getattr(line, 'Amount', 0) or 0)
                except (TypeError, ValueError):
                    pass
        return round(debit, 2)
    return None


def query_window(entity_cls, period_start, period_end, client, rate_limiter):
    """Page an entity to exhaustion over TxnDate in [period_start, period_end].

    Mirrors _shared/locate.py::_query_all — never trust a single truncated page; a record
    beyond page 1 would otherwise read as a false 'clean'. Raises on query failure (fail
    loud); main() turns that into a structured error.
    """
    where_clause = f"TxnDate >= '{period_start}' AND TxnDate <= '{period_end}'"
    results = []
    start = 1
    while True:
        rate_limiter.wait()
        page = entity_cls.where(where_clause, start_position=start,
                                max_results=_PAGE_SIZE, qb=client) or []
        results.extend(page)
        if len(page) < _PAGE_SIZE:
            return results
        start += _PAGE_SIZE


def main():
    args = parse_args()

    for value, label in ((args.period_start, 'period_start'), (args.period_end, 'period_end')):
        try:
            datetime.strptime(value, '%Y-%m-%d')
        except ValueError:
            print(json.dumps({'success': False,
                              'error': f"{label} must be YYYY-MM-DD, got {value!r}"}))
            sys.exit(1)

    if args.entity_types:
        entity_types = [t.strip() for t in args.entity_types.split(',') if t.strip()]
        unknown = [t for t in entity_types if t not in ENTITY_MAP]
        if unknown:
            print(json.dumps({'success': False,
                              'error': f"unknown entity_types {unknown}; valid: {list(ENTITY_MAP)}"}))
            sys.exit(1)
    else:
        entity_types = list(ENTITY_MAP)

    log = lambda m: print(m, file=sys.stderr)
    log(f"Closing-bracket SoR scan {args.period_start}..{args.period_end} "
        f"({len(entity_types)} entity types)")

    # --- Local links (READ-ONLY): which QBO ids a local TA/JE/TAP already claims. Built up
    #     front so a DB problem fails fast, before any QBO calls. Fail LOUD — never silently
    #     fall back to "no links", which would resurrect the false positives this guards. ---
    try:
        links = load_local_links(config_loader.get_db_path())
    except Exception as e:
        print(json.dumps({'success': False,
                          'error': f"local link load failed: {e}"})); sys.exit(1)
    n_links = sum(len(v) for v in links.values())
    log(f"Loaded {n_links} local external_id link(s) (status synced|ignore) for the link check")

    # --- Auth (mirror reconcile_trial_balance.py: one up-front refresh for a multi-pull run) ---
    try:
        credentials = validate_qbo_env_vars()
        client, error = create_qbo_client(credentials)
        if error:
            print(json.dumps({'success': False, 'error': error})); sys.exit(1)
        client, error = refresh_client(client)  # force a fresh token up front (many pulls)
        if error:
            print(json.dumps({'success': False, 'error': f"token refresh failed: {error}"})); sys.exit(1)
        save_tokens_if_available(client, ENV_PATH)
        ok, msg = test_qbo_connection(client, ENV_PATH)
        if not ok:
            print(json.dumps({'success': False, 'error': msg})); sys.exit(1)
        log(msg)
    except Exception as e:
        print(json.dumps({'success': False, 'error': f"auth/setup failed: {e}"})); sys.exit(1)

    # --- Scan (READ-ONLY): page each entity over the window, match the tag client-side ---
    rate_limiter = QBORateLimiter()
    scanned_counts = {}
    untagged = []          # genuine direct records (no tag, no link, not voided) — the GATE
    linked_untagged = []   # no tag but claimed by a local external_id link — informational
    voided_benign = []     # no tag, not linked, but VOIDED in QBO — informational
    try:
        for type_name in entity_types:
            records = query_window(ENTITY_MAP[type_name], args.period_start,
                                   args.period_end, client, rate_limiter)
            save_tokens_if_available(client, ENV_PATH)
            scanned_counts[type_name] = len(records)
            type_links = links.get(type_name, {})
            n_untagged = 0
            n_linked = 0
            n_voided = 0
            for obj in records:
                if has_bk_tag(obj):
                    continue
                rec_id = str(getattr(obj, 'Id', '') or '')
                rec = {
                    'type': type_name,
                    'id': rec_id,
                    'txn_date': getattr(obj, 'TxnDate', None) or '',
                    'amount': _amount(obj, type_name),
                    'name_or_memo': _name_or_memo(obj),
                }
                claim = type_links.get(rec_id)
                if claim:                       # accounted by a local external_id link
                    rec['linked_via'] = '+'.join(sorted(claim))
                    linked_untagged.append(rec)
                    n_linked += 1
                    continue
                if is_voided_benign(obj):       # voided in QBO — nullified, zero impact
                    rec['void_marker'] = (getattr(obj, 'PrivateNote', None) or '').strip()
                    voided_benign.append(rec)
                    n_voided += 1
                    continue
                untagged.append(rec)            # genuine direct record — surfaces the gate
                n_untagged += 1
            note = ('  (publisher never tags this type — all are direct)'
                    if type_name not in TAGGED_BY_PUBLISHER and n_untagged else '')
            linknote = f'  (+{n_linked} linked-accounted)' if n_linked else ''
            voidnote = f'  (+{n_voided} voided-benign)' if n_voided else ''
            log(f"  {type_name:<14} scanned {len(records):>4}  direct {n_untagged:>4}"
                f"{note}{linknote}{voidnote}")
    except Exception as e:
        print(json.dumps({'success': False, 'error': f"scan failed: {e}",
                          'scanned_counts_by_type': scanned_counts})); sys.exit(1)

    success = len(untagged) == 0
    total_scanned = sum(scanned_counts.values())
    extra = ""
    if linked_untagged:
        extra += (f" {len(linked_untagged)} untagged record(s) recognized as accounted via a "
                  f"local external_id link (see linked_untagged_records).")
    if voided_benign:
        extra += (f" {len(voided_benign)} untagged record(s) recognized as benign because they "
                  f"are VOIDED in QBO (see voided_benign_records).")
    if success:
        summary = (f"CLEAR — all {total_scanned} record(s) in {args.period_start}.."
                   f"{args.period_end} are accounted (carry the [bk:] publish tag, a local "
                   f"external_id link, or a QBO void); no genuine direct SoR records. Closing "
                   f"gate clear.{extra}")
    else:
        summary = (f"HARD STOP — {len(untagged)} record(s) dated in {args.period_start}.."
                   f"{args.period_end} are genuine direct entries (no [bk:] publish tag, no "
                   f"local external_id link, not voided) — direct QBO entries / failed syncs. "
                   f"Resolve before Publish so the publisher does not double-post.{extra}")

    log("\n" + "=" * 64)
    log(summary)

    result = {
        'success': success,
        'period': [args.period_start, args.period_end],
        'entity_types_scanned': entity_types,
        'scanned_counts_by_type': scanned_counts,
        'untagged_count': len(untagged),
        'untagged_records': untagged,
        'linked_untagged_count': len(linked_untagged),
        'linked_untagged_records': linked_untagged,
        'voided_benign_count': len(voided_benign),
        'voided_benign_records': voided_benign,
        'summary': summary,
    }
    print(json.dumps(result, indent=2))
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
