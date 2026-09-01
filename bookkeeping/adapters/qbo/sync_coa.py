#!/usr/bin/env python3
"""
Sync Chart of Accounts from QuickBooks Online
Pulls account list from QBO and upserts to local chart_of_accounts table.

Usage:
    BOOKKEEPING_CONFIG_PATH=_local-bookkeeping/config.yaml \
      {python} {module_root}/adapters/qbo/sync_coa.py [--dry_run]
"""

import argparse
import json
import sqlite3
import sys
import os
from typing import Dict, List, Optional, Tuple

try:
    from quickbooks.objects.account import Account
    QBO_IMPORTS_AVAILABLE = True
except ImportError:
    QBO_IMPORTS_AVAILABLE = False

# Bootstrap config
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(script_dir, '..', '..', 'scripts', '_shared'))
sys.path.insert(0, script_dir)
import config_loader

from _shared.client import (
    validate_qbo_env_vars, create_qbo_client, save_tokens_if_available,
    fetch_all_pages
)

from dotenv import load_dotenv

_config = config_loader.load_config()
ENV_PATH = os.path.join(_config['local_dir'], 'adapters', '.env')
load_dotenv(ENV_PATH)


def parse_arguments():
    parser = argparse.ArgumentParser(description='Sync Chart of Accounts from QuickBooks Online')
    parser.add_argument('--dry_run', action='store_true', help='Show what would be synced without making changes')
    return parser.parse_args()


def fetch_accounts_from_qbo(client) -> Tuple[List[Dict], Optional[str]]:
    """Fetch all accounts from QBO, paginated (.all() alone caps at MAXRESULTS 100)."""
    try:
        accounts = fetch_all_pages(Account, client)
        save_tokens_if_available(client, ENV_PATH)

        result = []
        for account in accounts:
            qbo_type = getattr(account, 'AccountType', '') or ''
            account_type = map_qbo_account_type(qbo_type)

            acct_num = getattr(account, 'AcctNum', None)
            if not acct_num:
                acct_num = f"QBO-{account.Id}"

            result.append({
                'code': acct_num,
                'name': account.Name,
                'type': account_type,
                'remote_id': account.Id,
                'qbo_type': qbo_type,
                'active': getattr(account, 'Active', True),
            })

        return result, None

    except Exception as e:
        return [], f"Failed to fetch accounts: {str(e)}"


def map_qbo_account_type(qbo_type: str) -> str:
    """Map QBO AccountType to our simplified type."""
    qbo_type_lower = qbo_type.lower()

    if any(t in qbo_type_lower for t in ['bank', 'accounts receivable', 'other current asset', 'fixed asset', 'other asset']):
        return 'asset'
    if any(t in qbo_type_lower for t in ['accounts payable', 'credit card', 'other current liability', 'long term liability']):
        return 'liability'
    if 'equity' in qbo_type_lower:
        return 'equity'
    if 'income' in qbo_type_lower or 'revenue' in qbo_type_lower:
        return 'income'
    if 'expense' in qbo_type_lower or 'cost of goods' in qbo_type_lower:
        return 'expense'
    return 'asset'


def upsert_accounts(conn: sqlite3.Connection, accounts: List[Dict], dry_run: bool) -> Dict:
    """Upsert accounts to chart_of_accounts table."""
    cursor = conn.cursor()

    inserted = 0
    updated = 0
    skipped = 0
    details = []

    for account in accounts:
        if not account.get('active', True):
            skipped += 1
            continue

        cursor.execute(
            "SELECT code, name, type, meta FROM chart_of_accounts WHERE remote_id = ?",
            (account['remote_id'],)
        )
        existing_by_remote = cursor.fetchone()

        cursor.execute(
            "SELECT code, name, type, remote_id, meta FROM chart_of_accounts WHERE code = ?",
            (account['code'],)
        )
        existing_by_code = cursor.fetchone()

        def _merge_meta(existing_row, meta_idx):
            existing_meta = {}
            if existing_row and existing_row[meta_idx]:
                try:
                    existing_meta = json.loads(existing_row[meta_idx])
                except (json.JSONDecodeError, TypeError):
                    existing_meta = {}
            existing_meta['qbo_type'] = account['qbo_type']
            return json.dumps(existing_meta)

        if existing_by_remote:
            if not dry_run:
                merged_meta = _merge_meta(existing_by_remote, 3)
                cursor.execute(
                    "UPDATE chart_of_accounts SET name = ?, type = ?, code = ?, meta = ? WHERE remote_id = ?",
                    (account['name'], account['type'], account['code'], merged_meta, account['remote_id'])
                )
            updated += 1
            details.append({'action': 'update', 'code': account['code'], 'name': account['name'], 'remote_id': account['remote_id']})

        elif existing_by_code:
            if not dry_run:
                merged_meta = _merge_meta(existing_by_code, 4)
                cursor.execute(
                    "UPDATE chart_of_accounts SET name = ?, type = ?, remote_id = ?, meta = ? WHERE code = ?",
                    (account['name'], account['type'], account['remote_id'], merged_meta, account['code'])
                )
            updated += 1
            details.append({'action': 'update', 'code': account['code'], 'name': account['name'], 'remote_id': account['remote_id'], 'note': 'added remote_id to existing code'})

        else:
            if not dry_run:
                cursor.execute(
                    "INSERT INTO chart_of_accounts (code, name, type, remote_id, meta) VALUES (?, ?, ?, ?, ?)",
                    (account['code'], account['name'], account['type'], account['remote_id'], json.dumps({'qbo_type': account['qbo_type']}))
                )
            inserted += 1
            details.append({'action': 'insert', 'code': account['code'], 'name': account['name'], 'type': account['type'], 'remote_id': account['remote_id']})

    if not dry_run:
        conn.commit()

    return {'inserted': inserted, 'updated': updated, 'skipped': skipped, 'details': details}


def main():
    try:
        args = parse_arguments()
        credentials = validate_qbo_env_vars()

        client, client_error = create_qbo_client(credentials)
        if client_error:
            print(json.dumps({"success": False, "error": client_error}, indent=2))
            sys.exit(1)

        accounts, fetch_error = fetch_accounts_from_qbo(client)
        if fetch_error:
            print(json.dumps({"success": False, "error": fetch_error}, indent=2))
            sys.exit(1)

        conn = sqlite3.connect(config_loader.get_db_path())
        conn.execute("PRAGMA foreign_keys = ON")

        try:
            result = upsert_accounts(conn, accounts, args.dry_run)
            output = {
                "success": True,
                "dry_run": args.dry_run,
                "accounts_fetched": len(accounts),
                "inserted": result['inserted'],
                "updated": result['updated'],
                "skipped": result['skipped'],
            }
            if args.dry_run:
                output['details'] = result['details']

            print(json.dumps(output, indent=2))
            sys.exit(0)
        finally:
            conn.close()

    except ValueError as e:
        print(json.dumps({"success": False, "error": str(e)}, indent=2))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"success": False, "error": f"Unexpected error: {str(e)}"}, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
