# Publishing

This file says how a change to a skill gets published and what checks it passes
first.

## Where the skills live

The four skills in this repository are edited here. So is the plugin manifest in
`.claude-plugin/`.

A change goes on a branch. A pull request opens for it. A maintainer merges it.
The merge is a merge commit. The next section says why.

## The stamp

Each `SKILL.md` carries a version stamp at the end of its front matter:

    metadata:
      version: "<short commit> <date>"

The commit is the last one that changed the skill's directory. The date is that
commit's date in UTC. An installed copy compares its stamp with the one on
`main`. When they differ, a newer version exists.

The stamp is written by the last commit on the branch. That commit changes the
stamp lines and nothing else. A squash would replace the commit the stamp names,
which is why the merge is a merge commit.

`tools/check-front-pages.py` refuses a stamp that is missing, malformed, or
names any commit other than the last one that changed the skill.

## The house-word check

No published file names one of our machines or people, or a name we have
retired. The check is one grep:

    /usr/bin/grep -rniE '\btyr\b|tyr-|jeremiah|/srv/' <skill directories>

Exit 1 is a pass. The words carry word boundaries. Without them "tyr" matches
inside "EntityRef", a QuickBooks field name.

Prove the check works before trusting it. Run the same grep over a scratch file
that holds one of the words. It has to exit 0.

`wagmi` is the publisher. `commonclaw` is the product. `claw` is the product's
word for a machine that runs it. The check allows all three.

A file that trips the check is fixed in place. Do not strip the word at publish
time. The next person edits the unstripped file and the word comes back.

A published file names no path from one machine. A machine sets its own paths
in its settings file.

## The activation paragraph and the check script

Every skill opens its activation with the same paragraph and carries the same
`scripts/check-current.py`, byte for byte. A change to either goes into all four
skills in one commit.

`tools/check-front-pages.py` refuses a second version of the paragraph or the
script, a bad stamp, and a front page over 500 lines. Prove it the same way as
the grep: plant a fault in a scratch copy and check that the script fails.
