# Git — worktree lanes + cross-orchestration coordination

How orchestrations use git so parallel work never collides and the **commitment step** (merge-to-mainline / deploy / publish) serializes. **Discipline + a primitive — not a rigid script;** apply judgment.

## Intra-orchestration: worktree lanes (guidance)
- **One worktree per build unit**, off a **frozen base sha** (record it in `plan.md`):
  `git worktree add <wt>/<unit> -b <project>/<unit> <base>` — then **immediately** `git submodule update --init` (the worktree submodule footgun). The delegate works **only** in its worktree.
- **Namespace** branches + worktree dirs by **project slug** (`<project>/<unit>`) so two orchestrations never collide on names.
- **Integration:** when unit gates pass, merge unit branches back in **dependency order** onto the project's integration branch; **re-run the gate** on the integrated result before it counts.
- **Cleanup:** `git worktree remove` after merge.
- **Concurrent same-repo delegates: worktrees are REQUIRED, not guidance** — two lanes in one clone comingle silently (proven twice in the field). A trivial / tightly-serial **solo** unit may still run inline when no sibling shares the repo ("adapt the fan-out").
- **Worktree-per-merge: the merge itself runs in a dedicated worktree created for that merge — never the shared main checkout.** Another session (or orchestration) may have the shared checkout on *any* branch, and a `rev-parse <branch>` name-check passes while you are not ON that branch — the merge lands wherever HEAD actually points. Create the worktree, merge, push, remove. *(Both rules are git instances of the general "exclusive lanes; reserved commitment surfaces" principle in `method.md` — apply the same shape in non-git media.)*

A repository with one clone cannot open a second worktree on its mainline branch. There, merge in the checkout that holds it, and assert in the merge's own command that `HEAD` resolves to that branch and the tree is clean.

## Cross-orchestration: the commitment lease
Builds/research parallelize freely (disjoint worktrees, **no locking**). **Only the commitment step takes a lease**, so two orchestrations — or one orchestrator running sibling projects — never merge/deploy/publish to the same target at once.

- **Acquire before, release after:**
  ```
  bus lock <repo>:<resource> --holder <orchestrator-handle>   # e.g. <repo>:deploy
  …do the merge / deploy / publish…
  bus unlock <repo>:<resource> --holder <orchestrator-handle>
  ```
- **Name the lease for the resource being serialized**, not the whole repo — `<repo>:mainline`, `<repo>:deploy`, `<repo>:publish`. Independent resources run in **parallel**; the *same* resource **waits**. Start with that small vocabulary; add names only when real contention appears.
- **If held:** `bus lock` exits non-zero and reports the holder + age. Wait and retry, or do other ready work first.
- **Staleness:** leases carry holder + timestamp + TTL (default 30 min). `bus locks` shows age/expiry; a past-TTL lease is auto-taken-over on the next `bus lock`, or force-released with `bus unlock <name> --steal` (logged) when you're sure the holder is dead.
- **Coarser fallback:** if naming resources is overkill for a repo, lock the repo itself (`bus lock <repo>`) — same primitive, less parallelism.

## In the loop
The orchestrator takes the relevant lease at its **serialization point** (`run.md`), holds it across the one commitment, and releases on success **or** rollback — recording the rev + rollback path in the workpaper. Holding a lease is itself a reversible, recorded action (it never needs a human gate).
