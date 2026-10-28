#!/usr/bin/env bash
# adhd-salah-nudge.sh PrayerName — run at iqamah by the transient timer from adhd-salah-schedule.sh.
# Hands over to `timetrack salah enforce`, which asks, warns, then locks (D8). The fallback
# below is the old PLAIN notification, used only when timetrack is not installed: no
# --wait/--action, because the caelestia (quickshell) notifier was probed 2026-07-18 and hangs
# on `notify-send --wait --action=...` for the full timeout, which would wedge this unit.
# Streak-positive wording, never guilt.
set -uo pipefail
name="${1:-Salah}"
tt="$(command -v timetrack || true)"
[ -z "$tt" ] && [ -x "$HOME/.nix-profile/bin/timetrack" ] && tt="$HOME/.nix-profile/bin/timetrack"
[ -n "$tt" ] && exec "$tt" salah enforce "$name"
notify-send "🕌 $name" "Iqamah time — log when you're back. (Super+Shift+; to log)" 2>/dev/null || true
