#!/usr/bin/env python3
"""
Auth My Accountant (AMA) — Bundle Client Adapter
Creates multi-institution auth bundles on the AMA session broker and retrieves
connected-account results. The bundle URL goes to the client (or is self-authed);
results come back as Stripe Financial Connections account IDs (fca_xxx) ready
for scripts/manage_bank_feeds.py and the stripe_fc_* adapters.

Usage:
    # Create a bundle (returns URL to send to the client)
    python adapters/ama_client.py create-bundle
    python adapters/ama_client.py create-bundle --firm_name "Your Firm" \\
        --consent_title "Connect Your Bank Accounts" \\
        --consent_body "Please connect all accounts used for your business." \\
        --client_ref your-client-id --max_sessions 5 --expires_in_hours 72

    # Check bundle status / retrieve connected accounts
    python adapters/ama_client.py status --bundle_id <uuid>

Environment (from {local_dir}/adapters/.env):
    AMA_FIRM_API_KEY        required — firm API key (acp_...)
    AMA_API_URL             optional — defaults to production AMA
    STRIPE_API_KEY          create-bundle only — passed transiently to AMA, never stored there
    STRIPE_PUBLISHABLE_KEY  create-bundle only
"""

import argparse
import json
import sys
import os
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
