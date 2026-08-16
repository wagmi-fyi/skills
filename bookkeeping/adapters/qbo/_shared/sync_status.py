"""Sync status DB update helpers for QBO publishing."""

import json
import sqlite3
from datetime import datetime


def update_sync_success(conn: sqlite3.Connection, table: str, record_id: str, external_id: str) -> None:
    """Update record after successful sync."""
    now = datetime.now().isoformat()
    sync_data = json.dumps({
        'status': 'synced',
        'external_id': external_id,
        'error': None,
        'last_synced_at': now
    })
    conn.execute(f"UPDATE {table} SET sync = ? WHERE id = ?", (sync_data, record_id))


def _is_verify_error(error) -> bool:
    """LOCATE_AMBIGUOUS / LOCATE_INCONCLUSIVE (see _shared/locate.py) mean the
    object's posted-state in QBO is UNKNOWN — a blind re-publish could
    double-post."""
    if isinstance(error, str):
        return error.startswith('LOCATE_AMBIGUOUS') or error.startswith('LOCATE_INCONCLUSIVE')
    if isinstance(error, dict):
        code = str(error.get('error_code') or '')
        return code.startswith('LOCATE_AMBIGUOUS') or code.startswith('LOCATE_INCONCLUSIVE')
    return False


def update_sync_error(conn: sqlite3.Connection, table: str, record_id: str, error) -> None:
    """Update record after failed sync.

    Posted-state-unknown errors route to status='verify' instead of 'error':
    neither `--sync_status pending` nor `--sync_status error` selects that
    status, so the record is structurally excluded from re-publish until a
    human verifies in QBO (then sets the real external_id, or resets the row
    to pending if the object truly never posted).
    """
    now = datetime.now().isoformat()
    sync_obj = {
        'status': 'verify' if _is_verify_error(error) else 'error',
        'external_id': None,
        'error': str(error) if not isinstance(error, dict) else error,
        'last_synced_at': now
    }
    sync_data = json.dumps(sync_obj)
    conn.execute(f"UPDATE {table} SET sync = ? WHERE id = ?", (sync_data, record_id))


def update_sync_ignore(conn: sqlite3.Connection, table: str, record_id: str) -> None:
    """Mark record as ignored for sync (e.g., clearing JEs replaced by Payment objects)."""
    now = datetime.now().isoformat()
    sync_data = json.dumps({
        'status': 'ignore',
        'external_id': None,
        'error': None,
        'last_synced_at': now
    })
    conn.execute(f"UPDATE {table} SET sync = ? WHERE id = ?", (sync_data, record_id))
