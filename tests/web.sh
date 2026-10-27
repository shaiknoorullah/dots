#!/usr/bin/env bash
# Browser time (web.py). The risk is counting a tab nobody was looking at: five days of raw
# web events summed to 97 hours. A web second counts only where the tab was current, the
# browser had OS focus, and AW said not-afk. The other assertions pin what is kept: domain
# and an allow-listed ticket id, never a URL, never an incognito tab.
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$root/nix/pkgs/timetrack"
export TIMETRACK_STATE_DIR="$tmp/state" XDG_CONFIG_HOME="$tmp/config"
python3 - <<'PY'
from datetime import date, datetime, timedelta

from timetrack import dbs, migrate, web
from timetrack.backfill import aw

fails = []
def check(name, got, want):
    if got != want: fails.append(f"{name}: got {got!r}, want {want!r}")

day = date(2026, 10, 27)
t = lambda h, m=0: datetime(2026, 10, 27, h, m).astimezone()

# 14:00-16:00 one ERP task tab is current the whole time
WEB = [(t(14), t(16), {"url": "https://erp.example.com/app/task/TASK-2026-00512",
                        "title": "about TASK-2026-00999", "incognito": False}),
       (t(16), t(17), {"url": "https://example.org/private", "incognito": True}),
       (t(17), t(18), {"url": "https://www.github.com/o/r/pull/7", "incognito": False})]
# the browser had focus 14:00-15:00 and 17:00-18:00 only
WIN = [(t(14), t(15), {"app": "zen-beta"}), (t(15), t(16), {"app": "com.mitchellh.ghostty"}),
       (t(16), t(17), {"app": "zen-beta"}), (t(17), t(18), {"app": "zen-beta"})]
# present 14:00-14:30 and 17:00-18:00
PRESENT = [(t(14), t(14, 30)), (t(17), t(18))]

web._events = lambda kind, a, b: WEB if kind == "web.tab.current" else WIN
aw.not_afk = lambda since=None, until=None, now=None: PRESENT

by_dom, by_tkt = web.day_totals(day)
check("gated to focus AND presence", by_dom.get("erp.example.com"), 1800.0)
check("www. stripped", by_dom.get("github.com"), 3600.0)
check("incognito dropped", "example.org" in by_dom, False)
check("ticket from URL path", by_tkt, {"TASK-2026-00512": 1800.0})

check("employee id is not a ticket", web.ticket("https://erp.example.com/app/employee/EMP-00097"), None)
check("project id is a ticket", web.ticket("https://erp.example.com/app/project/PROJ-0032"), "PROJ-0032")
check("query string ignored", web.ticket("https://x.example/search?q=TASK-2026-1"), None)

migrate.migrate()
web.write(day); web.write(day)  # a rerun replaces, never adds
conn = dbs.open_private(readonly=True)
check("rerun idempotent", conn.execute(
    "SELECT seconds FROM web_ticket_day WHERE day='2026-10-27'").fetchall()[0][0], 1800)
cols = {r[1] for tbl in ("web_domain_day", "web_ticket_day")
        for r in conn.execute(f"PRAGMA table_info({tbl})")}
check("no url or title column", cols & {"url", "title"}, set())

if fails:
    print("web FAILED:")
    for f in fails: print("  " + f)
    raise SystemExit(1)
print("web ok (4 scenarios, 10 assertions)")
PY
