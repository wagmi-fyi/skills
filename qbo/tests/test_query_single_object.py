#!/usr/bin/env python3
"""
Self-contained tests for query.py against entities QuickBooks answers with one object.

CompanyInfo and Preferences are one record per company. A stand-in on loopback
answers them the way the defect was reported: the query response carries the
entity as one object, and the count query carries no totalCount. Preferences
has no query endpoint in the SDK at all, so it is read from its own endpoint.

  * A single-object answer is one record, counted as one, and never truncated.
  * A list answer still counts and truncates as before.
  * A type error inside the skill's own code is labelled SKILL_ERROR. A real API
    fault stays API_ERROR.

Nothing leaves the machine. Every connection to a host other than 127.0.0.1 is
refused inside the process under test, and the OAuth discovery document comes
from the stand-in too.

Run, from the qbo skill directory:
    uv run --with-requirements requirements.txt python3 -m unittest tests.test_query_single_object
"""

import http.server
import json
import os
import subprocess
import sys
import threading
import unittest
from unittest import mock

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(THIS_DIR)
QUERY = os.path.join(SKILL_DIR, "scripts", "query.py")
REALM = "4620816365000000000"

try:
    import quickbooks  # noqa: F401
    SDK_PRESENT = True
except ImportError:
    SDK_PRESENT = False

COMPANY = {"CompanyName": "Stand-in Co", "Id": "1", "Country": "US",
           "CompanyAddr": {"Line1": "1 Main St", "City": "Springfield"}}
PREFS = {"Id": "1", "AccountingInfoPrefs": {"TrackDepartments": False},
         "CurrencyPrefs": {"MultiCurrencyEnabled": False}}
ACCOUNTS = [{"Id": "1", "Name": "Checking", "AccountType": "Bank"},
            {"Id": "2", "Name": "Savings", "AccountType": "Bank"}]


