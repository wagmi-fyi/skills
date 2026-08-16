#!/usr/bin/env bash
# orchestrate/bus-watch — push-wake daemon. On a new bus message, poke the
# recipient's tmux pane to check its inbox. Run once per machine, in a spare shell.
# Requires: tmux (sessions hosted in tmux) + fswatch.
#
# Usage:
#   bus-watch.sh [run]    run the watcher in the foreground (what run.md backgrounds)
#   bus-watch.sh status   report whether a watcher is running
#   bus-watch.sh stop     stop the running watcher
#   bus-watch.sh restart  stop, then relaunch a fresh detached watcher
set -uo pipefail
SKILL="$(cd "$(dirname "$0")/.." && pwd)"
SELF="$SKILL/scripts/bus-watch.sh"
BUS_CLI="$SKILL/scripts/bus"
BUS_DIR="${SESSION_BUS_DIR:-$HOME/.claude/session-bus}"
INBOX="$BUS_DIR/inbox"
LOCK="$BUS_DIR/.watch.pid"
LOG="$BUS_DIR/.watch.log"

# Echo the live watcher's pid, or return non-zero. Validates the pidfile pid is
# actually alive AND is the watcher (guards against a recycled pid).
watcher_pid() {
  local pid; pid="$(cat "$LOCK" 2>/dev/null || true)"
  [ -n "${pid:-}" ] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  ps -p "$pid" -o command= 2>/dev/null | grep -q 'bus-watch' || return 1
  printf '%s\n' "$pid"
}

# Stop by PID: TERM (then KILL) the watcher pid + its direct children (the fswatch
# and the read-loop subshell). Targeted by pid only — never pattern-matches, so it
# cannot kill a caller whose own argv contains "bus-watch.sh"/"fswatch".
stop_watcher() {
  local pid kids p
  pid="$(watcher_pid)" || { echo "no watcher running"; rm -f "$LOCK"; return 0; }
  kids="$(pgrep -P "$pid" 2>/dev/null | tr '\n' ' ')"
  echo "stopping watcher pid $pid (children: ${kids:-none})"
  kill -TERM $pid $kids 2>/dev/null
  sleep 1
  for p in $pid $kids; do kill -0 "$p" 2>/dev/null && kill -KILL "$p" 2>/dev/null; done
  rm -f "$LOCK"
  echo "stopped"
}

status_watcher() {
  local pid
  if pid="$(watcher_pid)"; then
    echo "watcher running: pid $pid"
    pgrep -P "$pid" 2>/dev/null | while read -r c; do ps -p "$c" -o pid=,command=; done
  else
    echo "watcher not running"
  fi
}

run_watcher() {
  command -v fswatch >/dev/null 2>&1 || { echo "fswatch not found — brew/apt install fswatch" >&2; exit 1; }
  command -v tmux    >/dev/null 2>&1 || { echo "tmux not found — push-wake needs tmux" >&2; exit 1; }
  mkdir -p "$INBOX"

  # single-instance guard — safe to auto-start; refuses a second watcher on the same bus dir
  if [ -f "$LOCK" ] && kill -0 "$(cat "$LOCK" 2>/dev/null)" 2>/dev/null; then
    echo "bus-watch already running (pid $(cat "$LOCK")) — not starting a second"; exit 0
  fi
  echo $$ > "$LOCK"; trap 'rm -f "$LOCK"' EXIT

  echo "orchestrate push-watcher up — machine-level (serves ALL orchestrations on this bus) — watching $INBOX  (Ctrl-C to stop)"

  fswatch -0 "$INBOX" | while read -r -d '' path; do
    case "$path" in *.jsonl) ;; *) continue ;; esac
    handle="$(basename "$path" .jsonl)"
    pane="$("$BUS_CLI" pane "$handle" 2>/dev/null || true)"
    if [ -n "${pane:-}" ] && tmux has-session >/dev/null 2>&1; then
      # Split the keystroke: send the text literally (-l), pause, then Enter as its own
      # keypress. A single "text + Enter" send-keys gets coalesced into one chunk on a
      # busy pane — the TUI then reads the trailing newline as a literal newline in the
      # composer (paste semantics) instead of a submit, so the poke lands but never
      # sends. The delayed, separate Enter registers as a real keypress even while the
      # pane is mid-generation. Do NOT recombine these into one send-keys.
      if tmux send-keys -t "$pane" -l "[bus] new mail — run: bus inbox $handle and act on it" 2>/dev/null; then
        sleep 0.5
        tmux send-keys -t "$pane" Enter 2>/dev/null
        echo "poked $handle ($pane)"
      else
        echo "poke failed: $handle ($pane) — pane may be gone"
      fi
    else
      echo "no registered pane for $handle — skipping (it can still pull on its own)"
    fi
  done
}

case "${1:-run}" in
  run)     run_watcher ;;
  stop)    stop_watcher ;;
  status)  status_watcher ;;
  restart) stop_watcher; echo "relaunching detached…"; ( nohup bash "$SELF" run > "$LOG" 2>&1 < /dev/null & ); sleep 1; status_watcher ;;
  *) echo "usage: bus-watch.sh [run|stop|status|restart]" >&2; exit 2 ;;
esac
