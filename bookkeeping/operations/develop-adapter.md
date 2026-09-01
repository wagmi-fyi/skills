# Operation: Develop Adapter

Build or modify a data adapter — code that transforms data into or out of the Bookkeeping pipeline. Adapters connect the core processing scripts to external systems (bank CSVs, accounting APIs, revenue platforms, payroll systems) and client-specific data formats.

## Intent

Turn user requirements into working adapter code. Handle both new adapter development and modifications to existing adapters. Support four adapter domains: ingest, trade-accounts, manual-journals, and publish.

---

## Two Execution Modes

The operation adapts to how the user presents the work:

### Mode A: Spec-First (Tech Spec Provided)

User provides a path to an existing tech spec file. This is implementation-only mode — planning is complete, proceed directly to execution.

**Flow:** Load spec → Execute tasks → Self-check → Adversarial review → Resolve findings

### Mode B: Direct Instructions (No Spec)

User provides direct instructions without a formal spec. Determine whether to plan first or execute directly.

**Flow:** Assess clarity → [Plan first OR Execute directly] → Self-check → Adversarial review → Resolve findings

**Decision point:** After mode detection, present menu:
- [P] Plan first — Exit to spec-first workflow (adapter-spec) to create a tech spec
- [E] Execute directly — Proceed with direct implementation

ALWAYS halt and wait for user input at this menu.

---

## Adapter Resolution Protocol

When locating or creating adapters, resolution follows this order:

1. **Local flat file:** `{local_dir}/adapters/{domain}/{adapter_name}.py`
2. **Local directory:** `{local_dir}/adapters/{domain}/{adapter_name}/`
3. **Firm flat file:** `{firm_root}/adapters/{domain}/{adapter_name}.py`
4. **Firm directory:** `{firm_root}/adapters/{domain}/{adapter_name}/`
5. **Shared flat file:** `adapters/{adapter_name}.py`
6. **Shared directory:** `adapters/{adapter_name}/`
7. **Error with guidance:** If none found, provide clear message about expected locations

If `{firm_root}` is not set, skip steps 3-4.

**Domains:** `ingest`, `trade-accounts`, `manual-journals`, `publish`

**Placement decision:**
- Client-specific adapters → `{local_dir}/adapters/{domain}/`
- Firm-shared adapters → `{firm_root}/adapters/{domain}/`
- Universal adapters → `adapters/` (published with core)

---

## Adapter Conventions

All adapters follow these patterns (enforced during self-check and adversarial review):

### Script Naming
- snake_case, verb-first
- Examples: `parse_chase_csv.py`, `sync_to_qbo.py`, `fetch_stripe_deposits.py`

### Output Contract
Structured JSON to stdout, progress to stderr:

```json
{
  "summary": "What was accomplished",
  "data": { "results": "..." },
  "next_steps": ["Suggested follow-up"]
}
```

Error output:
```json
{
  "status": "error",
  "message": "Human-readable description",
  "suggestion": "What to try next"
}
```

### Config Loading
Scripts find config via `BOOKKEEPING_CONFIG_PATH` environment variable:

```python
import os, sys, yaml

_config_path = os.environ.get('BOOKKEEPING_CONFIG_PATH')
if not _config_path:
    raise RuntimeError("Set BOOKKEEPING_CONFIG_PATH to config.yaml path")
```

Core scripts use `config_loader.load_config()`. Local adapters bootstrap the module root from config first.

### Fail-Fast Philosophy
- No retry logic (the caller decides recovery)
- Fail loudly with clear errors (no swallowed exceptions)
- Let external systems validate (API errors bubble up)
- Minimal input validation (the caller reads the error and corrects the call)

### Atomic Operations
One script = one thing. No monolithic workflows combining multiple steps. When step 4 fails, the caller has to be able to tell that it was step 4.

---

## Development Flow

### 1. Understand the Requirement

**Mode A:** Load tech spec, extract problem statement, solution, tasks, acceptance criteria.

**Mode B:**
- If user instructions are clear and specific: gather context, present plan, confirm
- If requirements need exploration: recommend planning mode ([P] option from menu)

**Context to gather:**
- Data source (format, API, credentials)
- Adapter type (ingest, trade-accounts, manual-journals, publish)
- Code placement (local vs shared)
- Existing patterns (search for similar adapters)
- Quality gate requirements (what does "correct" look like?)

**Load project context:** Check for `**/project-context.md` and incorporate if found.

### 2. Gather Implementation Context

**Before writing code:**

Search for relevant existing code:
- Similar adapters in the same domain
- Import patterns and shared utilities
- Error handling approaches
- Test patterns

Infer patterns from existing adapters:
- Code style (formatting, naming, structure)
- Output contract usage
- Config loading patterns
- Database interaction patterns

