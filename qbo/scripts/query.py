#!/usr/bin/env python3
"""
QBO Query - Generic query script for QuickBooks Online entities.

Examples:
    # Get all accounts (up to 100)
    python query.py --entity=Account

    # Get invoices after a date
    python query.py --entity=Invoice --where="TxnDate > '2024-01-01'"

    # Get a specific journal entry by ID
    python query.py --entity=JournalEntry --id=123

    # Count customers
    python query.py --entity=Customer --count_only

    # Paginate through results
    python query.py --entity=Bill --max_results=50 --start_position=51
"""

import argparse
import json
import sys
import time
from typing import Any, Dict, List, Optional

# Import shared client module
import qbo_client


def parse_arguments():
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description='Query QuickBooks Online entities',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        '--entity',
        required=True,
        help='Entity type to query (e.g., Account, Invoice, JournalEntry)'
    )
    parser.add_argument(
        '--id',
        help='Fetch single record by ID'
    )
    parser.add_argument(
        '--where',
        help='WHERE clause for filtering (e.g., "TxnDate > \'2024-01-01\'")'
    )
    parser.add_argument(
        '--max_results',
        type=int,
        default=100,
        help='Maximum results to return (default: 100, max: 1000)'
    )
    parser.add_argument(
        '--start_position',
        type=int,
        default=1,
        help='Pagination offset (default: 1)'
    )
    parser.add_argument(
        '--count_only',
        action='store_true',
        help='Return only the count, not records'
    )
    return parser.parse_args()


class ClientHolder:
    """Mutable holder for client to allow refresh during retries."""
    def __init__(self, client):
        self.client = client


def execute_with_auth_retry(func, client_holder: ClientHolder):
    """
    Execute function with automatic token refresh on auth errors.

    On 401/auth error: refresh tokens, update client, retry once.

    Args:
        func: Function that takes client as argument.
        client_holder: ClientHolder wrapping the QBO client.

    Returns:
        Function result.

    Raises:
        Exception if retry also fails or refresh fails.
    """
    try:
        return func(client_holder.client)
    except Exception as e:
        if not qbo_client.is_auth_error(e):
            raise

        # Auth error - try to refresh
        new_client, refresh_error = qbo_client.refresh_client(client_holder.client)
        if refresh_error:
            # Refresh failed - raise with refresh error info
            raise Exception(f"Auth failed and refresh failed: {refresh_error['message']}") from e

        # Update client and retry once
        client_holder.client = new_client
        return func(client_holder.client)


def retry_with_backoff(func, max_retries: int = 3, base_delay: float = 1.0):
    """
    Execute function with exponential backoff retry on transient errors.

    Args:
        func: Function to execute.
        max_retries: Maximum retry attempts.
        base_delay: Initial delay in seconds.

    Returns:
        Function result.

    Raises:
        Last exception if all retries fail.
    """
    last_exception = None

    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as e:
            last_exception = e
            error_str = str(e).lower()

            # Check if retryable
            is_rate_limit = '429' in error_str or 'rate' in error_str or 'throttl' in error_str
            is_transient = '500' in error_str or '502' in error_str or '503' in error_str or 'timeout' in error_str

            if not (is_rate_limit or is_transient) or attempt >= max_retries:
                raise

            # Exponential backoff
            delay = base_delay * (2 ** attempt)
            time.sleep(delay)

    raise last_exception


def build_query(entity_name: str, where: Optional[str], start_position: int, max_results: int) -> str:
    """
    Build QBO query string.

    Args:
        entity_name: Entity type name.
        where: Optional WHERE clause.
        start_position: Pagination offset.
        max_results: Max results to fetch.

    Returns:
        Query string for Entity.query().
    """
    query = f"SELECT * FROM {entity_name}"

    if where:
        query += f" WHERE {where}"

    query += f" STARTPOSITION {start_position} MAXRESULTS {max_results}"

    return query


def entity_to_dict(entity) -> Dict[str, Any]:
    """
    Convert QBO entity object to dictionary.

    Args:
        entity: QBO SDK entity object.

    Returns:
        Dictionary representation.
    """
    result = {}

    # Get all attributes that don't start with underscore
    for attr in dir(entity):
        if attr.startswith('_'):
            continue
        if callable(getattr(entity, attr)):
            continue

        try:
            value = getattr(entity, attr)

            # Handle nested objects
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


