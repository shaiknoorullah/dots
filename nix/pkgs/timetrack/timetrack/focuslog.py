"""Which herdr pane has focus, as a log -- the raw material for termclass.py.

ActivityWatch says the terminal had OS focus; it cannot say which pane inside herdr, and
that is where the work is. herdr emits pane/tab/workspace focus events (no timestamp: the
daemon stamps them on READ, see clockd), and a snapshot says what each pane is -- its
workspace, tab, cwd, terminal, the Claude session it hosts, a reported ticket token.

One `focus_span` row is one uninterrupted stretch of focus on one pane with one CONTEXT.
If the context changes while the pane stays focused (an agent starts, the pane is
renamed, the shell moves to another directory) the span is closed and a new one opened, so
a row never claims a context the pane did not have for the whole of it.

A span is open only while all of these hold, because anything else would be time nobody
spent: herdr is connected, the human is present, a pane has focus. It is closed at the
instant that stops being true -- back-dated to the last moment the daemon was known alive
when the cause is a connection gap, never stretched across it.

Pane ids are RECYCLED across herdr restarts, so a durable join never rests on pane_id
alone: terminal_id and the agent session are stored beside it, and termclass bounds every
join in time.

Everything is private.db: the labels and cwds name the employer. work.db never sees them.
"""
import json
import os
import socket
import sys

from . import binding, herdr_proto

REFRESH_MIN = 2.0       # an event burst (pane.updated fires on every title change) is one fetch
REFRESH_MAX = 30.0      # at most this stale, whatever happens
HEARTBEAT = 15.0        # how often an open span's seen_unix moves

CTX = ("pane_id", "tab_id", "workspace_id", "terminal_id", "agent_session",
       "ticket_token", "cwd", "ws_label", "tab_label", "pane_label")


def log(msg):
    print(f"timetrack focuslog: {msg}", file=sys.stderr, flush=True)


def unwrap(result):
    """session.snapshot answers {"type": ..., "snapshot": {...}}; tolerate the bare form."""
    result = result or {}
    snap = result.get("snapshot")
    return snap if isinstance(snap, dict) else result


