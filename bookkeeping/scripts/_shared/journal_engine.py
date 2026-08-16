#!/usr/bin/env python3
"""
Shared Journal Entry Creation Module
Contains reusable functions for creating double-entry journal entries.
Used by: bulk_cat_transactions, apply_cat_rules, amazon_aggregate_daily, shopify_reconcile_payouts, test_cat_rule, apply_transfer
"""

import json
import sqlite3
import uuid
from typing import List, Dict, Tuple, Optional


JOURNAL_MEMO = None  # or "System-generated categorization journal"


def parse_source(source: str) -> str:
    """
    Extract account code from source field.
    Expected format: "1001 - Checking Account"
    Returns: "1001"
    """
    if " - " not in source:
        raise ValueError(f"Invalid source format: {source}. Expected format: 'CODE - NAME'")

    parts = source.split(" - ", 1)
    return parts[0].strip()


def get_import_data(conn: sqlite3.Connection, import_id: str) -> Dict:
    """
    Fetch import record with validation.
    Returns dict with: id, source, banking_date, amount, balance_type, bank_account_code
    Raises ValueError if import doesn't exist, is already processed, or has invalid data.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, source, banking_date, amount, raw_data, processed
        FROM imports
        WHERE id = ?
        """,
        (import_id,)
    )
    row = cursor.fetchone()

    if not row:
        raise ValueError(f"Import ID {import_id} not found")

    import_id, source, banking_date, amount, raw_data_json, processed = row

    if processed:
        raise ValueError(f"Import ID {import_id} already processed")

    # Parse raw_data JSON to get balance_type
    try:
        raw_data = json.loads(raw_data_json)
    except json.JSONDecodeError:
        raise ValueError(f"Invalid raw_data JSON for import {import_id}")

    balance_type = raw_data.get("Balance Type")
    if not balance_type:
        raise ValueError(f"Missing 'Balance Type' in raw_data for import {import_id}")

    if balance_type not in ["cash", "credit"]:
        raise ValueError(
            f"Invalid balance_type '{balance_type}' for import {import_id}. "
            f"Must be 'cash' or 'credit'"
        )

    # Parse source to get bank account code
    try:
        bank_account_code = parse_source(source)
    except ValueError as e:
        raise ValueError(f"Import {import_id}: {str(e)}")

    return {
        "id": import_id,
        "source": source,
        "banking_date": banking_date,
        "amount": amount,
        "balance_type": balance_type,
        "bank_account_code": bank_account_code,
    }


def validate_account_codes(conn: sqlite3.Connection, codes: List[str]) -> List[str]:
    """
    Validate account codes exist in chart_of_accounts.
    Returns list of invalid codes (empty list if all valid).
    """
    if not codes:
        return []

    cursor = conn.cursor()
    placeholders = ",".join("?" * len(codes))
    cursor.execute(
        f"SELECT code FROM chart_of_accounts WHERE code IN ({placeholders})",
        codes
    )
    valid_codes = {row[0] for row in cursor.fetchall()}

    invalid_codes = [code for code in codes if code not in valid_codes]
    return invalid_codes


def get_account_types(conn: sqlite3.Connection, codes: List[str]) -> Dict[str, str]:
    """
    Return {account_code: type} for the given codes (e.g. 'income', 'expense',
    'asset', 'liability', 'equity'). Codes not found are omitted.
    Used to route per-posting class onto P&L (income/expense) postings only.
    """
    if not codes:
        return {}

    cursor = conn.cursor()
    placeholders = ",".join("?" * len(codes))
    cursor.execute(
        f"SELECT code, type FROM chart_of_accounts WHERE code IN ({placeholders})",
        codes
    )
    return {row[0]: row[1] for row in cursor.fetchall()}


def validate_tags(conn: sqlite3.Connection, tags: List[str]) -> List[str]:
    """
    Validate tags exist in tags table.
    Returns list of invalid tags (empty list if all valid).
    """
    if not tags:
        return []

    cursor = conn.cursor()
    placeholders = ",".join("?" * len(tags))
    cursor.execute(
        f"SELECT name FROM tags WHERE name IN ({placeholders})",
        tags
    )
    valid_tags = {row[0] for row in cursor.fetchall()}

    invalid_tags = [tag for tag in tags if tag not in valid_tags]
    return invalid_tags


