#!/usr/bin/env python3
"""
Tests for create_cat_rule.py. A new rule goes in switched off, is tested
against the history, and is switched on only when the test passes. The
follow-up flags switch a rule on or off, or delete it, without raw SQL.

Run from the bookkeeping skill directory:
    uv run --no-project --with-requirements requirements.txt python3 -m unittest scripts.tests.test_create_cat_rule
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
import uuid

# Path setup matches how skill scripts import _shared
SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SCRIPTS_DIR, '_shared'))
sys.path.insert(0, SCRIPTS_DIR)

import create_cat_rule  # noqa: E402

SCHEMA_PATH = os.path.join(
    os.path.dirname(SCRIPTS_DIR),  # bookkeeping/
    'reference', 'schema.sql'
)


def make_temp_db():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    conn = sqlite3.connect(path)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.execute("INSERT INTO chart_of_accounts (code, name, type) VALUES ('1000', 'Checking', 'asset')")
    conn.execute("INSERT INTO chart_of_accounts (code, name, type) VALUES ('6100', 'Software', 'expense')")
    conn.execute("INSERT INTO chart_of_accounts (code, name, type) VALUES ('6200', 'Office Supplies', 'expense')")
    conn.execute("INSERT INTO contacts (name, meta) VALUES ('Paperjam Co', '{}')")
    conn.execute("INSERT INTO tags (name, category) VALUES ('Admin', 'Department')")
    conn.execute("INSERT INTO tags (name, category) VALUES ('ClassA', 'Class')")
    conn.commit()
    return conn, path


def add_history(conn, reference, account_code, contact, amount_cents=-2500):
    """Insert a processed import and the journal entry it was coded to."""
    iid = str(uuid.uuid4())
    je = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO imports (id, source, type, banking_date, amount, raw_data, processed) "
        "VALUES (?, '1000 - Checking', 'bank_feed', '2026-05-01', ?, ?, 1)",
        (iid, amount_cents, json.dumps({'Reference': reference, 'Balance Type': 'cash'})),
    )
    conn.execute(
        "INSERT INTO journal_entries (id, import_id, transaction_date) VALUES (?, ?, '2026-05-01')",
        (je, iid),
    )
    conn.execute("INSERT OR IGNORE INTO contacts (name, meta) VALUES (?, '{}')", (contact,))
    for code, direction in (('1000', 'credit'), (account_code, 'debit')):
        conn.execute(
            "INSERT INTO postings (id, journal_entry_id, account_code, direction, amount, contact, tags) "
            "VALUES (?, ?, ?, ?, ?, ?, '[]')",
            (str(uuid.uuid4()), je, code, direction, abs(amount_cents), contact),
        )
    conn.commit()


def contains_rule(name, text, account_code, contact=None, priority=None):
    rule = {
        'name': name,
        'match_criteria': {
            'logic': 'all',
            'conditions': [{'field': 'reference', 'operator': 'contains', 'value': text}],
        },
        'apply_actions': {'postings': [{'account_code': account_code, 'contact': contact}]},
    }
    if priority is not None:
        rule['priority'] = priority
    return rule


class CreateCatRuleTests(unittest.TestCase):

    def setUp(self):
        self.conn, self.path = make_temp_db()

    def tearDown(self):
        self.conn.close()
        os.remove(self.path)

    # ----- helpers -----

    def _create(self, rule):
        return create_cat_rule.create_rule(self.conn, rule, 'test')

    def _row(self, rule_id):
        return self.conn.execute(
            "SELECT priority, active FROM categorization_rules WHERE id = ?", (rule_id,)
        ).fetchone()

    def _rule_count(self):
        return self.conn.execute("SELECT COUNT(*) FROM categorization_rules").fetchone()[0]

    # ----- tests -----

    def test_passing_rule_is_switched_on(self):
        """History agrees with the rule, so it goes in and is switched on."""
        add_history(self.conn, 'CLOUDNOTE SUBSCRIPTION', '6100', 'Cloudnote')
        add_history(self.conn, 'CLOUDNOTE SUBSCRIPTION', '6100', 'Cloudnote')
        out = self._create(contains_rule('Cloudnote Subscription', 'cloudnote', '6100', 'Cloudnote'))
        self.assertEqual(out['status'], 'active', out)
        self.assertEqual(out['test']['rule_matches'], 2)
        self.assertEqual(out['test']['accuracy_percentage'], 100.0)
        self.assertEqual(self._row(out['rule_id'])[1], 1)

    def test_mismatching_rule_stays_off_and_lists_mismatches(self):
        """One history line went to another account, so the rule stays off."""
        add_history(self.conn, 'PAPERJAM ORDER', '6200', 'Paperjam Co')
        add_history(self.conn, 'PAPERJAM ORDER', '6100', 'Paperjam Co')
        out = self._create(contains_rule('Paperjam Orders', 'paperjam', '6200', 'Paperjam Co'))
        self.assertEqual(out['status'], 'inactive', out)
        self.assertEqual(out['test']['accuracy_percentage'], 50.0)
        self.assertEqual(len(out['mismatch_details']), 1)
        detail = out['mismatch_details'][0]
        self.assertEqual(detail['comparison'], 'mismatch')
        self.assertIn('mismatch_reason', detail)
        self.assertEqual(self._row(out['rule_id'])[1], 0)

    def test_zero_matches_is_switched_on(self):
        """No history line matches, which counts as a pass."""
        add_history(self.conn, 'SOMETHING ELSE', '6200', 'Paperjam Co')
        out = self._create(contains_rule('Brightlamp Energy', 'brightlamp', '6200', 'Brightlamp'))
        self.assertEqual(out['status'], 'active', out)
        self.assertEqual(out['test']['rule_matches'], 0)
        self.assertEqual(out['new_contacts'], ['Brightlamp'])
        self.assertEqual(self._row(out['rule_id'])[1], 1)

    def test_bad_account_code_writes_nothing(self):
        out = self._create(contains_rule('Bad Account', 'x', '9999'))
        self.assertEqual(out['status'], 'failed')
        self.assertTrue(any('9999' in e for e in out['errors']), out['errors'])
        self.assertIsNone(out['rule_id'])
        self.assertEqual(self._rule_count(), 0)
        audit = self.conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        self.assertEqual(audit, 0)

    def test_bad_criteria_class_and_tag_write_nothing(self):
        rule = contains_rule('Bad Parts', 'x', '6200')
        rule['match_criteria']['conditions'][0]['operator'] = 'resembles'
        rule['apply_actions']['class_name'] = 'NoSuchClass'
        rule['apply_actions']['postings'][0]['tags'] = ['NoSuchTag']
        out = self._create(rule)
        self.assertEqual(out['status'], 'failed')
        joined = ' '.join(out['errors'])
        self.assertIn('resembles', joined)
        self.assertIn('NoSuchClass', joined)
        self.assertIn('NoSuchTag', joined)
        self.assertEqual(self._rule_count(), 0)

    def test_empty_conditions_are_refused(self):
        """A rule with no conditions would match every line."""
        rule = contains_rule('Everything', 'x', '6200')
        rule['match_criteria']['conditions'] = []
        out = self._create(rule)
        self.assertEqual(out['status'], 'failed')
        self.assertIn('at least one condition', ' '.join(out['errors']))
        self.assertEqual(self._rule_count(), 0)

    def test_priority_collision_moves_by_five(self):
        first = self._create(contains_rule('First', 'aaa', '6200', priority=50))
        second = self._create(contains_rule('Second', 'bbb', '6200', priority=50))
        self.assertEqual(first['priority'], 50)
        self.assertEqual(second['priority'], 55)
        self.assertIn('taken', second['priority_reason'])
        self.assertEqual(self._row(second['rule_id'])[0], 55)

    def test_chosen_priority_follows_specificity(self):
        broad = self._create(contains_rule('Broad', 'aaa', '6200'))
        self.assertEqual(broad['priority'], 100)
        specific = contains_rule('Specific', 'bbb', '6200')
        specific['match_criteria']['conditions'].append(
            {'field': 'amount', 'operator': 'equals_number', 'value': -2500})
        out = self._create(specific)
        self.assertEqual(out['priority'], 10)
        later_broad = self._create(contains_rule('Later Broad', 'ccc', '6200'))
        self.assertEqual(later_broad['priority'], 110)

    def test_activate_retests_and_refuses_below_100(self):
        add_history(self.conn, 'PAPERJAM ORDER', '6200', 'Paperjam Co')
        add_history(self.conn, 'PAPERJAM ORDER', '6100', 'Paperjam Co')
        out = self._create(contains_rule('Paperjam Orders', 'paperjam', '6200', 'Paperjam Co'))
        rule_id = out['rule_id']
        refused = create_cat_rule.activate_rule(self.conn, rule_id, None, 'test')
        self.assertFalse(refused['success'])
        self.assertEqual(len(refused['mismatch_details']), 1)
        self.assertEqual(self._row(rule_id)[1], 0)

        # The history is corrected, so the re-test passes.
        self.conn.execute("UPDATE postings SET account_code = '6200' WHERE account_code = '6100'")
        self.conn.commit()
        passed = create_cat_rule.activate_rule(self.conn, rule_id, None, 'test')
        self.assertTrue(passed['success'], passed)
        self.assertEqual(passed['test']['accuracy_percentage'], 100.0)
        self.assertEqual(self._row(rule_id)[1], 1)

    def test_activate_force_records_the_reason(self):
        add_history(self.conn, 'PAPERJAM ORDER', '6200', 'Paperjam Co')
        add_history(self.conn, 'PAPERJAM ORDER', '6100', 'Paperjam Co')
        out = self._create(contains_rule('Paperjam Orders', 'paperjam', '6200', 'Paperjam Co'))
        forced = create_cat_rule.activate_rule(
            self.conn, out['rule_id'], 'The 6100 line was a one-off', 'test')
        self.assertTrue(forced['success'], forced)
        self.assertTrue(forced['forced'])
        self.assertEqual(self._row(out['rule_id'])[1], 1)
        reason = self.conn.execute(
            "SELECT reason FROM audit_log WHERE record_id = ? AND action = 'update'",
            (out['rule_id'],),
        ).fetchone()[0]
        self.assertIn('one-off', reason)

    def test_delete_refuses_a_switched_on_rule(self):
        out = self._create(contains_rule('Brightlamp Energy', 'brightlamp', '6200'))
        self.assertEqual(out['status'], 'active')
        refused = create_cat_rule.delete_rule(self.conn, out['rule_id'], 'test')
        self.assertFalse(refused['success'])
        self.assertEqual(self._rule_count(), 1)

    def test_deactivate_then_delete(self):
        out = self._create(contains_rule('Brightlamp Energy', 'brightlamp', '6200'))
        self.assertEqual(out['status'], 'active')
        switched_off = create_cat_rule.deactivate_rule(self.conn, out['rule_id'], 'test')
        self.assertTrue(switched_off['success'], switched_off)
        self.assertEqual(self._row(out['rule_id'])[1], 0)
        deleted = create_cat_rule.delete_rule(self.conn, out['rule_id'], 'test')
        self.assertTrue(deleted['success'], deleted)
        self.assertEqual(self._rule_count(), 0)
        actions = [r[0] for r in self.conn.execute(
            "SELECT field_changes FROM audit_log WHERE record_id = ? AND action = 'update'",
            (out['rule_id'],))]
        self.assertIn(json.dumps({'active': [1, 0]}), actions)

    def test_deactivate_refuses_a_switched_off_rule(self):
        add_history(self.conn, 'PAPERJAM ORDER', '6100', 'Paperjam Co')
        out = self._create(contains_rule('Paperjam Orders', 'paperjam', '6200', 'Paperjam Co'))
        self.assertEqual(out['status'], 'inactive')
        refused = create_cat_rule.deactivate_rule(self.conn, out['rule_id'], 'test')
        self.assertFalse(refused['success'])
        self.assertIn('already switched off', refused['error'])
        self.assertEqual(self._row(out['rule_id'])[1], 0)

    def test_delete_removes_a_switched_off_rule(self):
        add_history(self.conn, 'PAPERJAM ORDER', '6100', 'Paperjam Co')
        out = self._create(contains_rule('Paperjam Orders', 'paperjam', '6200', 'Paperjam Co'))
        self.assertEqual(out['status'], 'inactive')
        deleted = create_cat_rule.delete_rule(self.conn, out['rule_id'], 'test')
        self.assertTrue(deleted['success'], deleted)
        self.assertEqual(self._rule_count(), 0)

    def test_batch_keeps_going_past_a_failure(self):
        rules = [
            contains_rule('Bad Account', 'x', '9999'),
            contains_rule('Brightlamp Energy', 'brightlamp', '6200'),
        ]
        out = create_cat_rule.create_rules(self.conn, rules, 'test')
        self.assertEqual(out['failed'], 1)
        self.assertEqual(out['active'], 1)
        self.assertEqual(self._rule_count(), 1)


if __name__ == '__main__':
    unittest.main()
