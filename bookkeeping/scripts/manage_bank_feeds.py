#!/usr/bin/env python3
"""
Manage Bank Feeds Registry — {local_dir}/bank_feeds.yaml
Sole owner of the machine-maintained mapping between chart_of_accounts codes
and live bank-feed provider accounts (Stripe Financial Connections fca_xxx).

The registry tells period-close which accounts are pulled live via the
stripe_fc_* adapters instead of from files. Connection happens via
operations/connect-bank-feeds.md (AMA bundle → adapters/ama_client.py).

Usage:
    # Map a connected FC account to a chart-of-accounts code
    python scripts/manage_bank_feeds.py add --account_code 10100 \\
        --provider_account_id fca_xxx --institution "Wells Fargo" \\
        --last4 1234 --category cash --subcategory checking

    # List mappings
    python scripts/manage_bank_feeds.py list
    python scripts/manage_bank_feeds.py list --account_code 10100 --status active

    # Update a mapping (status, incremental-sync bookkeeping)
    python scripts/manage_bank_feeds.py update --account_code 10100 --status disconnected
    python scripts/manage_bank_feeds.py update --account_code 10100 \\
        --last_txn_refresh_id fctxnref_xxx --last_pulled_through 2026-06-30
"""

import argparse
import json
import sys
import os
import sqlite3
from datetime import datetime, timezone

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '_shared'))
import config_loader

HEADER_COMMENT = (
    "# Machine-owned by scripts/manage_bank_feeds.py -- do not hand-edit.\n"
    "# Maps chart_of_accounts codes to live bank-feed provider accounts (Stripe FC).\n"
)

VALID_CATEGORIES = {'cash', 'credit', 'investment', 'other'}
VALID_STATUSES = {'active', 'disconnected', 'error'}
# Subcategories stripe_fc_transactions.py can auto-resolve balance_type for.
AUTO_BALANCE_TYPE_SUBCATEGORIES = {
    'checking', 'savings', 'money_market', 'prepaid', 'credit_card', 'line_of_credit'
}


# =============================================================================
# CLI Setup
# =============================================================================

def parse_arguments():
    """Parse CLI arguments with subcommands."""
    parser = argparse.ArgumentParser(
        description="Manage the bank_feeds.yaml registry (COA code ↔ FC account mapping)"
    )
    subparsers = parser.add_subparsers(dest='command', required=True)

    add = subparsers.add_parser('add', help='Map a provider account to a COA code')
    add.add_argument('--account_code', required=True, help='chart_of_accounts code (e.g., 10100)')
    add.add_argument('--provider', default='stripe_fc', help='Feed provider (default: stripe_fc)')
    add.add_argument('--provider_account_id', required=True, help='Provider account ID (fca_xxx)')
    add.add_argument('--institution', required=True, help='Institution name (e.g., "Wells Fargo")')
    add.add_argument('--last4', required=True, help='Last 4 digits of the account number')
    add.add_argument('--category', required=True, choices=sorted(VALID_CATEGORIES),
                     help='FC account category')
    add.add_argument('--subcategory', required=True,
                     help='FC account subcategory (checking, savings, credit_card, ...)')
    add.add_argument('--display_name', default=None, help='Optional display name from the institution')

    lst = subparsers.add_parser('list', help='List registry entries')
    lst.add_argument('--account_code', default=None, help='Filter by COA code')
    lst.add_argument('--status', default=None, choices=sorted(VALID_STATUSES), help='Filter by status')

    upd = subparsers.add_parser('update', help='Update an existing mapping')
    upd.add_argument('--account_code', required=True, help='chart_of_accounts code to update')
    upd.add_argument('--status', default=None, choices=sorted(VALID_STATUSES), help='New status')
    upd.add_argument('--last_txn_refresh_id', default=None,
                     help='Latest transaction refresh ID (fctxnref_xxx) for incremental pulls')
    upd.add_argument('--last_pulled_through', default=None,
                     help='Last date (YYYY-MM-DD) through which transactions were pulled+ingested')

    rmp = subparsers.add_parser('remap', help='Point an existing mapping at a new provider account (re-auth)')
    rmp.add_argument('--account_code', required=True, help='chart_of_accounts code to remap')
    rmp.add_argument('--provider_account_id', required=True, help='NEW provider account ID (fca_xxx)')
    rmp.add_argument('--institution', default=None, help='Override institution name if it changed')
    rmp.add_argument('--last4', default=None, help='Override last4 if it changed')
    rmp.add_argument('--subcategory', default=None, help='Override subcategory if it changed')
    rmp.add_argument('--display_name', default=None, help='Override display name')

    return parser.parse_args()


