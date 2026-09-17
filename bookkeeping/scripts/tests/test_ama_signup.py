#!/usr/bin/env python3
"""
Tests for ama_client.py signup. The command makes a firm on the service and
saves its key in the adapter settings file, readable by the user only. It
never prints the key, refuses to overwrite a saved key unless told, and says
plainly when the service refused. The service is stubbed; nothing leaves the
machine.

Run from the bookkeeping skill directory:
    uv run --no-project --with-requirements requirements.txt python3 -m unittest scripts.tests.test_ama_signup
"""

import argparse
import contextlib
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_DIR = os.path.dirname(SCRIPTS_DIR)

# The adapter loads config at import time, so point it at a throwaway config.
_tmpdir = tempfile.mkdtemp(prefix='ama-signup-test-')
_local_dir = os.path.join(_tmpdir, '_local-bookkeeping')
os.makedirs(_local_dir, exist_ok=True)
with open(os.path.join(_local_dir, 'config.yaml'), 'w') as f:
    f.write(
        'local_dir: "{project-root}/_local-bookkeeping"\n'
        'database_dir: "{project-root}/database"\n'
        'database_name: "bookkeeping.db"\n'
    )
os.environ['BOOKKEEPING_CONFIG_PATH'] = os.path.join(_local_dir, 'config.yaml')

_spec = importlib.util.spec_from_file_location(
    "ama_client", os.path.join(SKILL_DIR, 'adapters', 'ama_client.py'))
ama_client = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ama_client)

KEY = 'acp_' + 'ab' * 32
API_URL = 'https://ama.example'


class Headers(dict):
    def get(self, name, default=None):
        return super().get(name, default)


def ok_response(name='Acme Accounting'):
    return 201, {'id': 'firm-1', 'name': name, 'api_key': KEY}, Headers()


class SignupTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='ama-signup-case-')
        self.env_path = os.path.join(self.dir, 'adapters', '.env')

    def write_env(self, text):
        os.makedirs(os.path.dirname(self.env_path), exist_ok=True)
        with open(self.env_path, 'w') as f:
            f.write(text)

    def read_env(self):
        with open(self.env_path) as f:
            return f.read()

    def run_signup(self, response=None, firm_name='Acme Accounting', replace=False,
                   side_effect=None):
        """Run the command. Returns (exit code, stdout, stderr, request mock)."""
        args = argparse.Namespace(firm_name=firm_name, replace=replace)
        request = mock.Mock(return_value=response or ok_response(),
                            side_effect=side_effect)
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with mock.patch.object(ama_client, 'signup_request', request), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                ama_client.cmd_signup(args, API_URL, env_path=self.env_path)
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue(), request

    def assert_no_key_printed(self, out, err):
        self.assertNotIn(KEY, out)
        self.assertNotIn(KEY, err)
        self.assertNotIn('acp_', out + err)

    def assert_no_temp_left(self):
        adapters = os.path.dirname(self.env_path)
        if os.path.isdir(adapters):
            self.assertEqual([n for n in os.listdir(adapters) if n.endswith('.tmp')], [])

    def error_of(self, out):
        return json.loads(out)['error']

    def test_saved_key_is_private_and_never_printed(self):
        code, out, err, _ = self.run_signup()
        self.assertEqual(code, 0)
        self.assertEqual(self.read_env(), f'AMA_FIRM_API_KEY={KEY}\n')
        self.assertEqual(stat.S_IMODE(os.stat(self.env_path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.env_path)).st_mode), 0o700)
        self.assertIn(f'Signed up Acme Accounting. Your firm key is saved in {self.env_path}', err)
        result = json.loads(out)
        self.assertEqual(result, {'success': True, 'firm_id': 'firm-1',
                                  'firm_name': 'Acme Accounting', 'saved_to': self.env_path})
        self.assert_no_key_printed(out, err)
        self.assert_no_temp_left()

    def test_other_settings_are_kept(self):
        self.write_env('STRIPE_API_KEY=rk_test_x\n# a note\nAMA_API_URL=https://x\n')
        code, _, _, _ = self.run_signup()
        self.assertEqual(code, 0)
        self.assertEqual(self.read_env(),
                         'STRIPE_API_KEY=rk_test_x\n# a note\nAMA_API_URL=https://x\n'
                         f'AMA_FIRM_API_KEY={KEY}\n')

    def test_existing_key_is_refused_without_a_call(self):
        self.write_env('AMA_FIRM_API_KEY=acp_old\n')
        code, out, _, request = self.run_signup()
        self.assertEqual(code, 1)
        request.assert_not_called()
        self.assertEqual(self.read_env(), 'AMA_FIRM_API_KEY=acp_old\n')
        self.assertEqual(self.error_of(out),
                         ama_client.SIGNUP_MESSAGES['already_has_key'].format(path=self.env_path))
        self.assertIn('--replace', self.error_of(out))

    def test_exported_key_counts_as_a_key(self):
        self.write_env('export AMA_FIRM_API_KEY="acp_old"\n')
        code, _, _, request = self.run_signup()
        self.assertEqual(code, 1)
        request.assert_not_called()

    def test_empty_key_line_is_no_key(self):
        self.write_env('AMA_FIRM_API_KEY=\nSTRIPE_API_KEY=rk_test_x\n')
        code, _, _, _ = self.run_signup()
        self.assertEqual(code, 0)
        self.assertEqual(self.read_env(), f'STRIPE_API_KEY=rk_test_x\nAMA_FIRM_API_KEY={KEY}\n')

    def test_replace_puts_the_new_key_in_place(self):
        self.write_env('AMA_FIRM_API_KEY=acp_old\nSTRIPE_API_KEY=rk_test_x\n')
        code, out, err, _ = self.run_signup(replace=True)
        self.assertEqual(code, 0)
        self.assertEqual(self.read_env(), f'STRIPE_API_KEY=rk_test_x\nAMA_FIRM_API_KEY={KEY}\n')
        self.assert_no_key_printed(out, err)

    def test_service_refusal_saves_nothing(self):
        self.write_env('STRIPE_API_KEY=rk_test_x\n')
        code, out, _, _ = self.run_signup(
            response=(400, {'error': 'Validation failed'}, Headers()))
        self.assertEqual(code, 1)
        self.assertEqual(self.error_of(out),
                         'The service refused the sign-up: Validation failed. Nothing was saved.')
        self.assertEqual(self.read_env(), 'STRIPE_API_KEY=rk_test_x\n')
        self.assert_no_temp_left()

    def test_daily_cap(self):
        code, out, _, _ = self.run_signup(response=(
            429, {'error': 'x', 'code': 'daily_cap_reached'}, Headers({'Retry-After': '3600'})))
        self.assertEqual(code, 1)
        self.assertEqual(self.error_of(out), ama_client.SIGNUP_MESSAGES['daily_cap'])
        self.assertFalse(os.path.exists(self.env_path))
        self.assert_no_temp_left()

    def test_rate_limited_gives_minutes(self):
        code, out, _, _ = self.run_signup(response=(
            429, {'error': 'x', 'code': 'rate_limited'}, Headers({'Retry-After': '2501'})))
        self.assertEqual(code, 1)
        self.assertEqual(self.error_of(out),
                         'Too many sign-ups from this network. Try again in 42 minutes. '
                         'Nothing was saved.')

    def test_rate_limited_one_minute(self):
        _, out, _, _ = self.run_signup(response=(
            429, {'code': 'rate_limited'}, Headers({'Retry-After': '30'})))
        self.assertIn('Try again in 1 minute.', self.error_of(out))

    def test_unreachable(self):
        code, out, _, _ = self.run_signup(side_effect=urllib.error.URLError('timed out'))
        self.assertEqual(code, 1)
        self.assertEqual(self.error_of(out),
                         f'Could not reach {API_URL}: timed out. Nothing was saved.')
        self.assert_no_temp_left()

    def test_key_that_cannot_be_saved_is_not_printed(self):
        with mock.patch.object(ama_client.os, 'replace', side_effect=OSError(28, 'No space left on device')):
            code, out, err, _ = self.run_signup()
        self.assertEqual(code, 1)
        self.assertEqual(self.error_of(out), ama_client.SIGNUP_MESSAGES['lost'].format(
            path=self.env_path, reason='No space left on device'))
        self.assert_no_key_printed(out, err)
        self.assert_no_temp_left()

    @unittest.skipIf(os.geteuid() == 0, 'root can write a read-only directory')
    def test_unwritable_folder_stops_before_the_call(self):
        adapters = os.path.dirname(self.env_path)
        os.makedirs(adapters)
        os.chmod(adapters, 0o500)
        try:
            code, out, _, request = self.run_signup()
        finally:
            os.chmod(adapters, 0o700)
        self.assertEqual(code, 1)
        request.assert_not_called()
        self.assertTrue(self.error_of(out).startswith(f'Cannot write the firm key to {self.env_path}'))

    def test_no_firm_name(self):
        with mock.patch.dict(ama_client._config, {'firm_name': None}):
            code, out, _, request = self.run_signup(firm_name=None)
        self.assertEqual(code, 1)
        request.assert_not_called()
        self.assertEqual(self.error_of(out), ama_client.SIGNUP_MESSAGES['no_name'])

    def test_firm_name_from_config(self):
        with mock.patch.dict(ama_client._config, {'firm_name': 'Config Firm'}):
            code, _, _, request = self.run_signup(firm_name=None,
                                                  response=ok_response('Config Firm'))
        self.assertEqual(code, 0)
        request.assert_called_once_with(API_URL, 'Config Firm')


if __name__ == '__main__':
    unittest.main()
