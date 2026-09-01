"""
ClientHolder + auth-aware plumbing for long publish runs.

QBO access tokens live ~60 minutes. The publisher builds ONE client at run
start; before this module, a run longer than the token life 401'd on every
later save (a long production run lost thousands of objects mid-run). Two mechanisms fix that:

  * Proactive refresh — before a save, if more than REFRESH_INTERVAL_SECONDS
    have passed since the last successful refresh, refresh first. The margin
    (40 min) sits well under the 60-min token life because the check only
    runs when a save happens — a long stall between saves eats into it.
  * Reactive retry — on a typed HTTP-401 (AuthorizationException; the SDK
    raises it BEFORE fault parsing, quickbooks/client.py make_request),
    refresh and retry the save ONCE.

`refresh_client()` returns a NEW QuickBooks client (stale references keep the
dead token), so every save path must read the client through the holder at
call time. The holder is threaded down from publish.py wherever a raw client
went; `resolve_client()` unwraps it (and passes raw clients through, so
legacy callers and hermetic tests are unaffected).

Philosophy note: the skill says "no built-in retry; the caller decides
recovery." The reactive auth-retry is a narrow, sanctioned exception:
transport-level, single attempt, typed-401 only, inside one long locked run
where the agent cannot intervene. Business faults (6xxx/10000) are NEVER
retried here — they go to the _shared/locate.py read-back instead.

When the refresh token itself is expired/revoked (REFRESH_TOKEN_EXPIRED;
Intuit refresh tokens last five years at most and rotate on every refresh,
so a stale stored value fails the same way), recovery is impossible
mid-run: the holder is marked auth-dead and every subsequent save fails FAST
and LOUD with a re-authorize message, without network calls. That keeps the
run's per-object accounting intact (each unposted object marked error,
retryable after re-auth) instead of thrashing thousands of doomed requests
or aborting the phase mid-transaction.

Imports from _shared.client happen lazily inside functions: the hermetic
tests stub `_shared.client` in sys.modules before importing publishers, and
a module-top import here would bypass that seam (or sys.exit on missing
credentials).
"""

import os
import time

from quickbooks.exceptions import AuthorizationException

# 40-minute margin under QBO's 60-minute access-token life.
REFRESH_INTERVAL_SECONDS = 2400


class ClientHolder:
    """Mutable handle for the live QBO client across a long publish run."""

    def __init__(self, client, clock=time.monotonic):
        self.client = client
        self.clock = clock
        self.last_refresh = clock()
        # Set to a human-actionable message once refresh is impossible
        # (expired/revoked refresh token). Checked before every save.
        self.auth_dead = None


def resolve_client(client_or_holder):
    """Unwrap a ClientHolder to the live client; pass raw clients through."""
    if isinstance(client_or_holder, ClientHolder):
        return client_or_holder.client
    return client_or_holder


def is_auth_fault(exc: Exception) -> bool:
    """Typed gate for the reactive retry.

    The SDK raises AuthorizationException for HTTP 401 (error_code=401,
    before fault parsing) and for QBO fault codes 1–499 (its auth/authz code
    space). Deliberately NOT the free-text is_auth_error() — business-fault
    detail strings mentioning "token"/"authentication" must not trigger a
    refresh+retry (that would skip the locate read-back and risk the exact
    double-post Gap #1 prevents).
    """
    return isinstance(exc, AuthorizationException)


def auth_dead_error(client_or_holder):
    """The fatal auth message if the holder is poisoned, else None."""
    if isinstance(client_or_holder, ClientHolder) and client_or_holder.auth_dead:
        return f"AUTH_DEAD: {client_or_holder.auth_dead}"
    return None


def _latest_stored_refresh_token(env_path):
    """Best-effort read of the newest persisted refresh token.

    Two processes can share one credentials file (a publish run + a /qbo
    script). Intuit rotates the refresh token periodically, so refreshing
    from a STALE in-memory token after another process already rotated would
    fail with invalid_grant. Re-reading the file first makes every process
    converge on the newest chain instead of racing it. Returns None when the
    path is unset/unreadable (callers fall back to the in-memory token)."""
    if not env_path or not os.path.exists(env_path):
        return None
    try:
        with open(env_path) as f:
            for line in f:
                if line.startswith('QBO_REFRESH_TOKEN='):
                    return line.split('=', 1)[1].strip() or None
    except OSError:
        return None
    return None


def _refresh(holder: ClientHolder, env_path=None) -> bool:
    """Refresh the holder's client. Advances last_refresh ONLY on a confirmed
    successful refresh (a failed refresh must not look fresh — the next save
    re-attempts). Marks the holder auth-dead on REFRESH_TOKEN_EXPIRED."""
    from _shared.client import refresh_client  # lazy: test stub seam
    creds = getattr(holder.client, '_qbo_credentials', None)
    if creds is not None:
        stored = _latest_stored_refresh_token(env_path)
        if stored and stored != creds.get('refresh_token'):
            # Another process rotated the chain after this client was built —
            # refresh from the newest stored token, not the stale one.
            creds['refresh_token'] = stored
    new_client, err = refresh_client(holder.client)
    if new_client is not None:
        holder.client = new_client
        holder.last_refresh = holder.clock()
        return True
    if isinstance(err, dict) and err.get('error') == 'REFRESH_TOKEN_EXPIRED':
        holder.auth_dead = err.get(
            'message', 'QBO refresh token expired — re-authorize and update .env')
    return False


def maybe_proactive_refresh(client_or_holder, env_path=None) -> None:
    """Refresh before a save when the interval has lapsed. No-op for raw
    clients, poisoned holders, and fresh tokens. A transient refresh failure
    is non-fatal here: the save proceeds on the current token (it may still
    be valid) and the reactive path covers a 401."""
    if not isinstance(client_or_holder, ClientHolder) or client_or_holder.auth_dead:
        return
    if client_or_holder.clock() - client_or_holder.last_refresh <= REFRESH_INTERVAL_SECONDS:
        return
    _refresh(client_or_holder, env_path)


def try_reactive_refresh(client_or_holder, env_path=None) -> bool:
    """Refresh after a typed 401. True = retry the save with holder.client.
    Raw clients (legacy callers, hermetic tests) never retry — today's
    behavior."""
    if not isinstance(client_or_holder, ClientHolder) or client_or_holder.auth_dead:
        return False
    return _refresh(client_or_holder, env_path)
