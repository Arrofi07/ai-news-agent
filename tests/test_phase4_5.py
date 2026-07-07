"""
Tests for Phase 4 (LLM summarization) and Phase 5 (newsletter generation).
All offline — Gemini API calls are mocked, no API key needed.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch


# ── shared fixtures ───────────────────────────────────────────────────────────

def _sample_article(**overrides) -> dict:
    base = {
        "id": "test123",
        "title": "OpenAI releases GPT-X with advanced reasoning",
        "url": "https://openai.com/blog/gpt-x",
        "source": "OpenAI",
        "source_type": "rss",
        "content": "OpenAI announced GPT-X, a model with significantly improved reasoning.",
        "summary": None,
        "why_it_matters": None,
        "career_impact": "high",
        "category": "llm",
        "tags": "llm,company",
        "importance": 85.0,
        "extra": {},
        "published_at": datetime.now(timezone.utc).isoformat(),
    }
    base.update(overrides)
    return base


def _sample_github_repo(**overrides) -> dict:
    base = {
        "id": "gh123",
        "title": "owner/awesome-llm-agent",
        "url": "https://github.com/owner/awesome-llm-agent",
        "source": "GitHub Trending",
        "source_type": "github_trending",
        "content": "A framework for building LLM agents with tool calling.",
        "summary": None,
        "why_it_matters": None,
        "category": "open_source",
        "tags": "open_source",
        "importance": 60.0,
        "extra": {"stars_today": 450, "total_stars": 3200, "language": "Python"},
        "published_at": datetime.now(timezone.utc).isoformat(),
    }
    base.update(overrides)
    return base


def _sample_grouped() -> dict:
    return {
        "top_stories": [
            _sample_article(id="t1", title="OpenAI releases GPT-X", url="https://openai.com/a"),
            _sample_article(id="t2", title="Anthropic launches Claude 4",
                            source="Anthropic", url="https://anthropic.com/b",
                            summary="Claude 4 is now available."),
        ],
        "research": [
            _sample_article(id="r1", source="arXiv", source_type="arxiv",
                            url="https://arxiv.org/abs/123",
                            title="Scaling Laws for Multi-Agent LLM Systems"),
        ],
        "tools": [
            _sample_article(id="tool1", title="LangGraph 2.0 released",
                            url="https://langgraph.com/a", category="open_source"),
        ],
        "github": [
            _sample_github_repo(id="gh1"),
            _sample_github_repo(id="gh2", title="owner/vector-db",
                                url="https://github.com/owner/vector-db",
                                extra={"stars_today": 80, "total_stars": 900, "language": "Rust"}),
        ],
    }


# ── Gemini client tests ────────────────────────────────────────────────────────

class TestGeminiClient(unittest.TestCase):

    def _make_client(self):
        from llm.gemini import GeminiClient
        return GeminiClient(api_key="fake-key-for-testing", model="gemini-2.5-flash")

    def _mock_response(self, text: str) -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = lambda: None
        resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": text}]}}]
        }
        return resp

    def test_summarize_article_parses_valid_json(self):
        from llm.gemini import GeminiClient
        client = self._make_client()
        good_json = json.dumps({
            "summary": "OpenAI released GPT-X with improved reasoning.",
            "why_it_matters": "Significant step forward for AI agents.",
            "career_impact": "high",
            "category": "llm",
            "tags": ["llm", "openai"],
            "estimated_read_minutes": 3,
        })
        with patch("requests.post", return_value=self._mock_response(good_json)):
            result = client.summarize_article("GPT-X Released", "OpenAI", "Content here.")
        self.assertEqual(result["summary"], "OpenAI released GPT-X with improved reasoning.")
        self.assertEqual(result["career_impact"], "high")
        self.assertIsInstance(result["tags"], list)

    def test_summarize_strips_markdown_fences(self):
        from llm.gemini import GeminiClient
        client = self._make_client()
        fenced = '```json\n{"summary": "Test.", "why_it_matters": "Big.", "career_impact": "high", "category": "llm", "tags": [], "estimated_read_minutes": 2}\n```'
        with patch("requests.post", return_value=self._mock_response(fenced)):
            result = client.summarize_article("Test", "Test", "Content.")
        self.assertEqual(result["summary"], "Test.")

    def test_summarize_falls_back_on_invalid_json(self):
        from llm.gemini import GeminiClient
        client = self._make_client()
        with patch("requests.post", return_value=self._mock_response("not json at all!")):
            result = client.summarize_article("Title", "Source", "Content")
        # Should return fallback, not raise
        self.assertIn("summary", result)
        self.assertTrue(result.get("_fallback"))

    def test_summarize_falls_back_on_api_error(self):
        from llm.gemini import GeminiClient
        client = self._make_client()
        with patch("requests.post", side_effect=Exception("network error")):
            result = client.summarize_article("Title", "Source", "Content")
        self.assertIn("summary", result)
        self.assertTrue(result.get("_fallback"))

    def test_rate_limit_retries(self):
        from llm.gemini import GeminiClient
        client = self._make_client()
        rate_limited = MagicMock()
        rate_limited.status_code = 429

        good_json = json.dumps({"summary": "OK", "why_it_matters": "Yes",
                                "career_impact": "low", "category": "general",
                                "tags": [], "estimated_read_minutes": 1})
        good_resp = self._mock_response(good_json)

        with patch("requests.post", side_effect=[rate_limited, good_resp]), \
             patch("time.sleep"):
            result = client.summarize_article("T", "S", "C")
        self.assertEqual(result["summary"], "OK")

    def test_parse_json_response_handles_prose_wrapping(self):
        from llm.gemini import _parse_json_response
        text = 'Here is the analysis:\n{"summary": "Test", "why_it_matters": "Big", "career_impact": "high", "category": "llm", "tags": [], "estimated_read_minutes": 2}\nEnd of response.'
        result = _parse_json_response(text)
        self.assertIsNotNone(result)
        self.assertEqual(result["summary"], "Test")

    def test_fallback_summary_uses_first_sentences(self):
        from llm.gemini import _fallback_summary
        content = "First sentence here. Second sentence here. Third sentence."
        result = _fallback_summary("Title", content)
        self.assertIn("First sentence", result["summary"])
        self.assertTrue(result["_fallback"])

    def test_no_api_key_raises_on_init(self):
        from llm.gemini import GeminiClient
        with self.assertRaises(ValueError):
            GeminiClient(api_key="", model="gemini-2.5-flash")


# ── summarization runner tests ─────────────────────────────────────────────────

class TestSummarizationRunner(unittest.TestCase):

    def setUp(self):
        self.tmp_dbs: list[str] = []

    def tearDown(self):
        for p in self.tmp_dbs:
            Path(p).unlink(missing_ok=True)

    def _make_db(self) -> str:
        from database.database import init_db, db_session
        path = tempfile.mktemp(suffix=".db")
        self.tmp_dbs.append(path)
        init_db(path)
        now = datetime.now(timezone.utc).isoformat()
        with db_session(path) as conn:
            for i, row in enumerate([
                ("rss1", "OpenAI GPT-X launch", "https://openai.com/a",
                 "OpenAI", "rss", "Content A", 90.0),
                ("rss2", "Anthropic Claude update", "https://anthropic.com/b",
                 "Anthropic", "rss", "Content B", 80.0),
                ("arx1", "RAG Scaling Laws paper", "https://arxiv.org/a",
                 "arXiv", "arxiv", "Abstract here", 70.0),
                ("gh1", "owner/agent-framework", "https://github.com/owner/agent-framework",
                 "GitHub Trending", "github_trending", "A framework", 55.0),
            ]):
                conn.execute(
                    """INSERT INTO articles
                       (id,title,url,source,source_type,content,published_at,
                        category,tags,importance,extra)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (row[0], row[1], row[2], row[3], row[4], row[5], now,
                     "general", "", row[6],
                     json.dumps({"stars_today": 200, "total_stars": 1500, "language": "Python"}
                                if row[4] == "github_trending" else {}))
                )
        return path

    def test_run_summarization_without_api_key_uses_fallback(self):
        from llm.summarize import run_summarization
        from config.loader import load_config
        cfg = load_config()
        cfg.database.path = self._make_db()
        # Patch api_key to None
        import unittest.mock as mock
        with mock.patch.object(type(cfg.llm), 'api_key', new_callable=mock.PropertyMock, return_value=None):
            grouped = run_summarization(cfg)
        self.assertIn("top_stories", grouped)
        self.assertIn("github", grouped)
        # Every article should have a summary (fallback or real)
        for section, articles in grouped.items():
            for a in articles:
                self.assertIsNotNone(a.get("summary"),
                                     f"Missing summary in {section}: {a.get('title')}")

    def test_github_repos_get_auto_summary_without_llm(self):
        from llm.summarize import run_summarization, _github_why_it_matters
        repo = _sample_github_repo()
        why = _github_why_it_matters(repo)
        self.assertIn("450", why)
        self.assertIn("Python", why)

    def test_select_top_stories_excludes_duplicates(self):
        from llm.summarize import _select_top_stories
        from database.database import init_db, db_session
        path = tempfile.mktemp(suffix=".db")
        self.tmp_dbs.append(path)
        init_db(path)
        now = datetime.now(timezone.utc).isoformat()
        with db_session(path) as conn:
            conn.execute(
                "INSERT INTO articles (id,title,url,source,source_type,content,"
                "published_at,category,tags,importance,extra) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("dup1", "Dup Article", "https://dup.com/a", "S", "rss",
                 "C", now, "general", "", -1.0, "{}")
            )
            conn.execute(
                "INSERT INTO articles (id,title,url,source,source_type,content,"
                "published_at,category,tags,importance,extra) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("ok1", "Good Article", "https://good.com/a", "OpenAI", "rss",
                 "C", now, "general", "", 85.0, "{}")
            )
        results = _select_top_stories(path, 10)
        ids = [r["id"] for r in results]
        self.assertNotIn("dup1", ids)
        self.assertIn("ok1", ids)

    def test_select_top_stories_excludes_already_featured(self):
        """An article already shipped in a past newsletter must not be
        re-selected, even if its importance score is still high (this is
        the fix for old articles resurfacing after freshness decay)."""
        from llm.summarize import _select_top_stories
        from database.database import init_db, db_session
        path = tempfile.mktemp(suffix=".db")
        self.tmp_dbs.append(path)
        init_db(path)
        now = datetime.now(timezone.utc).isoformat()
        with db_session(path) as conn:
            conn.execute(
                "INSERT INTO articles (id,title,url,source,source_type,content,"
                "published_at,category,tags,importance,extra,featured_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("old1", "Old high scorer", "https://old.com/a", "OpenAI", "rss",
                 "C", now, "general", "", 95.0, "{}", now)  # already featured
            )
            conn.execute(
                "INSERT INTO articles (id,title,url,source,source_type,content,"
                "published_at,category,tags,importance,extra,featured_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("new1", "Fresh article", "https://new.com/a", "OpenAI", "rss",
                 "C", now, "general", "", 60.0, "{}", None)  # never featured
            )
        results = _select_top_stories(path, 10)
        ids = [r["id"] for r in results]
        self.assertNotIn("old1", ids, "already-featured article should be excluded")
        self.assertIn("new1", ids)

    def test_mark_articles_featured_stamps_all_selected_ids(self):
        from llm.summarize import mark_articles_featured
        from database.database import db_session
        path = self._make_db()
        grouped = {
            "top_stories": [{"id": "rss1"}, {"id": "rss2"}],
            "github": [{"id": "gh1"}],
        }
        mark_articles_featured(path, grouped)
        with db_session(path) as conn:
            rows = conn.execute(
                "SELECT id, featured_at FROM articles WHERE id IN ('rss1','rss2','gh1')"
            ).fetchall()
            for row in rows:
                self.assertIsNotNone(row["featured_at"], f"{row['id']} should be marked featured")
            # An article not in `grouped` (arx1) should be untouched
            untouched = conn.execute(
                "SELECT featured_at FROM articles WHERE id = 'arx1'"
            ).fetchone()
            self.assertIsNone(untouched["featured_at"])


