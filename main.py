"""
Entry point for the AI Intelligence Agent.

Phase 1+2 scope: run the collection pipeline (RSS, arXiv, GitHub Trending)
and report a per-source summary. Cleaning, dedup, ranking, classification,
LLM summarization, and newsletter generation are not wired in yet — those
land in Phases 3-5.

Usage:
    uv run main.py
"""

from __future__ import annotations

from config.loader import load_config
from scheduler.logging_setup import get_logger
from scheduler.weekly import run_collection

logger = get_logger(__name__)


def main() -> None:
    config = load_config()
    logger.info("Starting collection run...")
    summary = run_collection(config)

    logger.info("Collection run complete. Summary:")
    total_found, total_new = 0, 0
    for source_type, stats in summary.items():
        logger.info("  %-16s found=%-4d new=%-4d status=%s",
                    source_type, stats.get("found", 0), stats.get("new", 0), stats.get("status"))
        total_found += stats.get("found", 0)
        total_new += stats.get("new", 0)
    logger.info("Total: %d articles found, %d new.", total_found, total_new)


if __name__ == "__main__":
    main()
