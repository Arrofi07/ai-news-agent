"""
arXiv collector — pulls recent papers from configured categories
(cs.CL, cs.AI, cs.LG, cs.MA, etc.) via arXiv's public Atom API.

API docs: https://info.arxiv.org/help/api/user-manual.html
No API key required. We deliberately use the raw Atom feed via `requests`
+ stdlib XML parsing rather than a third-party `arxiv` package — one less
dependency for a very small, stable API surface.
"""

from __future__ import annotations

import time
from datetime import datetime
from xml.etree import ElementTree as ET

import requests

from collector.base import BaseCollector
from database.models import Article
from scheduler.logging_setup import get_logger

logger = get_logger(__name__)

ARXIV_API_URL = "http://export.arxiv.org/api/query"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


class ArxivCollector(BaseCollector):
    source_type = "arxiv"

    def collect(self) -> list[Article]:
        articles: list[Article] = []
        if not self.config.arxiv.enabled:
            logger.info("arXiv collection disabled in config, skipping.")
            return articles

        categories = self.config.arxiv.categories
        failures: list[str] = []

        for category in categories:
            try:
                entries = self._fetch_category(category)
            except Exception as e:
                logger.warning("Failed to fetch arXiv category %s: %s", category, e)
                failures.append(category)
                continue

            for entry in entries:
                try:
                    article = self._entry_to_article(entry, category)
                    if article:
                        articles.append(article)
                except Exception as e:
                    logger.warning("Skipping malformed arXiv entry in %s: %s", category, e)

            # arXiv asks for at least a few seconds between API calls.
            time.sleep(3)

        if categories and len(failures) == len(categories):
            # Every category failed (e.g. arXiv API outage) — surface this as a
            # real failure rather than a silent "success" with 0 articles, so
            # the orchestrator's collection_runs log reflects what happened.
            raise RuntimeError(
                f"All {len(categories)} arXiv categories failed to fetch: {failures}"
            )

        logger.info("arXiv collector gathered %d papers across %d categories (%d failed).",
                    len(articles), len(categories), len(failures))
        return articles

    # -- fetching -------------------------------------------------------

    def _fetch_category(self, category: str) -> list[dict]:
        params = {
            "search_query": f"cat:{category}",
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": self.config.arxiv.max_results_per_category,
        }
        headers = {"User-Agent": self.config.collection.user_agent}
        last_exc: Exception | None = None

        for attempt in range(self.config.collection.max_retries + 1):
            try:
                resp = requests.get(
                    ARXIV_API_URL, params=params, headers=headers,
                    timeout=self.config.collection.request_timeout_seconds,
                )
                resp.raise_for_status()
                return self._parse_atom(resp.content)
            except Exception as e:
                last_exc = e
                if attempt < self.config.collection.max_retries:
                    time.sleep(self.config.collection.retry_backoff_seconds)

        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _parse_atom(raw: bytes) -> list[dict]:
        root = ET.fromstring(raw)
        results = []
        for entry in root.findall("atom:entry", ATOM_NS):
            authors = [
                (a.findtext("atom:name", namespaces=ATOM_NS) or "").strip()
                for a in entry.findall("atom:author", ATOM_NS)
            ]
            link = ""
            for link_el in entry.findall("atom:link", ATOM_NS):
                if link_el.get("type") == "text/html" or link_el.get("rel") == "alternate":
                    link = link_el.get("href", "")
                    break
            if not link:
                link = entry.findtext("atom:id", namespaces=ATOM_NS) or ""

            results.append({
                "title": (entry.findtext("atom:title", namespaces=ATOM_NS) or "").strip().replace("\n", " "),
                "summary": (entry.findtext("atom:summary", namespaces=ATOM_NS) or "").strip().replace("\n", " "),
                "link": link,
                "published": entry.findtext("atom:published", namespaces=ATOM_NS),
                "authors": authors,
            })
        return results

    # -- normalization ---------------------------------------------------

    def _entry_to_article(self, entry: dict, category: str) -> Article | None:
        title = entry.get("title")
        link = entry.get("link")
        if not title or not link:
            return None

        published = entry.get("published")
        if published:
            try:
                # arXiv gives e.g. 2026-06-20T14:32:10Z — already ISO-ish, just validate.
                datetime.fromisoformat(published.replace("Z", "+00:00"))
            except ValueError:
                published = None

        authors = entry.get("authors", [])
        author_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")

        return Article(
            title=title,
            url=link,
            source="arXiv",
            source_type=self.source_type,
            author=author_str or None,
            published_at=published,
            content=entry.get("summary", "")[:5000],
            category="research",
            tags=[category],
            extra={"arxiv_category": category},
        )
