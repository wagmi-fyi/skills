#!/usr/bin/env python3
"""
Aged Trade Accounts Report
Point-in-time aging of open trade accounts, rolled up by counterparty + document
group (settlement/payout/invoice), aged by due date. The formal artifact for
Review Check 2 (Trade Accounts Aging).

Semantics:
- The open set is bounded by --as_of on BOTH sides: trade accounts whose origin
  journal entry is dated on/before as_of, net of payments/applications dated
  on/before as_of. This matches the Check 11 subledger-to-GL math, so the
  report's subledger_total ties to the control-account GL balance at as_of.
- Credit memos / vendor credits net via BOTH legitimate consumption forms:
  credit applications (TAPs with source_ta_id) and direct/owner-cleared
  settlement (TAPs on the CM/VC itself with no source_ta_id) — matching the
  publisher's semantics (see review-checks.md Check 11). Counting only one
  form creates a phantom subledger-to-GL gap that does not exist in the SoR.
- Groups netting at or below zero are residual credits: reported separately,
  never aged as receivables/payables.

Read-only: opens the database in SQLite read-only mode. Never writes.
"""

import argparse
import csv
import json
import sqlite3
import sys
from datetime import date

from _shared import config_loader

DEFAULT_GROUP_KEYS = "settlement_id,payout_id,order_num,invoice_num,doc_number"

SIDE_TYPES = {
    "receivable": ("receivable", "credit_memo"),
    "payable": ("payable", "vendor_credit"),
}


def parse_arguments():
    parser = argparse.ArgumentParser(
        description=(
            "Aged open trade accounts as of a date, grouped by counterparty + "
            "document group, past-due first. Artifact for Review Check 2; "
            "subledger_total ties to the Check 11 GL control balance."
        )
    )
    parser.add_argument("--as_of", required=True,
                        help="Report date YYYY-MM-DD (bounds origin JEs and payments)")
    parser.add_argument("--type", choices=["receivable", "payable"], default="receivable",
                        help="Which side to age: receivable (with credit_memos) or "
                             "payable (with vendor_credits). Default receivable.")
    parser.add_argument("--contact", default=None, help="Filter by contact name")
    parser.add_argument("--group_keys", default=DEFAULT_GROUP_KEYS,
                        help=f"Comma-separated metadata keys tried in order to form "
                             f"document groups. Default: {DEFAULT_GROUP_KEYS}")
    parser.add_argument("--output", default=None,
                        help="Optional CSV output path (open groups + residual credits)")
    return parser.parse_args()


def iso_to_date(s):
    return date.fromisoformat(s[:10])


def fetch_side(conn, as_of, side_types, contact_filter):
    """Fetch in-scope TAs with as-of remaining balances. All amounts in cents,
    debit-positive raw convention from the balance-account posting line."""
    cur = conn.cursor()
    params = [as_of]
    contact_sql = ""
    if contact_filter:
        contact_sql = "AND ta.contact = ?"
        params.append(contact_filter)
    placeholders = ",".join("?" for _ in side_types)
    params.extend(side_types)
    rows = cur.execute(f"""
        SELECT ta.id, ta.type, ta.contact, ta.document_date, ta.due_date,
               ta.metadata, je.transaction_date je_date,
               (SELECT SUM(CASE WHEN p.direction='debit' THEN p.amount ELSE -p.amount END)
                  FROM postings p
                 WHERE p.journal_entry_id = ta.journal_entry_id
                   AND p.account_code = json_extract(ta.metadata,'$.balance_account_code')
               ) AS due_line
        FROM trade_accounts ta
        JOIN journal_entries je ON je.id = ta.journal_entry_id
        WHERE ta.voided_at IS NULL
          AND je.transaction_date <= ?
          {contact_sql}
          AND ta.type IN ({placeholders})
    """, params).fetchall()

    items, warnings = [], []
    for r in rows:
        meta = json.loads(r["metadata"]) if r["metadata"] else {}
        if not meta.get("balance_account_code") or r["due_line"] is None:
            warnings.append({
                "trade_account_id": r["id"],
                "issue": "missing balance_account_code or no balance-account posting on origin JE; excluded",
            })
            continue
        if r["type"] in ("credit_memo", "vendor_credit"):
            # Consumption spans BOTH legitimate forms: credit applications
            # (source_ta_id) and direct/owner-cleared settlement (trade_account_id
            # with no source_ta_id). Mirrors _shared.trade_account_utils.
            consumed = cur.execute(
                """SELECT COALESCE(SUM(amount),0) FROM trade_account_payments
                   WHERE (source_ta_id = ?
                          OR (trade_account_id = ? AND source_ta_id IS NULL))
                     AND payment_date <= ?""",
                (r["id"], r["id"], as_of)).fetchone()[0]
            # due_line is contra-direction (CR for CM on A/R); consumption moves it toward 0
            remaining_raw = r["due_line"] + consumed if r["due_line"] < 0 else r["due_line"] - consumed
        else:
            paid = cur.execute(
                """SELECT COALESCE(SUM(amount),0) FROM trade_account_payments
                   WHERE trade_account_id = ? AND payment_date <= ?""",
                (r["id"], as_of)).fetchone()[0]
            remaining_raw = r["due_line"] - paid if r["due_line"] >= 0 else r["due_line"] + paid
        items.append({
            "id": r["id"], "type": r["type"], "contact": r["contact"],
            "document_date": r["document_date"],
            "due_date": r["due_date"] or r["document_date"],
            "source": meta.get("source") or "",
            "meta": meta,
            "balance_account": meta.get("balance_account_code"),
            "remaining_raw": remaining_raw,
        })
    return items, warnings


