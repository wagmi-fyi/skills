# Substrate runbook — Claude sessions

Delegates are **background agents**. The orchestrator runs wherever the human works: a desktop app, a terminal, an IDE chat. Every claim below was measured on a live run of this substrate rather than read out of a document, and each one names what was driven to establish it.

## Spawn

**The mechanism is a background agent**: a session started from the agent CLI with its background flag. It returns immediately, it has its own working directory, and it appears in the agent listing, where the human opens it and takes it over. That is what makes it a delegate.

**The harness's in-process subagent tool is a different mechanism, and it is not this one.** It produces no session: nothing lists, nothing is openable, nobody can take it over. It cannot spawn a delegate here or anywhere (`substrate.md`).

`scripts/spawn <handle> [instruction]` is the invocation — it resolves the model, the permission flag and the bus directory through the settings seam, sets the session's display name to the handle, pre-registers the handle, and prints JSON carrying the agent id. When it refuses, the mechanism it names is the one that is missing, and nothing substitutes for it.

Send the assignment over the bus **before** spawning. The delegate's first act is to read its own inbox.

The delegate inherits the spawning session's working directory, so spawn from the repo root.

## Reach — what the human sees

A background agent appears in the harness's **agent view**, where the human opens it, reads it and takes it over. That satisfies the method's rule that real work runs in a session a person can reach (`method.md`).

Reachable means the human can open the session and type into it. A client's own session list is a separate surface with a separate backend, and a delegate does not have to appear there to be reachable. Conflating the two is what makes auto-spawn look impossible on this substrate when it is not.

## Address — the bus handle is not the session name

Two namespaces. The bus knows the handle you chose. The harness knows the session's display name.

- `scripts/spawn` passes `--name <handle>`, so a delegate this skill spawned answers to the same word on both rails.
- A session the human opened carries a name derived from its first instruction, which is longer and rarely matches. One delegate was known to the bus by its bare handle while the listing called it `Bootstrap <handle> orchestrate delegate [642f89]`. That derived name is capped, so a long instruction leaves a name truncated mid-word. Both surfaces store the same truncated string, so copying it still works.

**Take the address from the listing verbatim, in both directions.** Send to the bare name; append the bracketed ref only when the listing shows the name twice or an error asks you to disambiguate. Proven both ways: a send addressed to a peer's *header* name failed outright, and the same peer's listing name plus its ref reached it.

**A name that does not resolve is refused, and so is your own.** Driven against a session the JSON board still lists, the send came back as "No agent named '<handle>' is reachable." Addressing yourself is refused with its own reason. **A wrong-but-live name is the one that does not refuse.** It delivers into a real session that has no idea what the message is about, and the sender sees a success. That has cost a delegate its whole wake leg, which surfaced only because the session that received it by mistake chose to answer.

**Go from a handle to a session by lookup, not by search.** `bus whois <handle>` prints the session id and process the handle registered with, and whether that process is still alive. The mapping holds because `bus init` runs inside the session it names. A handle that predates the field answers `?`, and the repair is to re-register it from inside its session; until then the only route is a search of the transcripts under `~/.claude/projects/<cwd-slug>/` for the handle, then the live process carrying that session id, then the listing row it matches. That search works and it is three chances to land on the wrong session, which is a wake delivered into a stranger while the sender sees success.

**The board's `id` is not an address.** `claude agents --json` gives each session an `id`; the listing shows a different bracketed ref for the same session. Neither derives from the other, and only the listing's ref disambiguates a send.

**A wake address comes from `bus wake <handle>`.** It reads the session id the handle registered, finds the session running under that id now, and prints the name the listing has for it, so a target that came back under a new process still resolves. A charter that names a wake target quotes what that prints. It refuses out loud when the session is gone, when the handle carries no session, and when nothing records an address for it. Each refusal is a wall: stop and report it.

## The board — `state` is the last thing that happened, `pid` is what is still there

**This is the sharpest rule on this substrate, and it has two halves.**

The two forms carry different populations, not the same one at different detail:

