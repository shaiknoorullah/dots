"""Explicit DDL with PRAGMA user_version migrations.

The previous design let sqlite_utils infer the schema from whatever happened to be
upserted. On a file that decides someone's pay that is not acceptable: there was no
CREATE TABLE anywhere, so "no work today" and "the table was never created" were
indistinguishable, and `alter=True` meant a typo in a key name silently added a column.

The DDL is embedded here rather than shipped as schema/*.sql so the package stays a
single importable unit with no data files to install -- it is still explicit, versioned
and reviewable in one place.
"""
import sqlite3

from . import dbs

WORK_V1 = """
-- Append-only ledger of clock state transitions: the ONLY source of truth for
-- attendance. Never UPDATEd, never DELETEd. Every reported number is recomputed from
-- these rows, so a daemon outage costs resolution, never data.
CREATE TABLE clock_event (
  id             INTEGER PRIMARY KEY,
  ts             TEXT    NOT NULL,            -- RFC3339 with offset
  ts_unix        INTEGER NOT NULL,
  attendance_day TEXT    NOT NULL,            -- YYYY-MM-DD, inherited from the session
  session_id     INTEGER NOT NULL REFERENCES work_session(id),
  kind           TEXT    NOT NULL,
  ticket         TEXT,
  actor          TEXT    NOT NULL,
  -- Opaque slot id such as 'slot:7'. NEVER a pane title or cwd: those carry company and
  -- cluster names, and this table is the one that may leave the machine.
  actor_ref      TEXT,
  reason_code    TEXT,                        -- enum only, never prose
  provenance     TEXT    NOT NULL,
  ruleset        TEXT    NOT NULL,
  confidence     REAL    NOT NULL DEFAULT 1.0,
  -- No 'lock' member, deliberately: the old code treated a screen lock as a clock-out,
  -- so ten minutes reading a PDF ended the working day. Progress continues behind a
  -- lock screen, and the CHECK makes that a schema-level guarantee.
  CHECK (kind IN ('session_open','session_close','run_start','run_stop',
                  'grace_start','grace_cancel','ticket_bind','ticket_unbind',
                  'deduction_start','deduction_stop','attest')),
  CHECK (actor IN ('human','agent','system')),
  CHECK (confidence > 0.0 AND confidence <= 1.0)
);
CREATE INDEX ix_ce_day  ON clock_event(attendance_day, ts_unix);
CREATE INDEX ix_ce_sess ON clock_event(session_id, ts_unix);
CREATE INDEX ix_ce_tkt  ON clock_event(ticket, ts_unix) WHERE ticket IS NOT NULL;

-- A session owns its attendance_day for good, and every event inherits it. This is what
-- "the login day owns the session" has to mean in practice: logind sessions are useless
-- here (one session stayed open for seven days), so sessions are derived from activity.
CREATE TABLE work_session (
  id             INTEGER PRIMARY KEY,
  opened_ts      TEXT    NOT NULL,
  opened_unix    INTEGER NOT NULL,
  closed_ts      TEXT,
  closed_unix    INTEGER,
  attendance_day TEXT    NOT NULL,
  open_trigger   TEXT    NOT NULL,
  close_trigger  TEXT,
  provenance     TEXT    NOT NULL
);
CREATE INDEX ix_ws_day ON work_session(attendance_day);

-- Breaks. Category only: the free text and the guess live in private.db.
CREATE TABLE deduction (
  id             INTEGER PRIMARY KEY,
  session_id     INTEGER REFERENCES work_session(id),
  attendance_day TEXT    NOT NULL,
  start_unix     INTEGER NOT NULL,
  end_unix       INTEGER,
  category       TEXT    NOT NULL,
  minutes        INTEGER,
  confirmed      INTEGER NOT NULL DEFAULT 0,
  provenance     TEXT    NOT NULL,
  CHECK (category IN ('salah','lunch','washroom','misc','call_personal'))
);
CREATE INDEX ix_ded_day ON deduction(attendance_day);

-- Per-ticket minutes, split so the submitted headline can be human-attended time while
-- agent-only time stays a separate, labelled line.
CREATE TABLE ticket_minutes (
  ticket         TEXT    NOT NULL,
  attendance_day TEXT    NOT NULL,
  minutes        INTEGER NOT NULL,
  human_minutes  INTEGER NOT NULL,
  agent_minutes  INTEGER NOT NULL,
  status         TEXT,
  ruleset        TEXT    NOT NULL,
  computed_unix  INTEGER NOT NULL,
  PRIMARY KEY (ticket, attendance_day)
);

-- The target denominator. note_ref is an opaque key into private.db, never the text.
CREATE TABLE day_class (
  day            TEXT PRIMARY KEY,
  kind           TEXT NOT NULL,
  leave_type     TEXT,
  pay_affecting  INTEGER NOT NULL DEFAULT 0,
  target_minutes INTEGER NOT NULL,
  note_ref       TEXT,
  attested_unix  INTEGER,
  CHECK (kind IN ('working','weekend','holiday','leave_paid','leave_unpaid',
                  'absent','worked_unlogged'))
);

-- Written once per month at submission and never UPDATEd. input_digest pins the exact
-- rows the figure came from, so a later bug cannot move a number already handed over.
CREATE TABLE monthly_submission (
  month          TEXT PRIMARY KEY,
  submitted_unix INTEGER NOT NULL,
  target_minutes INTEGER NOT NULL,
  worked_minutes INTEGER NOT NULL,
  delta_minutes  INTEGER NOT NULL,
  basis          TEXT    NOT NULL,
  ruleset        TEXT    NOT NULL,
  input_digest   TEXT    NOT NULL,
  export_path    TEXT    NOT NULL
);

-- The egress allow-list. Nothing leaves that is not named here, and
-- `timetrack egress --explain` prints this table with the SQL behind each field.
CREATE TABLE egress_field (
  target     TEXT NOT NULL,
  field      TEXT NOT NULL,
  source_sql TEXT NOT NULL,
  PRIMARY KEY (target, field)
);

CREATE TABLE schema_meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);

-- Pair run_start with the run_stop that follows it in the same session.
CREATE VIEW v_run_interval AS
WITH ev AS (
  SELECT id, session_id, attendance_day, ts_unix, kind, provenance, confidence, ticket,
         LEAD(ts_unix) OVER w AS next_unix,
         LEAD(kind)    OVER w AS next_kind
  FROM clock_event
  WHERE kind IN ('run_start','run_stop')
  WINDOW w AS (PARTITION BY session_id ORDER BY ts_unix, id)
)
SELECT session_id, attendance_day, ticket, provenance, confidence,
       ts_unix AS start_unix, next_unix AS end_unix, (next_unix - ts_unix) AS seconds
FROM ev
WHERE kind = 'run_start' AND next_kind = 'run_stop' AND next_unix > ts_unix;

CREATE VIEW v_day_span AS
SELECT attendance_day,
       CAST(ROUND(SUM(seconds) / 60.0) AS INTEGER) AS span_minutes,
       MIN(start_unix) AS first_unix,
       MAX(end_unix)   AS last_unix,
       GROUP_CONCAT(DISTINCT provenance) AS provenance,
       MIN(confidence) AS min_confidence
FROM v_run_interval
GROUP BY attendance_day;

CREATE VIEW v_day_deduction AS
SELECT attendance_day,
       CAST(ROUND(SUM(minutes)) AS INTEGER) AS deduction_minutes,
       SUM(CASE WHEN confirmed = 0 THEN 1 ELSE 0 END) AS unconfirmed
FROM deduction
WHERE end_unix IS NOT NULL
GROUP BY attendance_day;

-- net_minutes is NULL when no break has been recorded for the day. That is deliberate:
-- the employer's rule is net of breaks, so presenting a span as if it were net would
-- over-report. A NULL forces the export to show the gap instead of inventing a number.
CREATE VIEW v_day_worked AS
SELECT c.day AS attendance_day, c.kind, c.leave_type, c.pay_affecting, c.target_minutes,
       COALESCE(s.span_minutes, 0) AS span_minutes,
       s.first_unix, s.last_unix, s.provenance, s.min_confidence,
       d.deduction_minutes, d.unconfirmed,
       CASE WHEN d.deduction_minutes IS NULL THEN NULL
            ELSE COALESCE(s.span_minutes, 0) - d.deduction_minutes END AS net_minutes
FROM day_class c
LEFT JOIN v_day_span s      ON s.attendance_day = c.day
LEFT JOIN v_day_deduction d ON d.attendance_day = c.day;
"""

