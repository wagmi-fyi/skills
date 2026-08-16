#!/usr/bin/env python3
"""
QBO Client - Shared authentication and entity mapping module for QuickBooks Online.
"""

import fcntl
import json
import os
import sys
import time
from typing import Dict, Optional, Tuple, Type

from dotenv import load_dotenv


def _find_env_file() -> str:
    """Locate QBO credentials .env file.

    Resolution order:
    1. QBO_ENV_PATH env var (explicit override)
    2. BOOKKEEPING_CONFIG_PATH → {local_dir}/adapters/.env (shared with bookkeeping)
    3. {cwd}/.claude/skills/qbo/.env (legacy standalone)
    """
    # 1. Explicit override
    explicit = os.environ.get('QBO_ENV_PATH')
    if explicit and os.path.exists(explicit):
        return explicit

    # 2. Derive from bookkeeping config
    config_path = os.environ.get('BOOKKEEPING_CONFIG_PATH')
    if config_path:
        local_dir = os.path.dirname(os.path.abspath(config_path))
        bk_env = os.path.join(local_dir, 'adapters', '.env')
        if os.path.exists(bk_env):
            return bk_env

    # 3. Legacy fallback
    project_env = os.path.join(os.getcwd(), '.claude', 'skills', 'qbo', '.env')
    if os.path.exists(project_env):
        return project_env

    print(json.dumps({
        "success": False,
        "error": "QBO_NOT_CONFIGURED",
        "message": (
            "QBO credentials not found. Set BOOKKEEPING_CONFIG_PATH or "
            f"create {os.path.join(os.getcwd(), '.claude', 'skills', 'qbo', '.env')} "
            "with your OAuth credentials."
        )
    }))
    sys.exit(1)


# Resolve and load project-level credentials
_env_file = _find_env_file()
load_dotenv(_env_file)
print(f"QBO: loaded credentials from {_env_file}", file=sys.stderr)

# QBO SDK imports
try:
    from intuitlib.client import AuthClient
    from intuitlib.exceptions import AuthClientError
    from quickbooks import QuickBooks
    from quickbooks.objects import (
        Account, Attachable, Bill, BillPayment, Budget, CompanyInfo,
        CreditCardPayment, CreditMemo, Customer, Department, Deposit,
        Employee, Estimate, Invoice, Item, JournalEntry, Payment,
        PaymentMethod, Preferences, Purchase, PurchaseOrder,
        RefundReceipt, SalesReceipt, TaxAgency, TaxCode, TaxRate,
        TaxService, Term, TimeActivity, Transfer, Vendor, VendorCredit
    )
    from quickbooks.objects.trackingclass import Class
    QBO_IMPORTS_AVAILABLE = True
except ImportError:
    QBO_IMPORTS_AVAILABLE = False
    AuthClientError = Exception  # Fallback for type hints


# Entity name mapping (case-insensitive lookup -> SDK class)
ENTITY_MAP: Dict[str, Type] = {}
if QBO_IMPORTS_AVAILABLE:
    ENTITY_MAP = {
        'account': Account,
        'attachable': Attachable,
        'bill': Bill,
        'billpayment': BillPayment,
        'budget': Budget,
        'companyinfo': CompanyInfo,
        'creditcardpayment': CreditCardPayment,
        'creditmemo': CreditMemo,
        'customer': Customer,
        'department': Department,
        'deposit': Deposit,
        'employee': Employee,
        'estimate': Estimate,
        'invoice': Invoice,
        'item': Item,
        'journalentry': JournalEntry,
        'payment': Payment,
        'paymentmethod': PaymentMethod,
        'preferences': Preferences,
        'purchase': Purchase,
        'purchaseorder': PurchaseOrder,
        'refundreceipt': RefundReceipt,
        'salesreceipt': SalesReceipt,
        'taxagency': TaxAgency,
        'taxcode': TaxCode,
        'taxrate': TaxRate,
        'taxservice': TaxService,
        'term': Term,
        'timeactivity': TimeActivity,
        'class': Class,
        'trackingclass': Class,  # Alias
        'transfer': Transfer,
        'vendor': Vendor,
        'vendorcredit': VendorCredit,
    }


