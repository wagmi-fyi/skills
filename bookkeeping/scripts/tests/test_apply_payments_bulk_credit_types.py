#!/usr/bin/env python3
"""
Tests for apply_payments_bulk.py interaction with new credit_memo / vendor_credit types.

Key concerns from the adversarial review:
- is_credit_memo() must short-circuit on type='credit_memo'/'vendor_credit'
- apply_payments_bulk must reject CM/VC TAs (they go through apply_credit.py instead)
- The legacy 'receivable' + reversed-direction credit memo path must keep working

Run:
    .venv/bin/python -m unittest scripts.tests.test_apply_payments_bulk_credit_types
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SCRIPTS_DIR, '_shared'))
sys.path.insert(0, SCRIPTS_DIR)

from trade_account_utils import is_credit_memo, compute_amount_due, compute_paid_amount, compute_applied_amount

# apply_payments_bulk.py lives in SCRIPTS_DIR
import importlib.util
_apb_spec = importlib.util.spec_from_file_location(
    "apply_payments_bulk",
    os.path.join(SCRIPTS_DIR, "apply_payments_bulk.py"),
)
apply_payments_bulk = importlib.util.module_from_spec(_apb_spec)
_apb_spec.loader.exec_module(apply_payments_bulk)
hamilton_split = apply_payments_bulk.hamilton_split
allocate_settlement_2d = apply_payments_bulk.allocate_settlement_2d

SCHEMA_PATH = os.path.join(
    os.path.dirname(SCRIPTS_DIR), 'reference', 'schema.sql'
)


def make_temp_db():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    conn = sqlite3.connect(path)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    for code, name, typ in [
        ('1001', 'Bank', 'asset'),
        ('1200', 'A/R', 'asset'),
        ('2000', 'A/P', 'liability'),
        ('4000', 'Sales Adj', 'income'),
        ('92830', 'Consulting', 'expense'),
        ('86000', 'FX Loss', 'expense'),
    ]:
        conn.execute("INSERT INTO chart_of_accounts (code, name, type) VALUES (?,?,?)", (code, name, typ))
    conn.execute("INSERT INTO contacts (name) VALUES ('CustA')")
    conn.commit()
    return conn, path


def make_config_yaml(db_path):
    f = tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False)
    f.write(f"""
user_name: Test
client_name: Test
client_id: test
module_root: "~/.claude/skills/bookkeeping"
local_dir: "{os.path.dirname(db_path)}"
output_folder: "{os.path.dirname(db_path)}/_bk-output"
database_dir: "{os.path.dirname(db_path)}"
database_name: "{os.path.basename(db_path)}"
default_system_of_record: "QBO"
period_type: "date-range"
fiscal_calendar: ""
coding:
  min_confidence_to_categorize: 5
  min_confidence_to_auto_approve: 9
