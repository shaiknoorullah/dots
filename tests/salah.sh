#!/usr/bin/env bash
# D8 (salah.py): the enforcement ladder. What must never happen: a lock for an absent or
# already-answered user, a lock inside the spacing window, a strike for anything but an
# escaped lock, a button for qaza once the day's budget is spent, or the real desktop being
# touched. Every clock, notification and subprocess is a replaced seam.
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$root/nix/pkgs/timetrack"
export TIMETRACK_STATE_DIR="$tmp/state" XDG_CONFIG_HOME="$tmp/config"
python3 - "$tmp" <<'PY'
import sys
from datetime import datetime
from pathlib import Path

from timetrack import away, config, dbs, migrate, notify, salah

fails, n_assert, n_scen = [], 0, 0
def check(name, got, want):
    global n_assert
    n_assert += 1
    if got != want: fails.append(f"{name}: got {got!r}, want {want!r}")

tmp = Path(sys.argv[1])
away.INTENT = tmp / "state" / "away-intent.json"
away.PRAYER_TIMES = tmp / "prayer-times.conf"
config.PANIC = tmp / "config" / "timetrack" / "PANIC"
config.HABITS_TOML = tmp / "config" / "timetrack" / "habits.toml"
D = datetime(2026, 10, 27)
at = lambda h, m: int(D.replace(hour=h, minute=m).timestamp())
away.PRAYER_TIMES.write_text("Dhuhr 13:30  # c\nAsr 17:15\nMaghrib 17:51\nIsha 19:30\n")
migrate.migrate()

clock = [0]
toasts, asks, calls = [], [], []
script = []                 # answers handed to notify.ask, in order
def fake_ask(app, summary, body, actions, timeout, urgency="normal"):
    asks.append({"summary": summary, "keys": [k for k, _ in actions], "timeout": timeout,
                 "urgency": urgency})
    ans = script.pop(0) if script else None
    if ans is None:
        clock[0] += timeout          # an unanswered question takes its whole timeout
    return ans
notify.ask = fake_ask
notify.toast = lambda app, summary, body="", **kw: toasts.append((summary, body))
def sleep(s): clock[0] += s
salah._now = lambda: clock[0]
salah._sleep = sleep
salah._lock = lambda: calls.append(("lock", clock[0]))
salah._day = lambda now: "2026-10-27"

state = {}
def world(**kw):
    """Reset the fakes. unlock_after: seconds the lock screen stays up."""
    global n_scen
    n_scen += 1
    state.update(present=True, fg=[], unlock_after=3600, **kw)
    toasts.clear(); asks.clear(); calls.clear(); script.clear()
    for p in (away.INTENT, config.PANIC): p.unlink(missing_ok=True)
    config.HABITS_TOML.unlink(missing_ok=True)
    with dbs.open_private() as c:
        c.execute("DELETE FROM salah_log"); c.execute("DELETE FROM salah_pause"); c.commit()
def fg():
    return state["fg"].pop(0) if state["fg"] else None
def locked():
    t0 = calls[0][1] if calls else None
    return t0 is not None and clock[0] - t0 < state["unlock_after"]
salah._presence = lambda now: state["present"]
salah._foreground = fg
salah._locked = locked

def run(prayer, h, m):
    clock[0] = at(h, m)
    return salah.enforce(prayer, clock[0])
def row(prayer):
    with dbs.open_private() as c:
        r = c.execute("SELECT * FROM salah_log WHERE prayer=?", (prayer,)).fetchone()
    return dict(r) if r else None
def seed(prayer, **cols):
    enf = cols.pop("enforcement", "locked")
    keys = ["day", "prayer", "enforcement"] + list(cols)
    vals = ["2026-10-27", prayer, enf] + list(cols.values())
    with dbs.open_private() as c:
        c.execute(f"INSERT INTO salah_log({','.join(keys)}) VALUES({','.join('?'*len(keys))})", vals)
        c.commit()
def intent():
    return away._read(away.INTENT, {})

# 1. panic: toast only
world(); config.PANIC.parent.mkdir(parents=True, exist_ok=True); config.PANIC.write_text("")
check("panic", run("Asr", 17, 15), "panic")
check("panic no ask/lock", (asks, calls, len(toasts)), ([], [], 1))

