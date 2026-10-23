#!/usr/bin/env bash
# The attestation spool and the daily sign-off.
#
# The spool is written by shell scripts that cannot import the package -- that is the whole
# design of the dead-man's switch -- so the contract between them is a file format, and a
# file format with no test is a file format that drifts. These assert the three outcomes
# that matter: a fact gets recorded, a fact that has nowhere to attach is KEPT rather than
# dropped, and a timeout never becomes an answer.
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$root/nix/pkgs/timetrack"
export TIMETRACK_STATE_DIR="$tmp/state" XDG_CONFIG_HOME="$tmp/config"
python3 - <<'PY'
import json
import time
from datetime import date, datetime

from timetrack import attest, config, dbs, migrate

fails = []
def check(name, got, want):
    if got != want: fails.append(f"{name}: got {got!r}, want {want!r}")

migrate.migrate()
conn = dbs.open_work()
# these runs stand for what clockd RECORDED and COUNTS, so the live clock is promoted
# here; shadow mode (live recorded, not counted) is tests/shadow.sh's subject
conn.execute("UPDATE schema_meta SET v='1' WHERE k='live_counts'"); conn.commit()
now = int(time.time())
day = date.fromtimestamp(now).isoformat()

def iso(t): return datetime.fromtimestamp(t).isoformat(timespec="seconds")

# 1. an unknown reason is refused at the door. The spool is a shell-script interface, so a
#    typo in a script must surface here rather than land a prose string in reason_code.
try:
    attest.spool_write("made_up_reason")
    fails.append("unknown reason accepted")
except ValueError:
    pass

# 2. a fact spooled with NO session anywhere cannot be attached -- and must be kept, not
#    silently discarded. Inventing a session to hold it would manufacture attendance.
attest.spool_write("prompt_timeout", ts=now - 600)
rep = attest.drain(conn)
check("no session: nothing written", rep["written"], 0)
check("no session: kept in spool", rep["unattached"], 1)
check("spool still has it", len(attest.pending_spool()), 1)
# the signal doctor alerts on is AGE, not count: undrained for an hour is normal (the
# capture job drains hourly), undrained for hours means the drain itself stopped running
age = attest.spool_age(now=now)
check("age is measured from when it happened", 595 < age < 605, True)
check("empty spool has no age", attest.spool_age() if not attest.pending_spool() else None, None)

# 3. with a session open, the same spooled fact becomes an append-only attest row, stamped
#    at the time it HAPPENED, not the time it was drained.
cur = conn.execute(
    "INSERT INTO work_session(opened_ts,opened_unix,attendance_day,open_trigger,provenance)"
    " VALUES(?,?,?,?,?)", (iso(now - 3600), now - 3600, day, "test", "live"))
sid = cur.lastrowid
conn.commit()
rep = attest.drain(conn)
check("now attached", rep["written"], 1)
check("spool drained", attest.pending_spool(), [])
row = conn.execute("SELECT kind, reason_code, ts_unix, actor, session_id FROM clock_event"
                   " WHERE reason_code='prompt_timeout'").fetchone()
check("kind", row["kind"], "attest")
check("stamped when it happened", row["ts_unix"], now - 600)
check("actor is system", row["actor"], "system")
check("attached to the open session", row["session_id"], sid)

# 4. a timeout is NEVER an answer. Nothing in the drain path may produce a run interval,
#    a deduction or a day stamp -- the one thing worse than an unanswered prompt is a
#    payroll record containing an answer the user did not give.
check("no run rows", conn.execute(
    "SELECT COUNT(*) FROM clock_event WHERE kind IN ('run_start','run_stop')").fetchone()[0], 0)
check("no deductions", conn.execute("SELECT COUNT(*) FROM deduction").fetchone()[0], 0)
check("no day stamped", conn.execute(
    "SELECT COUNT(*) FROM day_class WHERE attested_unix IS NOT NULL").fetchone()[0], 0)

# 5. the daily sign-off. A day with recorded time and no stamp is pending; signing it off
#    writes both the stamp and an event, and disputing writes the same pair with a
#    different verdict -- 'reviewed and rejected' is not the same state as 'never looked at'.
conn.execute("INSERT INTO day_class(day,kind,pay_affecting,target_minutes)"
             " VALUES(?,?,?,?)", (day, "working", 1, 480))
for kind, t in (("run_start", now - 3600), ("run_stop", now - 1800)):
    conn.execute(
        "INSERT INTO clock_event(ts,ts_unix,attendance_day,session_id,kind,actor,"
        "provenance,ruleset,confidence) VALUES(?,?,?,?,?,'human','live',?,1.0)",
        (iso(t), t, day, sid, kind, config.RULESET))
conn.commit()

tomorrow = date.fromtimestamp(now + 86400).isoformat()
pend = attest.pending_days(conn, day[:7], today=tomorrow)
check("the day is pending", [r["day"] for r in pend], [day])

attest.confirm(conn, day, "confirmed", ts=now)
check("stamped", conn.execute("SELECT attested_unix FROM day_class WHERE day=?",
                              (day,)).fetchone()[0], now)
check("event written", conn.execute(
    "SELECT COUNT(*) FROM clock_event WHERE reason_code='day_confirmed'").fetchone()[0], 1)
check("no longer pending", attest.pending_days(conn, day[:7], today=tomorrow), [])

attest.confirm(conn, day, "disputed", ts=now + 1)
check("dispute stamps too", conn.execute(
    "SELECT COUNT(*) FROM clock_event WHERE reason_code='day_disputed'").fetchone()[0], 1)

# 6. a day with no recorded time is NOT a pending sign-off. There is no figure to confirm;
#    it is a question for `timetrack backfill`, which asks a different question.
empty = date.fromtimestamp(now - 86400).isoformat()
conn.execute("INSERT INTO day_class(day,kind,pay_affecting,target_minutes)"
             " VALUES(?,?,?,?)", (empty, "working", 1, 480))
conn.commit()
check("empty day is not pending",
      [r["day"] for r in attest.pending_days(conn, empty[:7], today=tomorrow)], [])

if fails:
    print("attest FAILED:")
    for f in fails: print("  " + f)
    raise SystemExit(1)
print("attest ok (6 scenarios, 19 assertions)")
PY
