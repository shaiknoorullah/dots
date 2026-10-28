"""D21: at most 2 blocking prompts an hour and 8 a day.

Over budget a prompt degrades to a toast and stays queued: whatever it asked about is
still pending, and the next login or `timetrack backfill` asks again. Salah and away
gaps are exempt (away by the user's call, 2026-10-28) and never pass through here.

    if budget.take(kind):      # True: show the panel now (recorded as shown)
        ...
    else:                      # False: recorded as deferred; toast instead
        ...
"""
import time

from . import dbs

PER_HOUR = 2
PER_DAY = 8
EXEMPT = {"salah", "away"}


def used(conn, now):
    hour = conn.execute("SELECT COUNT(*) FROM prompt_log WHERE outcome = 'shown' AND ts_unix > ?",
                        (now - 3600,)).fetchone()[0]
    day = conn.execute("SELECT COUNT(*) FROM prompt_log WHERE outcome = 'shown' AND ts_unix > ?",
                       (now - 86400,)).fetchone()[0]
    return hour, day


def take(kind, now=None):
    """Spend one blocking prompt for `kind`, or record it as deferred."""
    if kind in EXEMPT:
        raise ValueError(f"{kind} prompts are exempt from the budget")
    now = int(now or time.time())
    with dbs.open_private() as conn:
        hour, day = used(conn, now)
        ok = hour < PER_HOUR and day < PER_DAY
        conn.execute("INSERT OR IGNORE INTO prompt_log(ts_unix, kind, outcome) VALUES(?,?,?)",
                     (now, kind, "shown" if ok else "deferred"))
        conn.commit()
    return ok
