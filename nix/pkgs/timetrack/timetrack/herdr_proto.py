"""The herdr socket surface clockd depends on, verified against herdr 0.9.3 on 2026-10-22.

Written down because every fact here was discovered by probing a live server, and a wrong
assumption about any of them is the kind that produces a plausible but wrong payroll number
rather than an error.

Protocol: NDJSON over a unix socket at ~/.config/herdr/herdr.sock. One JSON object per line
in each direction. A request is {"id", "method", "params"} -- `params` is REQUIRED even when
empty; omitting it is an invalid_request, not a default.

Subscriptions are objects, never strings: {"type": "<name>"} plus whatever that type needs.

  **pane.agent_status_changed REQUIRES a concrete pane_id.** Subscribing without one fails
  with `missing field pane_id`. This single fact decides the daemon's shape: there is no
  way to watch "every agent", so clockd must hold a per-pane subscription and add or drop
  one as panes come and go -- which is why the global lifecycle events below are subscribed
  too, and why a snapshot reconcile is needed on every pane.created (R1).

Everything else listed here subscribes globally with no pane_id.
"""

SOCKET = "~/.config/herdr/herdr.sock"

# An EVENT frame, captured live:
#
#   {"event": "pane_focused",
#    "data": {"type": "pane_focused", "pane_id": "w4:p1", "workspace_id": "w4"}}
#
# Three things that will bite a naive dispatcher:
#
#  1. **The event name uses UNDERSCORES while the subscription type uses DOTS.** You
#     subscribe to "pane.focused" and receive "pane_focused". Keying a handler table on the
#     string you subscribed with silently matches nothing, and a clock that receives no
#     events looks exactly like a clock with nothing to do.
#  2. An event frame has no `id`. A response frame has one. That is how to tell them apart
#     on a connection carrying both.
#  3. **Events carry no timestamp.** This is why the daemon stamps time on READ, before
#     parsing: the only moment it can honestly claim to know is when the bytes arrived.
#
# One user action can produce several events: focusing a pane emitted pane_focused,
# tab_focused and workspace_focused in the same millisecond.

def event_name(subscription_type):
    """"pane.agent_status_changed" -> "pane_agent_status_changed"."""
    return subscription_type.replace(".", "_")

# verified: subscribing with {"type": t} alone is accepted
GLOBAL_EVENTS = (
    "pane.created", "pane.closed", "pane.updated", "pane.focused",
    "pane.moved", "pane.exited", "pane.agent_detected", "pane.output_matched",
    "pane.scroll_changed", "layout.updated",
    "workspace.created", "workspace.updated", "workspace.metadata_updated",
    "workspace.renamed", "workspace.moved", "workspace.reordered",
    "workspace.closed", "workspace.focused",
    "worktree.created", "worktree.opened", "worktree.removed",
    "tab.created", "tab.closed", "tab.focused", "tab.renamed", "tab.moved",
)

# verified: requires pane_id
PER_PANE_EVENTS = ("pane.agent_status_changed",)

# methods clockd uses; the server rejects anything outside its own enum
M_SNAPSHOT = "session.snapshot"
M_AGENT_LIST = "agent.list"
M_AGENT_EXPLAIN = "agent.explain"     # why a pane is in its current state
M_PANE_LIST = "pane.list"
M_SUBSCRIBE = "events.subscribe"
M_PING = "ping"

# Agent states observed live. `unknown` is in the detection vocabulary; the plan's rule is
# that it HOLDS the previous state rather than resetting, because detection is regex over
# terminal output and the asymmetry must favour the user.
AGENT_STATES = ("working", "idle", "blocked", "done", "unknown")

# Title glyphs, checked against the osc_title_working rule in
# ~/.local/state/herdr/agent-detection/remote/claude.toml (priority 1100):
#   regex ^[U+2800-U+28FF U+25D0-U+25D3] followed by a space
# Observed live: working panes carry U+25D0 '◐' (matches); idle and done carry U+2733 '✳'
# (does not). So the priority-1100 title rule does not fire for a non-working pane and
# cannot outrank the blocked rules at 980 -- a concern raised in review that does not hold.
# Still unverified: what the title carries while a pane is genuinely `blocked`.
TITLE_WORKING_RANGES = ((0x2800, 0x28FF), (0x25D0, 0x25D3))
TITLE_IDLE_GLYPH = "✳"
