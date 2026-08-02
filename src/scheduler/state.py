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


@dataclass
class SchedulerState:
    last_successful_daily_close: date | None = None


def load_state(state_path: Path) -> SchedulerState:
    if not state_path.exists():
        return SchedulerState()

    data = json.loads(state_path.read_text(encoding="utf-8"))
    value = data.get("last_successful_daily_close")
    return SchedulerState(
        last_successful_daily_close=date.fromisoformat(value) if value else None
    )


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
        os.replace(tmp_path, state_path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
