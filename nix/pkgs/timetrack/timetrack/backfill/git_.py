"""Reconstruct work sessions from git author timestamps.

This is the only forensic source with real coverage: on the month being reconstructed,
every weekday had commits, and the timestamps are corroborated by the remotes, so a
figure derived from them can be checked by someone else.

Deliberately NOT scaled by the calibration factor. The stored intervals stay exactly what
the evidence says; the calibration is applied at report time and shown as its own column.
Baking it into the timestamps would turn evidence into fabrication.
"""
import glob
import os
import subprocess
from datetime import datetime, timezone

from .. import config

PROVENANCE = "backfill:git"
CONFIDENCE = 0.5  # a commit span is a lower bound: meetings, review and reading leave no commits


def repos():
    out = []
    for pattern in config.backfill_repo_globs():
        for path in glob.glob(os.path.expanduser(pattern)):
            if os.path.isdir(os.path.join(path, ".git")) or os.path.isfile(os.path.join(path, ".git")):
                out.append(path)
    src = os.path.expanduser("~/.local/share/chezmoi")
    if os.path.isdir(os.path.join(src, ".git")):
        out.append(src)
    return sorted(set(out))


def commit_times(since, until, authors=None):
    """Distinct author timestamps (epoch seconds) by the configured authors."""
    authors = set(config.backfill_authors() if authors is None else authors)
    if not authors:
        raise SystemExit(
            "timetrack: no backfill authors configured.\n"
            "  Add them to ~/.config/timetrack/payroll.toml:\n"
            "    [backfill]\n    authors = [\"you@example.com\"]"
        )
    stamps = set()
    for repo in repos():
        try:
            res = subprocess.run(
                ["git", "-C", repo, "log", "--all", "--no-merges",
                 "--pretty=%at%x09%ae", f"--since={since.isoformat()}",
                 f"--until={until.isoformat()}"],
                capture_output=True, text=True, timeout=180,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        for line in res.stdout.splitlines():
            if "\t" not in line:
                continue
            ts, email = line.split("\t", 1)
            if email.strip().lower() not in authors:
                continue
            try:
                stamps.add(int(ts))
            except ValueError:
                continue
    return sorted(stamps)


def cluster(stamps, gap=None, lead_in=None):
    """Group timestamps into sessions.

    A gap longer than `gap` starts a new session; `lead_in` credits the work that led up
    to a session's first commit. Returns [(start_unix, end_unix, n_commits)].
    """
    gap = config.gap_bridge_seconds() if gap is None else gap
    lead_in = config.lead_in_seconds() if lead_in is None else lead_in
    if not stamps:
        return []
    out, first, prev, n = [], stamps[0], stamps[0], 1
    for ts in stamps[1:]:
        if ts - prev > gap:
            out.append((first - lead_in, prev, n))
            first, n = ts, 0
        prev, n = ts, n + 1
    out.append((first - lead_in, prev, n))
    return out


def workdays(clusters, gap=None):
    """Group activity clusters into workdays, splitting on a sleep-length gap."""
    gap = config.workday_gap_seconds() if gap is None else gap
    if not clusters:
        return []
    out, cur = [], [clusters[0]]
    for cl in clusters[1:]:
        if cl[0] - cur[-1][1] >= gap:
            out.append(cur)
            cur = [cl]
        else:
            cur.append(cl)
    out.append(cur)
    return out


def _iso(unix):
    return datetime.fromtimestamp(unix).astimezone().isoformat(timespec="seconds")


def write(conn, since, until):
    """Replace this source's reconstruction for [since, until) and report what it wrote."""
    blocks = workdays(cluster(commit_times(since, until)))
    lo, hi = since.isoformat(), until.isoformat()
    old = [r["id"] for r in conn.execute(
        "SELECT id FROM work_session WHERE provenance=? AND attendance_day>=? AND attendance_day<?",
        (PROVENANCE, lo, hi))]
    if old:
        marks = ",".join("?" * len(old))
        conn.execute(f"DELETE FROM clock_event WHERE session_id IN ({marks})", old)
        conn.execute(f"DELETE FROM work_session WHERE id IN ({marks})", old)
    written = runs = 0
    for blocks_in_day in blocks:
        start, end = blocks_in_day[0][0], blocks_in_day[-1][1]
        day = datetime.fromtimestamp(start).date()
        if not (since <= day < until):
            continue
        cur = conn.execute(
            "INSERT INTO work_session(opened_ts,opened_unix,closed_ts,closed_unix,"
            "attendance_day,open_trigger,close_trigger,provenance) VALUES(?,?,?,?,?,?,?,?)",
            (_iso(start), start, _iso(end), end, day.isoformat(), "backfill", "backfill", PROVENANCE),
        )
        sid = cur.lastrowid

        def ev(kind, unix, reason=None):
            conn.execute(
                "INSERT INTO clock_event(ts,ts_unix,attendance_day,session_id,kind,actor,"
                "reason_code,provenance,ruleset,confidence) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (_iso(unix), unix, day.isoformat(), sid, kind, "human", reason,
                 PROVENANCE, config.RULESET, CONFIDENCE),
            )

        ev("session_open", start)
        # One run interval per activity cluster, so the idle gaps inside a workday are
        # excluded from active time while the session still spans login to logout.
        for cs, ce, ncommits in blocks_in_day:
            ev("run_start", cs)
            ev("run_stop", ce, f"commits:{ncommits}")
            runs += 1
        ev("session_close", end)
        written += 1
    conn.commit()
    return {"workdays_written": written, "run_intervals": runs,
            "sessions_replaced": len(old), "repos": len(repos())}
