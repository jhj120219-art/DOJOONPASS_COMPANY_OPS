"""Company Ops Runner (docs/07_SCHEDULER_CATCHUP_SPEC.md §37 "V1 기본 Runner 순서").

이 모듈은 새로운 알고리즘/데이터 모델/클래스를 추가하지 않는다. 이미 존재하는
transport / collector / history / scheduler / backup 모듈의 함수를 문서가 정한
순서 그대로 호출하여 조립하는 것이 이 파일의 유일한 책임이다.

실행 순서 (사용자 확정, docs/07 §37을 9단계로 요약한 것):

    1. Runner Lock Acquire
    2. Transport
    3. Collector
    4. History Filter
    5. Daily History
    6. Backup
    7. State
    8. Log
    9. Runner Lock Release

Runner API Contract(직전 Sprint 설계 확정)의 함수 시그니처를 그대로 사용한다.
경로 값을 어디서 얻어올지(Gap 6)는 이 파일이 결정하지 않는다 — 모든 경로는
호출자가 인자로 전달한다.
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

# src/app/runner.py 기준으로 src/를 sys.path에 추가한다.
# review_cli.py의 "Run directly from inside src/" 관례와 동일한 idiom을,
# app/ 한 단계 더 깊이 있는 위치에 맞춰 그대로 적용한 것뿐이다(parents[1] = src/).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backup.runner import run_once as backup_run_once  # noqa: E402  (backup/__init__.py 없음 — 서브모듈 직접 import)
from collector import (  # noqa: E402
    Collector,
    PersistentSeenEventStore,
    RuntimeOutcome,
    run_once as collector_run_once,
)
from events import Event  # noqa: E402
from history import FileHistoryRepository, HistoryFilter  # noqa: E402
from scheduler import run_once as scheduler_run_once  # noqa: E402
from scheduler.lock import release_lock, try_acquire_lock  # noqa: E402  (scheduler/__init__.py가 재노출하지 않음)
from transport import run_intake  # noqa: E402


def run_once(
    *,
    local_master_dir: Path,
    backup_working_copy_dir: Path,
    history_start_date: date,
    runner_lock_path: Path,
    now: datetime | None = None,
    transport_dir: Path | None = None,
    incoming_dir: Path | None = None,
    processed_dir: Path | None = None,
    rejected_dir: Path | None = None,
    collector_log_path: Path | None = None,
    collector_state_path: Path | None = None,
    keep_dir: Path | None = None,
    review_dir: Path | None = None,
    scheduler_state_path: Path | None = None,
    scheduler_lock_path: Path | None = None,
    backup_state_path: Path | None = None,
    run_id: str | None = None,
):
    """Runner 1회 실행. 반환값은 각 단계가 이미 반환하는 기존 객체들의 tuple이다
    (IntakeSummary, RuntimeSummary, SchedulerRunResult, BackupLogEntry) — 새 데이터
    모델을 만들지 않기 위해 그대로 묶어서 돌려준다. Lock을 얻지 못하면 None.
    """
    now = now or datetime.now().astimezone()

    # 1. Runner Lock Acquire — docs/07 §37 step 1, §24-27
    #    "Lock으로 하나의 Runner만 실행되게 한다"(§25)의 대상은 이 Runner 전체다.
    if not try_acquire_lock(runner_lock_path, now=now):
        return None

    try:
        # 2. Transport — docs/07 §9(Collector 실행 이전 수신측 promotion),
        #    docs/12 §5.2 Transport 책임, §7 Runtime Sequence
        intake_summary = run_intake(
            transport_dir=transport_dir,
            incoming_dir=incoming_dir,
            processed_dir=processed_dir,
            rejected_dir=rejected_dir,
        )

        # 3. Collector — docs/07 §9 "Collector 실행" + "Pending Event 처리"
        #    (incoming/ 전량 소진이 곧 Pending 처리, docs/03 §7 기본 처리 Pipeline)
        seen_store = PersistentSeenEventStore(state_path=collector_state_path)
        collector_instance = Collector(seen_store=seen_store)
        collector_summary = collector_run_once(
            collector=collector_instance,
            incoming_dir=incoming_dir,
            processed_dir=processed_dir,
            rejected_dir=rejected_dir,
            log_path=collector_log_path,
        )
        seen_store.record_run(now.isoformat(timespec="seconds"))

        # 4. History Filter — docs/07 §37 step 6 "History Pipeline",
        #    docs/05 §2 Event -> History Filter -> Candidate
        #    (ACCEPTED만 대상. DUPLICATE/REJECTED/FAILED는 History 대상이 아니다.
        #    processed_dir 재스캔이 아니라 이번 실행의 반환값만 사용한다 — Gap 3.)
        history_filter = HistoryFilter()
        repository = FileHistoryRepository(keep_dir=keep_dir, review_dir=review_dir)
        for processed_file in collector_summary.files:
            if processed_file.outcome is not RuntimeOutcome.ACCEPTED:
                continue
            event = Event.from_json(processed_file.destination_path.read_text(encoding="utf-8"))
            filter_result = history_filter.evaluate(event)
            repository.save(filter_result.candidate)

        # 5. Daily History — docs/07 §37 steps 7-8
        #    "Missing Daily Date 계산" + "Daily Catch-up"
        #    (scheduler.run_once가 내부에서 daily.generate_daily_history를 반복 호출)
        scheduler_result = scheduler_run_once(
            repository,
            history_start_date=history_start_date,
            now=now,
            state_path=scheduler_state_path,
            lock_path=scheduler_lock_path,
            daily_output_dir=local_master_dir / "daily",
        )

        # 6. Backup — docs/07 §37 step 9 "Backup Pending 처리", docs/08 §14-15
        backup_entry = backup_run_once(
            local_master_dir,
            backup_working_copy_dir,
            state_path=backup_state_path,
            now=now,
            run_id=run_id,
        )

        # 7. State — docs/07 §37 step 10 "State Save 확인"
        #    Collector(collector_state.json) / Scheduler(daily_history_state.json) /
        #    Backup(backup_state.json)가 각 단계 호출 시점에 이미 자체 저장을
        #    완료했다. Runner가 별도로 다시 저장할 state는 없다.

        # 8. Log — docs/07 §37 step 11 "Log 기록"
        #    Collector는 3단계 호출 과정에서 이미 자신의 로그 파일(collector.log)을
        #    기록했다. Scheduler/Backup에는 로그 파일을 쓰는 기존 함수가 없으므로
        #    (이전 Gap 7) 새로 만들지 않는다. 대신 각 단계의 기존 반환값을 그대로
        #    호출자에게 돌려준다.
        return intake_summary, collector_summary, scheduler_result, backup_entry

    finally:
        # 9. Runner Lock Release — docs/07 §37 step 12
        release_lock(runner_lock_path)
