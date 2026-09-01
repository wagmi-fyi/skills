#!/usr/bin/env python3
"""
Hermetic publisher tests for four-type settlement consolidation (NO real QBO calls).

Strategy:
  * Stub `_shared.client` in sys.modules BEFORE importing the QBO publishers, so the
    import doesn't pull in qbo_client (which sys.exit(1)s without credentials).
  * Fake `publish_single_qbo_object` (and Payment.save, used by payments.py's two-step)
    to capture the constructed QBO objects instead of hitting the network.
  * Build the DB by running the REAL apply_payments_bulk on a synthetic R+CM+P+VC
    settlement, so this also verifies the resolver→publisher metadata contract
    (settlement_id, application_method, source_ta_id, external_ids).

Proves the done-criteria: one Payment + one BillPayment + (settlement) credit-apps
consumed-not-double-published, with net bank across the two objects == the deposit.

Run:
    .venv/bin/python -m unittest scripts.tests.test_qbo_publish_settlement
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
import uuid
from unittest import mock

# The QBO SDK is adapter-tier (requirements.txt, QBO block) and reaches this
# module through the publishers it loads. Without it the subject cannot be
# exercised, so its cases skip rather than error and a non-QBO deployment still
# runs a green core suite. The guard is a class decorator, not a module-level
# SkipTest: unittest only converts the latter to a skip under discover(), and
# raises it uncaught when a module is named directly.
SOR_SKIP_REASON = (
    "QBO SDK absent (python-quickbooks) — SoR publisher tests skipped. "
    "Install the QBO block from the bookkeeping skill's requirements.txt."
)
try:
    import quickbooks  # noqa: F401
    QBO_SDK_PRESENT = True
except ImportError:
    QBO_SDK_PRESENT = False

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(THIS_DIR)
SKILL_DIR = os.path.dirname(SCRIPTS_DIR)
QBO_DIR = os.path.join(SKILL_DIR, 'adapters', 'qbo')
SCHEMA_PATH = os.path.join(SKILL_DIR, 'reference', 'schema.sql')

# The publishers are imported LAZILY (in setUp), not at module load. Two distinct
# `_shared` packages exist in this skill — scripts/_shared and adapters/qbo/_shared —
# and only one can own the bare name `_shared` in sys.modules at a time. Importing
# the publishers at module top would race other test modules under `unittest discover`
# (whoever registers `_shared` first wins). _load_publishers() repins the QBO package
# just-in-time and stubs _shared.client (so the import doesn't require QBO credentials).
common = payments_pub = billpay_pub = creditapp_pub = None


def _load_publishers():
    global common, payments_pub, billpay_pub, creditapp_pub
    if payments_pub is not None:
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
    sys.modules['_shared.client'] = stub
    from _shared import common as _common
    from _publishers import payments as _payments
    from _publishers import bill_payments as _billpay
    from _publishers import credit_applications as _creditapp
    common, payments_pub, billpay_pub, creditapp_pub = _common, _payments, _billpay, _creditapp


# --------------------------- DB fixture helpers ---------------------------

def make_temp_db():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    conn = sqlite3.connect(path)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    for code, name, typ in [
        ('1001', 'Bank', 'asset'),
        ('1200', 'A/R', 'asset'),
        ('2000', 'A/P', 'liability'),
        ('4000', 'Sales', 'income'),
        ('92830', 'Fees', 'expense'),
    ]:
        conn.execute("INSERT INTO chart_of_accounts (code, name, type) VALUES (?,?,?)", (code, name, typ))
    conn.execute("INSERT INTO contacts (name) VALUES ('CustA')")
    conn.execute("INSERT INTO contacts (name) VALUES ('VendX')")
    conn.commit()
    return conn, path


def make_config_yaml(db_path):
    f = tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False)
    f.write(f"""
user_name: Test
client_name: Test
client_id: test
module_root: "~/.claude/skills/bookkeeping"
local_dir: "{os.path.dirname(db_path)}"
output_folder: "{os.path.dirname(db_path)}/_bk-output"
database_dir: "{os.path.dirname(db_path)}"
database_name: "{os.path.basename(db_path)}"
default_system_of_record: "QBO"
period_type: "date-range"
fiscal_calendar: ""
coding:
  min_confidence_to_categorize: 5
  min_confidence_to_auto_approve: 9