PRIVATE_V1 = """
-- Prose for a classified day (leave reason, attestation note). work.db keeps only the
-- opaque note_ref that points here.
CREATE TABLE day_note (
  note_ref TEXT PRIMARY KEY,
  day      TEXT NOT NULL,
  text     TEXT NOT NULL
);

-- Surplus carry-forward: a private ledger, excluded from anything submitted, because it
-- has not been confirmed as company policy.
CREATE TABLE carry_forward (
  month           TEXT PRIMARY KEY,
  surplus_minutes INTEGER NOT NULL,
  running_balance INTEGER NOT NULL
);

CREATE TABLE schema_meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


# --- work v2 ---------------------------------------------------------------
# 'dinner' added to deduction.category. The original five were written for a day shift;
# this user's is 13:30-22:30, so the evening meal is the NORMAL one and 'lunch' would be
# wrong on almost every record. Labelling a 21:34 meal break "lunch" in a payroll export
# is the kind of small untruth that makes a reviewer doubt the rest of the figure.
#
# SQLite cannot alter a CHECK, so the table is rebuilt. Done now, while deduction holds
# a few dozen rows, rather than after a year of them.
WORK_V2 = """
PRAGMA foreign_keys=OFF;
-- The views must go FIRST. SQLite resolves a view's body lazily, so dropping a table a
-- view references leaves the schema in a state where the very next statement fails with
-- "error in view v_day_deduction: no such table: main.deduction". executescript does not
-- run in a transaction, so that failure left the database half-migrated: deduction gone,
-- deduction_new holding the rows, user_version still 1. Recreating the views afterwards
-- is what makes this safe to run on a populated database.
DROP VIEW IF EXISTS v_day_worked;
DROP VIEW IF EXISTS v_day_deduction;

