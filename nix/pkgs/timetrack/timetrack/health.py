"""D18: dose and health checkpoints.

Collect data, build nothing clever. The day's medication dose anchors a fixed set of
check-ins (dose, +1 h, +3 h, +6 h, and a wrap on the way out). Each is one notification
with the keys 1-5, soft-dismissible: an unanswered one is recorded as `timeout`, never
as a guessed score. The only view is the weekly table; there is deliberately no
dose-effect plot.

All of it lives in private.db. The day is login-to-logout (`aw.current_day`), never
midnight. Every clock and subprocess goes through a module-level seam so tests reach
neither the desktop nor systemd.
"""
import shutil
import subprocess
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config, dbs, notify
from .backfill import aw

SLOTS = ("dose", "h1", "h3", "h6")
OFFSETS = {"dose": 0, "h1": 3600, "h3": 3 * 3600, "h6": 6 * 3600}
LATE = 30 * 60                 # a slot this far past due when the dose is logged is skipped
PROV = "manual"

now_unix = time.time           # seams: tests replace these
run = subprocess.run
spawn = subprocess.Popen
ask_fn = notify.ask
toast = notify.toast


def _timetrack():
    return shutil.which("timetrack") or str(Path.home() / ".nix-profile/bin/timetrack")


def _today():
    with dbs.open_work() as w:
        return aw.current_day(w, now_unix())


