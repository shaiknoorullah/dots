#!/usr/bin/env bash
# Ticket binding tiers. The confidence attached here travels with every clock_event, so a
# wrong tier does not change a total -- it misrepresents the EVIDENCE behind one, which is
# worse, because the total still looks right while the defence of it is false.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)/nix/pkgs/timetrack"
python3 - <<'PY'
from timetrack import binding
fails = []
def check(name, got, want):
    if got != want: fails.append(f"{name}: got {got!r}, want {want!r}")

def r(pane): 
    v, c, t = binding.resolve(pane)
    return (v, c, t)

# THE SHAPE HERDR ACTUALLY RETURNS. Captured verbatim from `herdr agent list` after a
# `herdr pane report-metadata <pane> --source timetrack --token ticket=TEST-1`: the tokens
# sit at the top level of the pane entry. The first implementation looked under
# pane["metadata"], which herdr never sends, so tier 1 could never fire and a ticket the
# user had explicitly stated was recorded as if the branch name had merely implied it.
check("verified herdr shape: top-level tokens",
      r({"agent": "claude", "agent_status": "working", "pane_id": "w4:p1",
         "foreground_cwd": "/home/x/work/repo", "tokens": {"ticket": "TEST-1"},
         "git_branch": "feat/OTHER-9"}),
      ("TEST-1", 1.0, "token"))
check("token beats branch",
      r({"metadata": {"tokens": {"ticket": "T-1"}}, "branch": "feat/OTHER-9"}),
      ("T-1", 1.0, "token"))
# the ttl lapsed between one reconcile and the next: herdr drops the key entirely, and the
# binding must fall back to the branch rather than keep claiming the expired ticket
check("expired token falls to branch",
      r({"pane_id": "w4:p1", "git_branch": "feat/ABC-9-thing"}),
      ("ABC-9", 0.8, "branch"))
check("branch beats repo",
      r({"branch": "feat/ABC-123-thing", "cwd": "/anywhere"}),
      ("ABC-123", 0.8, "branch"))
check("typed prefix", r({"branch": "fix/XY-7"}), ("XY-7", 0.8, "branch"))
# The ids this system actually has to bind: the ERP `Task` naming series is three
# segments. A two-segment pattern matched TASK-2026 and stopped, so every ticket raised in
# 2026 would have been recorded as the same ticket and ticket_minutes would have held one
# row for the year. Asserted for both tiers, and for the hook that feeds tier 1.
check("three-segment erp id, branch",
      r({"branch": "feat/TASK-2026-00512-sha-pin"}), ("TASK-2026-00512", 0.8, "branch"))
check("three-segment erp id, loose",
      r({"branch": "TASK-2026-00533"}), ("TASK-2026-00533", 0.8, "branch"))
check("three-segment erp id, token",
      r({"tokens": {"ticket": "TASK-2026-00512"}}), ("TASK-2026-00512", 1.0, "token"))
check("two-segment id is unharmed", r({"branch": "feat/ABC-123-thing"}), ("ABC-123", 0.8, "branch"))
check("loose id still binds", r({"branch": "my-feat/ABC-1"}), ("ABC-1", 0.8, "branch"))
check("no id in branch", r({"branch": "main"}), (None, 0.0, "none"))
check("lowercase is not a ticket", r({"branch": "feat/abc-1"}), (None, 0.0, "none"))
check("nothing", r({}), (None, 0.0, "none"))

# an expired or absent token must NOT fall through to a stale value
check("empty metadata", r({"metadata": {}, "branch": "main"}), (None, 0.0, "none"))

# a repo match is a PROJECT and never silently becomes a ticket
v, c = binding.from_repo("/nowhere/unmapped")
check("unmapped repo yields nothing", (v, c), (None, 0.0))

if fails:
    print("binding FAILED:")
    for f in fails: print("  " + f)
    raise SystemExit(1)
print("binding ok (15 scenarios)")
PY
