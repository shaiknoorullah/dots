"""ActivityWatch ingest: the only MEASURED source available before clockd exists.

Two rules this module exists to enforce.

**Buckets are resolved by TYPE, never by name.** The predecessor hardcoded
`aw-watcher-afk_devsupreme`; the host is `desktop-snoorullah`, so every request 404'd and
was read as "no activity". A missing bucket raises here. It never returns an empty list.

**It never overwrites a higher-confidence source.** The hand-kept sheet is confidence 1.0
and is what gets submitted. AFK detection cannot see reading, meetings away from the desk,
or thinking, so an AW span is a LOWER BOUND and carries confidence 0.8. `write()` therefore
skips any attendance_day that already has a session, and `compare()` reports the overlap
without touching it -- that comparison is the evidence for whether AW can be trusted at
all, and it must not be contaminated by AW's own writes.
"""
import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from .. import config, intervals

BASE = "http://localhost:5600/api/0"
PROVENANCE = "backfill:aw"
MIN_PRESENT = 300  # seconds; below this a cluster is noise, not a workday
MIN_GAP = 180      # seconds; a shorter gap is a pause, not a break worth asking about
CONFIDENCE = 0.8


def _get(path, timeout=15):
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=timeout) as fh:
            return json.loads(fh.read())
    except (urllib.error.URLError, OSError) as e:
        raise SystemExit(f"timetrack: aw-server unreachable ({e}); is aw-server running?")


def bucket(kind):
    """kind: 'afkstatus' or 'currentwindow'. Raises rather than returning nothing."""
    buckets = _get("/buckets/")
    for name, meta in buckets.items():
        if meta.get("type") == kind:
            return name
    raise SystemExit(
        f"timetrack: no bucket of type {kind!r} among {len(buckets)} bucket(s). "
        "awatcher is not reporting -- this is the silent-zero failure, not an empty day.")


def not_afk(since=None, until=None, now=None):
    """Merged [start,end) spans where the user was present, as local datetimes.

    The NEWEST event is live and carries duration 0 until it closes -- ActivityWatch only
    writes a duration when a state ends. Dropping zero-duration events therefore threw away
    the run currently in progress, so today's session ended at the last CLOSED run and
    every minute since was invisible. Worse, a break could never become an interior gap
    while it was the most recent thing in the data: with nothing recorded after it, it read
    as the session closing rather than as an absence between two runs.

    So the newest event alone is extended to `now`. Any OTHER zero-duration event is
    genuinely empty and still dropped -- that is noise, not a live state.
    """
    events = _get(f"/buckets/{bucket('afkstatus')}/events?limit=100000")
    now = now or datetime.now().astimezone()
    # the API returns newest first; only that one may be in progress
    newest_ts = events[0]["timestamp"] if events else None
    spans = []
    for e in events:
        if (e.get("data") or {}).get("status") != "not-afk":
            continue
        dur = e.get("duration") or 0
        start = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).astimezone()
        if dur <= 0:
            if e["timestamp"] != newest_ts or start > now:
                continue
            end = now
        else:
            end = start + timedelta(seconds=dur)
        if since and end.date() < since:
            continue
        if until and start.date() > until:
            continue
        spans.append((start, end))
    return intervals.merge(spans)


def workdays(spans):
    """Cluster presence spans into workdays.

    Split on config.workday_gap_seconds() (8h), NOT on midnight. This user works evenings
    into the small hours, so a calendar day boundary would cut one shift in half and report
    an 01:20 start and a 22:45 finish on the same date as a 21-hour day.

    attendance_day is the date the cluster OPENS and is inherited by the whole cluster.
    """
    gap = timedelta(seconds=config.workday_gap_seconds())
    out = []
    for s, e in spans:
        if out and s - out[-1][-1][1] < gap:
            out[-1].append((s, e))
        else:
            out.append([(s, e)])
    days = [{"day": c[0][0].date().isoformat(),
             "open": c[0][0], "close": c[-1][1],
             "present_seconds": sum((b - a).total_seconds() for a, b in c),
             "runs": c} for c in out]
    # Drop degenerate clusters. A lone sub-minute AFK event produced a "workday" whose
    # open and close timestamps were identical -- a zero-length session that is noise, and
    # worse, one that makes a day look attended. MIN_PRESENT is the floor below which a
    # cluster is not evidence of anything.
    return [d for d in days if d["present_seconds"] >= MIN_PRESENT]


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def _drop_day(conn, day):
    """Remove an AW-written session for one day. Refuses to touch any other provenance."""
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM work_session WHERE attendance_day=? AND provenance=?",
        (day, PROVENANCE))]
    if not ids:
        return False
    marks = ",".join("?" * len(ids))
    for table in ("deduction", "clock_event"):
        conn.execute(f"DELETE FROM {table} WHERE session_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM work_session WHERE id IN ({marks})", ids)
    return True


