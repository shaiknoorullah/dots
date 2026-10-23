"""D4: the interval the user opens by hand, for work no sensor can see.

Reading a spec on paper, a whiteboard argument, an offline meeting, a phone call about an
incident -- none of it touches a keyboard, so AW reports afk and no agent is advancing.
This is the half of D4 that does not guess: the user states that they are working, and
that statement outranks every measured source (`presence.hotkey_open`).

Two things it must not do.

**It must not double-count.** v_day_span SUMs run intervals; it does not merge them. So an
interval written here that overlaps one clockd already wrote would be added twice and
inflate the day. Every write is therefore guarded by an overlap query against clock_event
itself -- not against the daemon's cache, which is a hint, and not against the daemon's
liveness, which says nothing about what it recorded.

**It must not vanish when the daemon is down.** The file this writes is read by presence,
so while clockd runs the interval becomes a normal run interval through the usual path and
nothing extra is needed. With clockd absent nothing would ever read it, so on close the
interval is written directly -- at confidence 1.0, because a human statement is the
strongest evidence this system has, and provenance 'hotkey' so it is always separable
from what was measured.
"""
import json
import time
from datetime import date, datetime

from . import attest, config, presence

FILE = presence.HOTKEY_FILE
# Under a minute is a mis-press, not an interval. Recording it would add a row to the
# ledger that says nothing and cost a reviewer a question.
MIN_SECONDS = 60


def _read():
    try:
        return json.loads(FILE.read_text())
    except (OSError, ValueError):
        return {}


def _write(d):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2))
    tmp.replace(FILE)


def state(now=None):
    now = now or time.time()
    d = _read()
    if d.get("opened_unix") and not d.get("closed_unix"):
        return {"open": True, "opened_unix": d["opened_unix"], "note": d.get("note", ""),
                "minutes": int((now - d["opened_unix"]) / 60)}
    return {"open": False, "opened_unix": None, "note": "", "minutes": 0}


def start(note="", now=None):
    now = int(now or time.time())
    cur = state(now)
    if cur["open"]:
        return {"action": "already_open", **cur}
    _write({"opened_unix": now, "note": str(note)[:120], "closed_unix": None})
    return {"action": "started", "opened_unix": now, "note": note}


def covered(conn, t0, t1):
    """Did anything already record time inside [t0, t1)?

    Checks closed intervals AND an unpaired run_start: a run the daemon still has open
    will be closed later at a time that covers this window, so writing now and letting it
    close afterwards is exactly the double count this guards against.
    """
    row = conn.execute(
        "SELECT COUNT(*) FROM v_run_interval WHERE start_unix < ? AND end_unix > ?",
        (t1, t0)).fetchone()[0]
    if row:
        return "an existing run interval overlaps it"
    # A live run counts as cover only once the live clock counts (migrate.WORK_V3). In
    # shadow its minutes are not in the figure, so refusing the hand interval because of
    # one would drop real time on the floor.
    open_run = conn.execute(
        "SELECT MIN(e.ts_unix) FROM clock_event e WHERE e.kind='run_start'"
        " AND (e.provenance != 'live' OR (SELECT v FROM schema_meta"
        "      WHERE k = 'live_counts') = '1')"
        " AND e.ts_unix < ? AND NOT EXISTS ("
        "   SELECT 1 FROM clock_event s WHERE s.session_id = e.session_id"
        "   AND s.kind='run_stop' AND s.ts_unix >= e.ts_unix)", (t1,)).fetchone()[0]
    if open_run is not None:
        return "a run is open across it (clockd owns this interval)"
    return None


def _session(conn, t0, day):
    """The session to hang the interval on, opening one if the day has none.

    Opening one here is legitimate in a way it is not in attest.py: the user has just
    stated they were working. That IS an attendance claim, and refusing to record it
    because no daemon happened to be running would lose exactly the work this feature
    exists to capture.
    """
    # Never an AW session. The hourly capture job drops and rewrites AW's session for
    # today, deleting its events by session_id -- a hand interval hung on it would vanish
    # within the hour. Hand intervals live in their own session, which nothing rewrites.
    row = conn.execute(
        "SELECT id FROM work_session WHERE attendance_day = ? AND provenance = 'hotkey'"
        " AND (closed_unix IS NULL OR closed_unix >= ?) ORDER BY opened_unix DESC LIMIT 1",
        (day, t0)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO work_session(opened_ts,opened_unix,attendance_day,open_trigger,"
        "provenance) VALUES(?,?,?,?,?)",
        (datetime.fromtimestamp(t0).isoformat(timespec="seconds"), int(t0), day,
         "hotkey", "hotkey"))
    sid = cur.lastrowid
    conn.execute(
        "INSERT INTO clock_event(ts,ts_unix,attendance_day,session_id,kind,actor,"
        "provenance,ruleset,confidence) VALUES(?,?,?,?,'session_open','human',?,?,?)",
        (datetime.fromtimestamp(t0).isoformat(timespec="seconds"), int(t0), day,
         sid, "hotkey", config.RULESET, 1.0))
    return sid


def stop(conn, note="", now=None):
    """Close the interval and record it, unless something already has."""
    now = int(now or time.time())
    cur = state(now)
    if not cur["open"]:
        return {"action": "not_open"}
    t0, t1 = int(cur["opened_unix"]), now
    d = _read()
    d["closed_unix"] = t1
    if note:
        d["note"] = str(note)[:120]
    _write(d)

    seconds = t1 - t0
    if seconds < MIN_SECONDS:
        return {"action": "too_short", "seconds": seconds}

    why = covered(conn, t0, t1)
    if why:
        # Recorded as an attestation rather than silently dropped: the user DID open an
        # interval, and "it was already counted" is the honest reason it adds no minutes.
        attest.spool_write("hotkey_covered", ts=t1, seconds=seconds)
        return {"action": "covered", "seconds": seconds, "why": why}

    day = date.fromtimestamp(t0).isoformat()
    sid = _session(conn, t0, day)
    for kind, ts in (("run_start", t0), ("run_stop", t1)):
        conn.execute(
            "INSERT INTO clock_event(ts,ts_unix,attendance_day,session_id,kind,actor,"
            "reason_code,provenance,ruleset,confidence)"
            " VALUES(?,?,?,?,?,'human','hotkey',?,?,?)",
            (datetime.fromtimestamp(ts).isoformat(timespec="seconds"), int(ts), day,
             sid, kind, "hotkey", config.RULESET, 1.0))
    conn.commit()
    attest.spool_write("hotkey_interval", ts=t1, seconds=seconds, day=day)
    return {"action": "recorded", "seconds": seconds, "day": day, "session_id": sid}


def toggle(conn, note="", now=None):
    return stop(conn, note=note, now=now) if state(now)["open"] else start(note, now=now)