def validate_class(conn: sqlite3.Connection, class_name: str) -> Optional[str]:
    """
    Validate class exists in tags table with category='Class'.
    Returns error message if invalid, None if valid.
    """
    if not class_name:
        return None  # Class is optional

    cursor = conn.cursor()
    cursor.execute(
        "SELECT name FROM tags WHERE name = ? AND category = 'Class'",
        (class_name,)
    )
    if not cursor.fetchone():
        return f"Invalid class '{class_name}' - not found in tags with category='Class'"
    return None


def auto_create_contact(conn: sqlite3.Connection, contact_name: str):
    """Create contact if doesn't exist."""
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO contacts (name, remote_id, meta) VALUES (?, NULL, '{}')",
        (contact_name,)
    )


def validate_and_calculate_split_amounts(postings: List[Dict], import_amount: int) -> Tuple[Optional[str], List[int]]:
    """
    Validate posting amounts for split transactions using Xero-style logic.

    Processing order (Xero model):
    1. Apply fixed amounts first
    2. Calculate percentages on remainder
    3. Percentages must sum to 100%

    Returns: (error_message: Optional[str], calculated_amounts: List[int])
    - If error_message is not None, validation failed
    - If error_message is None, calculated_amounts contains final amounts in cents
    """
    # Handle simple case: single posting without amount or percentage
    if len(postings) == 1 and "amount" not in postings[0] and "percentage" not in postings[0]:
        return (None, [abs(import_amount)])

    # Separate fixed and percentage postings
    fixed_postings = [p for p in postings if "amount" in p]
    percentage_postings = [p for p in postings if "percentage" in p]

    # Validate that each posting has either amount OR percentage (not both, not neither)
    for i, posting in enumerate(postings):
        has_amount = "amount" in posting
        has_percentage = "percentage" in posting

        if not has_amount and not has_percentage:
            return (f"Posting {i+1} must have either 'amount' or 'percentage' field", [])
        if has_amount and has_percentage:
            return (f"Posting {i+1} cannot have both 'amount' and 'percentage' fields", [])

    # Calculate total fixed amount
    total_fixed = sum(p["amount"] for p in fixed_postings)

    # Validate fixed amounts don't exceed transaction amount
    if total_fixed > abs(import_amount):
        return (
            f"Fixed amounts ({total_fixed} cents) exceed transaction amount ({abs(import_amount)} cents)",
            []
        )

    # Calculate remainder after fixed amounts
    remainder = abs(import_amount) - total_fixed

    # Validate percentages sum to 100% (if any percentage postings exist)
    if percentage_postings:
        total_pct = sum(p["percentage"] for p in percentage_postings)
        # Allow small rounding tolerance (0.9999 to 1.0001)
        if not (0.9999 <= total_pct <= 1.0001):
            return (
                f"Percentages must sum to 100%, got {total_pct * 100:.2f}%",
                []
            )

    # Calculate final amounts for all postings
    calculated = []
    for posting in postings:
        if "amount" in posting:
            # Fixed amount
            calculated.append(posting["amount"])
        elif "percentage" in posting:
            # Percentage of remainder
            amount = int(remainder * posting["percentage"])
            calculated.append(amount)

    # Handle rounding: last posting absorbs any cent differences
    total_calculated = sum(calculated)
    if total_calculated != abs(import_amount):
        diff = abs(import_amount) - total_calculated
        calculated[-1] += diff  # Adjust last posting by ±N cents

    return (None, calculated)


def determine_direction(
    balance_type: str,
    amount: int,
    is_bank_account: bool
) -> str:
    """
    Determine debit or credit based on balance type and amount sign.

    Cash accounts:
      - Negative amount (withdrawal): bank=credit, category=debit
      - Positive amount (deposit): bank=debit, category=credit

    Credit accounts:
      - Negative amount (charge): card=debit, category=credit
      - Positive amount (payment): card=credit, category=debit
    """
    if balance_type == "cash":
        if amount < 0:
            # Withdrawal: money leaving bank
            return "credit" if is_bank_account else "debit"
        else:
            # Deposit: money entering bank
            return "debit" if is_bank_account else "credit"

    elif balance_type == "credit":
        if amount < 0:
            # Charge: liability increasing
            return "debit" if is_bank_account else "credit"
        else:
            # Payment: liability decreasing
            return "credit" if is_bank_account else "debit"

    else:
        raise ValueError(f"Invalid balance_type: {balance_type}")


