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

A firm key comes from an operator rather than self-service, and the deeper troubleshooting steps in `reference/bank-feeds-troubleshooting.md` route to that operator. The rest of the skill works with no bank feeds at all.

## Agent install

Give this prompt to your agent:

```
Install the bookkeeping skill from WAGMI.

Source: https://github.com/wagmi-fyi/skills/tree/main/bookkeeping

Before you install anything:

1. Read it first. Fetch SKILL.md and every file it references. Tell me in
   plain terms what it does, what it touches on my machine, what it sends
   over the network, and what credentials it expects. Name anything you
   would not run yourself.

2. Elicit until you're 95% confident you understand my intent. Ask what I
   want this for, what my setup is, and what would make installing it a
   mistake. Don't guess.

3. Only then install it, to whichever path my agent reads:
     ~/.claude/skills/bookkeeping     Claude Code
     ~/.agents/skills/bookkeeping     the AGENTS.md convention
     .claude/skills/bookkeeping       this project only
   If I want QuickBooks, install the qbo skill the same way and in the
   same place, so the two sit side by side. Then ask me which adapters I
   plan to use, and set up only the packages those need from
   requirements.txt.
   Start a fresh session afterwards so it gets indexed.

4. Set up my first client: copy templates/config-template.yaml into a
   _local-bookkeeping/ folder in my project, fill in the paths, and ask me
   which system of record my books publish to. Then ask me for any
   credentials the adapters I chose need, and put them where this machine
   keeps secrets rather than in a file. Nothing goes in git. Never ask me
   to paste a secret into our conversation.

Stop and tell me if anything looks wrong, or if it needs something I
don't have.
```

## Manual install

Clone the repository, copy the `bookkeeping` folder into whichever skills directory your agent reads, and add `qbo` beside it if you need QuickBooks. Install the pins from `requirements.txt` for the adapters you actually use. Copy `templates/config-template.yaml` to `_local-bookkeeping/config.yaml` in your project and fill in the paths and the system of record. Adapter credentials come from the environment. `SKILL.md` covers the config chain in full.

## First run

> Use bookkeeping.

The skill reads the config, loads whatever client context exists, and tells you where things stand before asking what to work on. With no config yet it offers to walk through onboarding.

## Money is real

The publish adapters write to a live accounting system. Period closes produce numbers somebody files. `reference/quality-guidelines.md` and `reference/review-checks.md` carry the checks that keep the output trustworthy, and the operations gate irreversible steps on a human. Read `reference/bookkeeping-principles.md` before changing how anything is categorized.
