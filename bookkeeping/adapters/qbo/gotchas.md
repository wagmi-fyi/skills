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
  one, otherwise the payment's `import_id` **and the parent's contact**, since a plain ACH or wire deposit
  is one import and a QBO Payment carries one customer. One bank line paying two customers is two
  Payments, and a credit memo nets the invoices of its own customer. A settlement keeps its own path,
  because its invoice payments already carry cash net of the credit.

- **A bank-funded payment row no phase can post whole stops the run before anything posts.**
  `find_bank_funded_payment_gaps` names the row by id, in the dry run and in the live run. The check
  does two things. It tests that every bank-funded row is claimed by a phase.
  `PAYMENT_MATCHES_NO_PHASE` is a row no selection reads, and a bank-funded vendor credit is the case
  to expect. `DEPOSIT_GROUP_SPLIT` is a deposit keyed on its import that shares a bank line and a
  contact with a row no consumed-credit group holds, so the credit and the invoices it reduces would
  group apart and those invoices would post at full face. Two whole deposits on one bank line pass. A
  payable on that line passes, and so does another customer's row. The check also tests that every
  deposit can become one Payment. That part is `check_consumed_credit_group`, which the
  consumed-credit phase calls too, so a clean gate is never followed by the phase writing rows to
  error: a credit memo with no invoice of its own, a deposit a prior run part-published, two customers
  or two dates in one group, a credit worth more than the invoices.

  The stop holds back the bank-funded payment phases and nothing else. Journal entries,
  invoices, bills and the credit documents publish as usual, and the run reports
  `success: false` with every gap in `errors`.

  Most gaps are a metadata fix: give the credit memo and the invoices it reduces the same
  contact and the same key, then run again. A row no metadata change can route, a bank-funded
  vendor credit being the case to expect, has no remedy in the skill today, and there is no
  narrower `--publish_type` that reaches the payment phases without the check. Such a book
  publishes no bank-funded payments until the row is dealt with. Raise it: it needs a publish
  phase that does not exist yet.

- **A book whose invoices already posted at full face shows one credit memo in
  `PAYOUT_PARTIALLY_PUBLISHED` or `PAYOUT_GROUP_INCOMPLETE`.** The bank is over by the credit,
  the CreditMemo floats with `RemainingCredit` equal to its face, and the credit memo's payment
  row is still pending. The publisher cannot net a deposit whose invoices are already posted,
  so it refuses the group and the dry run names it. Repair it by hand with the recipe below,
  then the period ties and later deposits publish whole on their own.

- **Netting a credit into a Payment that already posted.** One sparse update on ONE of the
  deposit's QBO Payments: `TotalAmt = ΣR − ΣCM`, `Line = [Invoice LinkedTxn(face), CreditMemo
  LinkedTxn(face)]`. `CustomerRef` and `DepositToAccountRef` are both required on that update
  even though neither changes. The invoice stays at Balance 0, the bank drops by ΣCM, and the
  CreditMemo goes to Balance 0. Then set the local credit-memo payment row to `sync=ignore`, so
  no later run tries to publish it again. This is the answer to
  `PAYOUT_PARTIALLY_PUBLISHED`, whose message asks for manual reconciliation.

## Errors that lie

- **QB `10000`/some `6000` errors can post-then-fail.** The object may exist server-side despite
  the error response — read back by natural key before retrying, or a blind retry double-posts.
  (Handled in `_shared/` since the 2026-06 robustness work; kept here because it shapes how every
  new save-site must be written.)
