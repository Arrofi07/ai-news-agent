"""
RSS collector — pulls recent posts from company blogs and other RSS feeds
listed in config.yaml under sources.rss.feeds.

Uses feedparser when available (handles RSS/Atom variants, malformed XML,
encoding quirks robustly — this is the standard library for the job).
Falls back to a minimal stdlib XML parser if feedparser isn't installed,
so this module still degrades gracefully rather than hard-failing on import.
Run `uv sync` locally to get feedparser and always use the better path.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import requests

from collector.base import BaseCollector
from database.models import Article
from scheduler.logging_setup import get_logger

logger = get_logger(__name__)

try:
    import feedparser
    _HAS_FEEDPARSER = True
except ImportError:
    _HAS_FEEDPARSER = False


class RSSCollector(BaseCollector):
    source_type = "rss"

    def collect(self) -> list[Article]:
        articles: list[Article] = []
        if not self.config.rss.enabled:
            logger.info("RSS collection disabled in config, skipping.")
            return articles

        feeds = self.config.rss.feeds
        failures: list[str] = []

        for feed_cfg in feeds:
            try:
                entries = self._fetch_feed(feed_cfg.url)
            except Exception as e:
                logger.warning("Failed to fetch feed %s (%s): %s", feed_cfg.name, feed_cfg.url, e)
                failures.append(feed_cfg.name)
                continue

            for entry in entries:
                try:
                    article = self._entry_to_article(entry, feed_cfg)
                    if article:
                        articles.append(article)
                except Exception as e:
                    logger.warning("Skipping malformed entry from %s: %s", feed_cfg.name, e)

            # Be polite to remote servers between feeds.
            time.sleep(0.5)

        if feeds and len(failures) == len(feeds):
            # Every single feed failed — likely a network/DNS issue rather than
            # individual feeds being down. Surface as a failure rather than a
            # silent empty success.
            raise RuntimeError(f"All {len(feeds)} RSS feeds failed to fetch: {failures}")

        logger.info("RSS collector gathered %d articles from %d feeds (%d failed).",
                    len(articles), len(feeds), len(failures))
        return articles

    # -- fetching -----------------------------------------------------

    def _fetch_feed(self, url: str) -> list[dict]:
        headers = {"User-Agent": self.config.collection.user_agent}
        last_exc: Exception | None = None

        for attempt in range(self.config.collection.max_retries + 1):
            try:
                resp = requests.get(
                    url, headers=headers,
                    timeout=self.config.collection.request_timeout_seconds,
                )
                resp.raise_for_status()
                return self._parse_feed_bytes(resp.content)
            except Exception as e:
                last_exc = e
                if attempt < self.config.collection.max_retries:
                    time.sleep(self.config.collection.retry_backoff_seconds)

        assert last_exc is not None
        raise last_exc

    def _parse_feed_bytes(self, raw: bytes) -> list[dict]:
        if _HAS_FEEDPARSER:
            parsed = feedparser.parse(raw)
            return [self._normalize_feedparser_entry(e) for e in parsed.entries]
        return self._parse_with_stdlib(raw)

    @staticmethod
    def _normalize_feedparser_entry(entry) -> dict:
        published = None
        if getattr(entry, "published_parsed", None):
            published = datetime(
                *entry.published_parsed[:6], tzinfo=timezone.utc
            ).isoformat()
        return {
            "title": entry.get("title", "").strip(),
            "link": entry.get("link", "").strip(),
            "author": entry.get("author", None),
            "published": published,
            "summary": entry.get("summary", entry.get("description", "")),
        }

    @staticmethod
    def _parse_with_stdlib(raw: bytes) -> list[dict]:
        """Minimal RSS 2.0 / Atom fallback parser (no feedparser installed).
        Covers the common <item> (RSS) and <entry> (Atom) shapes — enough
        to keep this collector functional, not a full feedparser replacement."""
        entries: list[dict] = []
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            return entries

        ns = {"atom": "http://www.w3.org/2005/Atom"}

        # RSS 2.0
        for item in root.findall(".//item"):
            entries.append({
                "title": (item.findtext("title") or "").strip(),
                "link": (item.findtext("link") or "").strip(),
                "author": item.findtext("author"),
                "published": item.findtext("pubDate"),
                "summary": item.findtext("description") or "",
            })

        # Atom
        for entry in root.findall(".//atom:entry", ns):
            link_el = entry.find("atom:link", ns)
            link = link_el.get("href") if link_el is not None else ""
            entries.append({
                "title": (entry.findtext("atom:title", namespaces=ns) or "").strip(),
                "link": link,
                "author": entry.findtext("atom:author/atom:name", namespaces=ns),
                "published": entry.findtext("atom:updated", namespaces=ns),
                "summary": entry.findtext("atom:summary", namespaces=ns) or "",
            })

        return entries

    # -- normalization -------------------------------------------------

    def _entry_to_article(self, entry: dict, feed_cfg) -> Article | None:
        title = entry.get("title")
        link = entry.get("link")
        if not title or not link:
            return None

        return Article(
            title=title,
            url=link,
            source=feed_cfg.name,
            source_type=self.source_type,
            author=entry.get("author"),
            published_at=entry.get("published"),
            content=entry.get("summary", "")[:5000],  # cap raw content; full cleaning happens in processing/
            category=feed_cfg.category,
            tags=[feed_cfg.category],
        )