# =============================================================================
# Registry I/O
# =============================================================================

def registry_path(config):
    """Resolve the registry file path from config."""
    return os.path.join(config['local_dir'], 'bank_feeds.yaml')


def load_registry(path):
    """Load the registry, or return an empty structure if it doesn't exist yet."""
    if not os.path.exists(path):
        return {'version': 1, 'accounts': []}
    with open(path, 'r') as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or not isinstance(data.get('accounts'), list):
        raise ValueError(
            f"Registry at {path} is malformed (expected mapping with 'accounts' list). "
            f"Fix or remove the file before proceeding."
        )
    return data


def save_registry(path, data):
    """Write the registry atomically (temp + rename) with its machine-owned header.

    Atomic replace protects last_pulled_through — the only guard against
    duplicate history re-ingest — from torn writes when sessions run in parallel.
    """
    tmp_path = path + '.tmp'
    with open(tmp_path, 'w') as f:
        f.write(HEADER_COMMENT)
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
    os.replace(tmp_path, path)


# =============================================================================
# Database
# =============================================================================

def lookup_account(config, account_code):
    """Query chart_of_accounts for the code. Returns {code, name} or None."""
    db_path = os.path.join(config['database_dir'], config['database_name'])
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found at {db_path}")
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT code, name FROM chart_of_accounts WHERE code = ?", (account_code,))
        row = cursor.fetchone()
        return {'code': row[0], 'name': row[1]} if row else None
    finally:
        conn.close()


# =============================================================================
# Commands
# =============================================================================

def cmd_add(args, config):
    """Add a new mapping after validating the COA code and uniqueness."""
    account = lookup_account(config, args.account_code)
    if account is None:
        print(json.dumps({
            "success": False,
            "error": f"Account code {args.account_code} not found in chart_of_accounts. "
                     f"Create the account first."
        }))
        sys.exit(1)

    path = registry_path(config)
    registry = load_registry(path)

    for entry in registry['accounts']:
        if entry.get('account_code') == args.account_code:
            print(json.dumps({
                "success": False,
                "error": f"Account code {args.account_code} already mapped to "
                         f"{entry.get('provider_account_id')}. Use 'remap' to point it at a new "
                         f"provider account, 'update' to change fields, or "
                         f"'update --status disconnected' to retire the feed."
            }))
            sys.exit(1)
        if entry.get('provider_account_id') == args.provider_account_id:
            print(json.dumps({
                "success": False,
                "error": f"Provider account {args.provider_account_id} already mapped to "
                         f"account code {entry.get('account_code')}."
            }))
            sys.exit(1)

    if args.subcategory not in AUTO_BALANCE_TYPE_SUBCATEGORIES:
        print(f"WARNING: subcategory '{args.subcategory}' is outside the set "
              f"stripe_fc_transactions.py auto-resolves balance_type for "
              f"({', '.join(sorted(AUTO_BALANCE_TYPE_SUBCATEGORIES))}). "
              f"Pulls will need an explicit --balance_type.", file=sys.stderr)

    record = {
        'account_code': str(args.account_code),
        'provider': args.provider,
        'provider_account_id': args.provider_account_id,
        'institution': args.institution,
        'last4': str(args.last4),
        'category': args.category,
        'subcategory': args.subcategory,
        'display_name': args.display_name,
        'connected_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'status': 'active',
        'last_txn_refresh_id': None,
        'last_pulled_through': None,
    }
    registry['accounts'].append(record)
    save_registry(path, registry)

    print(f"Mapped {args.account_code} ({account['name']}) → {args.provider_account_id}",
          file=sys.stderr)
    print(json.dumps({
        "success": True,
        "account_code": args.account_code,
        "account_name": account['name'],
        "provider_account_id": args.provider_account_id,
        "registry_path": path,
    }, indent=2))


def cmd_list(args, config):
    """List registry entries with optional filters."""
    path = registry_path(config)
    registry = load_registry(path)

    accounts = registry['accounts']
    if args.account_code:
        accounts = [a for a in accounts if a.get('account_code') == args.account_code]
    if args.status:
        accounts = [a for a in accounts if a.get('status') == args.status]

    print(json.dumps({
        "success": True,
        "count": len(accounts),
        "registry_path": path,
        "accounts": accounts,
    }, indent=2))


