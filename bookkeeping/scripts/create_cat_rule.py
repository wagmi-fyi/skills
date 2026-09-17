#!/usr/bin/env python3
"""
Creates categorization rules and switches them on, switches them off, or deletes them.
A new rule is tested against the coded history before it is switched on.
"""

import argparse
import json
import os
import sqlite3
import sys
import uuid

# Add shared module to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '_shared'))
import config_loader
import journal_engine
import rule_matcher
import test_cat_rule

RULE_KEYS = {'name', 'priority', 'match_criteria', 'apply_actions'}
SPECIFIC_PRIORITY = 10
BROAD_STEP = 10
BROAD_START = 100
MIDDLE_START = 50
COLLISION_STEP = 5
BROAD_OPERATORS = {'contains', 'starts_with', 'is_blank'}
EXACT_OPERATORS = {'equals', 'equals_number'}
EPILOG = """\
A rule is a JSON object:
  {"name": "...", "priority": 20 (optional),
   "match_criteria": {"logic": "all" or "any",
                      "conditions": [{"field", "operator", "value"}]},
   "apply_actions": {"postings": [{"account_code", "contact", "tags",
                                   "amount" or "percentage", "class_name",
                                   "description"}],
                     "class_name": "..." (optional)}}
The fields and operators are the ones the rule matcher reads.
Pass one rule or a list of rules.

Output, as JSON on stdout:
  create      success, created, active, inactive, failed, and rules: one
              entry per rule with name, rule_id, status (active, inactive or
              failed), priority, priority_reason, new_contacts, errors, test,
              mismatch_details, partial_match_details
  --activate  success, rule_id, name, forced, test, mismatch_details,
              partial_match_details, and error on a refusal
  --deactivate, --delete
              success, rule_id, name, and error on a refusal
test holds the counts test_cat_rule.py prints. The detail lists hold its
detail entries. Status active means switched on, inactive means switched off.
Create exits 0 once the input is read, and each rule carries its own status.
A refusal or an error exits 1.
"""
SAMPLE_IMPORT = {
    'id': '',
    'source': '',
    'amount': 0,
    'banking_date': None,
    'raw_data': {'Reference': ''},
}


def parse_arguments():
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Create categorization rules. A new rule is checked, inserted switched off,\n"
            "tested against the coded history, and switched on only when the test passes.\n"
            "A pass means every matching line agrees with the rule, or no line matches."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--rules', help='One rule or a list of rules, as a JSON string')
    action.add_argument('--file', help='Path to a JSON file holding one rule or a list of rules')
    action.add_argument('--activate', metavar='RULE_ID',
                        help='Test a switched-off rule again and switch it on if it passes')
    action.add_argument('--deactivate', metavar='RULE_ID',
                        help='Switch off a rule that is switched on')
    action.add_argument('--delete', metavar='RULE_ID',
                        help='Delete a rule that is switched off')
    parser.add_argument('--force', metavar='REASON',
                        help='With --activate: switch the rule on even when the test fails. '
                             'The reason is written to the audit log.')
    parser.add_argument('--changed_by', default='create_cat_rule.py',
                        help='Audit log attribution')
    args = parser.parse_args()

    if args.force is not None and not args.activate:
        parser.error('--force works only with --activate')
    if args.force is not None and not args.force.strip():
        parser.error('--force needs a reason')

    return args


def load_rules_input(args):
    """Read the rules from --rules or --file and return them as a list."""
    try:
        if args.rules is not None:
            data = json.loads(args.rules)
        else:
            with open(args.file) as f:
                data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"Could not read the rules: {e}")

    if isinstance(data, dict):
        return [data]
    if isinstance(data, list) and data:
        return data
    raise ValueError("The rules must be one JSON object or a list of objects")


