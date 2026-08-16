#!/usr/bin/env python3
"""
Universal Ingest Script
Reads universal JSON (file or stdin), validates, deduplicates by external_id,
and bulk-inserts into the imports table. All adapters converge here.
"""

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime

# Add shared module to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '_shared'))
import config_loader

VALID_BALANCE_TYPES = ["cash", "credit"]


def parse_arguments():
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description='Ingest universal JSON into imports table')
    parser.add_argument('--account_code', required=True, help='Account code from chart of accounts')
    parser.add_argument('--file', default=None, help='Path to JSON file (reads stdin if omitted)')
    return parser.parse_args()


def read_input(file_path):
    """Read and parse JSON from file or stdin.

    Returns parsed dict. Validates top-level envelope structure.
    """
    if file_path:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        with open(file_path, 'r', encoding='utf-8') as f:
            raw = f.read()
    else:
        if sys.stdin.isatty():
            raise ValueError("No input provided. Use --file or pipe JSON to stdin.")
        raw = sys.stdin.read()

    if not raw.strip():
        raise ValueError("No input provided. Use --file or pipe JSON to stdin.")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}")

    # Envelope validation
    if not isinstance(data, dict):
        raise ValueError("Invalid input: expected JSON object with 'transactions' array")
    if "transactions" not in data:
        raise ValueError("Invalid input: expected JSON object with 'transactions' array")
    if not isinstance(data["transactions"], list):
        raise ValueError("Invalid input: expected JSON object with 'transactions' array")

    return data


def validate_transaction(index, txn):
    """Validate a single transaction against the universal contract.

    Args:
        index: 0-based index in the transactions array
        txn: Transaction dict

    Returns:
        List of error strings (empty = valid)
    """
    errors = []
    display_num = index + 1  # 1-based for user-facing messages

    # Required string fields
    for field in ["external_id", "reference", "currency"]:
        if field not in txn or not isinstance(txn[field], str) or not txn[field].strip():
            errors.append(f"Transaction {display_num}: Missing required field '{field}'")

    # balance_type: required, must be cash or credit
    if "balance_type" not in txn:
        errors.append(f"Transaction {display_num}: Missing required field 'balance_type'")
    elif txn["balance_type"] not in VALID_BALANCE_TYPES:
        errors.append(
            f"Transaction {display_num}: Invalid balance_type '{txn['balance_type']}'. "
            f"Must be 'cash' or 'credit'"
        )

    # amount: required, must be integer
    if "amount" not in txn:
        errors.append(f"Transaction {display_num}: Missing required field 'amount'")
    elif not isinstance(txn["amount"], int):
        errors.append(
            f"Transaction {display_num}: Invalid amount '{txn['amount']}'. "
            f"Must be an integer (cents)"
        )

    # date: required, must be valid ISO YYYY-MM-DD
    if "date" not in txn:
        errors.append(f"Transaction {display_num}: Missing required field 'date'")
    elif not isinstance(txn["date"], str):
        errors.append(f"Transaction {display_num}: Invalid date '{txn['date']}'. Must be ISO format YYYY-MM-DD")
    else:
        try:
            datetime.strptime(txn["date"], "%Y-%m-%d")
        except ValueError:
            errors.append(f"Transaction {display_num}: Invalid date '{txn['date']}'. Must be ISO format YYYY-MM-DD")

    return errors


def lookup_account(conn, account_code):
    """Query chart_of_accounts for account by code.

    Returns dict with 'code' and 'name', or None if not found.
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT code, name FROM chart_of_accounts WHERE code = ?",
        (account_code,)
    )
    row = cursor.fetchone()
    if row:
        return {"code": row[0], "name": row[1]}
    return None


def get_existing_external_ids(conn, source):
    """Get set of external_ids already in imports for this source."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT external_id FROM imports WHERE source = ? AND external_id IS NOT NULL",
        (source,)
    )
    return {row[0] for row in cursor.fetchall()}


