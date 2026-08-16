# Runtime Conventions

How skills are discovered on this machine, and the conventions that keep a workspace portable across harnesses.

## Discovery

- **Claude-enabled systems** (Claude Code and compatible): skills are discovered in `.claude/skills/` of the working directory and in `~/.claude/skills/` globally. Workspace instructions live in `CLAUDE.md`.
- **Other systems**: the AGENTS.md convention — instructions in `AGENTS.md` at the workspace root, skills in `.agents/skills/` (workspace) and `~/.agents/skills/` (global).

On a Claude-enabled system, build on the Claude paths. Elsewhere, follow AGENTS.md.

## Both Conventions Active

Author in **one** location and symlink the other to it, per directory:

```
.agents/skills/{skill} -> ../../.claude/skills/{skill}     # Claude system, AGENTS.md consumers
.claude/skills/{skill} -> ../../.agents/skills/{skill}     # the reverse
```

Never maintain two copies — a copy is a fork you didn't mean to make. The same rule applies to the instruction files: one canonical file, the other a symlink.

## New-Skill Visibility

Most harnesses index skills at session start. After installing or creating a skill, a fresh session (or the harness's reload mechanism) is what makes it visible — not repetition of the request.

## Workspace Roots

Every skill in the workspace assumes this layout. Skills are instructions; state outlives them and sits beside them at the root. The underscore prefixes keep state at the top of the file tree.

```
{workspace}/
├── .claude/skills/{skill}/   or   .agents/skills/{skill}/
├── _workpapers/              # running state, one subfolder per workflow, period-named files
├── database/                 # skill databases: {skill-name}.db — git-ignored
└── _local-{skill}/           # only when the skill is multi-tenant — this workspace's local tier (`reference/skill-patterns.md`)
```

## Secrets

Credentials never live inside a skill directory and are never committed. At rest they belong in a secrets manager with a CLI the agent can invoke (1Password's `op`, for example) — not in plaintext on disk. Inject at invocation: the skill names the credential it expects; the manager resolves it into the process environment (`op run`-style, or a `.env` holding manager references rather than values). The OS keychain is the fallback. A plaintext `.env` is a last resort — git-ignored, and a hardening trigger the first time real client work touches the skill. Auth mode matters: an app-integrated manager needs an attended, unlocked session; unattended runs need a service-account token. Debug output never prints a secret — lengths and hashes only.

## Script Runtimes

Scripts may be written in any language; the conventions are language-independent:

- **Dependencies ride with the script** where its ecosystem supports inline declaration; otherwise they're declared in the skill's Dependencies section.
- **Invocation goes through a resolver** that honors those declarations — a bare interpreter or package tool (`python`, `pip`, `node`) in an instruction is a bug; it may not resolve on the reader's machine.
- **Check for an established convention** per language — what do the workspace's existing scripts invoke? If nothing is established, ask the human and suggest the ecosystem default.

Python, the common case — suggested default `uv`, libraries declared inline and resolved by `uv run` at invocation (`uvx` for tool-style packages):

```
# /// script
# dependencies = ["requests"]
# ///
```

Other ecosystems follow the same shape. Match whichever form the workspace establishes.
