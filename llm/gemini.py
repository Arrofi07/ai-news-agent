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
            raw, truncated = self._call(prompt)
            if truncated:
                # A cut-off JSON blob will usually fail to parse anyway, but
                # don't even try — go straight to the rule-based fallback.
                logger.warning("Gemini summary truncated for '%s'", title[:60])
            else:
                result = _parse_json_response(raw)
                if result:
                    return result
        except Exception as e:
            logger.warning("Gemini summarization failed for '%s': %s", title[:60], e)

        return _fallback_summary(title, content)

    def generate_text(self, prompt: str, max_output_tokens: int = 4096) -> str:
        """
        General-purpose text generation (used for newsletter assembly).
        Returns empty string on failure — callers must handle this.

        max_output_tokens defaults to 4096 here (vs. 1024 for summarize_article)
        because the newsletter prompt asks the model to write ~15 full article
        blurbs plus headers/takeaways in one response. 1024 was the original
        bug: Gemini silently truncated mid-sentence and the truncated text
        still got written to disk as the "final" newsletter. Callers that
        expect a short response can still pass a smaller value explicitly.
        """
        try:
            text, truncated = self._call(prompt, max_output_tokens=max_output_tokens)
            if truncated:
                # Gemini itself told us (via finishReason=MAX_TOKENS) that this
                # response was cut off — even a *higher* budget can still run
                # out for an unusually large batch of articles. Treat this the
                # same as an API failure so the caller falls back to the
                # template instead of shipping a half-written newsletter.
                logger.warning(
                    "Gemini response truncated (hit maxOutputTokens=%d) — "
                    "discarding partial output.", max_output_tokens
                )
                return ""
            return text
        except Exception as e:
            logger.warning("Gemini generate_text failed: %s", e)
            return ""

    # ── private ────────────────────────────────────────────────────────────

    def _call(
        self, prompt: str, max_retries: int = 3, max_output_tokens: int = 1024
    ) -> tuple[str, bool]:
        """Make a Gemini API call with exponential backoff on rate limit errors.

        Returns (text, was_truncated). was_truncated is True when Gemini's own
        finishReason says the response was cut off by the token budget, so
        callers can decide whether a truncated response is acceptable instead
        of us silently returning partial text as if it were complete.
        """
        url = GEMINI_API_URL.format(model=self.model, api_key=self.api_key)
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.3,        # Low temp for factual summaries
                "maxOutputTokens": max_output_tokens,
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
                return _extract_text(data), _is_truncated(data)

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


def _is_truncated(response_data: dict) -> bool:
    """
    Check Gemini's own finishReason for the candidate. This is the authoritative
    signal that a response was cut off by maxOutputTokens — much more reliable
    than trying to guess truncation from the text itself (e.g. "does it end
    mid-word?"), and it's what actually caused the empty/broken newsletter bug.
    Missing/unexpected shape is treated as "not truncated" (fail open) since
    _extract_text will already have raised on a genuinely malformed response.
    """
    try:
        return response_data["candidates"][0].get("finishReason") == "MAX_TOKENS"
    except (KeyError, IndexError):
        return False


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