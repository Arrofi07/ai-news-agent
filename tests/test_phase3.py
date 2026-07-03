"""
Tests for Phase 3: cleaning, deduplication, classification, and ranking.
All offline — no network calls, no API keys.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta


def _make_db_with_articles(articles: list[dict]) -> str:
    """Create a temporary DB populated with the given article dicts."""
    from database.database import init_db, db_session
    path = tempfile.mktemp(suffix=".db")
    init_db(path)
    with db_session(path) as conn:
        for a in articles:
            conn.execute(
                """INSERT OR IGNORE INTO articles
                   (id, title, url, source, source_type, content,
                    published_at, category, tags, importance, extra)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    a.get("id", a["url"][-12:]),
                    a["title"], a["url"], a.get("source", "Test"),
                    a.get("source_type", "rss"),
                    a.get("content", ""),
                    a.get("published_at", datetime.now(timezone.utc).isoformat()),
                    a.get("category", "general"),
                    a.get("tags", ""),
                    a.get("importance", 0),
                    json.dumps(a.get("extra", {})),
                ),
            )
    return path


# ── cleaner tests ─────────────────────────────────────────────────────────────

class TestCleaner(unittest.TestCase):

    def test_strips_html_tags(self):
        from processing.cleaner import clean_content
        result = clean_content("<p>Hello <b>world</b></p>")
        self.assertNotIn("<p>", result)
        self.assertNotIn("<b>", result)
        self.assertIn("Hello", result)
        self.assertIn("world", result)

    def test_decodes_html_entities(self):
        from processing.cleaner import clean_content
        result = clean_content("OpenAI &amp; Anthropic are both &lt;great&gt;")
        self.assertIn("&", result)
        self.assertNotIn("&amp;", result)
        self.assertNotIn("&lt;", result)

    def test_removes_boilerplate_read_more(self):
        from processing.cleaner import clean_content
        result = clean_content("Interesting article content.\nRead more\nMore content.")
        self.assertNotIn("Read more", result)
        self.assertIn("Interesting article content", result)

    def test_collapses_excessive_whitespace(self):
        from processing.cleaner import clean_content
        result = clean_content("Hello     world\n\n\n\nNext paragraph")
        self.assertNotIn("     ", result)
        self.assertNotIn("\n\n\n", result)

    def test_empty_input_returns_empty_string(self):
        from processing.cleaner import clean_content
        self.assertEqual(clean_content(None), "")
        self.assertEqual(clean_content(""), "")

    def test_clean_url_removes_utm_params(self):
        from processing.cleaner import clean_url
        url = "https://openai.com/blog/gpt-x?utm_source=twitter&utm_campaign=launch"
        result = clean_url(url)
        self.assertNotIn("utm_source", result)
        self.assertNotIn("utm_campaign", result)
        self.assertIn("openai.com/blog/gpt-x", result)

    def test_clean_url_preserves_meaningful_params(self):
        from processing.cleaner import clean_url
        url = "https://arxiv.org/abs/2606.12345?version=2"
        result = clean_url(url)
        self.assertIn("version=2", result)

    def test_clean_url_handles_none(self):
        from processing.cleaner import clean_url
        self.assertEqual(clean_url(None), "")

    def test_clean_title_strips_source_suffix(self):
        from processing.cleaner import clean_title
        result = clean_title("GPT-X Released | OpenAI Blog")
        self.assertIn("GPT-X Released", result)
        # The pipe and source suffix should be removed
        self.assertNotIn("|", result)

    def test_clean_title_decodes_entities(self):
        from processing.cleaner import clean_title
        result = clean_title("Q&amp;A: How We Built GPT-X")
        self.assertIn("Q&A", result)
        self.assertNotIn("&amp;", result)


# ── deduplication tests ───────────────────────────────────────────────────────

