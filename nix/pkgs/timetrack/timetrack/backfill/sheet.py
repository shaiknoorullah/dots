"""Import the hand-kept timesheet.

This outranks every other backfill source. The sheet is first-hand and self-attested: it
records when work actually started and stopped, including the stretches after a Keka
punch-out, and it is the only source that has the breaks -- which the employer's rule
deducts and which no automatic source can reconstruct.

Confidence is 1.0 and provenance says 'sheet', so the export can state exactly which days
are self-reported rather than measured.
"""
import csv
from datetime import datetime

from .. import config

PROVENANCE = "backfill:sheet"
CONFIDENCE = 1.0

# Sheet column -> deduction category. Prayer columns collapse to 'salah'; the generic
# break columns are 'misc' because the sheet does not say what they were.
COLUMNS = {
    "zohr": "salah", "asr": "salah", "maghrib": "salah", "fajr": "salah", "isha": "salah",
    "lunch": "lunch", "break1": "misc", "break2": "misc", "misc": "misc",
    "washroom": "washroom",
}


def _minutes(value):
    value = (value or "").strip()
    if not value:
        return 0
    parts = value.split(":")
    return int(parts[0]) * 60 + int(parts[1])


def read(path):
    rows = []
    with open(path, newline="") as fh:
        for raw in csv.DictReader(fh):
            if not (raw.get("login") or "").strip():
                continue
            rows.append(raw)
    return rows


def _iso(dt):
    return dt.astimezone().isoformat(timespec="seconds")


def write(conn, path):
    """Load the sheet, replacing any other reconstruction for the days it covers."""
    rows = read(path)
    days = [r["date"] for r in rows]
    if days:
        marks = ",".join("?" * len(days))
        old = [x["id"] for x in conn.execute(
            f"SELECT id FROM work_session WHERE attendance_day IN ({marks})"
            " AND provenance LIKE 'backfill:%'", days)]
        if old:
            om = ",".join("?" * len(old))
            conn.execute(f"DELETE FROM clock_event WHERE session_id IN ({om})", old)
            conn.execute(f"DELETE FROM work_session WHERE id IN ({om})", old)
        conn.execute(f"DELETE FROM deduction WHERE attendance_day IN ({marks})"
                     " AND provenance=?", days + [PROVENANCE])

    sessions = breaks = 0
    for r in rows:
        day = r["date"]
        login = datetime.fromisoformat(r["login"])
        logout = datetime.fromisoformat(r["actual_logout"])
        s, e = int(login.timestamp()), int(logout.timestamp())
        cur = conn.execute(
            "INSERT INTO work_session(opened_ts,opened_unix,closed_ts,closed_unix,"
            "attendance_day,open_trigger,close_trigger,provenance) VALUES(?,?,?,?,?,?,?,?)",
            (_iso(login), s, _iso(logout), e, day, "sheet", "sheet", PROVENANCE))
        sid = cur.lastrowid
        for kind, unix in (("session_open", s), ("run_start", s), ("run_stop", e), ("session_close", e)):
            conn.execute(
                "INSERT INTO clock_event(ts,ts_unix,attendance_day,session_id,kind,actor,"
                "provenance,ruleset,confidence) VALUES(?,?,?,?,?,?,?,?,?)",
                (_iso(datetime.fromtimestamp(unix)), unix, day, sid, kind, "human",
                 PROVENANCE, config.RULESET, CONFIDENCE))
        # Breaks are stored as real deduction rows rather than one lump, so the dashboard
        # can show where the time went and the monthly figure stays auditable.
        cursor = s
        for col, category in COLUMNS.items():
            m = _minutes(r.get(col))
            if not m:
                continue
            conn.execute(
                "INSERT INTO deduction(session_id,attendance_day,start_unix,end_unix,"
                "category,minutes,confirmed,provenance) VALUES(?,?,?,?,?,?,1,?)",
                (sid, day, cursor, cursor + m * 60, category, m, PROVENANCE))
            cursor += m * 60
            breaks += 1
        sessions += 1
    conn.commit()
    return {"days": sessions, "break_rows": breaks}
