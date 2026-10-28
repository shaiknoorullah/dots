"""Break down & start dispatches an agent into the Hangar: a tab per ticket, read-only brief."""
import json
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401  (sys.path)
from dots_overview import config, launch


class FakeHerdr:
    def __init__(self, workspaces, panes=()):
        self.calls, self.ws, self.panes = [], list(workspaces), list(panes)

    def __call__(self, args, timeout=5):
        self.calls.append(list(args))
        if args[:2] == ["api", "snapshot"]:
            return json.dumps({"result": {"snapshot": {"workspaces": self.ws, "panes": self.panes,
                                                       "focused_workspace_id": "w1"}}})
        if args[:2] == ["tab", "create"]:
            return json.dumps({"result": {"root_pane": {"pane_id": "w5:p2", "tab_id": "w5:t2"}, "tab": {"tab_id": "w5:t2"}}})
        if args[:2] == ["workspace", "create"]:
            return json.dumps({"result": {"root_pane": {"pane_id": "w5:p1", "tab_id": "w5:t1"}}})
        return "{}"


PARENT = {"ref": "gh:Acme/widgets#12", "desc": "Fix the widget", "src": "gh", "url": "https://github.com/Acme/widgets/issues/12"}
STEP = {"uuid": "0123abcd-0000-4000-8000-000000000001"}


class Launch(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        (self.home / "work" / "widgets").mkdir(parents=True)
        self.cfg = config.Work({"WORK_CHECKOUTS": str(self.home / "work"), "AGENT_CWD": str(self.home)})

    def test_first_dispatch_creates_the_hangar_and_names_the_tab(self):
        h = FakeHerdr([{"workspace_id": "w1", "label": "Elsewhere"}])
        r = launch.launch(PARENT, ["Read it", "Fix it"], "Read it", STEP, cfg=self.cfg, run=h, sleep=lambda s: None)
        self.assertEqual(r, {"pane": "w5:p1", "tab": "w5:t1", "reused": False})
        create = next(c for c in h.calls if c[:2] == ["workspace", "create"])
        self.assertIn("Hangar", create)
        self.assertIn(f"DOTS_TASK_DECLARE={STEP['uuid']}", create)
        self.assertIn(str(self.home / "work" / "widgets"), create)        # the repo checkout
        self.assertIn(["tab", "rename", "w5:t1", "widgets#12"], h.calls)
        start = next(c for c in h.calls if c[:2] == ["agent", "start"])
        self.assertEqual(start[3:7], ["--kind", "claude", "--pane", "w5:p1"])
        self.assertRegex(start[2], r"^[a-z][a-z0-9_-]{0,31}$")          # herdr rejects anything else

    def test_later_dispatches_add_a_tab_to_the_existing_hangar(self):
        h = FakeHerdr([{"workspace_id": "w5", "label": "Hangar"}])
        r = launch.launch(PARENT, ["Read it"], "Read it", STEP, cfg=self.cfg, run=h, sleep=lambda s: None)
        self.assertEqual(r["pane"], "w5:p2")
        create = next(c for c in h.calls if c[:2] == ["tab", "create"])
        self.assertEqual(create[2:4], ["--workspace", "w5"])
        self.assertFalse(any(c[:2] == ["workspace", "create"] for c in h.calls))

    def test_a_live_agent_on_the_step_is_focused_not_duplicated(self):
        h = FakeHerdr([{"workspace_id": "w5", "label": "Hangar"}],
                      [{"pane_id": "w5:p2", "tab_id": "w5:t2", "workspace_id": "w5", "agent": "claude"}])
        r = launch.launch(PARENT, ["Read it"], "Read it", dict(STEP, pane="w5:p2"), cfg=self.cfg, run=h, sleep=lambda s: None)
        self.assertTrue(r["reused"])
        self.assertFalse(any(c[:2] in (["tab", "create"], ["agent", "start"]) for c in h.calls))

    def test_the_brief_prompt_is_read_only(self):
        h = FakeHerdr([{"workspace_id": "w5", "label": "Hangar"}])
        launch.launch(dict(PARENT, src="erp", ref="TASK-2026-00001"), ["Read it"], "Read it", STEP, cfg=self.cfg, run=h, sleep=lambda s: None)
        text = next(c for c in h.calls if c[:2] == ["agent", "prompt"])[3]
        for must in ("READ ONLY", "Never create, update, comment on", "never answer anyone on my behalf", "stop and wait"):
            self.assertIn(must, text)

    def test_agent_start_is_retried_until_the_shell_is_ready(self):
        h = FakeHerdr([{"workspace_id": "w5", "label": "Hangar"}])
        fails = [None, None]
        inner = h.__call__
        h_run = lambda args, timeout=5: (fails.pop() if args[:2] == ["agent", "start"] and fails else inner(args, timeout))  # noqa: E731
        r = launch.launch(PARENT, ["Read it"], "Read it", STEP, cfg=self.cfg, run=h_run, sleep=lambda s: None)
        self.assertEqual(r["pane"], "w5:p2")

    def test_no_repo_checkout_uses_agent_cwd(self):
        self.assertEqual(launch.cwd_for("TASK-2026-00001", self.cfg), str(self.home))
        self.assertEqual(launch.cwd_for("gh:Acme/../../etc#1", self.cfg), str(self.home))
        self.assertEqual(launch.cwd_for("gh:Acme/..#1", self.cfg), str(self.home))


if __name__ == "__main__":
    unittest.main()