def _detached(argv):
    try:
        spawn(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
              stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


def _upsert(p, day, slot, due, **cols):
    p.execute("INSERT OR IGNORE INTO health_checkpoint(day,slot,due_unix,outcome)"
              " VALUES(?,?,?,'pending')", (day, slot, due))
    sets = ", ".join(f"{k}=?" for k in cols)
    p.execute(f"UPDATE health_checkpoint SET {sets} WHERE day=? AND slot=?",
              (*cols.values(), day, slot))


def dose(ago=0):
    """Record a dose and schedule its checkpoints. Returns False if one exists today."""
    now = int(now_unix())
    day = _today()
    ts = now - int(ago) * 60
    with dbs.open_private() as p:
        if p.execute("SELECT 1 FROM med_dose WHERE day=?", (day,)).fetchone():
            toast("Check-in", "Dose already logged today", "No second schedule created.")
            print(f"health: a dose is already logged for {day}")
            return False
        p.execute("INSERT INTO med_dose(ts_unix,day,provenance) VALUES(?,?,?)", (ts, day, PROV))
        plan = []
        for slot in SLOTS:
            due = ts + OFFSETS[slot]
            if due < now - LATE:
                outcome = "skipped"
            else:
                outcome = "pending"
                plan.append((slot, due))
            p.execute("INSERT OR REPLACE INTO health_checkpoint(day,slot,due_unix,outcome)"
                      " VALUES(?,?,?,?)", (day, slot, due, outcome))
        p.commit()
    tt = _timetrack()
    later = [(s, d) for s, d in plan if s != "dose"]
    for slot, due in later:
        run(["systemd-run", "--user", "--collect", f"--on-active={max(1, due - now)}",
             f"--unit=timetrack-health-{slot}-{day}", tt, "health", "ask", slot],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if any(s == "dose" for s, _ in plan):
        _detached([tt, "health", "ask", "dose"])
    print(f"health: dose logged for {day}; check-ins: " + ", ".join(s for s, _ in later))
    return True


def ask(slot):
    """Ask one checkpoint; record the score, or `timeout`. PANIC records `skipped`."""
    now = int(now_unix())
    day = _today()
    if config.panic():
        with dbs.open_private() as p:
            _upsert(p, day, slot, now, outcome="skipped")
            p.commit()
        return None
    with dbs.open_private() as p:
        _upsert(p, day, slot, now, asked_unix=now)
        p.commit()
    mins = config.habits("health").get("timeout_min", 10)
    key = ask_fn(app="Check-in", summary="How are you right now?", body="1 rough · 5 sharp",
                 actions=[(str(i), str(i)) for i in range(1, 6)], timeout=mins * 60)
    with dbs.open_private() as p:
        if key in ("1", "2", "3", "4", "5"):
            _upsert(p, day, slot, now, score=int(key), outcome="answered",
                    answered_unix=int(now_unix()))
        else:
            key = None
            _upsert(p, day, slot, now, outcome="timeout")
        p.commit()
    return int(key) if key else None


def login_ask():
    """The dose question at login. Nothing is recorded if it goes unanswered."""
    day = _today()
    with dbs.open_private() as p:
        if p.execute("SELECT 1 FROM med_dose WHERE day=?", (day,)).fetchone():
            return None
    key = ask_fn(app="Check-in", summary="Dose taken?", body="",
                 actions=[("now", "Taken now"), ("h1", "~1 h ago"), ("h2", "~2 h ago"),
                          ("skip", "Not today")], timeout=15 * 60)
    if key in ("now", "h1", "h2"):
        dose(ago={"now": 0, "h1": 60, "h2": 120}[key])
    elif key == "skip":
        with dbs.open_private() as p:
            _upsert(p, day, "dose", int(now_unix()), outcome="skipped")
            p.commit()
    return key


def _cell(row):
    if row is None:
        return "·"
    if row["outcome"] == "answered" and row["score"] is not None:
        return str(row["score"])
    return "x" if row["outcome"] == "timeout" else "·"


def week(end=None):
    """The weekly table: the 7 days ending at `end`, one row per day, then a mean row."""
    end = date.fromisoformat(end) if end else date.fromisoformat(_today())
    days = [(end - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    cols = SLOTS + ("wrap",)
    lines = [f"{'day':<11}{'dose':>6}" + "".join(f"{c:>6}" for c in cols)]
    sums = {c: [] for c in cols}
    with dbs.open_private() as p:
        for d in days:
            m = p.execute("SELECT MIN(ts_unix) t FROM med_dose WHERE day=?", (d,)).fetchone()["t"]
            at = datetime.fromtimestamp(m).strftime("%H:%M") if m else "-"
            rows = {r["slot"]: r for r in p.execute(
                "SELECT * FROM health_checkpoint WHERE day=?", (d,))}
            cells = []
            for c in cols:
                r = rows.get(c)
                cells.append(_cell(r))
                if r is not None and r["outcome"] == "answered" and r["score"] is not None:
                    sums[c].append(r["score"])
            lines.append(f"{d:<11}{at:>6}" + "".join(f"{x:>6}" for x in cells))
    mean = [f"{sum(v) / len(v):.1f}" if v else "-" for v in (sums[c] for c in cols)]
    lines.append(f"{'mean':<11}{'':>6}" + "".join(f"{x:>6}" for x in mean))
    return "\n".join(lines)


def on_login(now):
    """Return at once: this runs inside the away watcher loop. The question is a child."""
    _detached([_timetrack(), "health", "login-ask"])


def add_parser(sub):
    h = sub.add_parser("health", help="D18: dose, check-ins, weekly table (private.db)")
    hs = h.add_subparsers(dest="health_cmd", required=True)

    s = hs.add_parser("dose", help="record a dose and schedule the check-ins")
    s.add_argument("--ago", type=int, default=0, metavar="MIN", help="taken this many minutes ago")
    s.set_defaults(fn=lambda a: 0 if dose(a.ago) else 1)

    s = hs.add_parser("ask", help="ask one checkpoint (run by the timers)")
    s.add_argument("slot", choices=SLOTS + ("wrap",))
    s.set_defaults(fn=lambda a: (ask(a.slot), 0)[1])

    s = hs.add_parser("login-ask", help="ask whether the dose was taken (run at login)")
    s.set_defaults(fn=lambda a: (login_ask(), 0)[1])

    s = hs.add_parser("week", help="the weekly table")
    s.add_argument("--end", metavar="YYYY-MM-DD", help="last day, default today")
    s.set_defaults(fn=lambda a: (print(week(a.end)), 0)[1])
