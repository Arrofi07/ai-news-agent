"""
config/prompts.py — all LLM prompts in one place.

Keeping prompts separate from the code that calls them means you can tune
them without touching business logic, and diff prompt changes clearly in git.

Each prompt is a function so it can accept dynamic context (article content,
date, user preferences) while keeping the template readable.
"""

from __future__ import annotations


def article_summary_prompt(title: str, source: str, content: str) -> str:
    """Prompt for summarizing a single article into structured JSON."""
    return f"""You are an AI industry analyst writing for a technical audience of AI and Data Engineers.

Analyze this article and respond with a JSON object only — no markdown, no preamble.

Article title: {title}
Source: {source}
Content:
{content[:3000]}

Respond with exactly this JSON structure:
{{
  "summary": "2-3 sentence plain-English summary of what happened",
  "why_it_matters": "1-2 sentences on why this is significant for AI/Data engineers",
  "career_impact": "one of: high | medium | low",
  "category": "one of: llm | agents | rag | data_engineering | mlops | open_source | research | safety | company | python | general",
  "tags": ["tag1", "tag2"],
  "estimated_read_minutes": 2
}}"""


def newsletter_prompt(
    week_label: str,
    top_stories: list[dict],
    research_papers: list[dict],
    new_tools: list[dict],
    github_repos: list[dict],
) -> str:
    """Prompt for generating the final weekly newsletter in Markdown."""

    def fmt_articles(articles: list[dict]) -> str:
        lines = []
        for a in articles:
            summary = a.get("summary") or a.get("content", "")[:200]
            why = a.get("why_it_matters", "")
            lines.append(
                f"- **{a['title']}** ({a['source']})\n"
                f"  Summary: {summary}\n"
                f"  Why it matters: {why}\n"
                f"  URL: {a['url']}"
            )
        return "\n\n".join(lines)

    return f"""You are writing a weekly AI & Data Engineering newsletter called "AI Weekly Intelligence Report".

Week: {week_label}
Audience: AI engineers, Data engineers, ML practitioners — technical but not academic.
Tone: Clear, direct, practical. No hype. No filler sentences. Every sentence earns its place.

You have been given pre-selected and pre-summarized articles. Your job is to:
1. Write a cohesive newsletter in Markdown
2. Group related stories naturally
3. Add a "Career Takeaways" section with 2-3 concrete, actionable insights
4. Keep each story summary tight (2-4 sentences)
5. Add an estimated total reading time at the bottom

## TOP STORIES TO INCLUDE:
{fmt_articles(top_stories)}

## RESEARCH PAPERS:
{fmt_articles(research_papers)}

## NEW TOOLS & LIBRARIES:
{fmt_articles(new_tools)}

## TRENDING ON GITHUB:
{fmt_articles(github_repos)}

Write the full newsletter now in Markdown. Use these exact section headers:
# AI Weekly Intelligence Report
## 🔥 Top Stories
## 📄 Research Worth Reading
## 🛠️ New Tools & Libraries
## 📈 Trending on GitHub
## 🎯 Career Takeaways

End with: _Estimated reading time: X minutes_"""