#!/usr/bin/env bash
# The hourly AW refresh must not undo what the user said about the day.
#
# Found live: the capture job re-derives TODAY every hour by dropping AW's session and
# writing it again. It deleted deductions by session_id, so a gap the user classified as
# dinner was back to "unconfirmed misc" an hour later -- and a gap declared a MEETING,
# which is worked time, was deducted again. On a day with an offline meeting that is the
# meeting silently subtracted from the figure handed to an employer.
set -uo pipefail
root="$(git rev-parse --show-toplevel)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$root/nix/pkgs/timetrack"
export TIMETRACK_STATE_DIR="$tmp/state" XDG_CONFIG_HOME="$tmp/config"
python3 - <<'PY'
from datetime import datetime, timedelta
from timetrack import dbs, interactive, migrate
from timetrack.backfill import aw

fails = []
def check(name, got, want):
    if got != want: fails.append(f"{name}: got {got!r}, want {want!r}")

migrate.migrate()
conn = dbs.open_work()
day = "2026-10-22"
T = lambda h, m: datetime(2026, 10, 22, h, m)
conn.execute("INSERT INTO day_class(day,kind,pay_affecting,target_minutes) VALUES(?,?,?,?)",
             (day, "working", 1, 480))
conn.commit()

def presence(until_h, until_m):
    # 13:00-15:00 | 30m dinner | 15:30-17:00 | 60m meeting | 18:00-<until>
    runs = [(T(13, 0), T(15, 0)), (T(15, 30), T(17, 0)), (T(18, 0), T(until_h, until_m))]
    secs = sum((b - a).total_seconds() for a, b in runs)
    return [{"day": day, "open": runs[0][0], "close": runs[-1][1],
             "present_seconds": secs, "runs": runs}]

def net():
    return conn.execute("SELECT net_minutes FROM v_day_worked WHERE attendance_day=?",
                        (day,)).fetchone()[0]

aw.not_afk = lambda *a, **k: []
aw.workdays = lambda _ev: presence(19, 0)
aw.write(conn, refresh_day=day)
check("first write: 6h span, 90m of gaps", net(), 360 - 90)

# the user answers both gaps: dinner (deducted) and a meeting (worked time)
rows = conn.execute("SELECT id, start_unix FROM deduction ORDER BY start_unix").fetchall()
conn.execute("UPDATE deduction SET category='dinner', confirmed=1, provenance='manual'"
             " WHERE id=?", (rows[0]["id"],))
conn.execute("UPDATE deduction SET minutes=0, confirmed=1, provenance=? WHERE id=?",
             (interactive.MEETING, rows[1]["id"]))
conn.commit()
check("after answering: the meeting returns 60 minutes", net(), 360 - 30)

# an hour later the day has grown, and the capture job refreshes it
aw.workdays = lambda _ev: presence(20, 0)
aw.write(conn, refresh_day=day)
check("REFRESH KEEPS THE MEETING AS WORKED TIME", net(), 420 - 30)
cats = conn.execute("SELECT category, minutes, confirmed, provenance FROM deduction"
                    " ORDER BY start_unix").fetchall()
check("two deductions, not four", len(cats), 2)
check("dinner survived", (cats[0]["category"], cats[0]["confirmed"]), ("dinner", 1))
check("meeting survived at zero cost", (cats[1]["minutes"], cats[1]["provenance"]),
      (0, interactive.MEETING))
check("nothing re-asked", conn.execute(
    "SELECT COUNT(*) FROM deduction WHERE confirmed=0").fetchone()[0], 0)

# the guard still holds: a refresh never touches a day it did not write itself
conn.execute("UPDATE work_session SET provenance='backfill:sheet'")
conn.commit()
aw.workdays = lambda _ev: presence(21, 0)
aw.write(conn, refresh_day=day)
check("sheet day untouched", net(), 420 - 30)

if fails:
    print("refresh FAILED:")
    for f in fails: print("  " + f)
    raise SystemExit(1)
print("refresh ok (4 scenarios, 8 assertions)")
PY