""")
    f.close()
    return f.name


def insert_je_with_postings(conn, postings, contact, txn_date='2026-04-01'):
    je_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO journal_entries (id, transaction_date, memo, sync) VALUES (?, ?, ?, ?)",
        (je_id, txn_date, 'test', '{"status":"pending"}')
    )
    for p in postings:
        conn.execute(
            "INSERT INTO postings (id, journal_entry_id, account_code, direction, amount, contact) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), je_id, p['account_code'], p['direction'], p['amount'], contact)
        )
    return je_id


def insert_ta(conn, ta_type, contact, je_id, balance_account_code):
    ta_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO trade_accounts (id, type, contact, document_date, journal_entry_id, sync, metadata) "
        "VALUES (?, ?, ?, '2026-04-01', ?, '{\"status\":\"pending\"}', ?)",
        (ta_id, ta_type, contact, je_id, json.dumps({"balance_account_code": balance_account_code}))
    )
    return ta_id


class ApplyPaymentsBulkCreditTypesTests(unittest.TestCase):

    def setUp(self):
        self.conn, self.path = make_temp_db()

    def tearDown(self):
        self.conn.close()
        os.remove(self.path)

    def test_legacy_receivable_credit_memo_still_detected(self):
        """A 'receivable' TA with reversed posting direction still detects as CM."""
        # CM-style: net CR on A/R (1200)
        je = insert_je_with_postings(self.conn, [
            {'account_code': '4000', 'direction': 'debit', 'amount': 500},
            {'account_code': '1200', 'direction': 'credit', 'amount': 500},
        ], 'CustA')
        self.conn.commit()
        # Legacy path: type='receivable' but functionally a CM
        self.assertTrue(is_credit_memo(self.conn, je, '1200', 'receivable'))

    def test_normal_receivable_not_detected(self):
        """Standard receivable invoice: net DR on A/R, returns False."""
        je = insert_je_with_postings(self.conn, [
            {'account_code': '1200', 'direction': 'debit', 'amount': 500},
            {'account_code': '4000', 'direction': 'credit', 'amount': 500},
        ], 'CustA')
        self.conn.commit()
        self.assertFalse(is_credit_memo(self.conn, je, '1200', 'receivable'))

    def test_first_class_cm_short_circuits(self):
        """type='credit_memo' returns True without inspecting postings."""
        # Even with no postings, short-circuit returns True
        self.assertTrue(is_credit_memo(self.conn, 'fake_je', 'fake', 'credit_memo'))
        self.assertTrue(is_credit_memo(self.conn, 'fake_je', 'fake', 'vendor_credit'))

    def test_apply_payments_bulk_rejects_credit_memo_ta(self):
        """Trying to use apply_payments_bulk on a CM TA must fail loudly."""
        # Build a CM TA + matching import for the bank line
        cm_je = insert_je_with_postings(self.conn, [
            {'account_code': '4000', 'direction': 'debit', 'amount': 1000},
            {'account_code': '1200', 'direction': 'credit', 'amount': 1000},
        ], 'CustA')
        cm = insert_ta(self.conn, 'credit_memo', 'CustA', cm_je, '1200')

        # Insert a bank import (apply_payments_bulk needs --import_id)
        imp_id = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO imports (id, source, type, banking_date, amount, raw_data, processed) "
            "VALUES (?, '1001 - Bank', 'feed', '2026-04-15', 50000, ?, 0)",
            (imp_id, json.dumps({
                "Description": "test",
                "Balance Type": "cash",
                "Account Code": "1001",
                "Reference": "test",
            }))
        )
        self.conn.commit()

        cfg = make_config_yaml(self.path)
        env = os.environ.copy()
        env['BOOKKEEPING_CONFIG_PATH'] = cfg

        proc = subprocess.run(
            [sys.executable,
             os.path.join(SCRIPTS_DIR, 'apply_payments_bulk.py'),
             '--import_id', imp_id,
             '--payment_date', '2026-04-15',
             '--payments', json.dumps([{"trade_account_id": cm, "amount": 1000}])],
            env=env, capture_output=True, text=True
        )
        os.unlink(cfg)

        # Expect failure with credit_memo error
        self.assertNotEqual(proc.returncode, 0)
        out = proc.stdout + proc.stderr
        self.assertIn('credit_memo', out)
        self.assertIn('apply_credit.py', out)


class HamiltonSplitTests(unittest.TestCase):
    """Pure-unit tests for the largest-remainder helper."""

    def test_equal_weights(self):
        out = hamilton_split(100, [100, 100, 100])
        self.assertEqual(sum(out), 100)
        # Each floor is 33; one entry gets +1 to reach 100
        self.assertTrue(all(33 <= v <= 34 for v in out), out)
        self.assertEqual(sorted(out), [33, 33, 34])

    def test_uneven_weights_sum_invariant(self):
        out = hamilton_split(100, [1, 2, 3, 4])
        self.assertEqual(sum(out), 100)
        # Floors: 10, 20, 30, 40 (sum 100) — no residual
        self.assertEqual(out, [10, 20, 30, 40])

    def test_small_amount_large_weights(self):
        out = hamilton_split(5, [100, 1])
        self.assertEqual(sum(out), 5)
        # Floors: 4, 0; residual 1 → goes to largest remainder
        # rem[0] = 500 - 4*101 = 96; rem[1] = 5 - 0*101 = 5; index 0 wins
        self.assertEqual(out, [5, 0])

    def test_deterministic_tie_break(self):
        # When fractional remainders tie, lower index wins
        out_a = hamilton_split(10, [3, 3, 3])
        out_b = hamilton_split(10, [3, 3, 3])
        self.assertEqual(out_a, out_b)
        self.assertEqual(sum(out_a), 10)

    def test_empty_weights_raises(self):
        with self.assertRaises(ValueError):
            hamilton_split(100, [])

    def test_zero_sum_weights_raises(self):
        with self.assertRaises(ValueError):
            hamilton_split(100, [0, 0, 0])

    def test_negative_sum_weights_raises(self):
        with self.assertRaises(ValueError):
            hamilton_split(100, [-1, -1])


class AllocateSettlement2DTests(unittest.TestCase):
    """Pure-unit tests for the settlement allocator."""

    def test_single_cm_14r(self):
        """14R × 1CM, production-derived face values (real rounding edge case)."""
        r_remaining = [210922, 570652, 637974, 595979, 549485, 564652, 552556,
                       456222, 369810, 570662, 389562, 471147, 577300, 297629]
        cm_remaining = [4287451]
        deposit = sum(r_remaining) - sum(cm_remaining)
        self.assertEqual(deposit, 2527101, "settlement invariant check")

        cash, cm_apps = allocate_settlement_2d(r_remaining, cm_remaining)

        self.assertEqual(sum(cash), 2527101)
        self.assertEqual(sum(cm_apps[i][0] for i in range(14)), 4287451)
        for i in range(14):
            self.assertEqual(cash[i] + cm_apps[i][0], r_remaining[i],
                             f"row closure failed for R[{i}]")
            self.assertGreater(cash[i], 0)
            self.assertGreater(cm_apps[i][0], 0)

    def test_multi_cm_rounding(self):
        """3R × 2CM with weights designed to force rounding."""
        r_remaining = [100, 200, 300]  # sum 600
        cm_remaining = [150, 250]      # sum 400; deposit = 200
        cash, cm_apps = allocate_settlement_2d(r_remaining, cm_remaining)

        # Column closure (Hamilton per CM)
        for j in range(2):
            col_sum = sum(cm_apps[i][j] for i in range(3))
            self.assertEqual(col_sum, cm_remaining[j], f"CM[{j}] column sum")

        # Row closure (by construction)
        for i in range(3):
            self.assertEqual(cash[i] + sum(cm_apps[i]), r_remaining[i],
                             f"row closure R[{i}]")
            self.assertGreaterEqual(cash[i], 0)

        # Deposit closure
        self.assertEqual(sum(cash), 200)

    def test_zero_cm_amounts_allowed(self):
        """A zero-amount CM contributes 0 to all R-TAs (degenerate but valid)."""
        r_remaining = [100, 100]
        cm_remaining = [0, 50]
        cash, cm_apps = allocate_settlement_2d(r_remaining, cm_remaining)
        # CM[0] is all zeros
        self.assertEqual(cm_apps[0][0], 0)
        self.assertEqual(cm_apps[1][0], 0)
        # CM[1] sums to 50
        self.assertEqual(cm_apps[0][1] + cm_apps[1][1], 50)
        # Row closure
        for i in range(2):
            self.assertEqual(cash[i] + sum(cm_apps[i]), r_remaining[i])

    def test_cm_exceeds_r_remaining_raises(self):
        """Malformed settlement: CMs exceed an R-TA's remaining → negative cash → raise."""
        with self.assertRaises(ValueError):
            allocate_settlement_2d([10], [50, 50])

    def test_settlement_10r_5cm(self):
        """10R × 5CM, production-derived face values (real rounding edge case).

        Stress test for the small-R edge case: R[1] face is $15.34,
        which is far smaller than the largest face ($41,736.29). Hamilton must
        distribute CMs across this wide weight range without producing negative cash.
        """
        r_remaining = [4173629, 1534, 79918, 301741, 489457, 547416, 484146,
                       462168, 337604, 377046]   # 10 receivables
        cm_remaining = [29356, 57642, 38139, 6574, 1378]   # 5 credit memos
        deposit = sum(r_remaining) - sum(cm_remaining)
        self.assertEqual(deposit, 7121570, "settlement invariant")

        cash, cm_apps = allocate_settlement_2d(r_remaining, cm_remaining)

        # Deposit closure
        self.assertEqual(sum(cash), 7121570)
        # Per-CM column closure
        for j in range(5):
            self.assertEqual(sum(cm_apps[i][j] for i in range(10)),
                             cm_remaining[j],
                             f"CM[{j}] column closure")
        # Per-R row closure (exact by construction)
        for i in range(10):
            self.assertEqual(cash[i] + sum(cm_apps[i]), r_remaining[i],
                             f"R[{i}] row closure (face={r_remaining[i]})")
            self.assertGreaterEqual(cash[i], 0,
                                    f"R[{i}] cash negative (face={r_remaining[i]})")

    def test_empty_r_raises(self):
        with self.assertRaises(ValueError):
            allocate_settlement_2d([], [50])

    def test_empty_cm_raises(self):
        """Pure-R settlements should use the bypass path, not this function."""
        with self.assertRaises(ValueError):
            allocate_settlement_2d([100, 200], [])