def write(conn, since=None, until=None, refresh_day=None):
    """Write AW-derived sessions for days that have NO session yet.

    `refresh_day` (an ISO date) additionally REPLACES that day if -- and only if -- the
    existing session is AW's own. Without it a periodic job is a no-op after the first run
    of the day: the day already has a session, so every later run skips it and the figure
    freezes at whenever it first ran. The provenance guard is what keeps that from ever
    eating the hand-kept sheet, which is confidence 1.0 and is what gets submitted.
    """
    days = workdays(not_afk(since, until))
    if refresh_day:
        _drop_day(conn, refresh_day)
    existing = {r["attendance_day"] for r in conn.execute(
        "SELECT DISTINCT attendance_day FROM work_session")}

    written, skipped = [], []
    for d in days:
        if d["day"] in existing:
            skipped.append(d["day"])
            continue
        cur = conn.execute(
            "INSERT INTO work_session(opened_ts,opened_unix,closed_ts,closed_unix,"
            "attendance_day,open_trigger,close_trigger,provenance) VALUES(?,?,?,?,?,?,?,?)",
            (_iso(d["open"]), int(d["open"].timestamp()), _iso(d["close"]),
             int(d["close"].timestamp()), d["day"], "aw", "aw", PROVENANCE))
        sid = cur.lastrowid
        # ONE run pair spanning open..close, matching how the sheet writes a day. The
        # obvious alternative -- a run pair per presence span -- makes span mean ACTIVE
        # time here and ELAPSED time there, and since v_day_worked computes
        # net = span - deductions, writing the gaps as deductions on top of that would
        # subtract them twice. Elapsed span plus explicit gap deductions keeps one
        # meaning for span across every source.
        ev = [("session_open", d["open"]), ("run_start", d["open"]),
              ("run_stop", d["close"]), ("session_close", d["close"])]
        for kind, dt in ev:
            conn.execute(
                "INSERT INTO clock_event(ts,ts_unix,attendance_day,session_id,kind,actor,"
                "provenance,ruleset,confidence) VALUES(?,?,?,?,?,?,?,?,?)",
                (_iso(dt), int(dt.timestamp()), d["day"], sid, kind, "human",
                 PROVENANCE, config.RULESET, CONFIDENCE))
        # Each gap between presence runs becomes an UNCONFIRMED deduction. confirmed=0 is
        # the whole point: the gap is real, but what it WAS is unknown, and an offline
        # meeting is worked time while lunch is not. Reclassifying a gap as a meeting
        # later deletes the deduction and the minutes return to the figure.
        # EVERY gap is deducted, without exception. span is now elapsed, so a gap left
        # undeducted stays inside the figure -- net would then exceed the active time
        # actually measured, which is the one direction a payroll number must never err in.
        # MIN_GAP decides only whether a HUMAN is asked about it: a two-minute pause is not
        # a break worth a prompt, so it is deducted and marked confirmed.
        gaps = asked = 0
        prev = None
        for a, b in d["runs"]:
            if prev is not None and b > a:
                secs = (a - prev).total_seconds()
                if secs > 0:
                    confirmed = 0 if secs >= MIN_GAP else 1
                    conn.execute(
                        "INSERT INTO deduction(session_id,attendance_day,start_unix,end_unix,"
                        "category,minutes,confirmed,provenance) VALUES(?,?,?,?,?,?,?,?)",
                        (sid, d["day"], int(prev.timestamp()), int(a.timestamp()),
                         "misc", secs / 60.0, confirmed, PROVENANCE))
                    gaps += 1
                    asked += 1 - confirmed
            prev = b
        written.append({"day": d["day"], "gaps": gaps, "to_classify": asked})
    conn.commit()
    return {"written": written, "skipped_existing": skipped}


def compare(conn, month):
    """Per-day AW presence against whatever is already recorded. Writes nothing.

    This is the cross-validation the plan asks for: if AW and the sheet disagree wildly on
    the days they share, AW cannot be used to calibrate anything.
    """
    aw = {d["day"]: d for d in workdays(not_afk())}
    rows = []
    for r in conn.execute(
            "SELECT attendance_day, span_minutes, net_minutes, provenance FROM v_day_worked"
            " WHERE attendance_day LIKE ? AND target_minutes > 0 ORDER BY attendance_day",
            (month + "-%",)):
        if PROVENANCE in (r["provenance"] or ""):
            # Comparing AW against a row AW itself wrote always yields 1.00 and silently
            # drags the median toward it. The comparison is only evidence when the
            # recorded figure came from somewhere else.
            continue
        a = aw.get(r["attendance_day"])
        aw_min = round(a["present_seconds"] / 60) if a else None
        basis = r["net_minutes"] if r["net_minutes"] is not None else r["span_minutes"]
        rows.append({
            "day": r["attendance_day"],
            "recorded_minutes": basis,
            "recorded_source": r["provenance"] or "",
            "aw_minutes": aw_min,
            "delta_minutes": (aw_min - basis) if (aw_min is not None and basis) else None,
            "ratio": (basis / aw_min) if (aw_min and basis) else None,
        })
    return rows