# 2. already logged / declared
world(); seed("Asr", enforcement="pending", status="jamaah")
check("logged", run("Asr", 17, 15), "logged")
check("logged silent", (asks, toasts, calls), ([], [], []))
world(); away.declare("salah", at(17, 10))
check("declared", run("Asr", 17, 15), "declared")
check("declared silent", (asks, toasts, calls), ([], [], []))
world(); away.declare("salah", at(16, 0))
check("stale declaration does not count", run("Asr", 17, 15), "locked")

# 3. away; AW down
world(); state["present"] = False
check("away", run("Asr", 17, 15), "away")
check("away toast", "Super+Shift+;" in toasts[0][0] and "away" in toasts[0][0], True)
check("away no lock", (asks, calls), ([], []))
world(); state["present"] = None
check("aw down", run("Asr", 17, 15), "vetoed")
check("aw down reason", (row("Asr")["veto_reason"], asks, calls), ("presence_unknown", [], []))

# 4. spaced
world(); seed("Asr", locked_unix=at(17, 15) + 60, unlocked_unix=at(17, 25))
check("spaced", run("Maghrib", 17, 51), "spaced")
check("spaced no lock", (asks, calls), ([], []))
world(); seed("Dhuhr", locked_unix=at(13, 31))
check("not spaced hours later", run("Asr", 17, 15), "locked")

# 5. paused
world()
with dbs.open_private() as c:
    c.execute("INSERT INTO salah_pause VALUES(?,?,?)", (at(9, 0), at(9, 0) + 86400, "three_strikes")); c.commit()
check("paused", run("Asr", 17, 15), "disabled")
check("paused no lock", (asks, calls), ([], []))

# 6. go -> locked, intent written, unlock after 10 min = no strike
world(); script[:] = ["go"]; state["unlock_after"] = 600
check("go", run("Asr", 17, 15), "locked")
r = row("Asr")
check("go row", (r["enforcement"], r["locked_unix"], r["strike"], r["snoozed"]), ("locked", at(17, 15), 0, 0))
check("go unlocked", r["unlocked_unix"] is not None and r["unlocked_unix"] - r["locked_unix"] >= 600, True)
check("go intent", intent().get("key"), "salah")
check("go ask", (asks[0]["summary"], asks[0]["urgency"], asks[0]["timeout"]), ("Asr · iqamah 17:15", "critical", 240))
check("go buttons", asks[0]["keys"], ["go", "snooze", "qaza_meeting", "qaza_incident"])

# 7. snooze, then silence twice -> armed -> locked
world(); script[:] = ["snooze"]
check("snooze path", run("Asr", 17, 15), "locked")
check("snooze asks", [(a["timeout"], "snooze" in a["keys"]) for a in asks], [(240, True), (240, False), (60, False)])
check("armed text", asks[2]["summary"], "Locking in 60 s")
check("snooze recorded", row("Asr")["snoozed"], 1)
check("lock after snooze+ask+armed", calls[0][1] - at(17, 15), 240 + 240 + 60)
check("lock time >= 5 min late", calls[0][1] - at(17, 15) >= 300, True)

# 8. qaza chosen
world(); script[:] = ["qaza_meeting"]
check("qaza", run("Asr", 17, 15), "qaza")
r = row("Asr")
check("qaza row", (r["qaza_reason"], r["status"], calls), ("meeting", None, []))
world(); script[:] = [None, "qaza_incident"]
check("qaza from armed", run("Asr", 17, 15), "qaza")
check("qaza armed reason", row("Asr")["qaza_reason"], "incident")

# 9. budget used: no qaza buttons
world(); seed("Dhuhr", qaza_reason="meeting"); seed("Isha", qaza_reason="call"); script[:] = ["go"]
run("Asr", 17, 15)
check("budget spent", asks[0]["keys"], ["go", "snooze"])
world(); seed("Dhuhr", qaza_reason="meeting"); script[:] = ["go"]
run("Asr", 17, 15)
check("budget left", "qaza_meeting" in asks[0]["keys"], True)
world(); config.HABITS_TOML.parent.mkdir(parents=True, exist_ok=True)
config.HABITS_TOML.write_text("[salah]\nqaza_per_day = 0\n"); script[:] = ["go"]
run("Asr", 17, 15)
check("budget from habits.toml", asks[0]["keys"], ["go", "snooze"])

