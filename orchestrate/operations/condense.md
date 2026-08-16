# Condense — keep the live workpaper resume-sized; archive history verbatim

Run when the live workpaper has outgrown its job as a re-grounding surface. The signal is structural, not numeric: a single read no longer returns the file whole, or closed-work history visibly outweighs live state. Usually invoked **from `checkpoint`** (postures are distilled first — the condense sweep feeds on the same filter); also fine standalone at any quiet moment. Prefer a quiet board — condensing mid-flight risks freezing a status that's about to be wrong.

> **Why this exists.** A workpaper serves two readers with opposite needs: the *next context window*, which wants the minimum tokens to re-ground, and the *record*, which wants every gate's evidence verbatim, forever. One file cannot serve both once a run gets long. Condensing splits them: the live file re-grounds; a dated archive holds the record.

## Intent

Leave the live workpaper carrying exactly what a freshly-compacted orchestrator must re-assert and act on — and nothing it would merely scroll past — while the full evidence record survives verbatim in an archive the live file cites.

## The one invariant

**Archive verbatim FIRST, then cut.** Copy the entire live file to a dated sibling (`workpaper-archive-<date>.md`, next to the workpaper) before removing anything. Evidence is **moved, never summarized** — a summary of evidence is not evidence, and "verified ✅" claims must stay traceable to what was actually verified. The copy is also the rollback for the rewrite. Archives are append-only as a set: a later condensation adds a new dated archive; it never rewrites an old one.

## Before cutting: sweep the departing history for live knowledge

History about to leave the live file is the last cheap moment to notice a durable pattern in it. Apply the checkpoint's posture filter (*changes future behavior · not already captured · states concisely*) to the journal entries being archived: what qualifies becomes a Standing Posture — or a promotion candidate, human-OK'd, per `checkpoint.md`. Everything else archives as narrative. When the orchestration composes on a domain skill that has its own knowledge-capture convention, route durable domain learnings there instead of into postures.

## What stays live (the re-grounding payload)

- **The cold-boot banner + read order** — now carrying the archive pointer and this operation's invariant, so future condensations inherit the rule.
- **Standing Postures, complete and verbatim** — the highest-value tokens in the file. Never condensed; a posture leaves only by deliberate retirement at a checkpoint.
- **STATUS, rewritten fresh as of now** — live state, wake events, standing warnings. A stale status is worse than none; don't trim the old one, replace it.
- **The board, split by liveness** — open/in-flight/blocked rows keep full detail; closed units collapse to one-line roll-ups (unit name · ✅ date · one-line gist) that cite the archive with a "do not re-verify" note. Keep unit names **stable** — charters, bus messages, and the archive all key on them.
- **Open human asks**, pruned to the actually-open.
- **The newest journal entries that constitute "where we are"** — the current arc, not the current day by calendar.

## What moves to the archive

Superseded STATUS blocks · closed units' full evidence rows · journal older than the current arc · resolved human asks. When in doubt, move it: the archive is one pointer away, and the live file is paid for on every resume.

## Gate

- A cold reader re-grounds from the live file **alone** — postures, status, board, newest journal — without opening the archive.
- Every closed unit resolves by name to its full evidence in the archive; nothing load-bearing exists only as a summary.
- The live file reads whole in a single read.
- The condensation itself is journaled (one line: what moved, where, and that the archive-first rule held).
