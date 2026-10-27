"""Completion detectors: READ the source, then mark the task done LOCALLY.

The truth tables below are pure functions of a recorded `gh`/ERP payload, so they are
tested from fixtures with no network. `run` is the thin shell that fetches, decides, and
writes in the spec's order: srcstate first, then `task done`, then mirror.json.

A detector never posts, comments, closes or merges. A source it cannot reach leaves the
task alone and turns that source "stale"; unreachable is never read as "done".
"""
import calendar
import time

from . import sources_erp, sources_gh, tw


def issue_done(v):
    return (v.get("state") or "").upper() == "CLOSED"


def pr_done(v):
    return bool(v.get("mergedAt")) or (v.get("state") or "").upper() in ("MERGED", "CLOSED")


def review_done(v, me, entry_ts):
    """Done when I submitted a review after the task appeared, or I am no longer asked and it is open.

    A merged or closed PR is done too: nobody is waiting on a review of it any more.
    """
    if pr_done(v):
        return True
    me = (me or "").lower()
    for r in v.get("reviews") or []:
        if str((r.get("author") or {}).get("login", "")).lower() == me and _iso(r.get("submittedAt")) > (entry_ts or 0):
            return True
    asked = {str(x.get("login", "")).lower() for x in v.get("reviewRequests") or [] if x.get("login")}
    return me not in asked


def erp_done(kind, status):
    return status in sources_erp.CLOSED.get(kind, ())


def _iso(s):
    if not s:
        return 0
    try:
        return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return 0


def split_state(srcstate):
    """'review:open' -> ('review', 'open'); a bare state has no kind."""
    k, sep, st = (srcstate or "").partition(":")
    return (k, st) if sep else (None, k)


def run(cfg, now=None, gh=sources_gh, erp=sources_erp):
    """Check every pending mirror task. Returns (closed refs, per-source state dict)."""
    now = int(now or time.time())
    closed, status = [], {}
    me = None
    for t in tw.export("status:pending", "+mirror"):
        ref, src = t.get("ref"), t.get("src")
        kind, _ = split_state(t.get("srcstate"))
        try:
            if src == "gh" and kind in ("issue", "pr", "review"):
                parsed = gh.parse_ref(ref)
                if not parsed:
                    continue
                v = gh.view(kind, *parsed)
                if kind == "issue":
                    fin = issue_done(v)
                elif kind == "pr":
                    fin = pr_done(v)
                else:
                    me = me or gh.whoami()
                    fin = review_done(v, me, t.get("entry_ts"))
                state = (v.get("state") or "open").lower()
                if kind == "review" and fin and state == "open":
                    state = "reviewed"
                status["gh"] = {"state": "ok", "ts": now, "detail": None}
            elif src == "erp" and kind in ("todo", "hd", "task") and cfg.erp_mode == "token":
                st = erp.view_status(cfg, kind, ref)
                if st is None:
                    continue
                fin, state = erp_done(kind, st), st.lower()
                status["erp"] = {"state": "ok", "ts": now, "detail": None}
            else:
                continue                      # claude-session tasks are never auto-done
        except (sources_gh.GhError, sources_erp.ErpError, OSError) as e:
            name = "gh" if src == "gh" else "erp"
            status[name] = {"state": "stale", "ts": now, "detail": str(e)[:80]}
            continue
        if fin:
            tw.done(t["uuid"], srcstate=f"{kind}:{state}", srcsync=now)
            closed.append(ref)
        elif t.get("srcstate") != f"{kind}:{state}":
            tw.modify(t["uuid"], srcstate=f"{kind}:{state}", srcsync=now)
    return closed, status
