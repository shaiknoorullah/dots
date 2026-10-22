#!/usr/bin/env bash
# The dead-man's switch. Four layers, and the contract between the shell and the package.
#
# This is the test that matters most in the whole timetrack suite, because the failure it
# guards against is not a wrong number -- it is a modal with a compositor-enforced keyboard
# grab that will not go away, on the machine of someone who takes medication to be able to
# work at all. Every assertion is about the thing still dying.
#
# The reaper is tested through its real argv and a stubbed systemctl/hyprctl, not by
# reading it: layer 2 exists precisely for the case where the Python is broken, so a test
# that imported timetrack to check it would test the wrong thing.
# Not `set -e`: this suite deliberately runs commands that must FAIL (a refusal, a
# timeout) and inspects their exit codes. Failures are counted in `fails`.
set -uo pipefail
root="$(git rev-parse --show-toplevel)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
fails=0
fail() { echo "  $*"; fails=$((fails + 1)); }

export HOME="$tmp/home" XDG_DATA_HOME="$tmp/home/.local/share" XDG_CONFIG_HOME="$tmp/home/.config"
PANELS="$XDG_DATA_HOME/timetrack/panels"
SPOOL="$XDG_DATA_HOME/timetrack/attest-spool"
PANIC="$XDG_CONFIG_HOME/timetrack/PANIC"
mkdir -p "$PANELS" "$XDG_CONFIG_HOME/timetrack" "$tmp/bin" "$HOME/.local/bin"

# stubs: record what was called instead of doing it
cat > "$tmp/bin/systemctl" <<'EOF'
#!/bin/sh
echo "$*" >> "$STUB_LOG"
case "$*" in
  *"is-active --quiet timetrack-deadman.timer"*) exit "${STUB_TIMER_RC:-0}" ;;
esac
exit 0
EOF
cat > "$tmp/bin/hyprctl" <<'EOF'
#!/bin/sh
echo "hyprctl $*" >> "$STUB_LOG"
EOF
cat > "$tmp/bin/ghostty" <<'EOF'
#!/bin/sh
echo "ghostty $*" >> "$STUB_LOG"
: > "$STUB_LOG.ghostty-ran"
EOF
chmod +x "$tmp/bin"/*
export PATH="$tmp/bin:$PATH" STUB_LOG="$tmp/calls.log"
: > "$STUB_LOG"

cp "$root/home/private_dot_local/private_bin/executable_timetrack-deadman" "$HOME/.local/bin/timetrack-deadman"
cp "$root/home/private_dot_local/private_bin/executable_timetrack-panel"   "$HOME/.local/bin/timetrack-panel"
chmod +x "$HOME/.local/bin"/*
DEADMAN="$HOME/.local/bin/timetrack-deadman"
PANEL="$HOME/.local/bin/timetrack-panel"

spooled() { find "$SPOOL" -name '*.json' 2>/dev/null | wc -l | tr -d ' '; }
reason_of() { cat "$SPOOL"/*.json 2>/dev/null | tr -d ' ' | sed -n 's/.*"reason":"\([a-z_]*\)".*/\1/p' | head -1; }
reset() { rm -rf "$SPOOL" "$PANELS"; mkdir -p "$PANELS"; : > "$STUB_LOG"; rm -f "$PANIC"; }

echo "deadman:"

# 1. nothing to do. Must be silent and must write nothing: this runs every 30 seconds for
#    the life of the session, and a line per run buries the one line that matters.
reset
rc=0; out=$(sh "$DEADMAN" 2>&1) || rc=$?
[ "$rc" = 0 ] || fail "1: no panels exited $rc"
[ -z "$out" ] || fail "1: no panels printed '$out'"
[ "$(spooled)" = 0 ] || fail "1: no panels spooled something"

# 2. a fresh heartbeat is a live panel. Reaping it would kill a prompt the user is reading.
reset
: > "$PANELS/fresh.hb"
sh "$DEADMAN" >/dev/null 2>&1
[ -e "$PANELS/fresh.hb" ] || fail "2: reaped a LIVE panel"
grep -q "stop timetrack-panel-fresh" "$STUB_LOG" && fail "2: stopped a live panel"
[ "$(spooled)" = 0 ] || fail "2: spooled for a live panel"

# 3. a stale heartbeat is a frozen panel: the unit is stopped, the fact is recorded, the
#    heartbeat is cleared so the next run does not reap it again.
reset
: > "$PANELS/wedged.hb"
touch -d "@$(( $(date +%s) - 300 ))" "$PANELS/wedged.hb"
out=$(sh "$DEADMAN" 2>&1)
grep -q "stop timetrack-panel-wedged.service" "$STUB_LOG" || fail "3: did not stop the unit"
[ -e "$PANELS/wedged.hb" ] && fail "3: left the heartbeat behind"
[ "$(spooled)" = 1 ] || fail "3: spooled $(spooled) facts, want 1"
[ "$(reason_of)" = deadman_reap ] || fail "3: reason was '$(reason_of)'"
case $out in *"reaped panel wedged"*) ;; *) fail "3: said nothing about it: '$out'" ;; esac