Verify placement in correct layer:
- Core scripts (`scripts/`) — pure function logic, same for all clients
- Shared adapters (`adapters/`) — integration-specific, reusable
- Local adapters (`{local_dir}/adapters/`) — client-specific overrides

**For Mode B only:** Present plan with:
- Files to modify
- Patterns identified
- Tasks to complete
- Inferred acceptance criteria

Halt and wait for user confirmation (y/n/adjust).

### 3. Execute Implementation

**Continuous execution policy:** Execute all tasks in sequence without stopping between tasks. Only halt for blocking issues:
- 3 consecutive failures on the same task
- Tests fail and fix is not obvious
- Blocking dependency discovered
- Ambiguity requiring user decision

Do NOT halt for minor issues, warnings, or style preferences.

**When running Python scripts:**

```bash
BOOKKEEPING_CONFIG_PATH={project-root}/_local-bookkeeping/config.yaml python script.py [args]
```

Never hardcode config paths inside scripts.

**Track progress:**
- Mark tasks complete as `- [x] Task N`
- Update tech spec status if Mode A

### 4. Self-Check

Before proceeding to adversarial review, verify:

**Tasks complete:**
- [ ] All tasks marked [x]
- [ ] No skipped tasks

**Tests passing:**
- [ ] Existing tests still pass
- [ ] New tests written (if needed)

**Acceptance criteria satisfied:**
- [ ] Each AC demonstrably met
- [ ] Edge cases considered

**Patterns followed:**
- [ ] Code style matches existing patterns
- [ ] Project-context rules followed (if present)
- [ ] Error handling consistent with codebase

**Adapter conventions:**
- [ ] Output contract (structured JSON to stdout, progress to stderr)
- [ ] BOOKKEEPING_CONFIG_PATH usage (no hardcoded paths)
- [ ] Three-layer placement verified
- [ ] No defensive over-engineering
- [ ] Atomic operations (one script = one thing)
- [ ] Quality gates defined where needed

**If Mode A:** Update tech spec status to "Implementation Complete".

Proceed immediately to adversarial review (no user interaction).

### 5. Adversarial Review

**Construct diff:**

Capture baseline commit at start of workflow:
```bash
git rev-parse HEAD  # or "NO_GIT" if not a git repo
```

At review time, generate diff:
- If Git commit hash: `git diff {baseline_commit}`
- If "NO_GIT": best-effort diff showing modified and new files
- Manually include new files created during workflow (not pre-existing untracked)
- Read-only inspection — do NOT `git add` anything

**Run review:**

Ideally run in separate subagent with read access to project but no context except the diff. If not possible, follow review instructions inline.

Reference: `reference/adversarial-review.md`

**Evaluate findings:**

Classify by severity (Critical, High, Medium, Low) and validity (real, noise, undecided). Order by severity. Number as F1, F2, F3.

**Zero findings is suspicious:** HALT if adversarial review returns zero findings. Re-analyze or request user guidance.

**Present findings:**
- If TodoWrite available: turn each finding into TODO with ID, severity, validity, description
- Otherwise: present as table with columns: ID, Severity, Validity, Description

Do NOT exclude findings based on severity/validity unless explicitly asked.

Proceed immediately to resolve findings (no user interaction).

### 6. Resolve Findings

Present three-option menu:

- [W] Walk through — Discuss each finding individually
- [F] Fix automatically — Auto-fix issues classified as "real"
- [S] Skip — Acknowledge and proceed without fixes

ALWAYS halt and wait for user selection.

**Walk-through mode [W]:**

Process each finding in order:
1. Present finding with context
2. Ask: "fix now / skip / discuss"
3. Apply fix if requested
4. Note if skipped
5. Provide more context if discuss
6. Move to next finding
7. Summarize after all processed

**Auto-fix mode [F]:**

Filter to findings classified as "real", apply fixes automatically. Report what was fixed and what was skipped (noise/uncertain).

**Skip mode [S]:**

Acknowledge review without applying fixes.

**If Mode A:** Update tech spec status to "Completed" and add review notes section:
- Adversarial review completion status
- Findings count (total/fixed/skipped)
- Resolution approach (walk-through/auto-fix/skip)

---

## References

- `reference/adapter-patterns.md` — adapter development patterns and conventions
- `reference/capability-registry.md` — available adapters and their capabilities
- `reference/adversarial-review.md` — structured review process
- `templates/tech-spec-template.md` — template for Mode B planning
- `reference/schema.sql` — database schema (if adapter touches DB)

---

## Quality Mindset

Write agent-first code. A script fails loudly, does one thing, and leaves the sequencing to its caller. Skip the defensive layers. An error whose handling needs context from outside the script belongs outside the script. The adapter's job is to transform data reliably, report the result clearly, and get out of the way.