def check_criteria(match_criteria):
    """Return a list of problems with the match criteria. Empty means it runs."""
    if not isinstance(match_criteria, dict):
        return ["match_criteria must be an object"]
    conditions = match_criteria.get('conditions')
    if not isinstance(conditions, list) or not conditions:
        return ["match_criteria needs at least one condition. A rule with none would match every line."]
    for i, condition in enumerate(conditions):
        if not isinstance(condition, dict):
            return [f"Condition {i + 1} must be an object"]
    # The matcher decides what it can run.
    try:
        rule_matcher.match_rule(SAMPLE_IMPORT, {'match_criteria': match_criteria})
    except KeyError as e:
        return [f"match_criteria is missing {e}"]
    except (ValueError, TypeError, AttributeError) as e:
        return [f"match_criteria does not run in the rule matcher: {e}"]
    return []


def check_postings(conn, apply_actions):
    """Return (problems, new_contacts) for the apply actions."""
    if not isinstance(apply_actions, dict):
        return (["apply_actions must be an object"], [])
    postings = apply_actions.get('postings')
    if not isinstance(postings, list) or not postings:
        return (["apply_actions needs at least one posting"], [])
    if not all(isinstance(p, dict) for p in postings):
        return (["Each posting must be an object"], [])

    problems = []
    missing = [i + 1 for i, p in enumerate(postings) if not p.get('account_code')]
    if missing:
        problems.append(f"Posting {', '.join(map(str, missing))} has no account_code")

    codes = [p['account_code'] for p in postings if p.get('account_code')]
    invalid_codes = journal_engine.validate_account_codes(conn, codes)
    if invalid_codes:
        problems.append(f"Account code not in the chart of accounts: {', '.join(invalid_codes)}")

    tags = [t for p in postings for t in (p.get('tags') or [])]
    invalid_tags = journal_engine.validate_tags(conn, tags)
    if invalid_tags:
        problems.append(f"Tag not found: {', '.join(invalid_tags)}")

    class_names = {apply_actions.get('class_name')} | {p.get('class_name') for p in postings}
    for class_name in sorted(c for c in class_names if c):
        class_error = journal_engine.validate_class(conn, class_name)
        if class_error:
            problems.append(f"Class not found: {class_name}")

    # The amounts must split the way the journal engine splits them.
    if any('direction' in p for p in postings):
        for i, p in enumerate(postings):
            if p.get('direction') not in ('debit', 'credit') or 'amount' not in p:
                problems.append(f"Posting {i + 1} needs a direction of debit or credit and an amount, "
                                "because another posting names a direction")
    else:
        try:
            fixed_total = sum(abs(p['amount']) for p in postings if 'amount' in p)
            split_error, _ = journal_engine.validate_and_calculate_split_amounts(postings, fixed_total)
        except TypeError:
            split_error = "Posting amounts must be whole numbers of cents"
        if split_error:
            problems.append(split_error)

    new_contacts = []
    for p in postings:
        contact = p.get('contact')
        if contact and contact not in new_contacts:
            found = conn.execute("SELECT 1 FROM contacts WHERE name = ?", (contact,)).fetchone()
            if not found:
                new_contacts.append(contact)

    return (problems, new_contacts)


def check_rule(conn, rule):
    """Return (problems, new_contacts) for one rule. Reads only."""
    if not isinstance(rule, dict):
        return (["A rule must be a JSON object"], [])
    problems = []
    unknown = sorted(set(rule) - RULE_KEYS)
    if unknown:
        problems.append(f"Unknown key: {', '.join(unknown)}")
    name = rule.get('name')
    if not isinstance(name, str) or not name.strip():
        problems.append("The rule needs a name")
    priority = rule.get('priority')
    if priority is not None and (not isinstance(priority, int) or isinstance(priority, bool)):
        problems.append("priority must be a whole number")
    problems.extend(check_criteria(rule.get('match_criteria')))
    posting_problems, new_contacts = check_postings(conn, rule.get('apply_actions'))
    problems.extend(posting_problems)
    return (problems, new_contacts)


def specificity(match_criteria):
    """Say how narrow the criteria are: specific, broad, or moderate."""
    conditions = match_criteria['conditions']
    if match_criteria['logic'] == 'any':
        return 'broad'
    if len(conditions) > 1 or any(c['operator'] in EXACT_OPERATORS for c in conditions):
        return 'specific'
    if conditions[0]['operator'] in BROAD_OPERATORS:
        return 'broad'
    return 'moderate'