class EndToEndMultiCMSettlementTests(unittest.TestCase):
    """Subprocess test exercising apply_payments_bulk.py with --auto_resolve_settlement
    on a multi-R, multi-CM settlement."""

    def setUp(self):
        self.conn, self.path = make_temp_db()

    def tearDown(self):
        self.conn.close()
        os.remove(self.path)

    def _insert_settlement_ta(self, ta_type, contact, je_id, balance_account_code,
                              settlement_id, document_date='2026-04-01'):
        ta_id = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO trade_accounts (id, type, contact, document_date, "
            "journal_entry_id, sync, metadata) "
            "VALUES (?, ?, ?, ?, ?, '{\"status\":\"pending\"}', ?)",
            (ta_id, ta_type, contact, document_date, je_id,
             json.dumps({"balance_account_code": balance_account_code,
                         "settlement_id": settlement_id}))
        )
        return ta_id

    def test_3R_2CM_settlement(self):
        """3 receivables + 2 credit memos, mixed settlement deposit."""
        sid = 'TEST-SID-001'
        # R-TAs: faces 100, 200, 300 cents (account 1200 = A/R)
        r_faces = [100, 200, 300]
        r_ids = []
        for i, face in enumerate(r_faces):
            je = insert_je_with_postings(self.conn, [
                {'account_code': '1200', 'direction': 'debit', 'amount': face},
                {'account_code': '4000', 'direction': 'credit', 'amount': face},
            ], 'CustA', txn_date=f'2026-04-{i+1:02d}')
            r_ids.append(self._insert_settlement_ta(
                'receivable', 'CustA', je, '1200', sid,
                document_date=f'2026-04-{i+1:02d}'))

        # CM-TAs: faces 50, 100 cents (CR 1200)
        cm_faces = [50, 100]
        cm_ids = []
        for i, face in enumerate(cm_faces):
            je = insert_je_with_postings(self.conn, [
                {'account_code': '4000', 'direction': 'debit', 'amount': face},
                {'account_code': '1200', 'direction': 'credit', 'amount': face},
            ], 'CustA', txn_date=f'2026-04-{i+10:02d}')
            cm_ids.append(self._insert_settlement_ta(
                'credit_memo', 'CustA', je, '1200', sid,
                document_date=f'2026-04-{i+10:02d}'))

        # Deposit: sum_R - sum_CM = 600 - 150 = 450 cents
        deposit_cents = sum(r_faces) - sum(cm_faces)
        imp_id = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO imports (id, source, type, banking_date, amount, raw_data, processed) "
            "VALUES (?, '1001 - Bank', 'feed', '2026-04-15', ?, ?, 0)",
            (imp_id, deposit_cents, json.dumps({
                "Description": "test deposit",
                "Balance Type": "cash",
                "Account Code": "1001",
                "Reference": "test",
            }))
        )
        self.conn.commit()

        cfg = make_config_yaml(self.path)
        env = os.environ.copy()
        env['BOOKKEEPING_CONFIG_PATH'] = cfg

        proc = subprocess.run(
            [sys.executable,
             os.path.join(SCRIPTS_DIR, 'apply_payments_bulk.py'),
             '--import_id', imp_id,
             '--payment_date', '2026-04-15',
             '--auto_resolve_settlement',
             '--settlement_id', sid,
             '--allow_mixed_credit'],
            env=env, capture_output=True, text=True
        )
        os.unlink(cfg)

        self.assertEqual(proc.returncode, 0,
                         f"adapter failed: stdout={proc.stdout} stderr={proc.stderr}")

        # Re-open the DB (subprocess wrote to it)
        conn2 = sqlite3.connect(self.path)
        try:
            # TAP count: 3 cash + 3×2 cm-app = 9
            n_taps = conn2.execute(
                "SELECT COUNT(*) FROM trade_account_payments tap "
                "JOIN trade_accounts ta ON ta.id = tap.trade_account_id "
                "WHERE json_extract(ta.metadata, '$.settlement_id') = ?",
                (sid,)
            ).fetchone()[0]
            self.assertEqual(n_taps, 9, "expect 3 cash + 6 cm-app = 9 TAPs")

            # Cash TAPs: one per R, source_ta_id IS NULL
            n_cash = conn2.execute(
                "SELECT COUNT(*) FROM trade_account_payments tap "
                "WHERE tap.source_ta_id IS NULL "
                "AND json_extract(tap.metadata, '$.settlement_id') = ?",
                (sid,)
            ).fetchone()[0]
            self.assertEqual(n_cash, 3)
            sum_cash = conn2.execute(
                "SELECT COALESCE(SUM(tap.amount), 0) FROM trade_account_payments tap "
                "WHERE tap.source_ta_id IS NULL "
                "AND json_extract(tap.metadata, '$.settlement_id') = ?",
                (sid,)
            ).fetchone()[0]
            self.assertEqual(sum_cash, deposit_cents)

            # CM-app TAPs: one per (R, CM) pair = 3×2 = 6
            n_cm_app = conn2.execute(
                "SELECT COUNT(*) FROM trade_account_payments tap "
                "WHERE tap.source_ta_id IS NOT NULL "
                "AND json_extract(tap.metadata, '$.settlement_id') = ?",
                (sid,)
            ).fetchone()[0]
            self.assertEqual(n_cm_app, 6)

            # Each R-TA remaining = 0
            for r_id in r_ids:
                amt = compute_amount_due(conn2, conn2.execute(
                    "SELECT journal_entry_id FROM trade_accounts WHERE id = ?",
                    (r_id,)).fetchone()[0], '1200')
                paid = compute_paid_amount(conn2, r_id)
                self.assertEqual(amt - paid, 0, f"R {r_id[:8]} remaining != 0")

            # Each CM remaining = 0
            for cm_id in cm_ids:
                amt = compute_amount_due(conn2, conn2.execute(
                    "SELECT journal_entry_id FROM trade_accounts WHERE id = ?",
                    (cm_id,)).fetchone()[0], '1200')
                applied = compute_applied_amount(conn2, cm_id)
                self.assertEqual(amt - applied, 0, f"CM {cm_id[:8]} remaining != 0")

            # Clearing JE balanced
            clearing_je_id = conn2.execute(
                "SELECT DISTINCT json_extract(tap.metadata, '$.clearing_je_id') "
                "FROM trade_account_payments tap "
                "JOIN trade_accounts ta ON ta.id = tap.trade_account_id "
                "WHERE json_extract(ta.metadata, '$.settlement_id') = ?",
                (sid,)
            ).fetchone()[0]
            self.assertIsNotNone(clearing_je_id)
            dr, cr = conn2.execute("""
                SELECT
                  COALESCE(SUM(CASE WHEN direction='debit' THEN amount ELSE 0 END), 0),
                  COALESCE(SUM(CASE WHEN direction='credit' THEN amount ELSE 0 END), 0)
                FROM postings WHERE journal_entry_id = ?
            """, (clearing_je_id,)).fetchone()
            self.assertEqual(dr, cr, "clearing JE imbalanced")

            # JE shape check: in settlement+CM mode, JE has 1 bank DR + N R CRs at face + M CM DRs at face
            n_postings = conn2.execute(
                "SELECT COUNT(*) FROM postings WHERE journal_entry_id = ?",
                (clearing_je_id,)
            ).fetchone()[0]
            self.assertEqual(n_postings, 1 + 3 + 2, "expect 1 bank + 3 R + 2 CM postings")
        finally:
            conn2.close()

    def test_settlement_shape_10R_5CM(self):
        """10 receivables + 5 credit memos — exercises the mixed-settlement shape.

        Uses production-derived face values so the rounding
        behavior matches a real-world case. Verifies adapter produces 60 TAPs
        (10 cash + 50 cm-app), all R-TAs remaining=0, JE has 16 postings (1 bank
        + 10 R-clears + 5 CM-consumes), and balances.
        """
        sid = 'TEST-SID-1'
        # Production-derived face values (cents) — chosen to exercise rounding.
        r_faces = [4173629, 1534, 79918, 301741, 489457, 547416, 484146,
                   462168, 337604, 377046]
        cm_faces = [29356, 57642, 38139, 6574, 1378]

        r_ids = []
        for i, face in enumerate(r_faces):
            je = insert_je_with_postings(self.conn, [
                {'account_code': '1200', 'direction': 'debit', 'amount': face},
                {'account_code': '4000', 'direction': 'credit', 'amount': face},
            ], 'MarketplaceCust', txn_date=f'2026-03-{25+i:02d}' if i < 7 else f'2026-04-{i-6:02d}')
            r_ids.append(self._insert_settlement_ta(
                'receivable', 'MarketplaceCust', je, '1200', sid,
                document_date=f'2026-03-{25+i:02d}' if i < 7 else f'2026-04-{i-6:02d}'))

        cm_ids = []
        for i, face in enumerate(cm_faces):
            je = insert_je_with_postings(self.conn, [
                {'account_code': '4000', 'direction': 'debit', 'amount': face},
                {'account_code': '1200', 'direction': 'credit', 'amount': face},
            ], 'MarketplaceCust', txn_date=f'2026-03-{26+i:02d}')
            cm_ids.append(self._insert_settlement_ta(
                'credit_memo', 'MarketplaceCust', je, '1200', sid,
                document_date=f'2026-03-{26+i:02d}'))

        deposit_cents = sum(r_faces) - sum(cm_faces)
        self.assertEqual(deposit_cents, 7121570, "deposit invariant")

        imp_id = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO imports (id, source, type, banking_date, amount, raw_data, processed) "
            "VALUES (?, '1001 - Bank', 'feed', '2026-04-10', ?, ?, 0)",
            (imp_id, deposit_cents, json.dumps({
                "Description": "MARKETPLACE SETTLEMENT",
                "Balance Type": "cash",
                "Account Code": "1001",
                "Reference": "TEST-SID-1",
            }))
        )
        self.conn.commit()

        cfg = make_config_yaml(self.path)
        env = os.environ.copy()
        env['BOOKKEEPING_CONFIG_PATH'] = cfg

        proc = subprocess.run(
            [sys.executable,
             os.path.join(SCRIPTS_DIR, 'apply_payments_bulk.py'),
             '--import_id', imp_id,
             '--payment_date', '2026-04-10',
             '--auto_resolve_settlement',
             '--settlement_id', sid,
             '--allow_mixed_credit'],
            env=env, capture_output=True, text=True
        )
        os.unlink(cfg)

        self.assertEqual(proc.returncode, 0,
                         f"adapter failed: stdout={proc.stdout} stderr={proc.stderr}")
        result = json.loads(proc.stdout)
        self.assertTrue(result['success'])
        self.assertEqual(result['payments_created'], 10)
        self.assertEqual(result['credit_apps_created'], 50)

        conn2 = sqlite3.connect(self.path)
        try:
            # TAP totals
            n_taps = conn2.execute(
                "SELECT COUNT(*) FROM trade_account_payments tap "
                "JOIN trade_accounts ta ON ta.id = tap.trade_account_id "
                "WHERE json_extract(ta.metadata, '$.settlement_id') = ?",
                (sid,)
            ).fetchone()[0]
            self.assertEqual(n_taps, 60, "10 cash + 50 cm-app = 60 TAPs")

            # Sum of cash TAPs == deposit
            sum_cash = conn2.execute(
                "SELECT COALESCE(SUM(tap.amount), 0) FROM trade_account_payments tap "
                "WHERE tap.source_ta_id IS NULL "
                "AND json_extract(tap.metadata, '$.settlement_id') = ?",
                (sid,)
            ).fetchone()[0]
            self.assertEqual(sum_cash, deposit_cents)

            # Per-CM: sum of cm-app TAPs sourced from that CM == CM face
            for cm_id, face in zip(cm_ids, cm_faces):
                cm_app_sum = conn2.execute(
                    "SELECT COALESCE(SUM(amount), 0) FROM trade_account_payments "
                    "WHERE source_ta_id = ?", (cm_id,)
                ).fetchone()[0]
                self.assertEqual(cm_app_sum, face,
                                 f"CM {cm_id[:8]} face={face} sum_cm_apps={cm_app_sum}")

            # Each R-TA remaining = 0 (including the small one)
            for r_id, r_face in zip(r_ids, r_faces):
                je_id = conn2.execute(
                    "SELECT journal_entry_id FROM trade_accounts WHERE id = ?",
                    (r_id,)).fetchone()[0]
                amt = compute_amount_due(conn2, je_id, '1200')
                paid = compute_paid_amount(conn2, r_id)
                self.assertEqual(amt - paid, 0,
                                 f"R {r_id[:8]} (face={r_face}) remaining {amt-paid} != 0")

            # Each CM remaining = 0
            for cm_id in cm_ids:
                je_id = conn2.execute(
                    "SELECT journal_entry_id FROM trade_accounts WHERE id = ?",
                    (cm_id,)).fetchone()[0]
                amt = compute_amount_due(conn2, je_id, '1200')
                applied = compute_applied_amount(conn2, cm_id)
                self.assertEqual(amt - applied, 0,
                                 f"CM {cm_id[:8]} remaining != 0")

            # Clearing JE: 1 bank + 10 R + 5 CM = 16 postings, balanced
            clearing_je_id = conn2.execute(
                "SELECT DISTINCT json_extract(tap.metadata, '$.clearing_je_id') "
                "FROM trade_account_payments tap "
                "JOIN trade_accounts ta ON ta.id = tap.trade_account_id "
                "WHERE json_extract(ta.metadata, '$.settlement_id') = ?",
                (sid,)
            ).fetchone()[0]
            n_postings = conn2.execute(
                "SELECT COUNT(*) FROM postings WHERE journal_entry_id = ?",
                (clearing_je_id,)
            ).fetchone()[0]
            self.assertEqual(n_postings, 16, "expect 1 bank + 10 R + 5 CM = 16 postings")
            dr, cr = conn2.execute("""
                SELECT
                  COALESCE(SUM(CASE WHEN direction='debit' THEN amount ELSE 0 END), 0),
                  COALESCE(SUM(CASE WHEN direction='credit' THEN amount ELSE 0 END), 0)
                FROM postings WHERE journal_entry_id = ?
            """, (clearing_je_id,)).fetchone()
            self.assertEqual(dr, cr)
            # GL invariant: bank_DR ($71,215.70) + Σ CM_face ($1,330.89) = Σ R_face ($72,546.59)
            self.assertEqual(dr, 7254659)

            # Smallest R-TA (face 1534 = $15.34) must have cash >= 0 — the stress case
            small_r_id = r_ids[1]
            small_cash = conn2.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM trade_account_payments "
                "WHERE trade_account_id = ? AND source_ta_id IS NULL", (small_r_id,)
            ).fetchone()[0]
            self.assertGreaterEqual(small_cash, 0,
                                    f"small R (face=1534) cash={small_cash} negative")
        finally:
            conn2.close()


