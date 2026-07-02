"""
Configuration loader for the AI Intelligence Agent.

Loads config/config.yaml and exposes it as a typed, dot-accessible object
instead of raw nested dicts, so the rest of the codebase doesn't sprinkle
config["sources"]["rss"]["feeds"] everywhere.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"


@dataclass
class RSSFeedConfig:
    name: str
    url: str
    category: str = "general"


@dataclass
class RSSConfig:
    enabled: bool = True
    feeds: list[RSSFeedConfig] = field(default_factory=list)


@dataclass
class ArxivConfig:
    enabled: bool = True
    categories: list[str] = field(default_factory=list)
    max_results_per_category: int = 20


@dataclass
class GitHubTrendingConfig:
    enabled: bool = True
    languages: list[str] = field(default_factory=list)
    since: str = "weekly"
    max_repos: int = 25


@dataclass
class CollectionConfig:
    request_timeout_seconds: int = 15
    user_agent: str = "ai-news-agent/0.1"
    max_retries: int = 2
    retry_backoff_seconds: int = 2


@dataclass
class DatabaseConfig:
    path: str = "data/news.db"

    @property
    def absolute_path(self) -> Path:
        p = Path(self.path)
        if p.is_absolute():
            return p
        return PROJECT_ROOT / p


@dataclass
class NewsletterConfig:
    max_articles: int = 15


@dataclass
class LLMConfig:
    provider: str = "gemini"
    model: str = "gemini-2.5-flash"

    @property
    def api_key(self) -> str | None:
        # Never store API keys in config.yaml. Always read from environment.
        env_var = {
            "gemini": "GEMINI_API_KEY",
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
        }.get(self.provider, "GEMINI_API_KEY")
        return os.environ.get(env_var)


@dataclass
class AppConfig:
    database: DatabaseConfig
    rss: RSSConfig
    arxiv: ArxivConfig
    github_trending: GitHubTrendingConfig
    collection: CollectionConfig
    newsletter: NewsletterConfig
    llm: LLMConfig
    raw: dict[str, Any] = field(default_factory=dict)


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    """Load and parse config.yaml into a typed AppConfig object."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found at {path}. "
            f"Did you mean to copy config/config.example.yaml?"
        )

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    sources = raw.get("sources", {})

    rss_raw = sources.get("rss", {})
    rss = RSSConfig(
        enabled=rss_raw.get("enabled", True),
        feeds=[RSSFeedConfig(**feed) for feed in rss_raw.get("feeds", [])],
    )

    arxiv_raw = sources.get("arxiv", {})
    arxiv = ArxivConfig(
        enabled=arxiv_raw.get("enabled", True),
        categories=arxiv_raw.get("categories", []),
        max_results_per_category=arxiv_raw.get("max_results_per_category", 20),
    )

    gh_raw = sources.get("github_trending", {})
    github_trending = GitHubTrendingConfig(
        enabled=gh_raw.get("enabled", True),
        languages=gh_raw.get("languages", []),
        since=gh_raw.get("since", "weekly"),
        max_repos=gh_raw.get("max_repos", 25),
    )

    collection_raw = raw.get("collection", {})
    collection = CollectionConfig(
        request_timeout_seconds=collection_raw.get("request_timeout_seconds", 15),
        user_agent=collection_raw.get("user_agent", "ai-news-agent/0.1"),
        max_retries=collection_raw.get("max_retries", 2),
        retry_backoff_seconds=collection_raw.get("retry_backoff_seconds", 2),
    )

    database = DatabaseConfig(path=raw.get("database", {}).get("path", "data/news.db"))

    newsletter = NewsletterConfig(
        max_articles=raw.get("newsletter", {}).get("max_articles", 15)
    )

    llm_raw = raw.get("llm", {})
    llm = LLMConfig(
        provider=llm_raw.get("provider", "gemini"),
        model=llm_raw.get("model", "gemini-2.5-flash"),
    )

    return AppConfig(
        database=database,
        rss=rss,
        arxiv=arxiv,
        github_trending=github_trending,
        collection=collection,
        newsletter=newsletter,
        llm=llm,
        raw=raw,
    )
