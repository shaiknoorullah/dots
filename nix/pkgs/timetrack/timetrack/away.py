"""D7: ask what an away gap was the moment the user comes back, not days later.

Meetings are offline, so no local sensor can tell a meeting from a break. Only the user
can, and only while they still remember. So on return from AFK:

    gap < MIN_GAP (3 min)     AW ingest already deducts it, confirmed; nothing to ask
    gap < PROMPT_MIN (10 min) auto-classified as a short break (deducted) + a 4s toast
    longer                    a blocking panel, salah pre-selected near an iqamah

The prompt budget (D21) caps blocking panels at 2 an hour and 8 a day. Over budget, or on
no answer, the gap stays UNCONFIRMED for the batch fallback (`timetrack backfill`).
Nothing is ever answered for the user: a timeout records an absence, not a choice.

Auto-classification only ever DEDUCTS. Getting a short gap wrong under-reports; it can
never put a minute into the figure that nobody evidenced.
"""
import json
import shutil
import subprocess
import sys
import time
from datetime import date, datetime, timedelta

from . import config, dbs, interactive
from .backfill import aw

MIN_GAP = aw.MIN_GAP
PROMPT_MIN = 10 * 60
RECENT = 5 * 60            # a return older than this was missed; the batch panel has it
BUDGET_HOUR, BUDGET_DAY = 2, 8
SALAH_BEFORE, SALAH_AFTER = 10 * 60, 30 * 60
AUTO = "return:auto"
STATE = config.STATE_DIR / "away-state.json"
PRAYER_TIMES = config.HOME / ".config/adhd/prayer-times.conf"
POLL = 15


def _ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def last_return(events, now):
    """(gap_start, gap_end) if the user has just come back from a long enough absence.

    `events` is the afk bucket newest first. The newest event must be not-afk and recent;
    the one before it afk. ActivityWatch backdates an afk event to the last input, so its
    start is when the user actually left.
    """
    if len(events) < 2:
        return None
    cur, prev = events[0], events[1]
    if (cur.get("data") or {}).get("status") != "not-afk":
        return None
    if (prev.get("data") or {}).get("status") != "afk":
        return None
    back = _ts(cur["timestamp"])
    left = _ts(prev["timestamp"])
    if now - back > RECENT or back - left < MIN_GAP:
        return None
    return int(left), int(back)


def salah_hint(g0, g1, path=None):
    """The prayer whose iqamah falls inside the gap (with margins), or None."""
    path = path or PRAYER_TIMES
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    day = datetime.fromtimestamp(g0).date()
    for line in lines:
        parts = line.split("#", 1)[0].split()
        if len(parts) < 2 or ":" not in parts[1]:
            continue
        hh, mm = (int(x) for x in parts[1].split(":"))
        iq = datetime.combine(day, datetime.min.time()).replace(hour=hh, minute=mm).timestamp()
        if g0 <= iq + SALAH_AFTER and g1 >= iq - SALAH_BEFORE:
            return parts[0]
    return None


def _state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {"handled": 0, "prompts": []}


def _save(st):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st))
    tmp.replace(STATE)


def within_budget(prompts, now):
    hour = sum(1 for t in prompts if now - t < 3600)
    day = sum(1 for t in prompts if datetime.fromtimestamp(t).date() == date.fromtimestamp(now))
    return hour < BUDGET_HOUR and day < BUDGET_DAY


def find_gap(conn, g0, g1):
    """The unconfirmed deduction that best matches [g0, g1), or None."""
    best, best_ov = None, 0
    for r in conn.execute(
            "SELECT id, start_unix, end_unix, minutes FROM deduction"
            " WHERE confirmed = 0 AND start_unix < ? AND end_unix > ?", (g1, g0)):
        ov = min(r["end_unix"], g1) - max(r["start_unix"], g0)
        if ov > best_ov:
            best, best_ov = dict(r), ov
    # half the gap at least, so a neighbouring gap is never answered by mistake
    return best if best and best_ov >= 0.5 * (g1 - g0) else None


