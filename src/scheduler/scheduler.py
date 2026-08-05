"""Scheduler / Catch-up Core — run_once() only (docs/07_SCHEDULER_CATCHUP_SPEC.md).

run_once() does exactly one thing, synchronously, and returns: given "now",
find every calendar date from the day after the last successful Daily
Close (or, on a first-ever run, `history_start_date` — never guessed,
section 50) through yesterday, and run the Daily History Generator
(Phase 4.4) for each, oldest first, saving state after every individual
success (section 29). It never processes today (section 18) and never
waits for 11:00 to catch up already-finished days (section 21: "정기
Scheduler 시각인 오전 11시까지 기다릴 필요는 없다" — the eligible range is
calendar-date math, not a clock-hour gate).

Nothing here loops, sleeps, spawns a thread, or registers with Windows
Task Scheduler — the caller decides when to call this, and how often.
Collector, Notion, Backup, and Transport are not called from here; this
Phase only orchestrates Daily History generation.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from daily import DEFAULT_DAILY_DIR, generate_daily_history
from history import HistoryRepository

from .lock import DEFAULT_LOCK_PATH, release_lock, try_acquire_lock
from .result import SchedulerRunResult, SchedulerStatus
from .state import DEFAULT_STATE_PATH, load_state, save_state


def _pending_dates(start: date, end: date) -> list[date]:
    if start > end:
        return []
    dates = []
    current = start
    while current <= end:
        dates.append(current)
        current += timedelta(days=1)
    return dates


def run_once(
    repository: HistoryRepository,
    *,
    history_start_date: date,
    now: datetime | None = None,
    state_path: Path | None = None,
    lock_path: Path | None = None,
    daily_output_dir: Path | None = None,
    already_locked: bool = False,
) -> SchedulerRunResult:
    """docs/07 §25: "Lock으로 하나의 Runner만 실행되게 한다" — a single,
    system-wide invariant, not "one Scheduler lock + one Runner lock".

    Pass `already_locked=True` when the caller (currently only
    `app/runner.py::run_once()`) already holds that system-wide lock for
    the entire pipeline this Scheduler step is one part of — Scheduler
    then does not acquire (or release) any lock of its own here, and
    `lock_path` is ignored, because a second acquisition of any lock
    file — even a different one — inside an already-locked critical
    section adds no real protection and only shifts the failure mode
    (a same-path collision self-deadlocks into SKIPPED_ALREADY_RUNNING;
    Runtime Stabilization Sprint found and fixed exactly this).

    Standalone/manual invocation (docs/07 §951 "수동 실행도 동일한 Lock과
    State 규칙을 따른다") must leave this False (the default) — Scheduler
    then still protects itself with its own lock exactly as before.
    """
    now = now or datetime.now().astimezone()
    state_path = Path(state_path) if state_path is not None else DEFAULT_STATE_PATH
    daily_dir = Path(daily_output_dir) if daily_output_dir is not None else DEFAULT_DAILY_DIR

    if already_locked:
        return _generate_pending_dates(
            repository,
            history_start_date=history_start_date,
            now=now,
            state_path=state_path,
            daily_dir=daily_dir,
        )

    resolved_lock_path = Path(lock_path) if lock_path is not None else DEFAULT_LOCK_PATH
    if not try_acquire_lock(resolved_lock_path, now=now):
        return SchedulerRunResult(status=SchedulerStatus.SKIPPED_ALREADY_RUNNING, generated_dates=())

    try:
        return _generate_pending_dates(
            repository,
            history_start_date=history_start_date,
            now=now,
            state_path=state_path,
            daily_dir=daily_dir,
        )
    finally:
        release_lock(resolved_lock_path)


def _generate_pending_dates(
    repository: HistoryRepository,
    *,
    history_start_date: date,
    now: datetime,
    state_path: Path,
    daily_dir: Path,
) -> SchedulerRunResult:
    """The actual Daily Catch-up work, run_once()'s only job once whichever
    locking branch above has already decided it's safe to proceed."""
    state = load_state(state_path)
    start = (
        state.last_successful_daily_close + timedelta(days=1)
        if state.last_successful_daily_close is not None
        else history_start_date
    )
    end = now.date() - timedelta(days=1)  # today is never processed

    generated: list[date] = []
    for target_date in _pending_dates(start, end):
        final_path = daily_dir / f"{target_date.isoformat()}.md"
        if not final_path.exists():
            try:
                generate_daily_history(repository, target_date, output_dir=daily_dir)
            except Exception as exc:  # noqa: BLE001
                # Dates close in order; stop here rather than skip ahead
                # and leave a gap in the sequence (section 30).
                return SchedulerRunResult(
                    status=SchedulerStatus.FAILED,
                    generated_dates=tuple(generated),
                    failed_date=target_date,
                    error=str(exc),
                )
        # Either just generated, or the file already existed from a
        # prior run that crashed after writing it but before saving
        # state (section 28) — either way this date is now done.
        state.last_successful_daily_close = target_date
        save_state(state_path, state)
        generated.append(target_date)

    return SchedulerRunResult(status=SchedulerStatus.COMPLETED, generated_dates=tuple(generated))
