"""
newsletter/email.py — send the weekly newsletter via Resend API.

Uses Resend's REST API directly via requests (no SDK needed).
Sender: onboarding@resend.dev (Resend's shared domain, free tier)
Recipient: the email you signed up with on Resend.

Resend API docs: https://resend.com/docs/api-reference/emails/send-email
"""

from __future__ import annotations

import requests

from scheduler.logging_setup import get_logger

logger = get_logger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"


def send_newsletter(
    html_content: str,
    markdown_content: str,
    week_label: str,
    api_key: str,
    to_email: str,
    from_email: str = "onboarding@resend.dev",
) -> bool:
    """
    Send the newsletter via Resend.

    Args:
        html_content:     Full HTML newsletter string
        markdown_content: Markdown version (sent as plain-text fallback)
        week_label:       e.g. "Week 28 - 2026"
        api_key:          Resend API key (from RESEND_API_KEY secret)
        to_email:         Recipient address
        from_email:       Sender address (default: Resend shared domain)

    Returns:
        True on success, False on failure (never raises — pipeline keeps running)
    """
    subject = f"🤖 AI Weekly Intelligence Report — {week_label}"

    payload = {
        "from": from_email,
        "to": [to_email],
        "subject": subject,
        "html": html_content,
        "text": _html_to_plain(markdown_content),
    }

    try:
        resp = requests.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        logger.info("Newsletter sent via Resend. id=%s to=%s", data.get("id"), to_email)
        return True

    except requests.exceptions.HTTPError as e:
        logger.error("Resend API HTTP error: %s — %s", e, resp.text)
        return False
    except Exception as e:
        logger.error("Failed to send newsletter via Resend: %s", e)
        return False


def _html_to_plain(markdown: str) -> str:
    """
    Use the Markdown content as the plain-text fallback for email clients
    that don't render HTML. Strip Markdown syntax markers minimally so it's
    still readable as plain text.
    """
    import re
    text = markdown
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)   # headings
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)                  # bold
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r"\1 (\2)", text)        # links
    text = re.sub(r"^>\s+", "", text, flags=re.MULTILINE)          # blockquotes
    text = re.sub(r"---+", "---", text)                            # hr
    return text.strip()
