"""
llm/common.py — provider-agnostic helpers shared by GeminiClient and GroqClient.

Both providers speak plain JSON-in-text and both need the same rule-based
fallback when no LLM is available. Keeping this in one place means a fix
here (e.g. a JSON-parsing edge case) benefits every provider automatically,
instead of drifting between two near-identical copies.
"""

from __future__ import annotations

import json
import re

from scheduler.logging_setup import get_logger

logger = get_logger(__name__)


def parse_json_response(text: str) -> dict | None:
    """
    Parse a JSON response from an LLM, handling common issues:
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
        # strict=False tolerates literal control characters (raw newlines,
        # tabs) inside string values — LLMs frequently emit these even when
        # explicitly asked for valid JSON (e.g. a multi-line "summary" field
        # with an actual newline instead of an escaped \n). Without this,
        # an otherwise well-formed response gets rejected outright over a
        # single stray character, which is exactly what happened with a
        # real Groq response ("Invalid control character at: line 2...").
        # The JSON structure itself is still fully validated either way.
        return json.loads(text, strict=False)
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse LLM JSON response: %s\nRaw: %s", e, text[:200])
        return None


def fallback_summary(title: str, content: str) -> dict:
    """
    Rule-based summary when no LLM is available (no provider configured, or
    every configured provider has failed for this run).
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