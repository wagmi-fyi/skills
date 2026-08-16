#!/usr/bin/env python3
"""
Sync Contacts to QuickBooks Online

Reads all contacts from the local database, determines Customer vs Vendor type
based on posting context, creates missing entities in QBO, and writes remote_id
back to the local contacts table.

Handles dual-use contacts (e.g., Amazon on both A/R and A/P) by creating a
separate "(Vendor)" contact for the A/P side and updating local postings/TAs.

Usage:
    BOOKKEEPING_CONFIG_PATH=_local-bookkeeping/config.yaml \
      .venv/bin/python3 ~/.claude/skills/bookkeeping/adapters/qbo/sync_contacts.py [--dry_run]
"""

import argparse
import json
import sqlite3
import sys
import os
import time
from typing import Dict, List, Optional, Tuple

try:
    from quickbooks.objects.customer import Customer
    from quickbooks.objects.vendor import Vendor
    QBO_IMPORTS_AVAILABLE = True
except ImportError:
    QBO_IMPORTS_AVAILABLE = False

# Bootstrap config
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(script_dir, '..', '..', 'scripts', '_shared'))
sys.path.insert(0, script_dir)
import config_loader

from _shared.client import (
    validate_qbo_env_vars, create_qbo_client, test_qbo_connection,
    save_tokens_if_available, QBORateLimiter, MAX_RETRIES,
    fetch_all_pages
)

from dotenv import load_dotenv

_config = config_loader.load_config()
ENV_PATH = os.path.join(_config['local_dir'], 'adapters', '.env')
load_dotenv(ENV_PATH)


# =============================================================================
# CLI
# =============================================================================

def parse_arguments():
    parser = argparse.ArgumentParser(
        description='Sync contacts to QuickBooks Online as Customers/Vendors'
    )
    parser.add_argument(
        '--dry_run', action='store_true',
        help='Analyze and report what would be created without making changes'
    )
    return parser.parse_args()


# =============================================================================
# Rate-limited API helpers
# =============================================================================

def _api_call_with_retry(rate_limiter, func, description: str):
    """Execute an API call with rate limiting and retry."""
    for attempt in range(MAX_RETRIES):
        try:
            rate_limiter.wait()
            result = func()
            return result, None
        except Exception as e:
            error_str = str(e)
            is_transient = any(code in error_str for code in ['429', '500', '502', '503'])
            if is_transient and attempt < MAX_RETRIES - 1:
                rate_limiter.trigger_backoff(attempt)
                continue
            return None, f"{description}: {error_str}"
    return None, f"{description}: max retries exceeded"


# =============================================================================
# QBO Entity Operations
# =============================================================================

def fetch_existing_customers(client, rate_limiter) -> Tuple[Dict[str, str], Optional[str]]:
    """Fetch all customers from QBO. Returns {display_name: id} map."""
    def do_fetch():
        results = fetch_all_pages(Customer, client)
        save_tokens_if_available(client, ENV_PATH)
        return {c.DisplayName: c.Id for c in results}

    result, error = _api_call_with_retry(rate_limiter, do_fetch, "Fetch customers")
    return result or {}, error


def fetch_existing_vendors(client, rate_limiter) -> Tuple[Dict[str, str], Optional[str]]:
    """Fetch all vendors from QBO. Returns {display_name: id} map."""
    def do_fetch():
        results = fetch_all_pages(Vendor, client)
        save_tokens_if_available(client, ENV_PATH)
        return {v.DisplayName: v.Id for v in results}

    result, error = _api_call_with_retry(rate_limiter, do_fetch, "Fetch vendors")
    return result or {}, error


def create_qbo_customer(client, rate_limiter, display_name: str) -> Tuple[Optional[str], Optional[str]]:
    """Create a Customer in QBO. Returns (id, error)."""
    def do_create():
        customer = Customer()
        customer.DisplayName = display_name
        customer.CompanyName = display_name
        customer.save(qb=client)
        save_tokens_if_available(client, ENV_PATH)
        return customer.Id

    return _api_call_with_retry(rate_limiter, do_create, f"Create customer '{display_name}'")


def create_qbo_vendor(client, rate_limiter, display_name: str) -> Tuple[Optional[str], Optional[str]]:
    """Create a Vendor in QBO. Returns (id, error)."""
    def do_create():
        vendor = Vendor()
        vendor.DisplayName = display_name
        vendor.CompanyName = display_name
        vendor.save(qb=client)
        save_tokens_if_available(client, ENV_PATH)
        return vendor.Id

    return _api_call_with_retry(rate_limiter, do_create, f"Create vendor '{display_name}'")


# =============================================================================
# Contact Classification
# =============================================================================

