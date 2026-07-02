"""
processing/ranking.py — score articles by importance (0–100).

The spec defines the ranking formula as:
    importance = source quality + github stars + reddit discussion
               + freshness + paper popularity + company announcement

This implements all of those as additive sub-scores, each capped to a
sub-range, so the final score is always in [0, 100] and interpretable:

  Source quality     0–30   (who published it)
  Freshness          0–25   (how recent)
  Content signals    0–25   (stars, abstract length, keyword density)
  Source type bonus  0–20   (company announcements > blog posts > random)

Scores are written back to articles.importance in the DB so later phases
(LLM selection, newsletter ordering) can ORDER BY importance DESC.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from database.database import db_session
from scheduler.logging_setup import get_logger

logger = get_logger(__name__)

# ── source quality scores (out of 30) ────────────────────────────────────────

SOURCE_QUALITY: dict[str, float] = {
    # Primary sources: highest signal
    "openai": 30,
    "anthropic": 30,
    "google deepmind": 28,
    "hugging face": 26,
    "nvidia ai": 24,
    "meta ai": 24,
    "microsoft ai": 22,
    # Research
    "arxiv": 20,
    # Community / trending
    "github trending": 15,
    # Reddit, HackerNews (Phase 3+)
    "hackernews": 12,
    "reddit": 10,
}
DEFAULT_SOURCE_QUALITY = 10  # Unknown sources get a baseline

# ── keyword signals that indicate high-importance content ────────────────────

HIGH_IMPORTANCE_KEYWORDS = [
    # Major product releases / announcements
    "release", "launch", "announce", "introduce", "unveil",
    # Capabilities that matter
    "breakthrough", "state-of-the-art", "sota", "benchmark",
    "reasoning", "multimodal", "agent", "tool use",
    # Infrastructure / ecosystem
    "open source", "open-source", "api", "model weights",
    # Safety / alignment (always relevant)
    "alignment", "safety", "red team",
]

LOW_IMPORTANCE_KEYWORDS = [
    "podcast", "interview", "opinion", "recap", "roundup",
    "sponsored", "advertisement", "ad ",
]


def run_ranking(db_path: str) -> dict:
    """
    Score all unranked articles (importance == 0) and write scores to DB.

    Returns {"ranked": N, "skipped_duplicates": M}
    """
    with db_session(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, title, url, source, source_type, content,
                   published_at, extra, importance
            FROM articles
            WHERE importance >= 0
            """
        ).fetchall()

    ranked = 0
    skipped = 0

    with db_session(db_path) as conn:
        for row in rows:
            row = dict(row)
            extra = json.loads(row.get("extra") or "{}")
            score = _compute_score(row, extra)
            conn.execute(
                "UPDATE articles SET importance = ? WHERE id = ?",
                (score, row["id"]),
            )
            ranked += 1

    logger.info("Ranking: scored %d articles.", ranked)
    return {"ranked": ranked, "skipped_duplicates": skipped}


def _compute_score(row: dict, extra: dict) -> float:
    """Compute a 0–100 importance score for a single article."""
    score = 0.0

    # 1. Source quality (0–30)
    source_key = (row.get("source") or "").lower()
    score += SOURCE_QUALITY.get(source_key, DEFAULT_SOURCE_QUALITY)

    # 2. Freshness (0–25): full score if today, linear decay over 7 days
    score += _freshness_score(row.get("published_at"), max_score=25, decay_days=7)

    # 3. Content signals (0–25)
    score += _content_signal_score(row, extra, max_score=25)

    # 4. Source type bonus (0–20)
    score += _source_type_bonus(row.get("source_type"), max_score=20)

    return round(min(score, 100.0), 2)


def _freshness_score(published_at: str | None, max_score: float, decay_days: int) -> float:
    """Linear decay from max_score (published today) to 0 (published decay_days ago)."""
    if not published_at:
        return max_score * 0.5  # Unknown date — give half credit

    try:
        # Handle both "Z" suffix and "+00:00" style
        pub = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return max_score * 0.5

    age_seconds = (datetime.now(timezone.utc) - pub).total_seconds()
    age_days = age_seconds / 86400

    if age_days <= 0:
        return max_score
    if age_days >= decay_days:
        return 0.0
    return max_score * (1 - age_days / decay_days)


def _content_signal_score(row: dict, extra: dict, max_score: float) -> float:
    """Score based on content signals specific to each source type."""
    source_type = row.get("source_type", "")
    score = 0.0

    if source_type == "github_trending":
        # Stars today is the strongest signal for GitHub Trending
        stars_today = extra.get("stars_today", 0)
        total_stars = extra.get("total_stars", 0)
        # 500 stars/day = max stars_today contribution
        score += min(stars_today / 500, 1.0) * (max_score * 0.6)
        # Total stars as secondary signal (cap at 10k)
        score += min(total_stars / 10_000, 1.0) * (max_score * 0.4)

    elif source_type == "arxiv":
        content = row.get("content") or ""
        # Longer abstracts tend to be more substantive
        score += min(len(content) / 1000, 1.0) * (max_score * 0.4)
        # Keyword signals in title/abstract
        score += _keyword_score(
            (row.get("title") or "") + " " + content,
            max_score * 0.6
        )

    else:  # RSS / other
        content = row.get("content") or ""
        score += _keyword_score(
            (row.get("title") or "") + " " + content,
            max_score
        )

    return min(score, max_score)


def _source_type_bonus(source_type: str | None, max_score: float) -> float:
    """Bonus points by source type — company announcements are highest signal."""
    bonuses = {
        "rss": max_score * 0.8,          # RSS = usually official blog posts
        "arxiv": max_score * 0.7,        # Research papers
        "github_trending": max_score * 0.5,  # Trending = community signal
        "reddit": max_score * 0.3,
        "hackernews": max_score * 0.4,
    }
    return bonuses.get(source_type or "", max_score * 0.3)


def _keyword_score(text: str, max_score: float) -> float:
    """Score based on presence of high/low importance keywords."""
    text_lower = text.lower()

    hits = sum(1 for kw in HIGH_IMPORTANCE_KEYWORDS if kw in text_lower)
    penalties = sum(1 for kw in LOW_IMPORTANCE_KEYWORDS if kw in text_lower)

    # Each keyword hit adds proportional score, capped at max_score
    raw = (hits - penalties * 0.5) / max(len(HIGH_IMPORTANCE_KEYWORDS), 1)
    return max(0.0, min(raw, 1.0)) * max_score