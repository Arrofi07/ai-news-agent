"""
processing/cleaner.py — clean raw article content before ranking and summarization.

RSS feeds and arXiv abstracts arrive with HTML tags, tracking params in URLs,
boilerplate navigation text, excessive whitespace, and other noise. This module
strips all of that to give the LLM (Phase 4) clean prose to work with.

Design: pure functions on strings — no DB access, no side effects. Easy to
test and easy to swap out individual steps if needed.
"""

from __future__ import annotations

import html
import re
import unicodedata
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from bs4 import BeautifulSoup

# Phrases that appear in boilerplate navigation / footer text in RSS feeds.
# These get stripped line-by-line from content.
_BOILERPLATE_PATTERNS: list[re.Pattern] = [
    re.compile(r, re.IGNORECASE)
    for r in [
        r"^(read more|continue reading|click here|learn more|full story)[.\s…]*$",
        r"^(subscribe|sign up|newsletter|follow us)[.\s…]*$",
        r"^(share this|tweet|facebook|linkedin)[.\s…]*$",
        r"^\s*[\[\(]?\s*(image|photo|illustration|credit|source)\s*:.*$",
        r"^(tags|categories|topics)\s*:.*$",
        r"^(posted in|filed under).*$",
        r"^\d+\s+min(ute)?\s+read\s*$",     # "5 min read" navigation artifact
    ]
]

# URL query parameters that are purely tracking and carry no semantic value.
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "msclkid", "ref", "source", "_hsenc", "_hsmi",
    "mc_cid", "mc_eid",
}


def clean_content(raw: str | None) -> str:
    """Full cleaning pipeline: HTML → plain text → normalize → strip boilerplate."""
    if not raw:
        return ""
    text = _strip_html(raw)
    text = _decode_entities(text)
    text = _normalize_unicode(text)
    text = _remove_boilerplate_lines(text)
    text = _normalize_whitespace(text)
    return text.strip()


def clean_url(url: str | None) -> str:
    """Remove tracking query parameters from a URL, keeping everything else."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query, keep_blank_values=True)
        cleaned = {k: v for k, v in params.items() if k.lower() not in _TRACKING_PARAMS}
        new_query = urlencode(cleaned, doseq=True)
        return urlunparse(parsed._replace(query=new_query))
    except Exception:
        return url


def clean_title(title: str | None) -> str:
    """Normalize a title: strip HTML tags, decode entities, collapse whitespace,
    strip trailing ' | Source Name' style suffixes."""
    if not title:
        return ""
    # Strip HTML first — some RSS feeds embed <b>, <em> etc. in titles
    title = _strip_html(title)
    title = html.unescape(title)
    title = _normalize_unicode(title)
    # Remove trailing "| Source Name" or "- Source Name" suffixes.
    # The pattern requires at least one space before the separator to avoid
    # cutting into hyphenated product names like "GPT-X" or "Claude-3.5".
    title = re.sub(r"\s+[\|–—]\s+\w[\w\s]{2,40}$", "", title).strip()
    return _normalize_whitespace(title)


# ── private helpers ──────────────────────────────────────────────────────────

def _strip_html(text: str) -> str:
    """Remove HTML tags. Uses BeautifulSoup for robustness on malformed HTML."""
    try:
        soup = BeautifulSoup(text, "lxml")
        # Replace <br> and <p> with newlines before extracting text
        for tag in soup.find_all(["br", "p", "div", "li", "h1", "h2", "h3", "h4"]):
            tag.insert_before("\n")
        return soup.get_text(separator=" ")
    except Exception:
        # Fallback: crude regex strip
        return re.sub(r"<[^>]+>", " ", text)


def _decode_entities(text: str) -> str:
    return html.unescape(text)


def _normalize_unicode(text: str) -> str:
    """Normalize to NFC (composed form) and replace common fancy punctuation
    with ASCII equivalents so downstream text comparison is stable."""
    text = unicodedata.normalize("NFC", text)
    replacements = {
        "\u2018": "'", "\u2019": "'",   # curly single quotes
        "\u201c": '"', "\u201d": '"',   # curly double quotes
        "\u2013": "-", "\u2014": "-",   # en-dash, em-dash
        "\u2026": "...",                # ellipsis
        "\u00a0": " ",                  # non-breaking space
    }
    for char, replacement in replacements.items():
        text = text.replace(char, replacement)
    return text


def _remove_boilerplate_lines(text: str) -> str:
    """Remove lines that match known boilerplate patterns."""
    lines = text.splitlines()
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if any(p.match(stripped) for p in _BOILERPLATE_PATTERNS):
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def _normalize_whitespace(text: str) -> str:
    """Collapse multiple blank lines → single blank line; collapse spaces."""
    text = re.sub(r"[ \t]+", " ", text)           # multiple spaces/tabs → single space
    text = re.sub(r"\n{3,}", "\n\n", text)        # 3+ newlines → 2
    return text.strip()