def classify_contacts(conn: sqlite3.Connection) -> List[Dict]:
    """
    Analyze all contacts and determine what QBO entity type they need.

    Classification rules:
    - Contact on A/R postings -> needs Customer
    - Contact on A/P postings -> needs Vendor
    - Contact on both -> needs both (dual-use, vendor gets "(Vendor)" suffix)
    - Contact on TAs: receivable -> Customer, payable -> Vendor
    """
    cursor = conn.cursor()
    contacts = cursor.execute("SELECT name, remote_id, meta FROM contacts").fetchall()

    results = []
    for name, remote_id, meta_json in contacts:
        meta = json.loads(meta_json) if meta_json else {}

        ar_count = cursor.execute("""
            SELECT COUNT(*) FROM postings p
            JOIN chart_of_accounts coa ON p.account_code = coa.code
            WHERE p.contact = ? AND json_extract(coa.meta, '$.qbo_type') = 'Accounts Receivable'
        """, (name,)).fetchone()[0]

        ap_count = cursor.execute("""
            SELECT COUNT(*) FROM postings p
            JOIN chart_of_accounts coa ON p.account_code = coa.code
            WHERE p.contact = ? AND json_extract(coa.meta, '$.qbo_type') = 'Accounts Payable'
        """, (name,)).fetchone()[0]

        recv_ta_count = cursor.execute(
            "SELECT COUNT(*) FROM trade_accounts WHERE contact = ? AND type = 'receivable'",
            (name,)
        ).fetchone()[0]

        pay_ta_count = cursor.execute(
            "SELECT COUNT(*) FROM trade_accounts WHERE contact = ? AND type = 'payable'",
            (name,)
        ).fetchone()[0]

        needs_customer = ar_count > 0 or recv_ta_count > 0
        needs_vendor = ap_count > 0 or pay_ta_count > 0

        if not needs_customer and not needs_vendor:
            # (b) Honor an explicit, previously-assigned type so a hand-corrected
            # contact is not silently re-derived from posting context on later
            # runs. The strong A/R / A/P signals above always win; only this weak
            # fallback defers to a stored type. Fresh auto-created contacts carry
            # meta '{}' (no type) and fall through to derivation below.
            explicit_type = meta.get('type')
            if explicit_type in ('customer', 'vendor'):
                needs_customer = explicit_type == 'customer'
                needs_vendor = explicit_type == 'vendor'
            else:
                type_counts = cursor.execute("""
                    SELECT coa.type, COUNT(*) as cnt FROM postings p
                    JOIN chart_of_accounts coa ON p.account_code = coa.code
                    WHERE p.contact = ?
                    GROUP BY coa.type ORDER BY cnt DESC
                """, (name,)).fetchall()

                if type_counts:
                    dominant = type_counts[0][0]
                    # (a) Inventory/COGS purchases hit asset/expense accounts, but
                    # the counterparty is a supplier. Only income implies a
                    # customer; asset/expense/everything else => vendor. (Was
                    # `dominant in ('income','asset')`, which mis-typed every
                    # inventory-buying vendor as a customer.)
                    if dominant == 'income':
                        needs_customer = True
                    else:
                        needs_vendor = True
                else:
                    needs_vendor = True

        results.append({
            'name': name, 'remote_id': remote_id, 'meta': meta,
            'needs_customer': needs_customer, 'needs_vendor': needs_vendor,
            'dual_use': needs_customer and needs_vendor,
            'ar_postings': ar_count, 'ap_postings': ap_count,
            'recv_tas': recv_ta_count, 'pay_tas': pay_ta_count,
        })

    return results


# =============================================================================
# Local DB Updates for Dual-Use Contacts
# =============================================================================

def create_vendor_split(conn: sqlite3.Connection, contact_name: str, dry_run: bool) -> Dict:
    """
    For a dual-use contact, create a "(Vendor)" variant and repoint A/P
    postings and payable TAs to the new contact name.
    """
    vendor_name = f"{contact_name} (Vendor)"
    cursor = conn.cursor()

    ap_postings = cursor.execute("""
        SELECT COUNT(*) FROM postings p
        JOIN chart_of_accounts coa ON p.account_code = coa.code
        WHERE p.contact = ? AND json_extract(coa.meta, '$.qbo_type') = 'Accounts Payable'
    """, (contact_name,)).fetchone()[0]

    pay_tas = cursor.execute(
        "SELECT COUNT(*) FROM trade_accounts WHERE contact = ? AND type = 'payable'",
        (contact_name,)
    ).fetchone()[0]

    if not dry_run:
        cursor.execute(
            "INSERT OR IGNORE INTO contacts (name, remote_id, meta) VALUES (?, NULL, ?)",
            (vendor_name, json.dumps({'type': 'vendor', 'split_from': contact_name}))
        )
        cursor.execute("""
            UPDATE postings SET contact = ?
            WHERE contact = ? AND account_code IN (
                SELECT code FROM chart_of_accounts
                WHERE json_extract(meta, '$.qbo_type') = 'Accounts Payable'
            )
        """, (vendor_name, contact_name))
        cursor.execute(
            "UPDATE trade_accounts SET contact = ? WHERE contact = ? AND type = 'payable'",
            (vendor_name, contact_name)
        )
        conn.commit()

    return {
        'original': contact_name, 'vendor_name': vendor_name,
        'ap_postings_repointed': ap_postings, 'payable_tas_repointed': pay_tas,
    }


