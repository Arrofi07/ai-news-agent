"""
llm/summarize.py — select top articles from the DB and summarize them with Gemini.

This is the Phase 4 orchestrator. It:
1. Pulls the top N articles per category from the DB (already ranked by Phase 3)
2. Calls Gemini to summarize each one
3. Writes the summary back to articles.summary in the DB
4. Returns the summarized articles grouped by category for the newsletter builder

Rate limiting: we add a small sleep between Gemini calls to stay well within
the free tier limits (15 RPM on Gemini 2.5 Flash free tier).
"""

from __future__ import annotations

import json
import time

from config.loader import AppConfig
from database.database import db_session
from llm.gemini import GeminiClient
from scheduler.logging_setup import get_logger

logger = get_logger(__name__)

# How many articles to summarize per category.
# Keeping this tight controls LLM cost and newsletter length.
MAX_PER_CATEGORY = {
    "top_stories": 5,    # Best articles across all RSS/company sources
    "research":    3,    # arXiv papers
    "tools":       4,    # New tools, libraries, open source
    "github":      5,    # GitHub Trending repos
}

# Delay between Gemini calls — keeps us well under 15 RPM free tier limit
INTER_CALL_DELAY_SECONDS = 4


def run_summarization(config: AppConfig) -> dict[str, list[dict]]:
    """
    Select, summarize, and return articles grouped into newsletter sections.

    Returns:
        {
          "top_stories": [...],
          "research": [...],
          "tools": [...],
          "github": [...],
        }
    Each article dict has all DB fields plus a populated "summary" key.
    """
    api_key = config.llm.api_key
    if not api_key:
        logger.warning(
            "GEMINI_API_KEY not set — skipping LLM summarization. "
            "Articles will use rule-based fallback summaries."
        )
        client = None
    else:
        client = GeminiClient(api_key=api_key, model=config.llm.model)

    db_path = config.database.path

    grouped = {
        "top_stories": _select_top_stories(db_path, MAX_PER_CATEGORY["top_stories"]),
        "research":    _select_by_source_type(db_path, "arxiv", MAX_PER_CATEGORY["research"]),
        "tools":       _select_tools(db_path, MAX_PER_CATEGORY["tools"]),
        "github":      _select_by_source_type(db_path, "github_trending", MAX_PER_CATEGORY["github"]),
    }

    total = sum(len(v) for v in grouped.values())
    logger.info("Summarizing %d articles across %d sections...", total, len(grouped))

    for section, articles in grouped.items():
        for i, article in enumerate(articles):
            # Skip GitHub repos — their description is already short and clear;
            # LLM summarization adds no value for a one-line description.
            if article.get("source_type") == "github_trending":
                article["summary"] = article.get("content") or article.get("title")
                article["why_it_matters"] = _github_why_it_matters(article)
                continue

            existing_summary = article.get("summary")
            if existing_summary:
                # Already summarized from a previous run — skip to save API calls
                logger.debug("Skipping already-summarized article: %s", article["title"][:60])
                continue

            if client:
                logger.info("[%s %d/%d] Summarizing: %s",
                            section, i + 1, len(articles), article["title"][:60])
                result = client.summarize_article(
                    title=article["title"],
                    source=article["source"],
                    content=article.get("content") or "",
                )
                article["summary"] = result.get("summary", "")
                article["why_it_matters"] = result.get("why_it_matters", "")
                article["career_impact"] = result.get("career_impact", "medium")

                # Persist summary to DB so we don't re-summarize on next run
                _save_summary(db_path, article["id"], result)

                time.sleep(INTER_CALL_DELAY_SECONDS)
            else:
                # No API key — use fallback
                from llm.gemini import _fallback_summary
                result = _fallback_summary(article["title"], article.get("content") or "")
                article["summary"] = result["summary"]
                article["why_it_matters"] = result["why_it_matters"]

    logger.info("Summarization complete.")
    return grouped


# ── article selection queries ──────────────────────────────────────────────────

