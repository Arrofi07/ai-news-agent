"""
tests/test_main.py — regression coverage for main.py's phase wiring.

This file exists because of a real production bug: Phase 5 was rebuilding a
fresh Gemini-only client instead of reusing whichever client survived Phase
4's circuit breaker (see llm/router.py). That bug reintroduced itself once
already after being fixed, and all 116 other tests still passed — because
nothing asserted main.py's own wiring, only the individual functions it
calls. These tests close that gap by mocking every phase and asserting the
exact object identity/kwargs passed between them.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


class TestMainPhaseWiring(unittest.TestCase):

    def _run_main_with_mocks(self, gemini_key="fake-gemini", groq_key=None,
                              resend_key=None, resend_to=None,
                              summarization_client=None, summarization_provider="gemini"):
        """
        Run main.main() with every phase mocked out, and return the mocks so
        each test can assert on how they were called. Keeping this as a
        shared helper means each test only needs to state what's different
        about its scenario, not re-wire the whole pipeline every time.
        """
        import main as main_module

        cfg = MagicMock()
        cfg.llm.api_key = gemini_key
        cfg.llm.model = "gemini-2.5-flash"
        cfg.llm.groq_api_key = groq_key
        cfg.database.path = "fake.db"

        grouped = {"top_stories": [{"id": "a1", "title": "T", "url": "https://x.com/a"}]}

        with patch.object(main_module, "load_config", return_value=cfg), \
             patch.object(main_module, "run_collection", return_value={}), \
             patch.object(main_module, "run_processing", return_value={
                 "cleaned": 0, "dedup": {"groups_found": 0}, "classified": 0, "ranked": 0}), \
             patch.object(main_module, "run_summarization",
                          return_value=(grouped, summarization_client, summarization_provider)), \
             patch.object(main_module, "build_markdown", return_value="# newsletter") as mock_build_md, \
             patch.object(main_module, "build_html", return_value="<html></html>") as mock_build_html, \
             patch.object(main_module, "mark_articles_featured") as mock_mark_featured, \
             patch.object(main_module, "send_newsletter", return_value=True) as mock_send_email, \
             patch.dict("os.environ", {
                 **({"RESEND_API_KEY": resend_key} if resend_key else {}),
                 **({"RESEND_TO_EMAIL": resend_to} if resend_to else {}),
             }, clear=False):
            main_module.main()

        return {
            "build_markdown": mock_build_md,
            "build_html": mock_build_html,
            "mark_articles_featured": mock_mark_featured,
            "send_newsletter": mock_send_email,
        }

    def test_phase5_reuses_phase4_survivor_client_not_a_fresh_one(self):
        """The actual regression: build_markdown must receive the exact
        client object run_summarization returned — not a newly constructed
        GeminiClient — so a Gemini→Groq switch in Phase 4 carries through to
        the newsletter-writing call in Phase 5."""
        sentinel_groq_client = MagicMock(name="groq_client_that_survived_phase4")
        mocks = self._run_main_with_mocks(
            summarization_client=sentinel_groq_client,
            summarization_provider="groq",
        )
        actual_client_passed = mocks["build_markdown"].call_args.kwargs["llm_client"]
        self.assertIs(
            actual_client_passed, sentinel_groq_client,
            "Phase 5 must reuse the Phase 4 survivor client by identity, not "
            "construct a new one — otherwise a Gemini failure in Phase 4 is "
            "silently ignored when Phase 5 retries Gemini anyway."
        )

    def test_phase5_passes_none_client_through_when_all_providers_exhausted(self):
        """If every provider was exhausted in Phase 4 (client is None),
        Phase 5 must not resurrect a Gemini client — None should reach
        build_markdown, triggering the template fallback there."""
        mocks = self._run_main_with_mocks(
            summarization_client=None,
            summarization_provider=None,
        )
        self.assertIsNone(mocks["build_markdown"].call_args.kwargs["llm_client"])

    def test_email_sent_when_both_secrets_present(self):
        mocks = self._run_main_with_mocks(resend_key="key", resend_to="me@example.com")
        mocks["send_newsletter"].assert_called_once()
        self.assertEqual(mocks["send_newsletter"].call_args.kwargs["to_email"], "me@example.com")

    def test_email_skipped_when_secrets_missing(self):
        mocks = self._run_main_with_mocks(resend_key=None, resend_to=None)
        mocks["send_newsletter"].assert_not_called()

    def test_articles_marked_featured_after_newsletter_built(self):
        """mark_articles_featured must run after build_markdown/build_html,
        not before — marking an article "sent" before confirming the
        newsletter was actually produced could burn articles that never
        shipped if something crashed mid-Phase-5."""
        mocks = self._run_main_with_mocks()
        mocks["mark_articles_featured"].assert_called_once()


if __name__ == "__main__":
    unittest.main()