class StandaloneLineSplitTests(unittest.TestCase):
    """Subprocess tests for --standalone_lines (signed import-split) + --adjustments deprecation."""

    def setUp(self):
        self.conn, self.path = make_temp_db()

    def tearDown(self):
        self.conn.close()
        os.remove(self.path)

    def _insert_import(self, amount_cents, ref='test'):
        imp_id = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO imports (id, source, type, banking_date, amount, raw_data, processed) "
            "VALUES (?, '1001 - Bank', 'feed', '2026-04-15', ?, ?, 0)",
            (imp_id, amount_cents, json.dumps({
                "Description": "test", "Balance Type": "cash",
                "Account Code": "1001", "Reference": ref,
            }))
        )
        return imp_id

    def _insert_receivable(self, face):
        je = insert_je_with_postings(self.conn, [
            {'account_code': '1200', 'direction': 'debit', 'amount': face},
            {'account_code': '4000', 'direction': 'credit', 'amount': face},
        ], 'CustA')
        return insert_ta(self.conn, 'receivable', 'CustA', je, '1200')

    def _insert_payable(self, face):
        je = insert_je_with_postings(self.conn, [
            {'account_code': '92830', 'direction': 'debit', 'amount': face},
            {'account_code': '2000', 'direction': 'credit', 'amount': face},
        ], 'VendX')
        return insert_ta(self.conn, 'payable', 'VendX', je, '2000')

    def _run(self, args_list):
        cfg = make_config_yaml(self.path)
        env = os.environ.copy()
        env['BOOKKEEPING_CONFIG_PATH'] = cfg
        proc = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, 'apply_payments_bulk.py')] + args_list,
            env=env, capture_output=True, text=True
        )
        os.unlink(cfg)
        return proc

    def _je_balanced(self, conn, je_id):
        dr, cr = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN direction='debit' THEN amount ELSE 0 END),0), "
            "COALESCE(SUM(CASE WHEN direction='credit' THEN amount ELSE 0 END),0) "
            "FROM postings WHERE journal_entry_id=?", (je_id,)
        ).fetchone()
        self.assertEqual(dr, cr, f"JE {je_id[:8]} imbalanced ({dr} != {cr})")

    def test_no_standalone_identical_to_today(self):
        """No --standalone_lines: single clearing JE, bank == import, no standalone JE."""
        inv = self._insert_receivable(50000)
        imp = self._insert_import(50000)
        self.conn.commit()
        proc = self._run([
            '--import_id', imp, '--payment_date', '2026-04-15',
            '--payments', json.dumps([{"trade_account_id": inv, "amount": 50000}]),
        ])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIsNone(result['standalone_je_id'])
        self.assertEqual(result['clearing_bank_cents'], 50000)
        self.assertEqual(result['import_total_cents'], 50000)
        self.assertEqual(result['standalone_bank_cents'], 0)

    def test_co_disbursement_withdrawal_split(self):
        """$11,931.64 ACH = $231.64 bill + $11,700 standalone consulting.
        Standalone slice runs the SAME direction as the wire; clearing < import."""
        bill = self._insert_payable(23164)
        imp = self._insert_import(-1193164)  # withdrawal
        self.conn.commit()
        proc = self._run([
            '--import_id', imp, '--payment_date', '2026-04-15',
            '--payments', json.dumps([{"trade_account_id": bill, "amount": 23164}]),
            '--standalone_lines', json.dumps([
                {"account_code": "92830", "amount": 1170000, "direction": "debit",
                 "contact": "Jane Doe", "description": "consulting"}
            ]),
        ])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIsNotNone(result['standalone_je_id'])
        self.assertEqual(result['import_total_cents'], 1193164)
        self.assertEqual(result['clearing_bank_cents'], 23164)
        self.assertEqual(result['standalone_bank_cents'], 1170000)
        self.assertEqual(result['clearing_bank_cents'] + result['standalone_bank_cents'],
                         result['import_total_cents'])

        conn2 = sqlite3.connect(self.path)
        try:
            cje, sje = result['clearing_je_id'], result['standalone_je_id']
            self.assertIn('ignore', conn2.execute(
                "SELECT sync FROM journal_entries WHERE id=?", (cje,)).fetchone()[0])
            ssync, simport = conn2.execute(
                "SELECT sync, import_id FROM journal_entries WHERE id=?", (sje,)).fetchone()
            self.assertIn('pending', ssync)
            self.assertIsNone(simport, "standalone JE must not carry import_id")
            sp = {r[0]: r for r in conn2.execute(
                "SELECT account_code, direction, amount, contact FROM postings "
                "WHERE journal_entry_id=?", (sje,)).fetchall()}
            self.assertEqual(sp['92830'], ('92830', 'debit', 1170000, 'Jane Doe'))
            self.assertEqual(sp['1001'], ('1001', 'credit', 1170000, None))
            self._je_balanced(conn2, cje)
            self._je_balanced(conn2, sje)
        finally:
            conn2.close()

    def test_opposite_direction_slice_on_deposit(self):
        """FX-style: $980 deposit settles a $1,000 invoice + $20 standalone expense.
        Standalone slice runs OPPOSITE the wire; clearing bank (1000) EXCEEDS import (980)."""
        inv = self._insert_receivable(100000)
        imp = self._insert_import(98000)  # deposit
        self.conn.commit()
        proc = self._run([
            '--import_id', imp, '--payment_date', '2026-04-15',
            '--payments', json.dumps([{"trade_account_id": inv, "amount": 100000}]),
            '--standalone_lines', json.dumps([
                {"account_code": "86000", "amount": 2000, "direction": "debit",
                 "contact": "Amazon", "description": "FX loss"}
            ]),
        ])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result['import_total_cents'], 98000)
        self.assertEqual(result['clearing_bank_cents'], 100000)  # exceeds the import
        self.assertEqual(result['standalone_bank_cents'], 2000)
        conn2 = sqlite3.connect(self.path)
        try:
            sje = result['standalone_je_id']
            sp = {r[0]: r for r in conn2.execute(
                "SELECT account_code, direction, amount FROM postings "
                "WHERE journal_entry_id=?", (sje,)).fetchall()}
            self.assertEqual(sp['86000'], ('86000', 'debit', 2000))
            self.assertEqual(sp['1001'], ('1001', 'credit', 2000))  # opposite the deposit
            self._je_balanced(conn2, result['clearing_je_id'])
            self._je_balanced(conn2, sje)
        finally:
            conn2.close()

    def test_adjustments_flag_deprecated(self):
        """A non-empty --adjustments is now a hard error pointing at the new paths."""
        inv = self._insert_receivable(50000)
        imp = self._insert_import(50000)
        self.conn.commit()
        proc = self._run([
            '--import_id', imp, '--payment_date', '2026-04-15',
            '--payments', json.dumps([{"trade_account_id": inv, "amount": 50000}]),
            '--adjustments', json.dumps([
                {"account_code": "86000", "amount": 100, "direction": "debit"}]),
        ])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('retired', proc.stdout + proc.stderr)

    def test_standalone_missing_account_code_errors(self):
        imp = self._insert_import(50000)
        self.conn.commit()
        proc = self._run([
            '--import_id', imp, '--payment_date', '2026-04-15',
            '--standalone_lines', json.dumps([{"amount": 100, "direction": "debit"}]),
        ])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('account_code', proc.stdout + proc.stderr)

    def test_standalone_bank_account_collision_errors(self):
        imp = self._insert_import(50000)
        self.conn.commit()
        proc = self._run([
            '--import_id', imp, '--payment_date', '2026-04-15',
            '--standalone_lines', json.dumps([
                {"account_code": "1001", "amount": 100, "direction": "debit", "contact": "X"}]),
        ])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('bank account', proc.stdout + proc.stderr)

    def test_no_settlement_cash_errors(self):
        """A standalone debit equal to a withdrawal leaves zero settlement cash → error."""
        imp = self._insert_import(-50000)  # withdrawal
        self.conn.commit()
        proc = self._run([
            '--import_id', imp, '--payment_date', '2026-04-15',
            '--standalone_lines', json.dumps([
                {"account_code": "86000", "amount": 50000, "direction": "debit", "contact": "X"}]),
        ])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('settlement cash', proc.stdout + proc.stderr)


