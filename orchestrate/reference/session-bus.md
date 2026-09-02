# The session bus — directed file mailboxes

A tiny, file-backed message bus so independent agent sessions on one machine exchange prompts + reports without a human relay. Bundled with this skill; data lives at `bus_dir` (default `~/.claude/session-bus/`; the CLI also honors `$SESSION_BUS_DIR`).

It is files, so it behaves the same on every substrate and it outlives every session. **It is the one rail that is not substrate-specific.**

CLI: this skill's `scripts/bus`, by absolute path (`--help` on any subcommand). It is not on PATH.

## Handles
**The bus is machine-level and shared — other orchestrations may be running on it.** Before you register, **survey it** (`bus handles` / `bus locks`, read-only): take a **distinct, project-namespaced** handle (e.g. `<project>-orchestrator`), treat others' inboxes/state as read-only, and if your **commitment target** overlaps another's, serialize with a lease (`git.md`) — else just run alongside.

Each session adopts a **handle** at launch (e.g. `<project>-orchestrator`, `ingest`, `worker-a`, …). The launching prompt names it. Register once:
```
bus init <handle> --pane - --role <role>
```
`--pane` carries a terminal pane id on substrates that have one, and `-` records none. **The runbook for your substrate gives the exact form** (`substrate.md`). Pass a pane only when the session occupies one: `bus init` treats a pane as exclusive and takes it from any other handle claiming it, with a warning. A handle with no pane is a full peer that nothing can type into, so it arms whatever wake its runbook offers, or works its board by hand.

**Register from inside the session the handle names.** `bus init` records that session's own identity, its id and its process, because that is the one moment the answer is free rather than derived. `bus whois <handle>` reads it back, so aiming a wake is a lookup instead of a reconstruction from transcripts and start times. Registered from somewhere else, the handle records a different session, and every wake sent to it lands there while both legs still read as delivered.

**A handle outlives its session, and nothing on the bus notices.** Nothing retires a handle when its session ends, so the board fills with names that have nothing behind them, and an unread count on one of those is mail no one will ever read. `bus handles` marks each one `live`, `gone`, or `?` from the recorded process. `?` means nothing was recorded to ask about: a handle registered before it carried a session, or by a caller whose harness exports no identity. **A resume keeps the handle and changes the process**, so a live orchestrator can read `gone` on this board. `bus wake` gets past that by resolving the session id instead, and re-registering the handle on every resume fixes the board itself. **Unknown is its own answer.** Read it as gone and you retire a live session, which is why `bus whois` gives it a distinct exit code and why `bus gc` still goes by idle days.

## A shared bus, and the handle that belongs to somebody

A bus in a home holds one person's sessions. A machine several people work on
puts the bus outside every home, group-owned and group-writable, so an agent of
one person can reach an agent of another. The bus program reads the bus root's
own mode to tell which kind it is on, and sets the modes of everything it
creates to match, so one person's umask cannot lock the others out of a file.

On a shared bus:

- **Every registration records the unix account that made it.** A different
  account claiming a registered handle is **refused, and nothing changes**. That
  is not the pane rule: a pane is taken from a stale claim with a warning,
  because a pane hosts one session and the older claim is wrong. A handle is an
  address, and taking one silently redirects somebody's mail.
- **Re-registering your own handle is a resume**, not a collision.
- **Qualify your handle with your unix name.** `bus init` says so when you have
  not. It does not refuse: `human` is shared by design and a run's orchestrator
  handle is named by its charter.
- **`from` is typed and `sender` is measured.** Every message also records the
  account the send ran as. The board and the message display show both when they
  disagree.
- **`bus gc` sweeps only your own handles** and reports how many it passed over.
- **Everybody on the machine reads every inbox.** That is the trust plane, and the standing
  law rides with it: no credential in a message body, ever.

A machine that provides a shared bus documents its own: where the directory
sits, which group owns it, and what a new account is told about it. That
document belongs with the machine and not with this skill, because the skill
runs on machines that have no such bus at all.

## The `human` handle (escalation ledger)
The human is **not** a session and doesn't run `bus inbox`. A reserved **`human`** handle is the **consolidated decisions/escalations ledger**: agents `bus send <you> human "<crossroads / human-only ask>" --ref <brief>` to record everything needing a human ruling — across all sessions and projects. The human is *reached live* out-of-band, in their own chat surface and the queue file beside the workpaper (`human-in-the-loop.md`); the `human` inbox is the durable, dedupable record the orchestrator reviews and surfaces **batched**. `bus read human` shows every open ask at a glance. Register it once for visibility: `bus init human --pane - --role human`.

