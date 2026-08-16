# Bookkeeping Principles and Constraints

## Principles
(Guide decision-making across contexts)

1. **Automation scales with confidence** — Higher certainty enables more autonomy. Config-driven thresholds define the zones: auto-approve, judgment call, client question. References: `coding.min_confidence_to_categorize`, `coding.min_confidence_to_auto_approve`. High confidence (9-10) requires exact historical match, clear vendor ID, consistent account usage, no ambiguity. AI-inferred matches are capped at medium or low regardless of signal quality. **Auto-categorization rules are only for the auto-approve zone (confidence ≥ `min_confidence_to_auto_approve`).** If a pattern doesn't meet that bar, it should be coded via AI categorization with an appropriate confidence score — not enshrined as a rule that bypasses review.

2. **Deduplication across sources** — The same economic event may appear in multiple data sources. Identify and link them to prevent double-counting (e.g., CC payments appearing as both bank withdrawals and credit card charges). Inter-account transfers must be detected before bulk categorization and processed via dedicated transfer handling.

3. **Learning from patterns** — Rule creation follows research, not the other way around. The coding workflow proceeds from certainty toward uncertainty: apply existing rules → code high-confidence transactions → research medium and low confidence → code based on gathered information if above threshold → flag remainder as client questions. **Stay in flow during coding** — when patterns emerge that would qualify for auto-cat rules, note them in the workpaper and continue coding. Circle back to build, test (`test_cat_rule.py`), and activate rules after the coding pass is complete. Patterns worthy of rules must meet: (1) confidence ≥ `min_confidence_to_auto_approve`, (2) second or later occurrence of same vendor pattern, (3) consistent account code across historical transactions, (4) clear match criteria can be formulated without false positive risk.

4. **Plain-language client communication** — Translate accounting into questions the client can answer. Group by topic, provide context, never use raw account codes. Explain rationale when presenting matches in low-confidence scenarios.

5. **Idempotent publishing** — If interrupted and resumed, never double-post entries in the SoR. Track what has been published. Duplicate prevention queries check existing records before creating entries.

6. **Granular verification** — Quality verification must be granular enough to catch per-account errors. Don't verify only at the aggregate level. Quality gates check both workpaper item statuses and database cross-reference queries, ensuring alignment between documentation and actual records.

7. **Context-driven behavior** — Same capability behaves differently in onboarding (guided, slow, more questions, wait for user confirmation) vs recurring close (efficient, autonomous, brief displays, auto-proceed) vs standalone (collaborative). Detection uses parent workpaper frontmatter to identify context.

8. **Error resilience without silent failures** — Individual failures are logged and flagged but do not stop processing. Continue through all operations, compile both successes and failures for quality gate. Each outcome must be reported; no silent failures.

9. **Registry-driven matching with AI fallback** — When registry exists, use explicit patterns with high confidence. When missing, fall back to AI inference with lower confidence expectations and more flagged items. Inform user of mode and expected accuracy.

10. **Knowledge accumulation is collaborative** — Propose updates to content files only when all criteria met: (1) would change future agent behavior, (2) not already captured in existing content/config/registry, (3) expressible in 1-3 sentences without losing key detail. User must approve/edit/skip each proposal; never auto-write.

11. **Period-based state and continuation** — Workpapers track progress via frontmatter (stepsCompleted, subWorkflows status). State-based routing enables interruption recovery. Always re-read workpaper before presenting dashboard, as state may have changed between sessions.

12. **Evidence makes workpapers self-sufficient** — Quality gate evidence must include per-entry detail granular enough that a manager can verify completion without querying the database. Evidence includes JE IDs, dates, memos, accounts, amounts, balance verification.

## Constraints
(Non-negotiable rules — must be obeyed)

1. **Period boundaries** — Every period must have unambiguous start and end dates. Fiscal periods require calendar lookup from `{local_dir}/content/period-close/fiscal-calendar.yaml`. Three accepted formats: `YYYY-MM` (calendar-monthly), `YYYY-PNN` (fiscal), `YYYY-MM-DD_YYYY-MM-DD` (explicit range). User confirmation required after period resolution.

2. **Balance reconciliation is a trust checkpoint** — Never auto-proceed past a mismatch without human decision. See Hard Stops in quality-guidelines.md. Each journal entry must balance (total_debits == total_credits), enforced at creation and re-verified in quality gate.

3. **Review is read-only** — Review analysis flags and escalates, never modifies the database. Quality gates are objective verification that checks evidence, reports findings, and routes accordingly. Quality gates do not fix problems.

4. **Anti-fabrication** — All financial figures must come from script outputs or source documents. The agent never fabricates financial data. Every account code must be validated against loaded chart of accounts before assignment.

5. **Precondition blocking** — Orchestrated workflows (period-close, onboarding) enforce preconditions: ingest must complete before coding, coding must complete before manual journals, both ingest and trade-accounts must complete before apply-payments. Standalone contexts skip precondition checks.

6. **Processing order for complex operations** — Execute in specific sequence when dependencies exist: inter-account transfers detected before bulk categorization, direct payment matches before clearing account matches before bulk payments, rules application before AI research.

7. **Mandatory quality gate pass criteria** — Every workflow has explicit, measurable quality gate criteria. All criteria must pass for workflow completion. Flagged items documented as exceptions do not prevent gate pass, but unresolved processing failures do.

8. **Workpaper updates only on success** — Write completion status to parent workpaper frontmatter only when quality gate passes. On failure, write failure status with evidence but no completedDate. Re-run contexts append rather than replace to preserve history.

9. **No modification without verification** — After executing database operations (categorization, payment application, journal entry creation), query database to verify records were created as expected. Mark operations failed if verification doesn't pass.

10. **Session limit awareness** — Context accumulation has limits. If pattern count exceeds 5, warn user about context strain and offer options to defer to fresh session. Provide file paths for standalone continuation.

11. **A bank line can settle multiple items; never synthesize credits at publish** — A bundled bank transaction is N postings that net to one bank line. The skill provides distinct capabilities for handling the non-settlement postings (a standalone object that nets to the bank via `apply_payments_bulk --standalone_lines`; a first-class credit_memo/vendor_credit batched into a settlement with `--allow_mixed_credit`); the processing agent selects per situation. Publishers must never reverse-engineer a credit memo / vendor credit from clearing-JE postings — that mis-attributes the counterparty and breaks when the amount meets or exceeds the payment principal.

12. **Staging must reconcile to the SoR at both ends of the period** — Open the period by reconciling the local ledger through the prior close against the system of record, and reconcile again before commit. The SoR is not a write-only mirror: it can hold records staging never created — entries edited or added directly in the SoR, system-generated artifacts (e.g. an auto-applied credit), or a prior sync that failed to land. The opening tie catches drift since the last seal; the closing direct-record detect catches SoR records dated in the period that the pipeline did not produce, *before* Publish, so the publisher cannot create a duplicate of something the SoR already holds. Both are reconciliations to resolve, not variances to wave through — see Hard Stops 6 and 7 in `quality-guidelines.md`. The mechanism is SoR-agnostic; the as-of tie and the direct-record scan live in the SoR's reconcile adapters (for QBO, `adapters/qbo/reconcile_trial_balance.py` and `adapters/qbo/scan_sor_direct_records.py`).