# ---------------------------------------------------------------------------
# Four-type settlement generalization (R + CM + P + VC in one deposit)
# ---------------------------------------------------------------------------

resolve_settlement_payments = apply_payments_bulk.resolve_settlement_payments


def _settlement_postings(ta_type, face):
    """Backing JE postings for a TA of the given type, plus its balance_account_code.

    A/R side uses 1200 (with income 4000); A/P side uses 2000 (with expense 92830).
    """
    if ta_type == 'receivable':
        return [{'account_code': '1200', 'direction': 'debit', 'amount': face},
                {'account_code': '4000', 'direction': 'credit', 'amount': face}], '1200'
    if ta_type == 'credit_memo':
        return [{'account_code': '4000', 'direction': 'debit', 'amount': face},
                {'account_code': '1200', 'direction': 'credit', 'amount': face}], '1200'
    if ta_type == 'payable':
        return [{'account_code': '92830', 'direction': 'debit', 'amount': face},
                {'account_code': '2000', 'direction': 'credit', 'amount': face}], '2000'
    if ta_type == 'vendor_credit':
        return [{'account_code': '2000', 'direction': 'debit', 'amount': face},
                {'account_code': '92830', 'direction': 'credit', 'amount': face}], '2000'
    raise ValueError(f"unknown ta_type {ta_type!r}")


