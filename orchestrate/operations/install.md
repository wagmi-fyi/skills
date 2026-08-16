# Install — stand up the substrate

Make this machine ready to orchestrate. **Idempotent** — safe to re-run.

## Intent
The bus + tmux substrate exist and pass a health-check, so `bootstrap`/`run`/`delegate` work. Report which capabilities the machine has → which `spawn_mode` + wake tier are available.

## Steps
1. **Bus.** Resolve `bus_dir` (config; default `~/.claude/session-bus`). `scripts/bus handles` — if it runs, the bus is live (the CLI creates its dirs on first use).
2. **Python 3.** The bus needs `python3` (stdlib only): `python3 --version`. Windows uses the automatic portable-lock shim — no action.
3. **tmux** (visible spawn + push-wake): `command -v tmux`. If absent: `brew install tmux` / `apt install tmux` / WSL on Windows. Without tmux: bus + manual nudge still work; auto-spawn + push degrade (set `spawn_mode: manual`).
4. **fswatch** (push-wake only): `command -v fswatch`. If you want zero-touch wake and it's absent: `brew install fswatch` / `apt install fswatch`. Optional.
5. **git + claude** (used by the work itself, not the bus): `command -v git` (worktree/branch lanes for build units) and `command -v claude` (spawning delegate sessions) — normally already present; flag if missing.
6. **Shell convenience** (optional, ask first — never edit their rc unprompted):
   - **`bus` alias:** `alias bus='~/.claude/skills/orchestrate/scripts/bus'`.
   - **Auto-tmux launch** — so plain `claude` always opens inside tmux (enables visible spawn + push; sessions survive terminal close). A zsh function (adapt for bash; preserve whatever flags the human already defaults to):
     ```zsh
     claude() {
       if [[ -n "$TMUX" || ! -t 1 || " $* " == *" -p "* || " $* " == *" --print "* ]]; then
         command claude "$@"                                       # in tmux / piped / headless -> run directly
       else
         local cmd="" a; for a in "$@"; do cmd+=" ${(q)a}"; done
         tmux new-session -A -s "claude-$$" "command claude${cmd}" # fresh session per launch; `spawn` adds windows
       fi
     }
     ```
   - **Mouse mode** (click panes/windows, drag to resize — no tmux keys to memorize): add `set -g mouse on` to `~/.tmux.conf`; for an already-running server, `tmux set -g mouse on`. (macOS: Option-drag for a native text selection.)
7. **Push daemon** (optional, if tmux+fswatch present): background one per machine — `nohup scripts/bus-watch.sh &`; check with `bus-watch.sh status`, bounce with `bus-watch.sh restart`.

## Health-check (report evidence)
```
bus send _selftest _selftest ping ok && bus inbox _selftest   # shows 1 msg
bus inbox _selftest                                            # shows none (cursor works)
```
Report: bus ✅ + python3 version + tmux? + fswatch? + push-daemon running? → the available `spawn_mode` and wake tier. (The `_selftest` files under `bus_dir` are throwaway; remove if you like.)
