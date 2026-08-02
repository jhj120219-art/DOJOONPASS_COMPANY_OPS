"""Collector's Input/Output contract.

Defines only the shapes this design phase commits to: what Collector
receives (CollectorInput), what it returns (CollectorResult /
CollectorStatus), and what signals an internal failure (CollectorError).
No Runtime behavior (file scanning, state persistence, logging) lives here.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Mapping, Union

from events import Event

CollectorInput = Union[str, bytes, Mapping[str, Any]]
"""Raw form Collector accepts for one Event: a JSON string/bytes, or an
already-parsed mapping. Collector does not care how this arrived — that is
Transport's job, which Collector does not know about."""


class CollectorStatus(enum.Enum):
    ACCEPTED = "ACCEPTED"
    DUPLICATE = "DUPLICATE"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class CollectorResult:
    """Outcome of a single Collector.collect() call.

    - ACCEPTED: `event` is a validated, not-previously-seen Event.
    - DUPLICATE: `event` is a validated Event whose event_id was already seen.
    - REJECTED: `event` is None; `errors` explains why (JSON/schema/business).

    `raw_input` is always the exact value passed to collect(), so a later
    Runtime layer can preserve the original Event content even for a
    REJECTED result (docs/03_COLLECTOR_SPEC.md section 11: rejected Events
    are preserved, never silently corrected).

    Collector does not decide what happens next (Notion, History) — routing
    is an orchestration concern outside this Interface.
    """

    status: CollectorStatus
    event: Event | None
    errors: tuple[str, ...]
    raw_input: CollectorInput


class CollectorError(Exception):
    """Raised when Collector cannot complete a determination at all — e.g.
    its SeenEventStore dependency failed.

    This is the retryable "internal problem" case (docs/03_COLLECTOR_SPEC.md
    section 32), distinct from REJECTED, which is a definitive validation
    judgment that is never retried.
    """
