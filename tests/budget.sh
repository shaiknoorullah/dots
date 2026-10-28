#!/usr/bin/env bash
# D21 (budget.py) and notify.ask: the two pieces every Phase 3 prompt goes through. What
# must never happen: a third blocking prompt inside an hour, an exempt kind charged, an
# unanswered question returned as an answer, or a stale button left on screen.
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$root/nix/pkgs/timetrack"
export TIMETRACK_STATE_DIR="$tmp/state" XDG_CONFIG_HOME="$tmp/config"
python3 - <<'PY'
import subprocess

from timetrack import budget, dbs, migrate, notify

fails = []
def check(name, got, want):
    if got != want: fails.append(f"{name}: got {got!r}, want {want!r}")

migrate.migrate()
T = 1_800_000_000

# 1. budget: 2 an hour, 8 a day, deferred ones recorded but not charged
check("first", budget.take("leave_backfill", T), True)
check("second", budget.take("login_why", T + 60), True)
check("third in the hour deferred", budget.take("login_why", T + 120), False)
check("an hour later", budget.take("login_why", T + 3700), True)
for i in range(4):
    budget.take("leave_backfill", T + 7400 + i * 3700)
check("seven shown so far", budget.used(dbs.open_private(), T + 7400 + 3 * 3700 + 1)[1], 7)
check("eighth still allowed", budget.take("login_why", T + 30000), True)
check("ninth deferred by the day cap", budget.take("login_why", T + 33700), False)
with dbs.open_private() as c:
    check("deferred are logged", c.execute("SELECT COUNT(*) FROM prompt_log WHERE outcome='deferred'").fetchone()[0], 2)
for k in ("salah", "away"):
    try:
        budget.take(k, T); fails.append(f"{k} was charged")
    except ValueError:
        pass

# 2. notify.ask: the id line first, then the key; nothing else is an answer
calls = []
def fake(script):
    def popen(argv, **kw):
        calls.append(argv)
        if argv[0] == "gdbus":
            return subprocess.Popen(["true"])
        return subprocess.Popen(["sh", "-c", script], stdout=subprocess.PIPE, text=True)
    return popen
A = [("1", "1"), ("2", "2"), ("snooze", "Snooze")]
notify.popen = fake('printf "2\\n1\\n"')
check("id 2 then key 1", notify.ask("t", "s", "b", A, 5), "1")
check("no close after an answer", [c for c in calls if c[0] == "gdbus"], [])
check("actions passed", [a for a in calls[0] if a.startswith("--action=")], ["--action=1=1", "--action=2=2", "--action=snooze=Snooze"])
calls.clear()
notify.popen = fake('printf "9\\n"')
check("closed without a button", notify.ask("t", "s", "b", A, 5), None)
calls.clear()
notify.popen = fake('printf "9\\nrm -rf\\n"')
check("garbage is no answer", notify.ask("t", "s", "b", A, 5), None)
calls.clear()
notify.popen = fake('printf "12\\n"; sleep 30')
check("timeout is None", notify.ask("t", "s", "b", A, 1), None)
check("timeout closes the notification", [c[-1] for c in calls if c[0] == "gdbus"], ["12"])
def broken(argv, **kw): raise OSError("no notify-send")
notify.popen = broken
check("no notify-send is None", notify.ask("t", "s", "b", A, 1), None)

if fails:
    print("budget FAILED:")
    for f in fails: print("  " + f)
    raise SystemExit(1)
print("budget ok (2 scenarios, 16 assertions)")
PY