def snapshot(path=None, timeout=5):
    """One session.snapshot over the herdr unix socket, or {} if herdr is not there.
    Read-only: used by termclass for the CURRENT workspace labels, never to change anything."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(os.path.expanduser(path or herdr_proto.SOCKET))
        f = s.makefile("rwb")
        f.write((json.dumps({"id": "c", "method": herdr_proto.M_SNAPSHOT, "params": {}})
                 + "\n").encode())
        f.flush()
        return unwrap(json.loads(f.readline()).get("result"))
    except (OSError, ValueError):
        return {}
    finally:
        s.close()


def _session(pane):
    s = pane.get("agent_session")
    if isinstance(s, dict):
        s = s.get("value")
    return str(s) if s else None


def context(snap, pane_id):
    """The context dict for one pane in a snapshot, or None if it is not there."""
    pane = next((p for p in (snap or {}).get("panes", []) if p.get("pane_id") == pane_id), None)
    if pane is None:
        return None
    ws = next((w for w in snap.get("workspaces", [])
               if w.get("workspace_id") == pane.get("workspace_id")), {})
    tab = next((t for t in snap.get("tabs", []) if t.get("tab_id") == pane.get("tab_id")), {})
    token, _ = binding.from_metadata(pane)
    return {"pane_id": pane_id, "tab_id": pane.get("tab_id"),
            "workspace_id": pane.get("workspace_id"), "terminal_id": pane.get("terminal_id"),
            "agent_session": _session(pane), "ticket_token": token,
            "cwd": pane.get("foreground_cwd") or pane.get("cwd"),
            "ws_label": ws.get("label"), "tab_label": tab.get("label"),
            "pane_label": pane.get("label")}


class FocusLog:
    """State machine over (connected, present, focused pane, context). All writes are
    private.db; `snapshot_fn` is injected so tests drive it without a socket."""

    def __init__(self, conn, snapshot_fn):
        self.conn = conn
        self.snapshot_fn = snapshot_fn
        self.snap = None
        self.snap_at = 0.0
        self.dirty = False
        self.focus_pane = None
        self.focus_pending = None      # tab/workspace focus: the pane is known only by a snapshot
        self.connected = False
        self.present = True
        self.open_id = None
        self.open_ctx = None
        self.open_start = None
        self.beat_at = 0.0

    # -- the log ------------------------------------------------------------
    def close_dangling(self):
        """Spans a dead previous life left open end where it last looked, not at now."""
        cur = self.conn.execute(
            "UPDATE focus_span SET end_unix = MAX(start_unix, COALESCE(seen_unix, start_unix))"
            " WHERE end_unix IS NULL")
        self.conn.commit()
        return cur.rowcount

    def _open(self, ctx, at):
        cols = ",".join(CTX)
        cur = self.conn.execute(
            f"INSERT INTO focus_span(start_unix, seen_unix, {cols})"
            f" VALUES(?,?,{','.join('?' * len(CTX))})",
            (int(at), int(at)) + tuple(ctx[k] for k in CTX))
        self.conn.commit()
        self.open_id, self.open_ctx, self.open_start = cur.lastrowid, ctx, int(at)
        self.beat_at = at

    def close(self, at, why=""):
        """Close the open span at `at`. A span that would be empty is deleted, not kept:
        one user action fires pane, tab and workspace focus in the same millisecond."""
        if self.open_id is None:
            return False
        if int(at) <= self.open_start:
            self.conn.execute("DELETE FROM focus_span WHERE id = ?", (self.open_id,))
        else:
            self.conn.execute("UPDATE focus_span SET end_unix = ?, seen_unix = ? WHERE id = ?",
                              (int(at), int(at), self.open_id))
        self.conn.commit()
        self.open_id = self.open_ctx = self.open_start = None
        return True

    def _sync(self, at):
        want = None
        if self.connected and self.present and self.focus_pane and self.snap:
            want = context(self.snap, self.focus_pane)
        if self.open_id is not None and want != self.open_ctx:
            self.close(at)
        if want is not None and self.open_id is None:
            self._open(want, at)

    # -- inputs -------------------------------------------------------------
    def refresh(self, now, at=None):
        """Fetch a snapshot; the focus it reports is the truth, whatever events said."""
        snap = unwrap(self.snapshot_fn())
        if not snap.get("panes") and not snap.get("workspaces"):
            raise ValueError("empty snapshot")
        self.snap, self.snap_at, self.dirty = snap, now, False
        self.focus_pane = snap.get("focused_pane_id") or None
        self._sync(now if at is None else at)

    def connect(self, now):
        """(Re)connected: the gap is already closed; open fresh from what IS focused."""
        self.connected = True
        self.refresh(now)

    def gap(self, at):
        """The stream dropped. Close at the last instant we were known alive."""
        self.connected = False
        self.snap = None
        self.close(at, "gap")

    def suspend(self, at):
        """The machine slept: close where we last were awake; the next tick reopens."""
        self.close(at, "suspend")
        self.dirty = True
        self.snap_at = 0.0

    def set_present(self, active, at):
        if bool(active) == self.present:
            return
        self.present = bool(active)
        self._sync(at)

    def on_event(self, name, data, recv):
        """name is the underscore form herdr sends (pane_focused, not pane.focused)."""
        if name == "pane_focused":
            pid = (data or {}).get("pane_id")
            self.focus_pane, self.focus_pending = pid, None
            if self.snap is None or context(self.snap, pid) is None:
                try:
                    # unknown pane: it was created after the cached snapshot
                    self.refresh(recv, at=recv)
                    return
                except Exception as e:      # noqa: BLE001 -- never let this take the clock down
                    log(f"refresh on unknown pane failed: {e}")
                    return
            self._sync(recv)
        elif name in ("tab_focused", "workspace_focused"):
            # which PANE is now focused is not in these frames; ask once, in tick()
            self.focus_pending = self.focus_pending or recv
        elif name in ("pane_created", "pane_closed", "pane_updated", "pane_exited",
                      "pane_agent_detected"):
            self.dirty = True

    def tick(self, now):
        """Resolve a pending tab/workspace focus, refresh a stale or dirty cache, heartbeat."""
        if not self.connected:
            return
        if self.focus_pending is not None:
            at, self.focus_pending = self.focus_pending, None
            self.refresh(now, at=at)
        elif (self.dirty and now - self.snap_at >= REFRESH_MIN) or now - self.snap_at >= REFRESH_MAX:
            self.refresh(now)
        if self.open_id is not None and now - self.beat_at >= HEARTBEAT:
            self.conn.execute("UPDATE focus_span SET seen_unix = ? WHERE id = ?",
                              (int(now), self.open_id))
            self.conn.commit()
            self.beat_at = now
