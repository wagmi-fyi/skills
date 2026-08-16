#!/usr/bin/env python3
"""
Tests for stripe_fc_transactions.py transform_to_universal_json.

Key concerns from the adversarial review:
- Credit-account sign flip: FC emits credit-card purchases as negative; the
  universal contract stores them positive (credit-normal). Cash accounts pass
  through unchanged. A silent regression here inverts every card ledger.
- raw_data must be plain JSON (StripeObject serialized via str() — v15 removed
  to_dict_recursive and dict() raises).
- Dates render from transacted_at as UTC YYYY-MM-DD.

Run:
    .venv/bin/python -m unittest scripts.tests.test_stripe_fc_transforms
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_DIR = os.path.dirname(SCRIPTS_DIR)

# The adapter loads config at import time — point it at a throwaway config.
_tmpdir = tempfile.mkdtemp(prefix='fc-transform-test-')
_local_dir = os.path.join(_tmpdir, '_local-bookkeeping')
os.makedirs(os.path.join(_local_dir, 'adapters'), exist_ok=True)
with open(os.path.join(_local_dir, 'config.yaml'), 'w') as f:
    f.write(
        'local_dir: "{project-root}/_local-bookkeeping"\n'
        'database_dir: "{project-root}/database"\n'
        'database_name: "bookkeeping.db"\n'
    )
os.environ['BOOKKEEPING_CONFIG_PATH'] = os.path.join(_local_dir, 'config.yaml')

_spec = importlib.util.spec_from_file_location(
    "stripe_fc_transactions",
    os.path.join(SKILL_DIR, "adapters", "stripe_fc_transactions.py"),
)
stripe_fc_transactions = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stripe_fc_transactions)
transform_to_universal_json = stripe_fc_transactions.transform_to_universal_json


class FakeTxn:
    """Minimal stand-in for a Stripe FC Transaction: attribute access + JSON str()."""

    def __init__(self, **fields):
        self._fields = fields
        for k, v in fields.items():
            setattr(self, k, v)

    def __str__(self):
        return json.dumps(self._fields)


def make_txn(amount, txn_id='fctxn_test1', transacted_at=1781038098, description='Test vendor'):
    return FakeTxn(
        id=txn_id,
        amount=amount,
        currency='usd',
        description=description,
        status='posted',
        transacted_at=transacted_at,  # default 1781038098 = 2026-06-09 UTC
    )


class TestSignConvention(unittest.TestCase):
    def test_credit_purchase_flips_positive(self):
        # FC emits a card purchase as negative; liability convention stores it positive
        envelope = transform_to_universal_json([make_txn(-1434)], 'credit')
        self.assertEqual(envelope['transactions'][0]['amount'], 1434)

    def test_credit_payment_flips_negative(self):
        # A card payment arrives positive in FC; stored negative (liability pay-down)
        envelope = transform_to_universal_json([make_txn(500000)], 'credit')
        self.assertEqual(envelope['transactions'][0]['amount'], -500000)

    def test_cash_amounts_pass_through(self):
        deposits = transform_to_universal_json([make_txn(428765)], 'cash')
        withdrawals = transform_to_universal_json([make_txn(-50000)], 'cash')
        self.assertEqual(deposits['transactions'][0]['amount'], 428765)
        self.assertEqual(withdrawals['transactions'][0]['amount'], -50000)


class TestEnvelopeShape(unittest.TestCase):
    def test_raw_data_is_plain_json(self):
        envelope = transform_to_universal_json([make_txn(-1434)], 'credit')
        raw = envelope['transactions'][0]['raw_data']
        self.assertIsInstance(raw, dict)
        # Round-trips through json — no StripeObject leakage
        self.assertEqual(json.loads(json.dumps(raw))['id'], 'fctxn_test1')
        # raw_data keeps the SOURCE amount, unflipped — it is evidence, not ledger input
        self.assertEqual(raw['amount'], -1434)

    def test_contract_fields(self):
        envelope = transform_to_universal_json(
            [make_txn(-1434, transacted_at=1781038098)], 'credit'
        )
        t = envelope['transactions'][0]
        self.assertEqual(t['external_id'], 'fctxn_test1')
        self.assertEqual(t['date'], '2026-06-09')
        self.assertEqual(t['balance_type'], 'credit')
        self.assertEqual(t['currency'], 'usd')
        self.assertEqual(t['reference'], 'Test vendor')

    def test_missing_description_falls_back(self):
        txn = make_txn(-100, description=None)
        envelope = transform_to_universal_json([txn], 'credit')
        self.assertEqual(envelope['transactions'][0]['reference'], 'No description')


if __name__ == '__main__':
    unittest.main()
