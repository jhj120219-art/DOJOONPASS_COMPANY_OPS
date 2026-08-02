"""History Review Layer (COO Architecture Decision, Phase 4.8).

Lets a human fill in the Decision Context fields Phase 4.7 added to
HistoryCandidate (decision_context, expected_outcome, actual_outcome,
lessons_learned) — fields nothing populates automatically, since Event
Schema was deliberately left unchanged (see Phase 4.7's report).

Reviewer only ever edits those four fields. It never changes
filter_result (KEEP/DROP/REVIEW stays whatever HistoryFilter decided —
promoting a REVIEW candidate to KEEP is not part of this Phase), never
creates or deletes a candidate, and only operates on what a
HistoryRepository already has stored. DROP candidates were never stored
there in the first place (docs/05_HISTORY_PIPELINE_SPEC.md section 49),
so there is nothing to review for them.
"""

from __future__ import annotations

import abc
import dataclasses
from typing import Any

from .repository import HistoryRepository
from .result import HistoryCandidate, HistoryDecision

_UNSET: Any = object()
"""Sentinel distinguishing "field not mentioned in this review" from an
explicit `None` (which clears a previously-set value back to empty)."""


class HistoryReviewError(Exception):
    """Raised when a review targets a history_id not found in the Repository."""


class HistoryReviewer(abc.ABC):
    @abc.abstractmethod
    def list_reviewable(self, decision: HistoryDecision | None = None) -> list[HistoryCandidate]:
        """List stored candidates available for review (KEEP and/or REVIEW)."""
        raise NotImplementedError

    @abc.abstractmethod
    def submit_review(
        self,
        history_id: str,
        *,
        decision_context: Any = _UNSET,
        expected_outcome: Any = _UNSET,
        actual_outcome: Any = _UNSET,
        lessons_learned: Any = _UNSET,
    ) -> HistoryCandidate:
        """Update only the fields explicitly passed; save the result back.

        A field left unset keeps its current stored value. Passing `None`
        explicitly clears that field. Raises HistoryReviewError if
        `history_id` has no stored candidate.
        """
        raise NotImplementedError


class RepositoryHistoryReviewer(HistoryReviewer):
    """HistoryReviewer backed directly by a HistoryRepository — no separate storage."""

    def __init__(self, repository: HistoryRepository):
        self._repository = repository

    def list_reviewable(self, decision: HistoryDecision | None = None) -> list[HistoryCandidate]:
        return self._repository.list(decision=decision)

    def submit_review(
        self,
        history_id: str,
        *,
        decision_context: Any = _UNSET,
        expected_outcome: Any = _UNSET,
        actual_outcome: Any = _UNSET,
        lessons_learned: Any = _UNSET,
    ) -> HistoryCandidate:
        candidate = self._repository.get(history_id)
        if candidate is None:
            raise HistoryReviewError(f"no stored candidate with history_id={history_id!r}")

        updates = {}
        if decision_context is not _UNSET:
            updates["decision_context"] = decision_context
        if expected_outcome is not _UNSET:
            updates["expected_outcome"] = expected_outcome
        if actual_outcome is not _UNSET:
            updates["actual_outcome"] = actual_outcome
        if lessons_learned is not _UNSET:
            updates["lessons_learned"] = lessons_learned

        if not updates:
            return candidate

        updated = dataclasses.replace(candidate, **updates)
        self._repository.save(updated, overwrite=True)
        return updated
