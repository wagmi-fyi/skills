#!/usr/bin/env python3
"""Two holes in the QBO payments publisher (NO real QBO calls).

Hole 1, sync-status complementarity. The consumed-credit selection reads the one sync
status the run was given. A credit memo's payment row it does not take leaves the invoices
on that bank line to publish at full face. The bank then holds more than it received and
the credit memo stays unapplied. The gate reads the credit rows in every status now and
names those invoice rows.

Hole 2, create then record. The publisher creates the QuickBooks Payment, then writes its
id into staging. The phase used to save the database once at the end, so a crash lost the
ids of every object that had already posted. Each row's outcome is saved before the next
row reaches QuickBooks now, so a crash costs the one object in flight. Closing that last
one needs a record of the intent written before the create, which is a unit of its own.
The test for it stays an expected failure here.

Four tests assert the gate stays quiet: an ordinary deposit, a repaired book, a deposit
with no credit memo, and a second customer on one bank line. They pass against the code as
it was before the gate learned this check, so they are evidence about the future rather
than about this change. They are what fails if a later check names a row it should leave
alone.

Run:
    python3 -m unittest scripts.tests.test_qbo_publish_two_holes
"""

import importlib.util
import json
import os
import sqlite3
import sys
import unittest
from unittest import mock

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
SIBLING = os.path.join(THIS_DIR, 'test_qbo_publish_consumed_credits.py')

# The consumed-credit module owns the fixture builders and the adapter loader for this
# subject. It has to exist once in the process: both copies would mutate sys.path and
# sys.modules through its loader, and its unloader would delete those entries under the
# other copy. Take the one unittest already imported, or register the one loaded here
# under the same name.
_SIBLING_NAME = 'scripts.tests.test_qbo_publish_consumed_credits'
cc = sys.modules.get(_SIBLING_NAME)
if cc is None:
    _spec = importlib.util.spec_from_file_location(_SIBLING_NAME, SIBLING)
    cc = importlib.util.module_from_spec(_spec)
    sys.modules[_SIBLING_NAME] = cc
    _spec.loader.exec_module(cc)