def insert_settlement_ta_with_je(conn, ta_type, face, contact, sid, doc_date='2026-04-01'):
    """Insert a TA of `ta_type` with face `face` cents, tagged into settlement `sid`."""
    postings, bac = _settlement_postings(ta_type, face)
    je_id = insert_je_with_postings(conn, postings, contact, txn_date=doc_date)
    ta_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO trade_accounts (id, type, contact, document_date, journal_entry_id, sync, metadata) "
        "VALUES (?, ?, ?, ?, ?, '{\"status\":\"pending\"}', ?)",
        (ta_id, ta_type, contact, doc_date, je_id,
         json.dumps({"balance_account_code": bac, "settlement_id": sid}))
    )
    return ta_id, bac


class AllocatePayableSideReuseTests(unittest.TestCase):
    """The 2D allocator is type-agnostic; it is reused verbatim for the A/P side
    (distribute P cash after VC offsets), so its closure invariants must hold there."""

    def test_pside_closure(self):
        p_cash, vc_apps = allocate_settlement_2d([500, 300], [100, 50])
        for j, vc in enumerate([100, 50]):                       # column closure
            self.assertEqual(sum(vc_apps[i][j] for i in range(2)), vc)
        for i, p in enumerate([500, 300]):                       # row closure
            self.assertEqual(p_cash[i] + sum(vc_apps[i]), p)
        self.assertEqual(sum(p_cash), (500 + 300) - (100 + 50))  # deposit closure
        self.assertTrue(all(c >= 0 for c in p_cash))

    def test_pside_vc_exceeds_p_raises(self):
        with self.assertRaises(ValueError):
            allocate_settlement_2d([10], [50, 50])


