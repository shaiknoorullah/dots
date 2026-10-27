"""What each browser visit actually was, whether it was work, and what it belonged to.

web.py answers "how long on which domain". That reads as noise: forty minutes of
github.com says nothing about which PR. This module keeps each VISIT (the same page,
focused and present, merged across short gaps) and labels it:

    activity   what was being done, specific ("Reviewing PR #812: fix ingress timeout")
    category   code-review | coding | ticket-work | infra-ops | docs-reference | learning |
               reading-blog | communication | email | admin | entertainment | social |
               shopping | news | other
    link       the ticket / PR / issue it belongs to, when there is one
    verdict    work | delegated | unproductive

Two passes, code first. Rules are cheap and exact: a ticket id in the path, a PR or issue
URL, a configured work domain or GitHub org. The local model (Ollama on loopback, never
anything remote) then names the activity, picks the category and may match the visit to
one of the open Taskwarrior tasks -- a match is accepted only if it names a task that was
actually offered. Labels are cached per page, so a page is asked about once.

The verdict rule is the user's: a visit is WORK if it is attached to a task or is part of
the job. Otherwise it is UNPRODUCTIVE -- unless an agent was running for at least half of
it, which makes it DELEGATED: wandering off is fine while the agents carry the work. Mail
on its own is not task work. Everything here lives in private.db and is shown only on the
user's own dashboards; nothing of it reaches a submission.

URLs are stored without query string or fragment (those carry tokens and search terms);
titles are passed through the same token redaction as everything else.
"""
import hashlib
import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, time as dtime, timedelta
from urllib.parse import urlsplit, urlunsplit

from . import config, dbs
from . import web
from .binding import TICKET
from .backfill import aw

OLLAMA = "http://127.0.0.1:11434/api/chat"
DEFAULT_MODEL = "qwen3-coder:30b"
MERGE_GAP = 60          # seconds: the same page re-focused within this is one visit
MIN_VISIT = 10          # seconds: shorter is a tab flicked past, not a visit
BATCH = 12              # pages per model call
DELEGATED_SHARE = 0.5   # agent running for at least this share of a visit
WORK_CATS = {"code-review", "coding", "ticket-work", "infra-ops", "docs-reference"}
NONWORK_CATS = {"email", "communication", "entertainment", "social", "shopping", "news"}
CATS = WORK_CATS | NONWORK_CATS | {"learning", "reading-blog", "admin", "other"}
GH_ITEM = re.compile(r"^/([^/]+)/([^/]+)/(pull|issues)/(\d+)")
SECRET = re.compile(r"(ghp_|gho_|ghs_|github_pat_|xox[abprs]-|sk-|AKIA)[\w-]{6,}|Bearer\s+\S+")


def clean_url(url):
    """Drop query and fragment: they carry tokens, codes and search terms."""
    p = urlsplit(url or "")
    return urlunsplit((p.scheme, p.netloc, p.path, "", ""))


def redact(s):
    return SECRET.sub("[redacted]", s or "")


def visits(day, now=None):
    """Focused, present browser time for one day, merged into visits of one page each."""
    t0 = datetime.combine(day, dtime.min).astimezone()
    t1 = t0 + timedelta(days=1)
    w = web._events("web.tab.current", t0, t1)
    focus = [(s, e) for s, e, d in web._events("currentwindow", t0, t1)
             if web.BROWSER_APP.match(d.get("app") or "")]
    present = [(max(s, t0), min(e, t1)) for s, e in aw.not_afk(day, day, now)
               if max(s, t0) < min(e, t1)]
    out = []
    for s, e, d in sorted(web.segments(w, focus, present), key=lambda x: x[0]):
        url = clean_url(d.get("url"))
        title = redact(d.get("title"))[:200]
        if out and out[-1]["url"] == url and (s - out[-1]["end"]).total_seconds() <= MERGE_GAP:
            out[-1]["end"] = max(out[-1]["end"], e)
            out[-1]["seconds"] += (e - s).total_seconds()
            continue
        out.append({"start": s, "end": e, "seconds": (e - s).total_seconds(),
                    "url": url, "title": title, "domain": web.domain(url)})
    return [v for v in out if v["seconds"] >= MIN_VISIT]


