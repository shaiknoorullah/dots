"""Break down & start, the last step: an agent in its own herdr tab, briefing me on the ticket.

Every dispatched agent lands in one herdr workspace, the Hangar (created on first use),
one tab per ticket, labelled with the ref, in the repo's checkout when there is one. Claude starts there with DOTS_TASK_DECLARE set, so the
SessionStart hook declares the started step before the agent can do anything (the task gate
never stops it). Its first prompt asks for a READ-ONLY gathering pass and a brief, then a
stop: it posts, comments, replies and changes nothing outside this machine.

work.conf (unmanaged) may override:
  DISPATCH_WS=<workspace label>                        (default: Hangar)
  AGENT_CWD=<dir for tickets with no repo checkout>   (default: home)
  WORK_CHECKOUTS=<dir holding repo checkouts>          (default: ~/work)
A second click on a ref whose step already has a live agent focuses that agent instead of
starting another.
"""
import json
import os
import re
from pathlib import Path

from . import config, herdr, names

GH_REF = re.compile(r"^gh:([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)#(\d+)$")
REPO = re.compile(r"^(?!\.+$)[A-Za-z0-9_.-]{1,100}$")     # never "." or ".."


def _json(out):
    try:
        return json.loads(out)["result"]
    except (TypeError, ValueError, KeyError):
        return None


HANGAR = "Hangar"


def hangar_id(cfg, snap):
    """workspace_id of the dispatch workspace, or None when it does not exist yet."""
    want = cfg.raw.get("DISPATCH_WS") or HANGAR
    return next((w["workspace_id"] for w in snap.get("workspaces") or [] if w.get("label") == want), None)


def cwd_for(ref, cfg):
    m = GH_REF.match(ref or "")
    root = Path(os.path.expanduser(cfg.raw.get("WORK_CHECKOUTS") or "~/work"))
    if m and REPO.match(m.group(2)) and (root / m.group(2)).is_dir():
        return str(root / m.group(2))
    return os.path.expanduser(cfg.raw.get("AGENT_CWD") or "~")


def prompt_for(parent, steps, current):
    ref, title, url = parent.get("ref"), parent.get("desc", ""), parent.get("url") or ""
    src = parent.get("src")
    how = []
    if src == "gh":
        how.append(f"- GitHub: `gh issue view {url} --comments` or `gh pr view {url} --comments`, plus linked "
                   "issues and PRs and the recent history of the files involved.")
    if src == "erp":
        how.append(f"- ERP: read {ref} and its comments and linked documents with the ERP read tools only "
                   "(get_document, query_documents). Never create, update, comment on, submit or cancel anything there.")
    how.append(f"- Local: `task parentref:{ref} list`, and this checkout if it is the repo.")
    plan = "\n".join(f"  {'>' if s == current else '-'} {s}" for s in steps)
    return (f"You are starting on {ref}: {title}\n"
            f"{url}\n\n"
            f"The task is already declared for this session (`dots-task current`). Steps:\n{plan}\n\n"
            "First, gather everything about this ticket. READ ONLY:\n" + "\n".join(how) + "\n\n"
            "Do not post, comment, reply, push or change anything outside this machine, and never answer "
            "anyone on my behalf. Then give me a short brief: what it is, its current state, who is waiting "
            "on what, open questions, and a proposed plan for the current step (>). Then stop and wait for me.")


def live_agent_for(step_uuid, snap, task):
    """The pane of a live agent already working this step, if any."""
    pane = task.get("pane") if task else None
    if not pane:
        return None
    for p in snap.get("panes") or []:
        if p.get("pane_id") == pane and p.get("agent"):
            return p
    return None


def launch(parent, steps, current, step_task, cfg=None, run=None):
    """Open the tab, start Claude, send the brief prompt. Returns {'pane', 'tab', 'reused'} or
    {'error': ...}. `run` is the herdr seam tests replace."""
    cfg = cfg or config.Work()
    run = run or herdr.run
    snap = _json(run(["api", "snapshot"]))
    snap = (snap or {}).get("snapshot") or {}
    live = live_agent_for(step_task.get("uuid"), snap, step_task)
    if live:
        run(["workspace", "focus", live.get("workspace_id")])
        run(["tab", "focus", live.get("tab_id")])
        return {"pane": live.get("pane_id"), "tab": live.get("tab_id"), "reused": True}
    label = re.sub(r"[^A-Za-z0-9#:/._-]", "", (parent.get("ref") or "task").split("/")[-1])[:32] or "task"
    common = ["--cwd", cwd_for(parent.get("ref"), cfg), "--env", f"DOTS_TASK_DECLARE={step_task['uuid']}", "--focus"]
    ws = hangar_id(cfg, snap)
    if ws:
        made = _json(run(["tab", "create", "--workspace", ws, "--label", label] + common))
    else:                       # first dispatch: the Hangar's own first tab is this ticket's
        made = _json(run(["workspace", "create", "--label", cfg.raw.get("DISPATCH_WS") or HANGAR] + common))
    pane = ((made or {}).get("root_pane") or {}).get("pane_id")
    if not pane:
        return {"error": "herdr did not create a tab"}
    tab = ((made or {}).get("tab") or {}).get("tab_id") or ((made or {}).get("root_pane") or {}).get("tab_id")
    if not ws and tab:
        run(["tab", "rename", tab, label])
    name = names.for_session(step_task["uuid"])
    if run(["agent", "start", name, "--kind", "claude", "--pane", pane, "--timeout", "60000"], timeout=70) is None:
        return {"error": f"claude did not start in {pane}", "pane": pane}
    if run(["agent", "prompt", pane, prompt_for(parent, steps, current)], timeout=15) is None:
        return {"error": f"the brief prompt was not delivered to {pane}", "pane": pane}
    return {"pane": pane, "tab": tab, "reused": False}
