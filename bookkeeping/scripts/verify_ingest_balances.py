#!/usr/bin/env python3
"""
Ingest Balance Verification Script
For each ingest account, computes postings_balance + unposted_imports and
compares to the statement ending balance. Reports PASS/WARN/FAIL per account.

JSON result to stdout, human-readable table to stderr.
"""

import argparse
import json
import os
import sqlite3
import sys

# Add shared module to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '_shared'))
import config_loader


def parse_arguments():
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description='Verify ingest balances against statement ending balances'
    )
    parser.add_argument(
        '--balances', required=True,
        help='JSON mapping account_code → statement balance in cents. '
             'Inline JSON string or path to JSON file.'
    )
    parser.add_argument(
        '--db', default=None,
        help='Path to bookkeeping.db (default: from config)'
    )
    parser.add_argument(
        '--account', default=None,
        help='Single account_code to verify (default: all)'
    )
    return parser.parse_args()


def parse_balances(raw):
    """Parse --balances argument as JSON string or file path.

    Returns dict mapping account_code (str) → statement balance (int, cents).
    """
    # Try inline JSON first
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        # Treat as file path
        if not os.path.exists(raw):
            raise ValueError(f"--balances is not valid JSON and file not found: {raw}")
        with open(raw, 'r', encoding='utf-8') as f:
            data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("--balances must be a JSON object mapping account_code → balance")

    return data


def discover_accounts(conn, account_filter=None):
    """Get distinct ingest accounts from imports table.

    Deduplicates by account code — imports with different source strings
    (e.g. after an account rename) are consolidated into one entry.

    Returns list of dicts: [{"code", "name", "type"}, ...]
    """
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT source FROM imports")
    rows = cursor.fetchall()

    seen_codes = {}
    for (source,) in rows:
        code, name = source.split(' - ', 1)

        if account_filter and code != account_filter:
            continue

        # Keep the latest name seen for this code (last source string wins)
        seen_codes[code] = name

    accounts = []
    for code, name in seen_codes.items():
        # Look up account type
        cursor.execute(
            "SELECT type FROM chart_of_accounts WHERE code = ?", (code,)
        )
        type_row = cursor.fetchone()
        account_type = type_row[0] if type_row else "unknown"

        accounts.append({
            "code": code,
            "name": name,
            "type": account_type,
        })

    return accounts


def compute_postings_balance(conn, account_code, account_type):
    """Compute direction-aware balance from all postings on this account.

    Assets (debit-normal): SUM(debit) - SUM(credit)
    Liabilities (credit-normal): SUM(credit) - SUM(debit)

    Returns signed integer cents. Returns 0 if no postings exist.
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT direction, SUM(amount) as total "
        "FROM postings WHERE account_code = ? GROUP BY direction",
        (account_code,)
    )
    totals = {row[0]: row[1] for row in cursor.fetchall()}
    debit_total = totals.get('debit', 0) or 0
    credit_total = totals.get('credit', 0) or 0

    if account_type == 'liability':
        return credit_total - debit_total
    else:
        # asset and any other type: debit-normal
        return debit_total - credit_total


def compute_unposted_imports(conn, account_code):
    """Sum import amounts not yet reflected in postings (processed != 1).

    Matches by account code prefix on the source string, consolidating
    imports across source renames (e.g. an account whose source label
    changed from "1001 - Bank" to "1001 - Bank (Old Provider)" still
    aggregates to the same code).

    Returns signed integer cents. Returns 0 if no matching imports.
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT SUM(amount) FROM imports "
        "WHERE source LIKE ? AND (processed IS NULL OR processed != 1)",
        (account_code + ' - %',)
    )
    result = cursor.fetchone()[0]
    return result if result is not None else 0


def verify_account(conn, account, statement_balance):
    """Run full verification for one account.

    Returns result dict with all computed values and status.
    """
    postings_bal = compute_postings_balance(conn, account["code"], account["type"])
    unposted = compute_unposted_imports(conn, account["code"])
    computed = postings_bal + unposted
    difference = computed - statement_balance
    status = "PASS" if difference == 0 else "WARN"

    return {
        "account_code": account["code"],
        "account_name": account["name"],
        "account_type": account["type"],
        "postings_balance": postings_bal,
        "unposted_imports": unposted,
        "computed_ending": computed,
        "statement_balance": statement_balance,
        "difference": difference,
        "status": status,
    }


