"""Collector Interface (docs/03_COLLECTOR_SPEC.md, narrowed for this design phase).

Collector's job, and only its job: take one Event (as JSON text/bytes or an
already-parsed mapping), validate it against the Event Schema (Phase 1's
src/events — Collector does not redefine validation), check it against a
SeenEventStore for duplicates, and return a CollectorResult.

Collector does not know about Transport (how the Event arrived) or Reporter
(who made it), and it does not call Notion or the History Pipeline —
routing an ACCEPTED Event onward is a later orchestration layer's job, not
Collector's.

No Runtime behavior lives here: no scanning runtime/events/incoming, no
writing to processed/rejected, no collector_state.json, no logging. That is
Collector Runtime, a later phase built on top of this Interface.
"""

from __future__ import annotations

import json
from typing import Mapping

from events import Event, validate_event

from .result import CollectorError, CollectorInput, CollectorResult, CollectorStatus
from .seen_store import SeenEventStore


class Collector:
    def __init__(self, *, seen_store: SeenEventStore):
        self._seen_store = seen_store

    def unmark_seen(self, event_id: str) -> None:
        """Roll back the mark_seen() collect() performed for an ACCEPTED Event
        that the caller then failed to consume.

        collect() decides ACCEPTED and records the id in one step, but the
        Event is not truly consumed until the Runtime has moved its file out
        of `incoming/`. Only the Runtime knows whether that succeeded, so only
        the Runtime can undo the mark — this is the seam for it
        (docs/03 §53's "one file's failure must not affect the rest", applied
        to the duplicate-check state as well as the batch).

        Delegates to the injected SeenEventStore, which decides whether a
        rollback is possible at all (the base class defaults to a no-op).
        """
        self._seen_store.unmark_seen(event_id)

    def collect(self, raw: CollectorInput) -> CollectorResult:
        if isinstance(raw, (str, bytes)):
            try:
                data = json.loads(raw)
            except (TypeError, ValueError, RecursionError) as exc:
                return CollectorResult(
                    status=CollectorStatus.REJECTED,
                    event=None,
                    errors=(f"invalid JSON: {exc}",),
                    raw_input=raw,
                )
        else:
            data = raw

        if not isinstance(data, Mapping):
            return CollectorResult(
                status=CollectorStatus.REJECTED,
                event=None,
                errors=("event data must be a JSON object",),
                raw_input=raw,
            )

        errors = validate_event(data)
        if errors:
            return CollectorResult(
                status=CollectorStatus.REJECTED,
                event=None,
                errors=tuple(errors),
                raw_input=raw,
            )

        event = Event.from_dict(data)

        try:
            already_seen = self._seen_store.is_seen(event.event_id)
        except Exception as exc:
            raise CollectorError(
                f"duplicate check failed for event_id={event.event_id!r}: {exc}"
            ) from exc

        if already_seen:
            return CollectorResult(
                status=CollectorStatus.DUPLICATE,
                event=event,
                errors=(),
                raw_input=raw,
            )

        try:
            self._seen_store.mark_seen(event.event_id)
        except Exception as exc:
            raise CollectorError(
                f"failed to record event_id={event.event_id!r} as seen: {exc}"
            ) from exc

        return CollectorResult(
            status=CollectorStatus.ACCEPTED,
            event=event,
            errors=(),
            raw_input=raw,
        )