| Form | Gives | Leaves out |
|---|---|---|
| the harness's listing tool | names, refs, kind, busy or idle, age. The only surface an address comes from | `state` |
| `claude agents --json` | `state`, `sessionId`, `pid`, `cwd`; needs no terminal; `--all` adds sessions that reached a completed state | sessions the listing reaches over other transports |

**`state` is a last-known value, not a live one.** A session that ends while `blocked` never reaches a completed state, so it stays in the default listing under that word for as long as the harness keeps records. On a machine that has been running orchestrations for weeks, most of the default listing is that: rows for sessions that no longer exist, every one reading `blocked`, the oldest of them days old. Read `state` alone and the board says two dozen delegates are parked waiting on you.

**`pid` is the liveness field, and it is exact.** A row carries a `pid` if and only if that process is alive, which is the same set the harness listing shows, which is the same set that has a socket under `$XDG_RUNTIME_DIR/cc-socks/`. Three surfaces, checked against each other while the population was changing under them, and no row on any side alone. So:

| Row | Means | Do |
|---|---|---|
| `pid`, `state: working` | mid-unit | leave it |
| `pid`, `state: blocked` | parked on something it cannot get itself | handle first. It is spending nothing and the run is stopped on it |
| `pid`, `state: done` | charter ended, session still parked and reachable | its report should be on the bus. If it isn't, the unit ended without one, and that is the finding |
| no `pid` | the session is gone whatever the row says | check the lane, then re-charter. Nothing is listening |

**Interactive sessions carry no `id` and no `state` at all**, only `pid` and `sessionId`. An orchestrator that runs interactively cannot read its own row's state, and neither can it read the human's.

**A resume gives the session a new process while the bus keeps the old one, so a handle can read `gone` on `bus handles` while its session is live and working; re-registering on resume repairs the board, and until that happens `bus wake` resolves the session id instead.**

**Read the JSON every beat, and read `pid` before you read `state`.** Nothing on this substrate announces itself. A finished delegate, a blocked one and a dead one are all silent, and stay silent until somebody looks.

## Reach the human

The durable record is the `human` bus handle and the queue file beside the workpaper (`human-in-the-loop.md`). Neither one interrupts anybody: the `human` inbox is a ledger the orchestrator reads and the human does not, and its unread count is not a backlog of unseen asks.

**The live reach is the harness's push-notification tool.** It raises a notification in the terminal the human works in, and pushes to their phone when their remote-control client is connected. It is the only path here that reaches somebody who has walked away. It also says when it did not send, which is what separates it from the two wrappers below. Spend it on what the contract calls a live reach: a crossroads, a human-only step, a long stretch of work finishing. Not on progress.

## Wake

- **The mechanism is a message from one session to another, sent with the harness's own send-to-a-peer tool.** It arrives at the peer's next tool round. **The agent CLI has no verb for it**, so there is no by-hand path: a session whose harness lacks that tool cannot wake a peer here at all, and the fallback is the bus plus somebody looking. That is the poke, and it is the wake leg a delegate owes with every report and every crossroads park (`delegate.md`). The `bus send` is the record; the message is what makes the orchestrator look. Nothing here polls on its own, so a report sent bus-only reaches its addressee whenever that session next happens to wake, which may be never.
- **Ask to be told when a peer next goes quiet.** The same send tool takes a flag that subscribes the sender to one notice when that session next goes idle or exits. It is one-shot, it costs the peer nothing when sent without a message, and only a main conversation may ask. This is the closest thing on this substrate to a delegate announcing that it stopped, and it is what turns "blocked looks like finished" into a signal rather than a poll. Idle with no report on the bus is the blocked case.
- **`scripts/desktop-wake <handle>`** emits one line per new bus message on stdout for the harness's background-watch primitive. Run it under that primitive, persistently, never detached. `--check` first: it catches an overridden `HOME` resolving to a private empty bus where every send still succeeds, and it refuses an uninitialized bus directory, which reads exactly like a quiet healthy one.
- Set `SESSION_BUS_DIR` on every invocation, to the path the spawn instruction names. An agent's shell state does not survive between tool calls, and a machine-wide export of that name wins any fallback a boot line leaves open.

