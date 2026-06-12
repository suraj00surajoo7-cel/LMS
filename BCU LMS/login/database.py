"""
database.py — SQLite setup for BCU LMS
"""

import sqlite3
import os
from flask import g

DB_PATH = os.path.join(os.path.dirname(__file__), "bcu_lms.db")

SCHEMA = """
-- Users table
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name     TEXT    NOT NULL,
    email         TEXT    NOT NULL UNIQUE,
    mobile        TEXT    NOT NULL,
    uucms_number  TEXT    NOT NULL UNIQUE,
    year          INTEGER NOT NULL CHECK(year BETWEEN 1 AND 4),
    semester      INTEGER NOT NULL CHECK(semester BETWEEN 1 AND 8),
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Login audit log
CREATE TABLE IF NOT EXISTS login_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ip_address TEXT,
    logged_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for fast lookups
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_email    ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_uucms    ON users(uucms_number);
CREATE INDEX IF NOT EXISTS idx_logs_user_id   ON login_logs(user_id);
"""


def get_db():
    """Return a per-request SQLite connection stored on Flask's `g`."""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row          # dict-like rows
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA journal_mode = WAL")  # better concurrency
    return g.db


def init_db():
    """Create tables if they don't exist (called at app startup)."""
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    print(f"[DB] Initialised at {DB_PATH}")
