"""
AI Intelligence Agent — main entry point.

Full pipeline:
  Phase 1+2  Collect  (RSS, arXiv, GitHub Trending)
  Phase 3    Process  (clean, dedup, classify, rank)
  Phase 4    Summarize (Gemini, falling back to Groq, then rule-based)
  Phase 5    Publish  (Markdown + HTML newsletter + email delivery)
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from config.loader import load_config
from newsletter.email import send_newsletter
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
    grouped, llm_client, provider_name = run_summarization(config)
    total_articles = sum(len(v) for v in grouped.values())
    logger.info("Summarized %d articles across %d sections (final provider: %s).",
                total_articles, len(grouped), provider_name or "rule-based")

    # ── Phase 5: Newsletter ─────────────────────────────────────────────
    logger.info("--- PHASE 5: NEWSLETTER ---")
    llm_client = None
    if config.llm.api_key:
        from llm.gemini import GeminiClient
        llm_client = GeminiClient(config.llm.api_key, config.llm.model)
    # Deliberately reuse whichever client survived Phase 4 rather than
    # constructing a fresh Gemini-only client here: if Gemini got rate
    # limited mid-run and the chain switched to Groq, the newsletter's
    # LLM-written prose should come from that same provider — otherwise
    # Phase 5 could retry Gemini right when we just learned it's failing.
    markdown = build_markdown(
        grouped=grouped,
        week_label=week_label,
        llm_client=llm_client,
        output_dir="output",
    )
    
    html = build_html(
        markdown_content=markdown,
        grouped=grouped,
        week_label=week_label,
        output_dir="output",
    )

    # ── Email delivery ──────────────────────────────────────────────────
    resend_api_key = os.environ.get("RESEND_API_KEY")
    resend_to = os.environ.get("RESEND_TO_EMAIL")

    if resend_api_key and resend_to:
        logger.info("--- EMAIL DELIVERY ---")
        success = send_newsletter(
            html_content=html,
            markdown_content=markdown,
            week_label=week_label,
            api_key=resend_api_key,
            to_email=resend_to,
        )
        if success:
            logger.info("Newsletter delivered to %s", resend_to)
        else:
            logger.warning("Email delivery failed — check logs above. Newsletter still in output/.")
    else:
        logger.info("Email delivery skipped (RESEND_API_KEY or RESEND_TO_EMAIL not set).")

    # Both files are written at this point — safe to stamp these articles as
    # "already sent" so next week's selection queries skip them, regardless
    # of how high their importance score still is. See llm/summarize.py.
    mark_articles_featured(config.database.path, grouped)

    logger.info("=== DONE. Check output/ for your newsletter. ===")


if __name__ == "__main__":
    main()