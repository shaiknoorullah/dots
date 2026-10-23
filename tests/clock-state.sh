#!/usr/bin/env bash
# Replay recorded event orderings through the clock state machine and assert the result.
#
# The plan asks for this specifically, and the reason is that clockd's failure mode is not
# a crash: it is a number that looks reasonable and is wrong. A state machine driven only
# by a live socket can only be observed, never asserted.
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
cd "$root/nix/pkgs/timetrack"
python3 - <<'PY'
from timetrack.clock_state import (Clock, ADVANCING, GRACE, INACTIVE,
                                   RUNNING, AMBER, STOPPED, GRACE_SECONDS)
fails = []
def check(name, got, want):
    if got != want:
        fails.append(f"{name}: got {got!r}, want {want!r}")

# 1. a working agent runs the clock even with nobody at the keyboard
c = Clock(); c.agent_status("p1", "working", 100)
check("agent working -> RUNNING", c.state, RUNNING)
check("  attributed to agent", c.attribution(), "agent")

# 2. with the human present it is the submitted number instead
c.set_human(True, 110)
check("human present -> human", c.attribution(), "human")

# 3. blocked opens grace, and grace alone is AMBER, not RUNNING
c = Clock(); c.agent_status("p1", "blocked", 200)
check("blocked -> AMBER", c.state, AMBER)
c.tick(200 + GRACE_SECONDS - 1)
check("  still AMBER inside grace", c.state, AMBER)
c.tick(200 + GRACE_SECONDS)
check("  grace expires -> STOPPED", c.state, STOPPED)

# 4. unknown HOLDS, it does not reset -- the asymmetry that favours the user
c = Clock(); c.agent_status("p1", "working", 300)
c.agent_status("p1", "unknown", 310)
check("unknown holds ADVANCING", c.agents["p1"].sub, ADVANCING)
check("  clock still RUNNING", c.state, RUNNING)

# 5. idle and done both stop; done is not a third running state
for s in ("idle", "done"):
    c = Clock(); c.agent_status("p1", "working", 400)
    c.agent_status("p1", s, 410)
    check(f"{s} -> STOPPED", c.state, STOPPED)

# 6. multi-agent: one blocked must not mask another working (D3's whole point)
c = Clock()
c.agent_status("p1", "blocked", 500)
c.agent_status("p2", "working", 501)
check("blocked + working -> RUNNING", c.state, RUNNING)
c.agent_status("p2", "idle", 600)
check("  working stops, blocked still in grace -> AMBER", c.state, AMBER)

# 7. a pane that vanishes releases the clock (R-pane-closed)
c = Clock(); c.agent_status("p1", "working", 700)
c.pane_gone("p1", 710)
check("pane gone -> STOPPED", c.state, STOPPED)

# 8. the human alone runs the clock with no agents at all
c = Clock(); c.set_human(True, 800)
check("human alone -> RUNNING", c.state, RUNNING)
check("  attributed to human", c.attribution(), "human")
c.set_human(False, 900)
check("  human leaves -> STOPPED", c.state, STOPPED)

# 9. STOPPED attributes to nothing -- never silently to the human
c = Clock()
check("stopped -> none", c.attribution(), "none")

# 10. grace is re-armed by a repeated blocked, not left to expire on the first
c = Clock(); c.agent_status("p1", "blocked", 1000)
c.agent_status("p1", "blocked", 1050)
c.tick(1000 + GRACE_SECONDS + 1)
check("re-armed grace survives the first deadline", c.state, AMBER)

# 11. tickets are only reported for agents actually advancing
c = Clock()
c.agent_status("p1", "working", 1100); c.bind_ticket("p1", "T-1", 1.0, 1100)
c.agent_status("p2", "blocked", 1100); c.bind_ticket("p2", "T-2", 1.0, 1100)
check("only advancing tickets", c.advancing_tickets(), ["T-1"])

if fails:
    print("clock-state FAILED:")
    for f in fails: print("  " + f)
    raise SystemExit(1)
print("clock-state ok (11 scenarios)")
PY
