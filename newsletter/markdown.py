"""
newsletter/markdown.py — build the weekly Markdown newsletter.

Two modes:
1. LLM mode (api key present): pass the grouped articles to Gemini and let
   it write the full newsletter prose using the newsletter_prompt.
2. Template mode (no api key / LLM fallback): assemble the newsletter from
   a structured template using the pre-computed summaries. Produces a
   clean, readable result without needing any LLM call at all.

Template mode is the default fallback and also useful for testing — it
guarantees a newsletter is always produced, even if the Gemini API is down.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from config.prompts import newsletter_prompt
from scheduler.logging_setup import get_logger

logger = get_logger(__name__)


def build_markdown(
    grouped: dict[str, list[dict]],
    week_label: str,
    llm_client=None,
    output_dir: str | Path = "output",
) -> str:
    """
    Build the full Markdown newsletter and write it to output/.

    Args:
        grouped:    Article sections from llm/summarize.py
        week_label: e.g. "Week 27 - 2026"
        llm_client: Optional GeminiClient for LLM-written prose
        output_dir: Where to write the .md file

    Returns:
        The Markdown string.
    """
    if llm_client:
        markdown = _build_with_llm(grouped, week_label, llm_client)
        if markdown:
            logger.info("Newsletter built using LLM.")
        else:
            logger.warning("LLM newsletter generation returned empty — falling back to template.")
            markdown = _build_from_template(grouped, week_label)
    else:
        markdown = _build_from_template(grouped, week_label)
        logger.info("Newsletter built using template (no LLM client).")

    # Write to disk
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    filename = f"newsletter_{_week_filename(week_label)}.md"
    filepath = output_path / filename
    filepath.write_text(markdown, encoding="utf-8")
    logger.info("Markdown newsletter written to %s", filepath)

    return markdown


# ── LLM mode ─────────────────────────────────────────────────────────────────

def _build_with_llm(grouped: dict, week_label: str, llm_client) -> str:
    prompt = newsletter_prompt(
        week_label=week_label,
        top_stories=grouped.get("top_stories", []),
        research_papers=grouped.get("research", []),
        new_tools=grouped.get("tools", []),
        github_repos=grouped.get("github", []),
    )
    return llm_client.generate_text(prompt)


# ── Template mode ─────────────────────────────────────────────────────────────

def _build_from_template(grouped: dict[str, list[dict]], week_label: str) -> str:
    now = datetime.now(timezone.utc)
    lines: list[str] = []

    lines += [
        f"# AI Weekly Intelligence Report",
        f"",
        f"**{week_label}** · Generated {now.strftime('%Y-%m-%d')}",
        f"",
        "---",
        "",
    ]

    # Top Stories
    top = grouped.get("top_stories", [])
    if top:
        lines += ["## 🔥 Top Stories", ""]
        for a in top:
            lines += _format_story(a)
        lines += ["---", ""]

    # Research
    research = grouped.get("research", [])
    if research:
        lines += ["## 📄 Research Worth Reading", ""]
        for a in research:
            lines += _format_story(a)
        lines += ["---", ""]

    # Tools
    tools = grouped.get("tools", [])
    if tools:
        lines += ["## 🛠️ New Tools & Libraries", ""]
        for a in tools:
            lines += _format_story(a)
        lines += ["---", ""]

    # GitHub Trending
    github = grouped.get("github", [])
    if github:
        lines += ["## 📈 Trending on GitHub", ""]
        for repo in github:
            lines += _format_github_repo(repo)
        lines += ["---", ""]

    # Career Takeaways — generated from category distribution of top stories
    lines += ["## 🎯 Career Takeaways", ""]
    lines += _build_career_takeaways(grouped)
    lines += ["", "---", ""]

    # Reading time estimate
    total_articles = sum(len(v) for v in grouped.values())
    est_minutes = max(5, total_articles * 1)
    lines += [f"_Estimated reading time: {est_minutes} minutes_", ""]

    return "\n".join(lines)


def _format_story(article: dict) -> list[str]:
    title = article.get("title", "Untitled")
    url = article.get("url", "")
    source = article.get("source", "")
    summary = article.get("summary") or article.get("content", "")[:300]
    why = article.get("why_it_matters", "")
    importance = article.get("importance", 0)

    lines = [f"### [{title}]({url})"]
    lines.append(f"**Source:** {source} · **Score:** {importance:.0f}/100")
    lines.append("")
    if summary:
        lines.append(summary)
        lines.append("")
    if why:
        lines.append(f"> **Why it matters:** {why}")
        lines.append("")
    return lines


def _format_github_repo(repo: dict) -> list[str]:
    title = repo.get("title", "")
    url = repo.get("url", "")
    description = repo.get("content") or repo.get("summary", "")
    extra = repo.get("extra") or {}
    stars_today = extra.get("stars_today", 0)
    total_stars = extra.get("total_stars", 0)
    language = extra.get("language", "")

    meta_parts = []
    if language:
        meta_parts.append(f"**{language}**")
    if total_stars:
        meta_parts.append(f"⭐ {total_stars:,}")
    if stars_today:
        meta_parts.append(f"↑ {stars_today:,} this week")

    lines = [f"### [{title}]({url})"]
    if meta_parts:
        lines.append(" · ".join(meta_parts))
        lines.append("")
    if description:
        lines.append(description)
        lines.append("")
    return lines


def _build_career_takeaways(grouped: dict) -> list[str]:
    """Generate 2-3 career takeaway bullets from the article mix."""
    all_categories: list[str] = []
    for articles in grouped.values():
        for a in articles:
            cats = (a.get("tags") or a.get("category") or "").split(",")
            all_categories.extend(c.strip() for c in cats if c.strip())

    # Count category frequency
    from collections import Counter
    counts = Counter(all_categories)

    takeaways = []
    if counts.get("agents", 0) >= 2:
        takeaways.append(
            "> AI Agents are heavily featured this week — if you're not familiar with "
            "tool calling and agent orchestration frameworks (LangGraph, MCP), now is the time."
        )
    if counts.get("data_engineering", 0) >= 1:
        takeaways.append(
            "> Data Engineering is moving fast around open table formats. "
            "Apache Iceberg and DuckDB are worth hands-on time if you haven't tried them."
        )
    if counts.get("rag", 0) >= 1:
        takeaways.append(
            "> RAG systems are maturing — the gap between basic similarity search "
            "and production-grade retrieval is widening. Focus on reranking and evaluation."
        )
    if counts.get("llm", 0) >= 2:
        takeaways.append(
            "> Multiple LLM releases this week. Track benchmarks critically — "
            "marketing numbers and real-world performance often diverge."
        )
    if not takeaways:
        takeaways = [
            "> Stay current with arXiv cs.AI and cs.LG — the gap between "
            "paper publication and production adoption is shrinking.",
            "> GitHub Trending is a strong early signal for tools worth evaluating. "
            "High stars-per-day often means real practitioner demand.",
        ]

    return takeaways[:3]


# ── utilities ─────────────────────────────────────────────────────────────────

def _week_filename(week_label: str) -> str:
    """Convert 'Week 27 - 2026' → '2026-W27' for clean filenames."""
    import re
    m = re.search(r"Week\s+(\d+)\s*[-–]\s*(\d{4})", week_label)
    if m:
        return f"{m.group(2)}-W{int(m.group(1)):02d}"
    # Fallback: use date
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
