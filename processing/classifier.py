"""
processing/classifier.py — assign topic categories to articles.

Uses keyword matching on title + content. No ML model needed for MVP —
keyword rules are fast, debuggable, and accurate enough for these
well-defined technical topics. The category field is used by the
newsletter builder to group stories into sections.

Each article can match multiple categories (tags list), but gets one
primary category for ordering purposes.

Phase 4 note: when LLM summarization arrives, the LLM can refine or
override these classifications. Keyword matching is the bootstrap layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from database.database import db_session
from scheduler.logging_setup import get_logger

logger = get_logger(__name__)


@dataclass
class CategoryRule:
    name: str           # e.g. "llm"
    display: str        # e.g. "LLMs & Foundation Models"
    keywords: list[str]
    priority: int = 5   # Higher = checked first; wins ties


CATEGORIES: list[CategoryRule] = [
    CategoryRule(
        name="agents",
        display="AI Agents",
        keywords=[
            "agent", "multi-agent", "agentic", "tool use", "tool calling",
            "mcp", "langgraph", "autogen", "crew", "autonomous",
            "function calling", "orchestrat",
        ],
        priority=10,
    ),
    CategoryRule(
        name="llm",
        display="LLMs & Foundation Models",
        keywords=[
            "llm", "large language model", "foundation model", "transformer",
            "gpt", "claude", "gemini", "llama", "mistral", "qwen",
            "fine-tun", "rlhf", "instruction tuning", "pretrain",
            "context window", "tokeniz",
        ],
        priority=9,
    ),
    CategoryRule(
        name="rag",
        display="RAG & Knowledge Systems",
        keywords=[
            "rag", "retrieval", "retrieval-augmented", "vector", "embedding",
            "semantic search", "knowledge base", "chunking", "rerank",
            "pgvector", "chroma", "pinecone", "weaviate", "qdrant",
        ],
        priority=8,
    ),
    CategoryRule(
        name="data_engineering",
        display="Data Engineering",
        keywords=[
            "data engineering", "data pipeline", "etl", "elt",
            "apache spark", "apache flink", "kafka", "airflow",
            "dbt", "iceberg", "delta lake", "hudi", "lakehouse",
            "data warehouse", "snowflake", "databricks", "bigquery",
            "duckdb", "polars",
        ],
        priority=8,
    ),
    CategoryRule(
        name="mlops",
        display="MLOps & Infrastructure",
        keywords=[
            "mlops", "model serving", "deployment", "inference",
            "kubernetes", "docker", "mlflow", "kubeflow", "bentoml",
            "ray", "triton", "onnx", "quantiz", "pruning",
            "monitoring", "drift", "a/b test",
        ],
        priority=7,
    ),
    CategoryRule(
        name="open_source",
        display="Open Source",
        keywords=[
            "open source", "open-source", "open weight", "github",
            "hugging face", "apache", "mit license", "community",
        ],
        priority=6,
    ),
    CategoryRule(
        name="research",
        display="Research & Papers",
        keywords=[
            "paper", "arxiv", "research", "benchmark", "dataset",
            "evaluation", "ablation", "experiment", "novel",
        ],
        priority=5,
    ),
    CategoryRule(
        name="safety",
        display="AI Safety & Ethics",
        keywords=[
            "safety", "alignment", "red team", "jailbreak", "hallucin",
            "bias", "fairness", "responsible ai", "ethics", "governance",
            "regulation", "policy",
        ],
        priority=9,
    ),
    CategoryRule(
        name="company",
        display="Company Updates",
        keywords=[
            "launch", "release", "announce", "partnership", "acqui",
            "funding", "series", "valuation", "openai", "anthropic",
            "google", "deepmind", "meta", "nvidia", "microsoft",
        ],
        priority=4,
    ),
    CategoryRule(
        name="python",
        display="Python & Tools",
        keywords=[
            "python", "pip", "pypi", "library", "sdk", "api client",
            "langchain", "pydantic", "fastapi", "asyncio",
        ],
        priority=4,
    ),
]

# Sort by priority descending so higher-priority rules win first
CATEGORIES.sort(key=lambda c: c.priority, reverse=True)


def classify_article(title: str, content: str) -> tuple[str, list[str]]:
    """
    Return (primary_category, [all_matched_tags]) for an article.

    The primary category is the highest-priority match. All matches
    are returned as tags. Falls back to "general" if nothing matches.
    """
    text = (title + " " + (content or "")).lower()
    matched: list[CategoryRule] = []

    for rule in CATEGORIES:
        if any(_keyword_matches(kw, text) for kw in rule.keywords):
            matched.append(rule)

    if not matched:
        return "general", ["general"]

    primary = matched[0].name  # highest priority match
    tags = [r.name for r in matched]
    return primary, tags


def run_classification(db_path: str) -> dict:
    """Classify all articles in the DB and update their category and tags fields."""
    with db_session(db_path) as conn:
        rows = conn.execute(
            "SELECT id, title, content, source_type FROM articles WHERE importance >= 0"
        ).fetchall()

    updates: list[tuple[str, str, str]] = []
    for row in rows:
        row = dict(row)
        # GitHub Trending repos always get "open_source" as primary
        if row["source_type"] == "github_trending":
            category, tags = "open_source", ["open_source"]
        else:
            category, tags = classify_article(
                row.get("title") or "",
                row.get("content") or "",
            )
        updates.append((category, ",".join(tags), row["id"]))

    with db_session(db_path) as conn:
        conn.executemany(
            "UPDATE articles SET category = ?, tags = ? WHERE id = ?",
            updates,
        )

    logger.info("Classification: categorized %d articles.", len(updates))
    return {"classified": len(updates)}


# ── helpers ──────────────────────────────────────────────────────────────────

def _keyword_matches(keyword: str, text: str) -> bool:
    """Match keyword as a whole word/phrase (avoids 'agent' matching 'management')."""
    # For multi-word phrases, exact substring match is fine.
    if " " in keyword or len(keyword) <= 3:
        return keyword in text
    # For single words, use word-boundary match.
    return bool(re.search(rf"\b{re.escape(keyword)}", text))