def choose_priority(conn, rule):
    """Return (priority, reason). A lower number runs first."""
    taken = {row[0] for row in conn.execute("SELECT priority FROM categorization_rules")}

    if rule.get('priority') is not None:
        priority = rule['priority']
        reason = f"given as {priority}"
    else:
        kind = specificity(rule['match_criteria'])
        if kind == 'specific':
            priority = SPECIFIC_PRIORITY
            reason = "specific criteria run first"
        elif kind == 'broad':
            priority = max(taken) + BROAD_STEP if taken else BROAD_START
            reason = "broad criteria run after every other rule"
        else:
            priority = (min(taken) + max(taken)) // 2 if taken else MIDDLE_START
            reason = "moderate criteria run in the middle of the other rules"

    start = priority
    while priority in taken:
        priority += COLLISION_STEP
    if priority != start:
        reason += f". {start} was taken, so it moved to {priority}"
    return (priority, reason)


def write_audit(conn, rule_id, action, field_changes, reason, changed_by):
    conn.execute(
        """
        INSERT INTO audit_log (
            id, table_name, record_id, action,
            field_changes, reason, changed_by, changed_at
        )
        VALUES (?, 'categorization_rules', ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            str(uuid.uuid4()),
            rule_id,
            action,
            json.dumps(field_changes) if field_changes is not None else None,
            reason,
            changed_by,
        )
    )


def test_summary(results):
    """Split the test output into its counts and the lines worth reading."""
    counts = {k: v for k, v in results.items() if k not in ('details', 'success')}
    mismatches = [d for d in results['details'] if d['comparison'] == 'mismatch']
    partials = [d for d in results['details'] if d['comparison'] == 'match_with_warnings']
    return (counts, mismatches, partials)


def test_passes(results):
    return results['rule_matches'] == 0 or results['accuracy_percentage'] == 100.0


def create_rule(conn, rule, changed_by):
    """Check, insert switched off, test, and switch on one rule."""
    name = rule.get('name') if isinstance(rule, dict) else None
    out = {
        'name': name,
        'rule_id': None,
        'status': 'failed',
        'priority': None,
        'priority_reason': None,
        'new_contacts': [],
        'errors': [],
        'test': None,
        'mismatch_details': [],
        'partial_match_details': [],
    }

    problems, new_contacts = check_rule(conn, rule)
    out['new_contacts'] = new_contacts
    if problems:
        out['errors'] = problems
        return out

    apply_actions = dict(rule['apply_actions'])
    # The journal engine reads contact on every posting, so it is always present.
    apply_actions['postings'] = [{'contact': None, **p} for p in apply_actions['postings']]

    rule_id = str(uuid.uuid4())
    try:
        priority, reason = choose_priority(conn, rule)
        conn.execute(
            """
            INSERT INTO categorization_rules (id, priority, name, match_criteria, apply_actions, active)
            VALUES (?, ?, ?, ?, ?, 0)
            """,
            (rule_id, priority, rule['name'].strip(),
             json.dumps(rule['match_criteria']), json.dumps(apply_actions))
        )
        write_audit(conn, rule_id, 'insert', None, "Rule created switched off", changed_by)

        results = test_cat_rule.run_test(conn, rule_id)
        counts, mismatches, partials = test_summary(results)

        if test_passes(results):
            conn.execute("UPDATE categorization_rules SET active = 1 WHERE id = ?", (rule_id,))
            write_audit(conn, rule_id, 'update', {'active': [0, 1]},
                        "Switched on after the test passed", changed_by)
            out['status'] = 'active'
        else:
            out['status'] = 'inactive'
        conn.commit()
    except Exception as e:
        conn.rollback()
        out['errors'] = [f"Nothing was written: {e}"]
        return out

    out.update({
        'rule_id': rule_id,
        'priority': priority,
        'priority_reason': reason,
        'test': counts,
        'mismatch_details': mismatches,
        'partial_match_details': partials,
    })
    return out


def create_rules(conn, rules, changed_by):
    """Create each rule in turn. One failure does not stop the rest."""
    outcomes = [create_rule(conn, rule, changed_by) for rule in rules]
    return {
        'success': True,
        'created': sum(1 for o in outcomes if o['status'] != 'failed'),
        'active': sum(1 for o in outcomes if o['status'] == 'active'),
        'inactive': sum(1 for o in outcomes if o['status'] == 'inactive'),
        'failed': sum(1 for o in outcomes if o['status'] == 'failed'),
        'rules': outcomes,
    }


def load_state(conn, rule_id):
    row = conn.execute(
        "SELECT name, active FROM categorization_rules WHERE id = ?", (rule_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"No rule has the id {rule_id}")
    return row[0], bool(row[1])


def activate_rule(conn, rule_id, force_reason, changed_by):
    """Test a switched-off rule again and switch it on if it passes."""
    name, active = load_state(conn, rule_id)
    out = {'success': False, 'rule_id': rule_id, 'name': name, 'forced': False}
    if active:
        out['error'] = "The rule is already switched on"
        return out

    try:
        results = test_cat_rule.run_test(conn, rule_id)
        counts, mismatches, partials = test_summary(results)
        out.update({'test': counts, 'mismatch_details': mismatches,
                    'partial_match_details': partials})

        if test_passes(results):
            reason = "Switched on after the test passed"
        elif force_reason:
            reason = f"Switched on at {counts['accuracy_percentage']} percent: {force_reason.strip()}"
            out['forced'] = True
        else:
            out['error'] = ("The test did not pass, so the rule stays off. "
                            "Fix the rule or the history, or pass --force with a reason.")
            return out

        conn.execute("UPDATE categorization_rules SET active = 1 WHERE id = ?", (rule_id,))
        write_audit(conn, rule_id, 'update', {'active': [0, 1]}, reason, changed_by)
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    out['success'] = True
    return out


def deactivate_rule(conn, rule_id, changed_by):
    """Switch off a rule that is switched on."""
    name, active = load_state(conn, rule_id)
    out = {'success': False, 'rule_id': rule_id, 'name': name}
    if not active:
        out['error'] = "The rule is already switched off"
        return out

    try:
        conn.execute("UPDATE categorization_rules SET active = 0 WHERE id = ?", (rule_id,))
        write_audit(conn, rule_id, 'update', {'active': [1, 0]}, "Switched off", changed_by)
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    out['success'] = True
    return out


def delete_rule(conn, rule_id, changed_by):
    """Delete a rule that is switched off. A switched-on rule is refused."""
    name, active = load_state(conn, rule_id)
    out = {'success': False, 'rule_id': rule_id, 'name': name}
    if active:
        out['error'] = "The rule is switched on. Switch it off with --deactivate first."
        return out

    try:
        row = conn.execute(
            "SELECT priority, match_criteria, apply_actions FROM categorization_rules WHERE id = ?",
            (rule_id,)
        ).fetchone()
        conn.execute("DELETE FROM categorization_rules WHERE id = ?", (rule_id,))
        write_audit(conn, rule_id, 'delete',
                    {'name': name, 'priority': row[0],
                     'match_criteria': json.loads(row[1]), 'apply_actions': json.loads(row[2])},
                    "Rule deleted", changed_by)
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    out['success'] = True
    return out


def main():
    """Main execution function."""
    args = parse_arguments()

    try:
        conn = sqlite3.connect(config_loader.get_db_path())
        conn.execute("PRAGMA foreign_keys = ON")
    except sqlite3.Error as e:
        print(json.dumps({
            "success": False,
            "error": f"Database connection failed: {str(e)}"
        }))
        sys.exit(1)

    try:
        if args.activate:
            result = activate_rule(conn, args.activate, args.force, args.changed_by)
        elif args.deactivate:
            result = deactivate_rule(conn, args.deactivate, args.changed_by)
        elif args.delete:
            result = delete_rule(conn, args.delete, args.changed_by)
        else:
            result = create_rules(conn, load_rules_input(args), args.changed_by)

        print(json.dumps(result, indent=2))
        sys.exit(0 if result['success'] else 1)

    except ValueError as e:
        print(json.dumps({"success": False, "error": str(e)}, indent=2))
        sys.exit(1)

    except Exception as e:
        print(json.dumps({"success": False, "error": f"Unexpected error: {str(e)}"}, indent=2))
        sys.exit(1)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
