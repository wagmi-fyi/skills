#!/usr/bin/env python3
"""
Publish to QuickBooks Online

Orchestrates publishing of journal entries, invoices, bills, payments, and
bill payments to QBO. Thin dispatcher — business logic lives in _publishers/.

Usage:
    BOOKKEEPING_CONFIG_PATH=_local-bookkeeping/config.yaml \
      {python} {module_root}/adapters/qbo/publish.py \
      --publish_type all --sync_status pending [--dry_run]
"""

import argparse
import json
import sqlite3
import sys
import os
from datetime import datetime
from typing import Dict, List, Optional

# Bootstrap config — resolve paths relative to the qbo/ adapter directory
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(script_dir, '..', '..', 'scripts', '_shared'))
sys.path.insert(0, script_dir)  # For _shared and _publishers imports
import config_loader

from _shared.auth import ClientHolder
from _shared.client import (
    validate_qbo_env_vars, create_qbo_client, test_qbo_connection,
    QBORateLimiter, FileLock
)
from _shared.sync_status import update_sync_error
from _shared.common import (
    query_trade_accounts, group_postings_by_ta, query_trade_account_payments,
    query_owner_cleared_payments, query_payout_consumed_credits
)

from _publishers.journal_entries import (
    query_journal_entries, group_postings_by_je,
    validate_account_mappings, validate_contact_mappings,
    validate_journal_balance, transform_to_qbo_journal_entry,
    publish_batch
)
from _publishers.invoices import publish_invoices
from _publishers.bills import publish_bills
from _publishers.credit_memos import publish_credit_memos
from _publishers.vendor_credits import publish_vendor_credits
from _publishers.credit_applications import publish_credit_applications
from _publishers.payments import publish_payments, publish_payout_consumed_credits
from _publishers.bill_payments import publish_bill_payments
from _publishers.owner_cleared import publish_owner_cleared

from dotenv import load_dotenv

_config = config_loader.load_config()
ENV_PATH = os.path.join(_config['local_dir'], 'adapters', '.env')
LOCK_FILE = os.path.join(script_dir, '.publish.lock')

load_dotenv(ENV_PATH)


# =============================================================================
# CLI
# =============================================================================

def parse_arguments():
    parser = argparse.ArgumentParser(description="Publish journal entries to QuickBooks Online")
    parser.add_argument('--sync_status', default='pending', choices=['pending', 'error'],
                        help='Filter by sync status (default: pending)')
    parser.add_argument('--start_date', type=str, help='Optional start date filter (YYYY-MM-DD)')
    parser.add_argument('--end_date', type=str, help='Optional end date filter (YYYY-MM-DD)')
    parser.add_argument('--dry_run', action='store_true', help='Validate without publishing')
    parser.add_argument('--publish_type', default='all',
                        choices=['all', 'jes', 'trade_accounts', 'payments', 'owner_cleared'],
                        help='What to publish (default: all)')
    return parser.parse_args()


def validate_date_format(date_str: str, param_name: str) -> None:
    try:
        datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        raise ValueError(f"{param_name} must be in YYYY-MM-DD format, got: {date_str}")


# =============================================================================
# Dry-Run Validation
# =============================================================================

