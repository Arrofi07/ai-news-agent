"""
llm/router.py — provider chain with a circuit breaker.

This replaces "always use Gemini, retry forever, eventually give up per
article" with: try the primary provider; if it fails repeatedly, switch to
the next provider *for the rest of this run* (not per-article); if every
configured provider is exhausted, fall back to rule-based summaries for
whatever's left.

Why "switch for the rest of the run" instead of per-article fallback:
the one place voice/style consistency actually matters is the newsletter's
LLM-written prose (llm/summarize.py calls generate_text once, for the whole
newsletter, not per-section) — so whichever provider survives Phase 4 is
also the one used to write Phase 5's newsletter, keeping that single big
piece of prose in one voice. Per-article JSON summaries are short factual
fields, not flowing prose, so switching mid-way through those is a much
smaller concern than it would be for the free-form newsletter text.
"""

from __future__ import annotations

from scheduler.logging_setup import get_logger

logger = get_logger(__name__)

# How many consecutive failures on the *current* provider before switching to
# the next one in the chain. 2 (not 1) tolerates a single transient blip
# (a timeout, a one-off 429) without abandoning a provider that's actually fine.
DEFAULT_FAILURE_THRESHOLD = 2


class ProviderChain:
    """
    Wraps an ordered list of (name, client) pairs. Call `record_result(ok)`
    after every real call against `active_client` to let the chain decide
    whether to advance. `active_client` becomes None once every provider in
    the chain has been exhausted — callers should treat that the same as
    "no LLM configured" (i.e. use the rule-based fallback).
    """

    def __init__(
        self,
        clients: list[tuple[str, object]],
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
    ):
        # Filter out any (name, None) pairs so callers can pass
        # "GeminiClient(...) if key else None" directly without pre-filtering.
        self._clients = [c for c in clients if c[1] is not None]
        self._idx = 0
        self._consecutive_failures = 0
        self._threshold = failure_threshold

    @property
    def active_name(self) -> str | None:
        """Name of the currently active provider, or None if exhausted."""
        if self._idx < len(self._clients):
            return self._clients[self._idx][0]
        return None

    @property
    def active_client(self):
        """The currently active client object, or None if exhausted."""
        if self._idx < len(self._clients):
            return self._clients[self._idx][1]
        return None

    def record_result(self, ok: bool) -> bool:
        """
        Report whether the last call against `active_client` succeeded.
        Returns True if this call caused a switch to the next provider (or to
        "exhausted" / rule-based) — callers can use that to decide whether to
        immediately retry the just-failed article with the new active_client.
        """
        if ok:
            self._consecutive_failures = 0
            return False

        self._consecutive_failures += 1
        if self._consecutive_failures < self._threshold:
            return False

        # Threshold hit — advance to the next provider (or past the end,
        # meaning "exhausted, use rule-based fallback for the rest of the run").
        previous_name = self.active_name
        self._idx += 1
        self._consecutive_failures = 0
        if self.active_name:
            logger.warning(
                "Provider '%s' failed %d times in a row — switching to '%s' "
                "for the rest of this run.", previous_name, self._threshold, self.active_name
            )
        else:
            logger.warning(
                "Provider '%s' failed %d times in a row and no further "
                "providers are configured — using rule-based summaries for "
                "the rest of this run.", previous_name, self._threshold
            )
        return True