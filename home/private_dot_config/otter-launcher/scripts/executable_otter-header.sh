#!/usr/bin/env bash
# otter-header.sh — bluetuith-style header bar: title (left) + system stats
# (right-aligned) + a full-width divider underneath. Printed by otter header_cmd.
set -uo pipefail
e=$'\e'
purple="${e}[38;2;189;147;249m"
fg="${e}[38;2;248;248;242m"; dim="${e}[38;2;98;114;164m"; rst="${e}[0m"; bold="${e}[1m"
# Plane 16 is dots-icon-font (Phosphor outlines, this repo's own font) -- the icon set the
# bar and the shell prompt already draw from, so the launcher header is not a fourth style.
# Written as the literal character rather than a \x escape, which is how tmux.conf and
# starship.toml carry theirs; a byte escape hid these three from the glyph lint entirely
# while still painting Nerd Font private-use codepoints on screen.
# Each is one character wide, which the padding arithmetic below depends on.
i_load='􀨶'   # U+100A36 cpu-fill       (load, was a heart)
i_mem='􁔐'    # U+101510 memory-fill    (RAM, was a microchip)
i_up='􀣠'     # U+1008E0 clock-fill     (uptime)
host="$(hostname -s 2>/dev/null || echo dev)"
cores="$(nproc 2>/dev/null || echo 1)"
load="$(awk -v c="$cores" '{printf "%.0f", ($1/c)*100}' /proc/loadavg 2>/dev/null)"
mem="$(free -h --si 2>/dev/null | awk 'NR==2{print $3}')"
up="$(uptime -p 2>/dev/null | sed 's/up //; s/ weeks*/w/; s/ days*/d/; s/ hours*/h/; s/ minutes*/m/; s/,//g' | awk '{print $1}')"
cols="$(tput cols 2>/dev/null || echo 60)"

# plain (un-styled) strings, only for width measurement
lp="  ${USER}@${host}"
rp="${i_load} ${load}%  ${i_mem} ${mem}  ${i_up} ${up}  hypr"
pad=$(( cols - ${#lp} - ${#rp} - 1 )); [ "$pad" -lt 1 ] && pad=1

# header bar: user@host on the left, stats pushed to the right.
# All three stat icons take the SAME accent and all three values the same dim -- the load
# icon used to be pink while its neighbours were purple, which read as load being special
# when it is just the first of three numbers. Colour here marks the icon/value rhythm, not
# severity; nothing in this header knows what a bad number would be.
printf '  %s%s%s%s%s@%s%s%s%*s%s%s %s%s%%  %s%s %s%s  %s%s %s%s  %shypr%s\n' \
  "$bold" "$purple" "$USER" "$rst" "$dim" "$fg" "$host" "$rst" \
  "$pad" "" \
  "$purple" "$i_load" "$dim" "$load" \
  "$purple" "$i_mem" "$dim" "$mem" \
  "$purple" "$i_up" "$dim" "$up" \
  "$purple" "$rst"

# full-width divider underneath
dash="$(printf '\xe2\x94\x80')"
div="$(printf '%*s' "$((cols - 2))" '' | sed "s/ /$dash/g")"
printf '  %s%s%s\n' "$dim" "$div" "$rst"