def cmd_update(args, config):
    """Update fields on an existing mapping."""
    updates = {}
    if args.status is not None:
        updates['status'] = args.status
    if args.last_txn_refresh_id is not None:
        updates['last_txn_refresh_id'] = args.last_txn_refresh_id
    if args.last_pulled_through is not None:
        try:
            datetime.strptime(args.last_pulled_through, '%Y-%m-%d')
        except ValueError:
            print(json.dumps({
                "success": False,
                "error": f"--last_pulled_through must be YYYY-MM-DD, got: {args.last_pulled_through}"
            }))
            sys.exit(1)
        updates['last_pulled_through'] = args.last_pulled_through

    if not updates:
        print(json.dumps({
            "success": False,
            "error": "Nothing to update. Provide at least one of --status, "
                     "--last_txn_refresh_id, --last_pulled_through."
        }))
        sys.exit(1)

    path = registry_path(config)
    registry = load_registry(path)

    entry = next((a for a in registry['accounts']
                  if a.get('account_code') == args.account_code), None)
    if entry is None:
        print(json.dumps({
            "success": False,
            "error": f"Account code {args.account_code} not found in registry at {path}. "
                     f"Use 'add' to create the mapping."
        }))
        sys.exit(1)

    entry.update(updates)
    save_registry(path, registry)

    print(json.dumps({
        "success": True,
        "account_code": args.account_code,
        "updated_fields": sorted(updates.keys()),
        "registry_path": path,
    }, indent=2))


def cmd_remap(args, config):
    """Point an existing mapping at a new provider account after re-auth.

    Preserves last_pulled_through (books coverage doesn't reset with the
    connection); clears last_txn_refresh_id (refresh ids are per-connection).
    """
    path = registry_path(config)
    registry = load_registry(path)

    entry = next((a for a in registry['accounts']
                  if a.get('account_code') == args.account_code), None)
    if entry is None:
        print(json.dumps({
            "success": False,
            "error": f"Account code {args.account_code} not found in registry at {path}. "
                     f"Use 'add' for first-time mapping."
        }))
        sys.exit(1)

    old_fca = entry.get('provider_account_id')
    if args.provider_account_id == old_fca:
        print(json.dumps({
            "success": False,
            "error": f"New provider account equals current mapping ({old_fca}). Nothing to remap."
        }))
        sys.exit(1)

    other = next((a for a in registry['accounts']
                  if a.get('provider_account_id') == args.provider_account_id
                  and a.get('account_code') != args.account_code), None)
    if other is not None:
        print(json.dumps({
            "success": False,
            "error": f"Provider account {args.provider_account_id} is already mapped to "
                     f"account code {other.get('account_code')}."
        }))
        sys.exit(1)

    entry['provider_account_id'] = args.provider_account_id
    for field in ('institution', 'last4', 'subcategory', 'display_name'):
        val = getattr(args, field)
        if val is not None:
            entry[field] = str(val) if field == 'last4' else val
    entry['connected_at'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    entry['status'] = 'active'
    entry['last_txn_refresh_id'] = None

    save_registry(path, registry)

    print(f"WARNING: external_ids differ across connections — the next pull MUST be "
          f"date-bounded (--start_date > last_pulled_through="
          f"{entry.get('last_pulled_through')}, re-derived from MAX(banking_date) at "
          f"execution time) and seam-diffed before ingest. "
          f"See reference/bank-feeds-troubleshooting.md.", file=sys.stderr)
    print(json.dumps({
        "success": True,
        "account_code": args.account_code,
        "old_provider_account_id": old_fca,
        "new_provider_account_id": args.provider_account_id,
        "preserved_last_pulled_through": entry.get('last_pulled_through'),
        "registry_path": path,
    }, indent=2))


# =============================================================================
# Main
# =============================================================================

def main():
    try:
        args = parse_arguments()
        config = config_loader.load_config()

        if args.command == 'add':
            cmd_add(args, config)
        elif args.command == 'list':
            cmd_list(args, config)
        elif args.command == 'update':
            cmd_update(args, config)
        elif args.command == 'remap':
            cmd_remap(args, config)

    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({
            "success": False,
            "error": f"Unexpected error: {repr(e)}"
        }))
        sys.exit(1)


if __name__ == "__main__":
    main()
