"""The only module in the package that opens a database connection.

Two physically separate files, both local -- nothing leaves this machine:

  work.db     the attendance facts -- sessions, deductions, ticket ids, minutes
  private.db  everything personal -- health, salah, break detail, day notes, and what
              other tools record locally (browser time per site, agent activity)

The split keeps the payroll file small and reviewable: work.db can be read end to end
without wading through personal data. Keeping every connection in one short file means
that boundary is verifiable by reading a single page. tests/locality.sh checks the other
half: that nothing in the package opens a connection off this machine.
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
