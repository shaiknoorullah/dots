"""D8: salah enforcement -- at iqamah, ask, then lock the screen so the prayer happens.

The nudge unit (adhd-salah-nudge.sh) runs `timetrack salah enforce PRAYER` once per prayer.
This module decides whether to bother the user at all, and if so walks a fixed ladder:

    ask (4 min, snooze once) -> armed warning (60 s) -> lock -> wait for unlock

Checks run in a fixed order and each leaves its verdict in salah_log.enforcement:
panic, already answered, away, spaced, paused, then enforce. Only the last locks.

Strikes. A strike is an ESCAPED lock: the user unlocked within two minutes of it. Nothing
else counts. In particular an absent user is never locked (the presence check precedes the
lock), so a nudge missed inside an away gap that is later classified as a meeting can never
become a strike. Three strikes in seven days pause locking for a week; nudges continue.

Nothing is answered for the user: a timeout is recorded as what it is (an armed warning, a
lock), never as a pick. Every clock, subprocess and presence read goes through a module
level seam (`_now`, `_sleep`, `_lock`, `_locked`, `_foreground`, `_presence`, `_day`) so
tests never touch the desktop. Everything here is local; salah_log lives in private.db.
"""
import os
import sqlite3
import time
from datetime import date, datetime

from . import away, config, dbs, notify
from .backfill import aw

STATUSES = ("jamaah", "alone", "qaza", "missed")
ASK_S = 240                 # first question, and the one after a snooze
ARMED_S = 60                # "Locking in 60 s"
LOCK_BEFORE = 15 * 60       # a salah declaration this recent already answers the nudge
SPACING = 40 * 60           # no second lock within this of the last (Asr / Maghrib)
LAST_CAP = 90 * 60          # veto cap after the last prayer of the day
VETO_POLL = 15
UNLOCK_POLL = 5
UNLOCK_MAX = 90 * 60
ESCAPE_S = 120              # unlocked this soon after the lock = escaped
APPEAR_S = 30               # hyprlock never showing up means the lock failed: no verdict
PAUSE_S = 7 * 86400
VETO_COMMS = {"terraform", "kubectl", "helm", "ssh", "psql", "argocd"}


# --- seams ------------------------------------------------------------------

def _now():
    return int(time.time())


def _sleep(seconds):
    time.sleep(seconds)


