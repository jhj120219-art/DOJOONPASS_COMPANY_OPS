"""Notion Sync Retry Queue (CEO Policy Decision — Notion Retry Architecture
Plan A): a Notion Sync failure is persisted here so the *next* Runner
execution retries it first, instead of the event being silently dropped
from Notion Sync consideration the moment Collector moves its file out of
`incoming/` (docs/03_COLLECTOR_SPEC.md §28/§32/§33, docs/12 §12 Retry
Policy — the previously-unimplemented gap this Sprint closes).

Storage shape matches this project's other runtime state files
(collector/state.py, scheduler/state.py, backup/state.py): one JSON file,
atomic write via tempfile + os.replace, missing file means "empty queue".
No new storage technology, no new external dependency.

One entry per `event_id` (upsert, never duplicated) — the full original
Event is stored inline (`Event.to_dict()`) so a retry never needs to
relocate the source file in `runtime/events/processed/`, and the queue
stays self-contained and independently inspectable.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from events import Event

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUEUE_PATH = PROJECT_ROOT / "runtime" / "state" / "notion_retry_queue.json"


@dataclass(frozen=True)
class RetryQueueEntry:
    event_id: str
    project_id: str
    event_data: dict[str, Any]
    added_at: str
    attempt_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "project_id": self.project_id,
            "event_data": self.event_data,
            "added_at": self.added_at,
            "attempt_count": self.attempt_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RetryQueueEntry":
        return cls(
            event_id=data["event_id"],
            project_id=data["project_id"],
            event_data=data["event_data"],
            added_at=data["added_at"],
            attempt_count=data.get("attempt_count", 0),
        )

    def to_event(self) -> Event:
        return Event.from_dict(self.event_data)


class RetryQueueError(ValueError):
    """Raised when notion_retry_queue.json exists but cannot be read as a
    valid queue (bad JSON, wrong shape, malformed entry).

    Never raised for a simply-missing file — that is an empty queue.
    State Recovery 통일 (CEO 승인 A안): same contract as
    `collector.state.CollectorStateError`, per docs/10 §46. Naming the
    failure matters especially here, because README RULE 5 puts Notion off
    the History critical path — an operator must be able to tell a damaged
    Notion queue apart from a damaged History state at a glance.
    """


def load_queue(path: Path) -> list[RetryQueueEntry]:
    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RetryQueueError(
            f"notion retry queue file is corrupted: {path} ({exc})"
        ) from exc

    if not isinstance(data, dict):
        raise RetryQueueError(
            f"notion retry queue file must contain a JSON object: {path}"
        )

    entries = data.get("entries", [])
    if not isinstance(entries, list):
        raise RetryQueueError(
            f"notion retry queue file has an invalid entries field: {path}"
        )

    try:
        return [RetryQueueEntry.from_dict(e) for e in entries]
    except (AttributeError, KeyError, TypeError) as exc:
        raise RetryQueueError(
            f"notion retry queue file has a malformed entry: {path} ({exc})"
        ) from exc


def save_queue(path: Path, entries: list[RetryQueueEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"entries": [e.to_dict() for e in entries]}
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def upsert_entry(
    entries: list[RetryQueueEntry], event: Event, *, now: datetime | None = None
) -> None:
    """In-memory half of `enqueue()`: upsert `event` into `entries`.

    Retry Queue Batch Save (CEO 승인 B안). `enqueue()` reads and rewrites the
    whole file on every single call, so a Notion outage affecting n Events
    costs O(n^2) bytes — measured this Sprint at 7.9 ms/enqueue for a 50-entry
    queue rising to 19.3 ms at 800 entries (551KB). Exposing the list
    operation lets `app/runner.py` load once, apply every change in memory,
    and write once per run, while `enqueue()` keeps its existing contract for
    every other caller.

    Dedup and semantics are unchanged: one entry per `event_id`,
    `attempt_count` incremented, `event_data` refreshed, `added_at` preserved.
    """
    now = now or datetime.now().astimezone()
    existing_index = next(
        (i for i, e in enumerate(entries) if e.event_id == event.event_id), None
    )

    if existing_index is None:
        entries.append(
            RetryQueueEntry(
                event_id=event.event_id,
                project_id=event.project_id,
                event_data=event.to_dict(),
                added_at=now.isoformat(timespec="seconds"),
                attempt_count=1,
            )
        )
    else:
        old = entries[existing_index]
        entries[existing_index] = RetryQueueEntry(
            event_id=old.event_id,
            project_id=old.project_id,
            event_data=event.to_dict(),
            added_at=old.added_at,
            attempt_count=old.attempt_count + 1,
        )


def remove_entry(entries: list[RetryQueueEntry], event_id: str) -> bool:
    """In-memory half of `dequeue()`. Returns True if anything was removed,
    so a batching caller knows whether the file needs rewriting at all."""
    remaining = [e for e in entries if e.event_id != event_id]
    if len(remaining) == len(entries):
        return False
    entries[:] = remaining
    return True


def enqueue(path: Path, event: Event, *, now: datetime | None = None) -> None:
    """Upsert: a re-failing event's existing entry is updated (attempt_count
    incremented, event_data refreshed) rather than duplicated — dedup is by
    `event_id`, matching Collector's own event_id-based duplicate model.

    Read-modify-write against the file. `app/runner.py` uses `upsert_entry()`
    with a single load/save per run instead (B안); this remains the simple
    entry point for one-off callers.
    """
    entries = load_queue(path)
    upsert_entry(entries, event, now=now)
    save_queue(path, entries)


def dequeue(path: Path, event_id: str) -> None:
    """No-op if `event_id` is not queued (idempotent — safe to call once
    per successful sync regardless of whether it came from the queue or
    from this run's freshly-collected events)."""
    entries = load_queue(path)
    if remove_entry(entries, event_id):
        save_queue(path, entries)
