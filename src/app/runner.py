"""Company Ops Runner (docs/07_SCHEDULER_CATCHUP_SPEC.md §37 "V1 기본 Runner 순서"
+ docs/04_NOTION_SYNC_SPEC.md §3/§6 Notion Sync 연동, Notion Runtime Integration
Phase 2에서 추가).

이 모듈은 새로운 알고리즘/데이터 모델/클래스를 추가하지 않는다. 이미 존재하는
transport / collector / notion / history / scheduler / backup 모듈의 함수를
문서가 정한 순서 그대로 호출하여 조립하는 것이 이 파일의 유일한 책임이다.

실행 순서 (사용자 확정, docs/07 §37을 9단계로 요약한 것에, docs/04 §3 기준
Notion Sync 단계를 Collector 직후에 추가한 것 — Notion Sync 자체는 docs/07이
아니라 docs/04가 정의하는 별개 단계다):

    1. Runner Lock Acquire
    2. Transport
    3. Collector
    4. Notion Sync   (신규 — docs/04 §3/§6, Collector ACCEPTED Event만 대상)
    5. History Filter
    6. Daily History
    7. Backup
    8. State
    9. Log
    10. Runner Lock Release

Notion Sync와 History Filter는 docs/04 §3("History는 별도 흐름이다")에 따라
서로 독립적인 병렬 분기이며 서로의 결과에 의존하지 않는다 — 순서를 바꿔도
동작은 동일하다. Collector 바로 다음에 둔 것은 ACCEPTED 판정 직후에 처리한다는
것을 코드 위치로도 드러내기 위함이다.

Runner API Contract(직전 Sprint 설계 확정)의 함수 시그니처를 그대로 사용한다.
경로 값을 어디서 얻어올지(Gap 6)는 이 파일이 결정하지 않는다 — 모든 경로는
호출자가 인자로 전달한다. Notion 연동 여부도 동일한 원칙을 따른다: `notion_sync`
인자를 주지 않으면(Notion 미설정) 이 단계는 건너뛴다 — Runner는 Notion 설정
여부를 스스로 판단하지 않는다.
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
from notion import ExecutionPlanSync, SyncResult, SyncStatus  # noqa: E402
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
    notion_sync: ExecutionPlanSync | None = None,
    keep_dir: Path | None = None,
    review_dir: Path | None = None,
    scheduler_state_path: Path | None = None,
    backup_state_path: Path | None = None,
    run_id: str | None = None,
):
    """Runner 1회 실행. 반환값은 각 단계가 이미 반환하는 기존 객체들의 tuple이다
    (IntakeSummary, RuntimeSummary, SchedulerRunResult, BackupLogEntry,
    tuple[SyncResult, ...]) — 새 데이터 모델을 만들지 않기 위해 그대로 묶어서
    돌려준다. Lock을 얻지 못하면 None.

    `notion_sync`를 주지 않으면(Notion 미설정) Notion Sync 단계는 건너뛰고
    다섯 번째 값은 빈 tuple이 된다 — 나머지 단계는 영향받지 않는다.
    """
    now = now or datetime.now().astimezone()

    # 1. Runner Lock Acquire — docs/07 §37 step 1, §24-27, §25 "Lock으로
    #    하나의 Runner만 실행되게 한다". Runtime Stabilization Sprint(Critical
    #    Fix): 이 Lock이 곧 시스템 전체의 유일한 Lock이다 — Scheduler는 이 함수
    #    안에서 호출되는 동안(6번 단계) 별도 Lock을 다시 잡지 않는다
    #    (already_locked=True). 예전에는 Scheduler가 자기 몫의 Lock을 다시
    #    시도했고, 그 기본 경로가 이 runner_lock_path와 우연히 같은 값으로
    #    호출되면 자기 자신과 충돌해 SKIPPED_ALREADY_RUNNING을 반환하는
    #    버그가 있었다(운영 중 실제 재현됨) — 이제 그 경로 자체가 코드에서
    #    사라졌다.
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

        # 4. Notion Sync — docs/04_NOTION_SYNC_SPEC.md §3, §6, §29-37 (신규,
        #    Notion Runtime Integration Phase 2에서 연결). Collector가 ACCEPTED으로
        #    분류한 Event만 대상이다 — DUPLICATE/REJECTED/FAILED는 애초에 이
        #    목록에 없으므로 Sync하지 않는다. History Filter의 KEEP/REVIEW/DROP
        #    판단과는 독립적으로 동작한다(§3 "History는 별도 흐름이다").
        #    notion_sync가 주어지지 않으면(Notion 미설정) 이 단계는 건너뛴다.
        #    Notion Sync 실패는 Runtime을 중단하지 않는다 — ExecutionPlanSync.sync()는
        #    NotionAPIError를 내부에서 흡수해 NOTION_RETRY_REQUIRED만 반환하지만,
        #    예상 밖의 예외까지 이 단계 밖으로 새어나가 나머지 단계(History/Daily/
        #    Backup)를 막지 않도록 여기서도 방어적으로 잡아 NOTION_FAILED로 기록한다
        #    (collector_runtime.run_once가 이미 쓰는 것과 동일한 방식, docs/03 §53).
        notion_sync_results: list[SyncResult] = []
        if notion_sync is not None:
            for processed_file in collector_summary.files:
                if processed_file.outcome is not RuntimeOutcome.ACCEPTED:
                    continue
                event = Event.from_json(processed_file.destination_path.read_text(encoding="utf-8"))
                try:
                    sync_result = notion_sync.sync(event)
                except Exception as exc:  # noqa: BLE001  (구현 범위 3: Notion 실패가 Runtime을 막지 않는다)
                    sync_result = SyncResult(
                        status=SyncStatus.NOTION_FAILED,
                        event_id=event.event_id,
                        project_id=event.project_id,
                        error=str(exc),
                    )
                notion_sync_results.append(sync_result)

        # 5. History Filter — docs/07 §37 step 6 "History Pipeline",
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

        # 6. Daily History — docs/07 §37 steps 7-8
        #    "Missing Daily Date 계산" + "Daily Catch-up"
        #    (scheduler.run_once가 내부에서 daily.generate_daily_history를 반복 호출)
        #    already_locked=True: 1번에서 이미 시스템 전체 Lock을 쥐고 있으므로
        #    Scheduler 자신의 Lock을 별도로 잡지 않는다(위 1번 주석 참고).
        scheduler_result = scheduler_run_once(
            repository,
            history_start_date=history_start_date,
            now=now,
            state_path=scheduler_state_path,
            daily_output_dir=local_master_dir / "daily",
            already_locked=True,
        )

        # 7. Backup — docs/07 §37 step 9 "Backup Pending 처리", docs/08 §14-15
        backup_entry = backup_run_once(
            local_master_dir,
            backup_working_copy_dir,
            state_path=backup_state_path,
            now=now,
            run_id=run_id,
        )

        # 8. State — docs/07 §37 step 10 "State Save 확인"
        #    Collector(collector_state.json) / Scheduler(daily_history_state.json) /
        #    Backup(backup_state.json)가 각 단계 호출 시점에 이미 자체 저장을
        #    완료했다. Runner가 별도로 다시 저장할 state는 없다. Notion Sync는
        #    자체 상태 파일이 없다(§6이 매 호출마다 project_id로 Notion을 직접
        #    조회하므로 로컬에 별도로 캐싱할 state가 없다).

        # 9. Log — docs/07 §37 step 11 "Log 기록"
        #    Collector는 3단계 호출 과정에서 이미 자신의 로그 파일(collector.log)을
        #    기록했다. Scheduler/Backup에는 로그 파일을 쓰는 기존 함수가 없으므로
        #    (이전 Gap 7) 새로 만들지 않는다. Notion Sync도 별도 로그 파일을 쓰는
        #    기존 함수가 없어 만들지 않는다(§55 Sync Logging은 이번 Sprint 범위
        #    밖 — 남은 Backlog). 대신 각 단계의 기존 반환값을 그대로 호출자에게
        #    돌려준다.
        return (
            intake_summary,
            collector_summary,
            scheduler_result,
            backup_entry,
            tuple(notion_sync_results),
        )

    finally:
        # 10. Runner Lock Release — docs/07 §37 step 12
        release_lock(runner_lock_path)
