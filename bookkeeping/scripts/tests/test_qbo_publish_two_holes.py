#!/usr/bin/env python3
"""Two holes in the QBO payments publisher, pinned as expected failures (NO real QBO calls).

Hole 1, sync-status complementarity. The consumed-credit selection and the disjointness
exclusion both require the credit memo's payment row to carry the run's sync status. A
credit row in any other status returns its invoices to the gross singleton path, and the
pre-publish gate reports clean. The invoices post at full face, the bank is over by the
credit, and the credit memo floats.

Hole 2, create then record. The publisher creates the QuickBooks Payment, then writes its
id into staging, then commits once at the end of the phase. A crash inside that window
leaves the rows pending with the Payment posted, and the next run creates a second one.

Both tests assert the behaviour the skill should have. Both fail today, so both carry
`expectedFailure` and the suite stays green. Neither test picks a remedy: hole 2 passes
under a read-back before create and under a named stop alike.

Run:
    python3 -m unittest scripts.tests.test_qbo_publish_two_holes
"""

import importlib.util
import json
import os
import sqlite3
import unittest
from unittest import mock

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
SIBLING = os.path.join(THIS_DIR, 'test_qbo_publish_consumed_credits.py')

# The consumed-credit module owns the fixture builders and the adapter loader for this
# subject. Load it by path so this module runs the same whether unittest names it or
# discovers it.
_spec = importlib.util.spec_from_file_location('consumed_credit_fixtures', SIBLING)
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)


def _set_sync(conn, tap_id, status, external_id=None):
    conn.execute("UPDATE trade_account_payments SET sync = ? WHERE id = ?",
                 (json.dumps({'status': status, 'external_id': external_id}), tap_id))
    conn.commit()


@unittest.skipUnless(cc.QBO_SDK_PRESENT, cc.SOR_SKIP_REASON)
class SyncStatusComplementarityTests(unittest.TestCase):
    """Hole 1: the credit row's sync status decides whether its invoices net."""

    def setUp(self):
        cc._load_modules()
        self.conn, self.path = cc.make_temp_db()

    def tearDown(self):
        self.conn.close()
        os.remove(self.path)

    @unittest.expectedFailure
    def test_a_credit_row_in_another_status_stops_the_run(self):
        """A deposit whose credit row errored on an earlier run. The invoices are still
        pending, so a plain run selects them. Publishing them at full face would put the
        bank over by the credit, so the run must name the rows instead."""
        _, taps = cc.build_deposit(self.conn, {})
        _set_sync(self.conn, taps['CM'], 'error')

        gaps = cc.common.find_bank_funded_payment_gaps(self.conn, 'pending', None, None)
        self.assertEqual({g['payment_id'] for g in gaps}, {taps['R1'], taps['R2']})


@unittest.skipUnless(cc.QBO_SDK_PRESENT, cc.SOR_SKIP_REASON)
class CreateThenRecordTests(unittest.TestCase):
    """Hole 2: the window between the QuickBooks create and the staging commit."""

    def setUp(self):
        cc._load_modules()
        self.conn, self.path = cc.make_temp_db()
        self.captured = []
        self._next = [1000]

    def tearDown(self):
        self.conn.close()
        os.remove(self.path)

    def _fake_publish(self, client, rate_limiter, obj, env_path):
        self._next[0] += 1
        ext = str(self._next[0])
        obj.Id = ext
        self.captured.append(ext)
        return ext, None

    def _run(self, conn, crash=False):
        patches = [
            mock.patch.object(cc.payments_pub, 'publish_single_qbo_object', self._fake_publish),
            mock.patch.object(cc.payments_pub.QBOPayment, 'save', lambda self, qb=None: self),
        ]
        if crash:
            def die(*a, **k):
                raise KeyboardInterrupt('the process was killed')
            patches.append(mock.patch.object(cc.payments_pub, 'update_sync_success', die))
        for p in patches:
            p.start()
        try:
            cc.payments_pub.publish_payout_consumed_credits(
                None, cc._FakeRL(), conn, {}, 'pending', None, None, '')
        except KeyboardInterrupt:
            pass
        finally:
            for p in patches:
                p.stop()

    @unittest.expectedFailure
    def test_a_crash_after_the_create_does_not_double_post(self):
        """The first run posts the Payment and dies before the id reaches staging. The
        second run must not post a second Payment for the same deposit."""
        cc.build_deposit(self.conn, {})
        self._run(self.conn, crash=True)
        self.assertEqual(len(self.captured), 1, 'the first run posted one Payment')

        # A crash ends the process, so the uncommitted staging writes are gone. A second
        # run reads the file as it survived.
        self.conn.close()
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._run(self.conn)

        self.assertEqual(len(self.captured), 1,
                         'the deposit reached QuickBooks once, not twice')


if __name__ == '__main__':
    unittest.main()