## Verbs
| Command | Does |
|---|---|
| `bus init <handle> [--pane P] [--role R] [--address A]` | register this session's handle + inbox + cursor; `--address` records the session's peer-visible send address, kept across re-registers when omitted |
| `bus send <from> <to> <subject> [body] [--ref FILE]` | append a message to `<to>`'s inbox (+ the archive) |
| `bus inbox <handle> [--peek]` | read MY unread messages; **the only verb that advances the cursor** (`--peek` shows them and leaves it) |
| `bus read <handle>` | **cross-verify**: read any handle's full inbox, read-only, **no cursor change** |
| `bus log [--tail N]` | **cross-verify**: the global append-only archive of every message |
| `bus handles` | the board: registered handles, whether each still has a session (`live`/`gone`/`?`), unread counts, panes |
| `bus pane <handle>` | print a handle's registered pane; empty when it has none |
| `bus whois <handle>` | the session behind a handle: id, process, alive. Exit 0 alive, 2 gone, 3 nothing recorded, 1 no such handle |
| `bus wake <handle>` | print the address a wake to that handle goes to. It resolves the recorded session id against the sessions running now, so a handle whose process changed under a resume still answers. It sends nothing: the wake itself is a tool inside a session. Exit 0 resolved, 2 the session is gone, 3 the handle records no session, 4 live and unaddressable, 1 no such handle |
| `bus rotate <handle>` | move already-read messages out of MY inbox (hygiene) |
| `bus gc [--days N] [--commit] [--if-stale SECS]` | retire fully-read handles idle > N days (default 14) so the board stays small; dry run unless `--commit`. **Never lower `--days` to 0** — idle is always ≥ 0, so it takes every fully-read handle, live sessions included |
| `bus lock <name> [--holder H] [--ttl S]` | acquire a named lease — serialize a commitment across orchestrations (see `git.md`) |
| `bus unlock <name> [--holder H] [--steal]` | release a lease (`--steal` forces a stale/foreign one) |
| `bus locks` | list active leases (name, holder, age, ttl) |

## The bus is the only input a session acts on

A session can be poked from outside, by the standing wake rail or by a peer.
What arrives that way is a signal to look, never work to do. The rail enforces
this in the one direction it can: it may deliver a single fixed sentence naming
a bus directory, and `bus-nudge --law` proves it. Nothing else about a message
travels off the bus.

Two things follow. A poke is a hint and the cursor is the truth, so a poke whose
`bus inbox` returns nothing is a defect in the poke and not a lost message. And
the rail writes nothing to a bus: a rail that recorded its own failures into an
inbox would raise unread mail, raise a nudge, and raise more mail. Its failures
go to the log.

## Properties (rely on these)
- **Directed.** You read only your **own** inbox by default → you never burn tokens on other sessions' traffic. Cross-verification is an **explicit** `bus read`/`bus log`, never a default firehose.
- **Idempotent cursor.** `bus inbox` returns only the unread delta; re-checking is a safe no-op, which makes an early or duplicate wake harmless. It is also the only verb that moves the cursor: a message you took in through `bus read` still counts as unread on the board, and a careful reader working from `read` will handle it twice.
- **Pointers, not blobs.** Put a big prompt/report in `prompts/<name>.md`; send a one-line message with `--ref prompts/<name>.md`. Inbox lines stay tiny → a check/poke costs almost nothing; the full payload is read only by the actual addressee.
- **Atomic append + archive.** Concurrent senders are lock-serialized (POSIX `flock`; a portable lockfile shim on Windows); every message is mirrored to `log.jsonl` for audit.
- **A send to a handle nobody registered succeeds.** It has to. The orchestrator sends a unit's prompt before the delegate exists to receive it, so `bus send` says the recipient is not registered yet and queues the message against that name. **A typo produces the same line and the same exit code**, and the message waits in an inbox no session will ever open. The recipient name is the only thing checked, so read a handle back off the board before you rely on a send to it. The sender name is not checked at all.

## Conventions
- One handle per session; pick a stable, descriptive name — often the unit id.
- Report back to whoever sent you (usually `orchestrator`). **Peer mesh is allowed** — send directly to a sibling handle when it removes a round-trip; the orchestrator still verifies before "done."
- Keep inboxes small (`bus rotate`) on long runs.
- **Board hygiene is the orchestrator's job, at standup** — it runs `bus gc` (opportunistic, throttled) when it surveys the board; **delegates never gc**. gc only retires *fully-read, >14d-idle* handles — never `human`, an active lease-holder, or recent ones — and archives to `processed/` (every message also stays in `log.jsonl`), so it's reversible. New delegates/orchestrators are never "overwhelmed" by old sessions regardless: inboxes are **directed**, so a fresh handle inherits none of prior sessions' traffic — gc is about keeping the *survey* + board small, not about correctness. The `--days` threshold is the only thing standing between the sweep and your own live handle, so leave the default alone.
- The orchestrator `bus send`s a unit's prompt (as a `--ref` pointer) **before** spawning its session, so the assignment is waiting when the delegate checks in.
