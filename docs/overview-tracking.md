# Work tracking (dots-overview)

Every piece of work gets a Taskwarrior task, completion is detected by **reading** the source, and six small JSON files tell the shell what needs you.

> Nothing here writes to ERP, GitHub, Slack, mail or Kubernetes. Completion is marked in local Taskwarrior only.

---

## 1. What runs

| piece | where | does |
|---|---|---|
| `dots-track-hook` | `~/.local/bin`, Claude hook | records session, pane, ticket; no network, no `task` |
| `dots-overview fast` | user timer, every 60 s | ingest + local + attention; local only, ~150 ms |
| `dots-overview sync` / `detect` | dots-ops job `overview-sync`, hourly | GitHub + ERP reads, completion detectors |
| `dots-overview report` | on logout, and a 23:50 fallback | the daily brief |
| `dots-jump` | `~/.local/bin` | focus the pane, open the url, print the resume command |

---

## 2. Files it writes

`~/.local/state/dots-overview/` (dir `0700`, files `0600`, all atomic, all `v:1`)

| file | schema | content |
|---|---|---|
| `now.json` | `docs/contracts/now.schema.json` | active task, agent, focus timer, clock |
| `activity.json` | `activity.schema.json` | worked time and completions per bucket |
| `attention.json` | `attention.schema.json` | ranked items, counts, per-source health |
| `heatmap.json` | `heatmap.schema.json` | 119 days, score 0..4 |
| `mirror.json` | `mirror.schema.json` | refs, urls, open or closed |
| `meta.json` | `meta.schema.json` | writer, timing, errors |

Internal: `sessions.jsonl`, `sessions/<id>.json`, `notifications.jsonl`, `focus.json`, `sync-status.json`, `app-cache.json`.

`DOTS_OVERVIEW_STATE_DIR` redirects all of it (the fixtures use this).

---

## 3. Config

`~/.config/dots-work/work.conf` is written **by hand** and never committed.
Copy `work.conf.example`; every value there is a placeholder.

Extra keys beyond the spec: `ERP_MCP_TOOLS` (read tools), `ERP_MCP_DENY` (every write-capable tool; mcp mode refuses to run while it is empty; passed as `--disallowedTools` with `--permission-mode default`) and `REDACT_WORDS` (extra words scrubbed from the brief on top of the always-on email scrub), so no tool, company or handle name lives in this public repo.

---

## 4. Taskwarrior schema

`home/create_dot_taskrc` is written once, so on a machine that already has a `~/.taskrc` run these:

```sh
for u in ref src srcstate url agent sess pane wspace parentref; do
  task rc.confirmation=off config uda.$u.type string
done
task rc.confirmation=off config uda.srcsync.type date
task rc.confirmation=off config uda.ref.label Ref
task rc.confirmation=off config uda.src.label Src
task rc.confirmation=off config uda.srcstate.label State
task rc.confirmation=off config uda.srcsync.label Synced
task rc.confirmation=off config uda.url.label URL
task rc.confirmation=off config uda.agent.label Agent
task rc.confirmation=off config uda.sess.label Session
task rc.confirmation=off config uda.pane.label Pane
task rc.confirmation=off config uda.wspace.label Space
task rc.confirmation=off config uda.parentref.label Parent
```

> The spec's `uda.parent` does not work. Taskwarrior reserves `parent` and refuses the whole taskrc. It is `parentref` here.

`srcstate` is `<kind>:<state>`, for example `review:open`, `pr:merged`, `hd:resolved`. The kind tells the detector which source call to make.

Tags: `+mirror` (sync-created), `+work` (company repo or ERP), `+attn` (somebody waits on you), `+sub` (breakdown step).

---

## 5. Claude hooks

The installer script now finds each hook by its own marker, so two hooks share `UserPromptSubmit`:

| hook | events |
|---|---|
| `timetrack-ticket-hook` | `UserPromptSubmit`, `SessionStart` |
| `dots-track-hook` | `UserPromptSubmit`, `SessionStart`, `Stop`, `SessionEnd` |

