"""Freeze a month's figure, and prove later that it was never retro-fitted.

A submitted number is a claim made to an employer on a date. If derivation changes
afterwards -- a bug fixed, a rule tightened, a break reclassified -- recomputing the month
silently produces a DIFFERENT number, and there is then no way to show which one was
actually handed over. That is the failure this guards: not fraud, but the ordinary case of
improving the code and losing the ability to defend last month's figure.

So `freeze` writes one immutable row per month: the figures, the ruleset that produced
them, and `input_digest` -- a sha256 over the exact ordered clock_event rows the figure
rested on. `verify` recomputes that digest and reports drift. Drift is not an error; it is
expected the moment a gap is classified. What matters is that it is VISIBLE and that the
frozen number stays readable next to the current one.

The table has month as its PRIMARY KEY and is never UPDATEd. Re-freezing is refused.
"""
import sqlite3
import time
from datetime import date

from . import config, derive, doctor


class Refused(Exception):
    """Raised instead of writing. The message is shown to the user verbatim."""


def existing(conn, month):
    row = conn.execute(
        "SELECT * FROM monthly_submission WHERE month = ?", (month,)).fetchone()
    return dict(row) if row else None


def preflight(month, allow_warn=True):
    """Invariants that must hold before a figure is defensible. Returns the check rows."""
    results = doctor.run(month=month)
    fails = [r for r in results if r["status"] == "fail"]
    if fails:
        raise Refused(
            "doctor reports "
            + "; ".join(f"{r['check']}: {r['detail']}" for r in fails)
            + "\n  a figure is not defensible while an invariant is broken")
    warns = [r for r in results if r["status"] == "warn"]
    if warns and not allow_warn:
        raise Refused("; ".join(f"{r['check']}: {r['detail']}" for r in warns))
    return results


def freeze(conn, month, export_dir=None, now=None, allow_warn=True):
    """Write the export, then the immutable row. Returns (row, summary, warnings)."""
    prior = existing(conn, month)
    if prior:
        raise Refused(
            f"{month} was already submitted at "
            f"{date.fromtimestamp(prior['submitted_unix']).isoformat()} "
            f"({derive.hhmm(prior['worked_minutes'])} on basis {prior['basis']}).\n"
            "  monthly_submission is immutable by design -- the point of the row is that\n"
            "  the number handed over cannot be quietly replaced by a later one.")

    results = preflight(month, allow_warn=allow_warn)
    warnings = [r for r in results if r["status"] == "warn"]

    from . import export_csv

    # Refuse a month that is still running. The row is immutable, so freezing on the 22nd
    # would permanently record a figure missing a third of the month and leave no way to
    # correct it. Submission is an end-of-month act; `export` is what you use before then.
    s_pre = derive.month_summary(conn, month)
    if s_pre["remaining_working_days"] > 0:
        raise Refused(
            f"{month} still has {s_pre['remaining_working_days']} working day(s) left "
            f"({', '.join(s_pre['remaining_days'][:4])}...).\n"
            "  The row cannot be rewritten, so freezing now would permanently record an\n"
            "  incomplete month. Use `timetrack export` until the month is done.")

    # Refuse an empty month. A zero figure is indistinguishable from a broken pipeline --
    # which is precisely how the predecessor failed -- and freezing it would make that
    # silence permanent and immutable.
    if s_pre["worked_minutes"] <= 0:
        raise Refused(
            f"{month} has no recorded work at all.\n"
            "  A zero month and a broken capture pipeline look identical, so this refuses\n"
            "  rather than freeze the ambiguity. Import or ingest the month first.")
    summary_path, _days_path, s = export_csv.write(conn, month, out_dir=export_dir)

    row = {
        "month": month,
        "submitted_unix": int(now or time.time()),
        "target_minutes": s["target_minutes"],
        "worked_minutes": s["worked_minutes"],
        "delta_minutes": s["worked_minutes"] - s["target_minutes"],
        "basis": s["basis"],
        "ruleset": s["ruleset"],
        "input_digest": derive.input_digest(conn, month),
        "export_path": str(summary_path),
    }
    try:
        conn.execute(
            "INSERT INTO monthly_submission(month,submitted_unix,target_minutes,"
            "worked_minutes,delta_minutes,basis,ruleset,input_digest,export_path)"
            " VALUES(:month,:submitted_unix,:target_minutes,:worked_minutes,"
            ":delta_minutes,:basis,:ruleset,:input_digest,:export_path)", row)
    except sqlite3.IntegrityError as e:
        raise Refused(f"{month} already has a submission row ({e})")
    conn.commit()
    return row, s, warnings


def verify(conn, month):
    """Compare the frozen figure against what the code produces today."""
    frozen = existing(conn, month)
    if not frozen:
        raise Refused(f"{month} has no submission row; nothing to verify")
    now_digest = derive.input_digest(conn, month)
    s = derive.month_summary(conn, month)
    return {
        "frozen": frozen,
        "current_worked": s["worked_minutes"],
        "current_basis": s["basis"],
        "current_ruleset": s["ruleset"],
        "current_digest": now_digest,
        "inputs_changed": now_digest != frozen["input_digest"],
        "figure_changed": s["worked_minutes"] != frozen["worked_minutes"],
        "ruleset_changed": s["ruleset"] != frozen["ruleset"],
        "drift_minutes": s["worked_minutes"] - frozen["worked_minutes"],
    }
