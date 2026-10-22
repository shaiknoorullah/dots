#!/usr/bin/env bash
# otter-power.sh — power menu (fzf-based otter-launcher module)
#
# Same actions/commands as legacy-menu-power.sh, ported to fzf: lock and suspend
# fire immediately, reboot/shutdown/logout go through a Yes/No confirm
# first so a stray Enter can't take down the session.
set -uo pipefail
source "$HOME/.config/otter-launcher/scripts/_otter-fzf.sh"

# A top-level destination list, so every item carries an icon (second-level action lists
# do not -- see _otter-fzf.sh). All five come from dots-icon-font at plane 16.
#
# The comment that used to sit here claimed these matched legacy-menu-power.sh "same
# codepoints, same glyphs", and had not been true since the glyph purge: lock and reboot
# had no icon at all, suspend and shutdown had stray Unicode technical symbols drawn in a
# different style from everything else on this desktop, and logout was still a Nerd Font
# private-use codepoint (U+F0343) -- written as a \U escape, which is the only reason the
# glyph lint never saw it. These strings are also the case-match keys below, so they are
# written once here and matched by variable, never by literal.
lock=$'􁐦  lock'
suspend=$'􁖔  suspend'
logout=$'􁱦  logout'
reboot=$'􀋈  reboot'
shutdown=$'􁧘  shutdown'

options="$lock
$suspend
$logout
$reboot
$shutdown"

# confirm_action — Yes/No fzf gate for destructive actions.
# Parameters: $1 — message shown in the header.
# Returns: 0 if "Yes" was chosen, 1 otherwise (including Esc/no selection).
confirm_action() {
    local answer
    answer="$(printf 'Yes\nNo\n' | fzf --header="􂊖  $1" --prompt=$'􀜌  ')"
    [[ "$answer" == "Yes" ]]
}

chosen="$(echo "$options" | fzf --header=$'􁧘  power')"

case "$chosen" in
    "$lock")
        setsid -f hyprlock >/dev/null 2>&1
        ;;
    "$suspend")
        systemctl suspend
        ;;
    "$logout")
        confirm_action 'Logout?' && hyprctl dispatch 'hl.dsp.exit()'
        ;;
    "$reboot")
        confirm_action 'Reboot?' && systemctl reboot
        ;;
    "$shutdown")
        confirm_action 'Shutdown?' && systemctl poweroff
        ;;
esac
