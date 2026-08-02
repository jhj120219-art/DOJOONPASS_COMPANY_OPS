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


class InMemorySeenEventStore(SeenEventStore):
    """Test/dev-only SeenEventStore backed by a plain set. Not persistent."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def is_seen(self, event_id: str) -> bool:
        return event_id in self._seen

    def mark_seen(self, event_id: str) -> None:
        self._seen.add(event_id)
