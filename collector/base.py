"""
Base interface every collector implements.

Keeping this contract tiny on purpose: a collector's only job is to return
a list of normalized Article objects. Cleaning, deduplication, ranking,
and classification all happen later in the pipeline (processing/), per the
spec's separation of "Step 1: Collect" from "Step 3+: Clean/Dedupe/Rank".
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from config.loader import AppConfig
from database.models import Article


class BaseCollector(ABC):
    source_type: str = "base"

    def __init__(self, config: AppConfig):
        self.config = config

    @abstractmethod
    def collect(self) -> list[Article]:
        """Fetch and return normalized articles. Must not raise on a single
        item failure — log and skip instead, so one bad feed/entry doesn't
        take down the whole collection run."""
        raise NotImplementedError
