#!/usr/bin/env python3
"""The window between a QuickBooks create and the staging save (NO real QBO calls).

The publisher creates the QuickBooks object, then writes its id into staging. With one
save at the end of a phase, a crash lost the id of every object that had already posted,
and the next run created each one again. What each turn of the loop did now reaches the
database before the next one reaches QuickBooks. A crash therefore costs the one object in
flight.

Closing that last one needs a record of the intent written before the create, which is
later work. Two tests cover it: one passes and pins what the publisher does today, and one
is an expected failure that says what should happen.

Run:
    python3 -m unittest scripts.tests.test_qbo_publish_crash_window
"""

import os
import sqlite3
import sys
import unittest
from unittest import mock

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(os.path.dirname(THIS_DIR))

# The consumed-credit module owns the fixture builders and the adapter loader for this
# subject. Import it under its own name, so unittest and this module share one copy and
# one set of the process-global entries its loader writes.
if SKILL_DIR not in sys.path:
    sys.path.insert(0, SKILL_DIR)
import scripts.tests.test_qbo_publish_consumed_credits as cc  # noqa: E402


@unittest.skipUnless(cc.QBO_SDK_PRESENT, cc.SOR_SKIP_REASON)
class CreateThenRecordTests(unittest.TestCase):
    """The window between the QuickBooks create and the staging save."""

    def setUp(self):
        cc._load_modules()
        self.conn, self.path = cc.make_temp_db()
        self.captured = []
        self._next = [1000]

    def tearDown(self):
        self.conn.close()
        os.remove(self.path)

    def _fake_publish(self, client, rate_limiter, obj, env_path):
        if len(self.captured) >= self._crash_after:
            raise KeyboardInterrupt('the process was killed')
        self._next[0] += 1
        ext = str(self._next[0])
        obj.Id = ext
        self.captured.append(ext)
        return ext, None

    def _run(self, conn, crash_after=None, crash_before_record=False):
        """crash_after: how many creates succeed before the process dies.
        crash_before_record: die between the create and the staging write instead."""
        self._crash_after = 10 ** 6 if crash_after is None else crash_after
        patches = [
            mock.patch.object(cc.payments_pub, 'publish_single_qbo_object', self._fake_publish),
            mock.patch.object(cc.payments_pub.QBOPayment, 'save', lambda self, qb=None: self),
        ]
        if crash_before_record:
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

    def _reopen(self):
        """A crash ends the process, so whatever was not saved is gone. Read the file back."""
        self.conn.close()
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row

    def _deposits(self, n):
        for i in range(n):
            import_id = cc.insert_import(self.conn, 50000)
            inv_ta = cc.insert_ta(self.conn, 'receivable', 60000, f'INV-{i}', {})
            cm_ta = cc.insert_ta(self.conn, 'credit_memo', 10000, f'CM-{i}', {})
            cc.insert_tap(self.conn, inv_ta, 60000, import_id=import_id)
            cc.insert_tap(self.conn, cm_ta, 10000, import_id=import_id)
        self.conn.commit()

    def test_a_crash_costs_the_deposit_in_flight_and_no_others(self):
        """Three deposits, the process killed after the second create. The two that posted
        carry their ids, so the second run has one deposit left to post."""
        self._deposits(3)
        self._run(self.conn, crash_after=2)
        self.assertEqual(len(self.captured), 2, 'two deposits reached QuickBooks')

        self._reopen()
        before = len(self.captured)
        self._run(self.conn)
        self.assertEqual(len(self.captured) - before, 1,
                         'the second run posted the deposit that was in flight')

    def test_a_deposit_is_on_the_file_before_the_next_one_posts(self):
        """Read the database from a second connection while the run is still going. Two
        deposits published so far means two rows already carry an id, whether or not the
        phase ever reaches its end."""
        self._deposits(3)
        seen = []
        other = sqlite3.connect(self.path)
        self.addCleanup(other.close)
        inner = self._fake_publish

        def publish_and_peek(client, rate_limiter, obj, env_path):
            result = inner(client, rate_limiter, obj, env_path)
            seen.append(other.execute(
                "SELECT COUNT(*) FROM trade_account_payments "
                "WHERE json_extract(sync, '$.status') = 'synced'").fetchone()[0])
            return result

        self._crash_after = 10 ** 6
        with mock.patch.object(cc.payments_pub, 'publish_single_qbo_object', publish_and_peek), \
                mock.patch.object(cc.payments_pub.QBOPayment, 'save', lambda self, qb=None: self):
            cc.payments_pub.publish_payout_consumed_credits(
                None, cc._FakeRL(), self.conn, {}, 'pending', None, None, '')

        # Two rows per deposit, and the count is read just after each create.
        self.assertEqual(seen, [0, 2, 4])

    def _singletons(self, n):
        """Deposits with no credit memo, so the rows take the singleton path."""
        for i in range(n):
            import_id = cc.insert_import(self.conn, 60000)
            inv_ta = cc.insert_ta(self.conn, 'receivable', 60000, f'INV-S{i}', {})
            cc.insert_tap(self.conn, inv_ta, 60000, import_id=import_id)
        self.conn.commit()

    def _run_singletons(self, conn, crash_after=None):
        self._crash_after = 10 ** 6 if crash_after is None else crash_after
        with mock.patch.object(cc.payments_pub, 'publish_single_qbo_object',
                               self._fake_publish), \
                mock.patch.object(cc.payments_pub.QBOPayment, 'save',
                                  lambda self, qb=None: self):
            try:
                cc.payments_pub.publish_payments(
                    None, cc._FakeRL(), conn, {}, 'pending', None, None, '')
            except KeyboardInterrupt:
                pass

    def test_the_singleton_path_costs_the_payment_in_flight_and_no_others(self):
        """Most books spend most of their rows on the singleton path, so it takes the same
        crash. Three plain invoice collections, the process killed after the second
        create."""
        self._singletons(3)
        self._run_singletons(self.conn, crash_after=2)
        self.assertEqual(len(self.captured), 2, 'two payments reached QuickBooks')

        self._reopen()
        before = len(self.captured)
        self._run_singletons(self.conn)
        self.assertEqual(len(self.captured) - before, 1,
                         'the second run posted the payment that was in flight')

    def test_today_a_crash_before_the_record_posts_the_deposit_twice(self):
        """What the publisher does today in the window the per-row save cannot reach. This
        passes, so a fixture that stops working turns it red. The expected failure below
        says what should happen instead."""
        self._deposits(1)
        self._run(self.conn, crash_before_record=True)
        self.assertEqual(len(self.captured), 1, 'the first run posted one Payment')

        self._reopen()
        before = len(self.captured)
        self._run(self.conn)
        self.assertEqual(len(self.captured) - before, 1,
                         'the second run posted the same deposit again')

    @unittest.expectedFailure
    def test_a_crash_before_the_record_does_not_double_post(self):
        """The one case the per-deposit save cannot reach. The Payment exists in QuickBooks
        and staging never learned its id, so the next run posts it again. Closing this needs
        the publisher to record what it is about to create before it creates, which is its
        own unit."""
        self._deposits(1)
        self._run(self.conn, crash_before_record=True)
        self.assertEqual(len(self.captured), 1, 'the first run posted one Payment')

        self._reopen()
        before = len(self.captured)
        self._run(self.conn)
        self.assertEqual(len(self.captured) - before, 0,
                         'the second run leaves the posted deposit alone')


if __name__ == '__main__':
    unittest.main()
