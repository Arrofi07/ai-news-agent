"""
Automated tests for Phase 1+2: project foundation and collectors.

These tests use only stdlib (unittest, unittest.mock) plus the packages
available at `uv sync` time — no pytest plugins required for the basics.
All network calls are mocked; tests run offline in CI with no API keys.

Run with:
    python3 -m pytest tests/test_phase1_2.py -v
    # or without pytest installed:
    python3 -m unittest discover tests
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── fixtures ────────────────────────────────────────────────────────────────

RSS_2_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Test Feed</title>
  <item>
    <title>GPT-X Released</title>
    <link>https://openai.com/blog/gpt-x</link>
    <author>OpenAI</author>
    <pubDate>Thu, 25 Jun 2026 10:00:00 GMT</pubDate>
    <description>Our most capable model yet.</description>
  </item>
  <item>
    <title>Safety Research Update</title>
    <link>https://openai.com/blog/safety-june-2026</link>
    <pubDate>Mon, 22 Jun 2026 09:00:00 GMT</pubDate>
    <description>Latest safety work.</description>
  </item>
</channel></rss>"""

ATOM_FIXTURE = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Claude Update</title>
    <link href="https://anthropic.com/news/claude-update" rel="alternate"/>
    <author><name>Anthropic</name></author>
    <updated>2026-06-24T12:00:00Z</updated>
    <summary>New Claude capabilities.</summary>
  </entry>
</feed>"""

ARXIV_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2606.12345v1</id>
    <title>Multi-Agent LLM Coordination</title>
    <summary>A new framework for multi-agent coordination.</summary>
    <published>2026-06-26T17:00:00Z</published>
    <author><name>Jane Doe</name></author>
    <author><name>John Smith</name></author>
    <link href="http://arxiv.org/abs/2606.12345v1" rel="alternate" type="text/html"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2606.12346v1</id>
    <title>RAG Scaling Laws</title>
    <summary>How RAG performance scales with corpus size.</summary>
    <published>2026-06-25T09:30:00Z</published>
    <author><name>Alice Lee</name></author>
    <link href="http://arxiv.org/abs/2606.12346v1" rel="alternate" type="text/html"/>
  </entry>
</feed>"""

GITHUB_TRENDING_FIXTURE = """<!DOCTYPE html><html><body>
  <article class="Box-row">
    <h2><a href="/owner1/cool-llm-agent">owner1 / cool-llm-agent</a></h2>
    <p>An LLM agent framework.</p>
    <span itemprop="programmingLanguage">Python</span>
    <a href="/owner1/cool-llm-agent/stargazers">4,521</a>
    <span class="d-inline-block float-sm-right">312 stars today</span>
  </article>
  <article class="Box-row">
    <h2><a href="/owner2/vector-db-lite">owner2 / vector-db-lite</a></h2>
    <p>A lightweight vector database.</p>
    <span itemprop="programmingLanguage">Python</span>
    <a href="/owner2/vector-db-lite/stargazers">1,203</a>
    <span class="d-inline-block float-sm-right">89 stars today</span>
  </article>
</body></html>"""


def _make_mock_response(content=None, text=None) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = lambda: None
    if content is not None:
        resp.content = content
    if text is not None:
        resp.text = text
    return resp


# ── config tests ─────────────────────────────────────────────────────────────

class TestConfigLoader(unittest.TestCase):

    def test_loads_default_config(self):
        from config.loader import load_config
        cfg = load_config()
        self.assertEqual(cfg.llm.model, "gemini-2.5-flash")
        self.assertIsInstance(cfg.rss.feeds, list)
        self.assertGreater(len(cfg.rss.feeds), 0)
        self.assertIn("cs.CL", cfg.arxiv.categories)
        self.assertIn("python", cfg.github_trending.languages)

    def test_db_absolute_path(self):
        from config.loader import load_config
        cfg = load_config()
        self.assertTrue(cfg.database.absolute_path.is_absolute())

    def test_llm_api_key_reads_from_env(self):
        from config.loader import load_config
        import os
        cfg = load_config()
        os.environ["GEMINI_API_KEY"] = "test-key-123"
        self.assertEqual(cfg.llm.api_key, "test-key-123")
        del os.environ["GEMINI_API_KEY"]

    def test_rss_feed_config_shape(self):
        from config.loader import load_config
        cfg = load_config()
        for feed in cfg.rss.feeds:
            self.assertTrue(feed.name)
            self.assertTrue(feed.url.startswith("http"))
            self.assertTrue(feed.category)


# ── database tests ────────────────────────────────────────────────────────────