### Wake delivery — the rail that pokes a session nobody is watching

The bullets above are what a session does for a peer while it is running. The
standing rail is `scripts/bus-nudge`, which runs outside every session, watches
the buses on the machine, and tells a live session that it has unread mail. On
this substrate its delivery adapter writes one turn into the target's own
socket, so the poke arrives without anybody looking.

**It delivers one fixed sentence and never anything else.** The sentence names a
bus directory. It carries no subject, no sender and no body, so a session that
receives one reads its own inbox, and the inbox is what it acts on. Run
`bus-nudge --law` to see the sentence and prove it. This is why the rail is safe
to run unattended: a wrong or stale nudge costs a turn and cannot inject work.

**Every session opts in through the machine's managed settings.** A session
holds a message from a sender that cannot attest its permission mode, and the
rail has no screen to answer the approval prompt on. The opt-in is
`crossSessionInbound`, set once for the machine; a repository may tighten it,
and a session started inside one that does is refused before anything is sent.

**It refuses a session that has ended and never resumes one.** A resume forks:
it makes a second copy under a new process and a new session id, and the
original stays dead. So an idle-exited target is a gap this rail does not close,
which is what the heartbeat below is now for.

`bus-nudge --check` reports the adapter it resolved and the pids of any watcher
already running for this account. Where the rail runs as a machine service, the
deployment that installed it puts its own reference beside it, and that file says
how it is stood up.

### What kills the watcher, and how you find out

The watcher is a process under a watch primitive that is itself bound to the session that armed it. It goes away when the session ends, when the watch is stopped, when a non-persistent watch hits its deadline, and when the primitive stops a watcher for emitting too much. None of those is a fault the watcher can see coming.

`desktop-wake` announces its own exit on every signal it can catch. **It cannot catch a kill**, and after one there is no notice at all. So do not let silence stand for health: **`--check <handle>` reports the pids of any watcher already running for that handle**, and re-running it is how you prove a watch armed earlier is still up. Re-run it after a compaction, after a recovery, and at the start of any stretch the run depends on being woken. A watcher that dies at the moment its session stops looking is the failure this substrate has actually paid for, more than once.

An *attended* run with no wake armed still works; `bus handles` unread counts are the truth in every configuration. The cost is latency.

## The heartbeat — the slow backstop under the wake rail

The wake rail above carries the fast path, and it reaches only sessions that are still running. The heartbeat covers what it cannot: a target that idle-exited, a rail that is off, a bus nobody is watching. So its interval is now the worst case of a **missed** wake rather than of every wake, and it can be slow.

The recurring path here is a **scheduled prompt against the orchestrator's own session**, running `run.md`'s beat: read the board from the JSON, drain the inbox, act on what it finds, say nothing when the board is quiet. Its interval is the worst case a lost wake costs, so set it against how long the human will be away.

**A session-bound scheduler dies with the session** and leaves nothing behind that says so. A restart, a recovered session, a fresh orchestrator picking the run up — each one starts with no heartbeat while the workpaper still records that one was armed. Re-stand it during `resume`, and treat a heartbeat you have not watched fire since re-entry as absent.

## Registration

`bus init <handle> --pane - --role <role> --address "<name>"`, where `<name>` is what the agent listing prints for this session. No session here occupies a pane. `scripts/spawn` records a delegate's address at pre-registration; an orchestrator passes its own at standup and again on resume if the listing name changed.

## Retire

A delegate that finished stays parked and reachable, and it costs nothing to run. Leave it: its context is the cheapest way to pull a thread on its work, and that is the payoff of a human-reachable session. Re-charter fresh rather than reviving a delegate whose context is spent.

**What it costs is board legibility, and the bill is real.** A unit that ends cleanly reaches `done` and drops out of the default listing once its process goes. A unit that ends any other way, killed, abandoned mid-turn, or parked on a question nobody answered, never reaches a completed state and stays in the default listing under `blocked` for as long as the harness keeps records. On the machine this was measured on that was 27 of 40 rows. Read the board by `pid` and they cost nothing; read it by `state` and they bury the live ones.

