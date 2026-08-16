# QBO Publisher Gotchas (`gotchas.md` convention)

Field-tested quirks of this SoR, reviewed by the Publish operation before every publish (see `operations/process-period.md`). Append newly-earned, SoR-generic gotchas here; client-specific state belongs in that client's local content, never in a shared adapter.

## Preferences

- **`SalesFormsPrefs.AutoApplyCredit` / `AutoApplyPayments` are read-only via API.** A full-object
  Preferences update returns success and silently ignores changes to these fields. They can only be
  changed in the UI: Gear → Account and settings → Advanced → Automation (each section has its OWN
  Save button). Treat the toggle as a documented per-client onboarding/publish precondition — the
  publisher cannot self-configure it.
- **`AutoApplyCredit=ON` breaks explicit credit application.** QBO auto-consumes a CreditMemo the
  moment it's created (zero-$ auto-generated Payment, oldest-open-invoices-first — it will happily
  spray pennies onto ancient partial invoices). Every subsequent explicit application of that credit
  fails QB 6000 ("Amount Received plus credits can't be less than selected charges"). Repair
  pattern: delete the auto-generated artifact Payment (restores prior invoice balances), then issue
  ONE atomic Payment update carrying the charge and ALL credit links simultaneously. A worked
  example lives in the relevant client's period-close workpaper (§Publish).

## Human-relayed UI changes

- **Wrong-company risk.** When a human flips a UI setting on request, their QBO browser session may
  be signed into a DIFFERENT company file (the firm's own books, another client). The setting lands
  wherever the session points — silently. Protocol: (1) have the human confirm the company name in
  the QBO header BEFORE changing anything; (2) API-verify afterward against the client realm
  (`companyinfo` CompanyName + the actual field value); (3) if a wrong-company change happened,
  remember to REVERT it in that company too. Earned 2026-06-11: an AutoApplyCredit toggle landed in
  the firm's account first; API read-back against the client caught it.
- **Verify with delayed read-back.** Allow a couple of minutes and re-read before concluding a UI
  change didn't take — and before concluding anything else, rule out wrong-company first.

## Payments

- **Payment line updates are not atomic-by-default.** Adding charge/credit links one save at a time
  can transiently violate "received + credits ≥ selected charges" and fail QB 6000 even when the
  final state would be valid. Build the complete line set and save once. (Publisher fix tracked in
  the relevant client's publisher-gaps notes.)

- **`--allow_mixed_credit` settlements net the CM only for `settlement_id`-keyed channels; `payout_id`
  channels (Shopify) silently skip it.** The consolidated mixed-payment path (`query_settlement_credit_apps`
  in `_publishers/payments.py`) pulls the CM-consume TAP by `metadata.settlement_id`. A payout_id-keyed
  channel has no settlement_id → the bank-funded CM-consume TAP (parent type=`credit_memo`, `import_id` set)
  matches no publisher phase → left `pending`; the invoice Payments then post at FULL face (over-depositing
  the bank by ΣCM) and the CreditMemo publishes but FLOATS unapplied (`RemainingCredit` = ΣCM). Symptom: one
  stubborn pending TAP whose parent is a credit_memo + a floating CM + bank over by exactly the CM total.
  Manual remediation: net the CM into ONE of the settlement's QBO Payments via a sparse update —
  `TotalAmt = ΣR − ΣCM`, `Line = [Invoice LinkedTxn(face), CreditMemo LinkedTxn(face)]` (CustomerRef AND
  DepositToAccountRef are required even on a sparse Payment update) → invoice stays Balance 0, bank drops by
  ΣCM, CM Balance→0; then set the local CM-consume TAP `sync=ignore`. Forward fix: the consolidated-payment
  grouping must also key on `payout_id`. Earned on a live close, on a payout-keyed channel.

## Errors that lie

- **QB `10000`/some `6000` errors can post-then-fail.** The object may exist server-side despite
  the error response — read back by natural key before retrying, or a blind retry double-posts.
  (Handled in `_shared/` since the 2026-06 robustness work; kept here because it shapes how every
  new save-site must be written.)
