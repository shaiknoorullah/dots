"""Names, detectors, sources, breakdown validation, work filter, notification log, ingest."""
import collections
import hashlib
import json
import os
import re
import stat
import subprocess
import time
import unittest
from pathlib import Path

import helpers
from dots_overview import (atomic, breakdown, config, detect, ingest, names, notifylog, sources_erp,
                           sources_gh, sync, tw)

PKG = Path(helpers.HERE.parent / "dots_overview")


class Names(unittest.TestCase):
    def test_256_unique_words(self):
        self.assertEqual(len(names.NAMES), 256)
        self.assertEqual(len(set(names.NAMES)), 256)

    def test_deterministic(self):
        sid = "f17184d7-e90f-438f-95d2-e0eb41ff5a2c"
        self.assertEqual(names.for_session(sid), names.for_session(sid))
        self.assertEqual(names.for_session(None), names.for_session(""))

    def test_collision_rate_is_flat(self):
        n = 5120
        c = collections.Counter(names.for_session(hashlib.sha256(str(i).encode()).hexdigest()) for i in range(n))
        self.assertEqual(len(c), 256)                   # every word is reachable
        self.assertLess(max(c.values()), 20 * 3)        # mean 20; nowhere near 3x
        # two sessions open at once: expected collision 1/256
        pairs = sum(1 for i in range(0, n - 1, 2)
                    if names.for_session(str(i)) == names.for_session(str(i + 1)))
        self.assertLess(pairs / (n // 2), 0.02)


class Truth(unittest.TestCase):
    """detector truth table, from recorded gh payloads"""

    def test_issue(self):
        self.assertTrue(detect.issue_done({"state": "CLOSED", "closedAt": "2026-10-01T00:00:00Z"}))
        self.assertFalse(detect.issue_done({"state": "OPEN"}))

    def test_authored_pr(self):
        self.assertTrue(detect.pr_done({"state": "MERGED", "mergedAt": "2026-10-01T00:00:00Z"}))
        self.assertTrue(detect.pr_done({"state": "CLOSED", "mergedAt": None}))
        self.assertTrue(detect.pr_done({"state": "OPEN", "mergedAt": "2026-10-01T00:00:00Z"}))
        self.assertFalse(detect.pr_done({"state": "OPEN", "mergedAt": None}))

    def test_review_requested(self):
        entry = 1_790_000_000
        asked = [{"login": "me"}]
        # still asked, no review: not done
        self.assertFalse(detect.review_done({"state": "OPEN", "reviewRequests": asked, "reviews": []}, "me", entry))
        # I submitted a review after the task appeared
        rev = [{"author": {"login": "Me"}, "submittedAt": "2026-10-28T00:00:00Z"}]
        self.assertTrue(detect.review_done({"state": "OPEN", "reviewRequests": asked, "reviews": rev}, "me", entry))
        # my review is OLDER than the task: not done
        old = [{"author": {"login": "me"}, "submittedAt": "2020-01-01T00:00:00Z"}]
        self.assertFalse(detect.review_done({"state": "OPEN", "reviewRequests": asked, "reviews": old}, "me", entry))
        # somebody else's review does not count
        other = [{"author": {"login": "bob"}, "submittedAt": "2026-10-28T00:00:00Z"}]
        self.assertFalse(detect.review_done({"state": "OPEN", "reviewRequests": asked, "reviews": other}, "me", entry))
        # no longer asked (a team request lacks a login) and still open: done
        self.assertTrue(detect.review_done({"state": "OPEN", "reviewRequests": [{"name": "team"}], "reviews": []}, "me", entry))
        # merged or closed: nobody waits on a review of it
        self.assertTrue(detect.review_done({"state": "MERGED", "mergedAt": "2026-10-01T00:00:00Z", "reviewRequests": asked}, "me", entry))

    def test_erp(self):
        self.assertTrue(detect.erp_done("todo", "Closed"))
        self.assertTrue(detect.erp_done("todo", "Cancelled"))
        self.assertFalse(detect.erp_done("todo", "Open"))
        self.assertTrue(detect.erp_done("hd", "Resolved"))
        self.assertTrue(detect.erp_done("hd", "Closed"))
        self.assertFalse(detect.erp_done("hd", "Replied"))
        self.assertTrue(detect.erp_done("task", "Completed"))
        self.assertTrue(detect.erp_done("task", "Cancelled"))
        self.assertFalse(detect.erp_done("task", "Working"))


class FakeGh:
    GhError = sources_gh.GhError

    def __init__(self, views, me="me", fail=False):
        self.views, self.me, self.fail, self.calls = views, me, fail, []

    parse_ref = staticmethod(sources_gh.parse_ref)

    def view(self, kind, repo, n):
        self.calls.append((kind, repo, n))
        if self.fail:
            raise sources_gh.GhError("network down")
        return self.views[(kind, repo, n)]

    def whoami(self):
        return self.me


class Detectors(helpers.Scratch):
    def setUp(self):
        super().setUp()
        self.tw = helpers.FakeTw(self)
        self.cfg = config.Work({})

    def mirror(self, ref, state, **kw):
        return self.tw.seed(ref=ref, src="gh", srcstate=state, tags=["mirror"], **kw)

    def test_closes_issue_and_merged_pr_and_records_srcstate_first(self):
        i = self.mirror("gh:acme/r#1", "issue:open")
        p = self.mirror("gh:acme/r#2", "pr:open")
        k = self.mirror("gh:acme/r#3", "pr:open")
        gh = FakeGh({("issue", "acme/r", 1): {"state": "CLOSED"},
                     ("pr", "acme/r", 2): {"state": "MERGED", "mergedAt": "2026-10-01T00:00:00Z"},
                     ("pr", "acme/r", 3): {"state": "OPEN", "mergedAt": None}})
        closed, status = detect.run(self.cfg, gh=gh)
        self.assertEqual(sorted(closed), ["gh:acme/r#1", "gh:acme/r#2"])
        self.assertEqual((i["status"], p["status"], k["status"]), ("completed", "completed", "pending"))
        self.assertEqual(i["srcstate"], "issue:closed")
        self.assertEqual(p["srcstate"], "pr:merged")
        # order: modify(srcstate) strictly before done
        ops = [(o[0], o[1]) for o in self.tw.log if o[1] == i["uuid"]]
        self.assertLess(ops.index(("modify", i["uuid"])), ops.index(("done", i["uuid"])))

    def test_unreachable_source_leaves_task_alone_and_marks_stale(self):
        t = self.mirror("gh:acme/r#9", "issue:open")
        closed, status = detect.run(self.cfg, gh=FakeGh({}, fail=True))
        self.assertEqual(closed, [])
        self.assertEqual(t["status"], "pending")
        self.assertEqual(status["gh"]["state"], "stale")

    def test_review_submitted_is_recorded_as_reviewed(self):
        t = self.mirror("gh:acme/r#5", "review:open", entry_ts=1_000)
        rev = [{"author": {"login": "me"}, "submittedAt": "2026-10-28T00:00:00Z"}]
        detect.run(self.cfg, gh=FakeGh({("review", "acme/r", 5): {"state": "OPEN", "reviewRequests": [{"login": "me"}], "reviews": rev}}))
        self.assertEqual((t["status"], t["srcstate"]), ("completed", "review:reviewed"))

    def test_claude_session_tasks_are_never_auto_done(self):
        t = self.tw.seed(src="claude", sess="s", tags=["mirror", "attn"])
        gh = FakeGh({})
        detect.run(self.cfg, gh=gh)
        self.assertEqual((t["status"], gh.calls), ("pending", []))

    def test_erp_off_does_not_touch_erp_tasks(self):
        t = self.tw.seed(ref="HD-1", src="erp", srcstate="hd:open", tags=["mirror"])
        detect.run(self.cfg, gh=FakeGh({}))
        self.assertEqual(t["status"], "pending")


class Sources(helpers.Scratch):
    def setUp(self):
        super().setUp()
        self.tw = helpers.FakeTw(self)

    def test_gh_search_budget_and_repo_comes_from_json(self):
        calls, sleeps = [], []

        def run(args, timeout=30):
            calls.append(args)
            if args[:2] == ["api", "rate_limit"]:
                return "30\n"
            row = {"number": 7, "title": "Fix", "url": "https://x/7", "state": "open", "repository": {"nameWithOwner": "acme/r"},
                   "author": {"login": "bob"}, "updatedAt": "2026-10-28T00:00:00Z"}
            return json.dumps([row])
        real = sources_gh.run
        sources_gh.run = run
        self.addCleanup(setattr, sources_gh, "run", real)
        cfg = config.Work({"WORK_GH_ORG": "acme"})
        items = sources_gh.fetch(cfg, sleep=sleeps.append)
        searches = [c for c in calls if c[0] == "search"]
        self.assertLessEqual(len(searches), sources_gh.MAX_SEARCH)
        self.assertEqual(len(sleeps), len(searches) - 1)
        self.assertTrue(all(s == sources_gh.SLEEP for s in sleeps))
        self.assertTrue(all(i["ref"] == "gh:acme/r#7" and i["work"] for i in items))
        self.assertEqual({i["kind"] for i in items}, {"issue", "pr", "review"})

    def test_gh_quota_low_aborts_before_any_search(self):
        calls = []

        def run(args, timeout=30):
            calls.append(args)
            return "3\n"
        real = sources_gh.run
        sources_gh.run = run
        self.addCleanup(setattr, sources_gh, "run", real)
        with self.assertRaises(sources_gh.GhError):
            sources_gh.fetch(config.Work({}), sleep=lambda s: None)
        self.assertTrue(all(c[0] == "api" for c in calls))

    def test_sync_is_idempotent_and_personal_repos_get_no_work_tag(self):
        items = [{"ref": "gh:acme/a#1", "src": "gh", "kind": "review", "repo": "acme/a", "url": "u", "title": "A", "state": "open", "work": True},
                 {"ref": "gh:me/b#2", "src": "gh", "kind": "issue", "repo": "me/b", "url": "u", "title": "B", "state": "open", "work": False}]

        class Gh:
            GhError = sources_gh.GhError

            @staticmethod
            def fetch(cfg, sleep=None):
                return items
        cfg = config.Work({})
        sync.run(cfg, gh=Gh, sleep=lambda s: None)
        sync.run(cfg, gh=Gh, sleep=lambda s: None)
        self.assertEqual(len(self.tw.tasks), 2)
        a, b = self.tw.tasks
        self.assertEqual((a["tags"], b["tags"]), (["mirror", "work", "attn"], ["mirror"]))
        self.assertEqual(a["srcstate"], "review:open")
        doc = sync.mirror_doc(int(time.time()))
        self.assertEqual(sorted(r["ref"] for r in doc["refs"]), ["gh:acme/a#1", "gh:me/b#2"])

    def test_erp_is_off_by_default_and_never_calls_out(self):
        cfg = config.Work({})
        self.assertEqual(sources_erp.state_for(cfg)[0], "off")
        calls = []
        real = sync.sources_gh.fetch
        sync.sources_gh.fetch = lambda cfg, sleep=None: []
        self.addCleanup(setattr, sync.sources_gh, "fetch", real)
        _, status, _ = sync.run(cfg, sleep=lambda s: None)
        self.assertEqual(status["erp"]["state"], "off")
        self.assertEqual(calls, [])

    def test_erp_mcp_needs_explicit_flag(self):
        cfg = config.Work({"ERP_MODE": "mcp"})
        self.assertEqual(sources_erp.state_for(cfg, mcp_ok=False)[0], "off")
        self.assertEqual(sources_erp.state_for(cfg, mcp_ok=True)[0], "ready")

    def test_erp_token_is_get_only_and_key_never_leaks(self):
        key = self.root / "erp.key"
        key.write_text("abc123:s3cr3tvalue")
        os.chmod(key, 0o644)
        cfg = config.Work({"ERP_MODE": "token", "ERP_BASE_URL": "https://erp.example.com", "ERP_KEY_FILE": str(key),
                           "ERP_DOCTYPES": "HD Ticket"})
        with self.assertRaises(sources_erp.ErpError):
            sources_erp.fetch_token(cfg, opener=lambda *a, **k: None)      # group/other readable: refused
        os.chmod(key, 0o600)
        seen = []

        class Resp:
            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps(self.body).encode()

        def opener(req, timeout=0):
            seen.append((req.get_method(), req.full_url, req.get_header("Authorization")))
            if "get_logged_user" in req.full_url:
                return Resp({"message": "me@example.com"})
            return Resp({"data": [{"name": "HD-123", "subject": "Broken (form) <b>x</b>", "status": "Open", "modified": "2026-10-28"}]})
        items = sources_erp.fetch_token(cfg, opener=opener)
        self.assertEqual({m for m, _, _ in seen}, {"GET"})
        self.assertTrue(all(a == "token abc123:s3cr3tvalue" for _, _, a in seen))
        self.assertTrue(all("s3cr3t" not in u for _, u, _ in seen))
        self.assertEqual(items[0]["ref"], "HD-123")
        self.assertNotIn("<", items[0]["title"])

        def boom(req, timeout=0):
            raise OSError("down")
        with self.assertRaises(sources_erp.ErpError) as cm:
            sources_erp.fetch_token(cfg, opener=boom)
        self.assertNotIn("s3cr3t", str(cm.exception))

    def test_erp_requires_https(self):
        key = self.root / "k"
        key.write_text("a:b")
        os.chmod(key, 0o600)
        cfg = config.Work({"ERP_MODE": "token", "ERP_BASE_URL": "http://erp.example.com", "ERP_KEY_FILE": str(key)})
        with self.assertRaises(sources_erp.ErpError):
            sources_erp.get(cfg, "/x", None, opener=lambda *a, **k: None)


class Hostile(helpers.Scratch):
    def test_validator_accepts_a_clean_answer(self):
        out = breakdown.validate('Sure!\n["Read the failing test", "Reproduce locally", "Write the fix"]')
        self.assertEqual(out, ["Read the failing test", "Reproduce locally", "Write the fix"])

    def test_shell_metacharacters_are_stripped(self):
        out = breakdown.validate(json.dumps(["Run $(rm -rf ~) now ok", "Then `id`; echo pwned > /etc/x", "Third step is fine"]))
        for s in out:      # what survives is inert text: no expansion, quoting, chaining or redirection
            self.assertIsNone(re.search(r"[$`;|&<>\\\"'*{}\[\]!?~=+@%^\n]", s), s)
        self.assertEqual(out[0], "Run (rm -rf ) now ok")

    def test_hostile_shapes_fall_back(self):
        for raw in ["", "no json here", "[]", '["only one step here"]', "[1, 2, 3]", '{"a": 1}',
                    json.dumps(["x" * 500, "y" * 500]), json.dumps(["short", "tiny"]),
                    json.dumps(["valid step number one"] * 500), "[unclosed", json.dumps([["nested", "list"], "ok step here"]),
                    json.dumps(["ok step number one", None])]:
            self.assertIsNone(breakdown.validate(raw), raw[:40])

    def test_fallback_is_one_clean_subtask(self):
        fb = breakdown.fallback("Fix $(curl evil) the form\nvalidation +tag ref:x", "HD-1")
        self.assertEqual(len(fb), 1)
        self.assertTrue(8 <= len(fb[0]) <= 80)
        self.assertEqual(breakdown.fallback("ab", "HD-1"), ["Work on HD-1"])

    def test_run_creates_subtasks_with_argv_only_and_starts_first(self):
        t = helpers.FakeTw(self)
        t.seed(desc="Parent ticket", ref="HD-5", src="erp", url="https://erp.example.com/app/hd-ticket/HD-5", project="work")
        opened = []
        model = lambda title: json.dumps(["Read the ticket body", "Reproduce the problem", "Ship a fix"])  # noqa: E731
        r = breakdown.run("HD-5", start=True, model=model, opener=lambda argv, **k: opened.append(argv))
        self.assertFalse(r["fallback"])
        subs = [x for x in t.tasks if "sub" in x["tags"]]
        self.assertEqual(len(subs), 3)
        self.assertEqual(subs[0]["parentref"], "HD-5")
        self.assertEqual(subs[0]["project"], "work")
        self.assertEqual(subs[0]["start_ts"], 1500)
        self.assertEqual(opened, [["xdg-open", "https://erp.example.com/app/hd-ticket/HD-5"]])

    def test_run_with_hostile_model_uses_fallback(self):
        t = helpers.FakeTw(self)
        t.seed(desc="Parent ticket title here", ref="HD-6", src="erp")
        r = breakdown.run("HD-6", model=lambda title: "$(reboot) [[[")
        self.assertTrue(r["fallback"])
        self.assertEqual(len([x for x in t.tasks if "sub" in x["tags"]]), 1)

    def test_tw_refuses_odd_refs_and_tags(self):
        for bad in ("a b", "x;y", "$(id)", "", "-x", "a" * 200):
            with self.assertRaises(tw.TwError):
                tw.by_ref(bad)
        with self.assertRaises(tw.TwError):
            tw.add("t", tags=("ok", "bad tag"))
        with self.assertRaises(tw.TwError):
            tw._attrs({"re f": "x"})


class WorkFilter(unittest.TestCase):
    def test_conf_parse(self):
        raw = config.parse('# c\nWORK_GH_ORG=acme  # trailing\nERP_DOCTYPES="ToDo,HD Ticket"\nbad line\nERP_MODE=token\n')
        self.assertEqual(raw["WORK_GH_ORG"], "acme")
        self.assertEqual(raw["ERP_DOCTYPES"], "ToDo,HD Ticket")
        cfg = config.Work(raw)
        self.assertEqual(cfg.erp_doctypes, ["ToDo", "HD Ticket"])
        self.assertEqual(cfg.erp_mode, "token")

    def test_work_vs_personal(self):
        cfg = config.Work({"WORK_GH_ORG": "acme", "WORK_PROJECTS": "work lab"})
        self.assertTrue(cfg.is_work_task({"project": "work"}))
        self.assertTrue(cfg.is_work_task({"src": "erp"}))
        self.assertTrue(cfg.is_work_task({"src": "gh", "ref": "gh:acme/x#1"}))
        self.assertTrue(cfg.is_work_task({"src": "gh", "ref": "gh:ACME/x#1"}))
        self.assertFalse(cfg.is_work_task({"src": "gh", "ref": "gh:me/dots#1"}))
        self.assertFalse(cfg.is_work_task({"project": "personal"}))
        self.assertFalse(cfg.is_work_task({"src": "claude"}))
        cfg = config.Work({"WORK_GH_ORG": "acme", "WORK_GH_REPOS": "acme/only"})
        self.assertTrue(cfg.is_work_task({"src": "gh", "ref": "gh:acme/only#1"}))
        self.assertFalse(cfg.is_work_task({"src": "gh", "ref": "gh:acme/other#1"}))
        self.assertFalse(config.Work({}).is_work_task({"src": "gh", "ref": "gh:acme/x#1"}))


SAMPLE = '''method call time=1793139785.757472 sender=:1.5 -> destination=org.freedesktop.Notifications serial=3 path=/org/freedesktop/Notifications; interface=org.freedesktop.Notifications; member=Notify
   string "Slack"
   uint32 0
   string "slack"
   string "New message from Ada"
   string "the secret body"
   array [
      string "default"
      string "Open"
   ]
   array [
      dict entry(
         string "urgency"
         variant             byte 2
      )
   ]
   int32 -1
method call time=1793139786.1 sender=:1.6 -> destination=org.freedesktop.Notifications serial=4 path=/org/freedesktop/Notifications; interface=org.freedesktop.Notifications; member=Notify
   string "notify-send"
   uint32 0
   string ""
   string "Build ok"
   string "nothing private"
   array [
   ]
   array [
   ]
   int32 -1
'''.splitlines()


class NotifyLog(helpers.Scratch):
    def test_parse_and_body_policy(self):
        evs = list(notifylog.parse(SAMPLE))
        self.assertEqual([(e["app"], e["summary"], e["urgency"]) for e in evs],
                         [("Slack", "New message from Ada", "critical"), ("notify-send", "Build ok", "normal")])
        cfg = config.Work({"NOTIFY_BODY_APPS": "notify-send"})
        recs = [notifylog.record(e, cfg, now=5) for e in evs]
        self.assertIsNone(recs[0]["body"])                    # slack is not listed: summary only
        self.assertEqual(recs[1]["body"], "nothing private")  # listed
        self.assertNotIn("secret", json.dumps(recs))

    def test_ring_cap_and_mode(self):
        p = config.STATE_DIR / "notifications.jsonl"
        for i in range(120):
            notifylog.append(p, {"ts": i, "app": "a", "summary": str(i), "body": None, "urgency": "normal", "id": i}, cap=50)
        lines = p.read_text().splitlines()
        self.assertLessEqual(len(lines), 250)
        self.assertEqual(json.loads(lines[-1])["summary"], "119")
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)


class Ingest(helpers.Scratch):
    def setUp(self):
        super().setUp()
        self.tw = helpers.FakeTw(self)

    def session(self, sess, **kw):
        rec = {"v": 1, "ts": int(time.time()), "action": "prompt", "sess": sess, "agent": None, "pane": "w1:p1",
               "tab": "w1:t1", "workspace": "w1", "cwd": "/x", "ticket": None, "prompt_head": "fix the thing", "transcript": None}
        rec.update(kw)
        atomic.write_json(config.STATE_DIR / "sessions" / f"{sess}.json", rec)

    def test_new_session_becomes_one_task_with_agent_pane_and_is_idempotent(self):
        self.session("sess-1")
        r1 = ingest.run(live=[])
        r2 = ingest.run(live=[])
        self.assertEqual((r1["created"], r2["created"]), (1, 0))
        t = self.tw.tasks[0]
        self.assertEqual((t["src"], t["sess"], t["pane"], t["wspace"]), ("claude", "sess-1", "w1:p1", "w1"))
        self.assertEqual(t["agent"], names.for_session("sess-1"))
        self.assertEqual(t["tags"], ["mirror", "attn"])

    def test_ticket_attaches_to_existing_ref_task_without_touching_its_title(self):
        orig = self.tw.seed(desc="Mirrored title", ref="TASK-2026-00561", src="erp", srcstate="task:open", tags=["mirror", "attn"])
        self.session("sess-2", ticket="TASK-2026-00561")
        r = ingest.run(live=[])
        self.assertEqual((r["created"], r["updated"]), (0, 1))
        self.assertEqual((orig["desc"], orig["src"], orig["sess"]), ("Mirrored title", "erp", "sess-2"))

    def test_session_without_a_prompt_is_not_work_yet(self):
        self.session("sess-3", prompt_head="", action="session")
        self.assertEqual(ingest.run(live=[])["created"], 0)

    def test_live_herdr_pane_wins_over_the_recorded_one(self):
        self.session("sess-4")
        ingest.run(live=[{"pane": "w7:p2", "workspace": "w7", "tab": "w7:t1", "sess": "sess-4", "status": "idle"}])
        self.assertEqual(self.tw.tasks[0]["pane"], "w7:p2")

    def test_old_sessions_are_ignored(self):
        self.session("sess-5", ts=int(time.time()) - 3 * 86400)
        self.assertEqual(ingest.run(live=[])["created"], 0)


class ReadOnly(unittest.TestCase):
    """The no-write guarantee is checked, not promised."""

    FORBIDDEN = [r"gh\s+api\s+-X", r"""["']-X["']""", r"--method", r"-f\s+\w+=", r"--field", r"--raw-field",
                 r"""\[\s*["'](pr|issue|repo|release|label)["']\s*,\s*["'](comment|review|merge|close|reopen|edit|create|delete|lock|ready)["']""",
                 r"""method\s*=\s*["'](POST|PUT|PATCH|DELETE)""", r"\.post\(", r"\.put\(", r"\.delete\("]

    def test_no_write_verb_anywhere_in_the_package(self):
        for f in PKG.glob("*.py"):
            text = f.read_text()
            for pat in self.FORBIDDEN:
                for m in re.finditer(pat, text):
                    line = text[:m.start()].count("\n") + 1
                    src = text.splitlines()[line - 1]
                    if src.lstrip().startswith("#") or "FORBIDDEN" in src:
                        continue
                    self.fail(f"{f.name}:{line} matches {pat!r}: {src.strip()}")

    def test_only_get_requests_exist(self):
        text = (PKG / "sources_erp.py").read_text()
        self.assertEqual(set(re.findall(r'method="([A-Z]+)"', text)), {"GET"})


if __name__ == "__main__":
    unittest.main()
