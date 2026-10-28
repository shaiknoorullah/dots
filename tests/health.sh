#!/usr/bin/env bash
# D18 (health.py): a dose anchors the check-ins. What must never happen: a score invented
# for an unanswered question, a second dose duplicating the schedule, a past slot being
# asked, a question while PANIC is set, or the login hook blocking the away watcher.
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$root/nix/pkgs/timetrack"
export TIMETRACK_STATE_DIR="$tmp/state" XDG_CONFIG_HOME="$tmp/config"
python3 - "$tmp" <<'PY'
import sys
from datetime import datetime
from pathlib import Path

from timetrack import config, dbs, health, migrate

fails = []
def check(name, got, want):
    if got != want: fails.append(f"{name}: got {got!r}, want {want!r}")

tmp = Path(sys.argv[1])
config.PANIC = tmp / "config" / "timetrack" / "PANIC"
T = int(datetime(2026, 10, 27, 10, 0).timestamp())
DAY = "2026-10-27"
clock = [T]
health.now_unix = lambda: clock[0]
health._timetrack = lambda: "/bin/tt"
runs, spawns, toasts, asked = [], [], [], []
health.run = lambda argv, **kw: runs.append(argv)
health.spawn = lambda argv, **kw: spawns.append((argv, kw))
health.toast = lambda *a, **kw: toasts.append(a)
answer = [None]
def fake_ask(**kw):
    asked.append(kw)
    return answer[0]
health.ask_fn = fake_ask

migrate.migrate()
def reset():
    with dbs.open_private() as p:
        p.execute("DELETE FROM med_dose"); p.execute("DELETE FROM health_checkpoint"); p.commit()
    runs.clear(); spawns.clear(); toasts.clear(); asked.clear()
def cps():
    with dbs.open_private() as p:
        return {r["slot"]: dict(r) for r in p.execute("SELECT * FROM health_checkpoint")}
def doses():
    with dbs.open_private() as p:
        return [dict(r) for r in p.execute("SELECT * FROM med_dose")]

# 1. dose schedules h1/h3/h6 and asks the dose slot now
reset()
check("dose ok", health.dose(), True)
check("dose row", [(d["ts_unix"], d["day"], d["provenance"]) for d in doses()], [(T, DAY, "manual")])
c = cps()
check("due times", {k: v["due_unix"] - T for k, v in c.items()}, {"dose": 0, "h1": 3600, "h3": 10800, "h6": 21600})
check("all pending", {v["outcome"] for v in c.values()}, {"pending"})
check("systemd-run argv", runs[0], ["systemd-run", "--user", "--collect", "--on-active=3600",
      f"--unit=timetrack-health-h1-{DAY}", "/bin/tt", "health", "ask", "h1"])
check("three timers", [r[-1] for r in runs], ["h1", "h3", "h6"])
check("dose slot asked now, detached", [(a, k["start_new_session"]) for a, k in spawns],
      [(["/bin/tt", "health", "ask", "dose"], True)])

# 2. a second dose is refused, with no new schedule
runs.clear(); spawns.clear()
check("second dose refused", health.dose(), False)
check("no duplicate timers", (runs, spawns, len(doses())), ([], [], 1))
check("refusal said so", len(toasts), 1)

# 3. --ago skips past slots (150 min ago: dose and h1 passed, h3 and h6 ahead)
reset()
health.dose(ago=150)
c = cps()
check("ago: outcomes", {k: v["outcome"] for k, v in c.items()},
      {"dose": "skipped", "h1": "skipped", "h3": "pending", "h6": "pending"})
check("ago: only future timers", [r[-1] for r in runs], ["h3", "h6"])
check("ago: h3 in 30 min", runs[0][3], "--on-active=1800")
check("ago: dose slot not asked", spawns, [])
check("ago: dose ts", doses()[0]["ts_unix"], T - 150 * 60)

# 4. ask answered -> score
reset(); health.dose(); asked.clear()
answer[0] = "4"
check("answered returns score", health.ask("h1"), 4)
r = cps()["h1"]
check("answered row", (r["score"], r["outcome"], r["asked_unix"], r["answered_unix"]), (4, "answered", T, T))
check("ask prompt", (asked[0]["app"], asked[0]["summary"], asked[0]["body"], asked[0]["timeout"],
      [k for k, _ in asked[0]["actions"]]), ("Check-in", "How are you right now?", "1 rough · 5 sharp", 600, list("12345")))

# 5. timeout -> 'timeout', no score
answer[0] = None
check("timeout returns None", health.ask("h3"), None)
r = cps()["h3"]
check("timeout row", (r["score"], r["outcome"], r["answered_unix"]), (None, "timeout", None))

# 6. PANIC -> skipped, nothing asked
asked.clear()
config.PANIC.parent.mkdir(parents=True, exist_ok=True); config.PANIC.write_text("")
check("panic: no answer", health.ask("h6"), None)
check("panic: skipped, not asked", (cps()["h6"]["outcome"], asked), ("skipped", []))
config.PANIC.unlink()

# 7. wrap has no row until now
reset()
check("wrap: no row yet", "wrap" in cps(), False)
answer[0] = "2"
health.ask("wrap")
r = cps()["wrap"]
check("wrap row", (r["due_unix"], r["score"], r["outcome"]), (T, 2, "answered"))

# 8. login-ask
reset()
answer[0] = "skip"
health.login_ask()
check("login skip: dose slot skipped", cps()["dose"]["outcome"], "skipped")
check("login skip: no dose", doses(), [])
reset(); answer[0] = "now"; health.login_ask()
check("login now", [d["ts_unix"] for d in doses()], [T])
reset(); answer[0] = "h1"; health.login_ask()
check("login h1", [d["ts_unix"] for d in doses()], [T - 3600])
check("login h1: past dose slot skipped, h1 due now", (cps()["dose"]["outcome"], cps()["h1"]["outcome"]),
      ("skipped", "pending"))
reset(); answer[0] = None; health.login_ask()
check("login unanswered records nothing", (doses(), cps()), ([], {}))
# does nothing when a dose exists
reset(); health.dose(); asked.clear(); answer[0] = "now"
health.login_ask()
check("login-ask with a dose: silent", (asked, len(doses())), ([], 1))

# 9. weekly table
reset(); health.dose()
answer[0] = "5"; health.ask("h1"); answer[0] = "3"; health.ask("h6"); answer[0] = None; health.ask("h3")
lines = health.week(DAY).split("\n")
check("week: 7 days + header + mean", len(lines), 9)
check("week: header", lines[0].split(), ["day", "dose", "dose", "h1", "h3", "h6", "wrap"])
check("week: today row", lines[7].split(), [DAY, "10:00", "·", "5", "x", "3", "·"])
check("week: empty day", lines[1].split(), ["2026-10-21", "-", "·", "·", "·", "·", "·"])
check("week: mean row", lines[8].split(), ["mean", "-", "5.0", "-", "3.0", "-"])
check("week: --end shifts window", health.week("2026-10-28").split("\n")[6].split()[0], DAY)

# 10. on_login returns without blocking: one detached spawn, nothing awaited
reset()
health.on_login(T)
check("on_login: one detached spawn", [(a, k["start_new_session"]) for a, k in spawns],
      [(["/bin/tt", "health", "login-ask"], True)])
check("on_login: did not ask", asked, [])

if fails:
    print("health FAILED:")
    for f in fails: print("  " + f)
    raise SystemExit(1)
print("health ok (10 scenarios)")
PY