def run_dry_run(client, conn, postings, grouped_jes, publish_type, sync_status, start_date, end_date):
    """Validate OAuth and account mappings without publishing."""
    result = {
        'oauth_status': 'unknown', 'oauth_message': '',
        'account_mapping_errors': [], 'balance_errors': [], 'class_ref_errors': [],
        'sample_entries': [], 'entry_count': len(grouped_jes), 'posting_count': len(postings),
        'invoice_count': 0, 'bill_count': 0, 'payment_count': 0, 'bill_payment_count': 0,
        'payout_consumed_credit_count': 0,
        'ta_contact_errors': [], 'payment_parent_errors': [], 'contact_mapping_errors': [],
    }

    if client:
        success, message = test_qbo_connection(client, ENV_PATH)
        result['oauth_status'] = 'valid' if success else 'invalid'
        result['oauth_message'] = message
    else:
        result['oauth_status'] = 'invalid'
        result['oauth_message'] = 'Client not initialized'

    result['contact_mapping_errors'] = validate_contact_mappings(postings, conn)

    if publish_type in ('all', 'jes'):
        result['account_mapping_errors'] = validate_account_mappings(postings)

        for je_id, je_postings in grouped_jes.items():
            balance_error = validate_journal_balance(je_postings)
            if balance_error:
                result['balance_errors'].append({'journal_entry_id': je_id, 'error': balance_error})

        sample_count = 0
        for je_id, je_postings in grouped_jes.items():
            qbo_entry, error = transform_to_qbo_journal_entry(je_id, je_postings)
            if qbo_entry:
                if qbo_entry.get('_class_name_missing_ref'):
                    result['class_ref_errors'].append({
                        'journal_entry_id': je_id,
                        'class_name': qbo_entry['_class_name_missing_ref'],
                        'error': f"class_name '{qbo_entry['_class_name_missing_ref']}' has no matching remote_id in tags table"
                    })
                if sample_count < 3:
                    result['sample_entries'].append({'je_id': je_id, 'qbo_format': qbo_entry})
                    sample_count += 1

    if publish_type in ('all', 'trade_accounts'):
        recv = query_trade_accounts(conn, sync_status, start_date, end_date, ta_type='receivable')
        recv_grouped = group_postings_by_ta(recv)
        result['invoice_count'] = len(recv_grouped)

        pay = query_trade_accounts(conn, sync_status, start_date, end_date, ta_type='payable')
        pay_grouped = group_postings_by_ta(pay)
        result['bill_count'] = len(pay_grouped)

        for ta_id, ta_posts in list(recv_grouped.items()) + list(pay_grouped.items()):
            first = ta_posts[0]
            if not first.get('contact_remote_id'):
                result['ta_contact_errors'].append({
                    'trade_account_id': ta_id, 'contact': first['ta_contact'],
                    'error': f"Contact '{first['ta_contact']}' has no remote_id"
                })

    if publish_type in ('all', 'payments', 'owner_cleared'):
        recv_pmts = query_trade_account_payments(conn, sync_status, start_date, end_date, ta_type='receivable')
        result['payment_count'] = len(recv_pmts)
        pay_pmts = query_trade_account_payments(conn, sync_status, start_date, end_date, ta_type='payable')
        result['bill_payment_count'] = len(pay_pmts)
        result['owner_cleared_count'] = len(
            query_owner_cleared_payments(conn, sync_status, start_date, end_date))
        # Consolidated payout-consumed-credit Payments (one per payout that consumes a CM
        # within the payout — the bank-funded CM-consume fix). Count = distinct payouts.
        pcc_rows = query_payout_consumed_credits(conn, sync_status)
        result['payout_consumed_credit_count'] = len({r['payout_id'] for r in pcc_rows})

        for row in recv_pmts + pay_pmts:
            if not row.get('ta_external_id'):
                result['payment_parent_errors'].append({
                    'payment_id': row['tap_id'], 'trade_account_id': row['trade_account_id'],
                    'warning': 'Parent trade account not synced yet'
                })

    return result


# =============================================================================
# Main
# =============================================================================

