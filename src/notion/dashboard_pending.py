"""Pending Operations Dashboard records (CEO Decision ④ retry behaviour).

Mirrors `notion/retry_queue.py` exactly — same single-JSON-file shape, same
atomic write (tempfile + os.replace), same "dedup by id, drain on success"
lifecycle, same "missing file means empty" rule. What it stores is the only
difference, and that difference is deliberate:

`retry_queue.py` entries hold a full **Event** and must round-trip through
`Event.from_dict()`. A Dashboard record is not an Event — it is a set of
already-built Notion properties describing one Runner execution. Putting
it into the Event queue would either corrupt that queue's `to_event()`
contract or force a change to the Event schema, both of which are
forbidden (CEO Decision ①: "Event Schema 변경 금지", "실패한 Event만
Retry Queue에 저장"). So the *mechanism* is reused as instructed, while
Event data and Dashboard data stay in separate files.

Dedup key is `run_id`: one Runner execution can never produce two OPS_RUNS
rows, whether it is recorded on the first attempt or the tenth.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DASHBOARD_PENDING_PATH = (
    PROJECT_ROOT / "runtime" / "state" / "dashboard_pending.json"
)


@dataclass(frozen=True)
class PendingDashboardRecord:
    run_id: str
    properties: dict[str, Any]
    queued_at: str
    attempt_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "properties": self.properties,
            "queued_at": self.queued_at,
            "attempt_count": self.attempt_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PendingDashboardRecord":
        return cls(
            run_id=data["run_id"],
            properties=data["properties"],
            queued_at=data["queued_at"],
            attempt_count=data.get("attempt_count", 0),
        )


def load_pending(path: Path) -> list[PendingDashboardRecord]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [PendingDashboardRecord.from_dict(e) for e in data.get("entries", [])]


def save_all(path: Path, records: list[PendingDashboardRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"entries": [r.to_dict() for r in records]}
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


def save_pending(
    path: Path,
    *,
    run_id: str,
    properties: dict[str, Any],
    now: datetime | None = None,
) -> None:
    """Upsert one pending record. Re-queuing the same `run_id` updates the
    existing entry (attempt_count += 1) instead of duplicating it."""
    now = now or datetime.now().astimezone()
    records = load_pending(path)
    index = next((i for i, r in enumerate(records) if r.run_id == run_id), None)

    if index is None:
        records.append(
            PendingDashboardRecord(
                run_id=run_id,
                properties=properties,
                queued_at=now.isoformat(timespec="seconds"),
                attempt_count=1,
            )
        )
    else:
        old = records[index]
        records[index] = PendingDashboardRecord(
            run_id=old.run_id,
            properties=properties,
            queued_at=old.queued_at,
            attempt_count=old.attempt_count + 1,
        )

    save_all(path, records)


def remove_pending(path: Path, run_id: str) -> None:
    """No-op if `run_id` isn't pending (idempotent)."""
    records = load_pending(path)
    remaining = [r for r in records if r.run_id != run_id]
    if len(remaining) != len(records):
        save_all(path, remaining)


def drain_pending(path: Path, client) -> tuple[int, int]:
    """Retry every pending Dashboard record. Never raises.

    Returns (recorded, still_pending). A record that succeeds is removed;
    one that fails again stays queued with an incremented attempt_count, to
    be retried by a later Runner execution. Like `record_run()`, this must
    never interrupt the Runtime, so all exceptions are absorbed here.
    """
    records = load_pending(path)
    if not records:
        return (0, 0)

    recorded = 0
    remaining: list[PendingDashboardRecord] = []
    for record in records:
        try:
            client.create_project(record.properties)
        except Exception:  # noqa: BLE001  (CEO ④: Runtime을 절대 중단시키지 않는다)
            remaining.append(
                PendingDashboardRecord(
                    run_id=record.run_id,
                    properties=record.properties,
                    queued_at=record.queued_at,
                    attempt_count=record.attempt_count + 1,
                )
            )
            continue
        recorded += 1

    try:
        save_all(path, remaining)
    except OSError:
        pass

    return (recorded, len(remaining))