CREATE TABLE deduction_new (
  id             INTEGER PRIMARY KEY,
  session_id     INTEGER REFERENCES work_session(id),
  attendance_day TEXT    NOT NULL,
  start_unix     INTEGER NOT NULL,
  end_unix       INTEGER,
  category       TEXT    NOT NULL,
  minutes        INTEGER,
  confirmed      INTEGER NOT NULL DEFAULT 0,
  provenance     TEXT    NOT NULL,
  CHECK (category IN ('salah','lunch','dinner','washroom','misc','call_personal'))
);
INSERT INTO deduction_new SELECT id, session_id, attendance_day, start_unix, end_unix,
  category, minutes, confirmed, provenance FROM deduction;
DROP TABLE deduction;
ALTER TABLE deduction_new RENAME TO deduction;
CREATE INDEX IF NOT EXISTS ix_ded_day ON deduction(attendance_day);

CREATE VIEW v_day_deduction AS
SELECT attendance_day,
       CAST(ROUND(SUM(minutes)) AS INTEGER) AS deduction_minutes,
       SUM(CASE WHEN confirmed = 0 THEN 1 ELSE 0 END) AS unconfirmed
FROM deduction
WHERE end_unix IS NOT NULL
GROUP BY attendance_day;

CREATE VIEW v_day_worked AS
SELECT c.day AS attendance_day, c.kind, c.leave_type, c.pay_affecting, c.target_minutes,
       COALESCE(s.span_minutes, 0) AS span_minutes,
       s.first_unix, s.last_unix, s.provenance, s.min_confidence,
       d.deduction_minutes, d.unconfirmed,
       CASE WHEN d.deduction_minutes IS NULL THEN NULL
            ELSE COALESCE(s.span_minutes, 0) - d.deduction_minutes END AS net_minutes
FROM day_class c
LEFT JOIN v_day_span s      ON s.attendance_day = c.day
LEFT JOIN v_day_deduction d ON d.attendance_day = c.day;
PRAGMA foreign_keys=ON;
"""

MIGRATIONS = {"work": [(1, WORK_V1), (2, WORK_V2)], "private": [(1, PRIVATE_V1)]}


def _apply(conn, which):
    have = conn.execute("PRAGMA user_version").fetchone()[0]
    applied = []
    for version, sql in MIGRATIONS[which]:
        if version <= have:
            continue
        conn.executescript(sql)
        conn.execute(f"PRAGMA user_version = {version:d}")
        applied.append(version)
    conn.commit()
    return have, applied


def migrate():
    """Bring both databases up to date. Idempotent."""
    out = {}
    for which, opener in (("work", dbs.open_work), ("private", dbs.open_private)):
        with opener() as conn:
            out[which] = _apply(conn, which)
    return out
