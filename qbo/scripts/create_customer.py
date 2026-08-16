#!/usr/bin/env python3
"""
QBO Create Customer - Find or create a Customer in QuickBooks Online.

Examples:
    # Find or create with just a name
    python create_customer.py --display_name="Example Bakery Co"

    # Create with full contact details
    python create_customer.py --display_name="Example Bakery Co" \
        --company_name="Example Bakery Co" \
        --email="info@example.com" \
        --phone="555-0100" \
        --line1="1 Example Street" --city="Anytown" --state="OH" --zip="43000" --country="US"
"""

import argparse
import json
import sys
import time
from typing import Any, Dict, Optional

import qbo_client


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


def find_existing_customer(client_holder: ClientHolder, display_name: str) -> Optional[Dict]:
    """
    Check if a customer with this DisplayName already exists.
    Returns the existing customer dict if found, None otherwise.
    """
    entity_class, _ = qbo_client.get_entity_class('Customer')

    # Escape single quotes in name for QBO query
    escaped_name = display_name.replace("'", "\\'")

    def do_query(c):
        query = f"SELECT * FROM Customer WHERE DisplayName = '{escaped_name}'"
        return entity_class.query(query, qb=c)

    results = retry_with_backoff(
        lambda: execute_with_auth_retry(do_query, client_holder)
    )

    if results:
        return entity_to_dict(results[0])

    return None


def parse_arguments():
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description='Find or create a Customer in QuickBooks Online',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument('--display_name', required=True,
                        help='Customer display name (must be unique in QBO)')
    parser.add_argument('--company_name', default='',
                        help='Company name')
    parser.add_argument('--email', default='',
                        help='Primary email address')
    parser.add_argument('--phone', default='',
                        help='Primary phone number')
    parser.add_argument('--line1', default='',
                        help='Billing address line 1')
    parser.add_argument('--city', default='',
                        help='Billing address city')
    parser.add_argument('--state', default='',
                        help='Billing address state')
    parser.add_argument('--zip', default='',
                        help='Billing address postal code')
    parser.add_argument('--country', default='',
                        help='Billing address country')
    return parser.parse_args()


def main():
    try:
        args = parse_arguments()

        # Create client
        client, client_error = qbo_client.create_client()
        if client_error:
            output_json(client_error)
            sys.exit(1)

        client_holder = ClientHolder(client)

        # Check if customer already exists
        existing = find_existing_customer(client_holder, args.display_name)
        if existing:
            output_json({
                "success": True,
                "action": "found_existing",
                "customer_id": existing.get('Id'),
                "display_name": args.display_name,
                "data": existing
            })
            sys.exit(0)

        # Build customer object
        from quickbooks.objects.customer import Customer
        from quickbooks.objects.base import Address, PhoneNumber, EmailAddress

        customer = Customer()
        customer.DisplayName = args.display_name

        if args.company_name:
            customer.CompanyName = args.company_name

        if args.email:
            email = EmailAddress()
            email.Address = args.email
            customer.PrimaryEmailAddr = email

        if args.phone:
            phone = PhoneNumber()
            phone.FreeFormNumber = args.phone
            customer.PrimaryPhone = phone

        if args.line1 or args.city or args.state or args.zip:
            addr = Address()
            if args.line1:
                addr.Line1 = args.line1
            if args.city:
                addr.City = args.city
            if args.state:
                addr.CountrySubDivisionCode = args.state
            if args.zip:
                addr.PostalCode = args.zip
            if args.country:
                addr.Country = args.country
            customer.BillAddr = addr

        # Save to QBO
        def save_customer(c):
            return customer.save(qb=c)

        try:
            saved = retry_with_backoff(
                lambda: execute_with_auth_retry(save_customer, client_holder)
            )

            saved_dict = entity_to_dict(saved)
            output_json({
                "success": True,
                "action": "created",
                "customer_id": saved_dict.get('Id'),
                "display_name": args.display_name,
                "data": saved_dict
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
