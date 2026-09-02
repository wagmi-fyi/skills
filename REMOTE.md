# REMOTE.md

Remote: `https://github.com/wagmi-fyi/skills.git`. The repository is public.

This is the published library. It is the source of truth for some of what it
holds and the output of a publish for the rest, so check which before you edit
a file.

`bookkeeping` and `qbo` are sourced here. The repository they were built in
was retired on 2026-09-02, and this one took over. An edit to either of them is
made here, on a branch, and reaches everybody when a maintainer merges the pull
request.

`master-builder` and `orchestrate` are published output. They are built in
CommonClaw's own repository and copied in, so an edit made to them here and not
made upstream is lost at the next publish.

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