def create_journal_entry(
    conn: sqlite3.Connection,
    categorization: Dict,
    import_data: Dict,
    confidence_score: Optional[int] = None,
    metadata: Optional[Dict] = None,
    class_name: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Create journal entry with postings.

    Args:
        conn: Database connection
        categorization: Dict with import_id and postings array
        import_data: Dict from get_import_data()
        confidence_score: AI confidence (1-10), use for AI categorizations
        metadata: Rule metadata dict, use for rule categorizations
        class_name: QBO Class name (must exist in tags with category='Class')

    Note: Only one of confidence_score or metadata should be set

    Returns: (success: bool, error_message: str or journal_entry_id: str)
    """
    try:
        # Extract categorization data
        import_id = categorization["import_id"]
        postings_input = categorization["postings"]

        # Use confidence_score from categorization if not explicitly provided
        if confidence_score is None and "confidence_score" in categorization:
            confidence_score = categorization["confidence_score"]

        # Validate we have postings
        if not postings_input or len(postings_input) == 0:
            return (False, "Empty postings array")

        # Collect all account codes (category accounts + bank account)
        category_account_codes = [p["account_code"] for p in postings_input]
        all_account_codes = category_account_codes + [import_data["bank_account_code"]]

        # Validate all account codes exist
        invalid_accounts = validate_account_codes(conn, all_account_codes)
        if invalid_accounts:
            return (False, f"Invalid account code(s): {', '.join(invalid_accounts)}")

        # Collect and validate all tags
        all_tags = []
        for posting in postings_input:
            tags = posting.get("tags", [])
            if tags:
                all_tags.extend(tags)

        if all_tags:
            invalid_tags = validate_tags(conn, all_tags)
            if invalid_tags:
                return (False, f"Invalid tag(s): {', '.join(invalid_tags)}")

        # Validate class if provided
        if class_name:
            class_error = validate_class(conn, class_name)
            if class_error:
                return (False, class_error)

        # Validate any per-posting class names (mixed-class categorizations)
        for cls in {p.get("class_name") for p in postings_input if p.get("class_name")}:
            class_error = validate_class(conn, cls)
            if class_error:
                return (False, class_error)

        # Auto-create contacts (skip None for multi-contact JEs)
        for posting in postings_input:
            if posting["contact"] is not None:
                auto_create_contact(conn, posting["contact"])

        # Explicit direction mode: when postings include 'direction', callers
        # control debit/credit per posting (e.g., net-of-fee deposits like Stripe payouts).
        # Otherwise, direction is auto-calculated from the import's balance type.
        explicit_mode = any("direction" in p for p in postings_input)

        if explicit_mode:
            # Validate all postings have direction + amount
            for i, p in enumerate(postings_input):
                if "direction" not in p:
                    return (False, f"Posting {i+1} missing 'direction' (required when any posting has explicit direction)")
                if "amount" not in p:
                    return (False, f"Posting {i+1} missing 'amount' (required in explicit direction mode)")
                if p["direction"] not in ("debit", "credit"):
                    return (False, f"Posting {i+1} direction must be 'debit' or 'credit', got '{p['direction']}'")
        else:
            # Standard mode: validate split amounts (handles percentages)
            split_error, calculated_amounts = validate_and_calculate_split_amounts(
                postings_input,
                import_data["amount"]
            )
            if split_error:
                return (False, split_error)

        # Generate journal entry ID
        journal_entry_id = str(uuid.uuid4())

        # Python sqlite3 handles transactions automatically - no explicit BEGIN needed
        try:
            # Build journal entry metadata
            je_metadata = {'class_name': class_name} if class_name else None
            je_metadata_json = json.dumps(je_metadata) if je_metadata else None

            # Insert journal entry
            conn.execute(
                """
                INSERT INTO journal_entries (
                    id, import_id, transaction_date, memo,
                    sync, metadata
                )
                VALUES (?, ?, ?, ?, '{"status":"pending"}', ?)
                """,
                (journal_entry_id, import_id, import_data["banking_date"], JOURNAL_MEMO, je_metadata_json)
            )

            # Build postings list (bank account offset + category postings)
            postings_to_insert = []

            # Create bank account offset posting
            bank_direction = determine_direction(
                import_data["balance_type"],
                import_data["amount"],
                is_bank_account=True
            )

            postings_to_insert.append({
                "id": str(uuid.uuid4()),
                "journal_entry_id": journal_entry_id,
                "account_code": import_data["bank_account_code"],
                "direction": bank_direction,
                "amount": abs(import_data["amount"]),
                "contact": postings_input[0]["contact"],  # Use first contact for bank posting
                "description": postings_input[0].get("description"),
                "tags": json.dumps([]),  # Bank posting has no tags
                "confidence_score": confidence_score,
                "metadata": json.dumps(metadata) if metadata else None,
            })

            # Per-posting class routing: fold class into each P&L posting's metadata.
            # An explicit per-posting class wins (mixed-class categorizations); otherwise
            # the categorization/JE-level class is applied to income/expense postings only
            # (balance-sheet lines stay class-less). The bank-offset posting above is left
            # untouched. Never mutate the shared `metadata` dict.
            acct_types = get_account_types(conn, category_account_codes)

            def _posting_metadata(posting):
                eff_class = posting.get("class_name")
                if (not eff_class and class_name
                        and acct_types.get(posting["account_code"]) in ("income", "expense")):
                    eff_class = class_name
                if not eff_class:
                    return json.dumps(metadata) if metadata else None
                m = dict(metadata) if metadata else {}
                m["class_name"] = eff_class
                return json.dumps(m)

            if explicit_mode:
                # Explicit mode: use caller-specified direction and amount per posting
                for posting in postings_input:
                    postings_to_insert.append({
                        "id": str(uuid.uuid4()),
                        "journal_entry_id": journal_entry_id,
                        "account_code": posting["account_code"],
                        "direction": posting["direction"],
                        "amount": posting["amount"],
                        "contact": posting["contact"],
                        "description": posting.get("description"),
                        "tags": json.dumps(posting.get("tags", [])),
                        "confidence_score": confidence_score,
                        "metadata": _posting_metadata(posting),
                    })
            else:
                # Standard mode: single auto-calculated direction for all category postings
                category_direction = determine_direction(
                    import_data["balance_type"],
                    import_data["amount"],
                    is_bank_account=False
                )

                for posting, posting_amount in zip(postings_input, calculated_amounts):
                    postings_to_insert.append({
                        "id": str(uuid.uuid4()),
                        "journal_entry_id": journal_entry_id,
                        "account_code": posting["account_code"],
                        "direction": category_direction,
                        "amount": abs(posting_amount),
                        "contact": posting["contact"],
                        "description": posting.get("description"),
                        "tags": json.dumps(posting.get("tags", [])),
                        "confidence_score": confidence_score,
                        "metadata": _posting_metadata(posting),
                    })

            # Validate balance (sum debits = sum credits)
            total_debits = sum(
                p["amount"] for p in postings_to_insert if p["direction"] == "debit"
            )
            total_credits = sum(
                p["amount"] for p in postings_to_insert if p["direction"] == "credit"
            )

            if total_debits != total_credits:
                return (
                    False,
                    f"Journal entry does not balance: debits={total_debits}, credits={total_credits}"
                )

            # Insert all postings
            for posting in postings_to_insert:
                conn.execute(
                    """
                    INSERT INTO postings (
                        id, journal_entry_id, account_code, direction,
                        amount, contact, description, tags,
                        confidence_score, metadata
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        posting["id"],
                        posting["journal_entry_id"],
                        posting["account_code"],
                        posting["direction"],
                        posting["amount"],
                        posting["contact"],
                        posting["description"],
                        posting["tags"],
                        posting["confidence_score"],
                        posting["metadata"],
                    )
                )

            # Mark import as processed
            conn.execute(
                "UPDATE imports SET processed = 1 WHERE id = ?",
                (import_id,)
            )

            # Commit transaction
            conn.commit()

            return (True, journal_entry_id)

        except Exception as e:
            conn.rollback()
            return (False, f"Database error: {str(e)}")

    except KeyError as e:
        return (False, f"Missing required field: {str(e)}")
    except Exception as e:
        return (False, f"Unexpected error: {str(e)}")


