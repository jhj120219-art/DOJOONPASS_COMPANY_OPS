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

from .dashboard import RUN_ID_PROPERTY

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


class DashboardPendingError(ValueError):
    """Raised when dashboard_pending.json exists but cannot be read as a
    valid pending set (bad JSON, wrong shape, malformed record).

    Never raised for a simply-missing file — that is an empty set.
    State Recovery 통일 (CEO 승인 A안): same contract as
    `collector.state.CollectorStateError`, per docs/10 §46.

    Note the division of labour with `drain_pending()`: this names the
    failure, and `drain_pending()` absorbs it, because CEO Decision ④ says a
    Dashboard problem must never interrupt the Runtime.
    """


def load_pending(path: Path) -> list[PendingDashboardRecord]:
    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DashboardPendingError(
            f"dashboard pending file is corrupted: {path} ({exc})"
        ) from exc

    if not isinstance(data, dict):
        raise DashboardPendingError(
            f"dashboard pending file must contain a JSON object: {path}"
        )

    entries = data.get("entries", [])
    if not isinstance(entries, list):
        raise DashboardPendingError(
            f"dashboard pending file has an invalid entries field: {path}"
        )

    try:
        return [PendingDashboardRecord.from_dict(e) for e in entries]
    except (AttributeError, KeyError, TypeError) as exc:
        raise DashboardPendingError(
            f"dashboard pending file has a malformed record: {path} ({exc})"
        ) from exc


def save_all(path: Path, records: list[PendingDashboardRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"entries": [r.to_dict() for r in records]}
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            # Durability, not only atomicity — see reporter/local_output.py.
            handle.flush()
            os.fsync(handle.fileno())
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


class DrainPendingResult(tuple):
    """`(recorded, still_pending)` plus why the last failure happened.

    A tuple subclass so every existing
    `recorded, still_pending = drain_pending(...)` call site keeps working
    unchanged — the same technique `runsummary.RunResult` uses to add
    `.summary` to a 5-tuple without touching 219 unpacking sites.

    `last_reason` exists because the reason used to be thrown away. A record
    Notion permanently refuses (a Select value it will not accept, say) came
    back around every run with nothing but `attempt_count` climbing, and no
    trace anywhere of what Notion actually said. That is the same diagnostic
    blank BUG-13 closed for Notion Sync; the Dashboard queue still had it.

    Only the last reason is kept, not all of them: the queue drains in one
    pass against one Notion, so the failures in a pass are near-always the
    same failure, and one bounded string keeps the log line bounded.

    No `__slots__`, for the same reason `app.runner.RunResult` declares
    none: a tuple subclass that declares it cannot carry the instance
    attribute the class exists to add.
    """

    def __new__(cls, recorded: int, still_pending: int, last_reason: str | None = None):
        self = super().__new__(cls, (recorded, still_pending))
        self._last_reason = last_reason  # type: ignore[attr-defined]
        return self

    @property
    def recorded(self) -> int:
        return self[0]

    @property
    def still_pending(self) -> int:
        return self[1]

    @property
    def last_reason(self) -> str | None:
        return self._last_reason  # type: ignore[attr-defined]


def drain_pending(path: Path, client) -> DrainPendingResult:
    """Retry every pending Dashboard record. Never raises.

    Returns (recorded, still_pending) — a `DrainPendingResult`, which
    unpacks as that pair and additionally carries `.last_reason`. A record
    that succeeds is removed; one that fails again stays queued with an
    incremented attempt_count, to be retried by a later Runner execution.
    Like `record_run()`, this must never interrupt the Runtime, so all
    exceptions are absorbed here.
    """
    try:
        records = load_pending(path)
    except DashboardPendingError as exc:
        # CEO Decision ④: a Dashboard problem must never interrupt the
        # Runtime, and this function's contract is "Never raises". A damaged
        # pending file is therefore reported as "nothing to drain" rather than
        # propagated — the file is left untouched for an operator to inspect
        # (docs/10 §46: 프로그램이 임의로 삭제하지 않는다). The reason travels
        # out even so: "nothing to drain" and "the queue file is corrupt" are
        # the same numbers and very different situations.
        return DrainPendingResult(0, 0, str(exc))

    if not records:
        return DrainPendingResult(0, 0)

    recorded = 0
    last_reason: str | None = None
    remaining: list[PendingDashboardRecord] = []
    for record in records:
        try:
            # Find-before-create for the same reason `record_run()` does it:
            # this queue exists precisely because a write appeared to fail,
            # and "appeared to" includes writes that actually landed.
            client.find_or_create_by_title(
                property_name=RUN_ID_PROPERTY,
                value=record.run_id,
                properties=record.properties,
            )
        except Exception as exc:  # noqa: BLE001  (CEO ④: Runtime을 절대 중단시키지 않는다)
            last_reason = str(exc)
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
    except OSError as exc:
        # The retries themselves already happened; failing to record that
        # only means the same records are attempted again next run, which
        # find-before-create now makes harmless. Still worth naming.
        last_reason = last_reason or f"could not update the pending file: {exc}"

    return DrainPendingResult(recorded, len(remaining), last_reason)
