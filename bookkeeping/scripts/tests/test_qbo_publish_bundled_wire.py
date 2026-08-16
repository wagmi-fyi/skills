#!/usr/bin/env python3
"""
Bundled-wire publish contract after retiring the legacy inline CM/VC path.

A single bank wire that pays a bill AND carries a standalone co-disbursement is split by
apply_payments_bulk into a clearing JE (sync=ignore, settled by the BillPayment) and a
standalone JE (sync=pending, published as its own QBO JournalEntry). The QBO publishers
must NOT synthesize a Credit Memo / Vendor Credit from clearing-JE postings anymore.

The QBO publisher modules import the /qbo skill (OAuth) at module load, so they can't be
imported in a config-free unit test. The behavioral half of the contract — standalone JE
is sync=pending + not TA-backed, clearing JE is sync=ignore — is asserted in
test_apply_payments_bulk_credit_types.StandaloneLineSplitTests, and the live publish-query
selection is exercised by a production dry-run regression. These tests lock the durable
STRUCTURAL guarantee that the inline synthesis path stays deleted.

Run:
    .venv/bin/python -m unittest scripts.tests.test_qbo_publish_bundled_wire
"""

import os
import unittest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_ROOT = os.path.dirname(SCRIPTS_DIR)
QBO = os.path.join(SKILL_ROOT, 'adapters', 'qbo')


def _read(*parts):
    with open(os.path.join(QBO, *parts)) as f:
        return f.read()


class InlineSynthesisRetiredTests(unittest.TestCase):
    """The clearing-JE → CM/VC synthesizer and its call sites are gone."""

    def test_detector_removed_from_common(self):
        src = _read('_shared', 'common.py')
        self.assertNotIn('def detect_clearing_je_adjustments', src,
                         "detect_clearing_je_adjustments must be removed from common.py")

    def test_payments_has_no_cm_synthesis(self):
        src = _read('_publishers', 'payments.py')
        self.assertNotIn('detect_clearing_je_adjustments', src,
                         "payments.py must not call the retired detector")
        self.assertNotIn('CreditMemo()', src,
                         "payments.py must not synthesize Credit Memos from adjustments")
        self.assertNotIn('import CreditMemo', src,
                         "the CreditMemo import should be dropped")

    def test_bill_payments_has_no_vc_synthesis(self):
        src = _read('_publishers', 'bill_payments.py')
        self.assertNotIn('detect_clearing_je_adjustments', src,
                         "bill_payments.py must not call the retired detector")
        self.assertNotIn('VendorCredit()', src,
                         "bill_payments.py must not synthesize Vendor Credits from adjustments")
        self.assertNotIn('import VendorCredit', src,
                         "the VendorCredit import should be dropped")

    def test_publish_query_still_excludes_ignore_and_ta_backed(self):
        """The standalone JE (pending, not TA-backed) is published; the clearing JE
        (sync=ignore) and TA-backed JEs are excluded — design intact in the query."""
        src = _read('_publishers', 'journal_entries.py')
        self.assertIn("json_extract(je.sync, '$.status') = ?", src)
        self.assertIn('ta_check.id IS NULL', src)


if __name__ == '__main__':
    unittest.main()