class TestDeduplication(unittest.TestCase):

    def setUp(self):
        self.dbs: list[str] = []

    def tearDown(self):
        for p in self.dbs:
            Path(p).unlink(missing_ok=True)

    def _db(self, articles):
        path = _make_db_with_articles(articles)
        self.dbs.append(path)
        return path

    def test_jaccard_identical_titles(self):
        from processing.deduplicate import _tokenize, _jaccard
        a = _tokenize("OpenAI releases GPT-X model")
        b = _tokenize("OpenAI releases GPT-X model")
        self.assertEqual(_jaccard(a, b), 1.0)

    def test_jaccard_similar_titles(self):
        from processing.deduplicate import _tokenize, _jaccard
        a = _tokenize("OpenAI releases GPT-X")
        b = _tokenize("OpenAI launches GPT-X model")
        sim = _jaccard(a, b)
        # 2 shared tokens (openai, gpt) out of 5 union tokens = 0.4.
        # Threshold is 0.5, so these won't auto-group — but the similarity
        # is meaningfully non-zero, which is what this test verifies.
        self.assertGreaterEqual(sim, 0.3)

    def test_jaccard_different_titles(self):
        from processing.deduplicate import _tokenize, _jaccard
        a = _tokenize("OpenAI releases GPT-X")
        b = _tokenize("Python 3.13 adds new features")
        sim = _jaccard(a, b)
        self.assertLess(sim, 0.2)

    def test_groups_near_duplicate_articles(self):
        from processing.deduplicate import run_deduplication, _group_duplicates, _load_articles
        db = self._db([
            {"id": "aaa", "title": "OpenAI releases GPT-X model",
             "url": "https://openai.com/gpt-x", "source": "OpenAI", "source_type": "rss"},
            {"id": "bbb", "title": "OpenAI launches GPT-X model",
             "url": "https://techcrunch.com/gpt-x", "source": "TechCrunch", "source_type": "rss"},
            {"id": "ccc", "title": "Python 3.13 released with new parser",
             "url": "https://python.org/news", "source": "Python", "source_type": "rss"},
        ])
        result = run_deduplication(db)
        self.assertEqual(result["groups_found"], 1)
        self.assertEqual(result["duplicates_marked"], 1)

    def test_unique_articles_not_grouped(self):
        from processing.deduplicate import run_deduplication
        db = self._db([
            {"id": "aaa", "title": "OpenAI releases GPT-X",
             "url": "https://openai.com/a", "source": "OpenAI"},
            {"id": "bbb", "title": "Google DeepMind publishes AlphaFold 4",
             "url": "https://deepmind.com/b", "source": "DeepMind"},
            {"id": "ccc", "title": "Python 3.13 released",
             "url": "https://python.org/c", "source": "Python"},
        ])
        result = run_deduplication(db)
        self.assertEqual(result["groups_found"], 0)
        self.assertEqual(result["duplicates_marked"], 0)

    def test_canonical_prefers_primary_source(self):
        from processing.deduplicate import _pick_canonical, ArticleRow
        openai = ArticleRow("a", "OpenAI releases X", "https://openai.com", "OpenAI", "rss", "Long content here yes indeed", 0)
        techcrunch = ArticleRow("b", "OpenAI releases X", "https://techcrunch.com", "TechCrunch", "rss", "Short", 0)
        canonical = _pick_canonical([techcrunch, openai])
        self.assertEqual(canonical.source, "OpenAI")


# ── classifier tests ──────────────────────────────────────────────────────────

