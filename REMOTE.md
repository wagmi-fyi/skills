# REMOTE.md

Remote: `https://github.com/wagmi-fyi/skills.git`. The repository is public.

People install the skills from this repository. That is why it has a remote.
`PUBLISHING.md` says how a change gets in.

## What pushes

Nothing pushes on its own. No hook or schedule writes here.

A change is pushed as a branch with a pull request. The merge request names the
head commit, so a branch that moved after review is refused.

`main` moves only when a maintainer merges a pull request. Installed copies
follow `main`. A branch changes nothing for anyone until it is merged.

## Contributions from outside

Pull requests from outside are not accepted yet. Issues are. README.md says so.
