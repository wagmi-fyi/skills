#!/usr/bin/env python3
"""
QBO Create Invoice - Create an Invoice in QuickBooks Online.

Examples:
    # Create a simple invoice
    python create_invoice.py \
        --customer_id=123 \
        --invoice_num="1700710" \
        --txn_date="2026-03-01" \
        --due_date="2026-03-31" \
        --line_items='[{"description": "Scottish Shortbread x24", "amount": 150.00, "item_id": "1"}]'

    # With class tracking and email
    python create_invoice.py \
        --customer_id=123 \
        --invoice_num="1700710" \
        --txn_date="2026-03-01" \
        --due_date="2026-03-31" \
        --class_id="1571398" \
        --bill_email="buyer@example.com" \
        --line_items='[{"description": "Products", "amount": 250.00, "item_id": "1"}]'
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


def check_duplicate_invoice(client_holder: ClientHolder, doc_number: str) -> Optional[Dict]:
    """Check if an invoice with this DocNumber already exists."""
    entity_class, _ = qbo_client.get_entity_class('Invoice')

    def do_query(c):
        query = f"SELECT * FROM Invoice WHERE DocNumber = '{doc_number}'"
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
        description='Create an Invoice in QuickBooks Online',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument('--customer_id', required=True,
                        help='QBO Customer ID')
    parser.add_argument('--invoice_num', required=True,
                        help='Invoice/DocNumber')
    parser.add_argument('--txn_date', required=True,
                        help='Invoice date YYYY-MM-DD')
    parser.add_argument('--due_date', required=True,
                        help='Due date YYYY-MM-DD')
    parser.add_argument('--line_items', required=True,
                        help='JSON array: [{"description": "...", "amount": 150.00, "item_id": "1"}]')
    parser.add_argument('--class_id', default='',
                        help='QBO Class ID for all line items')
    parser.add_argument('--bill_email', default='',
                        help='Billing email (overrides customer default)')
    parser.add_argument('--private_note', default='',
                        help='Internal memo/note')
    parser.add_argument('--custom_fields', default='',
                        help='JSON array of QBO custom fields: [{"DefinitionId":"1","Name":"Sales Rep","Type":"StringType","StringValue":"Jane Doe"}]')
    parser.add_argument('--terms_id', default='',
                        help='QBO Term ID (Invoice.SalesTermRef). e.g., 6=Due on receipt, 8=Net 30')
    parser.add_argument('--customer_memo', default='',
                        help='Customer-facing memo (Invoice.CustomerMemo) — prints on the invoice. e.g., "PO #1786SSF"')
    return parser.parse_args()


def main():
    try:
        args = parse_arguments()

        # Parse line items JSON
        try:
            line_items = json.loads(args.line_items)
        except json.JSONDecodeError as e:
            output_json({
                "success": False,
                "error": "INVALID_JSON",
                "message": f"Invalid line_items JSON: {str(e)}"
            })
            sys.exit(1)

        if not line_items:
            output_json({
                "success": False,
                "error": "EMPTY_LINE_ITEMS",
                "message": "At least one line item is required"
            })
            sys.exit(1)

        # Create client
        client, client_error = qbo_client.create_client()
        if client_error:
            output_json(client_error)
            sys.exit(1)

        client_holder = ClientHolder(client)

        # Check for duplicate invoice
        existing = check_duplicate_invoice(client_holder, args.invoice_num)
        if existing:
            # QBO enforces DocNumber uniqueness realm-wide (verified empirically
            # against a production realm: CustomTxnNumbers=false, 0 dup DocNumbers
            # across 492 invoices). So a DocNumber match alone could be either:
            #   (a) an idempotent re-sync of the same invoice we're trying to create
            #   (b) a true conflict where a different customer's invoice grabbed
            #       this DocNumber (e.g., QBO auto-assigned it).
            # Distinguish by CustomerRef — silently linking on case (b) writes a
            # bad external_id pointing to someone else's invoice.
            existing_customer_id = (existing.get('CustomerRef') or {}).get('value')
            if existing_customer_id != args.customer_id:
                output_json({
                    "success": False,
                    "error": "DOCNUMBER_CONFLICT",
                    "message": (
                        f"DocNumber '{args.invoice_num}' already used by "
                        f"Invoice Id={existing.get('Id')} for a different customer "
                        f"(existing CustomerRef={existing_customer_id}, "
                        f"requested={args.customer_id}). "
                        "Resolve by renaming the conflicting invoice's DocNumber "
                        "or using a different DocNumber for this invoice."
                    ),
                    "conflicting_invoice_id": existing.get('Id'),
                    "conflicting_customer_id": existing_customer_id,
                    "conflicting_doc_number": args.invoice_num,
                })
                sys.exit(1)

            output_json({
                "success": True,
                "action": "found_existing",
                "invoice_id": existing.get('Id'),
                "doc_number": args.invoice_num,
                "data": existing
            })
            sys.exit(0)

        # Build invoice object
        from quickbooks.objects.invoice import Invoice
        from quickbooks.objects.base import Ref, EmailAddress
        from quickbooks.objects.detailline import SalesItemLine, SalesItemLineDetail

        invoice = Invoice()

        # Set customer reference
        customer_ref = Ref()
        customer_ref.value = args.customer_id
        invoice.CustomerRef = customer_ref

        # Set dates and doc number
        invoice.TxnDate = args.txn_date
        invoice.DueDate = args.due_date
        invoice.DocNumber = args.invoice_num

        if args.bill_email:
            email = EmailAddress()
            email.Address = args.bill_email
            invoice.BillEmail = email

        if args.private_note:
            invoice.PrivateNote = args.private_note

        if args.terms_id:
            terms_ref = Ref()
            terms_ref.value = args.terms_id
            invoice.SalesTermRef = terms_ref

        if args.customer_memo:
            from quickbooks.objects.base import CustomerMemo
            memo = CustomerMemo()
            memo.value = args.customer_memo
            invoice.CustomerMemo = memo

        # Set custom fields
        if args.custom_fields:
            try:
                custom_fields = json.loads(args.custom_fields)
            except json.JSONDecodeError as e:
                output_json({
                    "success": False,
                    "error": "INVALID_JSON",
                    "message": f"Invalid custom_fields JSON: {str(e)}"
                })
                sys.exit(1)

            from quickbooks.objects.invoice import CustomField
            for cf in custom_fields:
                field = CustomField()
                field.DefinitionId = cf['DefinitionId']
                field.Name = cf.get('Name', '')
                field.Type = cf.get('Type', 'StringType')
                field.StringValue = cf.get('StringValue', '')
                invoice.CustomField.append(field)

        # Build line items
        for item in line_items:
            line = SalesItemLine()
            line.DetailType = "SalesItemLineDetail"
            line.Amount = item['amount']
            line.Description = item.get('description', '')

            detail = SalesItemLineDetail()
            detail.UnitPrice = item['amount']
            detail.Qty = item.get('qty', 1)

            # ItemRef is required for SalesItemLineDetail
            if item.get('item_id'):
                item_ref = Ref()
                item_ref.value = str(item['item_id'])
                detail.ItemRef = item_ref

            # Per-line class override or default class
            line_class_id = item.get('class_id', args.class_id)
            if line_class_id:
                class_ref = Ref()
                class_ref.value = str(line_class_id)
                detail.ClassRef = class_ref

            line.SalesItemLineDetail = detail
            invoice.Line.append(line)

        # Save to QBO
        def save_invoice(c):
            return invoice.save(qb=c)

        try:
            saved = retry_with_backoff(
                lambda: execute_with_auth_retry(save_invoice, client_holder)
            )

            saved_dict = entity_to_dict(saved)
            output_json({
                "success": True,
                "action": "created",
                "invoice_id": saved_dict.get('Id'),
                "doc_number": args.invoice_num,
                "total_amt": saved_dict.get('TotalAmt'),
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
