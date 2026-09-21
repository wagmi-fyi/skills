#!/usr/bin/env python3
"""A bank-funded credit memo the publisher will not post (NO real QBO calls).

The consumed-credit selection reads the one sync status the run was given. A credit memo's
payment row it leaves behind still reduces its bank line, so the payments on that line stop
agreeing with the money that arrived. The gate reads the credit rows in every status and
names the invoice rows on that line.

A credit row carrying an external id, or set to ignore, has been dealt with. What tells a
line repaired by the recipe in gotchas.md from one where somebody put the credit aside
before anything posted is whether an invoice on that line has published. A line with one
passes. A line with none has its invoice rows named.

This module also covers which refusal a half-posted deposit gets.

Nine of these tests pass against the code as it stood before this check existed. They say
nothing about the check working. They are what fails if a later change names a row it
should leave alone.

Run:
    python3 -m unittest scripts.tests.test_qbo_publish_stranded_credits
"""

import json
import os
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

    def _singleton_ids(self, sync_status='pending'):
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
        self.assertEqual(self._singleton_ids(), {taps['R1'], taps['R2']})

    def test_every_status_that_leaves_the_credit_unposted_stops_the_run(self):
        """error, verify and a missing sync value each leave the credit out of the run with
        nothing in QuickBooks to show for it, so each one reaches the same stop."""
        for status, external_id in [('error', None), ('verify', None), (None, None)]:
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
        for status in ('pending', 'error'):
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
                    # The status is not why this row is out, so the message must not
                    # send a person to change it.
                    message = next(
                        g['error_message'] for g in
                        cc.common.find_bank_funded_payment_gaps(conn, 'pending', None, None)
                        if g['payment_id'] == tap_inv)
                    self.assertIn('settled through a channel', message)
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

    def test_a_credit_row_carrying_an_external_id_with_nothing_published_stops_the_run(self):
        """An external id says the credit reached QuickBooks. Nothing on this bank line has
        published, so the invoices would still publish for more than the bank received."""
        _, taps = cc.build_deposit(self.conn, {})
        _set_sync(self.conn, taps['CM'], 'synced', external_id='QBO-CM-4')

        self.assertEqual(self._named(), {(taps['R1'], 'DEPOSIT_CREDIT_OFF_STATUS'),
                                         (taps['R2'], 'DEPOSIT_CREDIT_OFF_STATUS')})
        message = self._gaps()[0]['error_message']
        self.assertIn('QBO-CM-4', message)
        self.assertIn('no invoice on this line has published', message)

    def test_a_credit_row_carrying_an_external_id_on_a_posted_line_is_accounted_for(self):
        """The same row on a line an invoice has already published from. That line is past
        what this check can help with, so a later payment on it still publishes."""
        _, taps = cc.build_deposit(self.conn, {})
        _set_sync(self.conn, taps['R1'], 'synced', external_id='QBO-PMT-1')
        _set_sync(self.conn, taps['CM'], 'synced', external_id='QBO-CM-4')

        self.assertEqual(self._gaps(), [])
        self.assertIn(taps['R2'], self._singleton_ids())

    def test_a_suppressed_credit_row_with_nothing_published_stops_the_run(self):
        """Setting a row to ignore says it will never reach QuickBooks. Doing that before
        any invoice on the line has published leaves the invoices to publish for more than
        the bank received, so the gate names them."""
        _, taps = cc.build_deposit(self.conn, {})
        _set_sync(self.conn, taps['CM'], 'ignore')

        self.assertEqual(self._named(), {(taps['R1'], 'DEPOSIT_CREDIT_OFF_STATUS'),
                                         (taps['R2'], 'DEPOSIT_CREDIT_OFF_STATUS')})
        self.assertIn('it is set to ignore', self._gaps()[0]['error_message'])

    def test_voiding_the_credit_memo_clears_the_stop(self):
        """A credit memo that should never post is voided. The gate reads no voided row, so
        the line publishes."""
        _, taps = cc.build_deposit(self.conn, {})
        _set_sync(self.conn, taps['CM'], 'ignore')
        self.conn.execute(
            "UPDATE trade_accounts SET voided_at = '2026-04-20' WHERE id = "
            "(SELECT trade_account_id FROM trade_account_payments WHERE id = ?)",
            (taps['CM'],))
        self.conn.commit()

        self.assertEqual(self._gaps(), [])

    def test_a_repaired_line_takes_a_later_payment(self):
        """The recipe in gotchas.md nets a credit into a posted Payment by hand and sets
        the credit row to ignore. An invoice on that line has published, so a payment
        applied to it later has to publish too."""
        import_id = cc.insert_import(self.conn, 90000)
        first = cc.insert_ta(self.conn, 'receivable', 60000, 'INV-A', {})
        cm_ta = cc.insert_ta(self.conn, 'credit_memo', 10000, 'CM-1', {})
        later = cc.insert_ta(self.conn, 'receivable', 40000, 'INV-B', {})
        tap_first = cc.insert_tap(self.conn, first, 60000, import_id=import_id)
        tap_cm = cc.insert_tap(self.conn, cm_ta, 10000, import_id=import_id)
        tap_later = cc.insert_tap(self.conn, later, 40000, import_id=import_id)
        self.conn.commit()
        _set_sync(self.conn, tap_first, 'synced', external_id='QBO-PMT-1')
        _set_sync(self.conn, tap_cm, 'ignore')

        self.assertEqual(self._gaps(), [])
        self.assertIn(tap_later, self._singleton_ids())

    def test_a_settled_line_takes_the_same_two_answers(self):
        """A settlement's deposit key is NULL, so the published-invoice test reads it the
        same way a plain line is read."""
        for published, expected in ((False, 2), (True, 0)):
            with self.subTest(an_invoice_published=published):
                conn, path = cc.make_temp_db()
                try:
                    import_id = cc.insert_import(conn, 90000)
                    first = cc.insert_ta(conn, 'receivable', 60000, 'INV-1', {})
                    cm_ta = cc.insert_ta(conn, 'credit_memo', 10000, 'CM-1', {})
                    later = cc.insert_ta(conn, 'receivable', 40000, 'INV-2', {})
                    taps = [cc.insert_tap(conn, first, 60000, import_id=import_id),
                            cc.insert_tap(conn, cm_ta, 10000, import_id=import_id),
                            cc.insert_tap(conn, later, 40000, import_id=import_id)]
                    for tap_id in taps:
                        conn.execute(
                            "UPDATE trade_account_payments SET metadata = "
                            "json_set(metadata, '$.settlement_id', 'SET-1') WHERE id = ?",
                            (tap_id,))
                    _set_sync(conn, taps[1], 'ignore')
                    if published:
                        _set_sync(conn, taps[0], 'synced', external_id='QBO-PMT-1')
                    named = [g for g in cc.common.find_bank_funded_payment_gaps(
                        conn, 'pending', None, None)
                        if g['error_code'] == 'DEPOSIT_CREDIT_OFF_STATUS']
                    self.assertEqual(len(named), expected)
                finally:
                    conn.close()
                    os.remove(path)

    def test_one_payout_posting_does_not_vouch_for_another(self):
        """Two payouts settled on one bank line. A posted invoice under one payout says
        nothing about the other, so the other's suppressed credit still stops the run."""
        import_id = cc.insert_import(self.conn, 90000)
        one_inv = cc.insert_ta(self.conn, 'receivable', 60000, 'INV-1', {'payout_id': 'PO-1'})
        one_cm = cc.insert_ta(self.conn, 'credit_memo', 10000, 'CM-1', {'payout_id': 'PO-1'})
        two_inv = cc.insert_ta(self.conn, 'receivable', 40000, 'INV-2', {'payout_id': 'PO-2'})
        tap_one = cc.insert_tap(self.conn, one_inv, 60000, import_id=import_id)
        tap_cm = cc.insert_tap(self.conn, one_cm, 10000, import_id=import_id)
        tap_two = cc.insert_tap(self.conn, two_inv, 40000, import_id=import_id)
        self.conn.commit()
        _set_sync(self.conn, tap_cm, 'ignore')
        _set_sync(self.conn, tap_two, 'synced', external_id='QBO-PMT-2')

        self.assertEqual(self._named(), {(tap_one, 'DEPOSIT_CREDIT_OFF_STATUS')})

    def test_two_payouts_each_answer_for_their_own_credit(self):
        """One import settling two payouts, each with a credit nobody dealt with. Keeping
        one credit per bank line named one invoice and let the other publish at full
        face, and which one it was came down to row order."""
        import_id = cc.insert_import(self.conn, 90000)
        named = {}
        for payout, face in (('PO-1', 60000), ('PO-2', 40000)):
            inv_ta = cc.insert_ta(self.conn, 'receivable', face, f'INV-{payout}',
                                  {'payout_id': payout})
            cm_ta = cc.insert_ta(self.conn, 'credit_memo', 10000, f'CM-{payout}',
                                 {'payout_id': payout})
            named[payout] = cc.insert_tap(self.conn, inv_ta, face, import_id=import_id)
            cm_tap = cc.insert_tap(self.conn, cm_ta, 10000, import_id=import_id)
            self.conn.commit()
            _set_sync(self.conn, cm_tap, 'error')

        self.assertEqual(self._named(),
                         {(named['PO-1'], 'DEPOSIT_CREDIT_OFF_STATUS'),
                          (named['PO-2'], 'DEPOSIT_CREDIT_OFF_STATUS')})

    def test_voiding_a_published_invoice_leaves_the_line_repaired(self):
        """The Payment that invoice posted is still in QuickBooks after its trade account
        is voided, so the line has still moved past what this check can help with."""
        import_id = cc.insert_import(self.conn, 90000)
        first = cc.insert_ta(self.conn, 'receivable', 60000, 'INV-A', {})
        cm_ta = cc.insert_ta(self.conn, 'credit_memo', 10000, 'CM-1', {})
        later = cc.insert_ta(self.conn, 'receivable', 40000, 'INV-B', {})
        tap_first = cc.insert_tap(self.conn, first, 60000, import_id=import_id)
        tap_cm = cc.insert_tap(self.conn, cm_ta, 10000, import_id=import_id)
        cc.insert_tap(self.conn, later, 40000, import_id=import_id)
        self.conn.commit()
        _set_sync(self.conn, tap_first, 'synced', external_id='QBO-PMT-1')
        _set_sync(self.conn, tap_cm, 'ignore')
        self.assertEqual(self._gaps(), [])

        self.conn.execute("UPDATE trade_accounts SET voided_at = '2026-04-20' WHERE id = ?",
                          (first,))
        self.conn.commit()

        self.assertEqual(self._gaps(), [])

    def test_the_message_names_the_credit_a_person_can_act_on(self):
        """A line carrying two credits, one repaired and one errored. The errored one is
        the reason a person can act on, so the message names it."""
        import_id = cc.insert_import(self.conn, 80000)
        inv_ta = cc.insert_ta(self.conn, 'receivable', 100000, 'INV-A', {})
        done = cc.insert_ta(self.conn, 'credit_memo', 10000, 'CM-1', {})
        stuck = cc.insert_ta(self.conn, 'credit_memo', 10000, 'CM-2', {})
        posted = cc.insert_ta(self.conn, 'receivable', 20000, 'INV-B', {})
        tap_inv = cc.insert_tap(self.conn, inv_ta, 100000, import_id=import_id)
        tap_done = cc.insert_tap(self.conn, done, 10000, import_id=import_id)
        tap_stuck = cc.insert_tap(self.conn, stuck, 10000, import_id=import_id)
        tap_posted = cc.insert_tap(self.conn, posted, 20000, import_id=import_id)
        self.conn.commit()
        _set_sync(self.conn, tap_done, 'ignore')
        _set_sync(self.conn, tap_stuck, 'error')
        _set_sync(self.conn, tap_posted, 'synced', external_id='QBO-PMT-1')

        self.assertEqual(self._named(), {(tap_inv, 'DEPOSIT_CREDIT_OFF_STATUS')})
        message = self._gaps()[0]['error_message']
        self.assertIn(tap_stuck, message)
        self.assertIn('its sync status is error', message)

    def test_a_missing_sync_value_is_described_in_words(self):
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
class HalfPostedDepositTests(unittest.TestCase):
    """Which refusal a deposit gets when its invoices published and its credit did not."""

    def setUp(self):
        cc._load_modules()
        self.conn, self.path = cc.make_temp_db()

    def tearDown(self):
        self.conn.close()
        os.remove(self.path)

    def _refusals(self):
        groups = {}
        for row in cc.common.query_payout_consumed_credits(self.conn, 'pending'):
            groups.setdefault(row['group_key'], []).append(row)
        return [cc.common.check_consumed_credit_group(self.conn, key, group)
                for key, group in groups.items()]

    def test_a_half_posted_deposit_is_named_half_posted(self):
        """The invoices published at full face and the credit row is still waiting. The
        group holds that one row, so counting members calls it incomplete and sends a
        person looking for an invoice that was never missing. It is half posted, which is
        what the repair recipe in gotchas.md answers."""
        _, taps = cc.build_deposit(self.conn, {})
        for role in ('R1', 'R2'):
            _set_sync(self.conn, taps[role], 'synced', external_id='QBO-PMT-1')

        self.assertEqual([r[0] for r in self._refusals()], ['PAYOUT_PARTIALLY_PUBLISHED'])

    def test_a_credit_with_no_invoice_of_its_own_is_still_incomplete(self):
        """Nothing on this bank line has published. The credit memo's customer has no
        invoice here, so the group is incomplete and keeps that name."""
        import_id = cc.insert_import(self.conn, 50000)
        inv_ta = cc.insert_ta(self.conn, 'receivable', 60000, 'INV-A', {},
                              contact='Northwind Supply')
        cm_ta = cc.insert_ta(self.conn, 'credit_memo', 10000, 'CM-1', {},
                             contact='Dockside Freight')
        cc.insert_tap(self.conn, inv_ta, 60000, import_id=import_id)
        cc.insert_tap(self.conn, cm_ta, 10000, import_id=import_id)
        self.conn.commit()

        self.assertEqual([r[0] for r in self._refusals()], ['PAYOUT_GROUP_INCOMPLETE'])


if __name__ == '__main__':
    unittest.main()
