"""
llm/summarize.py — select top articles from the DB and summarize them.

This is the Phase 4 orchestrator. It:
1. Pulls the top N articles per category from the DB (already ranked by Phase 3)
2. Calls an LLM to summarize each one, via a provider chain (Gemini, then
   Groq as fallback, then rule-based) — see llm/router.py
3. Writes the summary back to articles.summary in the DB
4. Returns the summarized articles grouped by category for the newsletter
   builder, plus whichever provider survived the run (so Phase 5's newsletter
   prose uses the same provider, not a different one)

Rate limiting: we add a small sleep between LLM calls to stay well within
free tier limits (this matters much more for Gemini's ~10-15 RPM than for
Groq's much higher daily caps, but it's a shared delay across whichever
provider is currently active).
"""

from __future__ import annotations

import json
import time

from config.loader import AppConfig
from database.database import db_session
from llm.common import fallback_summary
from llm.gemini import GeminiClient
from llm.groq_client import GroqClient
from llm.router import ProviderChain
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

# Delay between LLM calls — keeps us well under free tier RPM limits
INTER_CALL_DELAY_SECONDS = 4


def _build_provider_chain(config: AppConfig) -> ProviderChain:
    """
    Build the Gemini -> Groq fallback chain. Either provider is optional —
    a missing API key just means that provider is skipped when building the
    chain, not an error. If neither key is set, the chain is empty and
    active_client is always None (pure rule-based summaries), same as
    before this fallback existed.
    """
    clients: list[tuple[str, object]] = []

    if config.llm.api_key:
        clients.append(("gemini", GeminiClient(api_key=config.llm.api_key, model=config.llm.model)))
    else:
        logger.warning("GEMINI_API_KEY not set — Gemini unavailable for this run.")

    if config.llm.groq_api_key:
        clients.append(("groq", GroqClient(api_key=config.llm.groq_api_key, model=config.llm.groq_model)))
    else:
        logger.info("GROQ_API_KEY not set — no fallback provider configured for this run.")

    if not clients:
        logger.warning(
            "No LLM provider configured — all articles will use rule-based fallback summaries."
        )

    return ProviderChain(clients)


def run_summarization(config: AppConfig) -> tuple[dict[str, list[dict]], object, str | None]:
    """
    Select, summarize, and return articles grouped into newsletter sections.

    Returns:
        (grouped, active_client, active_provider_name)

        grouped: {
          "top_stories": [...],
          "research": [...],
          "tools": [...],
          "github": [...],
        }
        Each article dict has all DB fields plus a populated "summary" key.

        active_client: whichever provider's client object was still active
        when this function returned (could be the Gemini client, the Groq
        client, or None if every provider was exhausted this run). Callers
        (main.py) should pass this straight into build_markdown for Phase 5
        so the newsletter's prose uses the same provider/voice as the
        article summaries that made it into this run.

        active_provider_name: "gemini", "groq", or None — same info as
        active_client but human-readable, useful for logging.
    """
    chain = _build_provider_chain(config)
    db_path = config.database.path

    top_stories = _select_top_stories(db_path, MAX_PER_CATEGORY["top_stories"])
    research = _select_by_source_type(db_path, "arxiv", MAX_PER_CATEGORY["research"])
    # top_stories (source_type='rss') and research (source_type='arxiv') can't
    # overlap with each other by construction, but "tools" only filters by
    # category/tags — not a single source_type — so without this exclusion
    # an arXiv paper tagged open_source/python could be selected again here.
    already_selected_ids = {a["id"] for a in top_stories} | {a["id"] for a in research}
    tools = _select_tools(db_path, MAX_PER_CATEGORY["tools"], exclude_ids=already_selected_ids)
    github = _select_by_source_type(db_path, "github_trending", MAX_PER_CATEGORY["github"])

    grouped = {
        "top_stories": top_stories,
        "research":    research,
        "tools":       tools,
        "github":      github,
    }

    total = sum(len(v) for v in grouped.values())
    logger.info("Summarizing %d articles across %d sections (provider: %s)...",
                total, len(grouped), chain.active_name or "rule-based")

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

            client = chain.active_client
            if client:
                logger.info("[%s %d/%d] Summarizing via %s: %s",
                            section, i + 1, len(articles), chain.active_name, article["title"][:60])
                result, ok = client.summarize_article(
                    title=article["title"],
                    source=article["source"],
                    content=article.get("content") or "",
                )
                switched = chain.record_result(ok)
                if switched and chain.active_client:
                    # Don't leave this article on a rule-based summary just
                    # because it happened to be the one that tripped the
                    # circuit breaker — retry it immediately on whichever
                    # provider we just switched to.
                    logger.info("Retrying '%s' on %s.", article["title"][:60], chain.active_name)
                    result, ok = chain.active_client.summarize_article(
                        title=article["title"],
                        source=article["source"],
                        content=article.get("content") or "",
                    )
                    chain.record_result(ok)

                article["summary"] = result.get("summary", "")
                article["why_it_matters"] = result.get("why_it_matters", "")
                article["career_impact"] = result.get("career_impact", "medium")

                # Persist summary to DB so we don't re-summarize on next run
                _save_summary(db_path, article["id"], result)

                time.sleep(INTER_CALL_DELAY_SECONDS)
            else:
                # Every provider is either unconfigured or exhausted for
                # this run — use the rule-based fallback directly.
                result = fallback_summary(article["title"], article.get("content") or "")
                article["summary"] = result["summary"]
                article["why_it_matters"] = result["why_it_matters"]

    logger.info("Summarization complete. Final active provider: %s", chain.active_name or "rule-based")
    return grouped, chain.active_client, chain.active_name


