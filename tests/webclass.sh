#!/usr/bin/env bash
# Browser visits labelled (webclass.py). What must hold: a visit attached to a task is work;
# mail alone is not; the model can only attach a task it was offered; wandering while an
# agent runs is "delegated", not "unproductive"; query strings and tokens never reach disk.
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$root/nix/pkgs/timetrack"
export TIMETRACK_STATE_DIR="$tmp/state" XDG_CONFIG_HOME="$tmp/config"
python3 - <<'PY'
import json
from datetime import date, datetime, timedelta

from timetrack import config, dbs, migrate, webclass

fails = []
def check(name, got, want):
    if got != want: fails.append(f"{name}: got {got!r}, want {want!r}")

CONF = {"work_domains": ["corp.example"], "work_github_orgs": ["acme"],
        "nonwork_domains": ["mail.example"]}
config.web = lambda: CONF

# 1. URL hygiene
check("query and fragment dropped", webclass.clean_url("https://x.example/a/b?code=SECRET#frag"),
      "https://x.example/a/b")
check("token redacted", webclass.redact("see ghp_abcdefghijklmnop here"), "see [redacted] here")

# 2. rules
v = lambda url, title="t": {"url": url, "title": title, "domain": __import__("timetrack.web", fromlist=["x"]).domain(url)}
r = webclass.rules(v("https://github.com/acme/infra/pull/812"), CONF)
check("work org PR", (r["work"], r["activity"], r["link"] is not None), (True, "PR #812 in infra", True))
r = webclass.rules(v("https://github.com/someone/dots/pull/3"), CONF)
check("other org PR not ruled work", r["work"], None)
check("org settings page is work", webclass.rules(v("https://github.com/organizations/acme/settings/security"), CONF)["work"], True)
check("enterprise page is work", webclass.rules(v("https://github.com/enterprises/ACME/settings"), CONF)["work"], True)
check("subdomain of work domain", webclass.rules(v("https://ci.corp.example/x"), CONF)["work"], True)
check("mail is not work", webclass.rules(v("https://mail.example/inbox"), CONF)["work"], False)
check("ticket in path", webclass.rules(v("https://corp.example/app/task/TASK-2026-00512"), CONF)["ticket"], "TASK-2026-00512")

# 3. verdict
check("work", webclass.verdict(True, 0), "work")
check("delegated", webclass.verdict(False, 0.6), "delegated")
check("unproductive", webclass.verdict(False, 0.2), "unproductive")
check("overlap", webclass._overlap(100, 200, [(50, 150), (180, 400)]), 70)

# 4. end to end with stubbed sources and a stubbed model
day = date(2026, 10, 27)
t = lambda h, m=0: datetime(2026, 10, 27, h, m).astimezone()
V = [{"start": t(14), "end": t(14, 20), "seconds": 1200, "url": "https://github.com/someone/blog/issues/1", "title": "Some blog", "domain": "github.com"},
     {"start": t(15), "end": t(15, 10), "seconds": 600, "url": "https://news.example/item", "title": "HN", "domain": "news.example"},
     {"start": t(16), "end": t(16, 10), "seconds": 600, "url": "https://news.example/other", "title": "HN 2", "domain": "news.example"},
     {"start": t(17), "end": t(17, 5), "seconds": 300, "url": "https://docs.example/k8s", "title": "k8s docs", "domain": "docs.example"}]
webclass.visits = lambda d, now=None: [dict(x) for x in V]
TASKS = [{"id": "aaaa1111", "uuid": "aaaa1111-0000", "desc": "fix ingress timeout", "refs": {"https://github.com/someone/blog/issues/1"}},
         {"id": "bbbb2222", "uuid": "bbbb2222-0000", "desc": "upgrade k8s", "refs": set()}]
webclass.open_tasks = lambda: TASKS
webclass.agent_spans = lambda d: [(int(t(16).timestamp()), int(t(16, 10).timestamp()))]
calls = []
def fake_llm(batch, tasks, model):
    calls.append(len(batch))
    out = {}
    for b in batch:
        if "news" in b["domain"]:
            out[b["k"]] = {"activity": "Reading Hacker News", "category": "news", "work": False, "task": None}
        elif "docs" in b["domain"]:
            out[b["k"]] = {"activity": "Reading k8s ingress docs", "category": "docs-reference", "work": True, "task": "bbbb2222"}
        else:
            out[b["k"]] = {"activity": "Issue #1", "category": "other", "work": False, "task": "zzzz9999"}
    return out
webclass.llm_label = fake_llm

migrate.migrate()
rows = webclass.classify(day)
by = {r[5]: r for r in rows}
check("code-matched task is work", by["https://github.com/someone/blog/issues/1"][13], "work")
check("model task outside offer ignored, code match kept", by["https://github.com/someone/blog/issues/1"][9], "aaaa1111-0000")
check("news, no agent: unproductive", by["https://news.example/item"][13], "unproductive")
check("news while agent ran: delegated", by["https://news.example/other"][13], "delegated")
check("model-attached task is work", (by["https://docs.example/k8s"][13], by["https://docs.example/k8s"][9]), ("work", "bbbb2222-0000"))
check("activity is the model's words", by["https://news.example/item"][10], "Reading Hacker News")

n = len(calls); webclass.classify(day)
check("labels cached: no second model call", len(calls), n)
conn = dbs.open_private(readonly=True)
check("rerun replaces, not appends", conn.execute("SELECT COUNT(*) FROM web_visit WHERE day='2026-10-27'").fetchone()[0], 4)

webclass.llm_label = lambda *a: {}
conn2 = dbs.open_private(); conn2.execute("DELETE FROM web_label_cache"); conn2.commit()
rows = webclass.classify(day, use_llm=True)
check("model down: rules still label", {r[15] for r in rows}, {"rule"})

if fails:
    print("webclass FAILED:")
    for f in fails: print("  " + f)
    raise SystemExit(1)
print("webclass ok (4 scenarios, 22 assertions)")
PY
