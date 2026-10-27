#!/usr/bin/env bash
# D7, the on-return prompt (away.py). What must never happen: a gap answered without the
# user, a neighbouring gap answered by mistake, or a prompt past the D21 budget.
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$root/nix/pkgs/timetrack"
export TIMETRACK_STATE_DIR="$tmp/state" XDG_CONFIG_HOME="$tmp/config"
python3 - "$tmp" <<'PY'
import subprocess, sys
from datetime import datetime
from pathlib import Path

from timetrack import away, config, dbs, migrate
from timetrack.backfill import aw

fails = []
def check(name, got, want):
    if got != want: fails.append(f"{name}: got {got!r}, want {want!r}")

tmp = Path(sys.argv[1])
away.STATE = tmp / "state" / "away-state.json"
config.PANIC = tmp / "config" / "timetrack" / "PANIC"
away._toast = lambda body: None
aw.write = lambda conn, **kw: None      # the refresh is tests/refresh.sh's subject

T = int(datetime(2026, 10, 27, 17, 0).timestamp())
iso = lambda t: datetime.fromtimestamp(t).astimezone().isoformat()
ev = lambda t, status: {"timestamp": iso(t), "data": {"status": status}}

# 1. last_return
check("fresh return", away.last_return([ev(T, "not-afk"), ev(T - 1200, "afk")], T + 30), (T - 1200, T))
check("still away", away.last_return([ev(T, "afk"), ev(T - 600, "not-afk")], T + 30), None)
check("stale return", away.last_return([ev(T, "not-afk"), ev(T - 1200, "afk")], T + 600), None)
check("blip under MIN_GAP", away.last_return([ev(T, "not-afk"), ev(T - 60, "afk")], T + 30), None)

# 2. salah hint
pt = tmp / "prayer-times.conf"
pt.write_text("# generated\nAsr 17:15    # adhan 16:10\nMaghrib 17:52\n")
check("gap over Asr iqamah", away.salah_hint(T, T + 1800, pt), "Asr")
check("gap far from iqamah", away.salah_hint(T - 7200, T - 5400, pt), None)

# 3. budget
check("2 this hour is the cap", away.within_budget([T - 100, T - 200], T), False)
check("8 today is the cap", away.within_budget([T - 3600 * h for h in range(2, 10)], T), False)
check("1 this hour ok", away.within_budget([T - 100], T), True)

migrate.migrate()
conn = dbs.open_work()
sid = conn.execute("INSERT INTO work_session(opened_ts,opened_unix,attendance_day,open_trigger,"
                   "provenance) VALUES(?,?,?,?,?)", (iso(T - 9000), T - 9000, "2026-10-27",
                   "aw", "backfill:aw")).lastrowid
def gap(g0, g1):
    return conn.execute("INSERT INTO deduction(session_id,attendance_day,start_unix,end_unix,"
                        "category,minutes,confirmed,provenance) VALUES(?,?,?,?,?,?,?,?)",
                        (sid, "2026-10-27", g0, g1, "misc", (g1 - g0) / 60, 0,
                         "backfill:aw")).lastrowid
row = lambda i: dict(conn.execute("SELECT * FROM deduction WHERE id=?", (i,)).fetchone())
short, long_, other = gap(T - 7200, T - 6900), gap(T - 3600, T - 1800), gap(T - 1700, T - 1000)
conn.commit()

# 4. find_gap never answers a neighbour
check("matches its own gap", away.find_gap(conn, T - 3590, T - 1800)["id"], long_)
check("no match on a sliver", away.find_gap(conn, T - 1810, T - 1100) and
      away.find_gap(conn, T - 1810, T - 1100)["id"], other)

# 5. handle
check("short gap auto", away.handle(T - 7200, T - 6900, T), "auto")
r = row(short)
check("short: deducted, confirmed, marked auto", (r["category"], r["confirmed"], r["provenance"]),
      ("misc", 1, away.AUTO))

calls = []
def panel(answer, rc=0):
    def f(cmd):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, rc, stdout=answer, stderr="")
    return f

check("timeout leaves it alone", away.handle(T - 3600, T - 1800, T, launch=panel("", 2)), "unanswered")
check("still unconfirmed", row(long_)["confirmed"], 0)
check("meeting answered", away.handle(T - 3600, T - 1800, T + 100, launch=panel("4\n")),
      "answered:meeting")
r = row(long_)
check("meeting costs nothing", (r["minutes"], r["confirmed"], r["provenance"]),
      (0, 1, "manual:meeting"))

before = len(calls)
check("over budget defers", away.handle(T - 1700, T - 1000, T + 200, launch=panel("1\n")), "deferred")
check("no panel when over budget", len(calls), before)
check("deferred stays unconfirmed", row(other)["confirmed"], 0)

away.STATE.unlink()
config.PANIC.parent.mkdir(parents=True, exist_ok=True); config.PANIC.touch()
check("PANIC defers", away.handle(T - 1700, T - 1000, T + 9000, launch=panel("1\n")), "deferred")
check("no panel under PANIC", len(calls), before)

if fails:
    print("away FAILED:")
    for f in fails: print("  " + f)
    raise SystemExit(1)
print("away ok (5 scenarios, 22 assertions)")
PY
