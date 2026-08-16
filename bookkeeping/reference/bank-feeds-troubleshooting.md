# Bank Feeds Troubleshooting

Failure modes and repairs for live bank feeds (Stripe Financial Connections via Auth My Accountant). Setup lives in `operations/connect-bank-feeds.md`; period-close consumption lives in `operations/process-period.md`. This is the reference for when something breaks.

## Diagnostic ladder

Work top-down; each step localizes the fault:

1. **Registry state:** `manage_bank_feeds.py list --account_code {code}` — is the mapping `active`? What was `last_pulled_through`?
2. **FC account + refresh state:** `stripe_fc_refresh.py --account_id {fca}` (read mode) — `account_status` active? Refresh statuses `succeeded`/`pending`/`failed`? `next_refresh_available_at` in the future?
3. **Balance freshness:** `stripe_fc_balances.py --account_id {fca}` — does `as_of` move after a refresh? A frozen `as_of` with an active account suggests institution-side staleness.
4. **AMA layer:** `ama_client.py status --bundle_id {id}` for connection-era questions; AMA HTTP errors pass through verbatim.
5. **Platform escalation:** service down (5xx), env/deploy issues, firm-key problems, prod DB — these belong to the AMA platform operator, not the bookkeeping session: `RUNBOOK.md` in the auth-my-accountant repository (operator access only; not reachable from client workspaces).

## Failure table

| Symptom | Likely cause | Fix |
|---|---|---|
| AMA HTTP 401 | `AMA_FIRM_API_KEY` missing/wrong in `{local_dir}/adapters/.env`, or firm deactivated | Check key present; escalate to platform operator for key replacement (keys are never recoverable, only replaced) |
| AMA HTTP 404 on bundle | Wrong `bundle_id`, or bundle belongs to a different firm | Re-check the id from the create-bundle output in the workpaper |
| AMA HTTP 429 | Rate limit | Respect `Retry-After`; don't loop |
| `create-bundle` fails with Stripe permission error | Restricted key missing **Customers: Write** (AMA creates one transient Customer per bundle), or FC not enabled for live mode | Fix key scopes / enable FC in the Stripe Dashboard |
| Client finished connecting but `status` shows no accounts | Browser-side submission failed (origin validation) — platform-level | Escalate: platform runbook, "origin validation" diagnostic |
| Refresh trigger fails: "account is inactive" | Connection broken/revoked at the bank | **Re-auth procedure** (below) |
| `transaction_refresh.status: failed` | Transient institution failure, or broken connection | Re-trigger once after a pause; if it fails again or account goes inactive → re-auth |
| Refresh trigger errors / no-ops, `next_refresh_available_at` in future | Stripe refresh rate limit | Don't hammer; pull cached data or wait until the timestamp |
| Pull returns 0 transactions | Often legitimate (sparse account — savings) | Cross-check: balance `as_of` fresh + low-activity account → fine. Never pipe an empty pull to ingest (it exits 1 by design) |
| Credit-account balance sign looks wrong | Institution-dependent: some report credit `current` as negative-owed (Wells Fargo), others positive-owed | Verification input is always **positive owed**; negate when the institution reports negative |
| `verify_ingest_balances` mismatch after FC ingest | Duplicates across a source seam, or missing coverage | **Seam playbook** (below) |
| FC pull missing transactions the bank shows | History depth limit (~180 days, institution-dependent) or transactions feature not refreshed | Check earliest `date` in a full pull; gap-fill via file-based adapter if FC can't reach far enough |

## Re-auth procedure (broken/revoked connection)

The most common repair. A new connection issues a **new `fca` id** for the same bank account, and — critically — **all-new `fctxn_` external_ids**, so external-id dedup gives zero protection against re-ingesting history.

1. Create a fresh bundle (`ama_client.py create-bundle`), deliver the link, poll — same as `connect-bank-feeds.md` steps 2–5.
2. Identify the re-connected account by institution + last4 in the `status` output.
3. `manage_bank_feeds.py remap --account_code {code} --provider_account_id {fca_NEW}` — this preserves `last_pulled_through`, resets `connected_at`/`status`, and clears `last_txn_refresh_id` (refresh ids are per-connection).
4. Refresh + smoke the new connection (`stripe_fc_refresh.py --trigger`, poll, balances read).
5. **Date-bound the next pull**: `--start_date` must be derived from live DB state (`MAX(banking_date)` for the account's source, +1 day), never from memory or the registry alone. Run the seam diff before ingesting.

## Seam playbook (mismatched balances after ingest)

Any time two sources (or two connections) meet at a date boundary:

1. Re-derive coverage **at execution time**: `SELECT MAX(banking_date) FROM imports WHERE source LIKE '{code} - %'` — a parallel session may have moved it since you planned.
2. Overlap diff before ingesting: compare the FC pull against existing imports in a ~10-day window at the boundary, matching on (date, amount) exact then ±1 day (file posting dates vs FC `transacted_at` UTC can skew ±1d).
3. If duplicates already landed: identify the FC batch (`batch_id` from the ingest result), `DELETE FROM imports WHERE batch_id = ?` (surgical — the sanctioned [D] path), re-derive, re-pull date-bounded, re-ingest.
4. Verification diff that exactly equals a suspicious subtotal (e.g., the overlapping window's sum) is the duplicate signature — reconcile to the cent before and after.

## Facts worth remembering

- Refresh feature enum is **singular** `balance` (`transactions|balance|ownership`); session permissions/prefetch are **plural** `balances`. Mixing them is a validation error.
- Bundle links expire (≤168h) but **connected accounts remain retrievable from expired bundles** — late polling is safe; connecting late is not.
- One institution login can surface multiple accounts, including sub-cards. Control-account feeds may carry sub-card activity (WF does) — map only the control account; verify against the control balance.
- FC `external_id`s are stable within a connection (re-pulls dedup cleanly) and worthless across connections (re-auth) or across sources (CSV→FC).
