"""
Article model: the common schema every collector normalizes into,
matching the spec's "Step 2: Normalize data" requirement.

Implementation note: uses stdlib dataclasses instead of pydantic. Pydantic
would give you validation for free, but it's an extra dependency for what
is currently a simple, fully-controlled internal shape. If later phases
want field validation (e.g. enforcing category enums, URL format), swapping
this for a pydantic BaseModel is a small, contained change since all
collectors construct Article through this one class.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone


@dataclass
class Article:
    title: str
    url: str
    source: str          # human-readable, e.g. "OpenAI", "arXiv", "GitHub Trending"
    source_type: str     # "rss" | "arxiv" | "github_trending" | "reddit" | "hackernews"
    author: str | None = None
    published_at: str | None = None   # ISO 8601 string
    content: str | None = None
    category: str | None = None
    tags: list[str] = field(default_factory=list)
    importance: float = 0.0
    extra: dict = field(default_factory=dict)  # source-specific fields (stars, citations...)

    id: str = field(init=False)

    def __post_init__(self) -> None:
        self.id = self._make_id()

    def _make_id(self) -> str:
        """Stable ID derived from the URL, so re-collecting the same article
        across runs naturally deduplicates at the database layer (UNIQUE url
        + same id on upsert)."""
        normalized_url = self.url.strip().rstrip("/").lower()
        return hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()[:24]

    def to_db_row(self) -> dict:
        d = asdict(self)
        d["tags"] = ",".join(self.tags) if self.tags else ""
        d["extra"] = json.dumps(self.extra, ensure_ascii=False) if self.extra else "{}"
        return d


def upsert_article(conn: sqlite3.Connection, article: Article) -> bool:
    """
    Insert an article, or update it in place if the URL already exists.

    Returns True if this was a brand-new row, False if it already existed
    (and was just refreshed/touched).
    """
    row = article.to_db_row()
    cur = conn.execute("SELECT id FROM articles WHERE url = ?", (row["url"],))
    existing = cur.fetchone()

    if existing:
        conn.execute(
            """
            UPDATE articles SET
                title = ?, source = ?, source_type = ?, author = ?,
                published_at = ?, content = ?, category = ?, tags = ?,
                extra = ?, updated_at = ?
            WHERE url = ?
            """,
            (
                row["title"], row["source"], row["source_type"], row["author"],
                row["published_at"], row["content"], row["category"], row["tags"],
                row["extra"], datetime.now(timezone.utc).isoformat(), row["url"],
            ),
        )
        return False

    conn.execute(
        """
        INSERT INTO articles
            (id, title, url, source, source_type, author, published_at,
             content, summary, category, tags, importance, extra)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
        """,
        (
            row["id"], row["title"], row["url"], row["source"], row["source_type"],
            row["author"], row["published_at"], row["content"], row["category"],
            row["tags"], row["importance"], row["extra"],
        ),
    )
    return True
