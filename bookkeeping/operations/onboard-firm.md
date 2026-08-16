# Operation: Onboard Firm

First-time firm setup — from zero to ready-to-onboard-clients. Discover the firm's identity, bookkeeping philosophy, standards, team, and preferences through conversation. Capture what matters in persistent context that all clients will inherit.

## Firm Directory

All firm context lives at `{firm_root}` (typically `~/.claude/bookkeeping-firm/`):

```
{firm_root}/
├── config.yaml              # Firm identity, defaults, content manifest
├── content/                 # Discovered firm knowledge (structure emerges from discovery)
├── adapters/                # Firm-shared adapters (if applicable)
│   └── {domain}/
├── reference/               # Firm additions to core quality/review guidance (if applicable)
└── templates/               # Firm starter content for new client onboarding (if applicable)
```

Only `config.yaml` has a fixed schema. Everything else is created based on what the discovery phase reveals.

## Workpaper

Create at `{firm_root}/onboarding-workpaper.md`. Tracks discovery progress, decisions made, files created. Follow the same session-close protocol as client onboarding — update before ending every session.

## State Artifacts

| File | Contains | Updated When |
|------|----------|-------------|
| `{firm_root}/onboarding-workpaper.md` | Discovery progress, decisions, files created | Every session |
| `{firm_root}/config.yaml` | Firm identity, defaults, content manifest | Capture phase and ongoing |
| `{firm_root}/content/*` | Discovered firm knowledge files | Capture phase and ongoing |

## Session Close Protocol

**Before ending any session**, update all state artifacts so the next session can resume without re-discovering context:

1. **Workpaper** — Update current phase, what was discovered, what was captured, what remains. Include enough detail for a fresh session to resume.
2. **Config** — Ensure `content_manifest` reflects all created files.
3. **Content files** — If insights were surfaced but not yet written, capture them now.

**The test:** Could a fresh agent, reading only the workpaper and config, resume the onboarding without asking the user to repeat anything?

---

## Phases

Phases are sequential. Each produces artifacts the next consumes. Within each phase, reason dynamically about what this specific firm needs.

### Phase: Discover — Understand the firm's practice

**Intent:** Build a genuine understanding of how this firm does bookkeeping. Not a questionnaire — a conversation.

**Mindset:** Be genuinely curious. Pull on open strings. Ask follow-up questions. Probe for things the user might not think to mention. Walk through topics conversationally — one or two questions at a time, thinking about each response before going deeper.

**Areas to explore (not a fixed list — follow what matters):**

- **Firm identity** — Name, size, specialization, client types, geographic focus, engagement model. What kind of businesses do they serve? What's their niche?
- **Bookkeeping standards** — Basis (cash, accrual, modified accrual), how strictly enforced, how they handle edge cases. What's "good enough" vs. what's unacceptable? Are standards consistent across all clients or do they flex?
- **Revenue recognition** — Policies, when to recognize, how to handle deferred revenue, deposits, retainers, progress billing. Standardized across clients or varies by industry?
- **Accrual policies** — What gets accrued, frequency, materiality cutoffs, month-end vs year-end treatment. Any firm-wide thresholds (e.g., "accrue anything over $500")?
- **System of record** — Do they standardize on one SoR (QBO for everyone)? Allow client choice? Have migration policies? Why that SoR?
- **COA philosophy** — Standard template they customize per client? Fully custom each time? Industry-specific templates? How granular (e.g., sub-accounts for every vendor, or keep it simple)?
- **Quality standards** — What does a "clean close" look like? Review process, sign-off authority, hard stops vs. judgment calls. How do they handle discrepancies?
- **Team structure** — Who does what? How are clients assigned? Who reviews? Who handles escalations? What's the typical engagement team?
- **Integration preferences** — Standard tools, platforms, banks they commonly encounter. Firm-wide API credentials or per-client? Any firm-wide subscriptions?
- **Communication style** — How do they communicate with clients about questions? Batch or ad-hoc? Email, portal, phone? Tone and formality level?
- **Compliance and reporting** — Any jurisdiction-specific requirements? Sales tax handling? 1099 preparation? Industry-specific compliance?

**Do not treat this as a checklist.** Some firms have deep, specific standards on revenue recognition and barely think about COA. Others have rigid COA templates and flexible everything else. Follow the energy. If the user goes deep on accrual methodology, go deep. If they hand-wave at team structure, note the basics and move on.