# =============================================================================
# Main Sync Logic
# =============================================================================

def sync_contacts(client, rate_limiter, conn: sqlite3.Connection, dry_run: bool) -> Dict:
    """Full sync: classify → split dual-use → fetch existing → create missing → update local."""
    result = {
        'classified': 0, 'customers_created': 0, 'customers_existing': 0,
        'vendors_created': 0, 'vendors_existing': 0,
        'dual_use_splits': [], 'skipped': 0, 'errors': [], 'details': [],
    }

    contacts = classify_contacts(conn)
    result['classified'] = len(contacts)

    for c in contacts:
        if c['dual_use']:
            split_info = create_vendor_split(conn, c['name'], dry_run)
            result['dual_use_splits'].append(split_info)

    contacts = classify_contacts(conn)

    existing_customers, cust_err = fetch_existing_customers(client, rate_limiter)
    if cust_err:
        result['errors'].append({'phase': 'fetch_customers', 'error': cust_err})

    existing_vendors, vend_err = fetch_existing_vendors(client, rate_limiter)
    if vend_err:
        result['errors'].append({'phase': 'fetch_vendors', 'error': vend_err})

    for c in contacts:
        name = c['name']
        has_remote = c['remote_id'] is not None and c['remote_id'] != ''

        if c['needs_customer'] and not c['needs_vendor']:
            if has_remote:
                result['skipped'] += 1
                continue

            qbo_id = existing_customers.get(name)
            if qbo_id:
                result['customers_existing'] += 1
                action = 'found_existing'
            else:
                if dry_run:
                    result['customers_created'] += 1
                    result['details'].append({'name': name, 'type': 'Customer', 'action': 'would_create'})
                    continue

                qbo_id, err = create_qbo_customer(client, rate_limiter, name)
                if err:
                    result['errors'].append({'contact': name, 'type': 'Customer', 'error': err})
                    continue
                result['customers_created'] += 1
                action = 'created'

            if not dry_run:
                conn.execute(
                    "UPDATE contacts SET remote_id = ?, meta = ? WHERE name = ?",
                    (qbo_id, json.dumps({'type': 'customer'}), name)
                )
                conn.commit()

            result['details'].append({'name': name, 'type': 'Customer', 'action': action, 'qbo_id': qbo_id})

        elif c['needs_vendor'] and not c['needs_customer']:
            if has_remote:
                result['skipped'] += 1
                continue

            qbo_id = existing_vendors.get(name)
            if qbo_id:
                result['vendors_existing'] += 1
                action = 'found_existing'
            else:
                if dry_run:
                    result['vendors_created'] += 1
                    result['details'].append({'name': name, 'type': 'Vendor', 'action': 'would_create'})
                    continue

                qbo_id, err = create_qbo_vendor(client, rate_limiter, name)
                if err:
                    result['errors'].append({'contact': name, 'type': 'Vendor', 'error': err})
                    continue
                result['vendors_created'] += 1
                action = 'created'

            if not dry_run:
                conn.execute(
                    "UPDATE contacts SET remote_id = ?, meta = ? WHERE name = ?",
                    (qbo_id, json.dumps({'type': 'vendor'}), name)
                )
                conn.commit()

            result['details'].append({'name': name, 'type': 'Vendor', 'action': action, 'qbo_id': qbo_id})

        else:
            result['skipped'] += 1

    return result


# =============================================================================
# Main
# =============================================================================

def main():
    try:
        args = parse_arguments()

        credentials = validate_qbo_env_vars()
        client, client_error = create_qbo_client(credentials)
        if client_error:
            print(json.dumps({"success": False, "error": client_error}, indent=2))
            sys.exit(1)

        success, msg = test_qbo_connection(client, ENV_PATH)
        if not success:
            print(json.dumps({"success": False, "error": msg}, indent=2))
            sys.exit(1)
        company_name = msg.replace("Connected to: ", "")

        rate_limiter = QBORateLimiter(min_interval=0.2)

        conn = sqlite3.connect(config_loader.get_db_path())
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

        try:
            result = sync_contacts(client, rate_limiter, conn, args.dry_run)

            output = {
                "success": len(result['errors']) == 0,
                "dry_run": args.dry_run,
                "company": company_name,
                "contacts_analyzed": result['classified'],
                "customers_created": result['customers_created'],
                "customers_existing": result['customers_existing'],
                "vendors_created": result['vendors_created'],
                "vendors_existing": result['vendors_existing'],
                "dual_use_splits": result['dual_use_splits'],
                "skipped": result['skipped'],
                "errors": result['errors'],
                "details": result['details'],
            }

            print(json.dumps(output, indent=2))
            sys.exit(0 if output['success'] else 1)

        finally:
            conn.close()

    except ValueError as e:
        print(json.dumps({"success": False, "error": str(e)}, indent=2))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"success": False, "error": f"Unexpected: {str(e)}"}, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
