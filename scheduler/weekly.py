"""
Collection run orchestrator.

Runs every enabled collector, normalizes failures so one broken source
doesn't kill the whole run, upserts results into SQLite, and records a
collection_runs row per source for observability (useful once this is
running unattended on GitHub Actions).
"""

from __future__ import annotations

import traceback
from datetime import datetime, timezone

from collector.arxiv import ArxivCollector
from collector.base import BaseCollector
from collector.github import GitHubTrendingCollector
from collector.rss import RSSCollector
from config.loader import AppConfig, load_config
from database.database import db_session, init_db
from database.models import upsert_article
from scheduler.logging_setup import get_logger

logger = get_logger(__name__)

ALL_COLLECTORS: list[type[BaseCollector]] = [
    RSSCollector,
    ArxivCollector,
    GitHubTrendingCollector,
]


def run_collection(config: AppConfig) -> dict[str, dict]:
    """
    Run every collector, save results, and return a per-source summary, e.g.:
        {"rss": {"found": 12, "new": 9}, "arxiv": {"found": 40, "new": 40}, ...}
    """
    init_db(config.database.path)
    summary: dict[str, dict] = {}

    for collector_cls in ALL_COLLECTORS:
        collector = collector_cls(config)
        source_type = collector.source_type
        started_at = datetime.now(timezone.utc).isoformat()

        try:
            articles = collector.collect()
        except Exception as e:
            logger.error("Collector '%s' crashed: %s\n%s", source_type, e, traceback.format_exc())
            summary[source_type] = {"found": 0, "new": 0, "status": "failed", "error": str(e)}
            _record_run(config, source_type, started_at, 0, 0, "failed", str(e))
            continue

        new_count = 0
        with db_session(config.database.path) as conn:
            for article in articles:
                try:
                    if upsert_article(conn, article):
                        new_count += 1
                except Exception as e:
                    logger.warning("Failed to save article '%s': %s", article.title[:60], e)

        summary[source_type] = {"found": len(articles), "new": new_count, "status": "success"}
        _record_run(config, source_type, started_at, len(articles), new_count, "success", None)
        logger.info("[%s] found=%d new=%d", source_type, len(articles), new_count)

    return summary


def _record_run(
    config: AppConfig,
    source_type: str,
    started_at: str,
    found: int,
    new: int,
    status: str,
    error_message: str | None,
) -> None:
    with db_session(config.database.path) as conn:
        conn.execute(
            """
            INSERT INTO collection_runs
                (source_type, started_at, finished_at, articles_found, articles_new, status, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_type, started_at, datetime.now(timezone.utc).isoformat(),
                found, new, status, error_message,
            ),
        )


if __name__ == "__main__":
    cfg = load_config()
    result = run_collection(cfg)
    print(result)
