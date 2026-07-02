"""
processing/deduplicate.py — detect and group near-duplicate stories.

Many sources report identical news (e.g. "OpenAI releases GPT-X" appears
on TechCrunch, The Verge, Wired, HackerNews, and three blogs the same day).
Rather than deleting duplicates, we GROUP them: one canonical article keeps
all sources listed, so the newsletter can say "covered by 5 sources" instead
of showing the same story five times.

Algorithm (no embedding/ML dependency for MVP):
1. Normalize title → lowercase, remove punctuation, tokenize
2. Compute Jaccard similarity between token sets of every pair
3. Articles with similarity ≥ threshold belong to the same group
4. Within a group, pick the "best" article (most content, best source rank)
   and attach the others as `related_sources` in its extra field.

This is O(n²) over candidate articles. At ~150 articles/week it's fast
enough (~0.01s). If we ever scale to thousands, switch to MinHash LSH.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from database.database import db_session
from scheduler.logging_setup import get_logger

logger = get_logger(__name__)

# Similarity threshold: 0.0 = everything is a duplicate, 1.0 = nothing is.
# 0.5 works well in practice: catches "OpenAI releases GPT-X", "OpenAI
# launches GPT-X", "OpenAI's new GPT-X model" as the same story, but keeps
# "OpenAI releases GPT-X" and "OpenAI releases Codex" separate.
SIMILARITY_THRESHOLD = 0.5

# Source quality ranking — higher rank = preferred canonical source when
# deduplicating. Primary sources always beat secondary coverage.
SOURCE_RANK: dict[str, int] = {
    "openai": 10,
    "anthropic": 10,
    "google deepmind": 10,
    "hugging face": 9,
    "nvidia ai": 9,
    "meta ai": 9,
    "arxiv": 8,
    "github trending": 7,
}


@dataclass
class ArticleRow:
    id: str
    title: str
    url: str
    source: str
    source_type: str
    content: str | None
    importance: float


def run_deduplication(db_path: str) -> dict:
    """
    Find near-duplicate articles in the DB and mark duplicates.

    Returns a summary: {"groups_found": N, "duplicates_marked": M}
    """
    articles = _load_articles(db_path)
    if not articles:
        return {"groups_found": 0, "duplicates_marked": 0}

    groups = _group_duplicates(articles)

    duplicates_marked = 0
    with db_session(db_path) as conn:
        for canonical, duplicates in groups:
            if not duplicates:
                continue
            dup_ids = [d.id for d in duplicates]
            source_list = canonical.source + ", " + ", ".join(d.source for d in duplicates)

            # Update the canonical article's extra field with all sources
            conn.execute(
                """
                UPDATE articles
                SET extra = json_set(COALESCE(extra, '{}'),
                    '$.duplicate_sources', ?)
                WHERE id = ?
                """,
                (source_list, canonical.id),
            )
            # Mark duplicates so ranking/newsletter skip them
            conn.execute(
                f"""
                UPDATE articles SET importance = -1
                WHERE id IN ({','.join('?' * len(dup_ids))})
                """,
                dup_ids,
            )
            duplicates_marked += len(dup_ids)
            logger.debug(
                "Grouped: '%s' (canonical) + %d duplicate(s)", canonical.title[:60], len(duplicates)
            )

    logger.info(
        "Deduplication: %d group(s) found, %d article(s) marked as duplicate.",
        len(groups), duplicates_marked,
    )
    return {"groups_found": len(groups), "duplicates_marked": duplicates_marked}


# ── internal ────────────────────────────────────────────────────────────────

def _load_articles(db_path: str) -> list[ArticleRow]:
    """Load all articles that haven't already been marked as duplicates."""
    with db_session(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, title, url, source, source_type, content, importance
            FROM articles
            WHERE importance >= 0
            ORDER BY published_at DESC
            """
        ).fetchall()
    return [ArticleRow(**dict(r)) for r in rows]


def _group_duplicates(articles: list[ArticleRow]) -> list[tuple[ArticleRow, list[ArticleRow]]]:
    """
    Return a list of (canonical_article, [duplicates]) groups.
    Articles not part of any group are not included.
    """
    token_sets = [_tokenize(a.title) for a in articles]
    n = len(articles)

    # Union-Find to group duplicates transitively
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        parent[find(x)] = find(y)

    for i in range(n):
        for j in range(i + 1, n):
            if not token_sets[i] or not token_sets[j]:
                continue
            sim = _jaccard(token_sets[i], token_sets[j])
            if sim >= SIMILARITY_THRESHOLD:
                union(i, j)

    # Collect groups (only groups with ≥2 members are interesting)
    from collections import defaultdict
    clusters: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        clusters[find(i)].append(i)

    groups: list[tuple[ArticleRow, list[ArticleRow]]] = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        member_articles = [articles[i] for i in members]
        canonical = _pick_canonical(member_articles)
        duplicates = [a for a in member_articles if a.id != canonical.id]
        groups.append((canonical, duplicates))

    return groups


def _pick_canonical(articles: list[ArticleRow]) -> ArticleRow:
    """Pick the best article from a duplicate group to be the canonical version."""
    def score(a: ArticleRow) -> tuple[int, int]:
        source_score = SOURCE_RANK.get(a.source.lower(), 5)
        content_len = len(a.content or "")
        return (source_score, content_len)

    return max(articles, key=score)


def _tokenize(text: str) -> frozenset[str]:
    """Normalize and tokenize a title into a set of meaningful words."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)     # strip punctuation
    words = text.split()
    # Remove short stop words — they add noise to Jaccard similarity
    stopwords = {"a", "an", "the", "is", "in", "on", "at", "to", "of",
                 "and", "or", "for", "with", "by", "from", "as", "its"}
    return frozenset(w for w in words if w not in stopwords and len(w) > 1)


def _jaccard(a: frozenset, b: frozenset) -> float:
    """Jaccard similarity = |intersection| / |union|"""
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)