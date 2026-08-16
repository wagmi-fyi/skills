# The session bus — directed file mailboxes

A tiny, file-backed message bus so independent Claude Code sessions on one machine exchange prompts + reports without a human relay. Bundled with this skill; data lives at `bus_dir` (default `~/.claude/session-bus/`; the CLI also honors `$SESSION_BUS_DIR`).

CLI: `~/.claude/skills/orchestrate/scripts/bus` (`--help` on any subcommand).

## Handles
**The bus is machine-level and shared — other orchestrations may be running on it.** Before you register, **survey it** (`bus handles` / `bus locks`, read-only): take a **distinct, project-namespaced** handle (e.g. `<project>-orchestrator`), treat others' inboxes/state as read-only, and if your **commitment target** overlaps another's, serialize with a lease (`git.md`) — else just run alongside.

Each session adopts a **handle** at launch (e.g. `<project>-orchestrator`, `ingest`, `worker-a`, …). The launching prompt names it. Register once:
```
bus init <handle> --pane "$TMUX_PANE" --role <role>
```
`--pane "$TMUX_PANE"` captures the tmux pane so push-wake + spawn can find you (harmless if not in tmux).

## The `human` handle (escalation ledger)
The human is **not** a session and doesn't run `bus inbox`. A reserved **`human`** handle is the **consolidated decisions/escalations ledger**: agents `bus send <you> human "<crossroads / human-only ask>" --ref <brief>` to record everything needing a human ruling — across all sessions and projects. The human is *reached live* out-of-band (`scripts/notify` + the paused visible pane + a browser decision-brief, per `human-in-the-loop.md`); the `human` inbox is the durable, dedupable record the orchestrator reviews and surfaces **batched**. `bus read human` shows every open ask at a glance. Register it once for visibility: `bus init human --role human`.

## Verbs
| Command | Does |
|---|---|
| `bus init <handle> [--pane P] [--role R]` | register this session's handle + inbox + cursor |
| `bus send <from> <to> <subject> [body] [--ref FILE]` | append a message to `<to>`'s inbox (+ the archive) |
| `bus inbox <handle> [--peek]` | read MY unread messages; advance the cursor |
| `bus read <handle>` | **cross-verify**: read any handle's full inbox, read-only, no cursor change |
| `bus log [--tail N]` | **cross-verify**: the global append-only archive of every message |
| `bus handles` | the board: registered handles + unread counts + panes |
| `bus pane <handle>` | print a handle's tmux pane (used by the push watcher) |
| `bus rotate <handle>` | move already-read messages out of MY inbox (hygiene) |
| `bus gc [--days N] [--commit] [--if-stale SECS]` | retire fully-read handles idle > N days (default 14) so the board stays small; dry run unless `--commit` |
| `bus lock <name> [--holder H] [--ttl S]` | acquire a named lease — serialize a commitment across orchestrations (see `git.md`) |
| `bus unlock <name> [--holder H] [--steal]` | release a lease (`--steal` forces a stale/foreign one) |
| `bus locks` | list active leases (name, holder, age, ttl) |

## Properties (rely on these)
- **Directed.** You read only your **own** inbox by default → you never burn tokens on other sessions' traffic. Cross-verification is an **explicit** `bus read`/`bus log`, never a default firehose.
- **Idempotent cursor.** `bus inbox` returns only the unread delta; re-checking is a safe no-op. This is what makes early/duplicate wakes (push, loop, manual nudge) harmless.
- **Pointers, not blobs.** Put a big prompt/report in `prompts/<name>.md`; send a one-line message with `--ref prompts/<name>.md`. Inbox lines stay tiny → a check/poke costs almost nothing; the full payload is read only by the actual addressee.
- **Atomic append + archive.** Concurrent senders are lock-serialized (POSIX `flock`; a portable lockfile shim on Windows); every message is mirrored to `log.jsonl` for audit.

## Conventions
- One handle per session; pick a stable, descriptive name — often the unit id.
- Report back to whoever sent you (usually `orchestrator`). **Peer mesh is allowed** — send directly to a sibling handle when it removes a round-trip; the orchestrator still verifies before "done."
- Keep inboxes small (`bus rotate`) on long runs.
- **Board hygiene is the orchestrator's job, at standup** — it runs `bus gc` (opportunistic, throttled) when it surveys the board; **delegates never gc**. gc only retires *fully-read, >14d-idle* handles — never `human`, an active lease-holder, or recent ones — and archives to `processed/` (every message also stays in `log.jsonl`), so it's reversible. New delegates/orchestrators are never "overwhelmed" by old sessions regardless: inboxes are **directed**, so a fresh handle inherits none of prior sessions' traffic — gc is about keeping the *survey* + board small, not about correctness.
- The orchestrator `bus send`s a unit's prompt (as a `--ref` pointer) **before** spawning/notifying its session, so the assignment is waiting when the delegate checks in.
