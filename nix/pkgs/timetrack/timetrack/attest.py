"""Attestations: the seams in the record, and the day the user signs off on.

Two things live here because they are the same thing seen from two ends.

**The spool.** Enforcement runs outside this package on purpose. A blocking panel is
launched by a shell script and reaped by a 30-second timer whose whole value is that it
keeps working when the Python here does not -- layer 2 of the dead-man's switch must
survive a syntax error anywhere in timetrack, so it cannot import timetrack. But those
components still have facts to record: a panel that timed out, a panel the reaper killed.
So they drop a small JSON file in a spool directory, and the next `timetrack` run turns
each one into an `attest` row. The record of a timeout is itself append-only, and a
fabricated answer is never written in its place.

**The daily attestation.** `day_class.attested_unix` has been in the schema since v1 and
nothing has ever set it. It is the difference between "a daemon measured this" and "the
person whose pay it is looked at it and said yes" -- and the second is what a timesheet
handed to an employer actually claims. A disputed day is recorded too, with the same
weight: the point is not to collect approvals, it is to know which days were reviewed.
"""
import json
import os
import time
from datetime import date, datetime
from pathlib import Path

from . import config

SPOOL = config.STATE_DIR / "attest-spool"

# reason_code is documented as an enum, never prose. Validated here rather than trusted,
# because the spool is written by shell scripts that cannot import this module -- an
# unknown reason from a drifted script must be visible, not silently stored.
REASONS = frozenset({
    "day_confirmed",        # the user reviewed the day's figure and accepted it
    "day_disputed",         # reviewed and NOT accepted; the figure stands, flagged
    "prompt_timeout",       # a panel reached RuntimeMaxSec with no answer (layer 1)
    "deadman_reap",         # the reaper killed a panel whose heartbeat went stale (layer 2)
    "panel_dismissed",      # the user closed the panel without answering
    "panic_engaged",        # the PANIC file was found; enforcement stood down (layer 3)
    "session_close_quiet",  # a work session closed after the quiet threshold (D1)
    "hotkey_interval",      # a hand-opened interval was recorded
    "hotkey_covered",       # a hand-opened interval was ALREADY covered; nothing written
    "daemon_restart", "dangling_run_closed", "suspend_detected", "clean_shutdown",
})


def spool_write(reason, ts=None, **fields):
    """Drop a fact for the next `timetrack` run to record. Never raises on a full disk:
    losing an attestation must not take down the thing that was attesting."""
    if reason not in REASONS:
        raise ValueError(f"unknown attest reason: {reason}")
    ts = int(ts or time.time())
    SPOOL.mkdir(parents=True, exist_ok=True)
    body = dict(fields, reason=reason, ts_unix=ts)
    path = SPOOL / f"{ts}-{reason}-{os.getpid()}.json"
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(body))
        tmp.replace(path)
    except OSError:
        return None
    return path


def _session_for(conn, ts):
    """The session that was open at ts, else the newest session on that calendar day.

    Deliberately no third fallback. clock_event.session_id is NOT NULL with a foreign key,
    so an attestation about a day that has no session has nothing to attach to -- and
    inventing a session to hold it would manufacture attendance out of a timeout. Those
    entries stay in the spool and `timetrack doctor` counts them.
    """
    row = conn.execute(
        "SELECT id, attendance_day FROM work_session"
        " WHERE opened_unix <= ? AND (closed_unix IS NULL OR closed_unix >= ?)"
        " ORDER BY opened_unix DESC LIMIT 1", (ts, ts)).fetchone()
    if row:
        return row["id"], row["attendance_day"]
    day = date.fromtimestamp(ts).isoformat()
    row = conn.execute(
        "SELECT id, attendance_day FROM work_session WHERE attendance_day = ?"
        " ORDER BY opened_unix DESC LIMIT 1", (day,)).fetchone()
    return (row["id"], row["attendance_day"]) if row else (None, None)


