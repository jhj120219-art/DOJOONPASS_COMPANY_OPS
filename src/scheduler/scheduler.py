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
) -> SchedulerRunResult:
    now = now or datetime.now().astimezone()
    state_path = Path(state_path) if state_path is not None else DEFAULT_STATE_PATH
    lock_path = Path(lock_path) if lock_path is not None else DEFAULT_LOCK_PATH
    daily_dir = Path(daily_output_dir) if daily_output_dir is not None else DEFAULT_DAILY_DIR

    if not try_acquire_lock(lock_path, now=now):
        return SchedulerRunResult(status=SchedulerStatus.SKIPPED_ALREADY_RUNNING, generated_dates=())

    try:
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
    finally:
        release_lock(lock_path)