class StandIn(http.server.BaseHTTPRequestHandler):
    """The QuickBooks API as far as query.py reaches it."""

    def log_message(self, *args):
        pass

    def answer(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        base = "http://%s:%d" % self.server.server_address
        if path == "/discovery":
            self.answer(200, {k: base + "/oauth" for k in (
                "authorization_endpoint", "token_endpoint", "revocation_endpoint",
                "issuer", "jwks_uri", "userinfo_endpoint")})
        elif path == "/v3/company/%s/preferences" % REALM:
            self.answer(200, {"Preferences": PREFS, "time": "2026-09-16T00:00:00Z"})
        else:
            self.answer(404, {"Fault": {"Error": [{"Message": "not found", "code": "610"}],
                                        "type": "ValidationFault"}})

    def do_POST(self):
        path = self.path.split("?")[0]
        select = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        if path != "/v3/company/%s/query" % REALM:
            return self.answer(404, {})
        if "FROM CompanyInfo" in select:
            # One object where a list is expected, and no totalCount on the count query.
            return self.answer(200, {"QueryResponse": {"CompanyInfo": COMPANY}})
        if "FROM Account" in select:
            if "COUNT(*)" in select:
                return self.answer(200, {"QueryResponse": {"totalCount": len(ACCOUNTS)}})
            return self.answer(200, {"QueryResponse": {"Account": ACCOUNTS,
                                                       "startPosition": 1,
                                                       "maxResults": len(ACCOUNTS)}})
        if "FROM Bill" in select:
            return self.answer(200, {"Fault": {"Error": [{"Message": "stand-in fault",
                                                          "Detail": "stand-in fault",
                                                          "code": "4000"}],
                                               "type": "ValidationFault"}})
        return self.answer(200, {"QueryResponse": {}})


# Runs inside the child. It refuses every non-loopback connection, points the
# SDK at the stand-in, and runs query.py as __main__.
BOOT = r"""
import runpy, socket, sys
_real = socket.getaddrinfo
def _loopback_only(host, *a, **k):
    if host not in ("127.0.0.1", "localhost"):
        raise OSError("test guard: refused a connection to %s" % host)
    return _real(host, *a, **k)
socket.getaddrinfo = _loopback_only
sys.path.insert(0, sys.argv[1].rsplit("/", 1)[0])
import quickbooks.client
quickbooks.client.QuickBooks.api_url_v3 = sys.argv[2] + "/v3"
sys.argv = [sys.argv[1]] + sys.argv[3:]
runpy.run_path(sys.argv[0], run_name="__main__")
"""


@unittest.skipUnless(SDK_PRESENT, "python-quickbooks is not installed; run under the skill's requirements.txt")
class QuerySingleObject(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), StandIn)
        cls.base = "http://127.0.0.1:%d" % cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def run_query(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith("QBO_")}
        env.update({
            "QBO_CLIENT_ID": "stand-in", "QBO_CLIENT_SECRET": "stand-in",
            "QBO_ACCESS_TOKEN": "stand-in", "QBO_REFRESH_TOKEN": "stand-in",
            "QBO_REALM_ID": REALM, "QBO_ENVIRONMENT": self.base + "/discovery",
            "PYTHONDONTWRITEBYTECODE": "1",
            # The stand-in speaks plain HTTP on loopback, and the OAuth
            # library refuses that unless told.
            "OAUTHLIB_INSECURE_TRANSPORT": "1",
        })
        proc = subprocess.run(
            [sys.executable, "-c", BOOT, QUERY, self.base] + list(args),
            env=env, capture_output=True, text=True, timeout=60)
        try:
            out = json.loads(proc.stdout)
        except ValueError:
            self.fail("query.py printed no JSON. stdout: %r stderr: %r" % (proc.stdout, proc.stderr))
        return proc.returncode, out

    def test_companyinfo_is_one_record(self):
        rc, out = self.run_query("--entity=CompanyInfo", "--max_results=1")
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["success"])
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["total_count"], 1)
        self.assertFalse(out["truncated"])
        self.assertEqual(len(out["data"]), 1)
        self.assertEqual(out["data"][0]["CompanyName"], "Stand-in Co")

    def test_companyinfo_is_not_sliced(self):
        rc, out = self.run_query("--entity=CompanyInfo")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["data"][0]["CompanyAddr"]["City"], "Springfield")

    def test_companyinfo_count_only(self):
        rc, out = self.run_query("--entity=CompanyInfo", "--count_only")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["count"], 1)

    def test_preferences_is_one_record(self):
        rc, out = self.run_query("--entity=Preferences")
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["success"])
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["total_count"], 1)
        self.assertFalse(out["truncated"])
        self.assertEqual(out["data"][0]["Id"], "1")

    def test_preferences_count_only(self):
        rc, out = self.run_query("--entity=Preferences", "--count_only")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["count"], 1)

    def test_preferences_refuses_a_filter(self):
        rc, out = self.run_query("--entity=Preferences", "--where=Id = '1'")
        self.assertEqual(rc, 1)
        self.assertEqual(out["error"], "INVALID_ARGUMENT")

    def test_list_entity_still_counts_and_truncates(self):
        rc, out = self.run_query("--entity=Account")
        self.assertEqual(rc, 0, out)
        self.assertEqual((out["count"], out["total_count"], out["truncated"]), (2, 2, False))
        rc, out = self.run_query("--entity=Account", "--max_results=2")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["count"], 2)
        rc, out = self.run_query("--entity=Account", "--count_only")
        self.assertEqual((rc, out["count"]), (0, 2))

    def test_api_fault_is_still_api_error(self):
        rc, out = self.run_query("--entity=Bill")
        self.assertEqual(rc, 1)
        self.assertEqual(out["error"], "API_ERROR")

    def test_type_error_in_skill_code_is_skill_error(self):
        # The label is decided from the exception, so one planted in the
        # skill's own conversion step must read as the skill's fault.
        # Credentials in the environment keep the import from looking for any.
        creds = {k: "stand-in" for k in ("QBO_CLIENT_ID", "QBO_CLIENT_SECRET",
                                          "QBO_ACCESS_TOKEN", "QBO_REFRESH_TOKEN",
                                          "QBO_REALM_ID")}
        sys.path.insert(0, os.path.join(SKILL_DIR, "scripts"))
        try:
            with mock.patch.dict(os.environ, creds):
                import query
        finally:
            sys.path.pop(0)
        self.assertEqual(query.failure(TypeError("x"))["error"], "SKILL_ERROR")
        self.assertEqual(query.failure(AttributeError("x"))["error"], "SKILL_ERROR")
        self.assertEqual(query.failure(Exception("Auth failed and refresh failed: x"))["error"], "API_ERROR")


if __name__ == "__main__":
    unittest.main()
