#!/usr/bin/env python3
"""
Test Categorization Rule Tool
Dry-run simulator for validating rule accuracy against historical data.
"""

import argparse
import json
import sqlite3
import sys
import os

# Add shared module to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '_shared'))
import rule_matcher
import journal_engine
import config_loader


def parse_arguments():
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Test categorization rule against historical data"
    )
    parser.add_argument('--rule_id', required=True, help='UUID of the rule to test')
    parser.add_argument('--start_date', default=None, help='Start date filter (YYYY-MM-DD)')
    parser.add_argument('--end_date', default=None, help='End date filter (YYYY-MM-DD)')
    parser.add_argument('--source_filter', default=None, help='Source filter (partial match)')
    return parser.parse_args()


def load_rule(conn, rule_id):
    """Load rule from database."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, priority, name, match_criteria, apply_actions
        FROM categorization_rules
        WHERE id = ?
    """, (rule_id,))

    row = cursor.fetchone()
    if not row:
        raise ValueError(f"Rule with ID {rule_id} not found")

    return {
        'id': row[0],
        'priority': row[1],
        'name': row[2],
        'match_criteria': json.loads(row[3]),
        'apply_actions': json.loads(row[4])
    }


def load_processed_imports(conn, start_date=None, end_date=None, source_filter=None):
    """Load processed imports with optional filters."""
    query = "SELECT id, source, amount, banking_date, raw_data FROM imports WHERE processed = 1"
    params = []

    if start_date:
        query += " AND banking_date >= ?"
        params.append(start_date)

    if end_date:
        query += " AND banking_date <= ?"
        params.append(end_date)

    if source_filter:
        query += " AND source LIKE ?"
        params.append(f"%{source_filter}%")

    cursor = conn.cursor()
    cursor.execute(query, params)

    imports = []
    for row in cursor.fetchall():
        imports.append({
            'id': row[0],
            'source': row[1],
            'amount': row[2],
            'banking_date': row[3],
            'raw_data': json.loads(row[4])
        })
    return imports


def simulate_postings(rule):
    """
    Build simulated posting details from rule's apply_actions.
    Returns list of dicts with account_code, contact, tags.
    """
    postings = rule['apply_actions']['postings']
    return [{
        'account_code': p['account_code'],
        'contact': p.get('contact', ''),
        'tags': p.get('tags', [])
    } for p in postings]


def load_actual_postings(conn, import_id):
    """Load actual postings for an import with account, contact, and tags."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.account_code, p.contact, p.tags
        FROM postings p
        JOIN journal_entries je ON p.journal_entry_id = je.id
        WHERE je.import_id = ?
    """, (import_id,))

    result = []
    for row in cursor.fetchall():
        tags_raw = row[2]
        tags = json.loads(tags_raw) if tags_raw else []
        result.append({
            'account_code': row[0],
            'contact': row[1],
            'tags': tags
        })
    return result


def compare_categorizations(simulated_postings, actual_postings, bank_account_code):
    """
    Three-tier comparison of simulated vs actual postings.
    Returns: (comparison_status, mismatch_reason, warnings)
      - "no_actual_categorization": no actual postings exist
      - "mismatch": account codes differ (failure)
      - "match_with_warnings": accounts match, contacts/tags differ (warning)
      - "match": everything matches
    """
    if not actual_postings:
        return ("no_actual_categorization", "No journal entries found for this import", [])

    # Filter out bank postings entirely from both sides
    sim_filtered = [p for p in simulated_postings if p['account_code'] != bank_account_code]
    act_filtered = [p for p in actual_postings if p['account_code'] != bank_account_code]

    # Tier 1: Account code set comparison
    sim_accounts = set(p['account_code'] for p in sim_filtered)
    act_accounts = set(p['account_code'] for p in act_filtered)

    if sim_accounts != act_accounts:
        reason = f"Simulated accounts {sorted(sim_accounts)} do not match actual accounts {sorted(act_accounts)}"
        return ("mismatch", reason, [])

    # Tier 2+3: Contact and tag comparison per account group
    warnings = []

    for account_code in sim_accounts:
        sim_for_acct = [p for p in sim_filtered if p['account_code'] == account_code]
        act_for_acct = [p for p in act_filtered if p['account_code'] == account_code]

        # Contact comparison (skip if simulated is empty/absent)
        sim_contacts = set(p['contact'] for p in sim_for_acct if p['contact'])
        if sim_contacts:
            act_contacts = set(p['contact'] for p in act_for_acct if p['contact'])
            if sim_contacts != act_contacts:
                warnings.append(
                    f"Account {account_code}: contact mismatch — simulated {sim_contacts} vs actual {act_contacts}"
                )

        # Tag comparison (skip if simulated is empty/absent)
        sim_tags = set()
        for p in sim_for_acct:
            sim_tags.update(p.get('tags', []))
        if sim_tags:
            act_tags = set()
            for p in act_for_acct:
                act_tags.update(p.get('tags', []))
            if sim_tags != act_tags:
                warnings.append(
                    f"Account {account_code}: tag mismatch — simulated {sim_tags} vs actual {act_tags}"
                )

    if warnings:
        return ("match_with_warnings", None, warnings)

    return ("match", None, [])


