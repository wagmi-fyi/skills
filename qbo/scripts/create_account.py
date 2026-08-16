#!/usr/bin/env python3
"""
QBO Create Account - Create new accounts in the QuickBooks Online Chart of Accounts.

Examples:
    # Create an expense account
    python create_account.py --name="Merchant Processing Fees" --account_type=Expense

    # Create with account number and sub-type
    python create_account.py --name="Business Checking" --account_type=Bank \
        --account_sub_type=Checking --acct_num=1000

    # Create a sub-account
    python create_account.py --name="Stripe Fees" --account_type=Expense \
        --parent_id=85 --account_sub_type=OtherMiscellaneousExpense
"""

import argparse
import json
import sys
import time
from typing import Any, Dict, Optional

# Import shared client module
import qbo_client

# Valid QBO AccountType values
VALID_ACCOUNT_TYPES = [
    'Bank',
    'Accounts Receivable',
    'Other Current Asset',
    'Fixed Asset',
    'Other Asset',
    'Accounts Payable',
    'Credit Card',
    'Other Current Liability',
    'Long Term Liability',
    'Equity',
    'Income',
    'Cost of Goods Sold',
    'Expense',
    'Other Income',
    'Other Expense',
]


def parse_arguments():
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description='Create a new account in QuickBooks Online',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        '--name',
        required=True,
        help='Account name (must be unique in QBO)'
    )
    parser.add_argument(
        '--account_type',
        required=True,
        help='Account type (e.g., Bank, Expense, Income, Equity)'
    )
    parser.add_argument(
        '--account_sub_type',
        default='',
        help='Account sub-type (e.g., Checking, OtherMiscellaneousExpense)'
    )
    parser.add_argument(
        '--acct_num',
        default='',
        help='Account number/code'
    )
    parser.add_argument(
        '--description',
        default='',
        help='Account description'
    )
    parser.add_argument(
        '--parent_id',
        default='',
        help='Parent account ID to create a sub-account'
    )
    return parser.parse_args()


class ClientHolder:
    """Mutable holder for client to allow refresh during retries."""
    def __init__(self, client):
        self.client = client


def execute_with_auth_retry(func, client_holder: ClientHolder):
    """Execute function with automatic token refresh on auth errors."""
    try:
        return func(client_holder.client)
    except Exception as e:
        if not qbo_client.is_auth_error(e):
            raise

        new_client, refresh_error = qbo_client.refresh_client(client_holder.client)
        if refresh_error:
            raise Exception(f"Auth failed and refresh failed: {refresh_error['message']}") from e

        client_holder.client = new_client
        return func(client_holder.client)


def retry_with_backoff(func, max_retries: int = 3, base_delay: float = 1.0):
    """Execute function with exponential backoff retry on transient errors."""
    last_exception = None

    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as e:
            last_exception = e
            error_str = str(e).lower()

            is_rate_limit = '429' in error_str or 'rate' in error_str or 'throttl' in error_str
            is_transient = '500' in error_str or '502' in error_str or '503' in error_str or 'timeout' in error_str

            if not (is_rate_limit or is_transient) or attempt >= max_retries:
                raise

            delay = base_delay * (2 ** attempt)
            time.sleep(delay)

    raise last_exception


def entity_to_dict(entity) -> Dict[str, Any]:
    """Convert QBO entity object to dictionary."""
    result = {}

    for attr in dir(entity):
        if attr.startswith('_'):
            continue
        if callable(getattr(entity, attr)):
            continue

        try:
            value = getattr(entity, attr)

            if hasattr(value, '__dict__') and not isinstance(value, (str, int, float, bool, type(None))):
                value = entity_to_dict(value)
            elif isinstance(value, list):
                value = [
                    entity_to_dict(item) if hasattr(item, '__dict__') else item
                    for item in value
                ]

            result[attr] = value
        except Exception:
            pass

    return result


def output_json(data: Dict[str, Any]) -> None:
    """Print JSON output to stdout."""
    print(json.dumps(data, indent=2, default=str))


def check_duplicate_name(client_holder: ClientHolder, name: str) -> Optional[Dict]:
    """
    Check if an account with this name already exists.
    Returns the existing account dict if found, None otherwise.
    """
    entity_class, _ = qbo_client.get_entity_class('Account')

    def do_query(c):
        query = f"SELECT * FROM Account WHERE Name = '{name}'"
        return entity_class.query(query, qb=c)

    results = retry_with_backoff(
        lambda: execute_with_auth_retry(do_query, client_holder)
    )

    if results:
        return entity_to_dict(results[0])

    return None


def main():
    try:
        args = parse_arguments()

        # Validate account type
        if args.account_type not in VALID_ACCOUNT_TYPES:
            output_json({
                "success": False,
                "error": "INVALID_ACCOUNT_TYPE",
                "message": f"Invalid account type '{args.account_type}'",
                "valid_types": VALID_ACCOUNT_TYPES
            })
            sys.exit(1)

        # Create client
        client, client_error = qbo_client.create_client()
        if client_error:
            output_json(client_error)
            sys.exit(1)

        client_holder = ClientHolder(client)

        # Check for duplicate account name
        existing = check_duplicate_name(client_holder, args.name)
        if existing:
            output_json({
                "success": False,
                "error": "DUPLICATE_NAME",
                "message": f"Account named '{args.name}' already exists",
                "existing_account": existing
            })
            sys.exit(1)

        # Build account object
        from quickbooks.objects.account import Account
        from quickbooks.objects.base import Ref

        account = Account()
        account.Name = args.name
        account.AccountType = args.account_type

        if args.account_sub_type:
            account.AccountSubType = args.account_sub_type

        if args.acct_num:
            account.AcctNum = args.acct_num

        if args.description:
            account.Description = args.description

        if args.parent_id:
            account.SubAccount = True
            parent_ref = Ref()
            parent_ref.value = args.parent_id
            account.ParentRef = parent_ref

        # Save to QBO
        def save_account(c):
            return account.save(qb=c)

        try:
            saved = retry_with_backoff(
                lambda: execute_with_auth_retry(save_account, client_holder)
            )

            output_json({
                "success": True,
                "message": f"Account '{args.name}' created successfully",
                "data": entity_to_dict(saved)
            })

        except Exception as e:
            output_json({
                "success": False,
                "error": "API_ERROR",
                "message": str(e),
                "retry_attempted": True
            })
            sys.exit(1)

    except Exception as e:
        output_json({
            "success": False,
            "error": "UNEXPECTED_ERROR",
            "message": str(e)
        })
        sys.exit(1)


if __name__ == "__main__":
    main()
