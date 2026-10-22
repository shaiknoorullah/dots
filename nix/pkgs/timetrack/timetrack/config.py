"""Paths, and the policy this repo is not allowed to contain.

`dots` is a PUBLIC repo, so nothing machine-, person- or company-specific lives here:
payroll figures, holiday dates, git author identities and the backfill calibration all
come from hand-copied files under ~/.config/timetrack/ (mode 0600) that chezmoi does not
manage. Every accessor below has a safe default so the package still imports and runs on
a machine where those files were never copied.
"""
import os
import tomllib
from datetime import date
from pathlib import Path

HOME = Path.home()
XDG_CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")
XDG_DATA = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local/share")

CONF_DIR = XDG_CONFIG / "timetrack"
PAYROLL_TOML = CONF_DIR / "payroll.toml"
HOLIDAYS_TOML = CONF_DIR / "holidays.toml"
CATEGORIES_TOML = CONF_DIR / "categories.toml"

STATE_DIR = Path(os.environ.get("TIMETRACK_STATE_DIR") or XDG_DATA / "timetrack")
WORK_DB = STATE_DIR / "work.db"
PRIVATE_DB = STATE_DIR / "private.db"
LOGIND_JSONL = STATE_DIR / "events" / "logind.jsonl"

VAULT = Path(os.environ.get("TIMETRACK_VAULT") or HOME / "powerhouse")
VAULT_DAILY = VAULT / "timetrack" / "daily"
EXPORT_DIR = VAULT / "exports"

AW_BASE = os.environ.get("TIMETRACK_AW_BASE", "http://127.0.0.1:5600")
TASK = "task"
TIMEW = "timew"

# Bump when a derivation rule changes. Recorded on every row so a number can always be
# traced to the rules that produced it, and so a submitted figure never silently moves.
RULESET = "v1"

_WEEKDAY = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _load(path):
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"timetrack: {path} is not valid TOML: {exc}")


def payroll():
    return _load(PAYROLL_TOML)


def hours_per_day():
    return int(payroll().get("rules", {}).get("hours_per_day", 8))


def week_off():
    """Weekday numbers (Mon=0) that are not working days."""
    names = payroll().get("rules", {}).get("week_off", ["Sat", "Sun"])
    return {_WEEKDAY[str(n).strip().lower()[:3]] for n in names}


def gross_monthly():
    v = payroll().get("salary", {}).get("gross_monthly")
    return float(v) if v is not None else None


def currency():
    return payroll().get("salary", {}).get("currency", "")


def calibration():
    """Multiplier correcting git-derived spans against measured spans.

    Default 1.0 -- uncalibrated, and therefore honest. A machine that has been measured
    against a real source sets its own factor in local config.
    """
    return float(payroll().get("backfill", {}).get("calibration", 1.0))


def backfill_authors():
    """Git author emails that count as "me". Personal identifiers: local config only."""
    raw = payroll().get("backfill", {}).get("authors", [])
    return [str(a).strip().lower() for a in raw if str(a).strip()]


def backfill_repo_globs():
    return [str(g) for g in payroll().get("backfill", {}).get("repos", ["~/work/*"])]


def gap_bridge_seconds():
    return int(payroll().get("backfill", {}).get("gap_bridge_minutes", 45)) * 60


def lead_in_seconds():
    return int(payroll().get("backfill", {}).get("lead_in_minutes", 20)) * 60


def holidays():
    """{date: label} of paid holidays. Hand-maintained because the ERP `Holiday` DocType
    is permission-denied for this account, so the dates cannot be fetched."""
    out = {}
    for _year, table in _load(HOLIDAYS_TOML).items():
        if not isinstance(table, dict):
            continue
        for key, label in table.items():
            try:
                out[date.fromisoformat(str(key))] = str(label)
            except ValueError:
                raise SystemExit(f"timetrack: {HOLIDAYS_TOML}: '{key}' is not an ISO date")
    return out