def _toast(body):
    try:
        subprocess.Popen(["notify-send", "-a", "timetrack", "-t", "4000", "Away", body],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def _clock(t):
    return datetime.fromtimestamp(t).strftime("%H:%M")


def ask(hint=None):
    """Inside the panel. Menu to stderr; only the answer key goes to stdout."""
    default = next((k for k, lbl, _ in interactive.GAP_CHOICES if lbl == "salah"), None) \
        if hint else None
    for key, label, _ in interactive.GAP_CHOICES:
        mark = "  <- Enter" if key == default else ""
        print(f"    {key}) {label}{mark}", file=sys.stderr)
    valid = {c[0] for c in interactive.GAP_CHOICES}
    while True:
        try:
            # the prompt to stderr too: input()'s own prompt goes to stdout, the answer file
            print("  what was it? ", end="", file=sys.stderr, flush=True)
            got = input().strip().lower()
        except EOFError:
            return 1
        if not got and default:
            got = default
        if got in valid:
            print(got)
            return 0
        print("    ?", file=sys.stderr)


def _choice(key):
    return next((c[2] for c in interactive.GAP_CHOICES if c[0] == key), "SKIP")


def handle(g0, g1, now, launch=None):
    """Classify or ask about one return. Returns what happened, for the log and tests."""
    with dbs.open_work() as conn:
        aw.write(conn, refresh_day=date.today().isoformat())
        gap = find_gap(conn, g0, g1)
        if not gap:
            return "no-gap"
        mins = round((g1 - g0) / 60)
        if g1 - g0 < PROMPT_MIN:
            interactive.answer_gap(conn, gap["id"], "misc", provenance=AUTO)
            conn.commit()
            _toast(f"{mins} min away · counted as a short break")
            return "auto"

    st = _state()
    if config.panic() or not within_budget(st["prompts"], now):
        _toast(f"{mins} min away ({_clock(g0)}–{_clock(g1)}) · answer later: timetrack backfill")
        return "deferred"
    st["prompts"] = [t for t in st["prompts"] if now - t < 86400] + [now]
    _save(st)

    hint = salah_hint(g0, g1)
    title = f"Away {_clock(g0)}–{_clock(g1)} ({mins} min). What was it?"
    if hint:
        title += f"  (near {hint} iqamah)"
    # absolute paths: the panel runs under the user manager, whose PATH may lack both
    tt = shutil.which("timetrack") or str(config.HOME / ".nix-profile/bin/timetrack")
    cmd = [str(config.HOME / ".local/bin/timetrack-panel"), f"away-{g1}", title, "--",
           tt, "away", "--ask"]
    if hint:
        cmd += ["--hint", hint]
    launch = launch or (lambda c: subprocess.run(c, capture_output=True, text=True, timeout=700))
    try:
        res = launch(cmd)
    except (OSError, subprocess.SubprocessError):
        return "panel-failed"
    if res.returncode != 0 or not res.stdout.strip():
        return "unanswered"      # the panel spooled timeout/dismissal itself
    choice = _choice(res.stdout.strip().splitlines()[-1])
    if choice == "SKIP":
        return "skipped"
    with dbs.open_work() as conn:
        gap = find_gap(conn, g0, g1)  # re-find: an hourly refresh may have re-numbered it
        if not gap:
            return "no-gap"
        interactive.answer_gap(conn, gap["id"], choice)
        conn.commit()
    return f"answered:{choice or 'meeting'}"


def watch():
    """Poll the afk bucket; handle each return once."""
    while True:
        now = time.time()
        try:
            events = aw._get(f"/buckets/{aw.bucket('afkstatus')}/events?limit=2")
            ret = last_return(events, now)
        except SystemExit as e:      # aw-server down: nothing to detect, keep watching
            print(f"away: {e}", file=sys.stderr)
            ret = None
        st = _state()
        if ret and ret[1] > st.get("handled", 0):
            st["handled"] = ret[1]
            _save(st)
            try:
                out = handle(*ret, now)
            except (Exception, SystemExit) as e:   # one bad return must not kill the watcher
                out = f"error: {e}"
            print(f"away {_clock(ret[0])}-{_clock(ret[1])}: {out}", file=sys.stderr, flush=True)
        time.sleep(POLL)
