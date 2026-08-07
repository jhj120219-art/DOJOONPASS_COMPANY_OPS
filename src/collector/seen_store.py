"""Duplicate-detection dependency Collector relies on.

Whether "have I seen this event_id before" is tracked in a JSON state file
(docs/03_COLLECTOR_SPEC.md sections 36-38 sketch `runtime/state/
collector_state.json`), a database, or something else is a Runtime/storage
decision explicitly deferred — same reasoning as deferring which Transport
to build. This module defines only the seam. InMemorySeenEventStore is a
test/dev double, not the eventual Runtime state manager.
"""

from __future__ import annotations

import abc


class SeenEventStore(abc.ABC):
    """Tracks which event_ids Collector has already accepted."""

    @abc.abstractmethod
    def is_seen(self, event_id: str) -> bool:
        raise NotImplementedError

    @abc.abstractmethod
    def mark_seen(self, event_id: str) -> None:
        raise NotImplementedError

    def unmark_seen(self, event_id: str) -> None:
        """Undo a `mark_seen()` whose Event was not actually consumed.

        Collector marks an event_id as seen while judging it ACCEPTED, but
        the Runtime only finishes consuming that Event once it has moved the
        file out of `incoming/`. If the move fails, the id has been burned:
        the file is retried on the next run, is judged DUPLICATE, and — since
        app/runner.py only routes ACCEPTED events onward — never reaches
        Notion Sync or History Filter. The History Candidate is lost for good
        (Audit BUG-9, and the main ingredient of BUG-20's measured loss).

        CEO-approved B안: keep `collect()`'s existing contract and give the
        Runtime a way to roll the mark back on the failure path.

        Not abstract on purpose — a store that cannot roll back (or a double
        that does not need to) inherits this no-op rather than being forced to
        implement it, exactly as `NotionTransport.search_pages()` does.
        """
        return None


class InMemorySeenEventStore(SeenEventStore):
    """Test/dev-only SeenEventStore backed by a plain set. Not persistent."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def is_seen(self, event_id: str) -> bool:
        return event_id in self._seen

    def mark_seen(self, event_id: str) -> None:
        self._seen.add(event_id)

    def unmark_seen(self, event_id: str) -> None:
        self._seen.discard(event_id)
