#!/usr/bin/env python3
"""
Auth My Accountant (AMA) — Bundle Client Adapter
Creates multi-institution auth bundles on the AMA session broker and retrieves
connected-account results. The bundle URL goes to the client (or is self-authed);
results come back as Stripe Financial Connections account IDs (fca_xxx) ready
for scripts/manage_bank_feeds.py and the stripe_fc_* adapters.

Usage:
    # Sign up a firm; its key is saved in {local_dir}/adapters/.env
    python adapters/ama_client.py signup --firm_name "Your Firm" [--replace]

    # Create a bundle (returns URL to send to the client)
    python adapters/ama_client.py create-bundle
    python adapters/ama_client.py create-bundle --firm_name "Your Firm" \\
        --consent_title "Connect Your Bank Accounts" \\
        --consent_body "Please connect all accounts used for your business." \\
        --client_ref your-client-id --max_sessions 5 --expires_in_hours 72

    # Check bundle status / retrieve connected accounts
    python adapters/ama_client.py status --bundle_id <uuid>

Environment (from {local_dir}/adapters/.env):
    AMA_FIRM_API_KEY        firm API key (acp_...); signup writes it, every other command needs it
    AMA_API_URL             optional — defaults to production AMA
    STRIPE_API_KEY          create-bundle only — passed transiently to AMA, never stored there
    STRIPE_PUBLISHABLE_KEY  create-bundle only
"""

import argparse
import json
import math
import sys
import os
import tempfile
import urllib.request
import urllib.error

# Load config to find local_dir for .env
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts', '_shared'))
import config_loader
_config = config_loader.load_config()
ENV_PATH = os.path.join(_config['local_dir'], 'adapters', '.env')

from dotenv import load_dotenv
load_dotenv(ENV_PATH)

DEFAULT_API_URL = "https://auth-my-accountant.vercel.app"

# Every sentence signup prints lives here, so the wording can be read and
# changed in one place.
SIGNUP_MESSAGES = {
    'saved': "Signed up {name}. Your firm key is saved in {path} as AMA_FIRM_API_KEY, "
             "readable by you only.\n"
             "The service keeps no copy of the key. If this file is lost, sign up again.",
    'already_has_key': "A firm key is already saved in {path}. Nothing was changed. "
                       "To sign up a new firm and put its key in its place, run the same "
                       "command with --replace. The old key keeps working until its firm "
                       "is suspended.",
    'no_name': "No firm name was given. Pass --firm_name, or set firm_name in config.yaml. "
               "Nothing was sent.",
    'cannot_write': "Cannot write the firm key to {path}: {reason}. Nothing was sent.",
    'refused': "The service refused the sign-up: {words}. Nothing was saved.",
    'daily_cap': "Sign-ups are closed for today because the daily limit was reached. "
                 "Try again after midnight UTC. Nothing was saved.",
    'rate_limited': "Too many sign-ups from this network. Try again in {minutes} {unit}. "
                    "Nothing was saved.",
    'unreachable': "Could not reach {url}: {reason}. Nothing was saved.",
    'lost': "The firm was made, but its key could not be saved to {path}: {reason}. "
            "The key is gone. Run signup again.",
}

KEY_NAME = 'AMA_FIRM_API_KEY'

# Session permissions enum (AMA validation.ts createBundleSchema) — plural "balances",
# unlike the refresh feature enum which uses singular "balance".
VALID_PERMISSIONS = {'transactions', 'balances', 'ownership', 'payment_method'}


# =============================================================================
# CLI Setup
# =============================================================================

