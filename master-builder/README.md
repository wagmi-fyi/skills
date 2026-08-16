# master-builder

A meta skill: it builds skills. Your agent interviews you about a workflow, scaffolds the smallest skill that works, and hardens it through real runs. You direct the build, judge the result, and request improvements.

## Install

Place this folder (or a symlink to it) in your harness's global skill directory:

```
~/.claude/skills/master-builder
```

On systems using the AGENTS.md convention, use `~/.agents/skills/` instead. Start a fresh agent session so the skill is indexed.

## First Run

Tell your agent:

> Use master-builder. Interview me about my [workflow] and build the first draft of the skill.

The interview runs until the agent is ~95% confident it understands your intent. Expect it to propose something smaller than you imagined — that's the point. Complex systems that work grow out of simple systems that work.
