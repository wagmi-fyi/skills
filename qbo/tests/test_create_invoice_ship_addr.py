#!/usr/bin/env python3
"""
Self-contained tests for create_invoice.py and its --ship_addr argument.

A stand-in on loopback answers the duplicate check with no invoice and records
the invoice body the script posts.

  * The supplied keys land on Invoice.ShipAddr under the QuickBooks field names.
    state is CountrySubDivisionCode and zip is PostalCode.
  * A key that was not supplied is absent from the body. No country is assumed.
  * Malformed JSON, a value that is not an object, and an unknown key each exit 1
    with INVALID_JSON, and the script sends no request at all.
  * Without --ship_addr the body carries no ShipAddr.

Nothing leaves the machine. Every connection to a host other than 127.0.0.1 is
refused inside the process under test, and the OAuth discovery document comes
from the stand-in too.

Run, from the qbo skill directory:
    uv run --with-requirements requirements.txt python3 -m unittest tests.test_create_invoice_ship_addr
"""

import http.server
import json
import os
import subprocess
import sys
import threading
import unittest

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(THIS_DIR)
CREATE_INVOICE = os.path.join(SKILL_DIR, "scripts", "create_invoice.py")
REALM = "4620816365000000000"

try:
    import quickbooks  # noqa: F401
    SDK_PRESENT = True
except ImportError:
    SDK_PRESENT = False

REQUESTS = []


class StandIn(http.server.BaseHTTPRequestHandler):
    """The QuickBooks API as far as create_invoice.py reaches it."""

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
        REQUESTS.append(("GET", path, None))
        base = "http://%s:%d" % self.server.server_address
        if path == "/discovery":
            self.answer(200, {k: base + "/oauth" for k in (
                "authorization_endpoint", "token_endpoint", "revocation_endpoint",
                "issuer", "jwks_uri", "userinfo_endpoint")})
        else:
            self.answer(404, {})

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        REQUESTS.append(("POST", path, body))
        if path == "/v3/company/%s/query" % REALM:
            return self.answer(200, {"QueryResponse": {}})
        if path == "/v3/company/%s/invoice" % REALM:
            saved = dict(json.loads(body), Id="501", TotalAmt=150.0)
            return self.answer(200, {"Invoice": saved})
        return self.answer(404, {})


# Runs inside the child. It refuses every non-loopback connection, points the
# SDK at the stand-in, and runs create_invoice.py as __main__.
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

BASE_ARGS = [
    "--customer_id=123",
    "--invoice_num=9100",
    "--txn_date=2026-03-01",
    "--due_date=2026-03-31",
    '--line_items=[{"description": "Widget", "amount": 150.00, "item_id": "1"}]',
]


@unittest.skipUnless(SDK_PRESENT, "python-quickbooks is not installed; run under the skill's requirements.txt")
class CreateInvoiceShipAddr(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), StandIn)
        cls.base = "http://127.0.0.1:%d" % cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        REQUESTS.clear()

    def run_create(self, *args):
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
            [sys.executable, "-c", BOOT, CREATE_INVOICE, self.base] + BASE_ARGS + list(args),
            env=env, capture_output=True, text=True, timeout=60)
        try:
            out = json.loads(proc.stdout)
        except ValueError:
            self.fail("create_invoice.py printed no JSON. stdout: %r stderr: %r" % (proc.stdout, proc.stderr))
        return proc.returncode, out

    def posted_invoice(self):
        bodies = [json.loads(b) for m, p, b in REQUESTS
                  if m == "POST" and p == "/v3/company/%s/invoice" % REALM]
        self.assertEqual(len(bodies), 1, REQUESTS)
        return bodies[0]

    def test_supplied_fields_map_onto_ship_addr(self):
        rc, out = self.run_create('--ship_addr={"line1": "Example Store 12", '
                                  '"line2": "100 Sample Street", "city": "Springfield", '
                                  '"state": "IL", "zip": "62701"}')
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["action"], "created")
        self.assertEqual(self.posted_invoice()["ShipAddr"], {
            "Line1": "Example Store 12",
            "Line2": "100 Sample Street",
            "City": "Springfield",
            "CountrySubDivisionCode": "IL",
            "PostalCode": "62701",
        })

    def test_country_is_absent_unless_supplied(self):
        rc, out = self.run_create('--ship_addr={"line1": "100 Sample Street"}')
        self.assertEqual(rc, 0, out)
        ship = self.posted_invoice()["ShipAddr"]
        self.assertNotIn("Country", ship)
        self.assertEqual(ship, {"Line1": "100 Sample Street"})

    def test_supplied_country_and_line3_are_set(self):
        rc, out = self.run_create('--ship_addr={"line3": "Dock 4", "country": "CA"}')
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.posted_invoice()["ShipAddr"], {"Line3": "Dock 4", "Country": "CA"})

    def test_no_ship_addr_leaves_the_customer_default(self):
        rc, out = self.run_create()
        self.assertEqual(rc, 0, out)
        self.assertNotIn("ShipAddr", self.posted_invoice())

    def assert_refused_before_any_request(self, value):
        rc, out = self.run_create("--ship_addr=" + value)
        self.assertEqual(rc, 1, out)
        self.assertFalse(out["success"])
        self.assertEqual(out["error"], "INVALID_JSON")
        self.assertEqual(REQUESTS, [])

    def test_malformed_json_posts_nothing(self):
        self.assert_refused_before_any_request('{"line1": "100 Sample Street"')

    def test_non_object_posts_nothing(self):
        self.assert_refused_before_any_request('["100 Sample Street"]')

    def test_unknown_key_posts_nothing(self):
        self.assert_refused_before_any_request('{"line1": "100 Sample Street", "postal_code": "62701"}')

    def test_non_string_value_posts_nothing(self):
        self.assert_refused_before_any_request('{"zip": 62701}')


if __name__ == "__main__":
    unittest.main()