# 10. veto clears -> armed again -> lock
world(); state["fg"] = ["terraform", "terraform", "terraform"]
check("veto then lock", run("Asr", 17, 15), "locked")
r = row("Asr")
check("veto reason kept", r["veto_reason"], "terraform")
check("veto toast", any("Holding the lock while terraform runs" in t[0] for t in toasts), True)
check("armed twice", [a["summary"] for a in asks if a["summary"].startswith("Locking")].__len__(), 2)
check("lock after 2 polls", calls[0][1] - at(17, 15) >= 60 + 30 + 60, True)

# 11. veto until the cap -> qaza long_command (cap = next iqamah, 17:51)
world(); state["fg"] = ["kubectl"] * 100000
check("veto cap", run("Asr", 17, 15), "qaza")
r = row("Asr")
check("veto cap row", (r["qaza_reason"], r["veto_reason"], calls), ("long_command", "kubectl", []))
check("veto cap time", clock[0] >= at(17, 51) and clock[0] < at(17, 51) + 60, True)
world(); state["fg"] = ["ssh"] * 100000
check("last prayer cap +90m", run("Isha", 19, 30), "qaza")
check("last cap time", at(21, 0) <= clock[0] < at(21, 0) + 60, True)

# 12. quick unlock -> strike; third strike -> pause
world(); script[:] = ["go"]; state["unlock_after"] = 30
run("Asr", 17, 15)
check("strike", row("Asr")["strike"], 1)
check("one strike no pause", dbs.open_private().execute("SELECT COUNT(*) FROM salah_pause").fetchone()[0], 0)
world(); seed("Dhuhr", locked_unix=at(13, 31) - 86400, strike=1)
seed("Fajr", locked_unix=at(5, 0) - 2 * 86400, strike=1)
script[:] = ["go"]; state["unlock_after"] = 30
run("Asr", 17, 15)
with dbs.open_private() as c:
    p = c.execute("SELECT * FROM salah_pause").fetchall()
check("three strikes pause", (len(p), p[0]["reason"] if p else None), (1, "three_strikes"))
check("pause is a week", p[0]["until_unix"] - p[0]["start_unix"], 7 * 86400)
check("pause toast", any("Three escaped locks this week" in t[0] for t in toasts), True)
world(); seed("Dhuhr", locked_unix=at(13, 31) - 9 * 86400, strike=1)   # too old to count
seed("Fajr", locked_unix=at(5, 0) - 2 * 86400, strike=1)
script[:] = ["go"]; state["unlock_after"] = 30
run("Asr", 17, 15)
check("old strike ignored", dbs.open_private().execute("SELECT COUNT(*) FROM salah_pause").fetchone()[0], 0)
world(); script[:] = ["go"]; state["unlock_after"] = 200
run("Asr", 17, 15)
check("slow unlock no strike", row("Asr")["strike"], 0)

# 13. log upsert
world(); clock[0] = at(17, 40)
salah.log("asr", "jamaah", clock[0])
r = row("Asr")
check("log new", (r["status"], r["enforcement"], r["logged_unix"], r["iqamah_unix"]), ("jamaah", "logged", at(17, 40), at(17, 15)))
salah.log("Asr", "qaza", clock[0] + 5)
with dbs.open_private() as c:
    rows = c.execute("SELECT status, logged_unix FROM salah_log WHERE prayer='Asr'").fetchall()
check("log upsert one row", [tuple(x) for x in rows], [("qaza", at(17, 40) + 5)])
world(); seed("Asr", enforcement="away")
salah.log("Asr", "alone", at(18, 0))
check("log keeps enforcement", (row("Asr")["enforcement"], row("Asr")["status"]), ("away", "alone"))
world(); script[:] = ["go"]
salah.log("Asr", "jamaah", at(17, 14))
check("logged before iqamah", run("Asr", 17, 15), "logged")

if fails:
    print("\n".join(fails)); sys.exit(1)
print(f"salah ok ({n_scen} scenarios, {n_assert} assertions)")
PY
