#!/usr/bin/env python3
"""
Hermetic tests for ClientHolder auth plumbing (NO real QBO calls).

Covers _shared/auth.py and its wiring through publish_single_qbo_object and
journal_entries.publish_single_entry:

  * Reactive: a typed HTTP-401 (AuthorizationException) refreshes once, swaps
    holder.client, and retries — with the swapped client used for BOTH the
    save and the token persist (passing the holder itself to
    save_tokens_if_available would silently no-op and re-open the gap).
  * Proactive: past REFRESH_INTERVAL_SECONDS the refresh fires BEFORE the
    save; last_refresh advances ONLY on a confirmed successful refresh.
  * REFRESH_TOKEN_EXPIRED poisons the holder: subsequent saves fail FAST and
    LOUD (AUTH_DEAD) without network calls — no thrash, rows stay retryable.
  * Business faults (e.g. 6240) are NEVER auth-retried — they go to the
    locate read-back, not a refresh (the "no built-in retry" philosophy
    exception is typed-401 only).
  * Raw clients (legacy callers, hermetic tests) keep today's behavior.

Run:
    python3 -m unittest scripts.tests.test_qbo_publish_auth_retry
"""

import os
import sys
import tempfile
import types
import unittest
from unittest import mock

from quickbooks.exceptions import AuthorizationException, QuickbooksException

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(THIS_DIR)
SKILL_DIR = os.path.dirname(SCRIPTS_DIR)
QBO_DIR = os.path.join(SKILL_DIR, 'adapters', 'qbo')

auth = common = je_pub = None
_client_stub = None


def _load_modules():
    global auth, common, je_pub, _client_stub
    if auth is not None:
        return
    for m in [k for k in list(sys.modules) if k == '_shared' or k.startswith('_shared.')
              or k == '_publishers' or k.startswith('_publishers.')]:
        del sys.modules[m]
    while QBO_DIR in sys.path:
        sys.path.remove(QBO_DIR)
    sys.path.insert(0, QBO_DIR)
    stub = types.ModuleType('_shared.client')
    stub.save_tokens_if_available = lambda *a, **k: None
    stub.MAX_RETRIES = 3
    stub.MIN_REQUEST_INTERVAL = 0
    # auth._refresh imports refresh_client lazily from _shared.client at call
    # time — tests override this attr per-case.
    stub.refresh_client = lambda client: (None, {'error': 'NOT_CONFIGURED'})
    sys.modules['_shared.client'] = stub
    _client_stub = stub
    from _shared import auth as _auth
    from _shared import common as _common
    from _publishers import journal_entries as _je
    auth, common, je_pub = _auth, _common, _je


class _FakeRL:
    def wait(self):
        pass

    def trigger_backoff(self, *a):
        pass


class _ScriptedObj:
    """QBO object whose save() follows a script of exceptions / 'ok'."""

    def __init__(self, script):
        self.script = list(script)
        self.Id = None
        self.saved_with = []

    def save(self, qb=None):
        self.saved_with.append(qb)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        self.Id = 'OK-1'
        return self