def write_event(conn, reason, ts, actor="system", ticket=None, confidence=1.0,
                provenance="live"):
    """Append one `attest` row. Returns False when there is no session to attach it to."""
    session_id, day = _session_for(conn, ts)
    if session_id is None:
        return False
    conn.execute(
        "INSERT INTO clock_event(ts,ts_unix,attendance_day,session_id,kind,actor,"
        "ticket,reason_code,provenance,ruleset,confidence)"
        " VALUES(?,?,?,?,'attest',?,?,?,?,?,?)",
        (datetime.fromtimestamp(ts).isoformat(timespec="seconds"), int(ts), day,
         session_id, actor, ticket, reason, provenance, config.RULESET, confidence))
    return True


def drain(conn):
    """Turn spooled facts into rows. Idempotent by construction: a drained file is
    deleted, and a file that could not be attached is left exactly where it was."""
    if not SPOOL.exists():
        return {"written": 0, "unattached": 0, "bad": 0}
    written = unattached = bad = 0
    for path in sorted(SPOOL.glob("*.json")):
        try:
            d = json.loads(path.read_text())
            reason, ts = d["reason"], int(d["ts_unix"])
        except (OSError, ValueError, KeyError):
            bad += 1
            continue
        if reason not in REASONS:
            bad += 1
            continue
        if write_event(conn, reason, ts, provenance=d.get("provenance", "live"),
                       confidence=float(d.get("confidence", 1.0))):
            conn.commit()
            try:
                path.unlink()
            except OSError:
                pass
            written += 1
        else:
            unattached += 1
    return {"written": written, "unattached": unattached, "bad": bad}


def pending_spool():
    """Spool entries not yet turned into rows. Undrained is NORMAL -- the hourly capture
    job drains them -- so a caller judging health must look at AGE, not count."""
    if not SPOOL.exists():
        return []
    return sorted(p.name for p in SPOOL.glob("*.json"))


def spool_age(now=None):
    """Seconds since the oldest undrained entry was written, or None if the spool is empty.

    This is the signal worth alerting on. A count says only that something happened since
    the last drain; an age says a drain that should have happened did not -- which is how
    a reap or a timeout ends up recorded nowhere while every component looks fine.
    """
    now = now or time.time()
    names = pending_spool()
    if not names:
        return None
    stamps = []
    for name in names:
        head = name.split("-", 1)[0]
        try:
            stamps.append(int(head))
        except ValueError:
            stamps.append(int((SPOOL / name).stat().st_mtime))
    return now - min(stamps)


# --- the daily attestation -------------------------------------------------
def day_rows(conn, month, today=None):
    """Every working day of the month with its figure and whether it was signed off."""
    today = today or date.today().isoformat()
    return conn.execute(
        "SELECT w.attendance_day AS day, w.span_minutes, w.deduction_minutes,"
        " w.net_minutes, w.target_minutes, w.unconfirmed, w.provenance, c.attested_unix"
        " FROM v_day_worked w JOIN day_class c ON c.day = w.attendance_day"
        " WHERE w.attendance_day LIKE ? AND w.kind = 'working' AND w.attendance_day < ?"
        " ORDER BY w.attendance_day", (month + "-%", today)).fetchall()


def pending_days(conn, month, today=None):
    """Past working days that carry recorded time and have never been signed off.

    Days with NO recorded time are not listed here. An empty day is a question about the
    record (and `timetrack backfill` asks it); this is a question about a figure, and
    there is no figure to confirm.
    """
    return [r for r in day_rows(conn, month, today)
            if (r["span_minutes"] or 0) > 0 and r["attested_unix"] is None]


def confirm(conn, day, verdict="confirmed", ts=None):
    """Sign off (or dispute) one day. Writes BOTH the day_class stamp and an event.

    The stamp is what queries read; the event is what survives a schema change and what
    carries the ruleset the figure was computed under. A disputed day is stamped too --
    'reviewed and rejected' is a different state from 'never looked at', and collapsing
    them would mean the only way to express a dispute was to leave the day unreviewed.
    """
    if verdict not in ("confirmed", "disputed"):
        raise ValueError(verdict)
    ts = int(ts or time.time())
    row = conn.execute("SELECT day FROM day_class WHERE day = ?", (day,)).fetchone()
    if not row:
        raise ValueError(f"{day} is not classified; run `timetrack seed` first")
    conn.execute("UPDATE day_class SET attested_unix = ? WHERE day = ?", (ts, day))
    attached = write_event(conn, f"day_{verdict}", ts, actor="human")
    conn.commit()
    return attached