def validate_env_vars() -> Dict[str, str]:
    """
    Validate required environment variables are set.

    Returns:
        Dict with credential values.

    Raises:
        ValueError: If required vars are missing.
    """
    required_vars = [
        'QBO_CLIENT_ID',
        'QBO_CLIENT_SECRET',
        'QBO_ACCESS_TOKEN',
        'QBO_REFRESH_TOKEN',
        'QBO_REALM_ID'
    ]

    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

    return {
        'client_id': os.getenv('QBO_CLIENT_ID'),
        'client_secret': os.getenv('QBO_CLIENT_SECRET'),
        'access_token': os.getenv('QBO_ACCESS_TOKEN'),
        'refresh_token': os.getenv('QBO_REFRESH_TOKEN'),
        'realm_id': os.getenv('QBO_REALM_ID'),
        'environment': os.getenv('QBO_ENVIRONMENT', 'production')
    }


def save_refreshed_tokens(access_token: str, refresh_token: str) -> bool:
    """
    Persist refreshed OAuth tokens back to the project-level .env file.

    Args:
        access_token: New access token.
        refresh_token: New refresh token.

    Returns:
        True if tokens were saved successfully, False otherwise.
    """
    try:
        with open(_env_file, 'r') as f:
            lines = f.readlines()

        # Guard: never overwrite a stored token with a blank/empty value.
        # create_client() builds AuthClient without a refresh_token attribute, so a
        # belt-and-suspenders save_tokens_if_available() call would otherwise wipe it.
        if not refresh_token:
            print("WARNING: refusing to write blank QBO_REFRESH_TOKEN; preserving existing value", file=sys.stderr)
        new_lines = []
        for line in lines:
            if line.startswith('QBO_ACCESS_TOKEN=') and access_token:
                new_lines.append(f'QBO_ACCESS_TOKEN={access_token}\n')
            elif line.startswith('QBO_REFRESH_TOKEN=') and refresh_token:
                new_lines.append(f'QBO_REFRESH_TOKEN={refresh_token}\n')
            else:
                new_lines.append(line)

        _atomic_write_lines(_env_file, new_lines)
        return True
    except Exception as e:
        print(f"WARNING: Failed to save refreshed tokens to {_env_file}: {e}", file=sys.stderr)
        return False


def _atomic_write_lines(path: str, lines) -> None:
    """Write a file via temp + fsync + os.replace.

    Token writes must never be torn: Intuit rotates the refresh token on
    every refresh and invalidates the prior one server-side, so a crash
    mid-write (truncating .env) loses the only valid refresh token and
    bricks every future run until manual re-authorization. A concurrent
    reader also never sees a half-written file.
    """
    tmp_path = f"{path}.tmp"
    with open(tmp_path, 'w') as f:
        f.writelines(lines)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def create_client(credentials: Optional[Dict[str, str]] = None) -> Tuple[Optional[object], Optional[Dict]]:
    """
    Create QBO client without preemptive token refresh.

    Uses lazy refresh - tokens are only refreshed when an API call fails with 401.
    Use refresh_client() to manually refresh if needed.

    Args:
        credentials: Optional credentials dict. If None, loads from env vars.

    Returns:
        Tuple of (client, error_dict). On success, error_dict is None.
        On failure, client is None and error_dict contains error details.
    """
    if not QBO_IMPORTS_AVAILABLE:
        return None, {
            "success": False,
            "error": "MISSING_PACKAGES",
            "message": "Missing required packages. Install with: pip install python-quickbooks intuitlib"
        }

    if credentials is None:
        try:
            credentials = validate_env_vars()
        except ValueError as e:
            return None, {
                "success": False,
                "error": "MISSING_ENV_VARS",
                "message": str(e)
            }

    try:
        auth_client = AuthClient(
            client_id=credentials['client_id'],
            client_secret=credentials['client_secret'],
            access_token=credentials['access_token'],
            environment=credentials['environment'],
            redirect_uri='https://developer.intuit.com/v2/OAuth2Playground/RedirectUrl'
        )

        # No preemptive refresh - use lazy refresh on 401
        client = QuickBooks(
            auth_client=auth_client,
            refresh_token=credentials['refresh_token'],
            company_id=credentials['realm_id']
        )

        # Store credentials on client for refresh_client() to use
        client._qbo_credentials = credentials

        return client, None

    except Exception as e:
        return None, {
            "success": False,
            "error": "CLIENT_ERROR",
            "message": f"Failed to create QBO client: {str(e)}"
        }


