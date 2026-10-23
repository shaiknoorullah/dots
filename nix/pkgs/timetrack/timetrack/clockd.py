"""The live clock: herdr socket -> state machine -> clock_event rows.

The daemon is a CACHE, never the truth. Every number still comes from v_day_worked
recomputed over clock_event; this only decides which events get written. That is why a
crash here costs a gap rather than a wrong figure, and why `timetrack reconstruct` can
backfill one at a lower confidence.

Not a poller. herdr events carry no timestamp and agent_status is a LEVEL rather than an
edge, so a 30-second poll cannot resolve a 90-second grace window: by the time it sampled,
the transition it needed to time has already been overwritten. The loop therefore blocks on
the socket and stamps time on READ, before parsing -- the arrival of the bytes is the only
instant it can honestly claim to know.

Race recoveries, each marked at its implementation:
  R1  a pane transitions before its subscription lands  -> subscribe, then reconcile
  R2  daemon restart loses in-flight state              -> reconcile + attest/daemon_restart
  R3  grace expiry racing an unblock                    -> debounce before acting
  R4  aw-server outage makes presence falsely false     -> presence.py falls back, flagged
  R5  suspend: wall clock jumps, monotonic does not     -> detect and retro-close
  R7  herdr restarts under us                           -> reconnect with backoff
  R8  two daemons writing the same rows                 -> flock
  R9  closing a session while an agent still works      -> close predicate checks the machine
"""
import fcntl
import json
import signal
import os
import socket
import time
from datetime import date, datetime

from . import attest, binding, clock_state, config, dbs, herdr_proto, presence

SOCK = os.path.expanduser(herdr_proto.SOCKET)
CACHE = config.STATE_DIR / "clock.json"
LOCK = config.STATE_DIR / "clockd.lock"

RECONCILE_SECONDS = 30      # R1: bounds how long a missed transition can be wrong
PRESENCE_SECONDS = 15
GRACE_DEBOUNCE = 2.0        # R3
SUSPEND_SKEW = 10.0         # R5: wall-vs-monotonic divergence that means we slept
BACKOFF = (1, 2, 5, 10, 30) # R7
# D1: a work_session closes after this much quiet and is BACK-DATED to the last activity.
# 150 minutes rather than something tidier because the shift is 13:30-22:30 with a meal
# break in it: an hour of quiet is a dinner, not the end of a day, and closing the session
# there would split one attendance day into two and hand the second one the wrong date.
QUIET_CLOSE_SECONDS = 150 * 60


def sd_notify(state):
    """Minimal sd_notify. No library: it is a datagram to $NOTIFY_SOCKET and nothing more.

    The watchdog is the point. A daemon whose loop wedges still holds its lock and still
    looks alive to anything that only checks for a process -- which is the same silent
    success the whole system exists to detect. WatchdogSec makes PID 1 notice instead.
    """
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    if addr[0] == "@":
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.sendall(state.encode())
        return True
    except OSError:
        return False


