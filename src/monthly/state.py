"""Monthly History state (docs/09 §45, §56, §87).

Same atomic-write idiom and typed-error contract as `scheduler/state.py`,
`collector/state.py`, and `agent/state.py` — a fourth instance of an
established pattern, not a new one.

    last_successful_monthly_close   "YYYY-MM", the last month consolidated
                                    (§45). Catch-up is computed from this
                                    rather than from the calendar, because
                                    "is today the 1st?" is the wrong
                                    question when the PC was off on the 1st
                                    (§90).

    dirty_months                    Months whose Daily History changed after
                                    the Monthly was written — a Late Event
                                    (§54-56). Recorded so the next Runner
                                    rebuilds them (§57) instead of leaving a
                                    Monthly that quietly disagrees with its
                                    own Daily files.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_PATH = PROJECT_ROOT / "runtime" / "state" / "monthly_history_state.json"

_MONTH_KEY = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class MonthlyStateError(ValueError):
    """Raised when monthly_history_state.json exists but cannot be read as
    valid Monthly state. Never raised for a simply-missing file."""


def month_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def parse_month_key(value: str) -> tuple[int, int]:
    if not _MONTH_KEY.match(value):
        raise ValueError(f"not a YYYY-MM month key: {value!r}")
    year, month = value.split("-")
    return int(year), int(month)


@dataclass
class MonthlyState:
    last_successful_monthly_close: str | None = None
    dirty_months: list[str] = field(default_factory=list)

    def mark_dirty(self, key: str) -> bool:
        """Record that `key`'s Daily History changed. Returns True if this
        is new information (so a caller can avoid a pointless save)."""
        if key in self.dirty_months:
            return False
        self.dirty_months.append(key)
        self.dirty_months.sort()
        return True

    def clear_dirty(self, key: str) -> bool:
        if key not in self.dirty_months:
            return False
        self.dirty_months.remove(key)
        return True


def load_state(state_path: Path) -> MonthlyState:
    state_path = Path(state_path)
    if not state_path.exists():
        return MonthlyState()

    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MonthlyStateError(
            f"monthly state file is corrupted: {state_path} ({exc})"
        ) from exc

    if not isinstance(data, dict):
        raise MonthlyStateError(
            f"monthly state file must contain a JSON object: {state_path}"
        )

    last = data.get("last_successful_monthly_close")
    if last is not None:
        if not isinstance(last, str) or not _MONTH_KEY.match(last):
            raise MonthlyStateError(
                f"monthly state file has an invalid last_successful_monthly_close: "
                f"{state_path}"
            )

    dirty = data.get("dirty_months", [])
    if not isinstance(dirty, list) or not all(
        isinstance(item, str) and _MONTH_KEY.match(item) for item in dirty
    ):
        raise MonthlyStateError(
            f"monthly state file has an invalid dirty_months field: {state_path}"
        )

    return MonthlyState(
        last_successful_monthly_close=last,
        dirty_months=sorted(set(dirty)),
    )


def save_state(state_path: Path, state: MonthlyState) -> None:
    state_path = Path(state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_successful_monthly_close": state.last_successful_monthly_close,
        "dirty_months": sorted(set(state.dirty_months)),
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
