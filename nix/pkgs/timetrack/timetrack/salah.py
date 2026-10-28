"""D8: salah enforcement -- at iqamah, ask, then lock the screen so the prayer happens.

The nudge unit (adhd-salah-nudge.sh) runs `timetrack salah enforce PRAYER` once per prayer.
This module decides whether to bother the user at all, and if so walks a fixed ladder:

    ask (4 min, snooze once) -> armed warning (60 s) -> lock -> wait for unlock

Checks run in a fixed order and each leaves its verdict in salah_log.enforcement:
panic, already answered, not at desk, spaced, paused, then enforce. Only the last locks.

Only prayers inside the user's own time are enforced (the user's call, 2026-10-29). A
prayer whose iqamah falls outside a session (logged out, or 150 min without presence)
is marked `away` -- "not at desk" -- silently, and is never auto-marked qaza: what
happens away from the desk is not this machine's business. Inside a session, a prayer
still unmarked when its time ends (prayer-times.conf `ends HH:MM`) becomes qaza on its
own (`status_by = 'auto'`); `sweep` does that, hourly and before every enforcement.

Coming back while a prayer's time is still open and it is unmarked (a return from an
away gap, or a login), the user is asked whether they prayed it (the user's call,
2026-10-29). Being back inside its time makes it theirs: an `away` row turns `pending`,
so it too becomes qaza if still unmarked at its end. Asked at most every 20 minutes.

A prayer's `day` is the calendar date of its iqamah, not the session's attendance day:
each prayer happens once per date, and keying by session would let a night session that
spans two Fajrs write both to one row.

Strikes. A strike is an ESCAPED lock: the user, present when it locked, unlocked within two
minutes of it. Nothing else counts. A user away from the desk when the lock lands cannot
escape it, so a nudge missed inside an away gap that is later classified as a meeting can
never become a strike. Three strikes in seven days pause locking for a week; nudges continue.

Nothing is answered for the user: a timeout is recorded as what it is (an armed warning, a
lock), never as a pick. Every clock, subprocess and presence read goes through a module
level seam (`_now`, `_sleep`, `_lock`, `_locked`, `_foreground`, `_presence`, `_session`) so
tests never touch the desktop. Everything here is local; salah_log lives in private.db.
"""
import os
import re
import shutil
import subprocess
import time
from datetime import date, datetime, timedelta

from . import away, config, dbs, notify
from .backfill import aw

STATUSES = ("jamaah", "alone", "qaza", "missed")
ASK_S = 240                 # first question, and the one after a snooze
ARMED_S = 60                # "Locking in 60 s"
LOCK_BEFORE = 15 * 60       # a salah declaration this recent already answers the nudge
SPACING = 40 * 60           # no second lock within this of the last (Asr / Maghrib)
LAST_CAP = 90 * 60          # veto cap, and a prayer's end, when the conf does not say
VETO_POLL = 15
UNLOCK_POLL = 5
UNLOCK_MAX = 90 * 60
ESCAPE_S = 120              # unlocked this soon after the lock = escaped
APPEAR_S = 30               # hyprlock never showing up means the lock failed: no verdict
PAUSE_S = 7 * 86400
ASK_AGAIN = 20 * 60         # on return: not asked about the same prayer more often
RETURN_ASK_S = 10 * 60
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


def _session(now):
    """True inside a session, False outside, None when the away watcher has not looked yet.

    The watcher stamps `last_active` every minute the user is present. A session is over
    after QUIET_LOGIN without presence, or at a logout (Super+Esc, "Log out for today").
    """
    st = away._read(away.STATE, None)
    if not isinstance(st, dict) or st.get("last_active") is None:
        return None
    last = int(st["last_active"])
    if now - last >= away.QUIET_LOGIN:
        return False
    return not away._logged_out_since(last - 120)


# --- data -------------------------------------------------------------------

def _clock(t):
    return datetime.fromtimestamp(t).strftime("%H:%M")


ENDS = re.compile(r"\bends (\d{1,2}):(\d{2})")


