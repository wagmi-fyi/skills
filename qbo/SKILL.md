---
name: qbo
license: Apache-2.0
description: Query and create QuickBooks Online data. Retrieve accounts, invoices, bills, journal entries, customers, vendors, items, classes, payments, deposits. Create new accounts in the chart of accounts. Use when pulling data from QBO, checking balances, looking up transactions, reading QuickBooks records, or adding accounts.
---

# QBO

Read and write QuickBooks Online data through the `python-quickbooks` SDK.

## Activation

Resolve two things once, before the first command, and reuse them for the session.

**`{qbo}` — this skill's own directory.** Every path below is written relative to it. It is the directory holding this file, and the harness knows where it loaded the skill from. Nothing here assumes a particular skill root.

**The Python invocation.** Dependencies are pinned in [`requirements.txt`](requirements.txt) and are not declared inline, so the command carries them. Match whatever the workspace already establishes. With no established convention, `uv` resolves them without a pre-built environment:

```
uv run --with-requirements {qbo}/requirements.txt {qbo}/scripts/query.py --entity=Account
```

An environment that already has the pins installed runs the script directly. Below, `{python} {qbo}/scripts/<script>.py` stands for whichever form this workspace uses.

## Scripts

Each does one thing and prints JSON to stdout. **Check `--help` before invoking one.** This table is for discovery; the script is the authority on its own arguments.

| Script | Does |
|---|---|
| `query.py` | Read any entity: by filter, by id, or as a count. Paginates. |
| `create_account.py` | Add a chart-of-accounts account. Refuses a duplicate name. |
| `create_customer.py` | Find a customer by display name, or create it. |
| `create_invoice.py` | Create an invoice from line items. Refuses a duplicate DocNumber. |
| `send_invoice.py` | Email an existing invoice. |

Reads are safe to explore with. Writes land in a live company file, so [`reference/production-testing.md`](reference/production-testing.md) governs the first write against any realm.

## Output

Every script prints one JSON object to stdout. A read carries `count` (rows in this response), `total_count` (rows matching), and `truncated`. A truncated result needs pagination, not a bigger limit: a page tops out at 1000 rows.

A failure also prints to stdout, as `success: false` with an `error` code, so a caller parses one stream either way. Diagnostics go to stderr.

## Reference

Load on demand, when the work needs that knowledge. A **precondition** reference is read before that action's first use in a session.

| File | Contains | Precondition |
|---|---|---|
| [`reference/entities.md`](reference/entities.md) | Per-entity attributes, filters, worked queries | Before querying an unfamiliar entity |
| [`reference/query-patterns.md`](reference/query-patterns.md) | Query syntax, SDK methods, pagination | Before composing a query or paging a result set |
| [`reference/production-testing.md`](reference/production-testing.md) | Live-realm probes: rails, cleanup, per-realm findings | Before any write against a live realm |
| [`reference/credential-setup.md`](reference/credential-setup.md) | Registering the Intuit app and producing the five credential values, step by step | Before helping anyone obtain credentials for the first time |

Which entities the SDK exposes is what `query.py --help` lists. That set tracks the SDK, so read it there rather than from a copy.

## Credentials

The skill needs a QuickBooks Online OAuth app and a company to point it at. Five values, all from Intuit:

| Value | Comes from |
|---|---|
| Client id, client secret | Your app on the [Intuit Developer Portal](https://developer.intuit.com) |
| Access token, refresh token | Intuit's OAuth 2.0 Playground, launched from that app's dashboard |
| Realm id | The company id, visible in the QuickBooks URL |

Nobody has these on a first install. [`reference/credential-setup.md`](reference/credential-setup.md) is the walkthrough: registering the app, the compliance questionnaire, the consent step, and the first token exchange. Read it before helping anyone obtain credentials, and follow its rule that a secret never enters the conversation.

### Where the skill looks

**The environment wins.** When `QBO_CLIENT_ID`, `QBO_CLIENT_SECRET`, `QBO_ACCESS_TOKEN`, `QBO_REFRESH_TOKEN` and `QBO_REALM_ID` are already set, no file is read and none has to exist. A secrets manager that injects at invocation lands here, and it is the only arrangement with no plaintext credential sitting on disk. Prefer it.

Otherwise the skill looks for a `.env`, first existing file winning:

1. **`QBO_ENV_PATH`** — an explicit path. The override for any layout the rules below miss.
2. **`BOOKKEEPING_CONFIG_PATH`** → `{local_dir}/adapters/.env`. When bookkeeping is in use, both read and write the same file, so a refreshed token cannot drift between them.
3. **`{cwd}/.claude/skills/qbo/.env`** — per project, resolved against the directory the command runs in. That dependence is what lets one project hold its own company file, and it means the same command run from a subdirectory resolves somewhere else.
4. **`~/.claude/skills/qbo/.env`** — a global install.

No candidate is inside this skill's directory, and none should be. A credential does not belong somewhere that gets copied, synced, or committed. [`scripts/.env.example`](scripts/.env.example) is the template and names every variable.

When nothing resolves, the error names the variables it wanted and every path it tried.

### Tokens

Refresh is lazy. A token is renewed only when a call returns 401, and the new pair is written back to whichever `.env` was loaded at startup. That path is logged to stderr, so the write target is never a guess.

When credentials came from the environment there is no file to write to. The skill says so on stderr and keeps working for the rest of the run. The refreshed pair has to be stored back wherever the environment gets its values, or the next run starts from the old one.

A refresh token has a five-year maximum life and rotates on each refresh, so the new value has to be persisted every time. Intuit replaced the old use-it-every-100-days policy in November 2025, and for the accounting scope the first tokens start expiring in October 2028. [`reference/credential-setup.md`](reference/credential-setup.md) carries the dates and the source.

`REFRESH_TOKEN_EXPIRED` means re-authorizing through the OAuth Playground, which needs a QuickBooks company admin. Nothing in the skill can recover it.

## Dependencies

[`requirements.txt`](requirements.txt) holds the pins.

**A deliberate deviation.** `master-builder`'s convention is that Python dependencies
are declared inline in each script, as PEP 723 metadata, with no separate list to
drift. This skill keeps a manifest instead, for two reasons. The import-name trap
below needs a comment line to live on, and a package list has nowhere to put it. And
`bookkeeping` puts this skill's `scripts` directory on `sys.path` and imports from it
at runtime, so the two skills share one environment, which PEP 723's per-script
isolation does not serve. Ruled 2026-08-16. One manifest, never both.

**Install by package name, not by import name.** The OAuth client imports as `intuitlib` and ships on PyPI as **`intuit-oauth`**. Installing `intuitlib` fetches an unrelated project, after which `qbo_client.py` sets `QBO_IMPORTS_AVAILABLE = False` and fails closed rather than raising. Install from `requirements.txt`, which carries the mapping.

## Limits

- Bank feeds are not in the API. Raw feed transactions cannot be read through this skill.
- Intuit rate-limits per realm. The scripts retry with backoff when they are throttled.
- A query page tops out at 1000 rows. Larger sets need pagination.
- Run queries one at a time. Two in parallel race on token refresh, and one of them loses its tokens.
