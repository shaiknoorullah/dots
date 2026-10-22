#!/usr/bin/env bash
# claude-notify.sh — Claude Code `Notification` hook → desktop notification
#
# Outside a multiplexer, Ghostty turns Claude Code's terminal notifications into desktop ones
# itself. Inside tmux or herdr those escape sequences never reach Ghostty, so this hook calls
# notify-send directly, tagged with where the pane lives.
#
# Clicking is handled by claude-focus.sh, which SwayNC runs on action (config.json:
# scripts."claude-focus".run-on = "action"). This hook only records where to jump, then returns.
# It must NOT wait for the click: `notify-send --action` implies --wait, a hook that blocks would
# stall Claude, and a backgrounded listener gets orphaned and never receives ActionInvoked — which
# is exactly why clicking used to do nothing.
#
# Wire up in ~/.claude/settings.json:
#   "hooks": { "Notification": [ { "hooks": [ { "type": "command",
#     "command": "$HOME/.config/dots-mux/claude-notify.sh" } ] } ] }

if   [[ -n "${TMUX:-}" && -n "${TMUX_PANE:-}" ]]; then MUX=tmux
elif [[ -n "${HERDR_PANE_ID:-}" ]];                then MUX=herdr
else exit 0   # not in a multiplexer: Ghostty already raised it, don't duplicate
fi
command -v notify-send &>/dev/null || exit 0

icon="$(dirname "$(readlink -f "$0")")/assets/claude.png"
[[ -f "$icon" ]] || icon=utilities-terminal

input=$(cat)
field() { jq -r "$1 // empty" <<<"$input" 2>/dev/null; }
message=$(field .message); title=$(field .title); cwd=$(field .cwd)
[[ -n "$message" ]] || message="Claude needs your attention"
[[ -n "$title" ]]   || title="Claude Code"
project=${cwd:+$(basename "$cwd")}

# Untrusted text (model/tool output): neutralise notification markup before it reaches either sink.
pango_esc() { local s=$1; s=${s//&/"&amp;"}; s=${s//</"&lt;"}; s=${s//>/"&gt;"}; printf '%s' "$s"; }
nl=$'\n'

state_dir="${XDG_CACHE_HOME:-$HOME/.cache}/dots"
mkdir -p "$state_dir"
state="$state_dir/claude-notify-target"

if [[ $MUX == tmux ]]; then
    location=$(tmux display-message -p -t "$TMUX_PANE" '#S:#I #W' 2>/dev/null)
    toast=${message//\#/##}
    tmux display-message -t "$TMUX_PANE" -d 4000 "✦ $toast" 2>/dev/null
    read -r client client_pid < <(
        session=$(tmux display-message -p -t "$TMUX_PANE" '#S' 2>/dev/null)
        tmux list-clients -F '#{client_activity}	#{client_session}	#{client_name}	#{client_pid}' 2>/dev/null |
            awk -F'\t' -v s="$session" '{ print ($2 == s ? 1 : 0) "\t" $0 }' |
            sort -t$'\t' -k1,1nr -k2,2nr | awk -F'\t' '{ print $4, $5 }')
    { printf 'MUX=tmux\n'
      printf 'TMUX_TARGET=%q\n' "$TMUX_PANE"
      printf 'TMUX_CLIENT=%q\n' "${client:-}"
      printf 'CLIENT_PIDS=%q\n' "${client_pid:-}"
    } > "$state"
else
    hget() { herdr "$1" get "$2" 2>/dev/null | jq -r "$3 // empty" 2>/dev/null; }
    ws_label=$(hget workspace "$HERDR_WORKSPACE_ID" '.result.workspace.label')
    tab_no=$(hget tab "$HERDR_TAB_ID" '.result.tab.number')
    tab_label=$(hget tab "$HERDR_TAB_ID" '.result.tab.label')
    location="${ws_label:-$HERDR_WORKSPACE_ID}${tab_no:+:$tab_no}${tab_label:+ $tab_label}"
    toast=${message//[$'\n\r\t']/ }
    herdr notification show "Claude Code" --body "$toast" --sound request >/dev/null 2>&1
    # herdr panes are children of the server, so the window belongs to a client process: one whose
    # argv is just the herdr binary. Own processes only, and /proc/PID/exe must be that binary --
    # argv[0] is attacker-settable.
    real=$(readlink -f "$(command -v herdr)" 2>/dev/null)
    pids=$(ps -u "$(id -u)" -o pid=,args= | awk 'NF==2 && $2 ~ /(^|\/)herdr$/ { print $1 }' |
           while read -r p; do
               [[ "$(readlink -f "/proc/$p/exe" 2>/dev/null)" == "$real" ]] && printf '%s ' "$p"
           done)
    { printf 'MUX=herdr\n'
      printf 'HERDR_WS=%q\n'  "$HERDR_WORKSPACE_ID"
      printf 'HERDR_TAB=%q\n' "$HERDR_TAB_ID"
      printf 'CLIENT_PIDS=%q\n' "${pids% }"
    } > "$state"
fi
chmod 600 "$state" 2>/dev/null

summary="$(pango_esc "$title${project:+ · $project}")"
body="$(pango_esc "$message")${location:+$nl<i>$MUX $(pango_esc "$location")</i>}"

# --action is what makes a body click count as an action, so SwayNC runs claude-focus.sh. It implies
# --wait, so this is detached and ignored: nothing depends on it surviving any more.
setsid -f notify-send --app-name="Claude Code" --icon="$icon" \
    --urgency=normal --expire-time=10000 --action=default=Focus \
    -- "$summary" "$body" </dev/null >/dev/null 2>&1

exit 0
