#!/usr/bin/env python3
"""
Create Manual Journal Entry
CLI wrapper for journal_engine.create_journal_entry_direct().
Creates manual journal entries (adjustments, accruals, reclassifications)
with pre-built postings.
"""

import argparse
import json
import sqlite3
import sys
import os
from datetime import datetime

# Add _shared to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '_shared'))

import journal_engine
import config_loader


def parse_arguments():
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Create a manual journal entry with pre-built postings"
    )
    parser.add_argument(
        '--date',
        required=True,
        type=str,
        help='Transaction date YYYY-MM-DD'
    )
    parser.add_argument(
        '--memo',
        required=True,
        type=str,
        help='Journal entry description/memo'
    )
    parser.add_argument(
        '--postings',
        required=True,
        type=str,
        help='JSON array of posting objects'
    )
    parser.add_argument(
        '--class_name',
        type=str,
        default=None,
        help='QBO Class name (validated against tags table)'
    )
    args = parser.parse_args()

    # Parse the postings JSON string
    try:
        args.postings = json.loads(args.postings)
    except json.JSONDecodeError as e:
        print(json.dumps({
            "success": False,
            "error": f"Invalid JSON in postings: {str(e)}"
        }))
        sys.exit(1)

    return args


def validate_date(date_str):
    """Validate date format is YYYY-MM-DD. Returns date string or raises ValueError."""
    datetime.strptime(date_str, "%Y-%m-%d")
    return date_str


def main():
    """Main execution function."""
    args = parse_arguments()

    # Validate date format
    try:
        validate_date(args.date)
    except ValueError:
        print(json.dumps({
            "success": False,
            "error": f"Invalid date format: '{args.date}'. Expected YYYY-MM-DD."
        }))
        sys.exit(1)

    # Guard: empty postings
    if not args.postings:
        print(json.dumps({
            "success": False,
            "error": "Postings array is empty"
        }))
        sys.exit(1)

    # Connect to database
    try:
        conn = sqlite3.connect(config_loader.get_db_path())
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
    except sqlite3.Error as e:
        print(json.dumps({
            "success": False,
            "error": f"Database connection failed: {str(e)}"
        }))
        sys.exit(1)

    # Pre-validate class_name (create_journal_entry_direct doesn't validate this)
    if args.class_name:
        class_error = journal_engine.validate_class(conn, args.class_name)
        if class_error:
            print(json.dumps({
                "success": False,
                "error": class_error
            }))
            conn.close()
            sys.exit(0)

    # Build JE metadata
    je_metadata = {"source": "manual"}
    if args.class_name:
        je_metadata["class_name"] = args.class_name

    # Create journal entry via engine
    # Engine handles: account code validation, contact auto-creation, balance check
    try:
        journal_entry_id = journal_engine.create_journal_entry_direct(
            conn,
            args.date,
            args.memo,
            args.postings,
            je_metadata
        )
        conn.commit()

        # Compute totals from postings for output contract
        total_debits = sum(p["amount"] for p in args.postings if p["direction"] == "debit")
        total_credits = sum(p["amount"] for p in args.postings if p["direction"] == "credit")

        print(json.dumps({
            "success": True,
            "journal_entry_id": journal_entry_id,
            "posting_count": len(args.postings),
            "total_debits": total_debits,
            "total_credits": total_credits
        }, indent=2))

    except ValueError as e:
        conn.rollback()
        print(json.dumps({
            "success": False,
            "error": str(e)
        }))

    except Exception as e:
        conn.rollback()
        print(json.dumps({
            "success": False,
            "error": f"Unexpected error: {str(e)}"
        }))

    conn.close()
    sys.exit(0)


if __name__ == "__main__":
    main()