class Clockd:
    def __init__(self, conn, now=None):
        self.conn = conn
        self.clock = clock_state.Clock()
        self.sock = None
        self.f = None
        self.subscribed = set()        # pane_ids with a live agent_status subscription
        self.session_id = None
        self.attendance_day = None
        self.run_open_at = None
        self.run_attr = None
        self.last_reconcile = 0.0
        self.last_presence = 0.0
        self.pending_grace = {}        # R3: pane_id -> act-after timestamp
        self.wall0 = now or time.time()
        self.mono0 = time.monotonic()

    # -- socket ------------------------------------------------------------
    def _open(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(10)
        s.connect(SOCK)
        return s, s.makefile("rwb")

    def _rpc(self, method, params=None):
        """One-shot call on its OWN connection: the event stream must not be interleaved
        with request/response traffic, or a reply is mistaken for an event."""
        s, f = self._open()
        try:
            f.write((json.dumps({"id": "c", "method": method,
                                 "params": params or {}}) + "\n").encode())
            f.flush()
            line = f.readline()
            return json.loads(line) if line else {}
        finally:
            s.close()

    def connect(self):
        self._rpc(herdr_proto.M_PING)          # R7: fail fast if herdr is not really there
        self.sock, self.f = self._open()
        self.sock.settimeout(1.0)
        self._subscribe(self._agent_panes())
        self.reconcile(time.time(), reason="connect")

    def _agent_panes(self):
        r = self._rpc(herdr_proto.M_AGENT_LIST).get("result") or {}
        return [a["pane_id"] for a in r.get("agents", []) if a.get("pane_id")]

    def _subscribe(self, panes):
        subs = [{"type": t} for t in (
            "pane.created", "pane.closed", "pane.exited", "pane.agent_detected")]
        # agent_status_changed cannot be watched globally -- it REQUIRES a pane_id, which
        # is the whole reason this daemon tracks pane lifecycle at all (herdr_proto)
        subs += [{"type": "pane.agent_status_changed", "pane_id": p} for p in panes]
        self.f.write((json.dumps({"id": "s", "method": herdr_proto.M_SUBSCRIBE,
                                  "params": {"subscriptions": subs}}) + "\n").encode())
        self.f.flush()
        self.f.readline()
        self.subscribed = set(panes)

    # -- reconciliation ----------------------------------------------------
    def close_dangling(self, now):
        """A run_start with no run_stop, left by a crash or a kill.

        Closed at the LAST EVENT the previous life recorded, not at now: the daemon has no
        idea how long it was dead, and crediting that silence would invent time. Closing
        early under-reports, which is the recoverable direction, and the attestation makes
        the seam visible so it can be backfilled deliberately.
        """
        # NOT v_run_interval. That view keeps only rows where the next event is a
        # run_stop, so an unpaired run_start is dropped and end_unix IS NULL can never
        # match -- a dangling-run check written against it reports a permanent all-clear.
        # clock_event is where the truth is.
        row = self.conn.execute(
            "SELECT e.id, e.session_id, e.attendance_day, e.ts_unix FROM clock_event e"
            " WHERE e.kind='run_start' AND NOT EXISTS ("
            "   SELECT 1 FROM clock_event s WHERE s.session_id = e.session_id"
            "   AND s.kind='run_stop' AND s.ts_unix >= e.ts_unix)"
            " ORDER BY e.ts_unix DESC" " LIMIT 1").fetchone()
        if not row:
            return 0
        last = self.conn.execute(
            "SELECT MAX(ts_unix) FROM clock_event WHERE session_id = ?",
            (row["session_id"],)).fetchone()[0] or row["ts_unix"]
        self.conn.execute(
            "INSERT INTO clock_event(ts,ts_unix,attendance_day,session_id,kind,actor,"
            "reason_code,provenance,ruleset,confidence) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (datetime.fromtimestamp(last).isoformat(timespec="seconds"), int(last),
             row["attendance_day"], row["session_id"], "run_stop", "system",
             "dangling_close", "live", config.RULESET, 0.8))
        self.conn.commit()
        return 1

    def adopt_session(self, now):
        """Take over the session a previous life left open, or close it if it went quiet.

        Without this a restart always opened a SECOND work_session for a day that already
        had one. The day total survives that (v_day_span groups by attendance_day), but D1
        says a session owns its attendance_day and inherits it to every event -- two live
        sessions for one day means two answers to "which day is this", and the one that
        loses is whichever the next event happens to pick.
        """
        row = self.conn.execute(
            # its OWN sessions only: a hotkey session never closes and an AW session is
            # dropped and rewritten every hour, so adopting either would hang live rows on
            # a session something else owns
            "SELECT id, attendance_day, opened_unix FROM work_session"
            " WHERE closed_unix IS NULL AND provenance = 'live'"
            " ORDER BY opened_unix DESC LIMIT 1").fetchone()
        if not row:
            return None
        last = self.conn.execute(
            "SELECT MAX(ts_unix) FROM clock_event WHERE session_id = ?",
            (row["id"],)).fetchone()[0] or row["opened_unix"]
        if now - last >= QUIET_CLOSE_SECONDS:
            self.session_id, self.attendance_day = row["id"], row["attendance_day"]
            self.close_session(last, "quiet_recovered")
            return "closed"
        self.session_id, self.attendance_day = row["id"], row["attendance_day"]
        return "adopted"

    def close_session(self, at, trigger):
        """Close the session at `at` -- an instant in the PAST, by design.

        Back-dating to the last recorded activity is the whole point: the daemon cannot
        know whether the quiet was a walk away from the desk or a closed laptop, and
        stamping `now` would credit every one of those minutes. It also spools the day's
        attestation, because the moment a day stops being added to is the moment it can be
        signed off -- and asking at that point beats asking a week later from memory.
        """
        if self.session_id is None:
            return
        day = self.attendance_day
        self.conn.execute(
            "INSERT INTO clock_event(ts,ts_unix,attendance_day,session_id,kind,actor,"
            "reason_code,provenance,ruleset,confidence)"
            " VALUES(?,?,?,?,'session_close','system',?,?,?,?)",
            (datetime.fromtimestamp(at).isoformat(timespec="seconds"), int(at), day,
             self.session_id, trigger, "live", config.RULESET, 1.0))
        self.conn.execute(
            "UPDATE work_session SET closed_ts=?, closed_unix=?, close_trigger=?"
            " WHERE id=?",
            (datetime.fromtimestamp(at).isoformat(timespec="seconds"), int(at),
             trigger, self.session_id))
        self.conn.commit()
        attest.spool_write("session_close_quiet", ts=int(at), day=day)
        self.session_id = None
        self.attendance_day = None

    def maybe_close_session(self, now):
        """R9 again, one level up: a session may only close while nothing is running."""
        if self.session_id is None or self.run_open_at is not None:
            return False
        if self.clock.state is not clock_state.STOPPED:
            return False
        last = self.conn.execute(
            "SELECT MAX(ts_unix) FROM clock_event WHERE session_id = ?",
            (self.session_id,)).fetchone()[0]
        if last is None or now - last < QUIET_CLOSE_SECONDS:
            return False
        self.close_session(last, "quiet")
        return True

    def shutdown(self, now=None):
        """Close the open run on the way out, so a clean stop never leaves a dangling start."""
        now = now or time.time()
        if self.run_open_at is not None:
            self._close_run(now, "shutdown")
            self._attest("clean_shutdown", now)
        self._cache(now)

    def reconcile(self, now, reason="periodic"):
        """R1/R2: ask what IS, rather than trusting the transitions we think we saw."""
        r = self._rpc(herdr_proto.M_AGENT_LIST).get("result") or {}
        live = {}
        for a in r.get("agents", []):
            pid = a.get("pane_id")
            if not pid:
                continue
            live[pid] = a
            self.clock.agent_status(pid, a.get("agent_status", "unknown"), now)
            value, conf, tier = binding.resolve(a)
            if value:
                self.clock.bind_ticket(pid, value, conf, now)
        for gone in set(self.clock.agents) - set(live):
            self.clock.pane_gone(gone, now)
        new = set(live) - self.subscribed
        if new:
            self._subscribe(sorted(set(live)))      # R1: resubscribe to cover new panes
        self.last_reconcile = now
        if reason == "restart":
            self._attest("daemon_restart", now)     # R2: the gap is auditable
        return live

    # -- event handling ----------------------------------------------------
    def handle(self, frame, recv):
        ev = frame.get("event")
        data = frame.get("data") or {}
        pid = data.get("pane_id")
        if ev == "pane_agent_status_changed":
            status = data.get("agent_status") or data.get("status") or "unknown"
            if status == "blocked":
                self.pending_grace[pid] = recv + GRACE_DEBOUNCE   # R3
            else:
                self.pending_grace.pop(pid, None)
            self.clock.agent_status(pid, status, recv)
        elif ev in ("pane_closed", "pane_exited"):
            self.clock.pane_gone(pid, recv)
            self.subscribed.discard(pid)
        elif ev in ("pane_created", "pane_agent_detected"):
            self.reconcile(recv, reason="pane")     # R1

    # -- the loop ----------------------------------------------------------
    def tick(self, now):
        # R5: wall time jumped relative to monotonic -> the machine slept. Anything we
        # believed about presence across that interval is fiction, so close the run at the
        # last instant we were awake rather than crediting the sleep.
        drift = (now - self.wall0) - (time.monotonic() - self.mono0)
        if abs(drift) > SUSPEND_SKEW:
            self._close_run(self.wall0 + (time.monotonic() - self.mono0), "suspend")
            self._attest("suspend_detected", now)
        self.wall0, self.mono0 = now, time.monotonic()

        for pid, due in list(self.pending_grace.items()):
            if now >= due:
                self.pending_grace.pop(pid, None)

        if now - self.last_presence >= PRESENCE_SECONDS:
            p = presence.human_active()
            self.clock.set_human(p.active, now)
            self.presence_detail = p
            self.last_presence = now
        if now - self.last_reconcile >= RECONCILE_SECONDS:
            self.reconcile(now)
        self.clock.tick(now)
        self._settle(now)
        self.maybe_close_session(now)

    def run(self, stop_after=None):
        start = time.time()
        attempt = 0
        while True:
            try:
                if self.sock is None:
                    self.connect()
                    attempt = 0
                line = self.f.readline()
                recv = time.time()               # stamp on READ, before parsing
                if not line:
                    raise ConnectionError("herdr closed the stream")
                self.handle(json.loads(line), recv)
            except socket.timeout:
                pass
            except (OSError, ConnectionError, ValueError):
                self._drop()                     # R7
                delay = BACKOFF[min(attempt, len(BACKOFF) - 1)]
                attempt += 1
                time.sleep(delay)
                continue
            self.tick(time.time())
            sd_notify("WATCHDOG=1")
            if stop_after and time.time() - start >= stop_after:
                return

    def _drop(self):
        try:
            if self.sock:
                self.sock.close()
        except OSError:
            pass
        self.sock = self.f = None
        self.subscribed = set()

    # -- persistence -------------------------------------------------------
    def _day(self, now):
        return date.fromtimestamp(now).isoformat()

    ACTORS = ("human", "agent", "system")

    def _event(self, kind, now, ticket=None, conf=1.0, actor="human",
               reason=None, actor_ref=None):
        # session_id is NOT NULL with a foreign key, so there is no such thing as an event
        # outside a session -- not even the restart attestation. Open one first.
        if self.session_id is None:
            self._open_session(now)
        if actor not in self.ACTORS:
            actor = "human"          # the column is a CHECK'd enum; 'none' is not a member
        self.conn.execute(
            "INSERT INTO clock_event(ts,ts_unix,attendance_day,session_id,kind,actor,"
            "actor_ref,ticket,reason_code,provenance,ruleset,confidence)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (datetime.fromtimestamp(now).isoformat(timespec="seconds"), int(now),
             self.attendance_day or self._day(now), self.session_id, kind, actor,
             actor_ref, ticket, reason, "live", config.RULESET, conf))
        self.conn.commit()

    def _attest(self, what, now):
        """reason_code, never ticket: the column is documented as an enum, and an
        attestation is a reason the record has a seam, not work done on something."""
        self._event("attest", now, actor="system", reason=what)

    def _open_session(self, now):
        self.attendance_day = self._day(now)
        cur = self.conn.execute(
            "INSERT INTO work_session(opened_ts,opened_unix,attendance_day,open_trigger,"
            "provenance) VALUES(?,?,?,?,?)",
            (datetime.fromtimestamp(now).isoformat(timespec="seconds"), int(now),
             self.attendance_day, "clockd", "live"))
        self.session_id = cur.lastrowid
        # direct insert: _event would re-enter _open_session and loop
        self.conn.execute(
            "INSERT INTO clock_event(ts,ts_unix,attendance_day,session_id,kind,actor,"
            "provenance,ruleset,confidence) VALUES(?,?,?,?,?,?,?,?,?)",
            (datetime.fromtimestamp(now).isoformat(timespec="seconds"), int(now),
             self.attendance_day, self.session_id, "session_open", "system",
             "live", config.RULESET, 1.0))
        self.conn.commit()

    def _close_run(self, now, why="stop"):
        if self.run_open_at is None:
            return
        self._event("run_stop", now, actor=self.run_attr or "human")
        self.run_open_at = None
        self.run_attr = None

    def _settle(self, now):
        """Translate the machine's state into append-only rows."""
        st = self.clock.state
        attr = self.clock.attribution()
        running = st is clock_state.RUNNING
        if running and self.run_open_at is None:
            if self.session_id is None:
                self._open_session(now)
            tickets = self.clock.advancing_tickets()
            self.run_attr = attr
            self.run_open_at = now
            self._event("run_start", now, ticket=tickets[0] if tickets else None,
                        actor=attr if attr != "none" else "human")
        elif not running and self.run_open_at is not None:
            # R9: never close while the machine still has something advancing or in grace
            self._close_run(now)
        self._cache(now)

    def _cache(self, now):
        s = self.clock.summary()
        s["updated_unix"] = int(now)
        s["session_id"] = self.session_id
        s["attendance_day"] = self.attendance_day
        p = getattr(self, "presence_detail", None)
        if p:
            s["presence"] = {"source": p.source, "degraded": p.degraded, "detail": p.detail}
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(s, indent=2))
        tmp.replace(CACHE)          # atomic: a reader never sees a half-written cache


def acquire_lock():
    """R8: two daemons would write duplicate run pairs and double the day."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None
    return fh


def main(stop_after=None):
    fh = acquire_lock()
    if fh is None:
        raise SystemExit("timetrack: another clockd holds the lock")
    with dbs.open_work() as conn:
        d = Clockd(conn)
        d.connect()
        n = d.close_dangling(time.time())      # R2: a previous life may have been killed
        if n:
            d._attest("dangling_run_closed", time.time())
        # order matters: the dangling RUN is closed first so its row is the session's last
        # activity, and the session then closes at that instant rather than at the restart
        d.adopt_session(time.time())
        attest.drain(d.conn)                   # record what the panels and the reaper saw
        d.reconcile(time.time(), reason="restart")
        sd_notify("READY=1")

        def _bye(signum, _frame):
            d.shutdown()
            raise SystemExit(0)
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, _bye)
        try:
            d.run(stop_after=stop_after)
        finally:
            d.shutdown()
    return 0