class TestDatabase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")

    def tearDown(self):
        Path(self.tmp).unlink(missing_ok=True)

    def test_init_creates_tables(self):
        from database.database import init_db, get_connection
        init_db(self.tmp)
        conn = get_connection(self.tmp)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        self.assertIn("articles", tables)
        self.assertIn("newsletters", tables)
        self.assertIn("collection_runs", tables)

    def test_init_is_idempotent(self):
        from database.database import init_db
        init_db(self.tmp)
        init_db(self.tmp)  # should not raise

    def test_upsert_new_article(self):
        from database.database import init_db, db_session
        from database.models import Article, upsert_article
        init_db(self.tmp)
        a = Article(
            title="Test Article", url="https://example.com/test",
            source="Test", source_type="rss",
        )
        with db_session(self.tmp) as conn:
            is_new = upsert_article(conn, a)
        self.assertTrue(is_new)

    def test_upsert_same_url_updates_not_duplicates(self):
        from database.database import init_db, db_session, get_connection
        from database.models import Article, upsert_article
        init_db(self.tmp)
        url = "https://example.com/same-url"
        a1 = Article(title="Original", url=url, source="S", source_type="rss")
        a2 = Article(title="Updated Title", url=url, source="S", source_type="rss")
        with db_session(self.tmp) as conn:
            upsert_article(conn, a1)
        with db_session(self.tmp) as conn:
            is_new = upsert_article(conn, a2)
        self.assertFalse(is_new)
        conn = get_connection(self.tmp)
        rows = conn.execute("SELECT COUNT(*) FROM articles WHERE url=?", (url,)).fetchone()
        conn.close()
        self.assertEqual(rows[0], 1)  # still exactly 1 row

    def test_article_id_is_stable(self):
        from database.models import Article
        url = "https://example.com/stable"
        a1 = Article(title="T1", url=url, source="S", source_type="rss")
        a2 = Article(title="T2 - different title", url=url, source="S", source_type="rss")
        self.assertEqual(a1.id, a2.id, "ID must be URL-derived, not title-derived")

    def test_article_extra_field_round_trips_as_json(self):
        from database.database import init_db, db_session, get_connection
        from database.models import Article, upsert_article
        init_db(self.tmp)
        a = Article(
            title="Repo", url="https://github.com/owner/repo",
            source="GitHub Trending", source_type="github_trending",
            extra={"stars_today": 312, "total_stars": 4521, "language": "Python"},
        )
        with db_session(self.tmp) as conn:
            upsert_article(conn, a)
        conn = get_connection(self.tmp)
        row = conn.execute("SELECT extra FROM articles WHERE url=?", (a.url,)).fetchone()
        conn.close()
        extra = json.loads(row["extra"])
        self.assertEqual(extra["stars_today"], 312)

    def test_tags_stored_as_comma_string(self):
        from database.database import init_db, db_session, get_connection
        from database.models import Article, upsert_article
        init_db(self.tmp)
        a = Article(
            title="Paper", url="https://arxiv.org/abs/2606.12345",
            source="arXiv", source_type="arxiv",
            tags=["cs.CL", "cs.AI"],
        )
        with db_session(self.tmp) as conn:
            upsert_article(conn, a)
        conn = get_connection(self.tmp)
        row = conn.execute("SELECT tags FROM articles WHERE url=?", (a.url,)).fetchone()
        conn.close()
        self.assertEqual(row["tags"], "cs.CL,cs.AI")


# ── RSS collector tests ───────────────────────────────────────────────────────

