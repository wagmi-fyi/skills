# Connect Bank Feeds

## Overview

**Intent:** Establish live bank-data connections for a client. The client (or the bookkeeper, with credentials) authenticates their banks through an Auth My Accountant (AMA) bundle link; the resulting Stripe Financial Connections accounts (`fca_xxx`) are mapped to chart-of-accounts codes in `{local_dir}/bank_feeds.yaml`; each mapped feed is smoke-proven (refresh → pull → balance read) before it is trusted by period close.

Run once per client at adoption, and again whenever accounts are added, repaired (re-auth after `disconnected`/`error`), or institutions change. After this operation, `operations/process-period.md` pulls mapped accounts live during Ingest instead of waiting on files.

**Mode note:** The Stripe key prefix determines everything downstream — `sk_test_` connects Stripe's test institutions (sandbox), `sk_live_` connects real banks. Live mode requires Financial Connections to be activated for the Stripe account in the Dashboard.

---

## Preconditions (fail fast, in order)

1. `BOOKKEEPING_CONFIG_PATH` set to the client's `_local-bookkeeping/config.yaml`.
2. `stripe` package importable in the project venv.
3. `{local_dir}/adapters/.env` contains `AMA_FIRM_API_KEY`, `STRIPE_API_KEY`, `STRIPE_PUBLISHABLE_KEY`. Confirm the sk/pk prefixes match the intended mode (test vs live) and each other.
4. The target chart-of-accounts codes exist (`manage_bank_feeds.py add` validates this, but resolve gaps before sending a link to a client).
5. Live runs only: Financial Connections enabled for the Stripe account (Dashboard → Financial Connections), including transactions data access.

---

## Steps

1. **Confirm consent text with the user (client-facing).** `--consent_title`, `--consent_body`, and `--firm_name` render on the auth page the client sees. Confirm wording before creating the bundle.

2. **Create the bundle.**
   ```bash
   BOOKKEEPING_CONFIG_PATH={...}/config.yaml python adapters/ama_client.py create-bundle \
       --firm_name "{firm}" --client_ref {client_id} [--max_sessions 5] [--expires_in_hours 72]
   ```
   Defaults request `transactions,balances` permissions and prefetch both at connect time. Record `bundle_id`, `url`, and `expires_at` in the conversation/workpaper — bundle lifecycle state is transient and is NOT stored in the registry.

3. **Deliver the link (HALT).** Present the URL. The human sends it to the client (or self-auths if they hold credentials). One institution login can surface multiple accounts in a single session. Default expiry is 72h — connecting must happen before expiry, but connected accounts remain retrievable from expired bundles indefinitely.

4. **Poll for results.** On user prompt (or periodic re-check):
   ```bash
   python adapters/ama_client.py status --bundle_id {uuid}
   ```
   Proceed when status is `active` or `completed` with ≥1 account.

5. **Present discovered accounts (HALT).** Table: `provider_account_id`, institution, last4, category/subcategory, display_name, account_status. The user maps each account to a COA code — or explicitly skips it with a reason. No silent skips.

6. **Validate and record each mapping.** Cross-check subcategory against COA account type before recording: `credit_card`/`line_of_credit` ↔ liability; `checking`/`savings`/`money_market` ↔ asset. On mismatch, warn and require explicit confirmation.
   ```bash
   python scripts/manage_bank_feeds.py add --account_code {code} \
       --provider_account_id {fca} --institution "{name}" --last4 {nnnn} \
       --category {category} --subcategory {subcategory}
   ```

7. **Smoke-prove each mapped feed.** Per account:
   - `python adapters/stripe_fc_refresh.py --account_id {fca} --trigger` (transactions+balance together)
   - Poll read mode until both refreshes are `succeeded` (agent-paced waits — the first transactions refresh can take minutes; `failed` is a loud stop)
   - Pull a sample **to a file** (never blind-pipe): `python adapters/stripe_fc_transactions.py --account_id {fca} --start_date {~30d ago} > {file}.json` — report transaction count, min/max dates, and the earliest history available (this is the history-depth observation; compare against any catch-up gap before relying on the feed)
   - `python adapters/stripe_fc_balances.py --account_id {fca}` — report `balance.current` and `as_of`
   - Do NOT ingest here unless the user is doing first adoption — ingestion belongs to `process-period.md`, which owns balance verification and its Hard Stop.

8. **Update the human-facing registry.** Propose edits to `{local_dir}/content/accounts-registry.md` (Source/Adapter columns → `Stripe FC (fca_…)`, cutover date note) via the standard knowledge-capture approval flow. If transitioning an account from file-based ingest, record the cutover boundary date — the seam between file-era and FC-era external IDs is not protected by dedup, only by date-bounding.

9. **Quality gate.** All must be true:
   - Every connected `fca` account is mapped or skipped-with-reason
   - Every mapped account has a successful refresh, sample pull, and balance read
   - `manage_bank_feeds.py list` output validates (1:1 code↔fca, correct categories)
   - accounts-registry.md update proposed
   - Evidence (bundle_id, accounts table, smoke results) recorded in the workpaper

---

## References

- `reference/capability-registry.md` — `ama_client.py`, `manage_bank_feeds.py`, `stripe_fc_refresh.py`, `stripe_fc_transactions.py`, `stripe_fc_balances.py` contracts
- `operations/process-period.md` — Ingest-domain consumption of `bank_feeds.yaml` (live pull pipeline + balance verification Hard Stop)
- `reference/adapter-patterns.md` — fail-loud conventions, env/config handling
- `reference/bank-feeds-troubleshooting.md` — failure modes, re-auth/remap procedure, seam playbook, platform escalation