class SettlementFourTypeResolveTests(unittest.TestCase):
    """resolve_settlement_payments generalized to R/CM/P/VC: net formula, per-side
    contact rule, anchoring guards, signed deposit (driven in-process)."""

    def setUp(self):
        self.conn, self.path = make_temp_db()
        for v in ('VendX', 'VendY', 'CustB'):
            self.conn.execute("INSERT INTO contacts (name) VALUES (?)", (v,))

    def tearDown(self):
        self.conn.close()
        os.remove(self.path)

    def _resolve(self, sid, deposit_signed):
        return resolve_settlement_payments(self.conn, sid, deposit_signed)

    def test_full_mix_resolves(self):
        sid = 'MIX-1'
        r, _ = insert_settlement_ta_with_je(self.conn, 'receivable', 1000, 'CustA', sid)
        cm, _ = insert_settlement_ta_with_je(self.conn, 'credit_memo', 100, 'CustA', sid)
        p, _ = insert_settlement_ta_with_je(self.conn, 'payable', 400, 'VendX', sid)
        vc, _ = insert_settlement_ta_with_je(self.conn, 'vendor_credit', 50, 'VendX', sid)
        net = 1000 - 100 - 400 + 50  # 550
        bank, credit_apps, sides, summary = self._resolve(sid, net)

        by_ta = {b['trade_account_id']: b['amount'] for b in bank}
        self.assertEqual(by_ta[r], 1000 - 100)   # R cash after CM offset
        self.assertEqual(by_ta[p], 400 - 50)      # P cash after VC offset
        ca = {c['source_ta_id']: c for c in credit_apps}
        self.assertEqual((ca[cm]['target_ta_id'], ca[cm]['amount']), (r, 100))
        self.assertEqual((ca[vc]['target_ta_id'], ca[vc]['amount']), (p, 50))
        self.assertEqual(sides, {'customer': 'CustA', 'vendor': 'VendX'})
        self.assertEqual(
            (summary['sum_r_cents'], summary['sum_cm_cents'],
             summary['sum_p_cents'], summary['sum_vc_cents'], summary['expected_net_cents']),
            (1000, 100, 400, 50, 550))
        # The two bank slices net to the deposit.
        self.assertEqual(by_ta[r] - by_ta[p], net)

    def test_net_negative_supported(self):
        sid = 'NEG-1'
        r, _ = insert_settlement_ta_with_je(self.conn, 'receivable', 100, 'CustA', sid)
        p, _ = insert_settlement_ta_with_je(self.conn, 'payable', 500, 'VendX', sid)
        net = 100 - 500  # -400 (withdrawal)
        bank, credit_apps, sides, summary = self._resolve(sid, net)
        self.assertEqual(credit_apps, [])
        by_ta = {b['trade_account_id']: b['amount'] for b in bank}
        self.assertEqual((by_ta[r], by_ta[p]), (100, 500))
        self.assertEqual(summary['expected_net_cents'], -400)

    def test_net_mismatch_raises(self):
        sid = 'MM-1'
        insert_settlement_ta_with_je(self.conn, 'receivable', 1000, 'CustA', sid)
        insert_settlement_ta_with_je(self.conn, 'payable', 400, 'VendX', sid)
        with self.assertRaises(ValueError) as ctx:
            self._resolve(sid, 999)  # true net = 600
        self.assertIn('net mismatch', str(ctx.exception))

    def test_pure_p_bypass(self):
        sid = 'PUREP-1'
        insert_settlement_ta_with_je(self.conn, 'payable', 300, 'VendX', sid)
        insert_settlement_ta_with_je(self.conn, 'payable', 200, 'VendX', sid)
        bank, credit_apps, sides, _ = self._resolve(sid, -(300 + 200))
        self.assertEqual(credit_apps, [])                          # allocator bypassed
        self.assertEqual(sorted(b['amount'] for b in bank), [200, 300])
        self.assertEqual((sides['customer'], sides['vendor']), (None, 'VendX'))

    def test_customer_side_multi_contact_raises(self):
        sid = 'CMULTI-1'
        insert_settlement_ta_with_je(self.conn, 'receivable', 1000, 'CustA', sid)
        insert_settlement_ta_with_je(self.conn, 'receivable', 500, 'CustB', sid)
        with self.assertRaises(ValueError) as ctx:
            self._resolve(sid, 1500)
        self.assertIn('A/R side', str(ctx.exception))

    def test_vendor_side_multi_contact_raises(self):
        sid = 'VMULTI-1'
        insert_settlement_ta_with_je(self.conn, 'payable', 400, 'VendX', sid)
        insert_settlement_ta_with_je(self.conn, 'payable', 200, 'VendY', sid)
        with self.assertRaises(ValueError) as ctx:
            self._resolve(sid, -600)
        self.assertIn('A/P side', str(ctx.exception))

    def test_vc_without_payable_raises(self):
        sid = 'VCNOP-1'
        insert_settlement_ta_with_je(self.conn, 'receivable', 1000, 'CustA', sid)
        insert_settlement_ta_with_je(self.conn, 'vendor_credit', 50, 'VendX', sid)
        with self.assertRaises(ValueError) as ctx:
            self._resolve(sid, 1050)
        self.assertIn('vendor_credit', str(ctx.exception))

    def test_cm_without_receivable_raises(self):
        sid = 'CMNOR-1'
        insert_settlement_ta_with_je(self.conn, 'payable', 400, 'VendX', sid)
        insert_settlement_ta_with_je(self.conn, 'credit_memo', 100, 'CustA', sid)
        with self.assertRaises(ValueError) as ctx:
            self._resolve(sid, -500)
        self.assertIn('credit_memo', str(ctx.exception))


