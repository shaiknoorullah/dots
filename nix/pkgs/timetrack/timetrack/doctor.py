"""Invariant checks over the whole timetrack installation.

This module exists because the predecessor's defining failure was SILENT SUCCESS: it
asked ActivityWatch for a bucket name that did not exist, took the 404 as an empty list,
and reported zero hours for months without ever erroring. Nothing about its output
distinguished "you did not work" from "I am broken".

So every check here is written to fail LOUDLY and to say what to do about it. A check that
cannot decide returns `warn`, never `ok` -- an unknown must never read as healthy.

Severity decides the exit code: any `fail` exits 1, warnings alone exit 0.
"""
import os
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import date

from . import config, dbs, derive

OK, WARN, FAIL = "ok", "warn", "fail"


def _r(name, status, detail, fix=""):
    return {"check": name, "status": status, "detail": detail, "fix": fix}


def _schema(results):
    for which, path, want in (("work", config.WORK_DB, 1), ("private", config.PRIVATE_DB, 1)):
        if not path.exists():
            results.append(_r(f"db:{which}", FAIL, "database file does not exist",
                              "timetrack migrate"))
            continue
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            uv = conn.execute("PRAGMA user_version").fetchone()[0]
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            conn.close()
        except sqlite3.Error as e:
            results.append(_r(f"db:{which}", FAIL, f"unreadable: {e}"))
            continue
        if integrity != "ok":
            results.append(_r(f"db:{which}", FAIL, f"integrity_check: {integrity}",
                              "restore from the vault snapshot"))
        elif uv != want:
            results.append(_r(f"db:{which}", FAIL, f"schema v{uv}, expected v{want}",
                              "timetrack migrate"))
        else:
            results.append(_r(f"db:{which}", OK, f"schema v{uv}, integrity ok"))


def _config(results):
    for label, path in (("payroll", config.PAYROLL_TOML), ("holidays", config.HOLIDAYS_TOML)):
        if not path.exists():
            results.append(_r(f"config:{label}", FAIL, f"{path} missing",
                              "hand-write it; it is deliberately unmanaged"))
            continue
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            # salary is in here and the repo is public; group/other readable is a leak
            results.append(_r(f"config:{label}", FAIL, f"mode {mode:04o} is group/other readable",
                              f"chmod 600 {path}"))
        else:
            results.append(_r(f"config:{label}", OK, f"present, mode {mode:04o}"))

    try:
        gross, hpd = config.gross_monthly(), config.hours_per_day()
    except SystemExit as e:
        results.append(_r("config:payroll-values", FAIL, str(e)))
        return
    if not gross or gross <= 0:
        results.append(_r("config:payroll-values", FAIL, "gross_monthly is unset or <= 0",
                          "every money figure is None without it"))
    elif not hpd or hpd <= 0:
        results.append(_r("config:payroll-values", FAIL, f"hours_per_day={hpd}"))
    else:
        results.append(_r("config:payroll-values", OK, f"{config.currency()} {gross:,.0f}/mo, {hpd}h/day"))


def _calibration(results, conn):
    """The bug this check exists for.

    calibration() corrects GIT-derived spans against measured ones. It was being applied
    to every span, including the hand-kept sheet -- inflating the user's own authoritative
    record by 18% and turning a real shortfall into an apparent surplus. A factor set
    while no git-derived rows exist has nothing to correct and can only distort.
    """
    calib = config.calibration()
    if calib == 1.0:
        results.append(_r("calibration", OK, "1.0 (uncalibrated, honest)"))
        return
    n = conn.execute(
        "SELECT COUNT(*) FROM work_session WHERE provenance LIKE '%git%'").fetchone()[0]
    if n == 0:
        # WARN not FAIL: derive now applies the factor only to git-derived spans, so with
        # no such rows it is inert. It is still unvalidated -- nothing has been measured
        # against anything -- and it would start biasing the figure the moment a git
        # backfill lands, which is precisely when nobody would be looking at this check.
        results.append(_r("calibration", WARN,
                          f"factor {calib} set but no git-derived sessions exist to justify it",
                          "inert today; it starts biasing the figure as soon as one lands"))
    else:
        results.append(_r("calibration", OK, f"{calib} over {n} git-derived session(s)"))


