"""
Database layer for the AI Intelligence Agent.

Uses plain sqlite3 from the standard library rather than an ORM. For an
MVP collecting a few hundred articles a week, raw SQL is simpler to read,
debug, and has zero extra dependencies. SQLAlchemy can be introduced later
if/when we move to PostgreSQL (see project spec, "Future: PostgreSQL").
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id              TEXT PRIMARY KEY,      -- stable hash, see processing/dedup.py
    title           TEXT NOT NULL,
    url             TEXT NOT NULL UNIQUE,
    source          TEXT NOT NULL,         -- e.g. "OpenAI", "arXiv", "GitHub Trending"
    source_type     TEXT NOT NULL,         -- "rss" | "arxiv" | "github_trending" | "reddit" | "hackernews"
    author          TEXT,
    published_at    TEXT,                  -- ISO 8601
    content         TEXT,                  -- cleaned full text / abstract
    summary         TEXT,                  -- LLM-generated, filled in Phase 4
    category        TEXT,                  -- assigned in Phase 3 classifier
    tags            TEXT,                  -- comma-separated for MVP
    importance      REAL DEFAULT 0,        -- assigned in Phase 3 ranking
    embedding       BLOB,                  -- reserved for future similarity work
    extra           TEXT,                  -- JSON blob for source-specific fields (stars, citations, etc.)
    featured_at     TEXT,                  -- ISO 8601, set once this article ships in a newsletter
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_articles_source_type ON articles(source_type);
CREATE INDEX IF NOT EXISTS idx_articles_published_at ON articles(published_at);
CREATE INDEX IF NOT EXISTS idx_articles_importance ON articles(importance);

CREATE TABLE IF NOT EXISTS newsletters (
    week            TEXT PRIMARY KEY,      -- e.g. "2026-W26"
    generated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    markdown        TEXT,
    html            TEXT,
    pdf_path        TEXT
);

CREATE TABLE IF NOT EXISTS collection_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type     TEXT NOT NULL,
    started_at      TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at     TEXT,
    articles_found  INTEGER DEFAULT 0,
    articles_new    INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'running',  -- running | success | failed
    error_message   TEXT
);
"""


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection with sane defaults (FK + row factory)."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path: str | Path) -> None:
    """Create tables if they don't already exist. Safe to call repeatedly."""
    conn = get_connection(db_path)
    try:
        conn.executescript(SCHEMA)
        _migrate_add_featured_at(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate_add_featured_at(conn: sqlite3.Connection) -> None:
    """
    One-off migration for DBs created before featured_at existed (e.g. a
    news.db already committed to the repo from an earlier run). Checks
    pragma table_info rather than just try/except-ing the ALTER TABLE, so
    running this repeatedly is a cheap no-op instead of relying on catching
    a "duplicate column" error every single time init_db runs.
    """
    existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(articles)")}
    if "featured_at" not in existing_columns:
        conn.execute("ALTER TABLE articles ADD COLUMN featured_at TEXT")


@contextmanager
def db_session(db_path: str | Path):
    """Context manager: yields a connection, commits on success, rolls back on error."""
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()