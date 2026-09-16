#!/usr/bin/env python3
"""
Self-contained tests for how qbo_client picks its credentials on a machine that
runs a token service.

The settings file decides. A stand-in service is a fake token command on a
scratch PATH and a fake client library reached through COMMONCLAW_CONN_LIB.
It answers every status question and records each call.

  * A file that holds token values is the credentials file. The service is
    never asked.
  * A file that holds only identifiers sends the client to the service. The
    row it asks for is intuit/<realm id>, or the name QBO_TOKEN_ROW gives.

Nothing leaves the machine. The files and the fake hold placeholder strings only.

Run, from the qbo skill directory:
    uv run --with-requirements requirements.txt python3 -m unittest tests.test_settings_file_decides
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(THIS_DIR), "scripts")
REALM = "4620816365000000000"
ROW = "intuit/" + REALM

IDENTIFIERS = "QBO_REALM_ID=%s\nQBO_ENVIRONMENT=sandbox\n" % REALM
TOKENS = ("QBO_CLIENT_ID=placeholder-id\n"
          "QBO_CLIENT_SECRET=placeholder-secret\n"
          "QBO_ACCESS_TOKEN=placeholder-access\n"
          "QBO_REFRESH_TOKEN=placeholder-refresh\n")

try:
    import dotenv  # noqa: F401
    DOTENV_PRESENT = True
except ImportError:
    DOTENV_PRESENT = False

FAKE_LIBRARY = r'''
import json, os

class TokensError(Exception):
    pass

def fingerprint(value):
    return "0" * 16 if value else ""

class TokensClient:
    def __init__(self, socket_path=None, timeout=60):
        pass

    def call(self, req):
        with open(os.environ["FAKE_TOKENS_LOG"], "a") as f:
            f.write(json.dumps(req) + "\n")
        return {"ok": True, "rows": []}

    def get(self, row):
        self.call({"verb": "get", "row": row})
        return {"access_token": "placeholder-access", "client": {"client_id": "placeholder-id"}}
'''

# Runs inside the child. It imports the client, which picks its path at import,
# and prints what it picked. On the service path it also builds a client, with
# the SDK's two constructors stubbed so nothing reaches Intuit.
PROBE = r"""
import json, socket, sys
def _refused(*a, **k):
    raise OSError("test guard: no network")
socket.getaddrinfo = _refused
sys.path.insert(0, sys.argv[1])
import qbo_client as q
built = None
if q._service is not None:
    q.AuthClient = lambda **k: object()
    q.QuickBooks = lambda **k: type("Client", (), {})()
    client, error = q.create_client()
    built = error is None
print(json.dumps({
    "service": q._service is not None,
    "env_file": q._env_file,
    "credentials": q.validate_env_vars(),
    "built": built,
}))
"""


@unittest.skipUnless(DOTENV_PRESENT, "python-dotenv is not installed; run under the skill's requirements.txt")
class SettingsFileDecides(unittest.TestCase):

    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix="qbo-settings-file-")
        self.addCleanup(shutil.rmtree, self.scratch)
        self.bin_dir = os.path.join(self.scratch, "bin")
        lib_dir = os.path.join(self.scratch, "lib", "commonclaw_connection")
        os.makedirs(self.bin_dir)
        os.makedirs(lib_dir)
        token = os.path.join(self.bin_dir, "token")
        with open(token, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(token, 0o755)
        open(os.path.join(lib_dir, "__init__.py"), "w").close()
        with open(os.path.join(lib_dir, "tokens_client.py"), "w") as f:
            f.write(FAKE_LIBRARY)
        self.env_file = os.path.join(self.scratch, "settings.env")
        self.log = os.path.join(self.scratch, "calls.log")

    def run_client(self, settings):
        with open(self.env_file, "w") as f:
            f.write(settings)
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("QBO_", "COMMONCLAW_", "BOOKKEEPING_"))}
        env.update({
            "PATH": self.bin_dir + os.pathsep + env.get("PATH", ""),
            "HOME": self.scratch,
            "COMMONCLAW_CONN_LIB": os.path.join(self.scratch, "lib"),
            "QBO_ENV_PATH": self.env_file,
            "FAKE_TOKENS_LOG": self.log,
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        proc = subprocess.run([sys.executable, "-c", PROBE, SCRIPTS], cwd=self.scratch,
                              env=env, capture_output=True, text=True, timeout=60)
        try:
            out = json.loads(proc.stdout)
        except ValueError:
            self.fail("the probe printed no JSON. stdout: %r stderr: %r" % (proc.stdout, proc.stderr))
        calls = []
        if os.path.exists(self.log):
            with open(self.log) as f:
                calls = [json.loads(line) for line in f]
        return out, proc.stderr.splitlines(), calls

    def test_file_with_tokens_is_the_credentials_file(self):
        out, err, calls = self.run_client(TOKENS + IDENTIFIERS)
        self.assertFalse(out["service"])
        self.assertEqual(out["env_file"], self.env_file)
        self.assertEqual(out["credentials"]["refresh_token"], "placeholder-refresh")
        self.assertEqual(calls, [])
        self.assertEqual(err, ["QBO: loaded credentials from " + self.env_file])

    def assert_service_path(self, settings, row):
        out, err, calls = self.run_client(settings)
        self.assertTrue(out["service"])
        self.assertIsNone(out["env_file"])
        self.assertEqual(out["credentials"]["token_row"], row)
        self.assertEqual(out["credentials"]["realm_id"], REALM)
        self.assertEqual(out["credentials"]["environment"], "sandbox")
        self.assertTrue(out["built"])
        self.assertEqual(calls, [{"verb": "status", "row": "intuit"},
                                 {"verb": "get", "row": row}])
        self.assertEqual(len(err), 1, err)
        self.assertIn("row " + row, err[0])
        self.assertNotIn("`", err[0])

    def test_file_with_identifiers_only_takes_the_service_path(self):
        self.assert_service_path(IDENTIFIERS, ROW)

    def test_token_row_names_the_row_as_given(self):
        self.assert_service_path(IDENTIFIERS + "QBO_TOKEN_ROW=intuit/example-books\n",
                                 "intuit/example-books")


if __name__ == "__main__":
    unittest.main()
