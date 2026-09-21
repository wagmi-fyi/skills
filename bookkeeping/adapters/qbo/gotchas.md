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

- **A deposit that consumes a credit memo posts as ONE Payment, net of the credit.** The credit
  arrives as a bank-funded CM-consume TAP: parent `type = 'credit_memo'`, `source_ta_id` NULL,
  `import_id` set. Phase 3b emits `TotalAmt = ΣR − ΣCM` with one Invoice line per invoice at face
  plus one CreditMemo line. The group key is `deposit_group_key` in `_shared/common.py`: the
  parent's `payout_id` where a batching channel stamps one, otherwise the payment's `import_id`
  and the parent's contact. One bank line paying two customers is two Payments. A settlement keeps
  its own path, because its invoice payments already carry cash net of the credit.

- **A bank-funded payment row no phase can post whole holds back the payment phases.**
  `find_bank_funded_payment_gaps` names each row by id, in the dry run and the live run, before
  anything posts. `PAYMENT_MATCHES_NO_PHASE`: no selection reads the row, and a bank-funded vendor
  credit is the case to expect. `DEPOSIT_GROUP_SPLIT`: a credit memo and the invoices it reduces
  carry different keys, so the invoices would post at full face. The codes of
  `check_consumed_credit_group`: the deposit cannot become one Payment. Both docstrings carry the
  detail. Every other phase publishes, and the run reports `success: false` with each gap in
  `errors`. Most gaps are a metadata fix: give the credit memo and its invoices one contact and
  one key, then run again. A row no metadata change can route has no remedy in the skill
  today, and that book publishes no bank-funded payments until the row is dealt with. Raise it.

- **A bank-funded credit memo nothing will post holds back the payment phases.** The
  consumed-credit selection reads the one sync status the run was given.
  `DEPOSIT_CREDIT_OFF_STATUS` names each invoice row on that credit's bank line, and the
  message gives the reason the credit row sits outside the run. Put the credit row in the
  run's status and run again when it never reached QuickBooks. When it did reach
  QuickBooks, its id belongs on the row, and the gate then passes the line. Setting the row
  to `ignore` passes the line too, because that says a person took responsibility for it.
  Check what the bank line should publish before you do that: nothing else stops the
  invoices posting for more than the bank received. No script in the skill sets either
  value today, which is the same gap as the unroutable row above. Raise it.

- **A run that dies can still re-post the one payment it was in the middle of.** In the
  three payment publishers, each row's outcome reaches the database before the next row
  reaches QuickBooks, so a crash costs that one payment. It had reached QuickBooks without
  its id reaching staging, and the next run posts it again.
  `scan_sor_direct_records.py` does not see such a duplicate, because it carries the
  `[bk:]` tag like any published object. The invoice, bill and credit-document publishers
  still save once at the end of their phase, so a crash there loses every id that phase
  wrote. After a run that is known to have died, the rows still unpublished are the ones to
  check, in `trade_accounts` as well as `trade_account_payments`.

- **A book whose invoices already posted at full face shows one credit memo in
  `PAYOUT_PARTIALLY_PUBLISHED`.** The bank is over by the credit, the CreditMemo floats at
  `RemainingCredit` equal to its face, and its payment row is still pending. The publisher cannot
  net a deposit whose invoices are already posted. Repair it by hand with the recipe below. Later
  deposits then publish whole on their own. `PAYOUT_GROUP_INCOMPLETE` is a different state: the
  credit memo's customer has no invoice on that bank line and nothing there has published.

- **Netting a credit into a Payment that already posted.** One sparse update on ONE of the
  deposit's QBO Payments: `TotalAmt = ΣR − ΣCM`, `Line = [Invoice LinkedTxn(face), CreditMemo
  LinkedTxn(face)]`. `CustomerRef` and `DepositToAccountRef` are both required on that update even
  though neither changes. The invoice stays at Balance 0, the bank drops by ΣCM, and the
  CreditMemo goes to Balance 0. Then set the local credit-memo payment row to `sync=ignore`, so no
  later run publishes it. This answers `PAYOUT_PARTIALLY_PUBLISHED`, whose message asks for manual
  reconciliation.

## Errors that lie

- **QB `10000`/some `6000` errors can post-then-fail.** The object may exist server-side despite
  the error response — read back by natural key before retrying, or a blind retry double-posts.
  (Handled in `_shared/` since the 2026-06 robustness work; kept here because it shapes how every
  new save-site must be written.)