def _lock():
    import subprocess
    subprocess.run(["loginctl", "lock-session"], check=False, stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _locked():
    import subprocess
    r = subprocess.run(["pgrep", "-x", "hyprlock"], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return r.returncode == 0


def _foreground():
    """comm of a watched long-running command that owns its terminal, else None.

    On a tty the foreground process group is the one the shell handed the terminal to, so
    `pgrp == tpgid` is "this is what the user is waiting on". A background `ssh -N` or a
    paused job fails the test and never vetoes.
    """
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return None
    for pid in pids:
        try:
            with open(f"/proc/{pid}/stat") as fh:
                stat = fh.read()
        except OSError:
            continue
        # comm may contain spaces and parens: split on the LAST ')'
        head, _, rest = stat.rpartition(")")
        comm = head.partition("(")[2]
        if comm not in VETO_COMMS:
            continue
        f = rest.split()           # f[0]=state f[1]=ppid f[2]=pgrp f[3]=session f[4]=tty f[5]=tpgid
        try:
            pgrp, tty, tpgid = int(f[2]), int(f[4]), int(f[5])
        except (IndexError, ValueError):
            continue
        if tty != 0 and pgrp == tpgid:
            return comm
    return None


def _presence(now):
    """True present, False absent, None when AW cannot say."""
    try:
        events = aw._get(f"/buckets/{aw.bucket('afkstatus')}/events?limit=1")
    except SystemExit:
        return None
    return away.present(events, now)


def _day(now):
    try:
        with dbs.open_work(readonly=True) as conn:
            return aw.current_day(conn, now)
    except sqlite3.Error:                  # no work.db yet: the calendar day is all we have
        return date.fromtimestamp(now).isoformat()


# --- data -------------------------------------------------------------------

def _clock(t):
    return datetime.fromtimestamp(t).strftime("%H:%M")


def _times(now):
    """[(prayer, iqamah_unix)] for today from prayer-times.conf, in file order."""
    try:
        lines = away.PRAYER_TIMES.read_text().splitlines()
    except OSError:
        return []
    base = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    for line in lines:
        parts = line.split("#", 1)[0].split()
        if len(parts) < 2 or ":" not in parts[1]:
            continue
        try:
            hh, mm = (int(x) for x in parts[1].split(":"))
        except ValueError:
            continue
        out.append((parts[0].capitalize(), int(base.replace(hour=hh, minute=mm).timestamp())))
    return out


def _iqamah(prayer, now):
    for name, t in _times(now):
        if name == prayer:
            return t
    return None


def _cap(prayer, iqamah, now):
    """Latest moment the lock may still be held back: the next iqamah, or +90 min."""
    later = sorted(t for _, t in _times(now) if iqamah is not None and t > iqamah)
    return later[0] if later else (iqamah or now) + LAST_CAP


def _upsert(conn, day, prayer, iqamah, now):
    conn.execute("INSERT INTO salah_log(day, prayer, iqamah_unix, nudged_unix, enforcement)"
                 " VALUES(?,?,?,?, 'pending')"
                 " ON CONFLICT(day, prayer) DO UPDATE SET nudged_unix = excluded.nudged_unix,"
                 " iqamah_unix = COALESCE(excluded.iqamah_unix, iqamah_unix)",
                 (day, prayer, iqamah, now))
    conn.commit()


def _set(conn, day, prayer, **cols):
    sets = ", ".join(f"{k} = ?" for k in cols)
    conn.execute(f"UPDATE salah_log SET {sets} WHERE day = ? AND prayer = ?",
                 (*cols.values(), day, prayer))
    conn.commit()


def _row(conn, day, prayer):
    return conn.execute("SELECT * FROM salah_log WHERE day = ? AND prayer = ?",
                        (day, prayer)).fetchone()


def _declared(now):
    try:
        d = away._read(away.INTENT, {})
    except Exception:
        return False
    since = d.get("since") or 0
    return d.get("key") == "salah" and 0 <= now - since <= LOCK_BEFORE


def _budget(conn, day):
    """Qaza buttons left today."""
    allowed = int(config.habits("salah").get("qaza_per_day", 2))
    used = conn.execute("SELECT COUNT(*) FROM salah_log WHERE day = ? AND qaza_reason IS NOT NULL",
                        (day,)).fetchone()[0]
    return used < allowed


def _strikes(conn, now):
    return conn.execute("SELECT COALESCE(SUM(strike), 0) FROM salah_log WHERE locked_unix >= ?",
                        (now - PAUSE_S,)).fetchone()[0]


def _pause(conn, now):
    return conn.execute("SELECT until_unix FROM salah_pause WHERE until_unix > ?"
                        " ORDER BY until_unix DESC LIMIT 1", (now,)).fetchone()


# --- enforcement ------------------------------------------------------------

def _nudge(prayer, iq, body):
    notify.toast("Salah", f"{prayer} · iqamah {_clock(iq)}", body)


def _ask(prayer, iq, conn, day, *, snooze_ok, summary=None, body=None, timeout=ASK_S):
    actions = [("go", "Go now")]
    if snooze_ok:
        actions.append(("snooze", "Snooze 5 min"))
    if _budget(conn, day):
        actions += [("qaza_meeting", "Qaza · meeting"), ("qaza_incident", "Qaza · incident")]
    return notify.ask(app="Salah", summary=summary or f"{prayer} · iqamah {_clock(iq)}",
                      body=body or f"Locking at {_clock(iq)}.", actions=actions,
                      timeout=timeout, urgency="critical")


def _qaza(conn, day, prayer, answer):
    _set(conn, day, prayer, enforcement="qaza", qaza_reason=answer.split("_", 1)[1])


def enforce(prayer, now=None):
    """Run the ladder for one prayer. Returns the final enforcement code."""
    prayer = prayer.capitalize()
    now = int(now or _now())
    day = _day(now)
    iq = _iqamah(prayer, now)
    shown = iq or now
    with dbs.open_private() as conn:
        _upsert(conn, day, prayer, iq, now)

        def done(code, **cols):
            _set(conn, day, prayer, enforcement=code, **cols)
            return code

        if config.panic():
            notify.toast("Salah", f"{prayer} · iqamah {_clock(shown)}",
                         "Enforcement is off. Log it with Super+Shift+;")
            return done("panic")
        row = _row(conn, day, prayer)
        if row["status"]:
            return done("logged")
        if _declared(now):
            return done("declared")
        here = _presence(now)
        if here is None:
            _nudge(prayer, shown, "Could not check presence. Log it with Super+Shift+;")
            return done("vetoed", veto_reason="presence_unknown")
        if not here:
            notify.toast("Salah", f"{prayer} iqamah passed while you were away"
                         " — log it with Super+Shift+;")
            return done("away")
        if conn.execute("SELECT 1 FROM salah_log WHERE locked_unix >= ? AND prayer != ?",
                        (now - SPACING, prayer)).fetchone():
            _nudge(prayer, shown, "Another prayer was just locked. Log it with Super+Shift+;")
            return done("spaced")
        if _pause(conn, now):
            _nudge(prayer, shown, "Locking is paused. Log it with Super+Shift+;")
            return done("disabled")
        return _enforce(conn, day, prayer, shown, _cap(prayer, iq, now), done)


def _enforce(conn, day, prayer, iq, cap, done):
    answer = _ask(prayer, iq, conn, day, snooze_ok=True)
    if answer == "snooze":
        _set(conn, day, prayer, snoozed=1)
        _sleep(ASK_S)
        answer = _ask(prayer, iq, conn, day, snooze_ok=False)
    while True:
        if answer and answer.startswith("qaza_"):
            _qaza(conn, day, prayer, answer)
            return "qaza"
        if answer != "go":
            answer = _ask(prayer, iq, conn, day, snooze_ok=False, summary="Locking in 60 s",
                          body=f"{prayer} · iqamah {_clock(iq)}", timeout=ARMED_S)
            if answer and answer.startswith("qaza_"):
                _qaza(conn, day, prayer, answer)
                return "qaza"
        if answer == "go":
            break                  # the user chose this; a running command does not veto it
        comm = _foreground()
        if not comm:
            break
        _set(conn, day, prayer, enforcement="vetoed", veto_reason=comm)
        notify.toast("Salah", f"Holding the lock while {comm} runs")
        while _foreground():
            if _now() >= cap:
                _set(conn, day, prayer, enforcement="qaza", qaza_reason="long_command")
                notify.toast("Salah", f"{prayer} went to qaza: a long command held the lock")
                return "qaza"
            _sleep(VETO_POLL)
        answer = None              # cleared: the 60 s warning again, then lock
    if _row(conn, day, prayer)["status"]:
        return done("logged")      # logged from the picker while the question was up
    return _lock_and_wait(conn, day, prayer)


def _lock_and_wait(conn, day, prayer):
    t0 = _now()
    away.declare("salah", t0)
    _lock()
    _set(conn, day, prayer, enforcement="locked", locked_unix=t0)
    seen = False
    while True:
        now = _now()
        if _locked():
            seen = True
        elif seen:
            break
        elif now - t0 >= APPEAR_S:
            return "locked"        # never saw the lock screen: no unlock to judge
        if now - t0 >= UNLOCK_MAX:
            return "locked"
        _sleep(UNLOCK_POLL)
    now = _now()
    cols = {"unlocked_unix": now}
    if now - t0 < ESCAPE_S:
        cols["strike"] = 1
    _set(conn, day, prayer, **cols)
    if cols.get("strike"):
        limit = int(config.habits("salah").get("strikes", 3))
        if _strikes(conn, now) >= limit and not _pause(conn, now):
            until = now + PAUSE_S
            conn.execute("INSERT OR IGNORE INTO salah_pause(start_unix, until_unix, reason)"
                         " VALUES(?,?, 'three_strikes')", (now, until))
            conn.commit()
            notify.toast("Salah", f"Three escaped locks this week: salah locking paused until "
                         f"{datetime.fromtimestamp(until).date():%a %d %b}. Nudges continue.")
    return "locked"


# --- log / status -----------------------------------------------------------

def log(prayer, status, now=None):
    """Upsert the (day, prayer) row with how it was prayed. Called by the picker."""
    if status not in STATUSES:
        raise SystemExit(f"timetrack: salah status must be one of {', '.join(STATUSES)}")
    prayer = prayer.capitalize()
    now = int(now or _now())
    day = _day(now)
    with dbs.open_private() as conn:
        conn.execute("INSERT INTO salah_log(day, prayer, iqamah_unix, enforcement, status,"
                     " logged_unix) VALUES(?,?,?, 'logged', ?, ?)"
                     " ON CONFLICT(day, prayer) DO UPDATE SET status = excluded.status,"
                     " logged_unix = excluded.logged_unix",
                     (day, prayer, _iqamah(prayer, now), status, now))
        conn.commit()


def status(now=None):
    now = int(now or _now())
    day = _day(now)
    with dbs.open_private(readonly=True) as conn:
        rows = conn.execute("SELECT * FROM salah_log WHERE day = ? ORDER BY iqamah_unix, id",
                            (day,)).fetchall()
        strikes = _strikes(conn, now)
        pause = _pause(conn, now)
    print(day)
    for r in rows:
        iq = _clock(r["iqamah_unix"]) if r["iqamah_unix"] else "--:--"
        print(f"  {r['prayer']:<8} {iq}  {r['status'] or '-':<7} {r['enforcement']}"
              + (f" ({r['qaza_reason'] or r['veto_reason']})"
                 if r["qaza_reason"] or r["veto_reason"] else ""))
    if not rows:
        print("  no prayers recorded yet")
    print(f"strikes (7 days): {strikes}")
    if pause:
        print(f"locking paused until {datetime.fromtimestamp(pause[0]):%Y-%m-%d %H:%M}")


def add_parser(sub):
    s = sub.add_parser("salah", help="D8: salah enforcement and log (local, private.db)")
    ss = s.add_subparsers(dest="salah_cmd", required=True)
    e = ss.add_parser("enforce", help="run by the nudge at iqamah")
    e.add_argument("prayer")
    e.set_defaults(fn=lambda a: (enforce(a.prayer), 0)[1])
    lg = ss.add_parser("log", help="record how a prayer was prayed")
    lg.add_argument("prayer")
    lg.add_argument("status", choices=STATUSES)
    lg.set_defaults(fn=lambda a: (log(a.prayer, a.status), 0)[1])
    ss.add_parser("status", help="today's prayers, recent strikes, any pause"
                  ).set_defaults(fn=lambda a: (status(), 0)[1])