def fmt_cents(amount_cents):
    """Format integer cents as currency string: $X,XXX.XX or -$X,XXX.XX."""
    negative = amount_cents < 0
    abs_amount = abs(amount_cents)
    dollars = abs_amount // 100
    cents = abs_amount % 100
    formatted = f"${dollars:,}.{cents:02d}"
    return f"-{formatted}" if negative else formatted


def print_table(results, overall_status, counts):
    """Print human-readable verification table to stderr."""
    print("\nIngest Balance Verification", file=sys.stderr)
    print("===========================\n", file=sys.stderr)

    # Column headers
    header = (
        f"{'Account':<9}| {'Postings':>12} | {'Imports':>12} | "
        f"{'Computed':>12} | {'Statement':>12} | {'Diff':>12} | Status"
    )
    separator = (
        f"{'-' * 9}|{'-' * 14}|{'-' * 14}|"
        f"{'-' * 14}|{'-' * 14}|{'-' * 14}|-------"
    )
    print(header, file=sys.stderr)
    print(separator, file=sys.stderr)

    for r in results:
        if r["status"] == "FAIL":
            print(
                f"{r['account_code']:<9}| {'—':>12} | {'—':>12} | "
                f"{'—':>12} | {'—':>12} | {'—':>12} | {r['status']}",
                file=sys.stderr
            )
        else:
            print(
                f"{r['account_code']:<9}| {fmt_cents(r['postings_balance']):>12} | "
                f"{fmt_cents(r['unposted_imports']):>12} | "
                f"{fmt_cents(r['computed_ending']):>12} | "
                f"{fmt_cents(r['statement_balance']):>12} | "
                f"{fmt_cents(r['difference']):>12} | {r['status']}",
                file=sys.stderr
            )

    print(
        f"\nOverall: {overall_status} "
        f"({counts['PASS']} PASS, {counts['WARN']} WARN, {counts['FAIL']} FAIL)",
        file=sys.stderr
    )


def main():
    try:
        args = parse_arguments()

        # DB connection
        db_path = args.db if args.db else config_loader.get_db_path()
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")

        try:
            # Parse balances input
            balances = parse_balances(args.balances)

            # Discover accounts
            accounts = discover_accounts(conn, account_filter=args.account)

            if not accounts:
                raise ValueError(
                    f"No imports found"
                    + (f" for account '{args.account}'" if args.account else "")
                )

            # Verify each account
            results = []
            for account in accounts:
                stmt_bal = balances.get(account["code"])
                if stmt_bal is None:
                    results.append({
                        "account_code": account["code"],
                        "account_name": account["name"],
                        "account_type": account["type"],
                        "postings_balance": None,
                        "unposted_imports": None,
                        "computed_ending": None,
                        "statement_balance": None,
                        "difference": None,
                        "status": "FAIL",
                        "message": "No statement balance provided",
                    })
                else:
                    results.append(verify_account(conn, account, stmt_bal))

            # Compute overall status
            counts = {"PASS": 0, "WARN": 0, "FAIL": 0}
            for r in results:
                counts[r["status"]] += 1

            if counts["FAIL"] > 0:
                overall = "FAIL"
            elif counts["WARN"] > 0:
                overall = "WARN"
            else:
                overall = "PASS"

            summary = (
                f"{len(results)} accounts: "
                f"{counts['PASS']} PASS, {counts['WARN']} WARN, {counts['FAIL']} FAIL"
            )

            # JSON to stdout
            print(json.dumps({
                "success": True,
                "summary": summary,
                "overall_status": overall,
                "accounts": results,
            }, indent=2))

            # Table to stderr
            print_table(results, overall, counts)

            sys.exit(0)

        finally:
            conn.close()

    except ValueError as e:
        print(json.dumps({
            "success": False,
            "error": str(e),
        }, indent=2))
        sys.exit(1)

    except Exception as e:
        print(json.dumps({
            "success": False,
            "error": f"Unexpected error: {str(e)}",
        }, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