def rules(v, conf):
    """Exact, cheap facts from the URL alone."""
    r = {"ticket": web.ticket(v["url"]), "link": None, "work": None, "activity": None}
    if r["ticket"]:
        r["link"], r["work"] = v["url"], True
    if v["domain"] == "github.com":
        path = urlsplit(v["url"]).path
        segs = path.strip("/").split("/")
        # repo pages are /<owner>/...; org and enterprise settings are /organizations/<org>/...
        owner = segs[1] if len(segs) > 1 and segs[0] in ("organizations", "enterprises", "orgs") else segs[0]
        if owner.lower() in {o.lower() for o in conf.get("work_github_orgs", [])}:
            r["work"] = True
        m = GH_ITEM.match(path)
        if m:
            _, repo, kind, num = m.groups()
            r["link"] = v["url"]
            r["activity"] = f"{'PR' if kind == 'pull' else 'Issue'} #{num} in {repo}"
    if _in(v["domain"], conf.get("nonwork_domains", [])):
        r["work"] = False if r["work"] is None else r["work"]
    elif _in(v["domain"], conf.get("work_domains", [])) and r["work"] is None:
        r["work"] = True
    return r


def _in(domain, listed):
    """Exact or subdomain match: "example.cloud" covers "ci.example.cloud"."""
    return any(domain == d or domain.endswith("." + d) for d in listed)


