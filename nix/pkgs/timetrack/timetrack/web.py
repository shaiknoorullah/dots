"""Browser time per site and per ticket, from aw-watcher-web. Local only (D11).

This is ATTRIBUTION, not presence. A browser tab reports itself as current for as long as
it is the selected tab, whether or not anyone is looking at it -- five days of raw web
events summed to 97 hours. So a web second counts only where all three agree:

    web tab current  AND  the browser window has OS focus  AND  AW says not-afk

That makes web time a subset of presence that is already counted. It can say WHAT the
time went on (the ERP task, the PR, the docs); it can never add a minute to the headline.

Only the domain and a ticket id are kept, never the URL or the title, and incognito tabs
are dropped before anything is computed. Everything goes to private.db.
"""
import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from urllib.parse import urlsplit

from . import config, dbs, intervals
from .backfill import aw
from .binding import TICKET

BROWSER_APP = re.compile(r"^(zen|firefox)", re.IGNORECASE)
TICKET_RE = re.compile(r"\b(" + TICKET + r")\b")


def _ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone()


def _events(kind, t0, t1):
    name = aw.bucket(kind)
    q = f"?start={t0.isoformat()}&end={t1.isoformat()}&limit=100000"
    out = []
    for e in aw._get(f"/buckets/{name}/events{q.replace('+', '%2B')}"):
        s = _ts(e["timestamp"])
        out.append((s, s + timedelta(seconds=e.get("duration") or 0), e.get("data") or {}))
    return out


def domain(url):
    host = (urlsplit(url or "").hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def ticket(url):
    """A ticket id in the URL path only, from the allowed prefixes. Titles are ignored:
    they quote other tickets."""
    for m in TICKET_RE.finditer(urlsplit(url or "").path):
        if m.group(1).startswith(config.web_ticket_prefixes()):
            return m.group(1)
    return None


def segments(web, focus, present):
    """Clip each web event to browser-focus ∩ presence. Yields (start, end, data)."""
    gate = intervals.merge(focus)
    gate = [(max(s, ps), min(e, pe)) for s, e in gate for ps, pe in present
            if max(s, ps) < min(e, pe)]
    for s, e, d in web:
        if d.get("incognito"):
            continue
        for gs, ge in gate:
            lo, hi = max(s, gs), min(e, ge)
            if lo < hi:
                yield lo, hi, d


def day_totals(day, now=None):
    """({domain: seconds}, {ticket: seconds}) for one local day."""
    t0 = datetime.combine(day, time.min).astimezone()
    t1 = t0 + timedelta(days=1)
    web = _events("web.tab.current", t0, t1)
    focus = [(s, e) for s, e, d in _events("currentwindow", t0, t1)
             if BROWSER_APP.match(d.get("app") or "")]
    present = [(max(s, t0), min(e, t1)) for s, e in aw.not_afk(day, day, now)
               if max(s, t0) < min(e, t1)]
    by_dom, by_tkt = defaultdict(float), defaultdict(float)
    for s, e, d in segments(web, focus, present):
        sec = (e - s).total_seconds()
        if dom := domain(d.get("url")):
            by_dom[dom] += sec
        if t := ticket(d.get("url")):
            by_tkt[t] += sec
    return dict(by_dom), dict(by_tkt)


def write(day, now=None):
    """Recompute one day. Derived data, so a rerun replaces it rather than adding to it."""
    by_dom, by_tkt = day_totals(day, now)
    d = day.isoformat()
    with dbs.open_private() as conn:
        conn.execute("DELETE FROM web_domain_day WHERE day = ?", (d,))
        conn.execute("DELETE FROM web_ticket_day WHERE day = ?", (d,))
        conn.executemany("INSERT INTO web_domain_day(day, domain, seconds) VALUES(?,?,?)",
                         [(d, k, round(v)) for k, v in by_dom.items() if v >= 1])
        conn.executemany("INSERT INTO web_ticket_day(day, ticket, seconds) VALUES(?,?,?)",
                         [(d, k, round(v)) for k, v in by_tkt.items() if v >= 1])
    return by_dom, by_tkt


def first_day():
    """The oldest day the web bucket has anything for, or None."""
    events = aw._get(f"/buckets/{aw.bucket('web.tab.current')}/events?limit=100000")
    return min((_ts(e["timestamp"]).date() for e in events), default=None)


def days_since(start, end=None):
    end = end or date.today()
    while start <= end:
        yield start
        start += timedelta(days=1)
