"""Bound reconstructed sessions by the windows the machine was actually running.

A reconstruction can claim time that could not have happened. A git-derived span bridges a
45-minute gap between commits; if the machine was shut down inside that gap, the bridge is
fiction. The sheet is hand-written from memory and can be wrong the same way. journald
knows exactly when this host was up, so it is the one source that can falsify the others.

`audit` REPORTS impossible minutes and never writes. `clip` can remove them, but only from
sessions below confidence 1.0 -- the hand-kept sheet is what gets submitted, and silently
shaving minutes off a figure the user attested to would be the wrong kind of correction.
For those days the right output is a question, not an edit.

Boot windows come from `journalctl --list-boots -o json`, whose first_entry/last_entry are
MICROseconds. Suspend is not subtracted: this host records no suspend/resume events, and
guessing at sleep from log silence would invent gaps rather than find them.
"""
import json
import subprocess

from .. import config, intervals

PROVENANCE = "audit:journald"


def boots():
    """[(start_unix, end_unix)] for every boot journald still retains, oldest first."""
    try:
        out = subprocess.run(["journalctl", "--list-boots", "-o", "json"],
                             capture_output=True, text=True, timeout=20, check=True).stdout
    except (OSError, subprocess.SubprocessError) as e:
        raise SystemExit(f"timetrack: cannot read journald boots ({e})")
    spans = []
    for b in json.loads(out or "[]"):
        first, last = b.get("first_entry"), b.get("last_entry")
        if first and last and last > first:
            spans.append((first / 1_000_000.0, last / 1_000_000.0))
    return sorted(spans)


def uptime():
    """Merged uptime, as a span list usable with intervals.subtract."""
    return intervals.merge(boots())


def _covered(start, end, up):
    """Seconds of [start,end) that fall inside a window the machine was running."""
    total = 0.0
    for s, e in up:
        lo, hi = max(start, s), min(end, e)
        if lo < hi:
            total += hi - lo
    return total


def audit(conn, month):
    """Per-day impossible minutes. Writes nothing."""
    up = uptime()
    if not up:
        raise SystemExit("timetrack: journald retains no boot windows; cannot bound anything")
    earliest = min(s for s, _ in up)

    rows = []
    for r in conn.execute(
            "SELECT session_id, attendance_day, start_unix, end_unix, provenance, confidence"
            " FROM v_run_interval WHERE attendance_day LIKE ? AND end_unix IS NOT NULL"
            " ORDER BY attendance_day, start_unix", (month + "-%",)):
        span = r["end_unix"] - r["start_unix"]
        if span <= 0:
            continue
        # A run that predates the oldest retained boot cannot be judged: journald has
        # simply forgotten. Reporting it as impossible would be a lie about the evidence.
        if r["end_unix"] < earliest:
            verdict = "unknowable"
            off = 0.0
        else:
            off = span - _covered(r["start_unix"], r["end_unix"], up)
            verdict = "impossible" if off > 60 else "ok"
        rows.append({
            "day": r["attendance_day"],
            "span_minutes": round(span / 60),
            "offline_minutes": round(off / 60),
            "verdict": verdict,
            "provenance": r["provenance"] or "",
            "confidence": r["confidence"],
        })

    per_day = {}
    for r in rows:
        d = per_day.setdefault(r["day"], {
            "day": r["day"], "span_minutes": 0, "offline_minutes": 0,
            "provenance": r["provenance"], "confidence": r["confidence"],
            "unknowable": 0})
        d["span_minutes"] += r["span_minutes"]
        d["offline_minutes"] += r["offline_minutes"]
        d["unknowable"] += 1 if r["verdict"] == "unknowable" else 0
        d["confidence"] = min(d["confidence"], r["confidence"])
    return sorted(per_day.values(), key=lambda d: d["day"])


def clip(conn, month, max_confidence=0.99):
    """Remove impossible run intervals from LOW-confidence sessions only.

    Deliberately not applied to confidence 1.0. The sheet is the user's own attestation and
    is what gets submitted; if journald contradicts it, that is a conversation, not a
    silent subtraction.
    """
    up = uptime()
    clipped = []
    for r in list(conn.execute(
            "SELECT session_id, attendance_day, start_unix, end_unix, confidence"
            " FROM v_run_interval WHERE attendance_day LIKE ? AND end_unix IS NOT NULL",
            (month + "-%",))):
        if r["confidence"] is None or r["confidence"] > max_confidence:
            continue
        span = r["end_unix"] - r["start_unix"]
        if span <= 0 or span - _covered(r["start_unix"], r["end_unix"], up) <= 60:
            continue
        conn.execute(
            "DELETE FROM clock_event WHERE session_id=? AND kind IN ('run_start','run_stop')"
            " AND ts_unix IN (?,?)", (r["session_id"], r["start_unix"], r["end_unix"]))
        clipped.append({"day": r["attendance_day"],
                        "minutes": round(span / 60),
                        "confidence": r["confidence"]})
    conn.commit()
    return clipped
