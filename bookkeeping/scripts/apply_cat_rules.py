#!/usr/bin/env python3
"""
Apply Categorization Rules Tool
Deterministic rule matching for bank transaction categorization.
"""

import argparse
import json
import sqlite3
import sys
import os
from datetime import datetime, timezone

# Add shared module to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '_shared'))

import journal_engine
import rule_matcher
import config_loader


def parse_arguments():
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Apply categorization rules to unprocessed imports"
    )
    return parser.parse_args()


def load_active_rules(conn):
    """Load active categorization rules ordered by priority."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, priority, name, match_criteria, apply_actions
        FROM categorization_rules
        WHERE active = 1
        ORDER BY priority ASC
    """)

    rules = []
    for row in cursor.fetchall():
        try:
            rules.append({
                'id': row[0],
                'priority': row[1],
                'name': row[2],
                'match_criteria': json.loads(row[3]),
                'apply_actions': json.loads(row[4])
            })
        except json.JSONDecodeError as e:
            # Skip rules with invalid JSON
            continue

    return rules


def load_unprocessed_imports(conn):
    """Load imports that haven't been categorized yet."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, source, amount, banking_date, raw_data
        FROM imports
        WHERE processed = 0
    """)

    imports = []
    for row in cursor.fetchall():
        try:
            imports.append({
                'id': row[0],
                'source': row[1],
                'amount': row[2],
                'banking_date': row[3],
                'raw_data': json.loads(row[4])
            })
        except json.JSONDecodeError:
            # Skip imports with invalid raw_data
            continue

    return imports


def build_rule_metadata(rule):
    """Build metadata dict for rule-based categorization."""
    return {
        "source": "rule",
        "rule_id": rule['id'],
        "rule_name": rule['name'],
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "priority": rule['priority']
    }


def main():
    """Main execution function."""
    args = parse_arguments()

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

    results = {
        "success": True,
        "processed": 0,
        "unmatched": 0,
        "failed": 0,
        "errors": []
    }

    # Load rules and imports
    rules = load_active_rules(conn)
    imports = load_unprocessed_imports(conn)

    # Process each import
    for import_record in imports:
        matched = False

        # Test rules in priority order (first match wins)
        for rule in rules:
            try:
                if rule_matcher.match_rule(import_record, rule):
                    # Build categorization from rule's apply_actions
                    categorization = {
                        "import_id": import_record['id'],
                        "postings": rule['apply_actions']['postings']
                    }

                    # Extract class_name from rule actions (optional)
                    class_name = rule['apply_actions'].get('class_name')

                    # Get import data
                    import_data = journal_engine.get_import_data(conn, import_record['id'])

                    # Build rule metadata
                    metadata = build_rule_metadata(rule)

                    # Create journal entry using shared module
                    # Note: Pass metadata instead of confidence_score
                    success, result = journal_engine.create_journal_entry(
                        conn,
                        categorization,
                        import_data,
                        confidence_score=None,
                        metadata=metadata,
                        class_name=class_name
                    )

                    if success:
                        results["processed"] += 1
                        matched = True
                        break  # First match wins
                    else:
                        results["failed"] += 1
                        results["errors"].append({
                            "import_id": import_record['id'],
                            "error": result
                        })
                        matched = True  # Don't try other rules if creation failed
                        break

            except Exception as e:
                results["failed"] += 1
                results["errors"].append({
                    "import_id": import_record['id'],
                    "error": f"Rule '{rule['name']}' error: {str(e)}"
                })
                matched = True  # Don't try other rules on error
                break

        if not matched:
            results["unmatched"] += 1

    conn.close()

    print(json.dumps(results, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
