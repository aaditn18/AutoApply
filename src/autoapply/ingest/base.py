"""Shared ingest plumbing.

Every source (`greenhouse.py`, `lever.py`, …) implements `JobSource` and
yields uniform `RawJob` records. The pipeline (`cli.ingest`) is the only
thing that knows how to walk multiple sources in parallel — sources
themselves are pure generators over a single board token.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class RawJob:
    """A just-ingested posting, before dedup + scoring.

    All fields are best-effort — when a source can't fill one, leave it empty.
    Downstream stages (select + congregate + execute) receive the same shape.
    """

    source: str                  # "greenhouse" | "lever" | "ycwaas" | ...
    source_id: str               # stable ID from the source (GH job_id, Lever posting id)
    board_token: str             # e.g. "airbnb" for boards.greenhouse.io/airbnb
    url: str
    title: str
    company: str
    location: str = ""
    department: str = ""
    description: str = ""        # HTML stripped — caller sanitizes + injection-scans
    posted_at: str = ""          # ISO8601 when available
    updated_at: str = ""
    employment_type: str = ""    # full-time / intern / contract — source-dependent
    metadata: dict[str, str] = field(default_factory=dict)


class JobSource(ABC):
    """Abstract base for a job board source. Subclasses implement
    `fetch_board(token)` to yield `RawJob` objects."""

    name: str = "unknown"

    @abstractmethod
    def fetch_board(self, board_token: str) -> Iterable[RawJob]:
        """Yield all active postings for one board token."""
        raise NotImplementedError

    # Convenience: iterate many boards in a single call.
    def fetch_many(self, tokens: Iterable[str]) -> Iterable[RawJob]:
        for tok in tokens:
            yield from self.fetch_board(tok)