""")
    f.close()
    return f.name


def _postings(ta_type, face):
    if ta_type == 'receivable':
        return [{'a': '1200', 'd': 'debit', 'm': face}, {'a': '4000', 'd': 'credit', 'm': face}], '1200'
    if ta_type == 'credit_memo':
        return [{'a': '4000', 'd': 'debit', 'm': face}, {'a': '1200', 'd': 'credit', 'm': face}], '1200'
    if ta_type == 'payable':
        return [{'a': '92830', 'd': 'debit', 'm': face}, {'a': '2000', 'd': 'credit', 'm': face}], '2000'
    if ta_type == 'vendor_credit':
        return [{'a': '2000', 'd': 'debit', 'm': face}, {'a': '92830', 'd': 'credit', 'm': face}], '2000'
    raise ValueError(ta_type)


def insert_ta(conn, ta_type, face, contact, sid, doc_date):
    postings, bac = _postings(ta_type, face)
    je_id = str(uuid.uuid4())
    conn.execute("INSERT INTO journal_entries (id, transaction_date, memo, sync) VALUES (?,?,?,?)",
                 (je_id, doc_date, 'test', '{"status":"pending"}'))
    for p in postings:
        conn.execute("INSERT INTO postings (id, journal_entry_id, account_code, direction, amount, contact) "
                     "VALUES (?,?,?,?,?,?)", (str(uuid.uuid4()), je_id, p['a'], p['d'], p['m'], contact))
    ta_id = str(uuid.uuid4())
    conn.execute("INSERT INTO trade_accounts (id, type, contact, document_date, journal_entry_id, sync, metadata) "
                 "VALUES (?,?,?,?,?,'{\"status\":\"pending\"}',?)",
                 (ta_id, ta_type, contact, doc_date, je_id,
                  json.dumps({"balance_account_code": bac, "settlement_id": sid})))
    return ta_id


class _FakeRL:
    def wait(self):
        pass

    def trigger_backoff(self):
        pass


@unittest.skipUnless(QBO_SDK_PRESENT, SOR_SKIP_REASON)
class PublisherSettlementTests(unittest.TestCase):

    def setUp(self):
        _load_publishers()
        self.captured = []
        self._next = [1000]

        def fake_publish(client, rate_limiter, obj, env_path):
            self._next[0] += 1
            ext = str(self._next[0])
            obj.Id = ext
            self.captured.append(obj)
            return ext, None

        self._patches = [
            mock.patch.object(payments_pub, 'publish_single_qbo_object', fake_publish),
            mock.patch.object(billpay_pub, 'publish_single_qbo_object', fake_publish),
            mock.patch.object(creditapp_pub, 'publish_single_qbo_object', fake_publish),
            # payments.py's two-step adds Line[] via payment.save(qb=client) directly.
            mock.patch.object(payments_pub.QBOPayment, 'save', lambda self, qb=None: self),
        ]
        for p in self._patches:
            p.start()
        self.conn = None
        self.path = None

    def tearDown(self):
        for p in self._patches:
            p.stop()
        if self.conn:
            self.conn.close()
        if self.path and os.path.exists(self.path):
            os.remove(self.path)

    # External ids assigned to each TA (simulating already-published documents).
    EXT = {'R1': 'INV-R1', 'R2': 'INV-R2', 'CM': 'CM-1',
           'P1': 'BILL-P1', 'P2': 'BILL-P2', 'VC': 'VC-1'}

    def _build_mixed(self, perturb=None):
        """Run the real apply_payments_bulk on a synthetic R+CM+P+VC settlement, then
        set external_ids / remote_ids so the publishers can consolidate. Returns sid."""
        conn, self.path = make_temp_db()
        sid = 'PUB-MIX-1'
        roles = {
            'R1': insert_ta(conn, 'receivable', 1000, 'CustA', sid, '2026-04-01'),
            'R2': insert_ta(conn, 'receivable', 500, 'CustA', sid, '2026-04-02'),
            'CM': insert_ta(conn, 'credit_memo', 300, 'CustA', sid, '2026-04-03'),
            'P1': insert_ta(conn, 'payable', 400, 'VendX', sid, '2026-04-04'),
            'P2': insert_ta(conn, 'payable', 200, 'VendX', sid, '2026-04-05'),
            'VC': insert_ta(conn, 'vendor_credit', 100, 'VendX', sid, '2026-04-06'),
        }
        self.roles = roles
        net = (1000 + 500 - 300) - (400 + 200 - 100)  # AR_net 1200 - AP_net 500 = 700
        self.net_cents = net
        imp_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO imports (id, source, type, banking_date, amount, raw_data, processed) "
            "VALUES (?, '1001 - Bank', 'feed', '2026-04-15', ?, ?, 0)",
            (imp_id, net, json.dumps({"Description": "settlement", "Balance Type": "cash",
                                      "Account Code": "1001", "Reference": "t"})))
        conn.commit()
        conn.close()  # release the file before the subprocess writes it

        cfg = make_config_yaml(self.path)
        env = os.environ.copy()
        env['BOOKKEEPING_CONFIG_PATH'] = cfg
        proc = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, 'apply_payments_bulk.py'),
             '--import_id', imp_id, '--payment_date', '2026-04-15',
             '--auto_resolve_settlement', '--settlement_id', sid, '--allow_mixed_credit'],
            env=env, capture_output=True, text=True)
        os.unlink(cfg)
        self.assertEqual(proc.returncode, 0, f"apply_payments_bulk failed: {proc.stdout}\n{proc.stderr}")

        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row  # publishers do dict(row); publish.py sets this
        for role, ta_id in roles.items():
            self.conn.execute("UPDATE trade_accounts SET sync = ? WHERE id = ?",
                              (json.dumps({"status": "synced", "external_id": self.EXT[role]}), ta_id))
        self.conn.execute("UPDATE contacts SET remote_id = 'QBO-CUST' WHERE name = 'CustA'")
        self.conn.execute("UPDATE contacts SET remote_id = 'QBO-VEND' WHERE name = 'VendX'")
        self.conn.execute("UPDATE chart_of_accounts SET remote_id = 'QBO-BANK-1001' WHERE code = '1001'")
        if perturb:
            perturb(self.conn, roles, sid)
        self.conn.commit()
        return sid

    def _run(self, fn):
        return fn(None, _FakeRL(), self.conn, {}, 'pending', None, None, '')

    def _lines(self, obj):
        """Map {(TxnType, TxnId): Amount} from a captured QBO object's Line[]."""
        out = {}
        for ln in obj.Line:
            lt = ln['LinkedTxn'][0]
            out[(lt['TxnType'], str(lt['TxnId']))] = ln['Amount']
        return out

    def _captured_of(self, type_name):
        objs = [o for o in self.captured if type(o).__name__ == type_name]
        return objs

    # ------------------------------ tests ------------------------------

    def test_full_mix_publishes_one_payment_one_billpayment(self):
        self._build_mixed()
        p_proc, p_fail, p_skip, p_err, p_ext = self._run(payments_pub.publish_payments)
        b_proc, b_fail, b_skip, b_err, b_ext = self._run(billpay_pub.publish_bill_payments)
        c_proc, c_fail, c_skip, c_err, c_ext = self._run(creditapp_pub.publish_credit_applications)

        # Receivable side: 2 R-cash + 2 CM-app TAPs consolidated into ONE Payment.
        self.assertEqual((p_fail, len(p_ext)), (0, 1), f"errors={p_err}")
        self.assertEqual(p_proc, 4)
        # Payable side: 2 P-cash + 2 VC-app TAPs consolidated into ONE BillPayment.
        self.assertEqual((b_fail, len(b_ext)), (0, 1), f"errors={b_err}")
        self.assertEqual(b_proc, 4)
        # Settlement credit-apps are folded into the two objects above — NOT double-published.
        self.assertEqual(c_proc, 0, "settlement credit-apps must not publish standalone")

        payment = self._captured_of('Payment')[0]
        billpayment = self._captured_of('BillPayment')[0]

        # Payment: TotalAmt = AR_net = $12.00; Lines = 2 Invoice (at face) + 1 CreditMemo.
        self.assertAlmostEqual(payment.TotalAmt, 12.00, places=2)
        self.assertEqual(self._lines(payment), {
            ('Invoice', 'INV-R1'): 10.00, ('Invoice', 'INV-R2'): 5.00,
            ('CreditMemo', 'CM-1'): 3.00})

        # BillPayment: TotalAmt = AP_net = $5.00; Lines = 2 Bill (at face) + 1 VendorCredit.
        self.assertAlmostEqual(billpayment.TotalAmt, 5.00, places=2)
        self.assertEqual(self._lines(billpayment), {
            ('Bill', 'BILL-P1'): 4.00, ('Bill', 'BILL-P2'): 2.00,
            ('VendorCredit', 'VC-1'): 1.00})

        # Net bank across the two QBO objects == the settlement deposit.
        self.assertAlmostEqual(payment.TotalAmt - billpayment.TotalAmt, self.net_cents / 100.0, places=2)

        # Every settlement TAP (4 bank-funded + 4 credit-app = 8) is now synced.
        synced = self.conn.execute(
            "SELECT COUNT(*) FROM trade_account_payments WHERE json_extract(sync,'$.external_id') IS NOT NULL "
            "AND json_extract(metadata,'$.settlement_id') = 'PUB-MIX-1'").fetchone()[0]
        self.assertEqual(synced, 8)

        # The clearing JE is sync=ignore (GL truth is the Payment + BillPayment).
        je_status = self.conn.execute(
            "SELECT DISTINCT json_extract(je.sync,'$.status') FROM journal_entries je "
            "JOIN trade_account_payments tap ON json_extract(tap.metadata,'$.clearing_je_id') = je.id "
            "WHERE json_extract(tap.metadata,'$.settlement_id') = 'PUB-MIX-1'").fetchone()[0]
        self.assertEqual(je_status, 'ignore')

    def test_billpayment_missing_bill_external_id_skips(self):
        def perturb(conn, roles, sid):
            conn.execute("UPDATE trade_accounts SET sync = '{\"status\":\"pending\"}' WHERE id = ?", (roles['P1'],))
        self._build_mixed(perturb=perturb)
        proc, fail, skip, err, ext = self._run(billpay_pub.publish_bill_payments)
        self.assertEqual(ext, [], "must not publish when a target Bill is unsynced")
        self.assertTrue(any(e['error_code'] == 'BILL_NOT_SYNCED' for e in err), err)

    def test_billpayment_heterogeneous_group_refuses(self):
        def perturb(conn, roles, sid):
            # Give one bank-funded P-TAP a different payment_date → 2 dates in the group.
            tap = conn.execute(
                "SELECT tap.id FROM trade_account_payments tap JOIN trade_accounts ta ON ta.id = tap.trade_account_id "
                "WHERE tap.source_ta_id IS NULL AND ta.type = 'payable' "
                "AND json_extract(tap.metadata,'$.settlement_id') = ? LIMIT 1", (sid,)).fetchone()[0]
            conn.execute("UPDATE trade_account_payments SET payment_date = '2026-05-01' WHERE id = ?", (tap,))
        self._build_mixed(perturb=perturb)
        proc, fail, skip, err, ext = self._run(billpay_pub.publish_bill_payments)
        self.assertEqual(ext, [])
        self.assertTrue(any(e['error_code'] == 'SETTLEMENT_GROUP_HETEROGENEOUS' for e in err), err)

    def test_payable_guard_ignores_synced_receivable_side(self):
        """A synced receivable-side TAP for the shared settlement_id must NOT trip the
        payable partial-publish guard (proves the type-restricted count)."""
        def perturb(conn, roles, sid):
            tap = conn.execute(
                "SELECT tap.id FROM trade_account_payments tap JOIN trade_accounts ta ON ta.id = tap.trade_account_id "
                "WHERE tap.source_ta_id IS NULL AND ta.type = 'receivable' "
                "AND json_extract(tap.metadata,'$.settlement_id') = ? LIMIT 1", (sid,)).fetchone()[0]
            conn.execute("UPDATE trade_account_payments SET sync = ? WHERE id = ?",
                         (json.dumps({"status": "synced", "external_id": "PRE-SYNCED-R"}), tap))
        self._build_mixed(perturb=perturb)
        proc, fail, skip, err, ext = self._run(billpay_pub.publish_bill_payments)
        self.assertEqual((fail, len(ext)), (0, 1), f"payable side should still publish; errors={err}")

    def test_payable_partial_publish_trips_on_synced_payable(self):
        """A synced payable-side TAP for the settlement_id IS a real partial publish → refuse."""
        def perturb(conn, roles, sid):
            tap = conn.execute(
                "SELECT tap.id FROM trade_account_payments tap JOIN trade_accounts ta ON ta.id = tap.trade_account_id "
                "WHERE tap.source_ta_id IS NULL AND ta.type = 'payable' "
                "AND json_extract(tap.metadata,'$.settlement_id') = ? LIMIT 1", (sid,)).fetchone()[0]
            conn.execute("UPDATE trade_account_payments SET sync = ? WHERE id = ?",
                         (json.dumps({"status": "synced", "external_id": "PRE-SYNCED-P"}), tap))
        self._build_mixed(perturb=perturb)
        proc, fail, skip, err, ext = self._run(billpay_pub.publish_bill_payments)
        self.assertEqual(ext, [])
        self.assertTrue(any(e['error_code'] == 'SETTLEMENT_PARTIALLY_PUBLISHED' for e in err), err)


if __name__ == '__main__':
    unittest.main()