def create_journal_entry_direct(
    conn: sqlite3.Connection,
    transaction_date: str,
    memo: str,
    postings_data: List[Dict],
    je_metadata: Optional[Dict] = None
) -> str:
    """
    Create journal entry with pre-built postings directly.

    Used by trade account scripts (Amazon, Shopify) that build their own
    postings with direction already determined.

    Args:
        conn: Database connection
        transaction_date: Date for the journal entry (YYYY-MM-DD)
        memo: Journal entry memo/description
        postings_data: List of dicts with keys:
            - account_code: str
            - direction: 'debit' or 'credit'
            - amount: int (cents)
            - contact: str
            - description: str
            - class_name: str (optional, stored in postings.metadata)
        je_metadata: Optional dict (e.g., {'class_name': 'Amazon - 3P Sales'})

    Returns: journal_entry_id (UUID string)
    Raises: ValueError if debits != credits or class validation fails

    Note: Caller is responsible for transaction management (commit/rollback).
    """
    # Validate class if provided in je_metadata
    if je_metadata and je_metadata.get('class_name'):
        class_error = validate_class(conn, je_metadata['class_name'])
        if class_error:
            raise ValueError(class_error)

    # Validate per-posting class names (deduplicate, batch validate)
    posting_classes = set(
        p['class_name'] for p in postings_data if p.get('class_name')
    )
    for cls in posting_classes:
        class_error = validate_class(conn, cls)
        if class_error:
            raise ValueError(class_error)

    # Validate account codes exist in chart_of_accounts
    codes = [p['account_code'] for p in postings_data]
    invalid = validate_account_codes(conn, codes)
    if invalid:
        raise ValueError(f"Invalid account code(s): {', '.join(invalid)}")

    # Auto-create contacts if they don't exist (skip None for multi-contact JEs)
    for p in postings_data:
        if p.get('contact') is not None:
            auto_create_contact(conn, p['contact'])

    journal_entry_id = str(uuid.uuid4())

    # Insert journal entry (no import_id - not from bank import flow)
    conn.execute(
        """
        INSERT INTO journal_entries (
            id, import_id, transaction_date, memo,
            sync, metadata
        )
        VALUES (?, NULL, ?, ?, '{"status":"pending"}', ?)
        """,
        (journal_entry_id, transaction_date, memo,
         json.dumps(je_metadata) if je_metadata else None)
    )

    # Validate balance
    total_debits = sum(p['amount'] for p in postings_data if p['direction'] == 'debit')
    total_credits = sum(p['amount'] for p in postings_data if p['direction'] == 'credit')

    if total_debits != total_credits:
        raise ValueError(
            f"Journal entry does not balance: debits={total_debits}, credits={total_credits}"
        )

    # Insert postings
    for posting in postings_data:
        # Build per-posting metadata from class_name if present
        posting_metadata = None
        if posting.get('class_name'):
            posting_metadata = json.dumps({'class_name': posting['class_name']})

        # Validate and serialize fx data if present
        fx_json = None
        if posting.get('fx') is not None:
            fx = posting['fx']
            # Validate required fields
            if not isinstance(fx, dict):
                raise ValueError(f"fx must be a dict, got {type(fx).__name__}")
            for req_field in ('amount', 'currency', 'rate'):
                if req_field not in fx:
                    raise ValueError(f"fx missing required field '{req_field}': {fx}")
            if not isinstance(fx['amount'], int):
                raise ValueError(f"fx.amount must be int, got {type(fx['amount']).__name__}: {fx['amount']}")
            if not isinstance(fx['currency'], str) or len(fx['currency']) != 3:
                raise ValueError(f"fx.currency must be 3-char string, got: {fx['currency']!r}")
            if not isinstance(fx['rate'], (int, float)) or fx['rate'] <= 0:
                raise ValueError(f"fx.rate must be a number > 0, got: {fx['rate']!r}")
            fx_json = json.dumps(fx)

        conn.execute(
            """
            INSERT INTO postings (
                id, journal_entry_id, account_code, direction,
                amount, contact, description, tags,
                confidence_score, metadata, fx
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, '[]', NULL, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                journal_entry_id,
                posting['account_code'],
                posting['direction'],
                posting['amount'],
                posting['contact'],
                posting['description'],
                posting_metadata,
                fx_json,
            )
        )

    return journal_entry_id


def create_journal_entry_transfer(
    conn: sqlite3.Connection,
    import_id: str,
    transaction_date: str,
    memo: str,
    postings_data: List[Dict],
    all_import_ids: List[str],
    class_name: Optional[str] = None,
    je_metadata: Optional[Dict] = None
) -> str:
    """
    Create journal entry for inter-account transfers with multi-import linking.

    Used by apply_transfer.py for transfers where the same payment appears
    as separate imports in multiple feeds (e.g., bank + credit card).

    Args:
        conn: Database connection
        import_id: Primary import ID (set as JE's import_id FK)
        transaction_date: Date for the journal entry (YYYY-MM-DD)
        memo: Journal entry memo/description
        postings_data: List of dicts with keys:
            - account_code: str (required)
            - direction: 'debit' or 'credit' (required)
            - amount: int in cents (required)
            - contact: str or None (optional)
            - description: str (optional)
        all_import_ids: All import IDs in the transfer group (primary + secondary)
        class_name: Optional QBO Class name (must exist in tags with category='Class')
        je_metadata: Optional additional metadata dict

    Returns: journal_entry_id (UUID string)
    Raises: ValueError if validation fails (balance, accounts, class)

    Note: Caller is responsible for transaction management (commit/rollback)
          and marking imports as processed.
    """
    # Validate class if provided
    if class_name:
        class_error = validate_class(conn, class_name)
        if class_error:
            raise ValueError(class_error)

    # Build metadata: merge transfer_group + class_name + any extra metadata
    metadata = je_metadata.copy() if je_metadata else {}
    metadata['transfer_group'] = all_import_ids
    if class_name:
        metadata['class_name'] = class_name

    if not postings_data:
        raise ValueError("Empty postings array")

    # Validate account codes exist in chart_of_accounts
    codes = [p['account_code'] for p in postings_data]
    invalid = validate_account_codes(conn, codes)
    if invalid:
        raise ValueError(f"Invalid account code(s): {', '.join(invalid)}")

    # Auto-create contacts if they don't exist (skip None for multi-contact JEs)
    for p in postings_data:
        if p.get('contact') is not None:
            auto_create_contact(conn, p['contact'])

    # Validate balance
    total_debits = sum(p['amount'] for p in postings_data if p['direction'] == 'debit')
    total_credits = sum(p['amount'] for p in postings_data if p['direction'] == 'credit')

    if total_debits != total_credits:
        raise ValueError(
            f"Journal entry does not balance: debits={total_debits}, credits={total_credits}"
        )

    journal_entry_id = str(uuid.uuid4())

    # Insert journal entry with import_id set (unlike create_journal_entry_direct)
    conn.execute(
        """
        INSERT INTO journal_entries (
            id, import_id, transaction_date, memo,
            sync, metadata
        )
        VALUES (?, ?, ?, ?, '{"status":"pending"}', ?)
        """,
        (journal_entry_id, import_id, transaction_date, memo,
         json.dumps(metadata))
    )

    # Insert postings
    for posting in postings_data:
        conn.execute(
            """
            INSERT INTO postings (
                id, journal_entry_id, account_code, direction,
                amount, contact, description, tags,
                confidence_score, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, '[]', NULL, NULL)
            """,
            (
                str(uuid.uuid4()),
                journal_entry_id,
                posting['account_code'],
                posting['direction'],
                posting['amount'],
                posting.get('contact'),
                posting.get('description'),
            )
        )

    return journal_entry_id
