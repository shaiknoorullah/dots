#!/usr/bin/env bash
# claude-notify.sh — Claude Code `Notification` hook → desktop notification
#
# Outside a multiplexer, Ghostty turns Claude Code's terminal notifications into desktop ones
# itself. Inside tmux or herdr those escape sequences never reach Ghostty, so this hook calls
# notify-send directly, tagged with where the pane lives. Clicking the notification focuses the
# terminal window (Hyprland: switching workspace if needed) and jumps the multiplexer back to
# the pane that raised it.
#
# Lives in dots-mux because it serves both multiplexers (and so its icon is not dropped by the
# herdr profile, which ignores .config/tmux). Wire up in ~/.claude/settings.json:
#   "hooks": { "Notification": [ { "hooks": [ { "type": "command",
#     "command": "~/.config/dots-mux/claude-notify.sh" } ] } ] }

if   [[ -n "${TMUX:-}" && -n "${TMUX_PANE:-}" ]]; then MUX=tmux
elif [[ -n "${HERDR_PANE_ID:-}" ]];                then MUX=herdr
else exit 0   # not in a multiplexer: Ghostty already raised it, don't duplicate
fi
command -v notify-send &>/dev/null || exit 0

icon="$(dirname "$(readlink -f "$0")")/assets/claude.png"
[[ -f "$icon" ]] || icon=utilities-terminal

input=$(cat)
field() { jq -r "$1 // empty" <<<"$input" 2>/dev/null; }
message=$(field .message)
title=$(field .title)
cwd=$(field .cwd)
[[ -n "$message" ]] || message="Claude needs your attention"
[[ -n "$title" ]] || title="Claude Code"
project=${cwd:+$(basename "$cwd")}

# Untrusted text (model/tool output): neutralise notification markup (&, <, >)
# and tmux format expansion (#(cmd), #{...}) before it reaches either sink.
pango_esc() { local s=$1; s=${s//&/"&amp;"}; s=${s//</"&lt;"}; s=${s//>/"&gt;"}; printf '%s' "$s"; }
nl=$'\n'

# ── Per-multiplexer: where the pane lives, an in-mux toast, the client pids that
#    might own a Hyprland window, and the jump back to the pane. ───────────────
if [[ $MUX == tmux ]]; then
    location=$(tmux display-message -p -t "$TMUX_PANE" '#S:#I #W' 2>/dev/null)
    mux_toast() { local m=${1//\#/##}; tmux display-message -t "$TMUX_PANE" -d 4000 "󰚩 $m" 2>/dev/null; }
    # A client already on the pane's session wins, else the most recently active one.
    mux_clients() {
        local session; session=$(tmux display-message -p -t "$TMUX_PANE" '#S' 2>/dev/null)
        tmux list-clients -F '#{client_activity}\t#{client_session}\t#{client_name}\t#{client_pid}' 2>/dev/null |
            awk -F'\t' -v s="$session" '{ print ($2 == s ? 1 : 0) "\t" $0 }' | sort -t$'\t' -k1,1nr -k2,2nr |
            awk -F'\t' '{ print $4 "\t" $5 }'
    }
    mux_jump() {
        local client=$1
        tmux switch-client ${client:+-c "$client"} -t "$TMUX_PANE" 2>/dev/null
        tmux select-window -t "$TMUX_PANE" 2>/dev/null
        tmux select-pane -t "$TMUX_PANE" 2>/dev/null
    }
else
    hget() { herdr "$1" get "$2" 2>/dev/null | jq -r "$3 // empty" 2>/dev/null; }
    ws_label=$(hget workspace "$HERDR_WORKSPACE_ID" '.result.workspace.label')
    tab_no=$(hget tab "$HERDR_TAB_ID" '.result.tab.number')
    tab_label=$(hget tab "$HERDR_TAB_ID" '.result.tab.label')
    location="${ws_label:-$HERDR_WORKSPACE_ID}${tab_no:+:$tab_no}${tab_label:+ $tab_label}"
    mux_toast() { local m=${1//[$'\n\r\t']/ }; herdr notification show "Claude Code" --body="$m" --sound=request >/dev/null 2>&1; }
    # herdr panes are children of the server, so the window belongs to a client process:
    # one whose argv is just the herdr binary, with no subcommand.
    mux_clients() {
        local real p
        real=$(readlink -f "$(command -v herdr)" 2>/dev/null) || return 0
        # own processes only, and the executable must really be that binary: argv[0] is attacker-settable
        ps -u "$(id -u)" -o pid=,args= | awk 'NF==2 && $2 ~ /(^|\/)herdr$/ { print $1 }' |
        while read -r p; do
            [[ "$(readlink -f "/proc/$p/exe" 2>/dev/null)" == "$real" ]] && printf -- '-\t%s\n' "$p"
        done
    }
    # Pane-level focus is directional only in herdr, so this lands on the right tab, not the
    # exact pane; a single-pane tab (the usual agent layout) is therefore exact.
    mux_jump() { herdr workspace focus "$HERDR_WORKSPACE_ID" >/dev/null 2>&1
                 herdr tab focus "$HERDR_TAB_ID" >/dev/null 2>&1; }
fi

summary="$(pango_esc "$title${project:+ · $project}")"
body="$(pango_esc "$message")${location:+$nl<i>$MUX $(pango_esc "$location")</i>}"

mux_toast "$message"

# Hyprland: focus the terminal window hosting the multiplexer client — this also switches to its
# workspace. Panes often carry a stale/empty HYPRLAND_INSTANCE_SIGNATURE, so fall back to the live socket.
focus_terminal() {
    local client_pid=$1 pid address d windows
    [[ $client_pid =~ ^[0-9]+$ ]] || return 1   # [[ x -gt 1 ]] evaluates arithmetic: keep it numeric
    command -v hyprctl &>/dev/null || return 1
    if [[ ! -S "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/hypr/${HYPRLAND_INSTANCE_SIGNATURE:-}/.socket.sock" ]]; then
        for d in "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"/hypr/*/; do
            [[ -S "$d.socket.sock" ]] && export HYPRLAND_INSTANCE_SIGNATURE=$(basename "$d")
        done
    fi
    windows=$(hyprctl clients -j 2>/dev/null) || return 1
    pid=$client_pid
    while [[ -n "$pid" && "$pid" -gt 1 ]]; do
        address=$(jq -r --argjson p "$pid" 'map(select(.pid == $p)) | .[0].address // empty' <<<"$windows")
        [[ -n "$address" ]] && break
        pid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
        [[ $pid =~ ^[0-9]+$ ]] || return 1
    done
    [[ -n "$address" ]] || return 1
    # Lua dispatcher (Hyprland 0.55+) first; fall back to the pre-Lua syntax
    [[ "$(hyprctl dispatch "hl.dsp.focus({ window = \"address:$address\" })" 2>/dev/null)" == ok ]] ||
        hyprctl dispatch focuswindow "address:$address" &>/dev/null
}

# Detach from the hook so Claude isn't blocked waiting on the click.
(
    action=$(notify-send --app-name="Claude Code" --icon="$icon" \
        --urgency=normal --expire-time=10000 \
        --action=default=Focus --wait -- \
        "$summary" "$body" 2>/dev/null)
    if [[ "$action" == "default" ]]; then
        client=""
        while IFS=$'\t' read -r name pid; do
            [[ -n "$pid" ]] || continue
            if focus_terminal "$pid"; then client=$name; break; fi
        done < <(mux_clients)
        [[ "$client" == "-" ]] && client=""
        mux_jump "$client"
    fi
) </dev/null &>/dev/null &
disown

exit 0