def _select_top_stories(db_path: str, limit: int) -> list[dict]:
    """Top articles from RSS/company sources by importance score."""
    with db_session(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, title, url, source, source_type, content, summary,
                   category, tags, importance, extra, published_at
            FROM articles
            WHERE importance > 0
              AND source_type = 'rss'
              AND featured_at IS NULL
            ORDER BY importance DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _select_by_source_type(db_path: str, source_type: str, limit: int) -> list[dict]:
    """Top articles of a specific source type by importance."""
    with db_session(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, title, url, source, source_type, content, summary,
                   category, tags, importance, extra, published_at
            FROM articles
            WHERE importance > 0
              AND source_type = ?
              AND featured_at IS NULL
            ORDER BY importance DESC
            LIMIT ?
            """,
            (source_type, limit),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _select_tools(db_path: str, limit: int) -> list[dict]:
    """New tools = high-importance open_source or python articles that aren't company announcements."""
    with db_session(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, title, url, source, source_type, content, summary,
                   category, tags, importance, extra, published_at
            FROM articles
            WHERE importance > 0
              AND (category IN ('open_source', 'python', 'mlops')
                   OR tags LIKE '%open_source%'
                   OR tags LIKE '%python%')
              AND source_type != 'github_trending'
              AND featured_at IS NULL
            ORDER BY importance DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def mark_articles_featured(db_path: str, grouped: dict[str, list[dict]]) -> None:
    """
    Stamp every article that went into this week's newsletter with
    featured_at = now, so future runs never re-select it (see the
    featured_at IS NULL filter in the selection queries above).

    Deliberately called by the caller (main.py) only *after* build_markdown /
    build_html have both succeeded — if we marked articles featured before
    confirming the newsletter was actually produced, a crash mid-pipeline
    could permanently "burn" articles that were never actually sent anywhere.
    """
    article_ids = [a["id"] for articles in grouped.values() for a in articles if a.get("id")]
    if not article_ids:
        return

    with db_session(db_path) as conn:
        # executemany over a list of 1-tuples — sqlite3 has no clean "IN (...)"
        # with a variable-length list, and this table only sees a few dozen
        # updates a week so a loop is simpler than building a dynamic query.
        conn.executemany(
            "UPDATE articles SET featured_at = datetime('now') WHERE id = ?",
            [(aid,) for aid in article_ids],
        )
    logger.info("Marked %d articles as featured (won't be re-selected in future runs).", len(article_ids))


# ── helpers ───────────────────────────────────────────────────────────────────

def _row_to_dict(row) -> dict:
    d = dict(row)
    try:
        d["extra"] = json.loads(d.get("extra") or "{}")
    except (json.JSONDecodeError, TypeError):
        d["extra"] = {}
    return d


def _save_summary(db_path: str, article_id: str, result: dict) -> None:
    """Persist LLM-generated summary fields back to the DB."""
    with db_session(db_path) as conn:
        conn.execute(
            """
            UPDATE articles SET
                summary  = ?,
                category = COALESCE(NULLIF(?, ''), category),
                tags     = COALESCE(NULLIF(?, ''), tags)
            WHERE id = ?
            """,
            (
                result.get("summary", ""),
                result.get("category", ""),
                ",".join(result.get("tags", [])),
                article_id,
            ),
        )


def _github_why_it_matters(article: dict) -> str:
    """Generate a why-it-matters blurb for GitHub repos from their star data."""
    extra = article.get("extra") or {}
    stars_today = extra.get("stars_today", 0)
    total_stars = extra.get("total_stars", 0)
    lang = extra.get("language", "")

    parts = []
    if stars_today >= 200:
        parts.append(f"Gaining {stars_today:,} stars today — strong community momentum.")
    elif stars_today > 0:
        parts.append(f"{stars_today} stars today.")
    if total_stars >= 1000:
        parts.append(f"{total_stars:,} total stars.")
    if lang:
        parts.append(f"Written in {lang}.")
    return " ".join(parts) or "Trending this week."