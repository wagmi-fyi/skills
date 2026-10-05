# qbo

Reads and writes QuickBooks Online from an agent session. Query any entity, create accounts, create and send invoices. Everything comes back as JSON.

The bookkeeping skill uses this one for its QuickBooks work. It also stands alone.

## What it needs

| | |
|---|---|
| **Runtime** | Python 3.12 or newer. |
| **Packages** | Three, pinned in `requirements.txt`. The QuickBooks SDK, Intuit's OAuth client, and a `.env` loader. |
| **Credentials** | A QuickBooks Online OAuth app: client id, client secret, access token, refresh token, and the realm id of the company. On a CommonClaw machine that runs a token service, the service can hold them, and the settings file then holds identifiers only. |
| **Network** | Intuit's API, and nothing else. |

Getting those five values means registering an app on the Intuit Developer Portal, passing a short compliance questionnaire, and running one consent flow as an admin of the QuickBooks company. `reference/credential-setup.md` walks through all of it, and your agent can drive it with you. Budget about 15 minutes.

## Install

Install the `wagmi-skills` plugin, which holds this skill. [Install](../README.md#install) in the repository's README says how.

## First run

Ask for something you can check against the QuickBooks web UI:

> Use qbo. List my bank accounts.

If the credentials are wrong the skill says so and names both the variables it wanted and every path it looked in. A `REFRESH_TOKEN_EXPIRED` error means re-running the consent step, which needs a company admin. The new refresh token goes into the `.env`, or into the token service's vault item followed by the seed door with `--reseed`.

## Writes are real

Reads are safe. `create_account.py`, `create_customer.py`, `create_invoice.py` and `send_invoice.py` change a live company file, and `send_invoice.py` emails a customer. `reference/production-testing.md` covers testing against a real realm without leaving a mess behind.