# ── article selection queries ──────────────────────────────────────────────────

def _select_top_stories(db_path: str, limit: int, exclude_ids: set[str] | None = None) -> list[dict]:
    """Top articles from RSS/company sources by importance score."""
    exclude_clause, params = _exclude_ids_clause(exclude_ids)
    with db_session(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT id, title, url, source, source_type, content, summary,
                   category, tags, importance, extra, published_at
            FROM articles
            WHERE importance > 0
              AND source_type = 'rss'
              AND featured_at IS NULL
              {exclude_clause}
            ORDER BY importance DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _select_by_source_type(
    db_path: str, source_type: str, limit: int, exclude_ids: set[str] | None = None
) -> list[dict]:
    """Top articles of a specific source type by importance."""
    exclude_clause, params = _exclude_ids_clause(exclude_ids)
    with db_session(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT id, title, url, source, source_type, content, summary,
                   category, tags, importance, extra, published_at
            FROM articles
            WHERE importance > 0
              AND source_type = ?
              AND featured_at IS NULL
              {exclude_clause}
            ORDER BY importance DESC
            LIMIT ?
            """,
            (source_type, *params, limit),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _select_tools(db_path: str, limit: int, exclude_ids: set[str] | None = None) -> list[dict]:
    """
    New tools = high-importance open_source or python articles that aren't
    company announcements.

    exclude_ids matters here specifically: unlike top_stories (source_type =
    'rss') and research (source_type = 'arxiv'), this query doesn't pin down
    a single source_type — it only excludes github_trending — so an arXiv
    paper tagged open_source/python can otherwise match both the research
    query *and* this one, getting selected (and summarized, and billed)
    twice as two separate article instances. Real example from production:
    the same paper appeared in both "research" and "tools" in one run.
    """
    exclude_clause, params = _exclude_ids_clause(exclude_ids)
    with db_session(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT id, title, url, source, source_type, content, summary,
                   category, tags, importance, extra, published_at
            FROM articles
            WHERE importance > 0
              AND (category IN ('open_source', 'python', 'mlops')
                   OR tags LIKE '%open_source%'
                   OR tags LIKE '%python%')
              AND source_type != 'github_trending'
              AND featured_at IS NULL
              {exclude_clause}
            ORDER BY importance DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _exclude_ids_clause(exclude_ids: set[str] | None) -> tuple[str, list[str]]:
    """
    Build an "AND id NOT IN (?, ?, ...)" SQL fragment plus its params.
    Returns ("", []) for None/empty so callers can always splice the result
    in without a None-check at every call site.
    """
    if not exclude_ids:
        return "", []
    placeholders = ",".join("?" for _ in exclude_ids)
    return f"AND id NOT IN ({placeholders})", list(exclude_ids)


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