**Produces:**
- Deep understanding of the firm's philosophy and practices
- Clear sense of what topics deserve their own content files vs. what's a brief note in config
- Identified firm templates, shared adapters, or reference content to create

Present a summary of discoveries and proposed content structure for user confirmation before moving to Capture.

---

### Phase: Capture — Write firm context

**Intent:** Turn discovered knowledge into persistent, well-organized files that the system will actively reference during client work.

**Mindset:** Write for a future agent, not the current user. The content should be self-contained enough that a fresh session can understand the firm's philosophy without re-asking.

**Always created:**
- `config.yaml` — Firm identity, defaults, and `content_manifest`

**Created based on discovery — whatever topics emerged as significant:**
- Content files under `content/` — one file per significant topic area. Use clear, descriptive filenames (e.g., `accrual-standards.md`, `team-roster.yaml`, `coa-philosophy.md`, `sor-policies.md`, `quality-expectations.md`).
- Templates under `templates/` — if the firm has standard starter content for new clients (COA templates, checklist additions, onboarding extras). These get copied to client content during client onboarding.
- Adapters under `adapters/` — if the firm has shared integrations (e.g., firm-wide QBO app credentials).
- Reference under `reference/` — if the firm has additions to core quality or review guidance that should layer on top of core reference during processing.

**Key behaviors:**
- **Create files based on what was discovered** — don't create empty templates for things that weren't discussed
- **Register every file in `content_manifest`** in config.yaml — this is what makes the content discoverable by other operations
- **Use semantic keys** in the manifest that map to how operations look them up (e.g., `team`, `bookkeeping_standards`, `coa_philosophy`, `sor_policies`)
- **Present each proposed file** for user confirmation before writing — don't batch. Walk through them, explaining what goes where and why.

### Firm Config Structure

```yaml
# Firm identity
firm_name: ""
firm_id: ""

# Defaults (inherited by new clients unless overridden in client config)
default_system_of_record: ""
default_period_type: ""
default_coding:
  min_confidence_to_categorize: 5
  min_confidence_to_auto_approve: 9

# Communication
communication_language: "English"
document_output_language: "English"

# Content manifest — what was discovered and where it lives.
# Populated during Capture, updated as firm knowledge evolves.
# Operations check this manifest to find available firm context.
content_manifest: {}
#  Example:
#    team: "content/team-roster.yaml"
#    bookkeeping_standards: "content/bookkeeping-standards.md"
#    coa_philosophy: "content/coa-philosophy.md"
#    sor_policies: "content/sor-policies.md"
```

---

### Phase: Verify — Confirm readiness

**Intent:** Could a fresh agent, reading only `{firm_root}/`, understand this firm well enough to onboard a new client with firm-appropriate defaults and context?

**Checks:**
- `config.yaml` has firm identity and meaningful defaults
- `content_manifest` registers every content file that was created
- Every manifest entry points to a file that exists and has substantive content
- Content files are written for agent consumption — clear, unambiguous, actionable
- If templates were created, they're ready to use during client onboarding
- If adapters were created, credentials are configured
- The firm context, combined with core, provides enough guidance to start a client onboarding with firm-appropriate defaults

If anything is incomplete, route back to Capture rather than forcing forward.

---

## Ongoing Evolution

Firm context isn't static. As the firm works with more clients, patterns emerge:

- A coding rule keeps appearing across clients → offer to promote to firm-level content
- A review procedure gets refined through practice → update firm reference
- A new team member joins or roles change → update team roster
- The firm adopts a new standard or policy → update the relevant content file
- A new shared adapter is developed → add to firm adapters

When `process-period` or `onboard-client` discovers something that's firm-level (not client-specific), the agent should note it and offer to update firm context. The `content_manifest` must be updated whenever content files are added or removed.

---

## References

- `templates/config-template.yaml` — client config structure (firm defaults flow into this)
- `reference/knowledge-capture.md` — patterns for capturing and storing learned knowledge
- `reference/adversarial-review.md` — structured challenge process for plans and outputs

## Quality Mindset

Firm onboarding sets the foundation for every client engagement. Invest the time to understand deeply now — it pays dividends across every client. Prefer more questions over fewer. Prefer capturing a nuance now over re-discovering it per client. The goal is firm context rich enough that client onboarding starts with informed defaults instead of blank slates.
