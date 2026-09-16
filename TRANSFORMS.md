# Transforms

What differs between a skill's source and its published copy, and why. Every
difference is one of the rules below. A difference that is not on this list is a
defect in the publish, not a convention.

## Where each skill comes from

| Skill | Source |
|---|---|
| `bookkeeping` | this repository is the source of truth |
| `qbo` | this repository is the source of truth |
| `master-builder` | the CommonClaw source repository, `.claude/skills/master-builder` |
| `orchestrate` | the CommonClaw source repository, `.claude/skills/orchestrate` |

A publish records the source revision each skill was taken from in the pull
request body, so a later reader can diff the two trees at that point.

## The transforms

**T1. The license line.** `license: Apache-2.0` is inserted after `name:` in each
published `SKILL.md`. The source files carry no license field.

**T2. `orchestrate/config.yaml` ships neutral.** `bus_dir`, `shared_bus`,
`substrate` and `delegate_model` are blank and `delegate_skip_permissions` is
false. Blank is a real value in this skill: it means the setting is established
at bootstrap, or that the machine has no shared bus, or that the delegate
inherits. The published file also carries explanatory comments the source does
not. The key sets are identical, and a key present in one and absent from the
other is a defect.

`delegate_skip_permissions` is a security bypass. It ships false and is opted
into per machine, never inherited from whatever the source repository runs with.

**T3. `master-builder/SKILL.md` says "the machine the agent runs on".** The
source names the deployment it was written on.

**T4. `bookkeeping/templates/config-template.yaml` gives `example-accounting` as
the `firm_id` example.**

**T5. Symlinks resolve.** `orchestrate/scripts/bus` is a symlink in the source
and a real file when published, with identical content. Nothing else in the
published tree is a link. Any other symlink reaching outside a skill's own tree
is a publish blocker, not a transform.

**T6. `orchestrate/scripts/bus-nudge` ships `/etc/bus-nudge.conf` as its conf
default.** The source names the path its own installer writes. The published
default is the plain one, and a deployment that installs the rail as a service
sets its own path at install time, either through `BUS_NUDGE_CONF` in the
environment or by rewriting the default line in the copy it installs. The
installer owns that rewrite. The rule the file states around the line holds
either way: the default and the installed conf have to name one file, or a
machine's own ruling reaches nothing.

**T7. The stamp.** The publish writes the source revision into each published
`SKILL.md`, as `version` under `metadata`, at the end of the front matter:

    metadata:
      version: "<short commit> <date>"

The commit is the source revision the publish took the skill from, the one the
pull request body records. The date is that commit's committer date in UTC,
`YYYY-MM-DD`. A source file that already has a `metadata` block keeps it, and
the line goes at its end. The source files carry no version line, and nobody
bumps a number by hand. An installed copy compares its stamp with the one on
`main`, so a stamp that differs is a newer version. The unit that publishes
applies T7 with the other transforms.

`bookkeeping` and `qbo` are sourced here, so their stamp is written at the
merge. The maintainer's merge unit commits it on the pull request's branch as
the branch's last commit, naming the commit before it: the last one that
changed the skill's tree. That commit changes the stamp line and nothing else.
The pull request merges with a merge commit, because a squash replaces the
commit the stamp names. The merge unit runs `tools/check-front-pages.py` on the
branch head before the merge and on `main` after it, and a pull request whose
stamp is missing or stale is refused.

**`.claude-plugin/` is publish-side only.** It is written here and never comes
from a source tree.

## The neutrality gate

No published file names the publisher's own machines, products or people. The
gate is a grep, and it runs with a control so that its exit 1 is a measurement
rather than an unreachable branch:

    /usr/bin/grep -rniE 'commonclaw|\bclaw\b|claws|\btyr\b|tyr-|jeremiah|/srv/' <skill trees>

Exit 1 over the published trees is the pass. The house words are anchored on
word boundaries, because unanchored they match ordinary content: "tyr" sits
inside "EntityRef", a QuickBooks field name. Run the same pattern over a file
known to hold house vocabulary and confirm it exits 0, or the check proves
nothing.

`wagmi` in a README is the publisher naming itself in its own repository. The
pattern excludes that case by design, and the exclusion is a rule rather than an
oversight.

## A file that cannot pass the gate does not publish, and the fix is upstream

Stripping at publish time makes the published tree match no source revision, and
the next author reintroduces the name having seen a clean repository. So the
source is fixed, the transform list stays this short, and a publish stays a
whole-tree diff.

This has been ruled twice. The wake rail is the worked example: its program was
a symlink into a deployment payload and its adapters were not in the skill tree
at all, so a published copy carried a rail that refused every substrate by name.
The fix was to move the source into the skill and to resolve the machine's own
paths through the settings seam, not to strip the rail at publish time.

## The update check is one paragraph

Every published skill opens its activation with the same paragraph, byte for
byte. The paragraph runs `scripts/check-current.py` and acts on the one line it
prints. The script reads the stamp, checks `main` once a day, and reads the
person's `UPDATE` word. A skill stands alone once installed, so each skill
carries its own copy of both. `tools/check-front-pages.py` is the gate. It
reads the front pages, refuses a second variant of the paragraph, refuses a
missing or malformed stamp, checks the stamp of a skill sourced here against
its history, and holds each front page under the format's 500-line ceiling. It
also refuses a script that differs from the others by one byte, and runs each
one with `--help`. A change to the paragraph or the script is made in all four
sources in one piece of work, and the gate runs over the sources too, with
`--unstamped` for each source tree.

Its pass counts beside a control: one changed byte in a scratch copy of one
paragraph has to make it fail.
