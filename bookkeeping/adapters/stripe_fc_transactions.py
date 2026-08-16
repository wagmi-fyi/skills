#!/usr/bin/env python3
"""
Stripe Financial Connections — Transaction Fetch Adapter
Pulls transactions for a connected FC account, transforms to universal JSON,
outputs to stdout for piping to ingest_universal.py.

Usage:
    python adapters/stripe_fc_transactions.py --account_id fca_xxx
    python adapters/stripe_fc_transactions.py --account_id fca_xxx --start_date 2026-01-01 --end_date 2026-01-31
    python adapters/stripe_fc_transactions.py --account_id fca_xxx --after_refresh fctxnref_xxx
    python adapters/stripe_fc_transactions.py --account_id fca_xxx > pull.json  # inspect count, then ingest_universal.py --file
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
        description="Fetch transactions from Stripe Financial Connections account"
    )
    parser.add_argument(
        '--account_id', required=True,
        help='Stripe Financial Connections account ID (fca_xxx)'
    )
    parser.add_argument(
        '--start_date', type=str, default=None,
        help='Filter transactions on or after this date (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--end_date', type=str, default=None,
        help='Filter transactions on or before this date (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--include_pending', action='store_true',
        help='Include pending transactions (default: posted only)'
    )
    parser.add_argument(
        '--after_refresh', type=str, default=None,
        help='Stripe TransactionRefresh ID (fctxnref_xxx, from stripe_fc_refresh.py read mode) for incremental sync'
    )
    parser.add_argument(
        '--balance_type', type=str, default=None, choices=['cash', 'credit'],
        help='Override balance_type (cash or credit). Fallback if account subcategory unavailable.'
    )
    return parser.parse_args()


def validate_date_format(date_str, param_name):
    """Validate date is in YYYY-MM-DD format."""
    try:
        datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        print(json.dumps({
            "success": False,
            "error": f"{param_name} must be in YYYY-MM-DD format, got: {date_str}"
        }))
        sys.exit(1)


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
# Balance Type Resolution
# =============================================================================

# Mirrored in scripts/manage_bank_feeds.py (AUTO_BALANCE_TYPE_SUBCATEGORIES) — keep in sync.
SUBCATEGORY_MAP = {
    'checking': 'cash',
    'savings': 'cash',
    'money_market': 'cash',
    'prepaid': 'cash',
    'credit_card': 'credit',
    'line_of_credit': 'credit',
}


def resolve_balance_type(account_id, cli_balance_type):
    """
    Resolve balance_type from CLI arg first, then FC account subcategory.
    Fails loud if neither source is available.
    """
    if cli_balance_type:
        return cli_balance_type

    try:
        account = stripe.financial_connections.Account.retrieve(account_id)
        subcategory = getattr(account, 'subcategory', None)
        if subcategory and subcategory in SUBCATEGORY_MAP:
            return SUBCATEGORY_MAP[subcategory]
    except stripe.StripeError as e:
        print(f"Warning: Could not retrieve account for balance_type resolution: {e}", file=sys.stderr)

    print(json.dumps({
        "success": False,
        "error": f"Cannot resolve balance_type for account {account_id}. "
                 f"Account subcategory unavailable and --balance_type not provided."
    }))
    sys.exit(1)


# =============================================================================
# Core Logic: Fetch Transactions
# =============================================================================

def fetch_transactions(account_id, after_refresh=None):
    """
    Paginate through all transactions for the given FC account.
    Returns list of Stripe transaction objects.
    """
    all_transactions = []
    params = {'account': account_id, 'limit': 100}

    if after_refresh:
        params['transaction_refresh'] = {'after': after_refresh}

    has_more = True
    while has_more:
        response = stripe.financial_connections.Transaction.list(**params)
        page_data = response.data
        all_transactions.extend(page_data)
        print(f"Fetched {len(all_transactions)} transactions...", file=sys.stderr)

        has_more = response.has_more
        if has_more and page_data:
            params['starting_after'] = page_data[-1].id

    return all_transactions


# =============================================================================
# Core Logic: Filter Transactions
# =============================================================================

def filter_transactions(transactions, include_pending=False, start_date=None, end_date=None):
    """
    Filter by status and date range.
    Returns filtered list.
    """
    # Status filter
    allowed_statuses = {'posted'}
    if include_pending:
        allowed_statuses.add('pending')

    filtered = [t for t in transactions if t.status in allowed_statuses]
    status_desc = "posted+pending" if include_pending else "posted only"

    # Date filter
    date_desc = "all dates"
    if start_date or end_date:
        def txn_date(t):
            return datetime.fromtimestamp(t.transacted_at, tz=timezone.utc).strftime('%Y-%m-%d')

        if start_date:
            filtered = [t for t in filtered if txn_date(t) >= start_date]
        if end_date:
            filtered = [t for t in filtered if txn_date(t) <= end_date]

        if start_date and end_date:
            date_desc = f"{start_date} to {end_date}"
        elif start_date:
            date_desc = f"from {start_date}"
        else:
            date_desc = f"through {end_date}"

    print(f"Filtered to {len(filtered)} transactions ({status_desc}, {date_desc})", file=sys.stderr)
    return filtered


# =============================================================================
# Core Logic: Transform to Universal JSON
# =============================================================================

def transform_to_universal_json(transactions, balance_type):
    """
    Map Stripe transactions to universal JSON contract.
    Returns envelope dict.
    """
    # Universal contract: positive amount increases the account's NORMAL balance
    # (debit for cash/asset, credit for credit/liability). FC emits credit-card
    # purchases as negative, so credit accounts need a sign flip to match the
    # convention bank-CSV adapters established. Confirmed against bank and Stripe
    # test institutions; the balance-verification Hard Stop catches any
    # institution that deviates from this convention.
    sign = -1 if balance_type == 'credit' else 1
    items = []
    for t in transactions:
        items.append({
            'external_id': t.id,
            'date': datetime.fromtimestamp(t.transacted_at, tz=timezone.utc).strftime('%Y-%m-%d'),
            'amount': sign * t.amount,
            'reference': t.description if t.description else "No description",
            'balance_type': balance_type,
            'currency': t.currency,
            # str() is StripeObject's stable JSON serialization across stripe-python majors
            # (to_dict_recursive was removed; dict() raises KeyError on v15+)
            'raw_data': json.loads(str(t)),
        })

    return {'transactions': items}


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
        if args.start_date:
            validate_date_format(args.start_date, '--start_date')
        if args.end_date:
            validate_date_format(args.end_date, '--end_date')

        api_key = validate_env_vars()
        stripe.api_key = api_key

        # Resolve balance_type
        balance_type = resolve_balance_type(args.account_id, args.balance_type)

        # Fetch
        raw_transactions = fetch_transactions(args.account_id, args.after_refresh)

        # Filter
        filtered = filter_transactions(
            raw_transactions,
            include_pending=args.include_pending,
            start_date=args.start_date,
            end_date=args.end_date,
        )

        # Transform
        envelope = transform_to_universal_json(filtered, balance_type)

        # Output
        count = len(envelope['transactions'])
        date_range = "N/A"
        if count > 0:
            dates = [t['date'] for t in envelope['transactions']]
            date_range = f"{min(dates)} to {max(dates)}"

        print(json.dumps(envelope, indent=2))
        print(f"Output {count} transactions for account {args.account_id} ({date_range})", file=sys.stderr)

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
