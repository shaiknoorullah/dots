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

check("token beats branch",
      r({"metadata": {"tokens": {"ticket": "T-1"}}, "branch": "feat/OTHER-9"}),
      ("T-1", 1.0, "token"))
check("branch beats repo",
      r({"branch": "feat/ABC-123-thing", "cwd": "/anywhere"}),
      ("ABC-123", 0.8, "branch"))
check("typed prefix", r({"branch": "fix/XY-7"}), ("XY-7", 0.8, "branch"))
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
print("binding ok (9 scenarios)")
PY