def main():
    lock = None

    try:
        args = parse_arguments()
        publish_type = args.publish_type

        if args.start_date:
            validate_date_format(args.start_date, 'start_date')
        if args.end_date:
            validate_date_format(args.end_date, 'end_date')

        # Acquire lock for non-dry-run
        if not args.dry_run:
            lock = FileLock(LOCK_FILE)
            if not lock.acquire():
                print(json.dumps({
                    "success": False, "dry_run": False,
                    "error": "Another instance is already running. Delete lock file if incorrect: " + LOCK_FILE
                }))
                sys.exit(1)

        # Validate credentials
        try:
            credentials = validate_qbo_env_vars()
        except ValueError as e:
            if args.dry_run:
                credentials = None
            else:
                print(json.dumps({"success": False, "dry_run": args.dry_run, "error": str(e)}))
                sys.exit(1)

        # Create QBO client
        client = None
        client_error = None
        if credentials:
            client, client_error = create_qbo_client(credentials)

        if client_error and not args.dry_run:
            print(json.dumps({"success": False, "dry_run": args.dry_run, "error": client_error}))
            sys.exit(1)

        # Connect to database
        try:
            conn = sqlite3.connect(config_loader.get_db_path())
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
        except sqlite3.Error as e:
            print(json.dumps({"success": False, "dry_run": args.dry_run, "error": f"Database connection failed: {str(e)}"}))
            sys.exit(1)

        # Query JEs (needed for JE publishing and dry-run)
        postings = []
        grouped_jes = {}
        if publish_type in ('all', 'jes'):
            postings = query_journal_entries(conn, args.sync_status, args.start_date, args.end_date)
            grouped_jes = group_postings_by_je(postings)

        # Dry-run mode
        if args.dry_run:
            validation = run_dry_run(
                client, conn, postings, grouped_jes,
                publish_type, args.sync_status, args.start_date, args.end_date
            )

            has_errors = (
                len(validation['account_mapping_errors']) > 0 or
                len(validation.get('contact_mapping_errors', [])) > 0 or
                len(validation['balance_errors']) > 0 or
                len(validation['class_ref_errors']) > 0 or
                len(validation.get('ta_contact_errors', [])) > 0 or
                (validation['oauth_status'] == 'invalid' and credentials is not None)
            )

            all_errors = (
                validation['account_mapping_errors'] +
                validation.get('contact_mapping_errors', []) +
                validation['balance_errors'] +
                validation['class_ref_errors'] +
                validation.get('ta_contact_errors', [])
            )

            result = {
                "success": not has_errors, "dry_run": True,
                "jes": {"processed": 0, "failed": 0, "skipped": 0},
                "invoices": {"processed": 0, "failed": 0, "skipped": 0},
                "bills": {"processed": 0, "failed": 0, "skipped": 0},
                "payments": {"processed": 0, "failed": 0, "skipped": 0},
                "bill_payments": {"processed": 0, "failed": 0, "skipped": 0},
                "errors": all_errors,
                "external_ids": {"jes": [], "invoices": [], "bills": [], "payments": [], "bill_payments": []},
                "date_range": {}, "validation": validation
            }

            print(json.dumps(result, indent=2))
            conn.close()
            sys.exit(0 if result['success'] else 1)

        # Live publish
        je_result = {"processed": 0, "failed": 0, "skipped": 0}
        inv_result = {"processed": 0, "failed": 0, "skipped": 0}
        bill_result = {"processed": 0, "failed": 0, "skipped": 0}
        cm_result = {"processed": 0, "failed": 0, "skipped": 0}
        vc_result = {"processed": 0, "failed": 0, "skipped": 0}
        capp_result = {"processed": 0, "failed": 0, "skipped": 0}
        pay_result = {"processed": 0, "failed": 0, "skipped": 0}
        pcc_result = {"processed": 0, "failed": 0, "skipped": 0}
        bp_result = {"processed": 0, "failed": 0, "skipped": 0}
        oc_result = {"processed": 0, "failed": 0, "skipped": 0}
        all_errors = []
        ext_ids = {"jes": [], "invoices": [], "bills": [],
                   "credit_memos": [], "vendor_credits": [], "credit_applications": [],
                   "payments": [], "payout_consumed_credits": [],
                   "bill_payments": [], "owner_cleared": []}

        rate_limiter = QBORateLimiter()

        # Thread a mutable ClientHolder through the publish phases: long runs
        # proactively refresh the 60-min access token and retry once on a
        # typed 401 (see _shared/auth.py). Dry-run above keeps the raw client
        # (single connection test, no long run).
        publish_client = ClientHolder(client) if client is not None else None

        # Phase 1: JEs
        if publish_type in ('all', 'jes') and grouped_jes:
            mapping_errors = validate_account_mappings(postings)
            skipped_je_ids = set()

            if mapping_errors:
                error_accounts = {e['account_code'] for e in mapping_errors}
                filtered_jes = {}
                for je_id, je_postings in grouped_jes.items():
                    has_error = any(p['account_code'] in error_accounts for p in je_postings)
                    if has_error:
                        skipped_je_ids.add(je_id)
                        update_sync_error(conn, 'journal_entries', je_id, {
                            'error_code': 'INVALID_ACCOUNT_REF', 'error_message': 'Account mapping error'
                        })
                    else:
                        filtered_jes[je_id] = je_postings
                grouped_jes = filtered_jes
                conn.commit()

            processed, failed, errors, external_ids = publish_batch(
                publish_client, rate_limiter, grouped_jes, conn, ENV_PATH
            )
            je_result = {"processed": processed, "failed": failed, "skipped": len(skipped_je_ids)}
            ext_ids["jes"] = external_ids
            all_errors.extend(errors)
            all_errors.extend([
                {'journal_entry_id': je_id, 'error_code': 'INVALID_ACCOUNT_REF', 'error_message': 'Account mapping error'}
                for je_id in skipped_je_ids
            ])

        # Phase 2: Invoices and Bills
        if publish_type in ('all', 'trade_accounts'):
            p, f, s, errs, eids = publish_invoices(
                publish_client, rate_limiter, conn, _config, args.sync_status, args.start_date, args.end_date, ENV_PATH
            )
            inv_result = {"processed": p, "failed": f, "skipped": s}
            ext_ids["invoices"] = eids
            all_errors.extend(errs)

            p, f, s, errs, eids = publish_bills(
                publish_client, rate_limiter, conn, _config, args.sync_status, args.start_date, args.end_date, ENV_PATH
            )
            bill_result = {"processed": p, "failed": f, "skipped": s}
            ext_ids["bills"] = eids
            all_errors.extend(errs)

        # Phase 2b: Credit Memos and Vendor Credits (standalone documents)
        if publish_type in ('all', 'trade_accounts'):
            p, f, s, errs, eids = publish_credit_memos(
                publish_client, rate_limiter, conn, _config, args.sync_status, args.start_date, args.end_date, ENV_PATH
            )
            cm_result = {"processed": p, "failed": f, "skipped": s}
            ext_ids["credit_memos"] = eids
            all_errors.extend(errs)

            p, f, s, errs, eids = publish_vendor_credits(
                publish_client, rate_limiter, conn, _config, args.sync_status, args.start_date, args.end_date, ENV_PATH
            )
            vc_result = {"processed": p, "failed": f, "skipped": s}
            ext_ids["vendor_credits"] = eids
            all_errors.extend(errs)

        # Phase 2c: Credit Applications (zero-amount Payments/BillPayments linking CM/VC to invoice/bill)
        if publish_type in ('all', 'trade_accounts', 'payments'):
            p, f, s, errs, eids = publish_credit_applications(
                publish_client, rate_limiter, conn, _config, args.sync_status, args.start_date, args.end_date, ENV_PATH
            )
            capp_result = {"processed": p, "failed": f, "skipped": s}
            ext_ids["credit_applications"] = eids
            all_errors.extend(errs)

        # Phase 3: Bank-funded Payments and BillPayments
        if publish_type in ('all', 'payments'):
            p, f, s, errs, eids = publish_payments(
                publish_client, rate_limiter, conn, _config, args.sync_status, args.start_date, args.end_date, ENV_PATH
            )
            pay_result = {"processed": p, "failed": f, "skipped": s}
            ext_ids["payments"] = eids
            all_errors.extend(errs)

            # Phase 3b: payout-keyed settlements that consume a CreditMemo within the payout
            # (bank-funded CM-consume fix — one consolidated mixed-Line Payment, net of the CM).
            # Disjoint from publish_payments above via the query_trade_account_payments exclusion.
            p, f, s, errs, eids = publish_payout_consumed_credits(
                publish_client, rate_limiter, conn, _config, args.sync_status, args.start_date, args.end_date, ENV_PATH
            )
            pcc_result = {"processed": p, "failed": f, "skipped": s}
            ext_ids["payout_consumed_credits"] = eids
            all_errors.extend(errs)

            p, f, s, errs, eids = publish_bill_payments(
                publish_client, rate_limiter, conn, _config, args.sync_status, args.start_date, args.end_date, ENV_PATH
            )
            bp_result = {"processed": p, "failed": f, "skipped": s}
            ext_ids["bill_payments"] = eids
            all_errors.extend(errs)

        # Phase 3c: Owner-cleared CM/VC applications (clearing JE + zero-amount
        # linking Payment/BillPayment). Runs after JEs (phase 1) and CM/VC
        # documents (phase 2b) so both sides of the link exist; the publisher
        # reuses any clearing JE phase 1 already published.
        if publish_type in ('all', 'payments', 'owner_cleared'):
            p, f, s, errs, eids = publish_owner_cleared(
                publish_client, rate_limiter, conn, _config, args.sync_status, args.start_date, args.end_date, ENV_PATH
            )
            oc_result = {"processed": p, "failed": f, "skipped": s}
            ext_ids["owner_cleared"] = eids
            all_errors.extend(errs)

        conn.close()

        all_results = [je_result, inv_result, bill_result, cm_result, vc_result,
                       capp_result, pay_result, pcc_result, bp_result, oc_result]
        total_failed = sum(r["failed"] for r in all_results)
        total_skipped = sum(r["skipped"] for r in all_results)

        result = {
            "success": total_failed == 0 and total_skipped == 0,
            "dry_run": False,
            "jes": je_result, "invoices": inv_result, "bills": bill_result,
            "credit_memos": cm_result, "vendor_credits": vc_result,
            "credit_applications": capp_result,
            "payments": pay_result, "payout_consumed_credits": pcc_result,
            "bill_payments": bp_result,
            "owner_cleared": oc_result,
            "errors": all_errors, "external_ids": ext_ids,
            "date_range": {"from": args.start_date or "", "to": args.end_date or ""}
        }

        print(json.dumps(result, indent=2))
        sys.exit(0 if result['success'] else 1)

    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}))
        sys.exit(1)

    finally:
        if lock:
            lock.release()


if __name__ == "__main__":
    main()
