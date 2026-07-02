"""
Entry point for the AI Intelligence Agent.

Phase 1+2: collection (RSS, arXiv, GitHub Trending)
Phase 3:   processing (clean, dedup, classify, rank)
Phase 4+:  LLM summarization and newsletter generation (coming soon)
"""

from __future__ import annotations

from config.loader import load_config
from processing.pipeline import run_processing
from scheduler.logging_setup import get_logger
from scheduler.weekly import run_collection

logger = get_logger(__name__)


def main() -> None:
    config = load_config()

    # ── Phase 1+2: Collect ──────────────────────────────────────────────
    logger.info("=== COLLECTION ===")
    collection_summary = run_collection(config)

    total_found = sum(s.get("found", 0) for s in collection_summary.values())
    total_new = sum(s.get("new", 0) for s in collection_summary.values())
    for source_type, stats in collection_summary.items():
        logger.info("  %-16s found=%-4d new=%-4d status=%s",
                    source_type, stats.get("found", 0),
                    stats.get("new", 0), stats.get("status"))
    logger.info("Collection total: %d found, %d new.", total_found, total_new)

    # ── Phase 3: Process ────────────────────────────────────────────────
    logger.info("=== PROCESSING ===")
    processing_summary = run_processing(config.database.path)
    logger.info(
        "Processing done: cleaned=%d | dedup_groups=%d | classified=%d | ranked=%d",
        processing_summary["cleaned"],
        processing_summary["dedup"]["groups_found"],
        processing_summary["classified"],
        processing_summary["ranked"],
    )

    # ── Preview: top 10 articles by importance ──────────────────────────
    _print_top_articles(config.database.path, n=10)


def _print_top_articles(db_path: str, n: int = 10) -> None:
    """Print the top-ranked articles so you can visually verify ranking quality."""
    from database.database import db_session
    with db_session(db_path) as conn:
        rows = conn.execute(
            """
            SELECT title, source, category, importance
            FROM articles
            WHERE importance > 0
            ORDER BY importance DESC
            LIMIT ?
            """,
            (n,),
        ).fetchall()

    logger.info("=== TOP %d ARTICLES BY IMPORTANCE ===", n)
    for i, row in enumerate(rows, 1):
        logger.info(
            "  %2d. [%5.1f] [%-16s] [%-14s] %s",
            i, row["importance"], row["source"][:16],
            row["category"][:14], row["title"][:70],
        )

if __name__ == "__main__":
    main()
