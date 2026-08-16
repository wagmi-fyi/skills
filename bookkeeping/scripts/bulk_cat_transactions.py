#!/usr/bin/env python3
"""
Bulk Categorize Transactions Tool
Creates double-entry journal entries from AI categorizations.
"""

import argparse
import json
import sqlite3
import sys
import os

# Add _shared to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '_shared'))

import journal_engine
import config_loader


def parse_arguments():
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Bulk categorize transactions into journal entries"
    )
    parser.add_argument(
        '--categorizations',
        required=True,
        type=str,
        help='JSON array of categorizations'
    )
    args = parser.parse_args()

    # Parse the categorizations JSON string
    try:
        args.categorizations = json.loads(args.categorizations)
    except json.JSONDecodeError as e:
        print(json.dumps({
            "success": False,
            "error": f"Invalid JSON in categorizations: {str(e)}"
        }))
        sys.exit(1)

    return args


def main():
    """Main execution function."""
    args = parse_arguments()
    categorizations = args.categorizations

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

    # Initialize results
    results = {
        "success": True,
        "processed": 0,
        "failed": 0,
        "errors": [],
        "journal_entry_ids": []
    }

    # Load coding config for confidence thresholds
    coding_config = config_loader.get_coding_config()
    min_confidence = coding_config['min_confidence_to_categorize']

    # Process each categorization
    for cat in categorizations:
        import_id = cat.get("import_id", "unknown")
        class_name = cat.get("class_name")
        postings = cat.get("postings", [])
        confidence = cat.get("confidence_score", 0)

        try:
            # Client question path: empty postings or below confidence threshold
            # Mark processed=2 without creating journal entry
            if not postings or confidence < min_confidence:
                cursor = conn.execute(
                    "UPDATE imports SET processed = 2 WHERE id = ? AND processed = 0",
                    (import_id,)
                )
                if cursor.rowcount > 0:
                    conn.commit()
                    results["processed"] += 1
                else:
                    conn.rollback()
                    results["failed"] += 1
                    results["errors"].append({
                        "import_id": import_id,
                        "error": "Import not found or already processed"
                    })
                continue

            # Get import data
            import_data = journal_engine.get_import_data(conn, import_id)

            # Create journal entry (metadata=None for AI categorizations)
            success, result = journal_engine.create_journal_entry(
                conn,
                cat,
                import_data,
                metadata=None,
                class_name=class_name
            )

            if success:
                results["processed"] += 1
                results["journal_entry_ids"].append(result)
            else:
                results["failed"] += 1
                results["errors"].append({
                    "import_id": import_id,
                    "error": result
                })

        except Exception as e:
            results["failed"] += 1
            results["errors"].append({
                "import_id": import_id,
                "error": str(e)
            })

    # Close database connection
    conn.close()

    # Output results as JSON
    print(json.dumps(results, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