def main():
    """Main execution function."""
    args = parse_arguments()

    try:
        conn = sqlite3.connect(config_loader.get_db_path())
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as e:
        print(json.dumps({
            "success": False,
            "error": f"Database connection failed: {str(e)}"
        }))
        sys.exit(1)

    try:
        # Load rule
        rule = load_rule(conn, args.rule_id)

        # Load processed imports with filters
        imports = load_processed_imports(
            conn,
            start_date=args.start_date,
            end_date=args.end_date,
            source_filter=args.source_filter
        )

        # Initialize results
        results = {
            "success": True,
            "rule_id": rule['id'],
            "rule_name": rule['name'],
            "total_imports_tested": len(imports),
            "rule_matches": 0,
            "accurate_predictions": 0,
            "partial_matches": 0,
            "mismatches": 0,
            "accuracy_percentage": None,
            "warning": None,
            "details": []
        }

        # Test each import
        for import_record in imports:
            # Get description for reporting
            description = import_record['raw_data'].get('Reference', '')

            # Test if rule matches
            rule_matched = rule_matcher.match_rule(import_record, rule)

            if rule_matched:
                results["rule_matches"] += 1

                # Get bank account code (needed to filter bank posting from comparison)
                try:
                    bank_account_code = journal_engine.parse_source(import_record['source'])
                except ValueError:
                    bank_account_code = ""

                # Simulate postings
                simulated_postings = simulate_postings(rule)

                # Load actual postings
                actual_postings = load_actual_postings(conn, import_record['id'])

                # Compare (three-tier)
                comparison, mismatch_reason, warnings = compare_categorizations(
                    simulated_postings,
                    actual_postings,
                    bank_account_code
                )

                if comparison == "match":
                    results["accurate_predictions"] += 1
                elif comparison == "match_with_warnings":
                    results["partial_matches"] += 1
                elif comparison == "mismatch":
                    results["mismatches"] += 1

                # Add to details
                results["details"].append({
                    "import_id": import_record['id'],
                    "banking_date": import_record['banking_date'],
                    "amount": import_record['amount'],
                    "description": description,
                    "rule_matched": True,
                    "comparison": comparison,
                    "simulated_postings": simulated_postings,
                    "actual_postings": actual_postings,
                    "mismatch_reason": mismatch_reason,
                    "warnings": warnings
                })
            else:
                # Rule didn't match - include actual postings for visibility
                actual_postings = load_actual_postings(conn, import_record['id'])
                results["details"].append({
                    "import_id": import_record['id'],
                    "banking_date": import_record['banking_date'],
                    "amount": import_record['amount'],
                    "description": description,
                    "rule_matched": False,
                    "comparison": None,
                    "simulated_postings": None,
                    "actual_postings": actual_postings,
                    "mismatch_reason": None,
                    "warnings": []
                })

        # Calculate accuracy percentage (partial matches count as accounts-correct)
        if results["rule_matches"] > 0:
            accounts_correct = results["accurate_predictions"] + results["partial_matches"]
            results["accuracy_percentage"] = round(
                (accounts_correct / results["rule_matches"]) * 100,
                2
            )
        else:
            results["accuracy_percentage"] = None
            results["warning"] = "Rule did not match any transactions in the test set. Consider reviewing rule conditions or testing with a different date range."

        print(json.dumps(results, indent=2))
        sys.exit(0)

    except ValueError as e:
        # Handle expected errors (rule not found, etc.)
        error_output = {
            "success": False,
            "error": str(e)
        }
        print(json.dumps(error_output, indent=2))
        sys.exit(1)

    except Exception as e:
        # Handle unexpected errors
        error_output = {
            "success": False,
            "error": f"Unexpected error: {str(e)}"
        }
        print(json.dumps(error_output, indent=2))
        sys.exit(1)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
