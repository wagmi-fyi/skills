# Substrate runbook — Codex

**Unwritten.** Nothing in this file describes how a run behaves on Codex, and nothing should be inferred from the other runbooks: `claude.md` and `tmux.md` answer the same acts with mechanisms that have nothing in common.

Do not select this substrate for a run until the file below it exists. If a run has to start on Codex before then, the honest move is to say so, work the acts by hand, and write down what each one turned out to be. That record is the runbook.

## What writing it requires

Answer the six acts in `../substrate.md` from a **live session on the substrate**, not from documentation:

1. **Spawn a delegate.** Is there a command that starts a session and returns? Does it accept a display name, a model, a working directory? If nothing spawns, the substrate runs `spawn_mode: manual` and the runbook says how the human opens a session instead.
2. **Address a peer.** Is there a namespace at all, and does it match the bus handle? Name the exact form and where it is read from.
3. **Read the board.** What lists the live sessions, and does that listing distinguish a **blocked** session from a **finished** one? If it cannot, say so plainly: the method's largest exposure is a delegate parked on a question that looks like a delegate that succeeded.
4. **Wake a peer.** Can anything reach a session that is not looking? If not, the wake is the board and the unread count, and the runbook says that rather than implying a poke exists.
5. **Reach the human.** Which channel actually arrives, and which one exits 0 having reached nobody.
6. **Retire a delegate.** What happens to a session whose unit closed, and whether anything has to be closed at all.

**Wake delivery has a written contract, and the stub for it ships.** The standing
wake rail (`scripts/bus-nudge`) keeps its transport in a per-substrate adapter,
and the codex adapter beside the others is a documented stub: it refuses by name
so that a machine selecting this substrate is told, rather than being served a
rail that reaches nobody in silence. Its header carries the three questions
writing it requires, and answering act 4 above is the same work.

Then the two questions every runbook answers: how the orchestrator learns a delegate blocked, and what fails silently.

**Derive each answer, quote what you ran, and do not carry a claim over from another runbook.** A borrowed mechanism that happens to be wrong is worse than an admitted gap, because the gap gets worked around and the wrong mechanism gets trusted.
