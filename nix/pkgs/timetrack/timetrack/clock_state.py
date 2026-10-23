"""The clock state machine: pure, no sockets, no clock of its own.

Separated from the daemon deliberately. Everything here is a function of (previous state,
event, timestamp), so the whole of D3 can be driven from a recorded event stream in a test
and asserted against -- which is the only way to be confident about a component whose bugs
produce a plausible WRONG number rather than an error.

Per-agent sub-states and the transitions, from the plan:

    working            -> ADVANCING
    blocked            -> GRACE, for GRACE_SECONDS
    idle | done        -> INACTIVE
    unknown            -> HOLDS the previous state

`unknown` holding rather than resetting is not symmetry for its own sake. Detection is
regex over terminal output; a missed match is far more likely than a false one, so the
asymmetry is chosen to favour the user rather than to look neutral.

Global roll-up:

    RUNNING   any agent ADVANCING, or the human is active
    AMBER     no agent ADVANCING, at least one in GRACE
    STOPPED   otherwise

D3's two numbers come from attribution, not from this machine: an interval is
human-attended when human_active held during it, and agent-only otherwise. The submitted
headline is the human-attended figure; agent-only time is dashboard-only and capped
elsewhere. This module records which, and never merges them.
"""
from dataclasses import dataclass, field

GRACE_SECONDS = 90          # D3: a blocked agent keeps the clock for this long
UNATTENDED_MAX_SECONDS = 90 * 60   # D3: unattended stretches beyond this are excluded

ADVANCING, GRACE, INACTIVE = "ADVANCING", "GRACE", "INACTIVE"
RUNNING, AMBER, STOPPED = "RUNNING", "AMBER", "STOPPED"

_FROM_STATUS = {
    "working": ADVANCING,
    "blocked": GRACE,
    "idle": INACTIVE,
    "done": INACTIVE,
}


@dataclass
class Agent:
    pane_id: str
    sub: str = INACTIVE
    since: float = 0.0
    grace_until: float = 0.0
    ticket: str | None = None
    confidence: float = 0.0
    last_status: str = "unknown"


@dataclass
class Clock:
    """Holds every agent's sub-state and the human's presence; derives the global state."""
    agents: dict = field(default_factory=dict)
    human_active: bool = False
    human_since: float = 0.0
    state: str = STOPPED
    changed_at: float = 0.0

    # -- inputs ------------------------------------------------------------
    def agent_status(self, pane_id, status, now):
        """Apply one pane.agent_status_changed. Returns True if the global state moved."""
        a = self.agents.get(pane_id) or Agent(pane_id=pane_id, since=now)
        self.agents[pane_id] = a
        a.last_status = status
        if status == "unknown":
            return self._settle(now)          # hold; an unknown is not evidence
        sub = _FROM_STATUS.get(status, INACTIVE)
        if sub != a.sub:
            a.sub, a.since = sub, now
        a.grace_until = now + GRACE_SECONDS if sub is GRACE else 0.0
        return self._settle(now)

    def pane_gone(self, pane_id, now):
        self.agents.pop(pane_id, None)
        return self._settle(now)

    def set_human(self, active, now):
        if active != self.human_active:
            self.human_active, self.human_since = active, now
        return self._settle(now)

    def tick(self, now):
        """Expire grace. Must be called even with no events, or a blocked agent holds forever."""
        for a in self.agents.values():
            if a.sub is GRACE and a.grace_until and now >= a.grace_until:
                a.sub, a.since, a.grace_until = INACTIVE, now, 0.0
        return self._settle(now)

    def bind_ticket(self, pane_id, ticket, confidence, now):
        a = self.agents.get(pane_id) or Agent(pane_id=pane_id, since=now)
        self.agents[pane_id] = a
        a.ticket, a.confidence = ticket, confidence

    # -- derivation --------------------------------------------------------
    def _derive(self):
        subs = [a.sub for a in self.agents.values()]
        if self.human_active or ADVANCING in subs:
            return RUNNING
        if GRACE in subs:
            return AMBER
        return STOPPED

    def _settle(self, now):
        new = self._derive()
        if new != self.state:
            self.state, self.changed_at = new, now
            return True
        return False

    # -- attribution -------------------------------------------------------
    def attribution(self):
        """Which number an interval ending now belongs to (D3).

        human  the submitted headline -- the user was present for it
        agent  dashboard only; real work, but nobody was watching
        none   the clock is not running
        """
        if self.state is STOPPED:
            return "none"
        return "human" if self.human_active else "agent"

    def advancing_tickets(self):
        return sorted({a.ticket for a in self.agents.values()
                       if a.sub is ADVANCING and a.ticket})

    def summary(self):
        return {
            "state": self.state,
            "attribution": self.attribution(),
            "human_active": self.human_active,
            "agents": {p: {"sub": a.sub, "ticket": a.ticket, "status": a.last_status}
                       for p, a in sorted(self.agents.items())},
        }
