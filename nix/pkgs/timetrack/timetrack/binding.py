"""Which ticket is a pane advancing? -- three tiers, each with its own confidence.

    1.0   a token reported on the pane itself via `herdr pane report-metadata`,
          set by a Claude Code hook. Explicit, and it EXPIRES: the --ttl-ms means a
          dead agent's binding stops claiming a ticket instead of claiming it forever.
    0.8   the worktree branch, when it names a ticket: feat/ABC-123-whatever
    0.5   the repository, mapped to a project. No ticket; feeds the promotion queue (D4)

The confidence is stored per event, not thrown away, because these are not equivalent
claims. "He said he was on ABC-123" and "the branch was called feat/ABC-123" look the same
in a total and are not the same evidence, and a figure that cannot show which it rests on
cannot be defended line by line.

The repo->project map lives in UNMANAGED config: `dots` is public and a repository list
names the employer. Absent map, tier 3 simply yields nothing rather than inventing one.
"""
import json
import re

from . import config

TIER_TOKEN, TIER_BRANCH, TIER_REPO = 1.0, 0.8, 0.5

# feat/ABC-123, fix/ABC-123-thing, chore/ABC-1 -- anchored at a path-segment boundary, so
# "my-feat/ABC-1" is not a typed-prefix match.
BRANCH_RE = re.compile(r"(?:^|/)(?:feat|fix|chore|refactor|docs|test|perf)/([A-Z][A-Z0-9]+-\d+)")
# A ticket shaped token ANYWHERE in the branch. This still resolves "my-feat/ABC-1" and
# "ABC-1-hotfix" at tier 2, which is intended: the id is present and a human put it there.
# Both forms share confidence 0.8 because both are the same kind of evidence -- a name,
# not a statement. Only tier 1 is someone actually saying what they are working on.
LOOSE_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")


def from_metadata(pane):
    """Tier 1. herdr returns reported tokens on the pane; a ttl'd one that lapsed is gone."""
    meta = (pane or {}).get("metadata") or {}
    tokens = meta.get("tokens") or meta
    if isinstance(tokens, dict):
        for key in ("ticket", "issue", "task"):
            v = tokens.get(key)
            if v:
                return str(v), TIER_TOKEN
    return None, 0.0


def from_branch(branch):
    """Tier 2. Prefer the typed-prefix form; fall back to any ticket-shaped token."""
    if not branch:
        return None, 0.0
    m = BRANCH_RE.search(branch)
    if m:
        return m.group(1), TIER_BRANCH
    m = LOOSE_RE.search(branch)
    if m:
        return m.group(1), TIER_BRANCH
    return None, 0.0


def _repo_map():
    path = config.CONF_DIR / "repos.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def from_repo(cwd):
    """Tier 3. A project, never a ticket -- so it is recorded as unattributed work."""
    if not cwd:
        return None, 0.0
    mapping = _repo_map()
    for prefix, project in sorted(mapping.items(), key=lambda kv: -len(kv[0])):
        if cwd == prefix or cwd.startswith(prefix.rstrip("/") + "/"):
            return project, TIER_REPO
    return None, 0.0


def resolve(pane):
    """Highest tier that answers. Returns (ticket_or_project, confidence, tier_name).

    Never guesses across tiers: a repo match does not become a ticket because a ticket was
    expected. Unmatched work is reported as such and feeds the promotion queue, which is
    the honest outcome and the one that produces a question later.
    """
    pane = pane or {}
    for fn, args, tier in (
        (from_metadata, (pane,), "token"),
        (from_branch, (pane.get("git_branch") or pane.get("branch"),), "branch"),
        (from_repo, (pane.get("foreground_cwd") or pane.get("cwd"),), "repo"),
    ):
        value, conf = fn(*args)
        if value:
            return value, conf, tier
    return None, 0.0, "none"