def _schedule(day):
    """[(prayer, iqamah_unix, ends_unix)] on a date, from prayer-times.conf, in file order.

    The conf is regenerated daily; a neighbouring date reuses its clock times, which move
    by a minute or two at most. An end earlier than the iqamah is the next morning (Isha).
    """
    try:
        lines = away.PRAYER_TIMES.read_text().splitlines()
    except OSError:
        return []
    base = datetime.combine(day, datetime.min.time())
    out = []
    for line in lines:
        body, _, comment = line.partition("#")
        parts = body.split()
        if len(parts) < 2 or ":" not in parts[1]:
            continue
        try:
            hh, mm = (int(x) for x in parts[1].split(":"))
        except ValueError:
            continue
        iq = int(base.replace(hour=hh, minute=mm).timestamp())
        m = ENDS.search(comment)
        if m:
            end = int(base.replace(hour=int(m[1]), minute=int(m[2])).timestamp())
            if end <= iq:
                end += 86400
        else:
            end = None
        out.append((parts[0].capitalize(), iq, end))
    # no `ends` in the conf: a prayer lasts until the next iqamah, the last one LAST_CAP
    return [(n, iq, end or (out[i + 1][1] if i + 1 < len(out) else iq + LAST_CAP))
            for i, (n, iq, end) in enumerate(out)]


def _times(now):
    """[(prayer, iqamah_unix)] for the date of `now`."""
    return [(n, iq) for n, iq, _ in _schedule(date.fromtimestamp(now))]


def _iqamah(prayer, now):
    for name, t in _times(now):
        if name == prayer:
            return t
    return None


def _day(now, prayer=None):
    """The date a prayer belongs to. Logged after midnight, Isha is yesterday's."""
    today = date.fromtimestamp(now)
    if prayer:
        iq = _iqamah(prayer, now)
        if iq is not None and iq > now + 30 * 60:
            return (today - timedelta(days=1)).isoformat()
    return today.isoformat()


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
    iq = _iqamah(prayer, now)
    shown = iq or now
    day = date.fromtimestamp(shown).isoformat()
    sweep(now)
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
        inside = _session(now)
        if inside is False:
            return done("away")    # not at desk: no nudge, no lock, never auto-qaza
        if inside is None:
            _nudge(prayer, shown, "Could not tell whether you are at the desk. "
                   "Log it with Super+Shift+;")
            return done("vetoed", veto_reason="session_unknown")
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
    here = _presence(t0)           # away from the desk, the lock cannot be escaped
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
    if now - t0 < ESCAPE_S and here:
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


# --- sweep ------------------------------------------------------------------

def sweep(now=None):
    """Settle every prayer whose time is over, yesterday's and today's.

    No row at all: enforcement never ran (machine off or asleep), so not at desk. A row
    from inside a session with no status: qaza, marked automatically. Returns the count of
    rows written, for the log.
    """
    now = int(now or _now())
    today = date.fromtimestamp(now)
    n = 0
    with dbs.open_private() as conn:
        for d in (today - timedelta(days=1), today):
            day = d.isoformat()
            for prayer, iq, end in _schedule(d):
                if end > now:
                    continue
                r = _row(conn, day, prayer)
                if r is None:
                    conn.execute("INSERT INTO salah_log(day, prayer, iqamah_unix, enforcement)"
                                 " VALUES(?,?,?, 'away')", (day, prayer, iq))
                    n += 1
                elif r["status"] is None and r["enforcement"] != "away":
                    _set(conn, day, prayer, status="qaza", status_by="auto", logged_unix=now)
                    n += 1
        conn.commit()
    return n


# --- on return --------------------------------------------------------------

def due_on_return(now):
    """[(day, prayer, iqamah, end)] still inside their time, unmarked, not just asked."""
    today = date.fromtimestamp(now)
    out = []
    with dbs.open_private() as conn:
        for d in (today - timedelta(days=1), today):
            for prayer, iq, end in _schedule(d):
                if not iq <= now < end:
                    continue
                r = _row(conn, d.isoformat(), prayer)
                if r is not None:
                    if r["status"]:
                        continue
                    if r["asked_unix"] and now - r["asked_unix"] < ASK_AGAIN:
                        continue
                    # the iqamah question itself is still up: don't ask twice at once
                    if (r["enforcement"] == "pending" and r["nudged_unix"]
                            and now - r["nudged_unix"] < ASK_S + ARMED_S + 60):
                        continue
                out.append((d.isoformat(), prayer, iq, end))
    return out