def parse_arguments():
    """Parse CLI arguments with subcommands."""
    parser = argparse.ArgumentParser(
        description="Create AMA auth bundles and retrieve connected fca account IDs"
    )
    subparsers = parser.add_subparsers(dest='command', required=True)

    create = subparsers.add_parser('create-bundle', help='Create a multi-institution auth bundle')
    create.add_argument('--consent_title', default='Connect Your Bank Accounts',
                        help='Client-facing consent title (max 200 chars)')
    create.add_argument('--consent_body',
                        default='Please connect all financial accounts used for your business '
                                'so your bookkeeper can retrieve transactions and balances.',
                        help='Client-facing consent body (max 5000 chars)')
    create.add_argument('--firm_name', default=None,
                        help='Firm name shown in the consent screen '
                             '(default: firm_name/firm_id from config; required if neither set)')
    create.add_argument('--client_ref', default=None,
                        help='Reference string for this client (default: client_id/client_name '
                             'from config; required if neither set)')
    create.add_argument('--max_sessions', type=int, default=5,
                        help='Number of institution sessions in the bundle (1-20)')
    create.add_argument('--expires_in_hours', type=int, default=72,
                        help='Bundle expiry in hours (1-168)')
    create.add_argument('--permissions', default='transactions,balances',
                        help='Comma-separated session permissions (plural enum: balances)')
    create.add_argument('--prefetch', default='transactions,balances',
                        help='Comma-separated data to prefetch at connect time (empty to disable)')

    signup = subparsers.add_parser(
        'signup', help='Sign up a firm and save its key in the adapter settings file')
    signup.add_argument('--firm_name', default=None,
                        help='The firm name to sign up (default: firm_name from config)')
    signup.add_argument('--replace', action='store_true',
                        help='Put the new key in place of a key the settings file already holds')

    status = subparsers.add_parser('status', help='Get bundle status and connected accounts')
    status.add_argument('--bundle_id', required=True, help='Bundle UUID from create-bundle')

    return parser.parse_args()


# =============================================================================
# Environment Validation
# =============================================================================

def require_env(names):
    """Check required env vars exist, fail fast if any missing."""
    values = {}
    missing = []
    for name in names:
        val = os.getenv(name)
        if not val:
            missing.append(name)
        values[name] = val
    if missing:
        print(json.dumps({
            "success": False,
            "error": f"Missing {', '.join(missing)} in environment. "
                     f"Set in _local-bookkeeping/adapters/.env or set BOOKKEEPING_CONFIG_PATH"
        }))
        sys.exit(1)
    return values


# =============================================================================
# HTTP
# =============================================================================

def api_request(method, url, api_key, body=None):
    """Make an authenticated request to AMA. Fails loud on any non-2xx."""
    data = json.dumps(body).encode('utf-8') if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('Authorization', f'Bearer {api_key}')
    req.add_header('Content-Type', 'application/json')
    req.add_header('User-Agent', 'bookkeeping-ama-client/1.0')

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode('utf-8', errors='replace')
        retry_after = e.headers.get('Retry-After')
        retry_note = f" (Retry-After: {retry_after}s)" if retry_after else ""
        print(json.dumps({
            "success": False,
            "error": f"AMA API HTTP {e.code} for {method} {url}{retry_note}: {body_text}"
        }))
        sys.exit(1)
    except urllib.error.URLError as e:
        print(json.dumps({
            "success": False,
            "error": f"AMA API connection error: {e.reason}"
        }))
        sys.exit(1)


# =============================================================================
# Commands
# =============================================================================

def parse_csv_list(raw):
    """Split a comma-separated flag value into a clean list."""
    return [item.strip() for item in raw.split(',') if item.strip()]


