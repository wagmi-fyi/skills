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

## Installing for everyone on a machine

On a machine that several people use, install once for all of them. Install one shared copy of
the plugin in a directory that root owns. Then link each skill from that copy into the
machine-wide skills directory of each agent on the machine.

The Claude Code binary is installed per user, so root has none of its own. Run the plugin
commands with your own binary, as root, with `HOME` set to the shared directory:

```
sudo install -d -m 0755 -o root -g root <shared-dir>
sudo HOME=<shared-dir> <path-to-your-claude> plugin marketplace add wagmi-fyi/skills
sudo HOME=<shared-dir> <path-to-your-claude> plugin install wagmi-skills@wagmi
sudo chmod -R a+rX <shared-dir>
```

The clone is now at `<shared-dir>/.claude/plugins/marketplaces/wagmi`. Link each skill into
the machine-wide skills directory of every agent present. Each agent's documentation names
that directory for your operating system.

```
clone=<shared-dir>/.claude/plugins/marketplaces/wagmi
for s in bookkeeping master-builder orchestrate qbo; do
  sudo ln -sfn "$clone/$s" <machine-skills-dir>/$s
done
```

Check the result as another person on the machine. Root and the installer can read files that
others cannot.

```
sudo -u <member> -H test -r <machine-skills-dir>/orchestrate/SKILL.md && echo ok
```

Then have that person start a fresh session and ask for one of the skills by name.

To update, run one command. The links point into the clone, so each person gets the change at
their next session. The links change only when a skill is added.

```
sudo HOME=<shared-dir> <path-to-your-claude> plugin marketplace update wagmi
```

Here, "update the copy the way it was installed" under [Staying current](#staying-current)
means that command, and the admin runs it. A person's own agent cannot write the shared clone.

On Claude Code, a skill from a plugin carries the plugin's name, as in
`wagmi-skills:orchestrate`. So a person who also installs the plugin for themselves gets both
copies, and the two names do not collide.

Other routes fall short for this job:

- A per-user plugin install reaches one person, and the next person to join the machine has
  no skills.
- The plugin keys in Claude Code's managed settings file enable a plugin that is already
  installed. They install nothing.
- Claude Code's `--plugin-dir` flag lasts for one invocation.
- Claude Code's plugin seed directory pays off for a plugin that ships hooks, agents or
  servers. This plugin ships only skills, so the seed directory only adds work.

## Staying current

Each skill can tell when a newer version of itself is published. When the skill
starts, a script in its folder, `scripts/check-current.py`, looks at the version
stamp in `SKILL.md`, gets the same file from this repository, and compares the
two. It does this at most once a day and prints one line with the result.

You decide what the agent does when a newer version exists. Write one word in a
file called `UPDATE` in the skill's folder:

| Word | What happens |
|---|---|
| `auto` | The agent updates the skill for you. This is the default. |
| `confirm` | The agent asks you first. |
| `pin` | The agent keeps the version you have. |

The script notes when it last checked in a file called `.update-check` in the
skill's folder. If that folder is read-only, the file is
`~/.cache/skill-update-check/<skill folder name>`. Delete that file to check
again now. If the check fails, or the skill is installed for
everyone on a machine, the agent keeps the version you have.

## Format

These follow the [Agent Skills specification](https://agentskills.io/specification), which is the
same format Claude Code and Codex read. Nothing here is specific to one agent product beyond the
install path.

## Bank feeds

`bookkeeping` pulls bank feeds through a broker service. The default endpoint is
`https://auth-my-accountant.vercel.app`, which WAGMI operates. Requests to it carry a firm key,
`AMA_FIRM_API_KEY`, and no data reaches that service without one. The adapter's `signup` command
makes a firm on the service and saves its key.
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
