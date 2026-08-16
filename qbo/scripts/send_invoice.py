#!/usr/bin/env python3
"""
QBO Send Invoice - Send an existing Invoice via email from QuickBooks Online.

Examples:
    # Send to customer's default email
    python send_invoice.py --invoice_id=456

    # Send to a specific email address
    python send_invoice.py --invoice_id=456 --send_to="buyer@example.com"
"""

import argparse
import json
import sys
import time
from typing import Any, Dict

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


def parse_arguments():
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description='Send a QBO Invoice via email',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument('--invoice_id', required=True,
                        help='QBO Invoice ID to send')
    parser.add_argument('--send_to', default='',
                        help='Override recipient email address')
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

        # Fetch the invoice first to verify it exists
        from quickbooks.objects.invoice import Invoice

        def fetch_invoice(c):
            return Invoice.get(args.invoice_id, qb=c)

        try:
            invoice = retry_with_backoff(
                lambda: execute_with_auth_retry(fetch_invoice, client_holder)
            )
        except Exception as e:
            output_json({
                "success": False,
                "error": "INVOICE_NOT_FOUND",
                "message": f"Could not fetch invoice {args.invoice_id}: {str(e)}"
            })
            sys.exit(1)

        # Send the invoice
        send_to = args.send_to if args.send_to else None

        def do_send(c):
            return invoice.send(qb=c, send_to=send_to)

        try:
            result = retry_with_backoff(
                lambda: execute_with_auth_retry(do_send, client_holder)
            )

            result_dict = entity_to_dict(result) if result else {}

            # Determine recipient
            recipient = args.send_to
            if not recipient:
                bill_email = getattr(invoice, 'BillEmail', None)
                if bill_email:
                    recipient = getattr(bill_email, 'Address', 'customer default')
                else:
                    recipient = 'customer default'

            output_json({
                "success": True,
                "invoice_id": args.invoice_id,
                "doc_number": getattr(invoice, 'DocNumber', ''),
                "sent_to": recipient,
                "data": result_dict
            })

        except Exception as e:
            output_json({
                "success": False,
                "error": "SEND_ERROR",
                "message": f"Failed to send invoice: {str(e)}",
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
