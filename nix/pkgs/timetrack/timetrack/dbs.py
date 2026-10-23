"""The only module in the package that opens a database connection.

Two physically separate files:

  work.db     facts that are allowed to leave this machine -- ticket id, minutes, status
  private.db  everything that must not -- health, mood, salah, window titles, URLs,
              agent telemetry, and the prose summaries

The egress side imports `open_work` and nothing else, so no code path exists from it to
private data. That is checked statically by tests/privacy.sh and again at the kernel
level by the egress unit's InaccessiblePaths=. Keeping every connection in one short file
is the point: the privacy boundary has to be verifiable by reading a single page.
"""
import sqlite3

from . import config


def _connect(path, readonly=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA trusted_schema=0")
    if not readonly:
        conn.execute("PRAGMA journal_mode=WAL")
    return conn


def open_work(readonly=False):
    return _connect(config.WORK_DB, readonly)


def open_private(readonly=False):
    return _connect(config.PRIVATE_DB, readonly)


def assert_single_database(conn):
    """Refuse to query a connection that has another database ATTACHed.

    Without this, a single ATTACH would quietly defeat the two-file split.
    """
    rows = conn.execute("PRAGMA database_list").fetchall()
    if len(rows) != 1:
        raise RuntimeError(f"refusing to run: {len(rows)} databases attached, expected 1")
    return conn
