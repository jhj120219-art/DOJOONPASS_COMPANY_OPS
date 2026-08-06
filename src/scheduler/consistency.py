"""State Consistency Check (docs/10_E2E_OPERATIONS_SPEC.md §46-49).

§48: "Runner 시작 시 최소 확인 가능: State Last Success -> Corresponding
Local History 존재?" — and §47 names the failure it detects:

    State: last_successful_daily_close = 2026-08-10
    but    2026-08-10.md 없음
    ->     STATE_INCONSISTENCY

Detection only, deliberately. This module reports; it never repairs,
regenerates, deletes, or overrides anything, and nothing here changes what
Scheduler decides to do. That restraint is the spec's own (§49: "History가
State보다 우선", §46: "프로그램이 임의로 모든 History를 삭제하거나 다시
생성하면 안 된다") — what to *do* about an inconsistency is an operator
decision (§64 lists State Inconsistency under "COO가 개입해야 하는 상황"),
so wiring this into Scheduler's control flow would be a policy change, not
a check.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .state import load_state


class ConsistencyStatus(enum.Enum):
    CONSISTENT = "CONSISTENT"
    # State claims a Daily Close that has no Local History file (§47).
    STATE_INCONSISTENCY = "STATE_INCONSISTENCY"
    # No state file yet — a first-ever run, not an inconsistency.
    NO_STATE = "NO_STATE"
    # The state file exists but cannot be read (§46 damaged state).
    STATE_UNREADABLE = "STATE_UNREADABLE"


@dataclass(frozen=True)
class ConsistencyResult:
    status: ConsistencyStatus
    last_successful_daily_close: date | None = None
    expected_history_path: Path | None = None
    detail: str | None = None

    @property
    def is_consistent(self) -> bool:
        return self.status is ConsistencyStatus.CONSISTENT


def check_state_consistency(state_path: Path, daily_dir: Path) -> ConsistencyResult:
    """Compare Scheduler state against the Local History it claims exists.

    Never raises — a damaged state file is itself one of the outcomes this
    is meant to report (§46), not an error to propagate.
    """
    if not Path(state_path).exists():
        return ConsistencyResult(
            status=ConsistencyStatus.NO_STATE,
            detail=f"no state file yet: {state_path}",
        )

    try:
        state = load_state(Path(state_path))
    except Exception as exc:  # noqa: BLE001  (§46: 손상된 State도 보고 대상이다)
        return ConsistencyResult(
            status=ConsistencyStatus.STATE_UNREADABLE,
            detail=f"state file could not be read: {exc}",
        )

    last_close = state.last_successful_daily_close
    if last_close is None:
        return ConsistencyResult(
            status=ConsistencyStatus.NO_STATE,
            detail="state file has no last_successful_daily_close yet",
        )

    expected = Path(daily_dir) / f"{last_close.isoformat()}.md"
    if expected.exists():
        return ConsistencyResult(
            status=ConsistencyStatus.CONSISTENT,
            last_successful_daily_close=last_close,
            expected_history_path=expected,
        )

    return ConsistencyResult(
        status=ConsistencyStatus.STATE_INCONSISTENCY,
        last_successful_daily_close=last_close,
        expected_history_path=expected,
        detail=(
            f"state claims Daily Close {last_close.isoformat()} but its Local History "
            f"file is missing: {expected}. History is the official record (§49) — do "
            "not trust the state file over it; operator review required (§64)."
        ),
    )