def cmd_create_bundle(args, api_url):
    """Create an auth bundle, output URL + ids."""
    env = require_env(['AMA_FIRM_API_KEY', 'STRIPE_API_KEY', 'STRIPE_PUBLISHABLE_KEY'])

    sk = env['STRIPE_API_KEY']
    if sk.startswith(('sk_test_', 'rk_test_')):
        print("Stripe key mode: TEST (sandbox institutions)", file=sys.stderr)
    elif sk.startswith(('sk_live_', 'rk_live_')):
        print("Stripe key mode: LIVE (real bank connections)", file=sys.stderr)
    else:
        print(f"WARNING: STRIPE_API_KEY has unrecognized prefix ({sk[:8]}…) — "
              f"expected sk_/rk_ + test_/live_", file=sys.stderr)

    permissions = parse_csv_list(args.permissions)
    invalid = set(permissions) - VALID_PERMISSIONS
    if invalid or not permissions:
        print(json.dumps({
            "success": False,
            "error": f"Invalid --permissions {sorted(invalid)}. "
                     f"Valid (plural enum): {sorted(VALID_PERMISSIONS)}"
        }))
        sys.exit(1)

    prefetch = parse_csv_list(args.prefetch)
    invalid_prefetch = set(prefetch) - VALID_PERMISSIONS
    if invalid_prefetch:
        print(json.dumps({
            "success": False,
            "error": f"Invalid --prefetch {sorted(invalid_prefetch)}. "
                     f"Valid (plural enum): {sorted(VALID_PERMISSIONS)}"
        }))
        sys.exit(1)

    client_ref = args.client_ref or _config.get('client_id') or _config.get('client_name')
    if not client_ref:
        print(json.dumps({
            "success": False,
            "error": "No client reference available. Set client_id (or client_name) in config.yaml "
                     "or pass --client_ref — bundles must be attributable to a client."
        }))
        sys.exit(1)
    firm_name = args.firm_name or _config.get('firm_name') or _config.get('firm_id')
    if not firm_name:
        print(json.dumps({
            "success": False,
            "error": "No firm name available. Set firm_name (or firm_id) in config.yaml "
                     "or pass --firm_name — it renders on the client-facing consent page."
        }))
        sys.exit(1)

    provider_config = {"permissions": permissions}
    if prefetch:
        provider_config["prefetch"] = prefetch

    body = {
        "provider": "stripe_fc",
        "provider_config": provider_config,
        "credentials": {
            "secret_key": sk,
            "publishable_key": env['STRIPE_PUBLISHABLE_KEY'],
        },
        "consent": {
            "title": args.consent_title,
            "body": args.consent_body,
            "firm_name": firm_name,
        },
        "client_ref": client_ref,
        "expires_in_hours": args.expires_in_hours,
        "max_sessions": args.max_sessions,
    }

    resp = api_request('POST', f"{api_url}/api/bundles", env['AMA_FIRM_API_KEY'], body)

    print(f"Bundle created. Send this link to the client: {resp.get('url')} "
          f"(expires {resp.get('expires_at')})", file=sys.stderr)
    print(json.dumps({
        "success": True,
        "bundle_id": resp.get('id'),
        "url": resp.get('url'),
        "token": resp.get('token'),
        "status": resp.get('status'),
        "expires_at": resp.get('expires_at'),
        "max_sessions": resp.get('max_sessions'),
        "client_ref": client_ref,
    }, indent=2))


def cmd_status(args, api_url):
    """Get bundle status, output flattened connected accounts."""
    env = require_env(['AMA_FIRM_API_KEY'])

    resp = api_request('GET', f"{api_url}/api/bundles/{args.bundle_id}", env['AMA_FIRM_API_KEY'])

    accounts = []
    for a in resp.get('accounts', []):
        meta = a.get('account_metadata') or {}
        accounts.append({
            "provider_account_id": a.get('provider_account_id'),
            "institution_name": meta.get('institution_name'),
            "last4": meta.get('last4'),
            "category": meta.get('category'),
            "subcategory": meta.get('subcategory'),
            "display_name": meta.get('display_name'),
            "account_status": meta.get('status'),
            "session_index": a.get('session_index'),
        })

    print(f"Bundle {resp.get('status')}: {resp.get('sessions_completed')}/{resp.get('sessions_total')} "
          f"sessions completed, {len(accounts)} account(s) connected", file=sys.stderr)
    print(json.dumps({
        "success": True,
        "bundle_id": resp.get('id'),
        "status": resp.get('status'),
        "client_ref": resp.get('client_ref'),
        "sessions_completed": resp.get('sessions_completed'),
        "sessions_total": resp.get('sessions_total'),
        "expires_at": resp.get('expires_at'),
        "accounts": accounts,
    }, indent=2))


def fail(message):
    """Print a failure the adapter's way and stop."""
    print(json.dumps({"success": False, "error": message}))
    sys.exit(1)


def env_line_key(line):
    """The variable a settings-file line assigns, or None."""
    stripped = line.strip()
    if not stripped or stripped.startswith('#') or '=' not in stripped:
        return None
    name = stripped.split('=', 1)[0].strip()
    if name.startswith('export '):
        name = name[len('export '):].strip()
    return name


def holds_key(lines):
    """True when a settings-file line gives AMA_FIRM_API_KEY a value."""
    for line in lines:
        if env_line_key(line) == KEY_NAME:
            value = line.split('=', 1)[1].strip().strip('"\'')
            if value:
                return True
    return False


