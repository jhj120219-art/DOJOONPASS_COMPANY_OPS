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


def load_queue(path: Path) -> list[RetryQueueEntry]:
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    entries = data.get("entries", [])
    return [RetryQueueEntry.from_dict(e) for e in entries]


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


def enqueue(path: Path, event: Event, *, now: datetime | None = None) -> None:
    """Upsert: a re-failing event's existing entry is updated (attempt_count
    incremented, event_data refreshed) rather than duplicated — dedup is by
    `event_id`, matching Collector's own event_id-based duplicate model.
    """
    now = now or datetime.now().astimezone()
    entries = load_queue(path)
    existing_index = next((i for i, e in enumerate(entries) if e.event_id == event.event_id), None)

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

    save_queue(path, entries)


def dequeue(path: Path, event_id: str) -> None:
    """No-op if `event_id` is not queued (idempotent — safe to call once
    per successful sync regardless of whether it came from the queue or
    from this run's freshly-collected events)."""
    entries = load_queue(path)
    remaining = [e for e in entries if e.event_id != event_id]
    if len(remaining) != len(entries):
        save_queue(path, remaining)