class AuthRetryTests(unittest.TestCase):

    def setUp(self):
        _load_modules()
        self.clk = [0.0]
        self.holder = auth.ClientHolder('client-A', clock=lambda: self.clk[0])
        self.refresh_calls = []
        self.token_saves = []
        self._patches = [
            mock.patch.object(common, 'save_tokens_if_available',
                              lambda c, p: self.token_saves.append(c)),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def _set_refresh(self, result):
        def fake_refresh(client):
            self.refresh_calls.append(client)
            return result
        return mock.patch.object(_client_stub, 'refresh_client', fake_refresh)

    # ----------------------------- reactive -----------------------------

    def test_401_refreshes_swaps_and_retries_once(self):
        obj = _ScriptedObj([AuthorizationException('auth failed', 401), 'ok'])
        with self._set_refresh(('client-B', None)):
            ext_id, error = common.publish_single_qbo_object(self.holder, _FakeRL(), obj, '')
        self.assertEqual((ext_id, error), ('OK-1', None))
        # Refreshed once, from the stale client; retried with the swap.
        self.assertEqual(self.refresh_calls, ['client-A'])
        self.assertEqual(obj.saved_with, ['client-A', 'client-B'])
        self.assertEqual(self.holder.client, 'client-B')
        # Tokens persisted via the RESOLVED swapped client — never the holder.
        self.assertEqual(self.token_saves, ['client-B'])

    def test_second_401_does_not_retry_again(self):
        obj = _ScriptedObj([AuthorizationException('auth failed', 401),
                            AuthorizationException('still failing', 401)])
        with self._set_refresh(('client-B', None)):
            ext_id, error = common.publish_single_qbo_object(self.holder, _FakeRL(), obj, '')
        self.assertIsNone(ext_id)
        self.assertIn('still failing', error)
        self.assertEqual(len(self.refresh_calls), 1)  # retry is ONCE

    def test_raw_client_keeps_todays_behavior(self):
        obj = _ScriptedObj([AuthorizationException('auth failed', 401)])
        with self._set_refresh(('client-B', None)):
            ext_id, error = common.publish_single_qbo_object('raw-client', _FakeRL(), obj, '')
        self.assertIsNone(ext_id)
        self.assertIn('auth failed', error)
        self.assertEqual(self.refresh_calls, [])  # no holder → no retry
        self.assertEqual(obj.saved_with, ['raw-client'])

    def test_business_fault_never_auth_retries(self):
        # The "no built-in retry" exception is typed-401 ONLY: a 6240 must go
        # to the locate path, never to a refresh+blind-retry.
        obj = _ScriptedObj([QuickbooksException('Duplicate Document Number', 6240)])
        with self._set_refresh(('client-B', None)):
            ext_id, error = common.publish_single_qbo_object(self.holder, _FakeRL(), obj, '')
        self.assertIsNone(ext_id)
        self.assertIn('Duplicate', error)
        self.assertEqual(self.refresh_calls, [])
        self.assertEqual(len(obj.saved_with), 1)

    # ----------------------------- proactive -----------------------------

    def test_fresh_token_no_proactive_refresh(self):
        self.clk[0] = 100.0  # well under the interval
        obj = _ScriptedObj(['ok'])
        with self._set_refresh(('client-B', None)):
            ext_id, _ = common.publish_single_qbo_object(self.holder, _FakeRL(), obj, '')
        self.assertEqual(ext_id, 'OK-1')
        self.assertEqual(self.refresh_calls, [])
        self.assertEqual(obj.saved_with, ['client-A'])

    def test_stale_token_proactively_refreshes_before_save(self):
        self.clk[0] = auth.REFRESH_INTERVAL_SECONDS + 1
        obj = _ScriptedObj(['ok'])
        with self._set_refresh(('client-B', None)):
            ext_id, _ = common.publish_single_qbo_object(self.holder, _FakeRL(), obj, '')
        self.assertEqual(ext_id, 'OK-1')
        self.assertEqual(self.refresh_calls, ['client-A'])
        self.assertEqual(obj.saved_with, ['client-B'])  # refreshed BEFORE the save
        self.assertEqual(self.holder.last_refresh, self.clk[0])  # advanced on success

    def test_failed_proactive_refresh_does_not_advance_clock(self):
        self.clk[0] = auth.REFRESH_INTERVAL_SECONDS + 1
        obj = _ScriptedObj(['ok'])
        with self._set_refresh((None, {'error': 'AUTH_ERROR', 'message': 'transient'})):
            ext_id, _ = common.publish_single_qbo_object(self.holder, _FakeRL(), obj, '')
        # Save proceeds on the current token (may still be valid); the next
        # save will re-attempt the refresh because last_refresh did NOT move.
        self.assertEqual(ext_id, 'OK-1')
        self.assertEqual(obj.saved_with, ['client-A'])
        self.assertEqual(self.holder.last_refresh, 0.0)
        self.assertIsNone(self.holder.auth_dead)

    # ------------------------- refresh token death -------------------------

    def test_refresh_token_expired_poisons_holder_and_fails_fast(self):
        dead = {'error': 'REFRESH_TOKEN_EXPIRED',
                'message': 'QBO refresh token has expired. Re-authorize and update .env.'}
        first = _ScriptedObj([AuthorizationException('auth failed', 401)])
        with self._set_refresh((None, dead)):
            ext_id, error = common.publish_single_qbo_object(self.holder, _FakeRL(), first, '')
        self.assertIsNone(ext_id)
        self.assertIn('auth failed', error)
        self.assertTrue(self.holder.auth_dead)

        # Every subsequent save fails FAST and LOUD with zero network calls.
        second = _ScriptedObj(['ok'])
        with self._set_refresh((None, dead)):
            ext_id, error = common.publish_single_qbo_object(self.holder, _FakeRL(), second, '')
        self.assertIsNone(ext_id)
        self.assertTrue(error.startswith('AUTH_DEAD'))
        self.assertIn('Re-authorize', error)
        self.assertEqual(second.saved_with, [])  # no save attempted
        self.assertEqual(len(self.refresh_calls), 1)  # no further refresh attempts

    # --------------------- token-chain convergence ---------------------

    def test_refresh_converges_on_newest_stored_token(self):
        # Another process rotated the refresh token (file holds R1) after
        # this client was built (memory holds R0). The refresh must use R1 —
        # refreshing from stale R0 would invalid_grant near Intuit's
        # rotation boundary.
        fd, env_path = tempfile.mkstemp(suffix='.env')
        os.close(fd)
        with open(env_path, 'w') as f:
            f.write('QBO_ACCESS_TOKEN=A1\nQBO_REFRESH_TOKEN=R1\n')
        try:
            fake_qbo_client = types.SimpleNamespace(
                _qbo_credentials={'refresh_token': 'R0', 'access_token': 'A0'})
            holder = auth.ClientHolder(fake_qbo_client, clock=lambda: self.clk[0])
            seen = []

            def fake_refresh(client):
                seen.append(client._qbo_credentials['refresh_token'])
                return ('client-B', None)

            with mock.patch.object(_client_stub, 'refresh_client', fake_refresh):
                ok = auth.try_reactive_refresh(holder, env_path)
            self.assertTrue(ok)
            self.assertEqual(seen, ['R1'])
        finally:
            os.remove(env_path)

    def test_refresh_keeps_memory_token_when_no_env(self):
        fake_qbo_client = types.SimpleNamespace(
            _qbo_credentials={'refresh_token': 'R0', 'access_token': 'A0'})
        holder = auth.ClientHolder(fake_qbo_client, clock=lambda: self.clk[0])
        seen = []

        def fake_refresh(client):
            seen.append(client._qbo_credentials['refresh_token'])
            return ('client-B', None)

        with mock.patch.object(_client_stub, 'refresh_client', fake_refresh):
            ok = auth.try_reactive_refresh(holder, '')
        self.assertTrue(ok)
        self.assertEqual(seen, ['R0'])

    # ------------------------- JE publisher wiring -------------------------

    def test_journal_entry_loop_auth_retries(self):
        outcomes = [AuthorizationException('auth failed', 401), 'ok']
        saved_with = []

        class _FakeJE:
            def __init__(self):
                self.Line = []
                self.Id = None

            def save(self, qb=None):
                saved_with.append(qb)
                step = outcomes.pop(0)
                if isinstance(step, Exception):
                    raise step
                self.Id = 'JE-9'
                return self

        with self._set_refresh(('client-B', None)), \
             mock.patch.object(je_pub, 'JournalEntry', _FakeJE), \
             mock.patch.object(je_pub, 'save_tokens_if_available',
                               lambda c, p: self.token_saves.append(c)):
            result = je_pub.publish_single_entry(
                self.holder, _FakeRL(), 'abcdefab-0000', {'TxnDate': '2026-04-30', 'Line': []}, '')
        self.assertTrue(result['success'], result)
        self.assertEqual(result['external_id'], 'JE-9')
        self.assertEqual(saved_with, ['client-A', 'client-B'])
        self.assertEqual(self.token_saves, ['client-B'])


if __name__ == '__main__':
    unittest.main()
