"""
GitHub Trending collector.

GitHub does not expose an official API for the trending page, so this
scrapes https://github.com/trending/<language>?since=<period> with
BeautifulSoup. This is the standard, widely-used approach (the same one
every "github-trending-api" wrapper project uses under the hood).

Caveat: HTML scraping is inherently fragile — if GitHub changes their
markup, this breaks silently (returns fewer/no results) rather than
loudly. We log a warning if a page yields zero repos so it's visible
in run logs rather than failing silently downstream.
"""

from __future__ import annotations

import re
import time

import requests
from bs4 import BeautifulSoup

from collector.base import BaseCollector
from database.models import Article
from scheduler.logging_setup import get_logger

logger = get_logger(__name__)

GITHUB_TRENDING_URL = "https://github.com/trending/{language}"


class GitHubTrendingCollector(BaseCollector):
    source_type = "github_trending"

    def collect(self) -> list[Article]:
        articles: list[Article] = []
        if not self.config.github_trending.enabled:
            logger.info("GitHub Trending collection disabled in config, skipping.")
            return articles

        languages = self.config.github_trending.languages or [""]  # "" = all languages
        for language in languages:
            try:
                repos = self._fetch_trending(language)
            except Exception as e:
                logger.warning("Failed to fetch GitHub Trending for '%s': %s", language or "all", e)
                continue

            if not repos:
                logger.warning(
                    "GitHub Trending page for '%s' returned 0 repos — "
                    "page markup may have changed.", language or "all"
                )

            for repo in repos[: self.config.github_trending.max_repos]:
                try:
                    article = self._repo_to_article(repo)
                    if article:
                        articles.append(article)
                except Exception as e:
                    logger.warning("Skipping malformed trending repo entry: %s", e)

            time.sleep(1)

        logger.info("GitHub Trending collector gathered %d repos across %d language(s).",
                    len(articles), len(languages))
        return articles

    # -- fetching ---------------------------------------------------------

    def _fetch_trending(self, language: str) -> list[dict]:
        url = GITHUB_TRENDING_URL.format(language=language)
        params = {"since": self.config.github_trending.since}
        headers = {"User-Agent": self.config.collection.user_agent}
        last_exc: Exception | None = None

        for attempt in range(self.config.collection.max_retries + 1):
            try:
                resp = requests.get(
                    url, params=params, headers=headers,
                    timeout=self.config.collection.request_timeout_seconds,
                )
                resp.raise_for_status()
                return self._parse_trending_html(resp.text)
            except Exception as e:
                last_exc = e
                if attempt < self.config.collection.max_retries:
                    time.sleep(self.config.collection.retry_backoff_seconds)

        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _parse_trending_html(html: str) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        repos = []

        for article_tag in soup.select("article.Box-row"):
            link_tag = article_tag.select_one("h2 a")
            if not link_tag:
                continue

            repo_path = link_tag.get("href", "").strip("/")  # e.g. "owner/repo"
            if not repo_path:
                continue

            description_tag = article_tag.select_one("p")
            description = description_tag.get_text(strip=True) if description_tag else ""

            language_tag = article_tag.select_one("[itemprop='programmingLanguage']")
            language = language_tag.get_text(strip=True) if language_tag else None

            stars_today_tag = article_tag.select_one("span.d-inline-block.float-sm-right")
            stars_today = 0
            if stars_today_tag:
                match = re.search(r"([\d,]+)", stars_today_tag.get_text())
                if match:
                    stars_today = int(match.group(1).replace(",", ""))

            total_stars_tag = article_tag.select_one("a[href$='/stargazers']")
            total_stars = 0
            if total_stars_tag:
                match = re.search(r"([\d,]+)", total_stars_tag.get_text())
                if match:
                    total_stars = int(match.group(1).replace(",", ""))

            repos.append({
                "repo_path": repo_path,
                "description": description,
                "language": language,
                "stars_today": stars_today,
                "total_stars": total_stars,
            })

        return repos

    # -- normalization ------------------------------------------------------

    def _repo_to_article(self, repo: dict) -> Article | None:
        repo_path = repo.get("repo_path")
        if not repo_path:
            return None

        return Article(
            title=repo_path,
            url=f"https://github.com/{repo_path}",
            source="GitHub Trending",
            source_type=self.source_type,
            content=repo.get("description") or "",
            category="open_source",
            tags=[t for t in [repo.get("language")] if t],
            extra={
                "stars_today": repo.get("stars_today", 0),
                "total_stars": repo.get("total_stars", 0),
                "language": repo.get("language"),
            },
        )