class TestRSSCollector(unittest.TestCase):

    def _make_collector(self):
        from config.loader import load_config
        from collector.rss import RSSCollector
        return RSSCollector(load_config())

    def test_parses_rss_2_fixture(self):
        from collector.rss import RSSCollector
        entries = RSSCollector._parse_with_stdlib(RSS_2_FIXTURE)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["title"], "GPT-X Released")
        self.assertEqual(entries[0]["link"], "https://openai.com/blog/gpt-x")
        self.assertIsNotNone(entries[0]["published"])

    def test_parses_atom_fixture(self):
        from collector.rss import RSSCollector
        entries = RSSCollector._parse_with_stdlib(ATOM_FIXTURE)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["title"], "Claude Update")
        self.assertIn("anthropic.com", entries[0]["link"])

    def test_gracefully_handles_malformed_xml(self):
        from collector.rss import RSSCollector
        entries = RSSCollector._parse_with_stdlib(b"<not valid xml <<>>")
        self.assertEqual(entries, [])

    def test_collect_returns_articles_when_network_succeeds(self):
        c = self._make_collector()
        with patch("requests.get", return_value=_make_mock_response(content=RSS_2_FIXTURE)), \
             patch("time.sleep"):
            articles = c.collect()
        self.assertGreater(len(articles), 0)
        for a in articles:
            self.assertTrue(a.title)
            self.assertTrue(a.url.startswith("http"))
            self.assertEqual(a.source_type, "rss")

    def test_collect_skips_individual_failed_feed_and_continues(self):
        c = self._make_collector()
        call_count = {"n": 0}

        def selective_fail(url, *args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise ConnectionError("First feed down")
            return _make_mock_response(content=RSS_2_FIXTURE)

        with patch("requests.get", side_effect=selective_fail), patch("time.sleep"):
            articles = c.collect()
        self.assertGreater(len(articles), 0, "Should still get articles from remaining feeds")

    def test_collect_raises_when_all_feeds_fail(self):
        c = self._make_collector()
        with patch("requests.get", side_effect=ConnectionError("all down")), \
             patch("time.sleep"):
            with self.assertRaises(RuntimeError):
                c.collect()

    def test_entry_with_missing_title_is_skipped(self):
        from collector.rss import RSSCollector
        entries = RSSCollector._parse_with_stdlib(
            b"<rss><channel><item><link>https://x.com/a</link></item></channel></rss>"
        )
        from config.loader import load_config
        from config.loader import RSSFeedConfig
        c = RSSCollector(load_config())
        feed_cfg = RSSFeedConfig(name="X", url="https://x.com", category="test")
        result = c._entry_to_article(entries[0] if entries else {"title": "", "link": "https://x.com/a"}, feed_cfg)
        self.assertIsNone(result)


# ── arXiv collector tests ──────────────────────────────────────────────────────

class TestArxivCollector(unittest.TestCase):

    def _make_collector(self):
        from config.loader import load_config
        from collector.arxiv import ArxivCollector
        return ArxivCollector(load_config())

    def test_parse_atom_extracts_title_link_authors(self):
        from collector.arxiv import ArxivCollector
        entries = ArxivCollector._parse_atom(ARXIV_FIXTURE)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["title"], "Multi-Agent LLM Coordination")
        self.assertIn("2606.12345", entries[0]["link"])
        self.assertEqual(entries[0]["authors"], ["Jane Doe", "John Smith"])
        self.assertEqual(entries[0]["published"], "2026-06-26T17:00:00Z")

    def test_author_truncation_with_et_al(self):
        from collector.arxiv import ArxivCollector
        xml = b"""<feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <title>Big Paper</title>
            <id>http://arxiv.org/abs/2606.00001</id>
            <published>2026-06-01T00:00:00Z</published>
            <author><name>A</name></author>
            <author><name>B</name></author>
            <author><name>C</name></author>
            <author><name>D</name></author>
            <link href="http://arxiv.org/abs/2606.00001" rel="alternate" type="text/html"/>
          </entry>
        </feed>"""
        entries = ArxivCollector._parse_atom(xml)
        c = ArxivCollector.__new__(ArxivCollector)
        article = c._entry_to_article(entries[0], "cs.AI")
        self.assertIn("et al.", article.author)
        self.assertNotIn("D", article.author)

    def test_collect_returns_papers_when_network_succeeds(self):
        c = self._make_collector()
        with patch("requests.get", return_value=_make_mock_response(content=ARXIV_FIXTURE)), \
             patch("time.sleep"):
            articles = c.collect()
        self.assertGreater(len(articles), 0)
        self.assertTrue(all(a.source_type == "arxiv" for a in articles))
        self.assertTrue(all(a.category == "research" for a in articles))

    def test_collect_raises_when_all_categories_fail(self):
        c = self._make_collector()
        with patch("requests.get", side_effect=ConnectionError("down")), \
             patch("time.sleep"):
            with self.assertRaises(RuntimeError):
                c.collect()

    def test_invalid_published_date_is_set_to_none(self):
        from collector.arxiv import ArxivCollector
        c = ArxivCollector.__new__(ArxivCollector)
        entry = {
            "title": "T", "link": "http://arxiv.org/abs/1",
            "published": "not-a-date", "authors": [],
        }
        article = c._entry_to_article(entry, "cs.AI")
        self.assertIsNone(article.published_at)


# ── GitHub Trending collector tests ────────────────────────────────────────────

