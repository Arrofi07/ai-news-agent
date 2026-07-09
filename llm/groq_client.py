"""
llm/groq_client.py — Groq client for article summarization and newsletter prose.

Groq hosts open-weight models (Llama, Qwen, etc.) on an OpenAI-compatible
REST API, which is why this looks structurally similar to gemini.py but
talks a different wire format (chat completions, Bearer auth, finish_reason
instead of finishReason). Kept as its own file rather than unifying into one
generic "LLMClient" because the two APIs' response shapes genuinely differ
enough that a shared base class would just be a thin wrapper hiding two
almost-identical implementations — not worth the indirection for two clients.

Same contract as GeminiClient so llm/router.py can swap between them:
- summarize_article(...) -> (result: dict, ok: bool), never raises
- generate_text(...) -> str, empty string on failure/truncation, never raises

summarize_article uses Groq's JSON mode (response_format: json_object) to
get structurally guaranteed valid JSON rather than hoping free-text output
happens to parse — see _call's want_json parameter.
"""

from __future__ import annotations

import time
from typing import Any

import requests

from config.prompts import article_summary_prompt
from llm.common import fallback_summary, parse_json_response
from scheduler.logging_setup import get_logger

logger = get_logger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqClient:
    def __init__(self, api_key: str, model: str = "openai/gpt-oss-120b"):
        if not api_key:
            raise ValueError(
                "GROQ_API_KEY is not set. "
                "Add it to your .env file or GitHub Actions secrets."
            )
        self.api_key = api_key
        self.model = model

    def summarize_article(
        self, title: str, source: str, content: str
    ) -> tuple[dict[str, Any], bool]:
        """Summarize a single article. Returns (result, ok). Never raises —
        see GeminiClient.summarize_article for the full contract this mirrors."""
        prompt = article_summary_prompt(title, source, content)
        try:
            raw, truncated = self._call(prompt, want_json=True)
            if truncated:
                logger.warning("Groq summary truncated for '%s'", title[:60])
            else:
                result = parse_json_response(raw)
                if result:
                    return result, True
        except Exception as e:
            logger.warning("Groq summarization failed for '%s': %s", title[:60], e)

        return fallback_summary(title, content), False

    def generate_text(self, prompt: str, max_output_tokens: int = 4096) -> str:
        """General-purpose text generation (newsletter assembly). Returns
        empty string on failure/truncation — same contract as GeminiClient."""
        try:
            text, truncated = self._call(prompt, max_output_tokens=max_output_tokens)
            if truncated:
                logger.warning(
                    "Groq response truncated (hit max_tokens=%d) — "
                    "discarding partial output.", max_output_tokens
                )
                return ""
            return text
        except Exception as e:
            logger.warning("Groq generate_text failed: %s", e)
            return ""

    # ── private ────────────────────────────────────────────────────────────

    def _call(
        self, prompt: str, max_retries: int = 3, max_output_tokens: int = 1024,
        want_json: bool = False,
    ) -> tuple[str, bool]:
        """Make a Groq chat-completion call with exponential backoff on 429s.

        Returns (text, was_truncated), reading finish_reason == "length" as
        Groq's equivalent of Gemini's finishReason == "MAX_TOKENS".

        want_json=True sets response_format: {"type": "json_object"}, Groq's
        OpenAI-compatible JSON mode — this guarantees syntactically valid
        JSON at the API level. This is the actual fix for a real production
        failure ("Expecting ',' delimiter...") that strict=False in
        llm/common.py couldn't recover from, because the JSON was
        structurally broken (most likely an unescaped quote inside a string
        value), not just containing a stray control character. Never set
        this for generate_text's newsletter prose, which must stay Markdown.
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,        # Low temp for factual summaries
            "max_tokens": max_output_tokens,
        }
        if want_json:
            payload["response_format"] = {"type": "json_object"}

        for attempt in range(max_retries):
            try:
                resp = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=30)

                if resp.status_code == 429:
                    # Rate limited — back off and retry. Groq's free tier is
                    # generous enough that this should be rare in normal use;
                    # if it does happen, llm/router.py's circuit breaker will
                    # move on to rule-based fallback rather than let this
                    # block the run the way unbounded retries did with Gemini.
                    wait = 2 ** attempt * 5   # 5s, 10s, 20s
                    logger.warning("Groq rate limited, waiting %ds (attempt %d/%d)...",
                                   wait, attempt + 1, max_retries)
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                data = resp.json()
                return _extract_text(data), _is_truncated(data)

            except requests.exceptions.Timeout:
                logger.warning("Groq API timeout (attempt %d/%d)", attempt + 1, max_retries)
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
            except requests.exceptions.RequestException as e:
                logger.warning("Groq API error (attempt %d/%d): %s", attempt + 1, max_retries, e)
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)

        raise RuntimeError(f"Groq API failed after {max_retries} attempts")


# ── helpers ───────────────────────────────────────────────────────────────────

def _extract_text(response_data: dict) -> str:
    """Pull the text content out of a Groq (OpenAI-shaped) chat completion response."""
    try:
        return response_data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise ValueError(f"Unexpected Groq response shape: {e}") from e


def _is_truncated(response_data: dict) -> bool:
    """Groq's finish_reason == 'length' is the OpenAI-style equivalent of
    Gemini's finishReason == 'MAX_TOKENS'. Fails open (False) on unexpected
    shape, same reasoning as gemini._is_truncated."""
    try:
        return response_data["choices"][0].get("finish_reason") == "length"
    except (KeyError, IndexError):
        return False