def refresh_client(client) -> Tuple[Optional[object], Optional[Dict]]:
    """
    Refresh the client's OAuth tokens and return a new client.

    Call this when an API call fails with 401/auth error.

    Args:
        client: Existing QuickBooks client with _qbo_credentials attached.

    Returns:
        Tuple of (new_client, error_dict). On success, error_dict is None.
    """
    if not hasattr(client, '_qbo_credentials'):
        return None, {
            "success": False,
            "error": "CLIENT_ERROR",
            "message": "Client missing credentials - cannot refresh"
        }

    credentials = client._qbo_credentials

    try:
        auth_client = AuthClient(
            client_id=credentials['client_id'],
            client_secret=credentials['client_secret'],
            access_token=credentials['access_token'],
            environment=credentials['environment'],
            redirect_uri='https://developer.intuit.com/v2/OAuth2Playground/RedirectUrl'
        )

        # Perform the refresh
        auth_client.refresh(refresh_token=credentials['refresh_token'])

        # Save new tokens to .env
        new_refresh = auth_client.refresh_token or credentials['refresh_token']
        save_refreshed_tokens(auth_client.access_token, new_refresh)

        # Update credentials for next refresh
        credentials['access_token'] = auth_client.access_token
        credentials['refresh_token'] = new_refresh

        # Create new client with refreshed tokens
        new_client = QuickBooks(
            auth_client=auth_client,
            refresh_token=new_refresh,
            company_id=credentials['realm_id']
        )
        new_client._qbo_credentials = credentials

        return new_client, None

    except AuthClientError as e:
        error_str = str(e).lower()
        if 'invalid_grant' in error_str or 'expired' in error_str:
            return None, {
                "success": False,
                "error": "REFRESH_TOKEN_EXPIRED",
                "message": "QBO refresh token has expired (valid for 100 days). Re-authorize at: https://developer.intuit.com/v2/OAuth2Playground/RedirectUrl and update .env with new tokens."
            }
        return None, {
            "success": False,
            "error": "AUTH_ERROR",
            "message": f"Token refresh failed: {str(e)}"
        }

    except Exception as e:
        return None, {
            "success": False,
            "error": "REFRESH_ERROR",
            "message": f"Failed to refresh client: {str(e)}"
        }


def is_auth_error(exception: Exception) -> bool:
    """
    Check if an exception indicates an authentication/authorization error.

    Args:
        exception: The exception to check.

    Returns:
        True if this looks like a 401/auth error that refresh might fix.
    """
    error_str = str(exception).lower()
    return any(indicator in error_str for indicator in [
        '401', 'unauthorized', 'authentication', 'token expired',
        'invalid_token', 'access_token'
    ])


def get_entity_class(entity_name: str) -> Tuple[Optional[Type], Optional[str]]:
    """
    Map entity name to SDK class (case-insensitive).

    Args:
        entity_name: Entity name like "Account", "invoice", "JournalEntry".

    Returns:
        Tuple of (entity_class, error_message). On success, error is None.
    """
    if not QBO_IMPORTS_AVAILABLE:
        return None, "QBO SDK not available"

    normalized = entity_name.lower().replace('-', '').replace('_', '')

    if normalized in ENTITY_MAP:
        return ENTITY_MAP[normalized], None

    valid = list_entity_names()
    return None, f"Unknown entity '{entity_name}'. Valid entities: {', '.join(valid)}"


def list_entity_names() -> list:
    """
    Return list of valid entity names for error messages.

    Returns:
        Sorted list of canonical entity names.
    """
    # Return canonical names (excluding aliases like 'trackingclass')
    canonical = set()
    seen_classes = set()

    for name, cls in ENTITY_MAP.items():
        if cls not in seen_classes:
            canonical.add(name.title() if name != 'class' else 'Class')
            seen_classes.add(cls)

    return sorted(canonical)


def fetch_all_pages(entity_cls, qb, order_by: str = 'Id', page_size: int = 1000) -> list:
    """
    Fetch every object of an entity type, paginating past the SDK's
    .all() default of MAXRESULTS 100 (which silently truncates).

    Args:
        entity_cls: SDK entity class (Account, Customer, Vendor, ...).
        qb: Authenticated QuickBooks client.
        order_by: Field for deterministic paging order (default 'Id').
        page_size: Rows per query; QBO's server-side ceiling is 1000.

    Returns:
        List of SDK objects across all pages.
    """
    results = []
    start_position = 1
    while True:
        page = entity_cls.all(order_by=order_by, start_position=start_position,
                              max_results=page_size, qb=qb)
        results.extend(page)
        if len(page) < page_size:
            break
        start_position += page_size
    return results