class TestClassifier(unittest.TestCase):

    def test_classifies_agent_content(self):
        from processing.classifier import classify_article
        cat, tags = classify_article("Building a multi-agent LLM orchestration system", "")
        self.assertEqual(cat, "agents")
        self.assertIn("agents", tags)

    def test_classifies_llm_content(self):
        from processing.classifier import classify_article
        cat, tags = classify_article("Scaling laws for large language models", "")
        self.assertEqual(cat, "llm")

    def test_classifies_data_engineering(self):
        from processing.classifier import classify_article
        cat, tags = classify_article("Apache Iceberg vs Delta Lake: the definitive comparison", "")
        self.assertIn("data_engineering", tags)

    def test_classifies_rag_content(self):
        from processing.classifier import classify_article
        cat, tags = classify_article("Improving RAG with better chunking strategies", "")
        self.assertEqual(cat, "rag")

    def test_classifies_safety_content(self):
        from processing.classifier import classify_article
        cat, tags = classify_article("Red team evaluation of frontier AI safety", "")
        self.assertIn("safety", tags)

    def test_multiple_categories_possible(self):
        from processing.classifier import classify_article
        _, tags = classify_article(
            "Open source LLM agent with RAG capabilities",
            "Uses vector embeddings and tool calling"
        )
        self.assertGreater(len(tags), 1)

    def test_unknown_content_falls_back_to_general(self):
        from processing.classifier import classify_article
        cat, tags = classify_article("Random unrelated cooking blog post", "Recipe for pasta")
        self.assertEqual(cat, "general")

    def test_github_trending_classified_as_open_source(self):
        from processing.classifier import run_classification
        db = _make_db_with_articles([{
            "id": "gh1", "title": "owner/cool-repo", "url": "https://github.com/owner/cool-repo",
            "source": "GitHub Trending", "source_type": "github_trending",
            "content": "A cool Python tool",
        }])
        run_classification(db)
        from database.database import db_session
        with db_session(db) as conn:
            row = conn.execute("SELECT category FROM articles WHERE id='gh1'").fetchone()
        self.assertEqual(row["category"], "open_source")
        Path(db).unlink(missing_ok=True)


# ── ranking tests ─────────────────────────────────────────────────────────────

class TestRanking(unittest.TestCase):

    def setUp(self):
        self.dbs: list[str] = []

    def tearDown(self):
        for p in self.dbs:
            Path(p).unlink(missing_ok=True)

    def _db(self, articles):
        path = _make_db_with_articles(articles)
        self.dbs.append(path)
        return path

    def test_scores_are_in_valid_range(self):
        from processing.ranking import run_ranking
        from database.database import db_session
        db = self._db([
            {"id": "a1", "title": "OpenAI releases new model",
             "url": "https://openai.com/a", "source": "OpenAI", "source_type": "rss",
             "published_at": datetime.now(timezone.utc).isoformat()},
        ])
        run_ranking(db)
        with db_session(db) as conn:
            row = conn.execute("SELECT importance FROM articles WHERE id='a1'").fetchone()
        self.assertGreaterEqual(row["importance"], 0)
        self.assertLessEqual(row["importance"], 100)

    def test_primary_source_scores_higher_than_random(self):
        from processing.ranking import run_ranking
        from database.database import db_session
        now = datetime.now(timezone.utc).isoformat()
        db = self._db([
            {"id": "primary", "title": "New model released",
             "url": "https://openai.com/x", "source": "OpenAI", "source_type": "rss",
             "published_at": now},
            {"id": "random", "title": "New model released",
             "url": "https://randomblog.com/x", "source": "RandomBlog", "source_type": "rss",
             "published_at": now},
        ])
        run_ranking(db)
        with db_session(db) as conn:
            rows = {r["id"]: r["importance"] for r in
                    conn.execute("SELECT id, importance FROM articles").fetchall()}
        self.assertGreater(rows["primary"], rows["random"])

    def test_recent_article_scores_higher_than_old(self):
        from processing.ranking import run_ranking
        from database.database import db_session
        db = self._db([
            {"id": "recent", "title": "Breaking news",
             "url": "https://openai.com/recent", "source": "OpenAI", "source_type": "rss",
             "published_at": datetime.now(timezone.utc).isoformat()},
            {"id": "old", "title": "Breaking news",
             "url": "https://openai.com/old", "source": "OpenAI", "source_type": "rss",
             "published_at": (datetime.now(timezone.utc) - timedelta(days=6)).isoformat()},
        ])
        run_ranking(db)
        with db_session(db) as conn:
            rows = {r["id"]: r["importance"] for r in
                    conn.execute("SELECT id, importance FROM articles").fetchall()}
        self.assertGreater(rows["recent"], rows["old"])

    def test_github_repo_with_high_stars_scores_well(self):
        from processing.ranking import run_ranking
        from database.database import db_session
        now = datetime.now(timezone.utc).isoformat()
        db = self._db([
            {"id": "viral", "title": "owner/viral-repo",
             "url": "https://github.com/owner/viral-repo",
             "source": "GitHub Trending", "source_type": "github_trending",
             "published_at": now, "extra": {"stars_today": 800, "total_stars": 12000}},
            {"id": "quiet", "title": "owner/quiet-repo",
             "url": "https://github.com/owner/quiet-repo",
             "source": "GitHub Trending", "source_type": "github_trending",
             "published_at": now, "extra": {"stars_today": 10, "total_stars": 50}},
        ])
        run_ranking(db)
        with db_session(db) as conn:
            rows = {r["id"]: r["importance"] for r in
                    conn.execute("SELECT id, importance FROM articles").fetchall()}
        self.assertGreater(rows["viral"], rows["quiet"])

    def test_high_importance_keywords_boost_score(self):
        from processing.ranking import _keyword_score
        high = _keyword_score("OpenAI announces breakthrough new agent release", 25)
        low = _keyword_score("A podcast recap interview opinion piece", 25)
        self.assertGreater(high, low)

    def test_freshness_decay(self):
        from processing.ranking import _freshness_score
        now = datetime.now(timezone.utc)
        score_today = _freshness_score(now.isoformat(), max_score=25, decay_days=7)
        score_old = _freshness_score(
            (now - timedelta(days=8)).isoformat(), max_score=25, decay_days=7
        )
        self.assertGreater(score_today, score_old)
        self.assertEqual(score_old, 0.0)


