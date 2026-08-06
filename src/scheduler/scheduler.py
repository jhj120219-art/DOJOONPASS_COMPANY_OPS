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

from daily import DEFAULT_DAILY_DIR, build_keep_index, generate_daily_history
from history import HistoryDecision, HistoryRepository

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

    pending_dates = _pending_dates(start, end)

    # Architecture 개선(CEO Decision ②, History Repository Cache A안):
    # 이 배치에서 실제로 History 생성이 필요할 수 있는 경우에만,
    # repository.list()를 배치당 정확히 1회 호출하고, 그 결과로 날짜별
    # History Index를 1회만 만들어 모든 날짜가 재사용한다.
    #
    # 예전에는 (1) generate_daily_history()가 매 날짜마다 repository.list()를
    # 다시 호출했고, (2) 그 뒤에도 매 날짜마다 전체 후보를 선형 스캔하며
    # timestamp를 재파싱했다. Index는 timestamp를 후보당 정확히 1회만
    # 파싱하고, 날짜별 조회를 dict 조회로 만든다.
    #
    # 계약은 전혀 바뀌지 않는다 — repository.list()가 무엇을 반환하는지도,
    # 어떤 후보가 어느 날짜에 들어가는지도(docs/06 §12) 동일하며, 렌더링
    # 직전 timestamp 정렬도 그대로다(= Markdown 결과 100% 동일).
    # generate_daily_history()를 직접 호출하는 다른 caller(테스트 등)는
    # keep_index/keep_candidates를 넘기지 않으면 예전 경로 그대로 동작한다.
    if pending_dates:
        try:
            keep_index = build_keep_index(repository.list(decision=HistoryDecision.KEEP))
        except Exception as exc:  # noqa: BLE001  (repository.list()도 다른 단계와
            # 동일하게 실패를 감춰서는 안 된다 — 예전에는 이 호출이 각 날짜의
            # generate_daily_history() 안에서 일어나 그 날짜의 try/except가
            # 잡았다. 지금은 배치 시작 전 1회이므로 여기서 직접 잡아 동일한
            # FAILED 계약(SchedulerRunResult, 예외를 밖으로 새어나가게 하지
            # 않음)을 유지한다 — 이 실패 시점 자체가 더 일찍이므로
            # generated_dates는 항상 빈 tuple이다(어떤 날짜도 아직 시도되지
            # 않았기 때문).
            return SchedulerRunResult(
                status=SchedulerStatus.FAILED,
                generated_dates=(),
                failed_date=pending_dates[0],
                error=str(exc),
            )
    else:
        keep_index = None

    generated: list[date] = []
    for target_date in pending_dates:
        final_path = daily_dir / f"{target_date.isoformat()}.md"
        if not final_path.exists():
            try:
                generate_daily_history(
                    repository, target_date, output_dir=daily_dir, keep_index=keep_index
                )
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
