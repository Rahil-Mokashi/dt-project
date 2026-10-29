import os
import sqlite3
from datetime import datetime

from .fmt import TS

DEFAULT_DB = os.environ.get(
    "RECOVERIQ_DB", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "recoveriq.db"))
DEMO_DATE = "2026-10-29"
DEFAULT_CLOCK = "14:20"

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
  id TEXT PRIMARY KEY, name TEXT, tier TEXT, tenure INTEGER, amount INTEGER, status TEXT,
  decline_code INTEGER, failure_type TEXT, fail_at TEXT,
  contacts INTEGER DEFAULT 0, retries INTEGER DEFAULT 0, access_until TEXT, on_time TEXT,
  opt_in INTEGER DEFAULT 1, consent_withdrawn_at TEXT, dnd INTEGER DEFAULT 0,
  hard_stop INTEGER DEFAULT 0, blocked_reason TEXT, message TEXT, message_source TEXT,
  next_retry_at TEXT, deferred_until TEXT, pause_until TEXT, confirmation_id TEXT,
  recovered_at TEXT, recovered_amount INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS audit (
  n INTEGER PRIMARY KEY, ts TEXT NOT NULL, case_id TEXT, customer TEXT, event TEXT NOT NULL,
  result TEXT, actor TEXT, detail TEXT, amount INTEGER
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
"""


def connect(path=None):
    conn = sqlite3.connect(path or DEFAULT_DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn, reset=False):
    """Create tables and seed on first run; reset=True rebuilds everything from the seed."""
    if reset:
        for t in ("audit", "cases", "settings"):
            conn.execute(f"DROP TABLE IF EXISTS {t}")  # dropping the table drops its triggers
    conn.executescript(SCHEMA)
    if not conn.execute("SELECT 1 FROM cases LIMIT 1").fetchone():
        from . import data_seed
        data_seed.seed(conn)
    conn.commit()


def get_clock(conn):
    row = conn.execute("SELECT v FROM settings WHERE k='clock'").fetchone()
    return row["v"] if row else DEFAULT_CLOCK


def set_clock(conn, hhmm):
    datetime.strptime(hhmm, "%H:%M")  # validate
    conn.execute("INSERT OR REPLACE INTO settings VALUES ('clock', ?)", (hhmm,))
    conn.commit()


def now(conn):
    """Demo clock as a datetime (demo date + stored HH:MM + live seconds). Never the system date."""
    h, m = map(int, get_clock(conn).split(":"))
    d = datetime.strptime(DEMO_DATE, "%Y-%m-%d")
    return d.replace(hour=h, minute=m, second=datetime.now().second)


def now_str(conn):
    return now(conn).strftime(TS)
