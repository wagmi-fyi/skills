"""
QBO Client — re-exports from the qbo skill (single source of truth).

The qbo skill owns all QBO infrastructure (OAuth, rate limiting, concurrency).
This module re-exports the functions that bookkeeping's QBO adapters need,
providing a single place to manage the dependency path.

The qbo skill's scripts directory is the doorway. It resolves in order, first
existing directory wins:

  1. QBO_SKILL_SCRIPTS — explicit override, for installs the rules below miss.
  2. The qbo skill installed beside this one, derived from this file's location.
     Portable across skill roots: wherever bookkeeping is installed, qbo sits
     next to it.
  3. ~/.claude/skills/qbo/scripts — a global install.

Rule 2 precedes rule 3 so a stale global copy cannot shadow the tree this
module is running from.

If no doorway resolves, this module fails with a clear error naming every path
tried.
"""

import os
import sys

# This file: <skills_dir>/bookkeeping/adapters/qbo/_shared/client.py
# Four levels up is the bookkeeping skill root; its parent holds sibling skills.
_bookkeeping_root = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
_skills_dir = os.path.dirname(_bookkeeping_root)

_candidates = [
    os.environ.get('QBO_SKILL_SCRIPTS'),
    os.path.join(_skills_dir, 'qbo', 'scripts'),
    os.path.expanduser('~/.claude/skills/qbo/scripts'),
]

_qbo_scripts = next(
    (os.path.abspath(c) for c in _candidates if c and os.path.isdir(c)),
    None,
)

if _qbo_scripts is None:
    raise ImportError(
        "qbo skill not found. Install it to use QBO as system of record. "
        "Tried: " + ", ".join(c for c in _candidates if c) + ". "
        "Set QBO_SKILL_SCRIPTS to the qbo skill's scripts directory to override."
    )

sys.path.insert(0, _qbo_scripts)

from qbo_client import (  # noqa: E402
    # Auth & client
    validate_qbo_env_vars,
    create_qbo_client,
    test_qbo_connection,
    refresh_client,
    is_auth_error,
    # Token management
    save_refreshed_tokens,
    save_tokens_if_available,
    # Queries
    fetch_all_pages,
    # Infrastructure
    QBORateLimiter,
    FileLock,
    # Constants
    MAX_RETRIES,
    MIN_REQUEST_INTERVAL,
)
