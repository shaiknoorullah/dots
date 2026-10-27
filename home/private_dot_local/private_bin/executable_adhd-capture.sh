#!/usr/bin/env bash
# adhd-capture.sh (Super+Shift+A) — dump a thought into the backlog without leaving the task
# you are on. One line, Enter, gone. The Control Center shows the backlog.
#
# Appends straight to a markdown file in the vault. It used to POST to the Obsidian Local
# REST plugin, which was never installed, and every failure was swallowed by `|| true` --
# so every capture since it was written went nowhere while a notification said "Captured".
# A file append has no server to be down.
#
#   text              -> backlog
#   !text             -> backlog, high priority
#   t: / task: text   -> backlog, and a taskwarrior task in the current context
#   #tags anywhere    -> kept as tags
set -euo pipefail

TASK=task  # taskwarrior (go-task shadows `task` in PATH)
VAULT="${POWERHOUSE:-$HOME/powerhouse}"
FILE="$VAULT/backlog/backlog.md"
CTX=$(cat "$HOME/.cache/ctx" 2>/dev/null || echo personal)

text=$(fzf --print-query --prompt=$'􀜌  backlog ' --header=$'􁙚  dump it, get back to work · !urgent  #tag' \
        --height=100% --reverse --no-info < /dev/null | head -1) || true
text="$(printf '%s' "$text" | tr -d '\r\n')"
[ -n "${text// }" ] || exit 0

prio=normal
case "$text" in '!'*) prio=high; text="${text#!}" ;; esac
case "$text" in
  t:*|task:*) text="${text#*:}"; "$TASK" add project:"$CTX" "$text" +next >/dev/null 2>&1 || true ;;
esac
text="${text# }"

mkdir -p "$(dirname "$FILE")"
line="- [ ] $text [captured:: $(date +%Y-%m-%dT%H:%M)] [priority:: $prio] [context:: $CTX]"
# flock: two captures in the same second must not interleave half-lines
(
  flock 9
  [ -s "$FILE" ] || printf '# Backlog\n\nThoughts dumped mid-task (Super+Shift+A). Tick, promote, or delete.\n\n' > "$FILE"
  printf '%s\n' "$line" >> "$FILE"
) 9>"$FILE.lock"

notify-send -a backlog -t 2500 "Backlog" "$text" 2>/dev/null || true
