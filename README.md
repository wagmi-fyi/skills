# skills

Agent skills published by WAGMI.

A skill teaches an agent how to do a real job: what to load before it starts, what conventions
hold, how it checks its own work. These are the ones we run ourselves. Each one grew out of real
work rather than a specification, and each one keeps growing that way.

Parts of this depend on Claude Code. `orchestrate` starts its delegate sessions with the `claude`
CLI, and it carries one runbook per way of running them: background agents, or panes in tmux. It
says what each way needs and refuses when the mechanism is absent, so you find out before a run
rather than during one. The skill descriptions say where a dependency applies. The rest expect an
agent harness that can read a skill and run a script.

## What's here

| Skill | What it does |
|---|---|
| [`bookkeeping`](bookkeeping/) | Ingest, categorize, reconcile and publish a client's financial data. Period closes, trade accounts, bank feeds, QuickBooks as the system of record |
| [`master-builder`](master-builder/) | Build and improve skills. It interviews you about a workflow, scaffolds the smallest skill that works, then hardens it over real runs |
| [`orchestrate`](orchestrate/) | Run one job across several agent sessions. One session decomposes and verifies, the others do the work in visible panes you can reach, and a built-in message bus carries the traffic |
| [`qbo`](qbo/) | Read and write QuickBooks Online. OAuth and token refresh, entity queries, and the writes that `bookkeeping` publishes through |

`bookkeeping` reaches QuickBooks through `qbo`. Its QuickBooks adapters fail to import unless
`qbo` is installed, so install both if you keep the books in QuickBooks.

## Install

On Claude Code, the plugin below installs all four skills in one command and keeps them current.
Any other agent copies the directories in by hand.

### As a Claude Code plugin

```
/plugin marketplace add wagmi-fyi/skills
/plugin install wagmi-skills@wagmi
```

Restart Claude Code afterwards. A plugin puts its skills under its own name, so `bookkeeping`
arrives as `wagmi-skills:bookkeeping`. Describe the work you want and the right one fires on its
own, or name it that way to ask for it directly.

All four install together. `bookkeeping` finds `qbo` beside it that way, which is what its
QuickBooks adapters need.

Run `/plugin update wagmi-skills` to take a later version. The version is the commit this
repository is on, so an update gives you whatever the most recent publish put here. Run
`/plugin uninstall wagmi-skills` to remove them.

### By hand, on any agent

A skill is a directory holding a `SKILL.md`. Put the directory where your agent looks for skills,
either as a copy or as a symlink:

| Agent | Location |
|---|---|
| Claude Code | `~/.claude/skills/<name>` |
| Anything on the AGENTS.md convention | `~/.agents/skills/<name>` |
| One project only | `.claude/skills/<name>` or `.agents/skills/<name>` in the repo |

Start a fresh session afterwards so the skill gets indexed. Then ask for it by name.

Keep the directory name as it ships. The format requires the `name` in a skill's frontmatter to
match its parent directory, so a rename on the way in breaks the skill.

Some skills carry their own dependencies or expect a companion skill. Read the `SKILL.md` before
first use; anything a skill needs is stated there.

## Format

These follow the [Agent Skills specification](https://agentskills.io/specification), which is the
same format Claude Code and Codex read. Nothing here is specific to one agent product beyond the
install path.

## Bank feeds

`bookkeeping` pulls bank feeds through a broker service. The default endpoint is
`https://auth-my-accountant.vercel.app`, which WAGMI operates. Requests to it carry an
`AMA_FIRM_API_KEY` that WAGMI issues, so no data reaches that service without a key we handed you.
Set `AMA_API_URL` to send the adapter somewhere else.

## Licensing

Apache-2.0 covers this repository, and the text is in [LICENSE](LICENSE).

The format lets a skill declare its own license in frontmatter, so a directory can carry different
terms from the repository default. Where a `SKILL.md` names a license, that is the one that governs
that skill. Everything published so far is Apache-2.0.

## Contributing

This is a published library. These are the skills WAGMI runs, and they ship as they are.

Issues are welcome. A skill doing the wrong thing on your books is worth telling us about.

Pull requests are not accepted yet. Support for them is coming shortly.
