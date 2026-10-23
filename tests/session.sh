#!/usr/bin/env bash
# The work_session lifecycle (D1): adoption on restart, and the back-dated quiet close.
#
# D1 says a session opens on activity, closes after ~150 minutes of quiet, and is
# BACK-DATED to the last activity -- and that its attendance_day is then inherited by every
# event. Two ways to get that wrong, both tested here:
#
#   closing at `now` instead of at the last activity CREDITS the quiet. On a day that ends
#   at 22:30 and is noticed at 01:00, that is two and a half hours of invented work in a
#   figure handed to an employer.
#
#   not adopting the open session on restart opens a SECOND one for the same day, so two
#   rows both claim to own that day's events and whichever is picked last wins.
set -uo pipefail
root="$(git rev-parse --show-toplevel)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$root/nix/pkgs/timetrack"
export TIMETRACK_STATE_DIR="$tmp/state" XDG_CONFIG_HOME="$tmp/config"
python3 - <<'PY'
import time
from datetime import date, datetime

from timetrack import attest, clock_state, clockd, config, dbs, migrate

fails = []
def check(name, got, want):
    if got != want: fails.append(f"{name}: got {got!r}, want {want!r}")

migrate.migrate()
conn = dbs.open_work()
now = int(time.time())
def iso(t): return datetime.fromtimestamp(t).isoformat(timespec="seconds")

def fresh():
    conn.execute("DELETE FROM clock_event")
    conn.execute("DELETE FROM work_session")
    conn.commit()
    for p in attest.SPOOL.glob("*.json"):
        p.unlink()

def seed(opened, last, day=None):
    day = day or date.fromtimestamp(opened).isoformat()
    cur = conn.execute(
        "INSERT INTO work_session(opened_ts,opened_unix,attendance_day,open_trigger,"
        "provenance) VALUES(?,?,?,?,?)", (iso(opened), opened, day, "clockd", "live"))
    sid = cur.lastrowid
    for kind, t in (("session_open", opened), ("run_start", opened + 60),
                    ("run_stop", last)):
        conn.execute(
            "INSERT INTO clock_event(ts,ts_unix,attendance_day,session_id,kind,actor,"
            "provenance,ruleset,confidence) VALUES(?,?,?,?,?,'human','live',?,1.0)",
            (iso(t), t, day, sid, kind, config.RULESET))
    conn.commit()
    return sid, day

# 1. a session whose last activity was minutes ago is ADOPTED, not duplicated.
fresh()
sid, day = seed(now - 3600, now - 300)
d = clockd.Clockd(conn)
check("adopted", d.adopt_session(now), "adopted")
check("same session", d.session_id, sid)
check("inherits its day", d.attendance_day, day)
check("no second session", conn.execute("SELECT COUNT(*) FROM work_session").fetchone()[0], 1)

# 2. quiet past the threshold: closed, and closed WHERE THE WORK STOPPED.
fresh()
last = now - (200 * 60)
sid, day = seed(now - (300 * 60), last)
d = clockd.Clockd(conn)
check("closed", d.adopt_session(now), "closed")
row = conn.execute("SELECT closed_unix, close_trigger FROM work_session WHERE id=?",
                   (sid,)).fetchone()
check("BACK-DATED to the last activity", row["closed_unix"], last)
check("not stamped now", row["closed_unix"] != now, True)
check("trigger named", row["close_trigger"], "quiet_recovered")
ev = conn.execute("SELECT ts_unix, reason_code, actor FROM clock_event"
                  " WHERE kind='session_close'").fetchone()
check("event back-dated too", ev["ts_unix"], last)
check("event reason", ev["reason_code"], "quiet_recovered")
check("session released", d.session_id, None)
check("attestation spooled", len(attest.pending_spool()), 1)

# 3. the next activity opens a NEW session, which owns the day it opened on -- the whole
#    point of D1's inheritance. A night shift crossing midnight gets the new date here.
d._open_session(now)
check("new session", d.session_id != sid, True)
check("new day", d.attendance_day, date.fromtimestamp(now).isoformat())
check("two sessions now", conn.execute("SELECT COUNT(*) FROM work_session").fetchone()[0], 2)

# 4. R9, one level up: a session may not close while a run is open, however quiet it looks.
fresh()
sid, day = seed(now - (300 * 60), now - (200 * 60))
d = clockd.Clockd(conn)
d.session_id, d.attendance_day = sid, day
d.run_open_at = now - 100
check("refuses while a run is open", d.maybe_close_session(now), False)
check("still open", conn.execute(
    "SELECT closed_unix FROM work_session WHERE id=?", (sid,)).fetchone()[0], None)

# 5. ... and not while the machine says something is advancing.
d.run_open_at = None
d.clock.agent_status("w1:p1", "working", now)
d.clock.tick(now)
check("machine is RUNNING", d.clock.state, clock_state.RUNNING)
check("refuses while advancing", d.maybe_close_session(now), False)

# 6. stopped and quiet: now it closes, at the last activity.
d.clock.agent_status("w1:p1", "idle", now)
d.clock.tick(now)
check("machine is STOPPED", d.clock.state, clock_state.STOPPED)
check("closes", d.maybe_close_session(now), True)
check("back-dated", conn.execute(
    "SELECT closed_unix FROM work_session WHERE id=?", (sid,)).fetchone()[0], now - (200 * 60))

# 7. quiet but not quiet ENOUGH: an hour of silence is a dinner on a 13:30-22:30 shift,
#    not the end of a day, and closing there would split one attendance day in two.
fresh()
sid, day = seed(now - (180 * 60), now - (60 * 60))
d = clockd.Clockd(conn)
d.session_id, d.attendance_day = sid, day
check("one hour of quiet is not the end of the day", d.maybe_close_session(now), False)

if fails:
    print("session FAILED:")
    for f in fails: print("  " + f)
    raise SystemExit(1)
print("session ok (7 scenarios, 21 assertions)")
PY
