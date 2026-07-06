"""
llm/gemini.py — Gemini 2.5 Flash client for article summarization.

Wraps the Gemini REST API directly via requests (no google-generativeai SDK
needed — one less dependency, and the REST API is stable and simple enough).

Key design decisions:
- Every call returns a result even on failure (fallback to rule-based summary)
  so one bad API response never breaks the full newsletter run.
- JSON responses are parsed defensively with multiple fallback strategies.
- Rate limiting is handled with exponential backoff — Gemini Flash has
  generous free tier limits but can 429 under burst load.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import requests

from config.prompts import article_summary_prompt
from scheduler.logging_setup import get_logger

logger = get_logger(__name__)

GEMINI_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models"
    "/{model}:generateContent?key={api_key}"
)


class GeminiClient:
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY is not set. "
                "Add it to your .env file or GitHub Actions secrets."
            )
        self.api_key = api_key
        self.model = model

    def summarize_article(
        self, title: str, source: str, content: str
    ) -> dict[str, Any]:
        """
        Summarize a single article. Returns a dict with at minimum:
          summary, why_it_matters, career_impact, category, tags, estimated_read_minutes

        Never raises — returns a rule-based fallback on any failure so the
        pipeline keeps running even if the API is down or quota is exhausted.
        """
        prompt = article_summary_prompt(title, source, content)
        try:
            raw = self._call(prompt)
            result = _parse_json_response(raw)
            if result:
                return result
        except Exception as e:
            logger.warning("Gemini summarization failed for '%s': %s", title[:60], e)

        return _fallback_summary(title, content)

    def generate_text(self, prompt: str) -> str:
        """
        General-purpose text generation (used for newsletter assembly).
        Returns empty string on failure — callers must handle this.
        """
        try:
            return self._call(prompt)
        except Exception as e:
            logger.warning("Gemini generate_text failed: %s", e)
            return ""

    # ── private ────────────────────────────────────────────────────────────

    def _call(self, prompt: str, max_retries: int = 3) -> str:
        """Make a Gemini API call with exponential backoff on rate limit errors."""
        url = GEMINI_API_URL.format(model=self.model, api_key=self.api_key)
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.3,        # Low temp for factual summaries
                "maxOutputTokens": 1024,
            },
        }

        for attempt in range(max_retries):
            try:
                resp = requests.post(url, json=payload, timeout=30)

                if resp.status_code == 429:
                    # Rate limited — back off and retry
                    wait = 2 ** attempt * 5   # 5s, 10s, 20s
                    logger.warning("Gemini rate limited, waiting %ds (attempt %d/%d)...",
                                   wait, attempt + 1, max_retries)
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                data = resp.json()
                return _extract_text(data)

            except requests.exceptions.Timeout:
                logger.warning("Gemini API timeout (attempt %d/%d)", attempt + 1, max_retries)
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
            except requests.exceptions.RequestException as e:
                logger.warning("Gemini API error (attempt %d/%d): %s", attempt + 1, max_retries, e)
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)

        raise RuntimeError(f"Gemini API failed after {max_retries} attempts")


# ── helpers ───────────────────────────────────────────────────────────────────

def _extract_text(response_data: dict) -> str:
    """Pull the text content out of a Gemini API response."""
    try:
        return response_data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise ValueError(f"Unexpected Gemini response shape: {e}") from e


def _parse_json_response(text: str) -> dict | None:
    """
    Parse a JSON response from the LLM, handling common issues:
    - Markdown code fences (```json ... ```)
    - Leading/trailing whitespace
    - Partial JSON (we log and return None so callers fall back gracefully)
    """
    if not text:
        return None

    # Strip markdown code fences
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text.strip(), flags=re.MULTILINE)
    text = text.strip()

    # Find the first {...} block if there's surrounding prose
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse LLM JSON response: %s\nRaw: %s", e, text[:200])
        return None


def _fallback_summary(title: str, content: str) -> dict:
    """
    Rule-based summary when the LLM is unavailable.
    Good enough to keep the newsletter running, clearly marked as auto-generated.
    """
    # Take the first 2 sentences of cleaned content as a summary
    sentences = re.split(r"(?<=[.!?])\s+", (content or "").strip())
    summary = " ".join(sentences[:2]) if sentences else title

    return {
        "summary": summary[:400] or title,
        "why_it_matters": "See the full article for details.",
        "career_impact": "medium",
        "category": "general",
        "tags": [],
        "estimated_read_minutes": 3,
        "_fallback": True,   # Flag so we know this wasn't LLM-generated
    }