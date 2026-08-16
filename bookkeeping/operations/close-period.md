# close-period

## Overview

Intent: Verify all bookkeeping work for the period is complete, resolve outstanding items, and seal the workpaper.

The close-period operation orchestrates the full closing process: resolving the period, checking prior period context, running all required domains, verifying completeness, and finalizing the workpaper with insights and carry-forward items.

## Period Resolution

Accept period in three formats:

- **Calendar-monthly**: `YYYY-MM` (auto-expands to full month: first day to last day)
- **Fiscal**: `YYYY-PNN` (requires calendar lookup from `{local_dir}/content/period-close/fiscal-calendar.yaml`)
- **Date-range**: `YYYY-MM-DD_YYYY-MM-DD` (explicit start and end dates)

Use `scripts/_shared/period_resolver.py` for resolution logic:

1. Parse input format
2. For fiscal periods, validate fiscal calendar path exists in config, load calendar, lookup period
3. For date ranges, validate both dates are valid ISO format and start precedes end
4. For calendar-monthly, compute first and last day of month
5. Resolve to canonical form: `{periodLabel, periodStart, periodEnd, periodType}`
6. Present resolved period to user and wait for confirmation before proceeding

If fiscal calendar is required but missing or period not found in calendar, stop with error.

## Prior Period Check

Discover prior period by scanning subdirectories in `{workpapers_dir}/period-close/` — each subdirectory is named `{periodLabel}` and contains `{periodLabel}.md`:

1. Read frontmatter from each workpaper to get `periodEnd` and `status`
2. Filter to workpapers with `status: 'complete'` or `status: 'in-progress'`
3. Exclude current period workpaper
4. Find workpaper whose `periodEnd` is most recent date before current `periodStart`

If prior period found:

- Check for gap between prior `periodEnd` and current `periodStart` (informational, not blocking)
- Scan for carry-forward items: domains with status other than complete (see Domain Orchestration for what counts as complete), Notes section content (especially items flagged for future periods), open to-do items or unresolved questions
- Present findings and wait for user confirmation before capturing to current workpaper

If no prior period found (first close scenario), present "This appears to be the first close" and skip carry-forward gracefully.

## Workpaper Resume Detection

Before initializing new close, check for existing workpaper at `{workpapers_dir}/period-close/{periodLabel}/{periodLabel}.md`:

- If exists AND has entries in `stepsCompleted` array (a close-period-owned key — see Workpaper section), route to continuation logic
- If exists but `stepsCompleted` is absent or empty, close-period has not run — treat as new (the workpaper may still carry domain progress from `process-period`; orchestration reads `domains_status` and picks up from there)
- Continuation routing uses state-based decision tree:
  - If step-02 not complete: go to step-02 (prior period)
  - If step-02 complete but any domain not complete: go to step-03 (orchestration)
  - If all domains complete but step-04 not complete: go to step-04 (verification)
  - If step-04 complete: go to step-05 (finalization)

## Domain Orchestration

Execute fixed sequence of 9 domains in order. Statuses live in the workpaper's `domains_status` frontmatter — the same contract `process-period` writes:

1. **GA** - Gather (`gather`)
2. **IN** - Ingest (`ingest`)
3. **TA** - Trade Accounts (`trade-accounts`)
4. **AP** - Apply Payments (`apply-payments`)
5. **CD** - Categorize (`categorize`)
6. **MJ** - Manual Journals (`manual-journals`)
7. **RV** - Review (`review`)
8. **CQ** - Client Questions (`client-questions`)
9. **PB** - Publish (`publish`)

A domain counts as **complete** when its status is `completed`, `completed-with-flags`, or `completed-with-overrides` — flags and overrides are surfaced in the verification report, never silently absorbed.

Orchestration loop:

1. Re-read workpaper state (state may have changed between sessions)
2. Present status dashboard showing: #, Code, Domain, Status for all 9 domains
3. Identify next domain: first in sequence with status `not-started`, `in-progress`, or `failed`
4. If all 9 are complete, mark step-03 complete and proceed to verification
5. Otherwise, offer three-way handoff choice:
   - **[S] Same session**: Execute domain in current conversation, read quality gate result, update workpaper, loop back
   - **[F] Fresh session**: Provide explicit instructions to start new conversation, invoke skill, run domain, return to resume. Update lastStep and stop.
   - **[X] Exit**: Save progress and resume later

Loop continues until all 9 domains are complete.

If a domain fails or is interrupted, track state in `lastStep` frontmatter field for interruption recovery.

## Verification

Safety check confirming orchestration completion:

1. Re-read workpaper
2. Check all 9 domains in `domains_status`
3. If ANY show `failed`, `not-started`, or `in-progress`, list which ones need attention and route back to step-03 orchestration
4. Only proceed to finalization if ALL 9 are complete
5. Present full quality gate report for all domains — including every flag and override carried by `completed-with-flags` / `completed-with-overrides`

**Legacy workpapers:** workpapers created before the Gather domain existed have no `gather` key in `domains_status` — treat it as not-applicable (pre-gather era), never as incomplete. Only workpapers instantiated from the current template are held to it.

Verification is blocking. Do not proceed to finalization if any domain is incomplete.

## If Incomplete

Don't force premature closure. If verification reveals missing work:

1. Identify what's missing
2. Add to action items in workpaper
3. Either resolve (if possible in same session) or escalate to user
4. Route back to orchestration step to complete missing workflows

Goal is clean closure, not forced closure.

## Finalization

Before marking workpaper complete:

1. **Close insights**: Prompt for insights and client nuances discovered during close:
   - Client-specific quirks or patterns
   - Process improvements identified
   - Recurring issues or special handling
   - Anything to help future closes
   - Append to Notes section flagged as "Close Insights - {periodLabel}"

2. **Forward-looking notes**: Prompt for to-do items or notes specifically for next period:
   - Pending issues to revisit
   - Scheduled follow-ups
   - Known upcoming events or changes
   - Append to Notes section flagged as "For next period"

3. **Workpaper finalization markers**: Update frontmatter:
   - Set `status: 'complete'`
   - Add 'step-05-finalize' to `stepsCompleted`
   - Set `lastStep: 'step-05-finalize'`
   - Set `finalizedDate` to current date

4. **Knowledge capture proposal**: Review the close process and propose updates to content files:
   - New patterns identified for `{local_dir}/content/learned-patterns/`
   - Client-specific context for `{local_dir}/content/client-context/`
   - Quality guideline refinements based on this close

## Workpaper

Uses period-close workpaper at `{workpapers_dir}/period-close/{periodLabel}/{periodLabel}.md` (template: `{module_root}/templates/period-close-workpaper.md`)

**Template-provided frontmatter** (written by `process-period`, read here):

- `client_name`, `client_id` (from config)
- `periodLabel`, `periodStart`, `periodEnd`, `periodType`
- `status` (in-progress → complete)
- `createdDate`
- `domains_status` (map of the 9 domains — the single domain-status contract shared with `process-period`)
- `action_items`, `roadblocks`

**Close-period-owned frontmatter** (added by this operation when it first runs):

- `stepsCompleted` (array tracking progress through close steps)
- `lastStep` (for interruption recovery)
- `finalizedDate` (set at finalization)

Replace template variables when creating new workpaper:
- `{{client_name}}`, `{{periodLabel}}`, `{{periodStart}}`, `{{periodEnd}}`
- Set `createdDate` to current date

## Config Validation

At initialization, validate config.yaml:

- Check `client_name` and `client_id` are populated
- If empty, stop with message directing user to set values before running period close
- This is blocking validation

## References

- `{local_dir}/reference/quality-guidelines.md` - Quality standards for all domains
- `{local_dir}/reference/bookkeeping-principles.md` - Core bookkeeping principles
- `{local_dir}/content/period-close/fiscal-calendar.yaml` - Fiscal period definitions (if used)
- `scripts/_shared/period_resolver.py` - Period resolution logic
