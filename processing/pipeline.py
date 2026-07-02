"""
processing/pipeline.py — runs the full processing pipeline on collected articles.

Order matters:
  1. Clean     — normalize content first so dedup/ranking work on clean text
  2. Deduplicate — group near-identical stories before scoring
  3. Classify  — assign categories (needed for newsletter section grouping)
  4. Rank      — score remaining (non-duplicate) articles

This module is the Phase 3 equivalent of scheduler/weekly.py for Phase 2.
It's called from main.py after collection completes.
"""

from __future__ import annotations

from database.database import db_session
from processing.cleaner import clean_content, clean_title, clean_url
from processing.classifier import run_classification
from processing.deduplicate import run_deduplication
from processing.ranking import run_ranking
from scheduler.logging_setup import get_logger

logger = get_logger(__name__)


def run_processing(db_path: str) -> dict:
    """
    Run the full processing pipeline.
    Returns a summary dict with counts from each step.
    """
    summary = {}

    # Step 1: Clean content in-place for all articles that haven't been cleaned yet.
    clean_result = _run_cleaning(db_path)
    summary["cleaned"] = clean_result

    # Step 2: Deduplicate — mark near-identical stories.
    dedup_result = run_deduplication(db_path)
    summary["dedup"] = dedup_result

    # Step 3: Classify — assign category and tags.
    classify_result = run_classification(db_path)
    summary["classified"] = classify_result["classified"]

    # Step 4: Rank — score every non-duplicate article.
    rank_result = run_ranking(db_path)
    summary["ranked"] = rank_result["ranked"]

    logger.info(
        "Processing complete: cleaned=%d dedup_groups=%d classified=%d ranked=%d",
        summary["cleaned"],
        summary["dedup"]["groups_found"],
        summary["classified"],
        summary["ranked"],
    )
    return summary


def _run_cleaning(db_path: str) -> int:
    """Clean title, content, and URL for all articles. Returns count of articles cleaned."""
    with db_session(db_path) as conn:
        rows = conn.execute(
            "SELECT id, title, content, url FROM articles"
        ).fetchall()

    updates = []
    for row in rows:
        clean_t = clean_title(row["title"])
        clean_c = clean_content(row["content"])
        clean_u = clean_url(row["url"])
        updates.append((clean_t, clean_c, clean_u, row["id"]))

    with db_session(db_path) as conn:
        conn.executemany(
            "UPDATE articles SET title = ?, content = ?, url = ? WHERE id = ?",
            updates,
        )

    logger.info("Cleaning: processed %d articles.", len(updates))
    return len(updates)