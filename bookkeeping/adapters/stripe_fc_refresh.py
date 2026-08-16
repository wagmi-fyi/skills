#!/usr/bin/env python3
"""
Stripe Financial Connections — Data Refresh Adapter
Triggers transactions and/or balance refreshes for a connected FC account and
reads refresh status. Companion to stripe_fc_transactions.py (which lists
transactions) and stripe_fc_balances.py (which reads balance values).

Refreshing both features together keeps the transaction list and the balance
snapshot coherent for balance verification.

Usage:
    # Trigger refresh of BOTH transactions and balance (fire-and-forget)
    python adapters/stripe_fc_refresh.py --account_id fca_xxx --trigger

    # Trigger a single feature
    python adapters/stripe_fc_refresh.py --account_id fca_xxx --trigger --features transactions

    # Read refresh statuses (incl. transaction_refresh.id for --after_refresh)
    python adapters/stripe_fc_refresh.py --account_id fca_xxx

    # Agent orchestration: trigger → wait → read
    python adapters/stripe_fc_refresh.py --account_id fca_xxx --trigger
    sleep 15
    python adapters/stripe_fc_refresh.py --account_id fca_xxx

Note: the refresh feature enum is SINGULAR "balance" (transactions|balance|ownership),
unlike AMA session permissions which use plural "balances".
"""

import argparse
import json
import sys
import os
from datetime import datetime, timezone

try:
    import stripe
    STRIPE_AVAILABLE = True
except ImportError:
    STRIPE_AVAILABLE = False

# Load config to find local_dir for .env
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts', '_shared'))
import config_loader
_config = config_loader.load_config()
ENV_PATH = os.path.join(_config['local_dir'], 'adapters', '.env')

from dotenv import load_dotenv
load_dotenv(ENV_PATH)

# Refresh feature enum (singular "balance") — do not confuse with the session
# permissions enum (plural "balances") used at bundle/session creation.
VALID_FEATURES = {'transactions', 'balance', 'ownership'}


# =============================================================================
# CLI Setup
# =============================================================================

def parse_arguments():
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Trigger or read data refreshes for a Stripe Financial Connections account"
    )
    parser.add_argument(
        '--account_id', required=True,
        help='Stripe Financial Connections account ID (fca_xxx)'
    )
    parser.add_argument(
        '--trigger', action='store_true',
        help='Trigger a refresh (fire-and-forget, does NOT poll or wait)'
    )
    parser.add_argument(
        '--features', type=str, default='transactions,balance',
        help='Comma-separated refresh features: transactions, balance (singular), ownership. '
             'Default: transactions,balance'
    )
    return parser.parse_args()


# =============================================================================
# Environment Validation
# =============================================================================

def validate_env_vars():
    """Check STRIPE_API_KEY exists, fail fast if missing."""
    api_key = os.getenv('STRIPE_API_KEY')
    if not api_key:
        print(json.dumps({
            "success": False,
            "error": "Missing STRIPE_API_KEY in environment. Set it in _local-bookkeeping/adapters/.env or set BOOKKEEPING_CONFIG_PATH"
        }))
        sys.exit(1)
    return api_key


# =============================================================================
# Helpers
# =============================================================================

def format_timestamp(unix_ts):
    """Convert Unix timestamp to ISO 8601 string, or return None."""
    if unix_ts is None:
        return None
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def refresh_block(obj, include_id=False):
    """Extract a refresh-status sub-object from the FC account, or None."""
    if obj is None:
        return None
    block = {}
    if include_id:
        block["id"] = getattr(obj, 'id', None)
    block["status"] = getattr(obj, 'status', None)
    block["last_attempted_at"] = format_timestamp(getattr(obj, 'last_attempted_at', None))
    block["next_refresh_available_at"] = format_timestamp(getattr(obj, 'next_refresh_available_at', None))
    return block


# =============================================================================
# Main
# =============================================================================

def main():
    if not STRIPE_AVAILABLE:
        print(json.dumps({
            "success": False,
            "error": "stripe package not installed. Install with: pip install stripe"
        }))
        sys.exit(1)

    try:
        args = parse_arguments()
        api_key = validate_env_vars()
        stripe.api_key = api_key

        # Trigger mode: fire-and-forget refresh
        if args.trigger:
            features = [f.strip() for f in args.features.split(',') if f.strip()]
            invalid = set(features) - VALID_FEATURES
            if invalid or not features:
                print(json.dumps({
                    "success": False,
                    "error": f"Invalid --features {sorted(invalid)}. "
                             f"Valid (singular enum): {sorted(VALID_FEATURES)}. "
                             f"Note: 'balance' is singular here; 'balances' is the session-permission spelling."
                }))
                sys.exit(1)

            try:
                stripe.financial_connections.Account.refresh_account(
                    args.account_id, features=features
                )
                print(f"Refresh triggered for {args.account_id}: {', '.join(features)}", file=sys.stderr)
            except stripe.StripeError as e:
                error_msg = str(e)
                if 'inactive' in error_msg.lower():
                    error_msg = (f"Account {args.account_id} is inactive. Stripe cannot refresh "
                                 f"inactive accounts. Original: {error_msg}")
                print(json.dumps({
                    "success": False,
                    "error": f"Refresh failed: {error_msg}"
                }))
                sys.exit(1)

            print(json.dumps({
                "success": True,
                "account_id": args.account_id,
                "refresh_triggered": True,
                "features": features,
                "refresh_status": "pending",
            }, indent=2))
            return

        # Read mode: report refresh statuses
        account = stripe.financial_connections.Account.retrieve(args.account_id)

        account_status = getattr(account, 'status', None)
        if account_status == 'inactive':
            print(f"WARNING: account {args.account_id} is inactive — refresh data may be stale "
                  f"and new refreshes will fail", file=sys.stderr)

        output = {
            "success": True,
            "account_id": args.account_id,
            "account_status": account_status,
            "subscriptions": list(getattr(account, 'subscriptions', None) or []),
            "balance_refresh": refresh_block(getattr(account, 'balance_refresh', None)),
            "transaction_refresh": refresh_block(getattr(account, 'transaction_refresh', None), include_id=True),
        }

        print(json.dumps(output, indent=2))

    except stripe.StripeError as e:
        print(json.dumps({
            "success": False,
            "error": f"Stripe API error: {str(e)}"
        }))
        sys.exit(1)

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
