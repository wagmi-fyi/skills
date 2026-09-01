# qbo

Reads and writes QuickBooks Online from an agent session. Query any entity, create accounts, create and send invoices. Everything comes back as JSON.

The bookkeeping skill uses this one for its QuickBooks work. It also stands alone.

## What it needs

| | |
|---|---|
| **Runtime** | Python 3.12 or newer. |
| **Packages** | Three, pinned in `requirements.txt`. The QuickBooks SDK, Intuit's OAuth client, and a `.env` loader. |
| **Credentials** | A QuickBooks Online OAuth app: client id, client secret, access token, refresh token, and the realm id of the company. |
| **Network** | Intuit's API, and nothing else. |

Getting those five values means registering an app on the Intuit Developer Portal, passing a short compliance questionnaire, and running one consent flow as an admin of the QuickBooks company. `reference/credential-setup.md` walks through all of it, and your agent can drive it with you. Budget about 15 minutes.

## Agent install

Give this prompt to your agent:

```
Install the qbo skill from WAGMI.

Source: https://github.com/wagmi-fyi/skills/tree/main/qbo

Before you install anything:

1. Read it first. Fetch SKILL.md and every file it references. Tell me in
   plain terms what it does, what it touches on my machine, what it sends
   over the network, and what credentials it expects. Name anything you
   would not run yourself.

2. Elicit until you're 95% confident you understand my intent. Ask what I
   want this for, what my setup is, and what would make installing it a
   mistake. Don't guess.

3. Only then install it, to whichever path my agent reads:
     ~/.claude/skills/qbo     Claude Code
     ~/.agents/skills/qbo     the AGENTS.md convention
     .claude/skills/qbo       this project only
   Then set up whatever this machine uses to resolve the Python packages
   in requirements.txt. Install by package name, not import name; the file
   says why that matters.
   Start a fresh session afterwards so it gets indexed.

4. Ask me whether I already have QuickBooks credentials. If I do, help me
   put them where this machine keeps secrets, so the skill reads them from
   the environment and no file sits on disk. If I don't, read
   reference/credential-setup.md and walk me through getting them. Never
   ask me to paste a secret into our conversation.

Stop and tell me if anything looks wrong, or if it needs something I
don't have.
```

## Manual install

Clone the repository, copy the `qbo` folder into whichever skills directory your agent reads, and install the pins from `requirements.txt` into the environment you run scripts with. For credentials, either export the five `QBO_` variables from your secrets manager, or copy `scripts/.env.example` to one of the paths listed in `SKILL.md` under "Where the skill looks" and fill it in. Never inside the skill directory, never in git. `reference/credential-setup.md` covers getting the values in the first place.

## First run

Ask for something you can check against the QuickBooks web UI:

> Use qbo. List my bank accounts.

If the credentials are wrong the skill says so and names both the variables it wanted and every path it looked in. A `REFRESH_TOKEN_EXPIRED` error means re-running the consent step, which needs a company admin.

## Writes are real

Reads are safe. `create_account.py`, `create_customer.py`, `create_invoice.py` and `send_invoice.py` change a live company file, and `send_invoice.py` emails a customer. `reference/production-testing.md` covers testing against a real realm without leaving a mess behind.