def build_import_row(txn, source, batch_id):
    """Construct a dict for INSERT into imports.

    raw_data uses title-case keys for backward compatibility with
    journal_engine.get_import_data() which reads "Balance Type", "Reference", etc.
    """
    raw_data = json.dumps({
        "Date": txn["date"],
        "Amount": txn["amount"],
        "Reference": txn["reference"],
        "Balance Type": txn["balance_type"],
        "Currency": txn["currency"],
        "external_id": txn["external_id"],
        "raw_data": txn.get("raw_data", {})
    })

    return {
        "id": str(uuid.uuid4()),
        "external_id": txn["external_id"],
        "source": source,
        "type": "bank_statement",
        "banking_date": txn["date"],
        "amount": txn["amount"],
        "batch_id": batch_id,
        "raw_data": raw_data,
        "processed": 0
    }


def insert_transactions(conn, rows):
    """Bulk insert rows into imports table with atomic transaction safety.

    Uses 'with conn:' context manager for proper transaction handling.
    Commits on success, rolls back on any exception.
    """
    insert_sql = """
        INSERT INTO imports (id, external_id, source, type, banking_date, amount, batch_id, raw_data, processed)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    try:
        with conn:
            for row in rows:
                conn.execute(insert_sql, (
                    row['id'],
                    row['external_id'],
                    row['source'],
                    row['type'],
                    row['banking_date'],
                    row['amount'],
                    row['batch_id'],
                    row['raw_data'],
                    row['processed']
                ))
    except Exception as e:
        raise Exception(f"Database insertion failed: {str(e)}")


def main():
    try:
        args = parse_arguments()

        # Get DB connection
        db_path = config_loader.get_db_path()
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")

        try:
            # Validate account exists
            account = lookup_account(conn, args.account_code)
            if not account:
                raise ValueError(f"Account code '{args.account_code}' not found")

            source = f"{account['code']} - {account['name']}"

            # Read and parse input
            data = read_input(args.file)
            transactions = data["transactions"]

            # Empty array check
            if not transactions:
                print(json.dumps({
                    "success": False,
                    "error": "No valid transactions found"
                }, indent=2))
                sys.exit(1)

            # Validate all transactions
            errors = []
            for i, txn in enumerate(transactions):
                txn_errors = validate_transaction(i, txn)
                errors.extend(txn_errors)

            if errors:
                error_msg = "\n".join(errors)
                print(json.dumps({
                    "success": False,
                    "error": f"Validation failed:\n{error_msg}"
                }, indent=2))
                sys.exit(1)

            # Deduplication (against DB + intra-batch)
            existing_ids = get_existing_external_ids(conn, source)
            seen_in_batch = set()
            new_transactions = []
            skipped = 0

            for txn in transactions:
                ext_id = txn["external_id"]
                if ext_id in existing_ids or ext_id in seen_in_batch:
                    skipped += 1
                else:
                    seen_in_batch.add(ext_id)
                    new_transactions.append(txn)

            # All skipped = success (idempotent)
            if not new_transactions:
                print(json.dumps({
                    "success": True,
                    "imported": 0,
                    "skipped": skipped,
                    "batch_id": None,
                    "source": source,
                    "date_range": None
                }, indent=2))
                sys.exit(0)

            # Build import rows
            batch_id = str(uuid.uuid4())
            rows = [build_import_row(txn, source, batch_id) for txn in new_transactions]

            # Insert atomically
            insert_transactions(conn, rows)

            # Compute date range
            dates = [row['banking_date'] for row in rows]
            date_range = {
                "start": min(dates),
                "end": max(dates)
            }

            # Output success
            print(json.dumps({
                "success": True,
                "imported": len(rows),
                "skipped": skipped,
                "batch_id": batch_id,
                "source": source,
                "date_range": date_range
            }, indent=2))
            sys.exit(0)

        finally:
            conn.close()

    except FileNotFoundError as e:
        print(json.dumps({
            "success": False,
            "error": str(e)
        }, indent=2))
        sys.exit(1)

    except ValueError as e:
        print(json.dumps({
            "success": False,
            "error": str(e)
        }, indent=2))
        sys.exit(1)

    except Exception as e:
        print(json.dumps({
            "success": False,
            "error": f"Unexpected error: {str(e)}"
        }, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
