#!/usr/bin/env bash
# The presence sources, and above all what happens when they cannot answer.
#
# A wrong `human_active` does not crash anything. It silently moves the number handed to an
# employer -- down if it says absent while the user works, up if it claims presence nobody
# can evidence. The second is the one that destroys a submission, so these assert that an
# unknown never reads as present.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)/nix/pkgs/timetrack"
python3 - <<'PY'
from timetrack import presence
fails = []
def check(name, got, want):
    if got != want: fails.append(f"{name}: got {got!r}, want {want!r}")

real_aw, real_logind, real_hot = presence.aw_presence, presence.logind_presence, presence.hotkey_open

# 1. AW healthy and present
presence.aw_presence = lambda now=None: (True, False, "not-afk for 2 min")
presence.hotkey_open = lambda now=None: (False, "")
p = presence.human_active()
check("aw present", (p.active, p.source, p.degraded), (True, "aw", False))

# 2. AW healthy and away -- that IS evidence of absence, not a degradation
presence.aw_presence = lambda now=None: (False, False, "afk for 20 min")
p = presence.human_active()
check("aw away", (p.active, p.source, p.degraded), (False, "aw", False))

# 3. R4: aw-server down, logind says not idle -> present, but flagged degraded
presence.aw_presence = lambda now=None: (None, True, "aw-server unreachable")
presence.logind_presence = lambda: True
p = presence.human_active()
check("aw down -> logind", (p.active, p.source, p.degraded), (True, "logind", True))

# 4. aw-server down, logind says idle
presence.logind_presence = lambda: False
p = presence.human_active()
check("aw down, logind idle", (p.active, p.source, p.degraded), (False, "logind", True))

# 5. NOTHING can answer. Must be False and degraded -- never a guessed yes.
presence.logind_presence = lambda: None
p = presence.human_active()
check("no source -> not present", p.active, False)
check("no source -> degraded", p.degraded, True)
check("no source -> named", p.source, "none")

# 6. a stale AW bucket is not evidence of absence; it degrades to the fallback
presence.aw_presence = lambda now=None: (None, True, "newest afk event is 40 min old")
presence.logind_presence = lambda: True
p = presence.human_active()
check("stale aw degrades", (p.active, p.source, p.degraded), (True, "logind", True))

# 7. an open hotkey interval outranks everything, including AW saying away
presence.aw_presence = lambda now=None: (False, False, "afk")
presence.logind_presence = lambda: False
presence.hotkey_open = lambda now=None: (True, "hotkey open 12 min")
p = presence.human_active()
check("hotkey wins", (p.active, p.source, p.degraded), (True, "hotkey", False))

presence.aw_presence, presence.logind_presence, presence.hotkey_open = real_aw, real_logind, real_hot
if fails:
    print("presence FAILED:")
    for f in fails: print("  " + f)
    raise SystemExit(1)
print("presence ok (7 scenarios)")
PY
