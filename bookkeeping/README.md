# bookkeeping

Runs a set of books from an agent session. Ingest transactions, categorize them, reconcile, close a period, publish to a system of record.

The work is intent-driven. You say what you want done and the agent reasons across the skill's operations to do it, rather than following a fixed script. State lives in workpapers that survive between sessions, so a close can stop and pick up later.

One skill serves many clients. Shared logic sits in the skill, firm-wide defaults in a firm directory, and each client's own conventions in that client's workspace.

## What it needs

| | |
|---|---|
| **Runtime** | Python 3.12 or newer. SQLite, which ships with Python. |
| **Packages** | Pinned in `requirements.txt`, grouped core against adapter. Core needs one package. Each adapter adds its own, so a deployment installs only the blocks it uses. |
| **A client config** | `templates/config-template.yaml` is the shape. It names the paths the skill reads and writes, and which system of record the books publish to. |
| **The qbo skill** | Only for QuickBooks work. Install it beside this one and the adapters find it. |
| **Credentials** | None for core. Each adapter names its own; see below. |

## What leaves the machine

Core bookkeeping talks to nothing. Ingest, categorize, reconcile and the SQLite staging all run local. Every outbound call comes from an adapter you chose to use.

| Adapter | Reaches | Needs |
|---|---|---|
| QuickBooks | Intuit's API | The qbo skill and its OAuth credentials |
| Client authorization link (Auth My Accountant) | `auth-my-accountant.vercel.app` by default, overridable with `AMA_API_URL` | `AMA_FIRM_API_KEY`. Creating a link also needs `STRIPE_API_KEY` and `STRIPE_PUBLISHABLE_KEY`. Those two open one Stripe session and are not stored at the far end |
| Stripe balances and transactions | Stripe's API | `STRIPE_API_KEY` |
| Exchange rates | `api.frankfurter.dev` | Nothing. It is an open endpoint |

Bank feeds run through Stripe Financial Connections by default. Your agent can help you set up Plaid or another feed provider if preferred.

Auth My Accountant, another open-source WAGMI project, helps your agent set up share links for clients to securely authorize read-only Stripe access to their financial accounts.

The skill's agent calls the service and gets back a link. You send that link to the client. They open it, pick their bank, and sign in through Stripe, on the bank's own site where the bank supports that. What returns to the skill is a list of account IDs with display details: institution name, last four digits, account type. Nobody on the firm's side sees the client's bank username or password. The Stripe keys the skill passes in open one session and are not stored. Transactions and balances never pass through the service; the skill pulls those from Stripe directly, which is the Stripe row above.

A firm key comes from one command, `adapters/ama_client.py signup`, which makes the firm on the service and saves its key for the adapter. The service keeps only a hash of the key, so a lost key means signing up again. The rest of the skill works with no bank feeds at all.

## Install

Install the `wagmi-skills` plugin, which holds this skill and `qbo`. [Install](../README.md#install) in the repository's README says how.

## First run

> Use bookkeeping.

The skill reads the config, loads whatever client context exists, and tells you where things stand before asking what to work on. With no config yet it offers to walk through onboarding.

## Money is real

The publish adapters write to a live accounting system. Period closes produce numbers somebody files. `reference/quality-guidelines.md` and `reference/review-checks.md` carry the checks that keep the output trustworthy, and the operations gate irreversible steps on a human. Read `reference/bookkeeping-principles.md` before changing how anything is categorized.