# 4. LAYER 3 WITH TEETH. PANIC reaps every panel on the next cycle regardless of age --
#    this is what takes down a grab already on screen without needing hyprctl or a working
#    keybind, and it is why `touch` is the documented escape.
reset
: > "$PANELS/grabbing.hb"
sh "$DEADMAN" >/dev/null 2>&1
[ -e "$PANELS/grabbing.hb" ] || fail "4: reaped before PANIC was set"
: > "$PANIC"
sh "$DEADMAN" >/dev/null 2>&1
[ -e "$PANELS/grabbing.hb" ] && fail "4: PANIC did not reap a fresh panel"
[ "$(reason_of)" = panic_engaged ] || fail "4: reason was '$(reason_of)'"

# 5. THE CROSS-LANGUAGE CONTRACT. The reaper writes JSON with printf because it may not
#    import the package; the package must be able to read exactly that. If this drifts, a
#    reap is recorded nowhere and the timer looks like it is working.
reset
: > "$PANELS/contract.hb"
touch -d "@$(( $(date +%s) - 300 ))" "$PANELS/contract.hb"
sh "$DEADMAN" >/dev/null 2>&1
( cd "$root/nix/pkgs/timetrack" && TIMETRACK_STATE_DIR="$XDG_DATA_HOME/timetrack" python3 - <<'PY'
import time
from datetime import date, datetime
from timetrack import attest, dbs, migrate
migrate.migrate()
conn = dbs.open_work()
now = int(time.time())
conn.execute("INSERT INTO work_session(opened_ts,opened_unix,attendance_day,open_trigger,"
             "provenance) VALUES(?,?,?,?,?)",
             (datetime.fromtimestamp(now - 3600).isoformat(timespec="seconds"), now - 3600,
              date.fromtimestamp(now).isoformat(), "test", "live"))
conn.commit()
rep = attest.drain(conn)
assert rep["written"] == 1, f"the package could not read the reaper's JSON: {rep}"
assert rep["bad"] == 0, f"reason_code rejected: {rep}"
row = conn.execute("SELECT reason_code, kind FROM clock_event").fetchone()
assert (row["kind"], row["reason_code"]) == ("attest", "deadman_reap"), tuple(row)
PY
) || fail "5: the reaper's spool format is not what the package parses"

echo "panel:"

# 6. THE REFUSAL. A keyboard-grabbing modal may not exist without its reaper. If this
#    check ever stops working, every other layer is one bug away from irrelevant.
reset
rc=0; out=$(STUB_TIMER_RC=3 sh "$PANEL" refuse "Title" -- true 2>&1) || rc=$?
[ "$rc" = 4 ] || fail "6: launched with no reaper (exit $rc)"
[ -e "$STUB_LOG.ghostty-ran" ] && fail "6: DREW A PANEL with no reaper running"
case $out in *deadman*) ;; *) fail "6: refusal did not name the reaper" ;; esac

# 7. LAYER 3, before anything is drawn. PANIC outranks the launch entirely, and the
#    stand-down is itself recorded.
reset
: > "$PANIC"
rc=0; out=$(sh "$PANEL" panicky "Title" -- true 2>&1) || rc=$?
[ "$rc" = 3 ] || fail "7: PANIC did not stop the launch (exit $rc)"
[ -e "$STUB_LOG.ghostty-ran" ] && fail "7: drew a panel while PANIC was engaged"
[ "$(reason_of)" = panic_engaged ] || fail "7: stand-down not recorded ('$(reason_of)')"

# 8. a timeout is not an answer. No answer file -> exit 2 and a prompt_timeout fact, never
#    a fabricated reply. This is the whole reason the launcher owns the answer protocol.
reset
cat > "$tmp/bin/systemd-run" <<'EOF'
#!/bin/sh
echo "systemd-run $*" >> "$STUB_LOG"
exit 143
EOF
chmod +x "$tmp/bin/systemd-run"
rc=0; out=$(sh "$PANEL" timeouty "Title" -- true 2>&1) || rc=$?
[ "$rc" = 2 ] || fail "8: a timeout did not report no-answer (exit $rc)"
[ "$(reason_of)" = prompt_timeout ] || fail "8: reason was '$(reason_of)'"
grep -q "RuntimeMaxSec=600" "$STUB_LOG" || fail "8: layer 1 ceiling not passed to systemd"
grep -q -- "--class=dots.block" "$STUB_LOG" || fail "8: panel is not a dots.block window"

if [ "$fails" != 0 ]; then echo "deadman FAILED ($fails)"; exit 1; fi
echo "deadman ok (8 scenarios, 26 assertions)"