def _set_sync(conn, tap_id, status, external_id=None):
    sync = None if status is None else json.dumps({'status': status,
                                                   'external_id': external_id})
    conn.execute("UPDATE trade_account_payments SET sync = ? WHERE id = ?", (sync, tap_id))
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

    def _gaps(self, sync_status='pending'):
        return cc.common.find_bank_funded_payment_gaps(self.conn, sync_status, None, None)

    def _named(self, sync_status='pending'):
        return {(g['payment_id'], g['error_code']) for g in self._gaps(sync_status)}

    def _singletons(self, sync_status='pending'):
        return {r['tap_id'] for r in cc.common.query_trade_account_payments(
            self.conn, sync_status, None, None, ta_type='receivable')}

    def test_a_credit_row_in_another_status_stops_the_run(self):
        """A deposit whose credit row errored on an earlier run. The invoices are still
        pending, so a plain run would select them at full face. The gate names them
        first."""
        _, taps = cc.build_deposit(self.conn, {})
        _set_sync(self.conn, taps['CM'], 'error')

        self.assertEqual(self._named(), {(taps['R1'], 'DEPOSIT_CREDIT_OFF_STATUS'),
                                         (taps['R2'], 'DEPOSIT_CREDIT_OFF_STATUS')})
        # The rows the gate names are the ones the singleton path would have posted.
        self.assertEqual(self._singletons(), {taps['R1'], taps['R2']})

    def test_every_status_the_credit_row_can_carry_stops_the_run(self):
        """error, verify, ignore, synced and a missing sync value all hide the credit from
        both selections, so each one has to reach the same stop."""
        for status, external_id in [('error', None), ('verify', None), ('ignore', None),
                                    ('synced', 'QBO-PMT-9'), (None, None)]:
            with self.subTest(status=status):
                conn, path = cc.make_temp_db()
                try:
                    _, taps = cc.build_deposit(conn, {})
                    conn.execute(
                        "UPDATE trade_account_payments SET sync = ? WHERE id = ?",
                        (None if status is None
                         else json.dumps({'status': status, 'external_id': external_id}),
                         taps['CM']))
                    conn.commit()
                    named = {(g['payment_id'], g['error_code'])
                             for g in cc.common.find_bank_funded_payment_gaps(
                                 conn, 'pending', None, None)}
                    self.assertEqual(named, {(taps['R1'], 'DEPOSIT_CREDIT_OFF_STATUS'),
                                             (taps['R2'], 'DEPOSIT_CREDIT_OFF_STATUS')})
                finally:
                    conn.close()
                    os.remove(path)

    def test_the_message_names_the_credit_row_and_its_status(self):
        """The invoice row is the one that would post wrong. The credit row is the one a
        person has to change, so the message carries its id and the status it sits in."""
        _, taps = cc.build_deposit(self.conn, {})
        _set_sync(self.conn, taps['CM'], 'verify')

        message = self._gaps()[0]['error_message']
        self.assertIn(taps['CM'], message)
        self.assertIn('verify', message)

    def test_an_ordinary_deposit_is_not_named(self):
        """Every row at the run's status: the consumed-credit phase takes the deposit whole
        and the gate has nothing to say."""
        cc.build_deposit(self.conn, {})
        self.assertEqual(self._gaps(), [])

    def test_a_repaired_book_is_not_named(self):
        """The repair recipe in gotchas.md nets the credit into a posted Payment by hand and
        sets the credit row to ignore. Its invoices are published, so no run selects them and
        the gate stays quiet."""
        _, taps = cc.build_deposit(self.conn, {})
        _set_sync(self.conn, taps['R1'], 'synced', 'QBO-PMT-1')
        _set_sync(self.conn, taps['R2'], 'synced', 'QBO-PMT-1')
        _set_sync(self.conn, taps['CM'], 'ignore')

        self.assertEqual(self._gaps(), [])

    def test_a_deposit_with_no_credit_memo_is_not_named(self):
        import_id = cc.insert_import(self.conn, 60000)
        inv_ta = cc.insert_ta(self.conn, 'receivable', 60000, 'INV-A', {})
        cc.insert_tap(self.conn, inv_ta, 60000, import_id=import_id)
        self.conn.commit()

        self.assertEqual(self._gaps(), [])

    def test_another_customer_on_the_same_bank_line_is_not_named(self):
        """A QBO Payment carries one customer, so the second customer's invoice is its own
        deposit. An off-status credit for the first customer does not reach it."""
        import_id = cc.insert_import(self.conn, 90000)
        inv_a = cc.insert_ta(self.conn, 'receivable', 60000, 'INV-A', {},
                             contact='Northwind Supply')
        inv_b = cc.insert_ta(self.conn, 'receivable', 40000, 'INV-B', {},
                             contact='Dockside Freight')
        cm_ta = cc.insert_ta(self.conn, 'credit_memo', 10000, 'CM-1', {},
                             contact='Northwind Supply')
        tap_a = cc.insert_tap(self.conn, inv_a, 60000, import_id=import_id)
        tap_b = cc.insert_tap(self.conn, inv_b, 40000, import_id=import_id)
        tap_cm = cc.insert_tap(self.conn, cm_ta, 10000, import_id=import_id)
        self.conn.commit()
        _set_sync(self.conn, tap_cm, 'error')

        self.assertEqual(self._named(), {(tap_a, 'DEPOSIT_CREDIT_OFF_STATUS')})
        self.assertNotIn(tap_b, {g['payment_id'] for g in self._gaps()})

    def test_a_settlement_keyed_bank_line_reaches_the_same_stop(self):
        """A settlement carries its cash net of the credit, so its deposit key is NULL by
        design and no key comparison reaches it. A bank-funded credit memo inside one is a
        second claim on money the settlement already accounted for, so the line's payments
        and the money that arrived stop agreeing."""
        for status in ('pending', 'error', 'ignore'):
            with self.subTest(status=status):
                conn, path = cc.make_temp_db()
                try:
                    import_id = cc.insert_import(conn, 50000)
                    inv_ta = cc.insert_ta(conn, 'receivable', 60000, 'INV-1', {})
                    cm_ta = cc.insert_ta(conn, 'credit_memo', 10000, 'CM-1', {})
                    tap_inv = cc.insert_tap(conn, inv_ta, 60000, import_id=import_id)
                    tap_cm = cc.insert_tap(conn, cm_ta, 10000, import_id=import_id)
                    for tap_id in (tap_inv, tap_cm):
                        conn.execute(
                            "UPDATE trade_account_payments SET metadata = "
                            "json_set(metadata, '$.settlement_id', 'SET-1') WHERE id = ?",
                            (tap_id,))
                    _set_sync(conn, tap_cm, status)
                    named = {(g['payment_id'], g['error_code'])
                             for g in cc.common.find_bank_funded_payment_gaps(
                                 conn, 'pending', None, None)}
                    self.assertIn((tap_inv, 'DEPOSIT_CREDIT_OFF_STATUS'), named)
                finally:
                    conn.close()
                    os.remove(path)

    def test_a_payout_keyed_deposit_reaches_the_same_stop(self):
        """The third deposit key. A payout-keyed credit row this run will not publish
        leaves its invoices to post at full face like any other."""
        _, taps = cc.build_deposit(self.conn, {'payout_id': 'PO-1'})
        self.assertEqual(self._gaps(), [])
        _set_sync(self.conn, taps['CM'], 'error')
        self.assertEqual(self._named(), {(taps['R1'], 'DEPOSIT_CREDIT_OFF_STATUS'),
                                         (taps['R2'], 'DEPOSIT_CREDIT_OFF_STATUS')})

    def test_a_credit_row_carrying_an_external_id_stops_the_run(self):
        """The row sits at the run's status and the consumed-credit selection still skips
        it, because that selection takes rows with no external id. The message has to give
        the id as the reason, since telling a person to change the status does nothing."""
        _, taps = cc.build_deposit(self.conn, {})
        _set_sync(self.conn, taps['CM'], 'pending', external_id='QBO-PMT-4')

        self.assertEqual(self._named(), {(taps['R1'], 'DEPOSIT_CREDIT_OFF_STATUS'),
                                         (taps['R2'], 'DEPOSIT_CREDIT_OFF_STATUS')})
        message = self._gaps()[0]['error_message']
        self.assertIn('QBO-PMT-4', message)
        self.assertNotIn('sync status', message)

    def test_a_suppressed_credit_row_says_somebody_suppressed_it(self):
        """A person sets a row to ignore to keep it out of QuickBooks. Doing that to a
        bank-funded credit memo whose invoices are unpublished leaves the invoices to post
        at full face, so the run stops and the message gives that reason."""
        _, taps = cc.build_deposit(self.conn, {})
        _set_sync(self.conn, taps['CM'], 'ignore')

        self.assertIn('suppressed', self._gaps()[0]['error_message'])

    def test_a_missing_sync_value_reads_as_words(self):
        """A row with no sync value at all. The message a person reads carries a phrase
        rather than a Python None."""
        _, taps = cc.build_deposit(self.conn, {})
        _set_sync(self.conn, taps['CM'], None)

        message = self._gaps()[0]['error_message']
        self.assertIn('carries no sync status', message)
        self.assertNotIn('None', message)

    def test_two_credit_memos_with_one_out_of_status_stop_the_run(self):
        """One bank line can carry two credit memos. One of them outside the run's
        selection is enough, because its share of the cash still went missing."""
        import_id = cc.insert_import(self.conn, 80000)
        inv_ta = cc.insert_ta(self.conn, 'receivable', 100000, 'INV-A', {})
        cm_one = cc.insert_ta(self.conn, 'credit_memo', 10000, 'CM-1', {})
        cm_two = cc.insert_ta(self.conn, 'credit_memo', 10000, 'CM-2', {})
        tap_inv = cc.insert_tap(self.conn, inv_ta, 100000, import_id=import_id)
        cc.insert_tap(self.conn, cm_one, 10000, import_id=import_id)
        tap_out = cc.insert_tap(self.conn, cm_two, 10000, import_id=import_id)
        self.conn.commit()
        _set_sync(self.conn, tap_out, 'error')

        self.assertEqual(self._named(), {(tap_inv, 'DEPOSIT_CREDIT_OFF_STATUS')})

    def test_a_payable_on_the_same_bank_line_is_left_alone(self):
        """A payable publishes as a BillPayment and the bank nets across the two objects,
        so an off-status credit memo for the customer does not reach it."""
        import_id = cc.insert_import(self.conn, 80000)
        inv_ta = cc.insert_ta(self.conn, 'receivable', 100000, 'INV-A', {})
        bill_ta = cc.insert_ta(self.conn, 'payable', 20000, 'BILL-1', {},
                               contact='Dockside Freight')
        cm_ta = cc.insert_ta(self.conn, 'credit_memo', 10000, 'CM-1', {})
        tap_inv = cc.insert_tap(self.conn, inv_ta, 100000, import_id=import_id)
        tap_bill = cc.insert_tap(self.conn, bill_ta, 20000, import_id=import_id)
        tap_cm = cc.insert_tap(self.conn, cm_ta, 10000, import_id=import_id)
        self.conn.commit()
        _set_sync(self.conn, tap_cm, 'error')

        self.assertEqual(self._named(), {(tap_inv, 'DEPOSIT_CREDIT_OFF_STATUS')})
        self.assertNotIn(tap_bill, {g['payment_id'] for g in self._gaps()})

    def test_an_invoice_row_outside_the_window_is_not_named(self):
        """The gate names rows this run would publish. An invoice dated outside the window
        is another run's work, whatever its bank line's credit memo carries."""
        import_id = cc.insert_import(self.conn, 90000)
        inv_ta = cc.insert_ta(self.conn, 'receivable', 100000, 'INV-A', {})
        cm_ta = cc.insert_ta(self.conn, 'credit_memo', 10000, 'CM-1', {})
        cc.insert_tap(self.conn, inv_ta, 100000, import_id=import_id, date='2026-05-20')
        tap_cm = cc.insert_tap(self.conn, cm_ta, 10000, import_id=import_id)
        self.conn.commit()
        _set_sync(self.conn, tap_cm, 'error')

        self.assertEqual(cc.common.find_bank_funded_payment_gaps(
            self.conn, 'pending', '2026-04-01', '2026-04-30'), [])

    def test_a_retry_run_reaches_the_same_stop(self):
        """Invoice rows reset to error and a credit row left pending. A run asking for error
        would post the invoices at full face, so it stops the same way."""
        _, taps = cc.build_deposit(self.conn, {})
        _set_sync(self.conn, taps['R1'], 'error')
        _set_sync(self.conn, taps['R2'], 'error')

        self.assertEqual(self._named('error'), {(taps['R1'], 'DEPOSIT_CREDIT_OFF_STATUS'),
                                                (taps['R2'], 'DEPOSIT_CREDIT_OFF_STATUS')})


@unittest.skipUnless(cc.QBO_SDK_PRESENT, cc.SOR_SKIP_REASON)
class CreateThenRecordTests(unittest.TestCase):
    """Hole 2: the window between the QuickBooks create and the staging save."""

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
        """The same crash on the path most books spend most of their rows in. Three plain
        invoice collections, the process killed after the second create."""
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