def _spawn(argv):
    subprocess.Popen(argv, start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def on_return(now):
    """Called by the away watcher on a return or a login. Spawns the question and returns."""
    if config.panic() or not due_on_return(now):
        return
    tt = shutil.which("timetrack") or str(config.HOME / ".nix-profile/bin/timetrack")
    _spawn([tt, "salah", "ask-return"])


def ask_return(now=None):
    """Ask about each open, unmarked prayer. Returns {prayer: answer or None}."""
    now = int(now or _now())
    got = {}
    for day, prayer, iq, end in due_on_return(now):
        with dbs.open_private() as conn:
            conn.execute("INSERT OR IGNORE INTO salah_log(day, prayer, iqamah_unix, enforcement)"
                         " VALUES(?,?,?, 'pending')", (day, prayer, iq))
            # back inside its time: the prayer is the user's now, so qaza if left unmarked
            conn.execute("UPDATE salah_log SET asked_unix = ?, enforcement = CASE enforcement"
                         " WHEN 'away' THEN 'pending' ELSE enforcement END"
                         " WHERE day = ? AND prayer = ?", (now, day, prayer))
            conn.commit()
        key = notify.ask(app="Salah", summary=f"Have you prayed {prayer}?",
                         body=f"Its time ends at {_clock(end)}.",
                         actions=[("jamaah", "Yes · jamaah"), ("alone", "Yes · alone"),
                                  ("later", "Not yet")],
                         timeout=RETURN_ASK_S, urgency="critical")
        if key in ("jamaah", "alone"):
            _log_day(day, prayer, iq, key, int(_now()))
        got[prayer] = key
    return got


# --- log / status -----------------------------------------------------------

def log(prayer, status, now=None):
    """Upsert the (day, prayer) row with how it was prayed. Called by the picker."""
    if status not in STATUSES:
        raise SystemExit(f"timetrack: salah status must be one of {', '.join(STATUSES)}")
    prayer = prayer.capitalize()
    now = int(now or _now())
    day = _day(now, prayer)
    iq = _iqamah(prayer, int(datetime.fromisoformat(day).timestamp()) + 43200)
    _log_day(day, prayer, iq, status, now)


def _log_day(day, prayer, iq, status, now):
    with dbs.open_private() as conn:
        conn.execute("INSERT INTO salah_log(day, prayer, iqamah_unix, enforcement, status,"
                     " status_by, logged_unix) VALUES(?,?,?, 'logged', ?, 'user', ?)"
                     " ON CONFLICT(day, prayer) DO UPDATE SET status = excluded.status,"
                     " status_by = 'user', logged_unix = excluded.logged_unix",
                     (day, prayer, iq, status, now))
        conn.commit()


def status(now=None):
    now = int(now or _now())
    day = _day(now)
    sweep(now)
    with dbs.open_private(readonly=True) as conn:
        rows = conn.execute("SELECT * FROM salah_log WHERE day = ? ORDER BY iqamah_unix, id",
                            (day,)).fetchall()
        strikes = _strikes(conn, now)
        pause = _pause(conn, now)
    print(day)
    for r in rows:
        iq = _clock(r["iqamah_unix"]) if r["iqamah_unix"] else "--:--"
        st = (r["status"] or "-") + (" (auto)" if r["status_by"] == "auto" else "")
        enf = "not at desk" if r["enforcement"] == "away" else r["enforcement"]
        print(f"  {r['prayer']:<8} {iq}  {st:<12} {enf}"
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
    ss.add_parser("ask-return", help="ask about open, unmarked prayers (spawned on return)"
                  ).set_defaults(fn=lambda a: (ask_return(), 0)[1])
    ss.add_parser("sweep", help="settle prayers whose time is over (run hourly)"
                  ).set_defaults(fn=lambda a: (print(f"{sweep()} prayer(s) settled"), 0)[1])