def main():
    try:
        args = parse_arguments()

        # Validate max_results
        if args.max_results > 1000:
            args.max_results = 1000
        if args.max_results < 1:
            args.max_results = 100

        # Validate start_position (1-based)
        if args.start_position < 1:
            args.start_position = 1

        # Get entity class
        entity_class, entity_error = qbo_client.get_entity_class(args.entity)
        if entity_error:
            output_json({
                "success": False,
                "error": "INVALID_ENTITY",
                "message": entity_error,
                "valid_entities": qbo_client.list_entity_names()
            })
            sys.exit(1)

        # Create client (no preemptive token refresh - uses lazy refresh on 401)
        client, client_error = qbo_client.create_client()
        if client_error:
            output_json(client_error)
            sys.exit(1)

        # Wrap client in holder for refresh capability
        client_holder = ClientHolder(client)

        # Determine query type and execute
        if args.id:
            # Fetch single record by ID
            def fetch_by_id(c):
                return entity_class.get(args.id, qb=c)

            try:
                record = retry_with_backoff(
                    lambda: execute_with_auth_retry(fetch_by_id, client_holder)
                )

                if record is None:
                    output_json({
                        "success": False,
                        "error": "NOT_FOUND",
                        "message": f"No {args.entity} found with ID {args.id}"
                    })
                    sys.exit(1)

                output_json({
                    "success": True,
                    "entity": args.entity,
                    "count": 1,
                    "data": entity_to_dict(record)
                })

            except Exception as e:
                output_json({
                    "success": False,
                    "error": "API_ERROR",
                    "message": str(e),
                    "retry_attempted": True
                })
                sys.exit(1)

        elif args.count_only:
            # Count only
            def count_entities(c):
                if args.where:
                    # For filtered count, run query and count results
                    # Note: May be truncated at 1000 for large result sets
                    query = f"SELECT * FROM {args.entity} WHERE {args.where} MAXRESULTS 1000"
                    result = entity_class.query(query, qb=c)
                    return len(result) if result else 0
                else:
                    # Unfiltered count uses SDK's count() method
                    return entity_class.count(qb=c)

            try:
                count = retry_with_backoff(
                    lambda: execute_with_auth_retry(count_entities, client_holder)
                )
                output_json({
                    "success": True,
                    "entity": args.entity,
                    "count": count,
                    "count_note": "Filtered counts may be truncated at 1000" if args.where else None,
                    "query": f"SELECT * FROM {args.entity}" + (f" WHERE {args.where}" if args.where else "") + " (count)"
                })

            except Exception as e:
                output_json({
                    "success": False,
                    "error": "API_ERROR",
                    "message": str(e),
                    "retry_attempted": True
                })
                sys.exit(1)

        else:
            # Query with optional filters
            query = build_query(args.entity, args.where, args.start_position, args.max_results)

            def execute_query(c):
                return entity_class.query(query, qb=c)

            def get_total_count(c):
                if args.where:
                    # For filtered count, run query and count results
                    # Note: May be truncated at 1000 for large result sets
                    count_query = f"SELECT * FROM {args.entity} WHERE {args.where} MAXRESULTS 1000"
                    result = entity_class.query(count_query, qb=c)
                    return len(result) if result else 0
                else:
                    # Unfiltered count uses SDK's count() method
                    return entity_class.count(qb=c)

            try:
                records = retry_with_backoff(
                    lambda: execute_with_auth_retry(execute_query, client_holder)
                )
                records = records or []

                # Get total count to determine truncation
                total_count = retry_with_backoff(
                    lambda: execute_with_auth_retry(get_total_count, client_holder)
                )

                result_count = len(records)
                truncated = result_count < total_count and result_count >= args.max_results

                output_json({
                    "success": True,
                    "entity": args.entity,
                    "count": result_count,
                    "total_count": total_count,
                    "truncated": truncated,
                    "query": query,
                    "data": [entity_to_dict(r) for r in records]
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
