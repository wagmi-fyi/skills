# REMOTE.md

Remote: `https://github.com/wagmi-fyi/skills.git`. The repository is public.

This is the published library. Every skill here is built somewhere else and copied
in, so no file here is a source file. An edit made here and not made upstream is
lost at the next publish.

## Why this repository has a remote

It is published, and it is open source. People install from it, so it has to sit
where they can reach it.

## What triggers a push

Nothing pushes on its own. No hook, no schedule, no automation writes to this
remote.

**Branches.** A publish is a piece of work somebody has been assigned. That work
pushes its own branch and opens a pull request, pinned to the head commit so a
race fails instead of merging something nobody read. A branch here is a proposal.
Nothing a reader installs changes when one appears.

**Main.** `main` moves when a maintainer merges that pull request. Nothing else
writes to `main`. An installed copy follows `main`, so the merge is the moment a
change reaches everybody.

**Contributions from outside.** README.md says pull requests are not accepted yet.
Issues are.