class EndToEndMixedSettlementTests(unittest.TestCase):
    """Subprocess test: a single deposit settles R + CM + P + VC across two contacts."""

    def setUp(self):
        self.conn, self.path = make_temp_db()
        self.conn.execute("INSERT INTO contacts (name) VALUES ('VendX')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        os.remove(self.path)

    def _run(self, sid, net_cents):
        imp_id = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO imports (id, source, type, banking_date, amount, raw_data, processed) "
            "VALUES (?, '1001 - Bank', 'feed', '2026-04-15', ?, ?, 0)",
            (imp_id, net_cents, json.dumps({
                "Description": "mixed settlement", "Balance Type": "cash",
                "Account Code": "1001", "Reference": "test",
            }))
        )
        self.conn.commit()
        cfg = make_config_yaml(self.path)
        env = os.environ.copy()
        env['BOOKKEEPING_CONFIG_PATH'] = cfg
        proc = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, 'apply_payments_bulk.py'),
             '--import_id', imp_id, '--payment_date', '2026-04-15',
             '--auto_resolve_settlement', '--settlement_id', sid, '--allow_mixed_credit'],
            env=env, capture_output=True, text=True
        )
        os.unlink(cfg)
        return proc

    def _bank_sum_for_type(self, conn2, sid, ta_type):
        return conn2.execute(
            "SELECT COALESCE(SUM(tap.amount), 0) FROM trade_account_payments tap "
            "JOIN trade_accounts ta ON ta.id = tap.trade_account_id "
            "WHERE tap.source_ta_id IS NULL AND ta.type = ? "
            "AND json_extract(tap.metadata, '$.settlement_id') = ?", (ta_type, sid)).fetchone()[0]

    def _clearing_je(self, conn2, sid):
        return conn2.execute(
            "SELECT DISTINCT json_extract(tap.metadata, '$.clearing_je_id') "
            "FROM trade_account_payments tap "
            "WHERE json_extract(tap.metadata, '$.settlement_id') = ?", (sid,)).fetchone()[0]

    def _dr_cr(self, conn2, je_id):
        return conn2.execute(
            "SELECT COALESCE(SUM(CASE WHEN direction='debit' THEN amount ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN direction='credit' THEN amount ELSE 0 END), 0) "
            "FROM postings WHERE journal_entry_id = ?", (je_id,)).fetchone()

    def test_full_mix_four_types(self):
        sid = 'E2E-MIX-1'
        r1, _ = insert_settlement_ta_with_je(self.conn, 'receivable', 1000, 'CustA', sid)
        r2, _ = insert_settlement_ta_with_je(self.conn, 'receivable', 500, 'CustA', sid)
        cm, _ = insert_settlement_ta_with_je(self.conn, 'credit_memo', 300, 'CustA', sid)
        p1, _ = insert_settlement_ta_with_je(self.conn, 'payable', 400, 'VendX', sid)
        p2, _ = insert_settlement_ta_with_je(self.conn, 'payable', 200, 'VendX', sid)
        vc, _ = insert_settlement_ta_with_je(self.conn, 'vendor_credit', 100, 'VendX', sid)
        ar_net = (1000 + 500) - 300   # 1200
        ap_net = (400 + 200) - 100    # 500
        net = ar_net - ap_net          # 700 (deposit)

        proc = self._run(sid, net)
        self.assertEqual(proc.returncode, 0, f"failed: {proc.stdout}\n{proc.stderr}")

        conn2 = sqlite3.connect(self.path)
        try:
            n_bank = conn2.execute(
                "SELECT COUNT(*) FROM trade_account_payments tap WHERE tap.source_ta_id IS NULL "
                "AND json_extract(tap.metadata, '$.settlement_id') = ?", (sid,)).fetchone()[0]
            self.assertEqual(n_bank, 4, "2 R-cash + 2 P-cash")
            n_capp = conn2.execute(
                "SELECT COUNT(*) FROM trade_account_payments tap WHERE tap.source_ta_id IS NOT NULL "
                "AND json_extract(tap.metadata, '$.settlement_id') = ?", (sid,)).fetchone()[0]
            self.assertEqual(n_capp, 4, "2 CM->R + 2 VC->P")

            # Per-side cash sums and the net bank across the two QBO objects.
            self.assertEqual(self._bank_sum_for_type(conn2, sid, 'receivable'), ar_net)
            self.assertEqual(self._bank_sum_for_type(conn2, sid, 'payable'), ap_net)
            self.assertEqual(
                self._bank_sum_for_type(conn2, sid, 'receivable')
                - self._bank_sum_for_type(conn2, sid, 'payable'), net)

            # Each credit source fully consumed.
            for src, face in ((cm, 300), (vc, 100)):
                self.assertEqual(conn2.execute(
                    "SELECT COALESCE(SUM(amount), 0) FROM trade_account_payments WHERE source_ta_id = ?",
                    (src,)).fetchone()[0], face)

            # remaining == 0 for every TA (targets via paid, sources via applied).
            for ta_id, bac, is_src in ((r1, '1200', False), (r2, '1200', False), (cm, '1200', True),
                                       (p1, '2000', False), (p2, '2000', False), (vc, '2000', True)):
                je = conn2.execute("SELECT journal_entry_id FROM trade_accounts WHERE id = ?",
                                   (ta_id,)).fetchone()[0]
                amt = compute_amount_due(conn2, je, bac)
                used = compute_applied_amount(conn2, ta_id) if is_src else compute_paid_amount(conn2, ta_id)
                self.assertEqual(amt - used, 0, f"TA {ta_id[:8]} remaining != 0")

            cje = self._clearing_je(conn2, sid)
            dr, cr = self._dr_cr(conn2, cje)
            self.assertEqual(dr, cr, "clearing JE imbalanced")
            n_post = conn2.execute("SELECT COUNT(*) FROM postings WHERE journal_entry_id = ?",
                                   (cje,)).fetchone()[0]
            self.assertEqual(n_post, 1 + 2 + 1 + 2 + 1, "1 bank + 2R + 1CM + 2P + 1VC")
            self.assertEqual(
                conn2.execute("SELECT direction, amount FROM postings "
                              "WHERE journal_entry_id = ? AND account_code = '1001'", (cje,)).fetchone(),
                ('debit', net))
        finally:
            conn2.close()

    def test_net_negative_p_dominated(self):
        sid = 'E2E-NEG-1'
        r, _ = insert_settlement_ta_with_je(self.conn, 'receivable', 100, 'CustA', sid)
        p, _ = insert_settlement_ta_with_je(self.conn, 'payable', 500, 'VendX', sid)
        net = 100 - 500  # -400 → import is a withdrawal

        proc = self._run(sid, net)
        self.assertEqual(proc.returncode, 0, f"failed: {proc.stdout}\n{proc.stderr}")

        conn2 = sqlite3.connect(self.path)
        try:
            for ta_id, bac in ((r, '1200'), (p, '2000')):
                je = conn2.execute("SELECT journal_entry_id FROM trade_accounts WHERE id = ?",
                                   (ta_id,)).fetchone()[0]
                self.assertEqual(
                    compute_amount_due(conn2, je, bac) - compute_paid_amount(conn2, ta_id), 0)
            cje = self._clearing_je(conn2, sid)
            dr, cr = self._dr_cr(conn2, cje)
            self.assertEqual(dr, cr)
            # Net-withdrawal: the single net bank line is a CREDIT of |net|.
            self.assertEqual(
                conn2.execute("SELECT direction, amount FROM postings "
                              "WHERE journal_entry_id = ? AND account_code = '1001'", (cje,)).fetchone(),
                ('credit', 400))
        finally:
            conn2.close()


if __name__ == '__main__':
    unittest.main()
