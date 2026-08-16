#!/usr/bin/env python3
"""
Stripe Financial Connections — Balance Fetch Adapter
Refreshes and returns balance data for a connected FC account.
Consumed by agent/quality gate logic, not piped to ingest.

Usage:
    # Read cached balance
    python adapters/stripe_fc_balances.py --account_id fca_xxx

    # Trigger refresh (fire-and-forget)
    python adapters/stripe_fc_balances.py --account_id fca_xxx --refresh

    # Agent orchestration: refresh → wait → read
    python adapters/stripe_fc_balances.py --account_id fca_xxx --refresh
    sleep 10
    python adapters/stripe_fc_balances.py --account_id fca_xxx
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


# =============================================================================
# CLI Setup
# =============================================================================

def parse_arguments():
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Fetch or refresh balance for a Stripe Financial Connections account"
    )
    parser.add_argument(
        '--account_id', required=True,
        help='Stripe Financial Connections account ID (fca_xxx)'
    )
    parser.add_argument(
        '--refresh', action='store_true',
        help='Trigger a balance refresh (fire-and-forget, does NOT poll or wait)'
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
# Core Logic: Trigger Refresh
# =============================================================================

def trigger_refresh(account_id):
    """
    Fire-and-forget balance refresh. Returns immediately with refresh status.
    Does NOT poll or wait — the agent decides when to check back.
    """
    result = stripe.financial_connections.Account.refresh_account(
        account_id, features=["balance"]
    )
    print(f"Balance refresh triggered for {account_id}", file=sys.stderr)
    return result


# =============================================================================
# Core Logic: Fetch Balance
# =============================================================================

def fetch_balance(account_id):
    """
    Retrieve cached balance from the FC account object.
    Returns the account object.
    """
    account = stripe.financial_connections.Account.retrieve(account_id)
    return account


# =============================================================================
# Helpers
# =============================================================================

def format_timestamp(unix_ts):
    """Convert Unix timestamp to ISO 8601 string, or return None."""
    if unix_ts is None:
        return None
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


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

        # Refresh mode: trigger and return immediately
        if args.refresh:
            try:
                trigger_refresh(args.account_id)
            except stripe.StripeError as e:
                error_msg = str(e)
                # Detect specific failure reasons
                if 'inactive' in error_msg.lower():
                    error_msg = f"Account {args.account_id} is inactive. Stripe cannot refresh inactive accounts. Original: {error_msg}"
                print(json.dumps({
                    "success": False,
                    "error": f"Balance refresh failed: {error_msg}"
                }))
                sys.exit(1)

            print(json.dumps({
                "success": True,
                "account_id": args.account_id,
                "refresh_triggered": True,
                "refresh_status": "pending",
                "balance": None,
            }, indent=2))
            return

        # Read mode: fetch cached balance
        account = fetch_balance(args.account_id)

        # Check account status
        if getattr(account, 'status', None) == 'inactive':
            print(json.dumps({
                "success": False,
                "error": f"Account {args.account_id} is inactive. Balance data may be stale or unavailable."
            }))
            sys.exit(1)

        # Extract balance
        balance = getattr(account, 'balance', None)
        if balance is None:
            print(json.dumps({
                "success": False,
                "error": f"No balance data available for account {args.account_id}. "
                         f"A balance refresh may not have been performed yet. "
                         f"Use --refresh to trigger one."
            }))
            sys.exit(1)

        # Extract balance fields
        # Stripe FC balance structure:
        #   balance.type = "cash" | "credit"
        #   balance.current = {"usd": 123400}  (currency-keyed dict)
        #   balance.cash.available = {"usd": 120000}  (cash only)
        #   balance.credit.used = {"usd": 3400}  (credit only)
        #   balance.as_of = Unix timestamp
        # Convert to a plain dict once — StripeObject lost dict-protocol methods
        # (.items()) in stripe-python v15; str() is its stable JSON serialization.
        balance_data = json.loads(str(balance))

        balance_type = balance_data.get('type')
        current_map = balance_data.get('current') or {}
        as_of = balance_data.get('as_of')

        # Extract currency and current value from currency-keyed dict
        currency = None
        current_val = None
        for curr, amount in current_map.items():
            currency = curr
            current_val = amount
            break
        if len(current_map) > 1:
            print(f"WARNING: multi-currency balance ({', '.join(sorted(current_map))}) — "
                  f"primary fields report '{currency}' only; full map in 'current_all'",
                  file=sys.stderr)

        # Extract available (cash) or used (credit) from sub-objects
        available_val = None
        used_val = None

        if balance_type == 'cash':
            avail_map = (balance_data.get('cash') or {}).get('available') or {}
            if currency:
                available_val = avail_map.get(currency)
        elif balance_type == 'credit':
            used_map = (balance_data.get('credit') or {}).get('used') or {}
            if currency:
                used_val = used_map.get(currency)

        # Extract refresh status
        balance_refresh = getattr(account, 'balance_refresh', None)
        refresh_status = None
        next_refresh_at = None
        if balance_refresh:
            refresh_status = getattr(balance_refresh, 'status', None)
            next_refresh_raw = getattr(balance_refresh, 'next_refresh_available_at', None)
            next_refresh_at = format_timestamp(next_refresh_raw)

        output = {
            "success": True,
            "account_id": args.account_id,
            "balance": {
                "current": current_val,
                "available": available_val,
                "used": used_val,
                "as_of": format_timestamp(as_of),
                "type": balance_type,
            },
            "currency": currency,
            "current_all": current_map if len(current_map) > 1 else None,
            "refresh_status": refresh_status,
            "next_refresh_available_at": next_refresh_at,
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