# =============================================================================
# Infrastructure: Rate Limiting, Concurrency, Connection Testing
#
# Used by bookkeeping and other modules that need sustained QBO API access.
# =============================================================================

MIN_REQUEST_INTERVAL = 0.15  # 150ms between requests
MAX_RETRIES = 3


def save_tokens_if_available(client, env_path: Optional[str] = None) -> None:
    """Save refreshed tokens from client's auth_client if available."""
    if hasattr(client, 'auth_client') and client.auth_client:
        auth = client.auth_client
        if hasattr(auth, 'access_token') and hasattr(auth, 'refresh_token'):
            if env_path:
                _save_tokens_to_path(auth.access_token, auth.refresh_token or '', env_path)
            else:
                save_refreshed_tokens(auth.access_token, auth.refresh_token or '')


def _save_tokens_to_path(access_token: str, refresh_token: str, env_path: str) -> None:
    """Save tokens to a specific .env path (for callers that resolve their own path)."""
    if not os.path.exists(env_path) or not access_token or not refresh_token:
        return
    try:
        with open(env_path, 'r') as f:
            lines = f.readlines()
        new_lines = []
        for line in lines:
            if line.startswith('QBO_ACCESS_TOKEN='):
                new_lines.append(f'QBO_ACCESS_TOKEN={access_token}\n')
            elif line.startswith('QBO_REFRESH_TOKEN='):
                new_lines.append(f'QBO_REFRESH_TOKEN={refresh_token}\n')
            else:
                new_lines.append(line)
        _atomic_write_lines(env_path, new_lines)
    except Exception as e:
        print(f"WARNING: Failed to save tokens to {env_path}: {e}", file=sys.stderr)


def test_qbo_connection(client, env_path: Optional[str] = None) -> Tuple[bool, str]:
    """Test QBO connection with a CompanyInfo query. Saves refreshed tokens."""
    try:
        results = CompanyInfo.all(qb=client)
        company_info = results[0] if results else None
        company_name = company_info.CompanyName if company_info else "Unknown"
        save_tokens_if_available(client, env_path)
        return True, f"Connected to: {company_name}"
    except Exception as e:
        return False, f"Connection test failed: {str(e)}"


# Alias for bookkeeping compatibility
validate_qbo_env_vars = validate_env_vars


def create_qbo_client(credentials: Dict[str, str]) -> Tuple[Optional[object], Optional[str]]:
    """
    Create QBO client — compatibility wrapper for bookkeeping.
    Returns (client, error_string) instead of (client, error_dict).
    """
    client, error_dict = create_client(credentials)
    if error_dict:
        return None, error_dict.get('message', str(error_dict))
    return client, None


class QBORateLimiter:
    """Rate limiter with exponential backoff for sustained QBO API access."""

    def __init__(self, min_interval: float = MIN_REQUEST_INTERVAL):
        self.min_interval = min_interval
        self.last_request_time = 0.0
        self.backoff_until = 0.0

    def wait(self):
        """Wait if needed before making the next API call."""
        now = time.time()
        if now < self.backoff_until:
            time.sleep(self.backoff_until - now)
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_request_time = time.time()

    def trigger_backoff(self, retry_count: int):
        """Trigger exponential backoff after a rate limit error."""
        wait_time = 0.5 * (2 ** retry_count)
        self.backoff_until = time.time() + wait_time


class FileLock:
    """Simple file-based lock for preventing concurrent QBO script runs."""

    def __init__(self, lock_path: str):
        self.lock_path = lock_path
        self.lock_file = None

    def acquire(self) -> bool:
        """Acquire lock. Returns True if successful, False if already locked."""
        try:
            self.lock_file = open(self.lock_path, 'w')
            fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.lock_file.write(str(os.getpid()))
            self.lock_file.flush()
            return True
        except (IOError, OSError):
            if self.lock_file:
                self.lock_file.close()
                self.lock_file = None
            return False

    def release(self):
        """Release the lock."""
        if self.lock_file:
            try:
                fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_UN)
                self.lock_file.close()
                os.remove(self.lock_path)
            except (IOError, OSError):
                pass
            self.lock_file = None
