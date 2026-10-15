"""SQLite connection, schema migration and time helpers shared by every DB module (PLAN §3.2).

Each worker uses its own connection. Writes that must not interleave run inside `immediate(conn)`, i.e. `BEGIN IMMEDIATE`,
SQLite's single writer lock, so two workers (or two processes) can never interleave a claim or a ledger check.
"""
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema.sql")


def connect(db_path, timeout=30.0):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None: we issue BEGIN ourselves, so the sqlite3 module never opens a deferred transaction behind our back.
    conn = sqlite3.connect(db_path, timeout=timeout, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
    return conn


@contextmanager
def immediate(conn):
    """One write transaction under the database write lock; rolls back on any error."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def migrate(conn):
    """Apply schema.sql (CREATE … IF NOT EXISTS) and add columns that an older DB lacks. Idempotent."""
    sql = SCHEMA.read_text(encoding="utf-8")
    conn.executescript(sql)
    for table, body in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", sql, re.S):
        have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for line in body.splitlines():
            m = re.match(r"\s+([a-z_0-9]+)\s+([A-Z]+[^,]*?),?\s*(--.*)?$", line)
            if not m or m.group(1) in have or m.group(1) in {"unique", "primary"}:
                continue
            decl = m.group(2).replace("PRIMARY KEY", "").replace(" UNIQUE", "")
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {m.group(1)} {decl}")


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    """Fixed-width ISO UTC text, so string order = time order in SQL comparisons."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def now_iso(plus_seconds=0.0):
    return iso(utcnow() + timedelta(seconds=plus_seconds))