# ── pipeline integration test ─────────────────────────────────────────────────

class TestProcessingPipeline(unittest.TestCase):

    def test_full_pipeline_runs_and_updates_db(self):
        from processing.pipeline import run_processing
        from database.database import db_session

        now = datetime.now(timezone.utc).isoformat()
        db = _make_db_with_articles([
            {"id": "r1", "title": "OpenAI releases <b>GPT-X</b> model &amp; API",
             "url": "https://openai.com/gpt-x?utm_source=twitter",
             "source": "OpenAI", "source_type": "rss",
             "content": "<p>We are excited to announce GPT-X, our most capable agent yet.</p>",
             "published_at": now},
            {"id": "r2", "title": "OpenAI releases GPT-X model API",
             "url": "https://techcrunch.com/openai-gpt-x",
             "source": "TechCrunch", "source_type": "rss",
             "content": "OpenAI has released a new model with tool use capabilities.",
             "published_at": now},
            {"id": "r3", "title": "Apache Iceberg 3.0 released for data engineering pipelines",
             "url": "https://iceberg.apache.org/news/3",
             "source": "Apache", "source_type": "rss",
             "content": "The new lakehouse format adds improved etl support.",
             "published_at": now},
        ])

        summary = run_processing(db)

        # All articles cleaned
        self.assertEqual(summary["cleaned"], 3)

        # Near-duplicate pair was detected
        self.assertGreaterEqual(summary["dedup"]["groups_found"], 1)

        # 2 articles classified (the duplicate is excluded from classification)
        self.assertGreaterEqual(summary["classified"], 2)

        # Scores assigned
        with db_session(db) as conn:
            rows = conn.execute(
                "SELECT title, category, importance, url FROM articles WHERE importance > 0"
            ).fetchall()

        scored = [dict(r) for r in rows]
        self.assertGreater(len(scored), 0)

        # HTML cleaned from title
        openai_row = next((r for r in scored if "openai.com" in r["url"]), None)
        if openai_row:
            self.assertNotIn("<b>", openai_row["title"])
            self.assertNotIn("&amp;", openai_row["title"])
            self.assertNotIn("utm_source", openai_row["url"])

        # Data engineering article classified correctly
        iceberg_row = next((r for r in scored if "iceberg" in r["url"]), None)
        if iceberg_row:
            self.assertIn(iceberg_row["category"], ["data_engineering", "general"])

        Path(db).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)