def signup_request(api_url, firm_name):
    """POST the sign-up. Returns (status, body dict, headers)."""
    req = urllib.request.Request(
        f"{api_url}/api/signup",
        data=json.dumps({"name": firm_name}).encode('utf-8'),
        method='POST',
    )
    req.add_header('Content-Type', 'application/json')
    req.add_header('User-Agent', 'bookkeeping-ama-client/1.0')
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode('utf-8')), resp.headers
    except urllib.error.HTTPError as e:
        text = e.read().decode('utf-8', errors='replace')
        try:
            body = json.loads(text)
        except ValueError:
            body = {"error": text.strip()[:200] or e.reason}
        return e.code, body, e.headers


def refusal_message(status, body, headers):
    """The sentence for a sign-up the service did not accept."""
    code = body.get('code') if isinstance(body, dict) else None
    if code == 'daily_cap_reached':
        return SIGNUP_MESSAGES['daily_cap']
    if code == 'rate_limited':
        try:
            minutes = max(1, math.ceil(int(headers.get('Retry-After')) / 60))
        except (TypeError, ValueError):
            minutes = 60
        return SIGNUP_MESSAGES['rate_limited'].format(
            minutes=minutes, unit='minute' if minutes == 1 else 'minutes')
    words = (body.get('error') if isinstance(body, dict) else None) or f"HTTP {status}"
    return SIGNUP_MESSAGES['refused'].format(words=str(words).rstrip('.'))


def cmd_signup(args, api_url, env_path=None):
    """Sign up a firm and save its key. The key is never printed."""
    env_path = env_path or ENV_PATH
    firm_name = (args.firm_name or _config.get('firm_name') or '').strip()
    if not firm_name:
        fail(SIGNUP_MESSAGES['no_name'])

    lines = []
    if os.path.exists(env_path):
        with open(env_path) as f:
            lines = f.read().splitlines()
    if holds_key(lines) and not args.replace:
        fail(SIGNUP_MESSAGES['already_has_key'].format(path=env_path))

    # Prove the file can be written before a key exists to lose.
    env_dir = os.path.dirname(env_path)
    try:
        os.makedirs(env_dir, mode=0o700, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=env_dir, prefix='.env.', suffix='.tmp')
    except OSError as e:
        fail(SIGNUP_MESSAGES['cannot_write'].format(path=env_path, reason=e.strerror or e))

    try:
        try:
            status, body, headers = signup_request(api_url, firm_name)
        except urllib.error.URLError as e:
            fail(SIGNUP_MESSAGES['unreachable'].format(url=api_url, reason=e.reason))
        if status != 201 or not isinstance(body, dict) or not body.get('api_key'):
            fail(refusal_message(status, body, headers))

        kept = [line for line in lines if env_line_key(line) != KEY_NAME]
        content = '\n'.join(kept + [f"{KEY_NAME}={body['api_key']}"]) + '\n'
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'w') as f:
                fd = None
                f.write(content)
            os.replace(tmp_path, env_path)
            tmp_path = None
        except OSError as e:
            fail(SIGNUP_MESSAGES['lost'].format(path=env_path, reason=e.strerror or e))
    finally:
        if fd is not None:
            os.close(fd)
        if tmp_path is not None and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    print(SIGNUP_MESSAGES['saved'].format(name=body.get('name', firm_name), path=env_path),
          file=sys.stderr)
    print(json.dumps({
        "success": True,
        "firm_id": body.get('id'),
        "firm_name": body.get('name'),
        "saved_to": env_path,
    }, indent=2))


# =============================================================================
# Main
# =============================================================================

def main():
    try:
        args = parse_arguments()
        api_url = (os.getenv('AMA_API_URL') or DEFAULT_API_URL).rstrip('/')

        if args.command == 'create-bundle':
            cmd_create_bundle(args, api_url)
        elif args.command == 'status':
            cmd_status(args, api_url)
        elif args.command == 'signup':
            cmd_signup(args, api_url)

    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({
            "success": False,
            "error": f"Unexpected error: {repr(e)}"
        }))
        sys.exit(1)


if __name__ == "__main__":
    main()
