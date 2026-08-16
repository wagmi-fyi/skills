#!/usr/bin/env python3
"""
Shared Trade Account Utilities
Contains reusable functions for trade account operations.
Used by: amazon_aggregate_daily, apply_payment, list_open_items
"""

import json
import sqlite3
from typing import Dict, Optional


def compute_amount_due(conn: sqlite3.Connection, journal_entry_id: str, balance_account_code: str) -> int:
    """
    Compute amount due by summing the balance account (A/R or A/P) postings directly.

    For receivables: A/R postings are debits (positive net).
    For payables: A/P postings are credits (negative net, absolute value returned).

    Args:
        conn: Database connection
        journal_entry_id: The journal entry linked to the trade account
        balance_account_code: The A/R or A/P account code to sum

    Returns: Amount due in cents (always positive)
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT ABS(SUM(CASE WHEN direction = 'debit' THEN amount ELSE -amount END))
        FROM postings
        WHERE journal_entry_id = ?
          AND account_code = ?
        """,
        (journal_entry_id, balance_account_code)
    )
    row = cursor.fetchone()
    return row[0] if row and row[0] else 0


def compute_paid_amount(conn: sqlite3.Connection, trade_account_id: str) -> int:
    """
    Compute sum of payments applied to a trade account.

    Args:
        conn: Database connection
        trade_account_id: The trade account ID

    Returns: Total paid amount in cents
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT COALESCE(SUM(amount), 0)
        FROM trade_account_payments
        WHERE trade_account_id = ?
        """,
        (trade_account_id,)
    )
    row = cursor.fetchone()
    return row[0]


def compute_applied_amount(conn: sqlite3.Connection, source_ta_id: str) -> int:
    """
    Compute sum of credit applications sourced from a CM/VC trade account.

    For credit_memo and vendor_credit TAs, "remaining balance" is amount_due − applied_amount.
    Application TAPs are inserted with source_ta_id pointing to the CM/VC.

    Args:
        conn: Database connection
        source_ta_id: The CM/VC trade account ID

    Returns: Total applied amount in cents
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT COALESCE(SUM(amount), 0)
        FROM trade_account_payments
        WHERE source_ta_id = ?
        """,
        (source_ta_id,)
    )
    row = cursor.fetchone()
    return row[0]


def compute_consumed_amount(conn: sqlite3.Connection, trade_account_id: str) -> int:
    """
    Compute total consumption of a CM/VC trade account across BOTH legitimate forms:

    1. Credit applications — TAPs with source_ta_id = this CM/VC (applied against
       a target invoice/bill via apply_credit.py / --allow_mixed_credit).
    2. Direct settlement — TAPs with trade_account_id = this CM/VC and no
       source_ta_id (owner-cleared form: the credit settled through a clearing/
       owner account because funds moved outside business accounts, or a vendor
       refund landed as a bank deposit). The QBO publisher's owner-cleared phase
       publishes exactly this shape.

    Counting only one form makes the other read as a permanently open credit —
    a phantom subledger-to-GL gap that does not exist in the system of record.

    Returns: Total consumed amount in cents
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT COALESCE(SUM(amount), 0)
        FROM trade_account_payments
        WHERE source_ta_id = ?
           OR (trade_account_id = ? AND source_ta_id IS NULL)
        """,
        (trade_account_id, trade_account_id)
    )
    row = cursor.fetchone()
    return row[0]


def compute_status(amount_due: int, paid_amount: int) -> str:
    """
    Compute trade account status from amounts.

    Args:
        amount_due: Total amount due in cents
        paid_amount: Total paid amount in cents

    Returns: Status string ('unpaid', 'partial', or 'paid')
    """
    remaining = amount_due - paid_amount
    if remaining <= 0:
        return 'paid'
    elif paid_amount > 0:
        return 'partial'
    else:
        return 'unpaid'


def is_credit_memo(conn: sqlite3.Connection, journal_entry_id: str, balance_account_code: str, ta_type: str) -> bool:
    """
    Detect if a trade account is a credit memo or vendor credit.

    First-class types ('credit_memo', 'vendor_credit') return True immediately.
    Legacy detection: TAs stored as 'receivable'/'payable' but with reversed posting
    directions on the balance account are treated as credit memos for backward
    compatibility (e.g., Amazon refund TAs created before first-class types existed).

    Uses net (debit - credit) across ALL postings on the balance account, not just the
    first posting — TAs can have multiple postings on A/R or A/P.

    Args:
        conn: Database connection
        journal_entry_id: The journal entry linked to the trade account
        balance_account_code: The A/R or A/P account code
        ta_type: 'receivable', 'payable', 'credit_memo', or 'vendor_credit'

    Returns: True if the TA is a credit memo / vendor credit
    """
    # First-class types short-circuit.
    if ta_type in ('credit_memo', 'vendor_credit'):
        return True

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT SUM(CASE WHEN direction = 'debit' THEN amount ELSE -amount END)
        FROM postings
        WHERE journal_entry_id = ? AND account_code = ?
        """,
        (journal_entry_id, balance_account_code)
    )
    row = cursor.fetchone()
    if not row or row[0] is None:
        return False
    net = row[0]
    # Normal receivable: net > 0 (debit balance). Credit memo: net < 0.
    # Normal payable: net < 0 (credit balance). Debit memo: net > 0.
    if ta_type == 'receivable':
        return net < 0
    else:
        return net > 0


def get_balance_account_code(trade_account: Dict) -> Optional[str]:
    """
    Extract balance_account_code from trade account metadata.

    Args:
        trade_account: Dict containing trade account data with 'metadata' key

    Returns: Balance account code or None if not found
    """
    metadata = trade_account.get('metadata', {})
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    return metadata.get('balance_account_code')
