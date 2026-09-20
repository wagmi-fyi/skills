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

- **A deposit that consumes a credit memo posts as ONE Payment, net of the credit.** One bank line can
  pay several invoices while a credit memo reduces the cash. The credit arrives as a bank-funded
  CM-consume TAP: parent trade account `type = 'credit_memo'`, `source_ta_id` NULL, `import_id` set.
  Phase 3b groups that deposit and emits `TotalAmt = ΣR − ΣCM` with one Invoice line per invoice at face
  plus one CreditMemo line, so the bank nets and the credit applies (`RemainingCredit` 0). The group key
  is `deposit_group_key` in `_shared/common.py`: the parent's `payout_id` where a batching channel stamps
  one, the payment's `import_id` otherwise, since a plain ACH or wire deposit is one import. A settlement
  keeps its own path, because its invoice payments already carry cash net of the credit.
  `CustomerRef` and `DepositToAccountRef` are required on the Line update even though it is sparse.

- **A bank-funded payment row no phase can post whole stops the run before anything posts.**
  `find_bank_funded_payment_gaps` names the row by id, in the dry run and in the live run.
  `PAYMENT_MATCHES_NO_PHASE` means no selection reads that row, a bank-funded vendor credit for one.
  `DEPOSIT_GROUP_SPLIT` means one bank line carries a credit memo keyed on the import and a receivable
  keyed on a payout, so the credit and the invoices it reduces would group apart and the invoices would
  post at full face. Neither is answerable from the data. Correct the metadata and run again.

## Errors that lie

- **QB `10000`/some `6000` errors can post-then-fail.** The object may exist server-side despite
  the error response — read back by natural key before retrying, or a blind retry double-posts.
  (Handled in `_shared/` since the 2026-06 robustness work; kept here because it shapes how every
  new save-site must be written.)