A session becomes a task on its **first prompt**, not at `SessionStart`. A session that never gets a prompt is not work yet.

---

## 6. Apply it live (orchestrator)

1. Merge the branch, then `chezmoi apply` (installs the hook, the job, the example conf, the new taskrc for fresh machines).
2. Run the `task config` block above once on the live `~/.taskrc`.
3. Add `dots-overview` to `nix/pkgs/overlay.nix`, the package list and `tests/expected-commands.txt` (T2's files), then `home-manager switch`.
4. Create units (T2 owns unit files, all disabled by default):
   `dots-overview-fast.timer` runs `dots-overview fast` every 60 s;
   `dots-overview-report.timer` runs `dots-overview report --reason nightly` at 23:50, `Persistent=true`;
   `dots-ops-overview-sync.timer` hourly, `OnStartupSec=240`;
   `dots-notifylog.service` runs `dots-overview notifylog`, opt-in.
5. `~/.config/dots-work/work.conf`: copy the example and fill it in by hand.
6. ERP: choose `token` or `mcp` and set `ERP_MODE`. Until then it stays off.
7. Check: `dots-overview fast && dots-overview doctor`.

---

## 6b. Terminal time (which task was the terminal on)

About 90% of a day is one app, so "by app" says nothing. Terminal time is attributed per herdr pane.

1. **Gate.** Terminal window focused and AW not-afk, the same gate browser time uses (`web.py`).
2. **Focus log.** `clockd` subscribes to `pane.focused`, `tab.focused`, `workspace.focused` and writes `focus_span` rows (one per uninterrupted focus on one pane and context).
   Spans close on the next focus, when you go idle, on a herdr disconnect (back-dated to the last moment the daemon was alive) and on suspend.
3. **Join ladder** (`timetrack term`, strongest first, the rung is kept as `labelled_by`):
   ```
   session → the Claude session in the pane owns a Taskwarrior task (sess UDA) or a ticket in sessions.jsonl
   token   → ticket token reported on the pane
   branch  → ticket in the branch checked out in the pane's directory (HEAD at run time)
   repo    → project the repo maps to (repos.json); a project, no ticket
   llm     → local model, open tasks and known projects only
   title   → before the focus log: workspace name from the window title
   ```
   Pane ids are recycled when herdr restarts, so joins also carry `terminal_id` and the session, and a session record is only matched inside a time window.
4. **The model** (Ollama on loopback) sees a directory name, workspace/tab/pane labels, the session summary and recent prompt heads, with secrets redacted.
   It may name only a task it was offered or a project already known; anything else is dropped.
   Answers are cached per context in `term_label_cache`.
5. **Where it lives.** `private.db` only: `focus_span`, `term_segment`, `term_day`, `term_label_cache` (schema v4). Pane titles, directories and prompts never reach `work.db`.
   `dots-overview` reads only task, project and agent labels from it, to fill `byRef`, `byProject`, `byAgent` and the terminal line of `byApp`.
6. **Honesty.** Time with no evidence behind it shows as `unattributed`, not as a verdict.
   Clock run time and attention are merged per ref with `max`, never summed.
7. **Run it.** `timetrack term [--day D] [--all] [--no-llm]`. `timetrack doctor` warns when herdr is up and you are present but no focus span is being written.
   herdr's `window_title` is set to `{workspace} · {tab} · {pane}` so even unlogged history names the workspace.

---

## 7. Known gaps

1. **Hook fixtures are documented shapes, not live captures.** A live capture needs a real model call, which this build's rules forbid. Capture one per event and replace `tests/fixtures/hooks/*.json`.
2. **ERP is untested against a real server.** Both adapters are covered by fakes only.
3. **Mail** is `unavailable` by design (no local store).
4. **Project time** comes from timewarrior tags (project first). Intervals with a single tag count as `unassigned`.
5. **Open clock sessions** are capped at 16 h so a session the daemon never closed cannot count a whole night as work.
