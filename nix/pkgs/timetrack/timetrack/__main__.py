"""timetrack CLI.

Every subcommand opens its own connection through dbs.py and does one thing. There is no
`all` command on purpose: the old one chained rollup -> note -> sync and swallowed the
failure of each, which is how the predecessor recorded nothing for months while appearing
to work.

`export` is read-only against work.db. Nothing here can reach private.db, and nothing here
writes upstream -- the ERP integration is gated and does not exist yet.
"""
import argparse
import sys
from datetime import date
from pathlib import Path

from . import calendar_, config, dbs, derive, export_csv, migrate


def _month(arg):
    if arg:
        return arg
    return date.today().strftime("%Y-%m")


def cmd_migrate(a):
    migrate.migrate()
    print("schema up to date")
    return 0


def cmd_seed(a):
    y, m = (int(x) for x in _month(a.month).split("-"))
    with dbs.open_work() as conn:
        n = calendar_.seed_month(conn, y, m)
        conn.commit()
    print(f"{_month(a.month)}: {n} day(s) classified")
    return 0


def cmd_import_sheet(a):
    from .backfill import sheet
    with dbs.open_work() as conn:
        n = sheet.write(conn, a.path)
        conn.commit()
    print(f"imported {n} row(s) from {a.path}")
    return 0


def cmd_backfill_git(a):
    from .backfill import git_
    with dbs.open_work() as conn:
        n = git_.write(conn, a.since, a.until)
        conn.commit()
    print(f"git backfill wrote {n} session(s) at confidence {git_.CONFIDENCE}")
    return 0


def cmd_status(a):
    month = _month(a.month)
    with dbs.open_work(readonly=True) as conn:
        s = derive.month_summary(conn, month)
    hm = derive.hhmm
    print(f"{month}  ruleset {s['ruleset']}  as at {s['today']}  basis {s['basis']}")
    print(f"  target            {hm(s['target_minutes'])} over {s['working_days']} working days")
    print(f"  worked to date    {hm(s['worked_minutes_to_date'])} "
          f"against {hm(s['owed_to_date_minutes'])} owed over "
          f"{s['elapsed_working_days']} elapsed day(s)")
    print(f"  span (gross)      {hm(s['span_minutes'])} before breaks")
    if s["days_missing_breaks"]:
        # say it plainly rather than quietly reporting a span as if it were net
        print(f"  !! {s['days_missing_breaks']} day(s) have no break recorded; "
              f"their span is counted as-is and OVERSTATES the net figure")
    print(f"  shortfall to date {hm(s['shortfall_to_date_minutes'])}"
          + (f"  = {s['currency']} {s['money_shortfall_to_date']:,.0f}"
             if s["money_shortfall_to_date"] is not None else ""))
    if s["remaining_working_days"]:
        print(f"  still needed      {hm(s['still_needed_minutes'])} "
              f"= {hm(s['still_needed_per_day_minutes'])}/day over "
              f"{s['remaining_working_days']} day(s)")
    print(f"  projected         {hm(s['projected_minutes'])} "
          f"(short {hm(s['projected_shortfall_minutes'])})")
    if s["money_projected"] is not None:
        print(f"  value at risk     {s['currency']} {s['money_projected']:,.0f}")
    print(f"  sources           {' + '.join(s['provenance']) or 'none'}")
    return 0


def cmd_export(a):
    month = _month(a.month)
    with dbs.open_work(readonly=True) as conn:
        summary, days, s = export_csv.write(conn, month, out_dir=a.out)
    print(f"{summary}\n{days}")
    print(f"  {s['working_days']} working days, worked "
          f"{derive.hhmm(s['worked_minutes_to_date'])} ({s['basis']}), short "
          f"{derive.hhmm(s['shortfall_to_date_minutes'])} to date")
    return 0


def cmd_days(a):
    month = _month(a.month)
    with dbs.open_work(readonly=True) as conn:
        rows = export_csv.day_table(conn, month)
    wide = [r for r in rows if r["day_type"] == "working"]
    print(f"{'date':12}{'in':7}{'out':7}{'span':8}{'brk':7}{'net':8}{'src':16}conf")
    for r in wide:
        print(f"{r['date']:12}{r['login']:7}{r['logout']:7}{r['span_hhmm']:8}"
              f"{r['break_hhmm']:7}{r['net_hhmm']:8}{r['source'][:15]:16}{r['confidence']}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="timetrack", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("migrate", help="create or upgrade the databases").set_defaults(fn=cmd_migrate)

    s = sub.add_parser("seed", help="classify a month's days (working/weekend/holiday)")
    s.add_argument("--month", help="YYYY-MM, default this month")
    s.set_defaults(fn=cmd_seed)

    s = sub.add_parser("import-sheet", help="import the hand-kept timesheet CSV")
    s.add_argument("path")
    s.set_defaults(fn=cmd_import_sheet)

    s = sub.add_parser("backfill-git", help="reconstruct sessions from commit times")
    s.add_argument("--since", required=True)
    s.add_argument("--until", required=True)
    s.set_defaults(fn=cmd_backfill_git)

    s = sub.add_parser("status", help="where the month stands")
    s.add_argument("--month")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("days", help="per-day table for a month")
    s.add_argument("--month")
    s.set_defaults(fn=cmd_days)

    s = sub.add_parser("export", help="write the submission CSVs")
    s.add_argument("--month")
    s.add_argument("--out", type=Path,
                   help="output directory, default the vault's exports/")
    s.set_defaults(fn=cmd_export)

    a = p.parse_args(argv if argv is not None else sys.argv[1:])
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