**The orchestrator can close one, and the agent CLI has the verbs.** `claude stop <id>` stops a background session and destroys nothing: the conversation is kept, `claude attach <id>` reopens it, `claude --resume` still works, and the worktree stays. What it changes is the board, moving the row from `blocked` to `stopped` and off the default listing, which is how a dead `blocked` row is cleared. `claude rm <id>` is the destructive counterpart: it deletes the session's job state and its worktree, and it works on a session that has already exited. Sessions another orchestrator spawned stop cleanly; the harness's own stop-a-background-task verb takes a background agent's name and refuses one it does not hold ("No task found with ID"). `claude attach`, `claude logs` and `claude respawn` are the rest of the family.

**Two things about `rm` that only show up when you drive it.** Its guard tests whether the worktree has been pushed to a remote, not whether it is merged, so on a machine whose repositories have no remote it refuses every worktree and the destructive half never fires; the sequence that works is `git worktree remove` first, then `claude rm` for the job state. And a worktree is job state only when the session's own record names it, so a session that took its own lane at a repo root records none, and `rm` never reaches that directory.

## Recover a session after a restart

`claude --bg --resume <session-id>` brings a delegate's context back as a fresh background agent. Proven this run: the resumed agent recalled the handle it had registered in the earlier session. Session ids come from `claude agents --json --all`, which includes completed sessions, or from the transcripts under `~/.claude/projects/<cwd-slug>/`.

## What fails silently here

- **`scripts/notify` and `scripts/present` reach nobody and still exit 0** when the box has no desktop session. Re-driven on this host, which has no display and none of the five notifier or opener binaries: `notify` prints one line into a void; `present` prints a `file://` path naming a machine the human is not sitting at, under the word "presented". **Do not use either on this substrate.**
- **The tmux adapter of the wake rail cannot deliver here.** It pokes a registered pane; no session on this substrate has one. Undeliverable rather than misconfigured, so no adapter debugging fixes it. `bus-nudge` names the substrate it refuses on.
- **A blocked delegate looks finished, and a dead one looks blocked.** See the board section. `state` is the last thing that happened, so a session that died while blocked reads as one waiting for you. This is the silent failure that costs the most, and `pid` is what settles it.
- **A wake delivered to the wrong session reports into a void, and both legs look done.** A name that does not resolve is refused out loud. A name that resolves to the wrong live session is not: the message lands, the sender sees success, and the intended reader never hears anything. Measured on a run whose first wake leg went to a stranger, and it surfaced only because that stranger chose to answer. Person-shaped recovery, not a mechanism.
- **A `bus send` to a mistyped handle succeeds and queues forever.** It is the same output as the intended case of sending a charter before its delegate exists. See `session-bus.md`.
- **A dead `desktop-wake` is indistinguishable from a quiet bus.** It announces every exit it can catch and cannot catch a kill. `--check <handle>` reports whether a watcher is running; nothing else does.
- **A stopped wake rail and a quiet bus look the same.** Nothing announces that the rail went down, and a run whose reports were arriving by themselves keeps waiting as if they still are. `bus-nudge --check` reports the pids of any watcher running for this account; where it runs as a machine service, the service manager's own status verb answers it. Silence is not health here either.
- **A session's log dump is a terminal capture**, control codes and spinner frames included. It reads a session's state badly and is no substitute for the evidence its bus report cites.
- **A wake to an idle interactive session sits in its queue.** The send reports delivered, and the message waits for whoever sits at that keyboard to type again, so delivery and processing are separate steps here. A peer orchestration sent both legs correctly and reached nobody this way; the backstop is the stall rail, which watches orchestrator handles for unread mail past its threshold and posts when it finds some.
- A peer send from a session in a broader permission class than its recipient reports success and is then held for the recipient user's approval; an unapproved hold expires and the message never lands. Delegates spawned with permissions bypassed hit this against any orchestrator running a stricter mode, which is the pairing `delegate_skip_permissions` produces by default. The sender receives delivery notices for the hold and the expiry: treat them as a failed wake and record the failure on the bus.