class TestGitHubTrendingCollector(unittest.TestCase):

    def _make_collector(self):
        from config.loader import load_config
        from collector.github import GitHubTrendingCollector
        return GitHubTrendingCollector(load_config())

    def test_parse_extracts_repos(self):
        from collector.github import GitHubTrendingCollector
        repos = GitHubTrendingCollector._parse_trending_html(GITHUB_TRENDING_FIXTURE)
        self.assertEqual(len(repos), 2)
        self.assertEqual(repos[0]["repo_path"], "owner1/cool-llm-agent")
        self.assertEqual(repos[0]["stars_today"], 312)
        self.assertEqual(repos[0]["total_stars"], 4521)
        self.assertEqual(repos[0]["language"], "Python")

    def test_parse_handles_comma_formatted_numbers(self):
        from collector.github import GitHubTrendingCollector
        repos = GitHubTrendingCollector._parse_trending_html(GITHUB_TRENDING_FIXTURE)
        self.assertIsInstance(repos[0]["total_stars"], int)

    def test_parse_returns_empty_on_unexpected_markup(self):
        from collector.github import GitHubTrendingCollector
        repos = GitHubTrendingCollector._parse_trending_html("<html><body>nothing here</body></html>")
        self.assertEqual(repos, [])

    def test_collect_returns_articles_when_network_succeeds(self):
        c = self._make_collector()
        with patch("requests.get", return_value=_make_mock_response(text=GITHUB_TRENDING_FIXTURE)), \
             patch("time.sleep"):
            articles = c.collect()
        self.assertGreater(len(articles), 0)
        for a in articles:
            self.assertTrue(a.url.startswith("https://github.com/"))
            self.assertEqual(a.source_type, "github_trending")
            self.assertIn("stars_today", a.extra)

    def test_article_url_is_full_github_url(self):
        from collector.github import GitHubTrendingCollector
        c = GitHubTrendingCollector.__new__(GitHubTrendingCollector)
        article = c._repo_to_article({"repo_path": "owner/myrepo", "description": "x"})
        self.assertEqual(article.url, "https://github.com/owner/myrepo")


# ── end-to-end orchestrator tests ─────────────────────────────────────────────

class TestOrchestratorE2E(unittest.TestCase):

    def setUp(self):
        self.tmp_db = tempfile.mktemp(suffix=".db")

    def tearDown(self):
        Path(self.tmp_db).unlink(missing_ok=True)

    def _run(self, fake_get_fn):
        from config.loader import load_config
        from scheduler.weekly import run_collection
        cfg = load_config()
        cfg.database.path = self.tmp_db
        with patch("requests.get", side_effect=fake_get_fn), patch("time.sleep"):
            return run_collection(cfg)

    def _all_good_fake_get(self, url, *args, **kwargs):
        if "export.arxiv.org" in url:
            return _make_mock_response(content=ARXIV_FIXTURE)
        if "github.com/trending" in url:
            return _make_mock_response(text=GITHUB_TRENDING_FIXTURE)
        return _make_mock_response(content=RSS_2_FIXTURE)

    def test_all_collectors_succeed(self):
        summary = self._run(self._all_good_fake_get)
        self.assertEqual(summary["rss"]["status"], "success")
        self.assertEqual(summary["arxiv"]["status"], "success")
        self.assertEqual(summary["github_trending"]["status"], "success")

    def test_articles_are_saved_to_db(self):
        from database.database import get_connection
        self._run(self._all_good_fake_get)
        conn = get_connection(self.tmp_db)
        count = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        conn.close()
        self.assertGreater(count, 0)

    def test_collection_runs_are_recorded(self):
        from database.database import get_connection
        self._run(self._all_good_fake_get)
        conn = get_connection(self.tmp_db)
        runs = conn.execute("SELECT source_type, status FROM collection_runs").fetchall()
        conn.close()
        statuses = {r["source_type"]: r["status"] for r in runs}
        self.assertEqual(statuses.get("rss"), "success")
        self.assertEqual(statuses.get("arxiv"), "success")

    def test_one_collector_down_others_still_run(self):
        def arxiv_down(url, *args, **kwargs):
            if "export.arxiv.org" in url:
                raise ConnectionError("arxiv down")
            return self._all_good_fake_get(url, *args, **kwargs)

        summary = self._run(arxiv_down)
        self.assertEqual(summary["arxiv"]["status"], "failed")
        self.assertEqual(summary["rss"]["status"], "success")
        self.assertEqual(summary["github_trending"]["status"], "success")

    def test_deduplication_across_runs(self):
        from database.database import get_connection
        # Run twice with identical data — row count must not double
        self._run(self._all_good_fake_get)
        count_after_first = get_connection(self.tmp_db).execute(
            "SELECT COUNT(*) FROM articles"
        ).fetchone()[0]

        self._run(self._all_good_fake_get)
        count_after_second = get_connection(self.tmp_db).execute(
            "SELECT COUNT(*) FROM articles"
        ).fetchone()[0]

        self.assertEqual(
            count_after_first, count_after_second,
            "Running twice with identical URLs must not create duplicate rows"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
