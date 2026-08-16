#!/usr/bin/env python3
"""
Apply Transfer Tool
Links multiple imports from different feeds into a single balanced journal entry.
Used for inter-account transfers (e.g., paying a credit card from the bank).
"""

import argparse
import json
import sqlite3
import sys

from _shared.journal_engine import create_journal_entry_transfer
from _shared import config_loader


def parse_arguments():
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Apply inter-account transfer — link multiple imports into one JE"
    )
    parser.add_argument(
        '--transfer',
        required=True,
        type=str,
        help='JSON object with transfer details'
    )
    args = parser.parse_args()

    try:
        args.transfer = json.loads(args.transfer)
    except json.JSONDecodeError as e:
        print(json.dumps({
            "success": False,
            "error": f"Invalid JSON in --transfer: {str(e)}"
        }))
        sys.exit(1)

    return args


def validate_imports(conn, all_import_ids):
    """
    Validate all imports exist and are not already processed (processed=1).
    Accepts processed=0 (unprocessed) and processed=2 (client question).
    Returns error string or None if all valid.
    """
    placeholders = ",".join("?" * len(all_import_ids))
    cursor = conn.cursor()
    cursor.execute(
        f"SELECT id, processed FROM imports WHERE id IN ({placeholders})",
        all_import_ids
    )
    found = {row[0]: row[1] for row in cursor.fetchall()}

    # Check for missing imports
    missing = [iid for iid in all_import_ids if iid not in found]
    if missing:
        return f"Import(s) not found: {', '.join(missing)}"

    # Check for already-processed imports (processed=1 means fully processed)
    already_processed = [iid for iid, proc in found.items() if proc == 1]
    if already_processed:
        return f"Import(s) already processed: {', '.join(already_processed)}"

    return None


def mark_imports_processed(conn, all_import_ids):
    """Mark all imports as processed=1."""
    for import_id in all_import_ids:
        conn.execute(
            "UPDATE imports SET processed = 1 WHERE id = ?",
            (import_id,)
        )


def main():
    try:
        args = parse_arguments()
        transfer = args.transfer

        # Extract fields
        primary_import_id = transfer['primary_import_id']
        secondary_import_ids = transfer['secondary_import_ids']
        transaction_date = transfer['transaction_date']
        memo = transfer['memo']
        postings = transfer['postings']
        class_name = transfer.get('class_name')

        # Build full import ID list
        all_import_ids = [primary_import_id] + secondary_import_ids

        # Connect to database
        conn = sqlite3.connect(config_loader.get_db_path())
        conn.execute("PRAGMA foreign_keys = ON")

        try:
            # Validate all imports exist and are unprocessed
            import_error = validate_imports(conn, all_import_ids)
            if import_error:
                raise ValueError(import_error)

            # Create transfer journal entry
            journal_entry_id = create_journal_entry_transfer(
                conn,
                import_id=primary_import_id,
                transaction_date=transaction_date,
                memo=memo,
                postings_data=postings,
                all_import_ids=all_import_ids,
                class_name=class_name
            )

            # Mark all imports as processed
            mark_imports_processed(conn, all_import_ids)

            # Commit
            conn.commit()

            # Output success
            print(json.dumps({
                "success": True,
                "journal_entry_id": journal_entry_id,
                "imports_processed": len(all_import_ids)
            }, indent=2))
            sys.exit(0)

        except Exception as e:
            conn.rollback()
            raise
        finally:
            conn.close()

    except KeyError as e:
        print(json.dumps({
            "success": False,
            "error": f"Missing required field: {str(e)}"
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
