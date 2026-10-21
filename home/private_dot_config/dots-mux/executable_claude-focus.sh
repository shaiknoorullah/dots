#!/usr/bin/env bash
# claude-focus.sh — run by SwayNC when a Claude Code notification is actioned
# (config.json: scripts."claude-focus".run-on = "action").
#
# WHY this is not done in claude-notify.sh: that would need `notify-send --wait` to stay alive and
# receive ActionInvoked over D-Bus. The hook has to return immediately so Claude is not blocked, so
# the listener gets orphaned and never receives the action — the click then does nothing. SwayNC
# runs this instead, so nothing has to survive the hook.
#
# The hook leaves its target here; this reads it and jumps. Last write wins: if several agents
# notify before you click, this focuses the most recent one.
set -uo pipefail
state="${XDG_CACHE_HOME:-$HOME/.cache}/dots/claude-notify-target"
[[ -r $state ]] || exit 0
# shellcheck disable=SC1090
source "$state"   # sets MUX, and HERDR_* or TMUX_PANE, plus CLIENT_PIDS

focus_window() {   # walk up from a multiplexer client pid to the process owning a Hyprland window
    local pid=$1 address windows d
    [[ $pid =~ ^[0-9]+$ ]] || return 1
    command -v hyprctl &>/dev/null || return 1
    if [[ ! -S "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/hypr/${HYPRLAND_INSTANCE_SIGNATURE:-}/.socket.sock" ]]; then
        for d in "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"/hypr/*/; do
            [[ -S "$d.socket.sock" ]] && export HYPRLAND_INSTANCE_SIGNATURE=$(basename "$d")
        done
    fi
    windows=$(hyprctl clients -j 2>/dev/null) || return 1
    while [[ $pid =~ ^[0-9]+$ && $pid -gt 1 ]]; do
        address=$(jq -r --argjson p "$pid" 'map(select(.pid == $p)) | .[0].address // empty' <<<"$windows")
        [[ -n $address ]] && break
        pid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
    done
    [[ -n ${address:-} ]] || return 1
    # Hyprland 0.55+ Lua dispatcher first, then the pre-Lua syntax
    [[ "$(hyprctl dispatch "hl.dsp.focus({ window = \"address:$address\" })" 2>/dev/null)" == ok ]] ||
        hyprctl dispatch focuswindow "address:$address" &>/dev/null
}

for p in ${CLIENT_PIDS:-}; do focus_window "$p" && break; done

case ${MUX:-} in
  herdr)
    [[ -n ${HERDR_WS:-}  ]] && herdr workspace focus "$HERDR_WS"  >/dev/null 2>&1
    [[ -n ${HERDR_TAB:-} ]] && herdr tab focus       "$HERDR_TAB" >/dev/null 2>&1
    ;;
  tmux)
    [[ -n ${TMUX_TARGET:-} ]] || exit 0
    tmux switch-client ${TMUX_CLIENT:+-c "$TMUX_CLIENT"} -t "$TMUX_TARGET" 2>/dev/null
    tmux select-window -t "$TMUX_TARGET" 2>/dev/null
    tmux select-pane   -t "$TMUX_TARGET" 2>/dev/null
    ;;
esac
exit 0