def group_items(items, group_keys, sign):
    groups = {}
    for it in items:
        ref, key_used = None, None
        for k in group_keys:
            v = it["meta"].get(k)
            if v not in (None, ""):
                ref, key_used = str(v), k
                break
        if ref is None:
            ref, key_used = f"ta:{it['id'][:8]}", "trade_account_id"
        gk = (it["contact"], key_used, ref)
        g = groups.setdefault(gk, {
            "contact": it["contact"], "group_type": key_used, "group_ref": ref,
            "source": it["source"], "ta_count": 0, "first_doc_date": it["document_date"],
            "due_date": it["due_date"], "net_cents": 0,
        })
        g["ta_count"] += 1
        g["first_doc_date"] = min(g["first_doc_date"], it["document_date"])
        g["due_date"] = max(g["due_date"], it["due_date"])
        g["net_cents"] += sign * it["remaining_raw"]
    return list(groups.values())


def main():
    try:
        args = parse_arguments()
        as_of = args.as_of
        iso_to_date(as_of)  # validate format early
        side_types = SIDE_TYPES[args.type]
        group_keys = [k.strip() for k in args.group_keys.split(",") if k.strip()]
        sign = 1 if args.type == "receivable" else -1

        conn = sqlite3.connect(f"file:{config_loader.get_db_path()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            items, warnings = fetch_side(conn, as_of, side_types, args.contact)
        finally:
            conn.close()

        groups = group_items(items, group_keys, sign)
        as_of_d = iso_to_date(as_of)
        open_groups, residual = [], []
        for g in groups:
            if g["net_cents"] == 0:
                continue
            g["days_overdue"] = max(0, (as_of_d - iso_to_date(g["due_date"])).days)
            (open_groups if g["net_cents"] > 0 else residual).append(g)

        open_groups.sort(key=lambda g: (-g["days_overdue"], g["due_date"], -g["net_cents"]))
        residual.sort(key=lambda g: g["net_cents"])

        past_due = [g for g in open_groups if g["days_overdue"] > 0]
        by_source = {}
        for g in open_groups + residual:
            b = by_source.setdefault(g["source"] or "(none)", {
                "groups": 0, "open_cents": 0, "residual_credit_cents": 0})
            b["groups"] += 1
            if g["net_cents"] > 0:
                b["open_cents"] += g["net_cents"]
            else:
                b["residual_credit_cents"] += g["net_cents"]

        if warnings:
            print(json.dumps({"warning": "excluded_trade_accounts",
                              "count": len(warnings), "details": warnings}),
                  file=sys.stderr)

        if args.output:
            with open(args.output, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["bucket", "source", "contact", "group_type", "group_ref",
                            "ta_count", "first_doc_date", "due_date", "days_overdue",
                            "open_amount_usd"])
                for g in open_groups:
                    w.writerow(["open", g["source"], g["contact"], g["group_type"],
                                g["group_ref"], g["ta_count"], g["first_doc_date"],
                                g["due_date"], g["days_overdue"],
                                f"{g['net_cents']/100:.2f}"])
                for g in residual:
                    w.writerow(["residual_credit", g["source"], g["contact"],
                                g["group_type"], g["group_ref"], g["ta_count"],
                                g["first_doc_date"], g["due_date"], "",
                                f"{g['net_cents']/100:.2f}"])

        balance_accounts = sorted({it["balance_account"] for it in items})
        open_total = sum(g["net_cents"] for g in open_groups)
        residual_total = sum(g["net_cents"] for g in residual)
        result = {
            "success": True,
            "as_of": as_of,
            "type": args.type,
            "group_keys": group_keys,
            "balance_accounts": balance_accounts,
            "open_groups": len(open_groups),
            "open_total_cents": open_total,
            "past_due_groups": len(past_due),
            "past_due_total_cents": sum(g["net_cents"] for g in past_due),
            "residual_credit_groups": len(residual),
            "residual_credit_total_cents": residual_total,
            "subledger_total_cents": open_total + residual_total,
            "by_source": by_source,
            "excluded_count": len(warnings),
            "csv": args.output,
        }
        print(json.dumps(result, indent=2))
        sys.exit(0)
    except Exception as e:
        print(json.dumps({"success": False, "error": f"{type(e).__name__}: {e}"},
                         indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