def _month(results, conn, month):
    rows = derive.day_rows(conn, month)
    if not rows:
        results.append(_r(f"day_class:{month}", FAIL, "month not classified",
                          f"timetrack seed --month {month}"))
        return
    results.append(_r(f"day_class:{month}", OK, f"{len(rows)} day(s) classified"))

    bad = [r["attendance_day"] for r in rows
           if r["target_minutes"] == 0 and (r["span_minutes"] or 0) > 0]
    if bad:
        # worked time on a day the calendar calls off: either the classification is wrong
        # or the work is genuinely uncompensated, and the two need different answers
        results.append(_r("classification", WARN,
                          f"{len(bad)} non-working day(s) carry activity: {', '.join(bad[:4])}",
                          "reclassify, or accept it as unpaid"))
    else:
        results.append(_r("classification", OK, "no activity on non-working days"))

    s = derive.month_summary(conn, month)
    if s["days_missing_breaks"]:
        results.append(_r("basis", WARN,
                          f"{s['days_missing_breaks']} working day(s) have no break recorded; "
                          "their span is counted as-is and OVERSTATES net",
                          "record the breaks before submitting"))
    else:
        results.append(_r("basis", OK, f"basis {s['basis']} across every working day"))


def _dangling(results, conn):
    open_runs = conn.execute(
        "SELECT COUNT(*) FROM v_run_interval WHERE end_unix IS NULL").fetchone()[0]
    if open_runs > 1:
        results.append(_r("clock", FAIL, f"{open_runs} run intervals never closed",
                          "a crash left the clock running; reconcile before trusting totals"))
    elif open_runs == 1:
        results.append(_r("clock", OK, "1 run interval open (the clock is running)"))
    else:
        results.append(_r("clock", OK, "no dangling run intervals"))


def _activitywatch(results):
    """Resolve buckets by TYPE, never by name.

    sources.py asked for aw-watcher-window_devsupreme on a host called desktop-snoorullah,
    got a 404, and returned []. Resolving by type is why that cannot recur.
    """
    try:
        with urllib.request.urlopen("http://localhost:5600/api/0/buckets/", timeout=3) as fh:
            buckets = __import__("json").loads(fh.read())
    except (urllib.error.URLError, OSError, ValueError) as e:
        results.append(_r("activitywatch", WARN, f"aw-server unreachable: {e}",
                          "systemctl --user status aw-server; live capture is blind without it"))
        return
    types = {b.get("type") for b in buckets.values()}
    missing = {"currentwindow", "afkstatus"} - types
    if missing:
        results.append(_r("activitywatch", FAIL,
                          f"{len(buckets)} bucket(s) but no {', '.join(sorted(missing))}",
                          "awatcher is not reporting; this is the original silent-zero bug"))
    else:
        results.append(_r("activitywatch", OK, f"{len(buckets)} bucket(s), window + afk present"))


def _backup(results):
    latest = config.HOME / ".local/state/dots-ops/state/vault-backup.json"
    if not latest.exists():
        results.append(_r("backup", WARN, "vault-backup has never run",
                          "the databases that determine pay are unprotected"))
        return
    age_h = (time.time() - latest.stat().st_mtime) / 3600
    if age_h > 36:
        results.append(_r("backup", FAIL, f"last vault backup {age_h:.0f}h ago",
                          "systemctl --user start dots-ops@vault-backup"))
    else:
        results.append(_r("backup", OK, f"last vault backup {age_h:.0f}h ago"))


def run(month=None):
    month = month or date.today().strftime("%Y-%m")
    results = []
    _schema(results)
    _config(results)
    if config.WORK_DB.exists():
        try:
            conn = dbs.open_work(readonly=True)
            dbs.assert_single_database(conn)
            _calibration(results, conn)
            _month(results, conn, month)
            _dangling(results, conn)
            conn.close()
        except sqlite3.Error as e:
            results.append(_r("queries", FAIL, f"{e}"))
    _activitywatch(results)
    _backup(results)
    return results