# ── markdown newsletter tests ─────────────────────────────────────────────────

class TestMarkdownNewsletter(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_build_markdown_produces_required_sections(self):
        from newsletter.markdown import build_markdown
        grouped = _sample_grouped()
        md = build_markdown(grouped, "Week 27 - 2026", output_dir=self.tmp_dir)

        self.assertIn("AI Weekly Intelligence Report", md)
        self.assertIn("🔥 Top Stories", md)
        self.assertIn("📄 Research Worth Reading", md)
        self.assertIn("🛠️ New Tools", md)
        self.assertIn("📈 Trending on GitHub", md)
        self.assertIn("🎯 Career Takeaways", md)
        self.assertIn("Estimated reading time", md)

    def test_build_markdown_includes_article_titles(self):
        from newsletter.markdown import build_markdown
        grouped = _sample_grouped()
        md = build_markdown(grouped, "Week 27 - 2026", output_dir=self.tmp_dir)
        self.assertIn("OpenAI releases GPT-X", md)
        self.assertIn("Scaling Laws for Multi-Agent", md)

    def test_build_markdown_includes_github_star_counts(self):
        from newsletter.markdown import build_markdown
        grouped = _sample_grouped()
        md = build_markdown(grouped, "Week 27 - 2026", output_dir=self.tmp_dir)
        self.assertIn("3,200", md)   # total stars, comma formatted

    def test_build_markdown_writes_file_to_output_dir(self):
        from newsletter.markdown import build_markdown
        grouped = _sample_grouped()
        build_markdown(grouped, "Week 27 - 2026", output_dir=self.tmp_dir)
        files = list(Path(self.tmp_dir).glob("*.md"))
        self.assertEqual(len(files), 1)
        self.assertIn("2026-W27", files[0].name)

    def test_build_markdown_with_llm_client_uses_llm_output(self):
        from newsletter.markdown import build_markdown
        mock_llm = MagicMock()
        # Must be long enough and mention every article's URL to pass
        # _validate_newsletter — a short/incomplete mock (as this test used
        # to have) is exactly the kind of broken response we now reject.
        mock_llm.generate_text.return_value = (
            "# AI Weekly\n\n"
            "OpenAI released GPT-X this week, a model with major reasoning gains. "
            "Read more: https://openai.com/a\n\n"
            "Anthropic also launched Claude 4, now generally available. "
            "See: https://anthropic.com/b\n\n"
            "On the research side, a new paper on scaling laws for multi-agent "
            "systems is worth a read: https://arxiv.org/abs/123\n\n"
            "LangGraph 2.0 shipped with a cleaner orchestration API: "
            "https://langgraph.com/a\n\n"
            "Trending on GitHub: owner/awesome-llm-agent "
            "(https://github.com/owner/awesome-llm-agent) and owner/vector-db "
            "(https://github.com/owner/vector-db) both picked up serious stars."
        )
        grouped = _sample_grouped()
        md = build_markdown(grouped, "Week 27 - 2026",
                            llm_client=mock_llm, output_dir=self.tmp_dir)
        self.assertIn("OpenAI released GPT-X", md)
        mock_llm.generate_text.assert_called_once()

    def test_build_markdown_rejects_truncated_llm_output(self):
        """A short/incomplete LLM response must fall back to the template
        instead of being written to disk as-is (the original bug)."""
        from newsletter.markdown import build_markdown
        mock_llm = MagicMock()
        mock_llm.generate_text.return_value = "# AI Weekly Intelligence Report\n\n### LeRobot v0.6"
        grouped = _sample_grouped()
        md = build_markdown(grouped, "Week 27 - 2026",
                            llm_client=mock_llm, output_dir=self.tmp_dir)
        # Should have fallen back to the template, which always includes
        # every article's URL and the reading-time footer.
        self.assertIn("_Estimated reading time", md)
        self.assertIn("https://openai.com/a", md)

    def test_build_markdown_falls_back_to_template_if_llm_returns_empty(self):
        from newsletter.markdown import build_markdown
        mock_llm = MagicMock()
        mock_llm.generate_text.return_value = ""
        grouped = _sample_grouped()
        md = build_markdown(grouped, "Week 27 - 2026",
                            llm_client=mock_llm, output_dir=self.tmp_dir)
        # Should fall back to template which always includes this header
        self.assertIn("AI Weekly Intelligence Report", md)

    def test_week_filename_conversion(self):
        from newsletter.markdown import _week_filename
        self.assertEqual(_week_filename("Week 27 - 2026"), "2026-W27")
        self.assertEqual(_week_filename("Week 3 - 2026"), "2026-W03")

    def test_career_takeaways_adapts_to_content(self):
        from newsletter.markdown import _build_career_takeaways
        # Heavy agent content should trigger the agents takeaway
        grouped_agents = {
            "top_stories": [_sample_article(tags="agents,llm"),
                            _sample_article(tags="agents")],
        }
        takeaways = _build_career_takeaways(grouped_agents)
        combined = " ".join(takeaways)
        self.assertIn("Agent", combined)


# ── HTML newsletter tests ─────────────────────────────────────────────────────

class TestHTMLNewsletter(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_build_html_produces_valid_structure(self):
        from newsletter.html import build_html
        grouped = _sample_grouped()
        html = build_html("", grouped, "Week 27 - 2026", output_dir=self.tmp_dir)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("<html", html)
        self.assertIn("AI Weekly Intelligence Report", html)
        self.assertIn("</html>", html)

    def test_build_html_escapes_special_characters(self):
        from newsletter.html import build_html
        grouped = _sample_grouped()
        # Inject a title with HTML special chars
        grouped["top_stories"][0]["title"] = "OpenAI & Anthropic <announce> GPT-X"
        html = build_html("", grouped, "Week 27 - 2026", output_dir=self.tmp_dir)
        self.assertNotIn("<announce>", html)
        self.assertIn("&lt;announce&gt;", html)
        self.assertIn("&amp;", html)

    def test_build_html_includes_all_sections(self):
        from newsletter.html import build_html
        grouped = _sample_grouped()
        html = build_html("", grouped, "Week 27 - 2026", output_dir=self.tmp_dir)
        self.assertIn("Top Stories", html)
        self.assertIn("Research Worth Reading", html)
        self.assertIn("New Tools", html)
        self.assertIn("Trending on GitHub", html)
        self.assertIn("Career Takeaways", html)

    def test_build_html_writes_file(self):
        from newsletter.html import build_html
        grouped = _sample_grouped()
        build_html("", grouped, "Week 27 - 2026", output_dir=self.tmp_dir)
        files = list(Path(self.tmp_dir).glob("*.html"))
        self.assertEqual(len(files), 1)
        self.assertIn("2026-W27", files[0].name)

    def test_github_section_shows_star_counts(self):
        from newsletter.html import build_html
        grouped = _sample_grouped()
        html = build_html("", grouped, "Week 27 - 2026", output_dir=self.tmp_dir)
        self.assertIn("450", html)      # stars_today
        self.assertIn("3,200", html)    # total_stars formatted

    def test_story_links_are_correct(self):
        from newsletter.html import build_html
        grouped = _sample_grouped()
        html = build_html("", grouped, "Week 27 - 2026", output_dir=self.tmp_dir)
        self.assertIn('href="https://openai.com/a"', html)

    def test_html_esc_function(self):
        from newsletter.html import _esc
        self.assertEqual(_esc("<script>alert('xss')</script>"),
                         "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;")


# ── prompts tests ─────────────────────────────────────────────────────────────

class TestPrompts(unittest.TestCase):

    def test_article_summary_prompt_includes_title_and_content(self):
        from config.prompts import article_summary_prompt
        p = article_summary_prompt("My Title", "OpenAI", "Some content here.")
        self.assertIn("My Title", p)
        self.assertIn("OpenAI", p)
        self.assertIn("Some content here", p)
        self.assertIn("JSON", p)

    def test_article_summary_prompt_truncates_long_content(self):
        from config.prompts import article_summary_prompt
        long_content = "x" * 10000
        p = article_summary_prompt("Title", "Source", long_content)
        # Content is capped at 3000 chars in the prompt
        content_part = p[p.find("Content:") + 8:]
        self.assertLessEqual(len(content_part), 3500)

    def test_newsletter_prompt_includes_all_sections(self):
        from config.prompts import newsletter_prompt
        p = newsletter_prompt(
            week_label="Week 27 - 2026",
            top_stories=[_sample_article()],
            research_papers=[_sample_article(source="arXiv")],
            new_tools=[_sample_article(title="New tool")],
            github_repos=[_sample_github_repo()],
        )
        self.assertIn("Week 27 - 2026", p)
        self.assertIn("TOP STORIES", p)
        self.assertIn("RESEARCH PAPERS", p)
        self.assertIn("TRENDING ON GITHUB", p)
        self.assertIn("🔥 Top Stories", p)   # Section headers in the prompt


if __name__ == "__main__":
    unittest.main(verbosity=2)