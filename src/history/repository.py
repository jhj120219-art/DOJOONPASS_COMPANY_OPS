"""History Repository interface (COO Architecture Decision, Phase 4.35).

Sits between History Filter and Daily History Generator:

    History Filter -> Repository -> Daily History Generator

A Repository's only job: hold HistoryCandidate objects that came out of
HistoryFilter, and let them be looked up again later. It is not History
Markdown, not Daily/Monthly History, and not the Desktop 4 Local Master —
none of that is built on top of this Interface yet.
"""

from __future__ import annotations

import abc

from .result import HistoryCandidate, HistoryDecision


class HistoryRepository(abc.ABC):
    @abc.abstractmethod
    def save(self, candidate: HistoryCandidate, *, overwrite: bool = False) -> bool:
        """Persist a KEEP or REVIEW candidate.

        A DROP candidate is not stored — that is the expected, non-error
        outcome for DROP, not a failure. Returns True iff something was
        actually written, False for a DROP candidate.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def get(self, history_id: str) -> HistoryCandidate | None:
        """Look up a single previously saved candidate by history_id."""
        raise NotImplementedError

    @abc.abstractmethod
    def list(self, decision: HistoryDecision | None = None) -> list[HistoryCandidate]:
        """List stored candidates, optionally filtered to KEEP or REVIEW.

        Filtering by DROP always returns an empty list — nothing is ever
        stored there.
        """
        raise NotImplementedError
