"""Scheduler's own state: only last_successful_daily_close.

docs/07_SCHEDULER_CATCHUP_SPEC.md sections 11-12 and 27-29; the on-disk
shape matches docs/06_DAILY_HISTORY_SPEC.md section 27
(`runtime/state/daily_history_state.json`, `{"last_successful_daily_close":
"YYYY-MM-DD"}`).

Deliberately narrow, per this Phase's scope: `pending_retry` and
`backup_pending` (also named in docs/07 section 11) are not tracked here —
Retry/Backup orchestration is out of scope for this Phase entirely.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_PATH = PROJECT_ROOT / "runtime" / "state" / "daily_history_state.json"


class SchedulerStateError(ValueError):
    """Raised when daily_history_state.json exists but cannot be read as
    valid Scheduler state (bad JSON, wrong shape, wrong field types).

    Never raised for a simply-missing file — that starts an empty state.

    State Recovery 통일 (CEO 승인 A안): this mirrors
    `collector.state.CollectorStateError`, which was previously the only
    loader in the project that reported a damaged state file as a typed,
    named error instead of letting a raw JSONDecodeError escape. docs/10 §46
    treats a damaged state file as a normal operational situation to be
    reported, not an arbitrary crash — so every state loader now says so in
    the same way.
    """


@dataclass
class SchedulerState:
    last_successful_daily_close: date | None = None


def load_state(state_path: Path) -> SchedulerState:
    if not state_path.exists():
        return SchedulerState()

    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    # `RecursionError` is what `json.loads()` answers a deeply nested file
    # with, and it is a `RuntimeError` rather than a `ValueError` — so the
    # conversion below, which is the whole of what this promises about a
    # file it cannot read, used to be skipped for that one shape. One home
    # for the reasoning: `ADeeplyNestedStateFileReadsLikeAnyOtherCorruptOneTests`
    # (C65), which also holds the roster this is one of nine entries in.
    except (OSError, ValueError, RecursionError) as exc:
        raise SchedulerStateError(
            f"scheduler state file is corrupted: {state_path} ({exc})"
        ) from exc

    if not isinstance(data, dict):
        raise SchedulerStateError(
            f"scheduler state file must contain a JSON object: {state_path}"
        )

    value = data.get("last_successful_daily_close")
    if value is not None and not isinstance(value, str):
        raise SchedulerStateError(
            f"scheduler state file has an invalid last_successful_daily_close "
            f"field: {state_path}"
        )

    try:
        parsed = date.fromisoformat(value) if value else None
    except ValueError as exc:
        raise SchedulerStateError(
            f"scheduler state file has an unparseable date: {state_path} ({exc})"
        ) from exc

    return SchedulerState(last_successful_daily_close=parsed)


def save_state(state_path: Path, state: SchedulerState) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_successful_daily_close": (
            state.last_successful_daily_close.isoformat()
            if state.last_successful_daily_close is not None
            else None
        )
    }
    fd, tmp_path = tempfile.mkstemp(dir=state_path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            # Durability, not only atomicity — see reporter/local_output.py.
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, state_path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
