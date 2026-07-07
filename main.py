"""
AI Intelligence Agent — main entry point.

Full pipeline:
  Phase 1+2  Collect  (RSS, arXiv, GitHub Trending)
  Phase 3    Process  (clean, dedup, classify, rank)
  Phase 4    Summarize (Gemini 2.5 Flash)
  Phase 5    Publish  (Markdown + HTML newsletter)
"""

from __future__ import annotations

from datetime import datetime, timezone

from config.loader import load_config
from llm.summarize import mark_articles_featured, run_summarization
from newsletter.html import build_html
from newsletter.markdown import build_markdown
from processing.pipeline import run_processing
from scheduler.logging_setup import get_logger
from scheduler.weekly import run_collection

logger = get_logger(__name__)


def _week_label() -> str:
    now = datetime.now(timezone.utc)
    week_num = now.isocalendar()[1]
    return f"Week {week_num} - {now.year}"


def main() -> None:
    config = load_config()
    week_label = _week_label()
    logger.info("=== AI INTELLIGENCE AGENT — %s ===", week_label)

    # ── Phase 1+2: Collect ──────────────────────────────────────────────
    logger.info("--- PHASE 1+2: COLLECTION ---")
    collection_summary = run_collection(config)
    total_new = sum(s.get("new", 0) for s in collection_summary.values())
    total_found = sum(s.get("found", 0) for s in collection_summary.values())
    for source, stats in collection_summary.items():
        logger.info("  %-16s found=%-4d new=%-4d status=%s",
                    source, stats["found"], stats["new"], stats["status"])
    logger.info("Collection: %d found, %d new.", total_found, total_new)

    # ── Phase 3: Process ────────────────────────────────────────────────
    logger.info("--- PHASE 3: PROCESSING ---")
    proc = run_processing(config.database.path)
    logger.info("Processing: cleaned=%d | dedup_groups=%d | classified=%d | ranked=%d",
                proc["cleaned"], proc["dedup"]["groups_found"],
                proc["classified"], proc["ranked"])

    # ── Phase 4: Summarize ──────────────────────────────────────────────
    logger.info("--- PHASE 4: SUMMARIZATION ---")
    grouped = run_summarization(config)
    total_articles = sum(len(v) for v in grouped.values())
    logger.info("Summarized %d articles across %d sections.", total_articles, len(grouped))

    # ── Phase 5: Newsletter ─────────────────────────────────────────────
    logger.info("--- PHASE 5: NEWSLETTER ---")
    llm_client = None
    if config.llm.api_key:
        from llm.gemini import GeminiClient
        llm_client = GeminiClient(config.llm.api_key, config.llm.model)

    markdown = build_markdown(
        grouped=grouped,
        week_label=week_label,
        llm_client=llm_client,
        output_dir="output",
    )
    build_html(
        markdown_content=markdown,
        grouped=grouped,
        week_label=week_label,
        output_dir="output",
    )

    # Both files are written at this point — safe to stamp these articles as
    # "already sent" so next week's selection queries skip them, regardless
    # of how high their importance score still is. See llm/summarize.py.
    mark_articles_featured(config.database.path, grouped)

    logger.info("=== DONE. Check output/ for your newsletter. ===")


if __name__ == "__main__":
    main()