def open_tasks():
    """Pending Taskwarrior tasks as [{id, desc, refs}] -- refs are any URL/ticket values."""
    try:
        out = subprocess.run(["task", "rc.hooks=off", "status:pending", "export"],
                             capture_output=True, text=True, timeout=20).stdout
        rows = json.loads(out or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    tasks = []
    for t in rows[:60]:
        refs = {str(v) for k, v in t.items()
                if isinstance(v, str) and (v.startswith("http") or re.fullmatch(TICKET, v))}
        tasks.append({"id": t["uuid"][:8], "uuid": t["uuid"], "desc": t.get("description", "")[:120], "refs": refs})
    return tasks


def match_task(v, r, tasks):
    """Code match: the visit's URL or ticket is one of a task's references."""
    for t in tasks:
        if v["url"] in t["refs"] or (r["ticket"] and (r["ticket"] in t["refs"] or r["ticket"] in t["desc"])):
            return t
    return None


def _key(v):
    return hashlib.sha1(f"{v['domain']}|{v['url']}|{v['title']}".encode()).hexdigest()[:16]


SYSTEM = (
    "You label browser visits for one person's private productivity log. The person is a "
    "DevOps / platform engineer. For EACH item return an object with: "
    "k (copy the item's k), activity (max 10 words, specific and concrete: what was being "
    "done on that page, naming the PR, ticket, doc topic or article; never just the site name), "
    "category (exactly one of: " + ", ".join(sorted(CATS)) + "), "
    "work_related (true only if this is plausibly part of the job), "
    "task (the id of the TASK this visit is clearly about, or null; only ids from the TASKS list). "
    'Reply with JSON only: {"items": [ ... ]}'
)


def llm_label(batch, tasks, model):
    """{k: label} from the local model, or {} if it is unavailable. Loopback only."""
    payload = {
        "model": model, "stream": False, "format": "json", "think": False,
        "options": {"temperature": 0},
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps({
                "TASKS": [{"id": t["id"], "desc": t["desc"]} for t in tasks],
                "ITEMS": [{"k": b["k"], "domain": b["domain"], "path": urlsplit(b["url"]).path[:160],
                           "title": b["title"]} for b in batch]})},
        ],
    }
    req = urllib.request.Request(OLLAMA, json.dumps(payload).encode(),
                                 {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as fh:
            body = json.loads(fh.read())
        items = json.loads(body["message"]["content"]).get("items", [])
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
        return {}
    ids = {t["id"] for t in tasks}
    out = {}
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict) or it.get("k") not in {b["k"] for b in batch}:
            continue
        cat = it.get("category") if it.get("category") in CATS else "other"
        out[it["k"]] = {"activity": redact(str(it.get("activity") or ""))[:120],
                        "category": cat, "work": bool(it.get("work_related")),
                        "task": it.get("task") if it.get("task") in ids else None}
    return out


def agent_spans(day):
    """[(start, end)] unix spans where any agent run was open, from clockd's live events."""
    t0 = int(datetime.combine(day, dtime.min).astimezone().timestamp())
    t1 = t0 + 86400 + 6 * 3600
    with dbs.open_work(readonly=True) as conn:
        rows = conn.execute(
            "SELECT actor_ref, kind, ts_unix FROM clock_event WHERE actor = 'agent'"
            " AND kind IN ('run_start','run_stop') AND ts_unix BETWEEN ? AND ?"
            " ORDER BY ts_unix", (t0 - 6 * 3600, t1)).fetchall()
    open_, spans = {}, []
    for ref, kind, ts in rows:
        if kind == "run_start":
            open_.setdefault(ref, ts)
        elif ref in open_:
            spans.append((open_.pop(ref), ts))
    now = int(time.time())
    spans += [(s, now) for s in open_.values()]
    return spans


def _overlap(a0, a1, spans):
    merged = sorted(spans)
    total, cur = 0, a0
    for s, e in merged:
        lo, hi = max(s, cur), min(e, a1)
        if lo < hi:
            total += hi - lo
            cur = hi
    return total


def verdict(work, agent_share):
    if work:
        return "work"
    return "delegated" if agent_share >= DELEGATED_SHARE else "unproductive"


def classify(day, now=None, use_llm=True):
    """Label every visit of `day`. Returns the rows written."""
    conf = config.web()
    model = conf.get("model", DEFAULT_MODEL)
    vs = visits(day, now)
    tasks = open_tasks()
    agents = agent_spans(day)
    for v in vs:
        v["k"] = _key(v)
        v["r"] = rules(v, conf)

    with dbs.open_private() as conn:
        cache = {row[0]: json.loads(row[1]) for row in conn.execute(
            "SELECT key, label FROM web_label_cache WHERE key IN (%s)" % ",".join("?" * len(vs)),
            [v["k"] for v in vs])} if vs else {}
        todo = list({v["k"]: v for v in vs if v["k"] not in cache}.values())
        if use_llm:
            for i in range(0, len(todo), BATCH):
                got = llm_label(todo[i:i + BATCH], tasks, model)
                for k, lab in got.items():
                    lab["model"] = model
                    cache[k] = lab
                    conn.execute("INSERT OR REPLACE INTO web_label_cache(key, label, created_unix)"
                                 " VALUES(?,?,?)", (k, json.dumps(lab), int(time.time())))
                conn.commit()

        rows = []
        by_id = {t["id"]: t for t in tasks}
        for v in vs:
            r, lab = v["r"], cache.get(v["k"], {})
            task = match_task(v, r, tasks) or by_id.get(lab.get("task"))
            cat = lab.get("category") or ("ticket-work" if r["ticket"] else "other")
            if r["work"] is not None:
                work = r["work"]
            elif task:
                work = True
            elif cat in NONWORK_CATS:
                work = False
            else:
                work = bool(lab.get("work")) and cat in WORK_CATS
            if task:                       # attached to a task always counts as work
                work = True
            a0, a1 = int(v["start"].timestamp()), int(v["end"].timestamp())
            share = _overlap(a0, a1, agents) / max(1, a1 - a0)
            rows.append((day.isoformat(), a0, a1, round(v["seconds"]), v["domain"], v["url"],
                         v["title"], r["ticket"], r["link"] or (v["url"] if task else None),
                         task["uuid"] if task else None,
                         lab.get("activity") or r["activity"] or v["title"] or v["domain"], cat,
                         int(work), verdict(work, share), round(share, 2),
                         "rule+llm" if lab else "rule", lab.get("model")))
        conn.execute("DELETE FROM web_visit WHERE day = ?", (day.isoformat(),))
        conn.executemany(
            "INSERT INTO web_visit(day, start_unix, end_unix, seconds, domain, url, title, ticket,"
            " link, task_uuid, activity, category, work, verdict, agent_share, labelled_by, model)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
    return rows
