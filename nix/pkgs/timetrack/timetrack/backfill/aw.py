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


def not_afk(since=None, until=None):
    """Merged [start,end) spans where the user was present, as local datetimes."""
    events = _get(f"/buckets/{bucket('afkstatus')}/events?limit=100000")
    spans = []
    for e in events:
        if (e.get("data") or {}).get("status") != "not-afk":
            continue
        dur = e.get("duration") or 0
        if dur <= 0:
            continue
        start = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).astimezone()
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


def write(conn, since=None, until=None):
    """Write AW-derived sessions for days that have NO session yet. Returns a report."""
    days = workdays(not_afk(since, until))
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
        ev = [("session_open", d["open"]), ("session_close", d["close"])]
        # one run pair per contiguous presence span, so gaps inside the day are real gaps
        for a, b in d["runs"]:
            ev += [("run_start", a), ("run_stop", b)]
        for kind, dt in ev:
            conn.execute(
                "INSERT INTO clock_event(ts,ts_unix,attendance_day,session_id,kind,actor,"
                "provenance,ruleset,confidence) VALUES(?,?,?,?,?,?,?,?,?)",
                (_iso(dt), int(dt.timestamp()), d["day"], sid, kind, "human",
                 PROVENANCE, config.RULESET, CONFIDENCE))
        written.append(d["day"])
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
