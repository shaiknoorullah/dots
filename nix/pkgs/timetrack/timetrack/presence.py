"""Is the human here? -- the `human_active` half of D3.

    human_active = AW not-afk  |  an open hotkey interval  |  a focused work window

Three sources because each fails differently. AW is the good one and the one that breaks:
R4 in the plan exists because an aw-server outage makes `human_active` falsely FALSE, which
stops the clock while the user is sitting there working.

**What happens when nothing can answer is the important decision here.** For an agent's
status the plan holds the previous value, because detection is lossy and the asymmetry
should favour the user. Presence is the opposite: this feeds a figure handed to an
employer, and claiming presence nobody can evidence is the one error that makes the whole
submission indefensible. So an unknown reads as NOT present -- which under-reports -- and
is flagged `degraded` so it is visible, auditable and can be backfilled deliberately.
Quietly guessing "yes" would be worth more minutes and far less trust.
"""
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta

from . import config

AW_BASE = "http://localhost:5600/api/0"
AW_TIMEOUT = 3
# Beyond this with no new AW event, the signal is not evidence of absence -- it is evidence
# that nobody is reporting. Chosen above awatcher's own heartbeat so a quiet desk does not
# trip it, and below the 90-second grace so a real outage is noticed within one cycle.
AW_STALE_SECONDS = 300
HOTKEY_FILE = config.STATE_DIR / "hotkey-interval.json"


@dataclass
class Presence:
    active: bool
    source: str          # aw | logind | hotkey | window | none
    degraded: bool       # no authoritative source could answer
    detail: str = ""


def _aw(path):
    with urllib.request.urlopen(f"{AW_BASE}{path}", timeout=AW_TIMEOUT) as fh:
        return json.loads(fh.read())


def aw_presence(now=None):
    """(active, stale, detail) from the newest afk event, or (None, True, why).

    Reads the newest event's STATUS and ignores its duration. ActivityWatch writes a
    duration only when a state ENDS, so the event in progress always carries 0 -- the same
    fact that made the ingest drop the live run and miss a 43-minute break. Here it means
    duration can say nothing about right now; only the status and the start time can.
    """
    now = now or datetime.now().astimezone()
    try:
        buckets = _aw("/buckets/")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, True, f"aw-server unreachable: {e}"
    name = next((n for n, m in buckets.items() if m.get("type") == "afkstatus"), None)
    if not name:
        return None, True, "no afkstatus bucket (awatcher not reporting)"
    try:
        events = _aw(f"/buckets/{name}/events?limit=1")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, True, f"aw bucket unreadable: {e}"
    if not events:
        return None, True, "afk bucket is empty"
    e = events[0]
    start = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).astimezone()
    status = (e.get("data") or {}).get("status")
    age = (now - start).total_seconds()
    end = start + timedelta(seconds=e.get("duration") or 0)
    if age > AW_STALE_SECONDS and end < now - timedelta(seconds=AW_STALE_SECONDS):
        return None, True, f"newest afk event is {age/60:.0f} min old"
    return status == "not-afk", False, f"{status} for {age/60:.1f} min"


def logind_presence():
    """Fallback for R4. True when logind says this user is not idle."""
    try:
        out = subprocess.run(
            ["loginctl", "show-user", str(os.geteuid()), "-p", "IdleHint"],
            capture_output=True, text=True, timeout=5, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        if line.startswith("IdleHint="):
            return line.split("=", 1)[1].strip() == "no"
    return None


def hotkey_open(now=None):
    """D4: an interval the user opened by hand always counts, whatever AW thinks."""
    now = now or time.time()
    try:
        d = json.loads(HOTKEY_FILE.read_text())
    except (OSError, ValueError):
        return False, ""
    if d.get("closed_unix"):
        return False, ""
    opened = d.get("opened_unix")
    if not opened:
        return False, ""
    return True, f"hotkey open {int((now - opened) / 60)} min ({d.get('note', '')})".strip()


def human_active(now=None):
    """Union the sources. Hotkey wins outright; otherwise AW, then logind."""
    hot, why = hotkey_open()
    if hot:
        return Presence(True, "hotkey", False, why)

    active, stale, detail = aw_presence(now)
    if not stale and active is not None:
        return Presence(active, "aw", False, detail)

    fb = logind_presence()
    if fb is not None:
        # honest about the downgrade: logind idle is a 15-minute-resolution signal, not AW's
        return Presence(fb, "logind", True, f"aw degraded ({detail}); logind idle_hint")

    return Presence(False, "none", True, f"no presence source: {detail}")
