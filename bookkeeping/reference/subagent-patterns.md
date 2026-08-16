# Subagent Patterns

Sub-agents are writers, not reporters. They write detailed output to files and return only a structured summary line. The orchestrator never receives raw analysis — it receives pointers and counts.

## When to Use

When work can be split into independent units and processed in parallel — bulk file extraction, multi-period processing, or any task where a single agent would strain context or output limits.

## Principles

- **Output goes to files.** Large results (data, JSON, reports) get written to disk. The agent returns a summary: what was produced, where it lives, how much, and anything unexpected.
- **Boundaries are explicit.** Each agent receives the exact scope of its work — date ranges, cutoffs, what's already been processed. Don't rely on the agent to infer boundaries from context.
- **Prefer scripts for volume.** When output is large enough to risk token limits, have the agent generate and run a script rather than producing data inline.
- **Launch in parallel.** Use `run_in_background: true` and issue all independent agent calls in a single message.
