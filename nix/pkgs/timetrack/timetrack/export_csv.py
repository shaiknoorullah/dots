"""CSV export of a month's figure.

Two files, and the order matters. D19: the submission leads with OUTCOMES, and hours
follow -- the point of the pack is to make a contribution visible, not to file a
timesheet. So `-summary.csv` is written first and is the page a reader opens.

CSV only, deliberately: no openpyxl. A spreadsheet binary cannot be diffed, cannot be
reviewed in a PR, and hides which cells are formulas.

Every per-day row carries its SOURCE and CONFIDENCE (D16). A reconstructed day and a
measured day must never be presented as the same kind of fact -- if a figure is
challenged, the row says for itself how it was obtained.
"""
import csv
from datetime import date

from . import config, derive

SUMMARY_HEADER = ["field", "value", "note"]

DAY_HEADER = [
    "date", "weekday", "day_type", "login", "logout",
    "span_hhmm", "break_hhmm", "net_hhmm", "target_hhmm", "delta_hhmm",
    "source", "confidence", "breaks_recorded",
]


def _delta(span, target):
    if span is None or not target:
        return None
    return span - target


def day_table(conn, month):
    """Per-day rows, in calendar order, each labelled by where it came from."""
    out = []
    for r in derive.day_rows(conn, month):
        d = date.fromisoformat(r["attendance_day"])
        span = r["span_minutes"] or 0
        has_breaks = r["deduction_minutes"] is not None
        out.append({
            "date": r["attendance_day"],
            "weekday": d.strftime("%a"),
            "day_type": r["kind"],
            "login": derive.clock(r["first_unix"]),
            "logout": derive.clock(r["last_unix"]),
            "span_hhmm": derive.hhmm(span) if span else "",
            "break_hhmm": derive.hhmm(r["deduction_minutes"]) if has_breaks else "",
            # net is NULL by design when no break was recorded: presenting a span as net
            # would over-report against an employer rule that is explicitly net of breaks
            "net_hhmm": derive.hhmm(r["net_minutes"]) if r["net_minutes"] is not None else "",
            "target_hhmm": derive.hhmm(r["target_minutes"]) if r["target_minutes"] else "",
            "delta_hhmm": derive.hhmm(_delta(span, r["target_minutes"])) if span else "",
            "source": r["provenance"] or "",
            "confidence": r["min_confidence"] if r["min_confidence"] is not None else "",
            "breaks_recorded": "yes" if has_breaks else "no",
        })
    return out


def summary_rows(conn, month, today=None):
    s = derive.month_summary(conn, month, today=today)
    cur = s["currency"]
    hm = derive.hhmm

    def money(v):
        return "" if v is None else f"{cur} {v:,.0f}"

    rows = [
        ("month", s["month"], "ruleset " + s["ruleset"]),
        ("generated", s["today"], "figures are as at this date"),
        ("", "", ""),
        ("working_days", s["working_days"], "weekdays minus paid holidays"),
        ("elapsed_working_days", s["elapsed_working_days"], ""),
        ("remaining_working_days", s["remaining_working_days"],
         ", ".join(s["remaining_days"]) if s["remaining_days"] else "none left"),
        ("target", hm(s["target_minutes"]), f"{config.hours_per_day()}h per working day"),
        ("", "", ""),
        ("basis", s["basis"], "net = after breaks, the employer's rule"),
        ("worked_to_date", hm(s["worked_minutes_to_date"]),
         f"over {s['elapsed_working_days']} elapsed working day(s)"),
        ("owed_to_date", hm(s["owed_to_date_minutes"]), "elapsed working days at target"),
        ("span_gross", hm(s["span_minutes"]),
         "first to last activity, BEFORE breaks -- not the submitted figure"),
        ("breaks_deducted", hm(s["span_minutes"] - s["net_minutes_known"]),
         f"over {s['days_with_breaks']} day(s) with a break recorded"),
        ("days_missing_breaks", s["days_missing_breaks"],
         "their span is counted as-is and overstates net" if s["days_missing_breaks"]
         else "none -- every working day has breaks recorded"),
        ("", "", ""),
        ("shortfall_to_date", hm(s["shortfall_to_date_minutes"]), "owed minus worked, to date"),
        ("still_needed", hm(s["still_needed_minutes"]), "to clear the month's target"),
        ("still_needed_per_day", hm(s["still_needed_per_day_minutes"]),
         "across the remaining working days"),
        ("projected", hm(s["projected_minutes"]),
         "where the month lands at a normal day from here"),
        ("projected_shortfall", hm(s["projected_shortfall_minutes"]), ""),
        ("", "", ""),
        ("daily_rate", money(s["daily_rate"]), "gross divided by working days"),
        ("hourly_rate", money(s["hourly_rate"]), f"daily divided by {config.hours_per_day()}"),
        ("value_of_shortfall_to_date", money(s["money_shortfall_to_date"]),
         f"on the {s['basis']} basis"),
        ("value_of_projected_shortfall", money(s["money_projected"]),
         "at a normal day's work for the rest of the month"),
        ("", "", ""),
        ("measured_days", s["measured_days"], "recorded live"),
        ("reconstructed_days", s["reconstructed_days"], "derived after the fact"),
        ("sources", " + ".join(s["provenance"]), "see the source column per day"),
        ("input_digest", derive.input_digest(conn, month),
         "sha256 of the events this figure rests on"),
    ]
    return rows, s


def write(conn, month, out_dir=None, today=None):
    """Write both files. Returns (summary_path, days_path, summary_dict)."""
    out_dir = out_dir or config.EXPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    rows, s = summary_rows(conn, month, today=today)
    summary_path = out_dir / f"{month}-summary.csv"
    with summary_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(SUMMARY_HEADER)
        w.writerows(rows)

    days_path = out_dir / f"{month}-days.csv"
    table = day_table(conn, month)
    with days_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=DAY_HEADER)
        w.writeheader()
        w.writerows(table)

    return summary_path, days_path, s
