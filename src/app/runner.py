"""Company Ops Runner (docs/07_SCHEDULER_CATCHUP_SPEC.md §37 "V1 기본 Runner 순서"
+ docs/04_NOTION_SYNC_SPEC.md §3/§6 Notion Sync 연동, Notion Runtime Integration
Phase 2에서 추가).

이 모듈은 새로운 알고리즘/데이터 모델/클래스를 추가하지 않는다. 이미 존재하는
transport / collector / notion / history / scheduler / backup 모듈의 함수를
문서가 정한 순서 그대로 호출하여 조립하는 것이 이 파일의 유일한 책임이다.
(예외: notion.retry_queue는 CEO Policy Decision — Notion Retry Architecture
Plan A — 에 따라 이번 Sprint에 신설된 유일한 신규 모듈이다. §55/§28/§32-33이
요구하던 "다음 실행 시 재처리 가능" 계약을 실제로 충족시키기 위한 것으로,
새로운 정책을 만드는 것이 아니라 이미 확정된 정책을 구현한다.)

실행 순서 (사용자 확정, docs/07 §37을 9단계로 요약한 것에, docs/04 §3 기준
Notion Sync 단계를 Collector 직후에 추가한 것 — Notion Sync 자체는 docs/07이
아니라 docs/04가 정의하는 별개 단계다):

    1. Runner Lock Acquire
    2. Transport
    3. Collector
    4. Notion Sync   (Retry Queue 우선 처리 -> 이번 실행 신규 ACCEPTED Event 순.
                       docs/04 §3/§6/§55, Notion Retry Architecture Plan A)
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

# collector/runtime.py의 PROJECT_ROOT/DEFAULT_*_PATH 관례를 그대로 따른다
# (src/<module>/<file>.py 기준 parents[2] = Repository Root).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_NOTION_SYNC_LOG_PATH = PROJECT_ROOT / "runtime" / "logs" / "notion_sync.log"

from backup.runner import run_once as backup_run_once  # noqa: E402  (backup/__init__.py 없음 — 서브모듈 직접 import)
from collector import (  # noqa: E402
    Collector,
    PersistentSeenEventStore,
    RuntimeOutcome,
    run_once as collector_run_once,
)
from events import Event  # noqa: E402
from history import FileHistoryRepository, HistoryFilter  # noqa: E402
from notion import (  # noqa: E402
    DEFAULT_QUEUE_PATH as DEFAULT_NOTION_RETRY_QUEUE_PATH,
    DashboardOutcome,
    ExecutionPlanSync,
    NotionClient,
    SyncResult,
    SyncStatus,
    build_index as build_retry_queue_index,
    load_queue as load_retry_queue,
    record_run as dashboard_record_run,
    remove_entry as retry_queue_remove,
    save_queue as save_retry_queue,
    upsert_entry as retry_queue_upsert,
)
from notion.dashboard_pending import (  # noqa: E402
    DEFAULT_DASHBOARD_PENDING_PATH,
    drain_pending,
    save_pending,
)
from scheduler import run_once as scheduler_run_once  # noqa: E402
from scheduler.lock import release_lock, try_acquire_lock  # noqa: E402  (scheduler/__init__.py가 재노출하지 않음)
from transport import run_intake  # noqa: E402


def _log_notion_sync(log_path: Path, sync_result: SyncResult) -> None:
    """docs/04_NOTION_SYNC_SPEC.md §55: event_id / project_id / sync
    timestamp / result을 최소 기록한다. §56에 따라 NOTION_API_TOKEN 등
    민감정보는 절대 기록하지 않는다 — SyncResult에 애초에 그런 값이 담기지
    않으므로(§56) 여기서 별도로 걸러낼 것이 없다. 형식은
    collector/runtime.py `_log()`와 동일한 관례(타임스탬프 접두 1줄)를 따른다.
    """
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        line = (
            f"{timestamp} EVENT {sync_result.event_id} "
            f"PROJECT {sync_result.project_id} "
            f"NOTION_RESULT {sync_result.status.value}\n"
        )
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass


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
    notion_sync_log_path: Path | None = None,
    notion_retry_queue_path: Path | None = None,
    dashboard_client: NotionClient | None = None,
    dashboard_pending_path: Path | None = None,
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

        # 4. Notion Sync — docs/04_NOTION_SYNC_SPEC.md §3, §6, §29-37, §55
        #    (Notion Runtime Integration Phase 2에서 연결; Retry Queue는
        #    CEO Policy Decision "Notion Retry Architecture Plan A"로 이번
        #    Sprint에 추가). Collector가 ACCEPTED으로 분류한 Event만 대상이다 —
        #    DUPLICATE/REJECTED/FAILED는 애초에 이 목록에 없으므로 Sync하지
        #    않는다. History Filter의 KEEP/REVIEW/DROP 판단과는 독립적으로
        #    동작한다(§3 "History는 별도 흐름이다"). notion_sync가 주어지지
        #    않으면(Notion 미설정) 이 단계는 Retry Queue 처리까지 포함해 전부
        #    건너뛴다 — Queue에 남은 Event는 다음 Notion 설정된 실행까지 그대로
        #    보존된다(삭제하지 않음).
        #    Notion Sync 실패는 Runtime을 중단하지 않는다 — ExecutionPlanSync.sync()는
        #    NotionAPIError를 내부에서 흡수해 NOTION_RETRY_REQUIRED만 반환하지만,
        #    예상 밖의 예외까지 이 단계 밖으로 새어나가 나머지 단계(History/Daily/
        #    Backup)를 막지 않도록 여기서도 방어적으로 잡아 NOTION_FAILED로 기록한다
        #    (collector_runtime.run_once가 이미 쓰는 것과 동일한 방식, docs/03 §53).
        notion_sync_results: list[SyncResult] = []
        if notion_sync is not None:
            resolved_notion_sync_log_path = (
                Path(notion_sync_log_path)
                if notion_sync_log_path is not None
                else DEFAULT_NOTION_SYNC_LOG_PATH
            )
            resolved_retry_queue_path = (
                Path(notion_retry_queue_path)
                if notion_retry_queue_path is not None
                else DEFAULT_NOTION_RETRY_QUEUE_PATH
            )

            # Retry Queue Batch Save (CEO 승인 B안): load the queue exactly
            # once, apply every change in memory, and write it back once at
            # the end of this step. Previously each enqueue()/dequeue() call
            # re-read and rewrote the entire file, so a Notion outage over n
            # Events cost O(n^2) bytes (measured: 7.9 ms/enqueue at 50 entries
            # -> 19.3 ms at 800). Semantics are unchanged — same upsert, same
            # event_id dedup, same "queue first" ordering.
            #
            # Crash safety: if this run dies before the single save, the queue
            # keeps its previous contents. That is safe because a successfully
            # synced Event simply stays queued and is retried next run, where
            # docs/04 §62's duplicate guard recognises it (Last Event ID
            # match) and returns NOTION_SKIPPED_OLD_EVENT before it is
            # dequeued. No Event is lost, and none is applied twice.
            queue_entries = load_retry_queue(resolved_retry_queue_path)
            queue_dirty = False
            # in-memory lookup index for this run's upserts/removes below —
            # a Notion outage held open across n queued Events previously cost
            # O(n^2) list scans draining the queue (measured: 0.45 ms at 100
            # entries -> 3,637 ms at 10,000). Additive only: entries' on-disk
            # shape, upsert semantics, and every other caller of upsert_entry/
            # remove_entry (enqueue(), dequeue(), tests) are unchanged.
            queue_index = build_retry_queue_index(queue_entries)

            def _sync_and_record(event: Event) -> SyncResult:
                nonlocal queue_dirty
                try:
                    sync_result = notion_sync.sync(event)
                except Exception as exc:  # noqa: BLE001  (구현 범위 3: Notion 실패가 Runtime을 막지 않는다)
                    sync_result = SyncResult(
                        status=SyncStatus.NOTION_FAILED,
                        event_id=event.event_id,
                        project_id=event.project_id,
                        error=str(exc),
                    )
                _log_notion_sync(resolved_notion_sync_log_path, sync_result)
                # CEO Policy Decision (Notion Retry Architecture Plan A):
                # a still-failing event stays queued (upsert, never
                # duplicated — dedup is by event_id); any non-error result
                # clears it from the queue, whether it arrived there via the
                # queue itself or fresh this run.
                if sync_result.status in (SyncStatus.NOTION_RETRY_REQUIRED, SyncStatus.NOTION_FAILED):
                    retry_queue_upsert(queue_entries, event, now=now, index=queue_index)
                    queue_dirty = True
                elif retry_queue_remove(queue_entries, event.event_id, index=queue_index):
                    queue_dirty = True
                return sync_result

            # 4c는 `finally`다. Batch Save(B안)는 쓰기 횟수만 줄이려는 변경이고
            # 내구성을 낮추려는 변경이 아니다 — 단계별 저장이던 시절에는 4b 도중
            # 예외가 나도 그때까지의 큐 변경은 이미 파일에 있었다. 한 번만 저장하게
            # 바꾸면서 그 보장이 사라졌고, 측정 결과 Notion 장애 6건 중 4번째에서
            # 예외가 나면 6건 전부가 큐에서 사라졌다. 그 Event들은 이미 수집 완료로
            # 표시돼 다시 수집되지 않으므로 Notion에 영원히 반영되지 않는다.
            # 따라서 이 블록을 어떻게 빠져나가든 델타는 반드시 기록한다.
            try:
                # 4a. Retry Queue를 가장 먼저 처리한다 (CEO Policy Decision).
                #     Iterate a snapshot: _sync_and_record() mutates queue_entries.
                for queued_entry in list(queue_entries):
                    notion_sync_results.append(_sync_and_record(queued_entry.to_event()))

                # 4b. 이번 실행에서 새로 수집된 ACCEPTED Event.
                for processed_file in collector_summary.files:
                    if processed_file.outcome is not RuntimeOutcome.ACCEPTED:
                        continue
                    event = Event.from_json(
                        processed_file.destination_path.read_text(encoding="utf-8")
                    )
                    notion_sync_results.append(_sync_and_record(event))
            finally:
                # 4c. 이 단계에서 발생한 모든 큐 변경을 1회만 기록한다 (B안).
                if queue_dirty:
                    try:
                        save_retry_queue(resolved_retry_queue_path, queue_entries)
                    except Exception as save_exc:  # noqa: BLE001
                        # 저장 자체가 실패하면 원래 예외를 가리지 않는다. 정상
                        # 경로에서는(원래 예외가 없으면) 저장 실패 자체를 알린다.
                        #
                        # `sys.exc_info()`는 이 except 블록 안에서 항상 지금
                        # 잡은 예외(save_exc) 자신을 가리켜 이 조건이 절대
                        # 참이 될 수 없었다(발견: 이 저장 실패가 try 블록
                        # 성공/실패 여부와 무관하게 항상 조용히 삼켜짐 — Retry
                        # Queue에 새로 추가된 재시도 대상이 디스크에 반영되지
                        # 않고 유실됨). `save_exc.__context__`는 이 예외가
                        # *발생한 시점*에 이미 전파 중이던 예외를 가리키므로
                        # (원래 예외 없음 -> None) 의도한 판단을 실제로 한다.
                        if save_exc.__context__ is None:
                            raise

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
        #    (이전 Gap 7) 새로 만들지 않는다. Notion Sync는 4단계에서 매 호출마다
        #    `_log_notion_sync()`가 event_id/project_id/timestamp/result를
        #    notion_sync.log에 이미 기록했다(§55, P1 Backlog 해소). 나머지 단계는
        #    각자의 기존 반환값을 그대로 호출자에게 돌려준다.

        # 9b. Operations Dashboard — CEO Decision ④. Runner 종료 직전 1회만
        #     기록한다(Event당 기록 금지 — docs/04 §53 "Notion 데이터 과잉 방지").
        #     이 단계는 이미 확정된 위 단계들의 결과만 읽는다 — History/Backup/
        #     Event를 건드리지 않는다.
        #
        #     실패해도 Runtime을 절대 중단시키지 않는다(CEO ④): record_run()은
        #     예외를 던지지 않고 결과만 돌려주며, 실패한 기록은 pending 파일에
        #     저장되어 다음 실행에서 재시도된다(Retry Queue와 동일한 방식 —
        #     자세한 이유는 notion/dashboard_pending.py docstring 참고).
        #     dashboard_client가 없으면(미설정) 이 단계 전체를 건너뛴다.
        #
        #     drain_pending()/dashboard_record_run()은 스스로 예외를 던지지
        #     않도록 구현돼 있지만(각자의 docstring 참고), 이 호출부는 그 내부
        #     구현을 신뢰하기만 할 뿐 구조적으로 강제하지 않았다 — 4단계의
        #     notion_sync.sync() 호출부가 `except Exception`으로 한 번 더
        #     감싸는 것과 다른 처리였다. 이미 History/Backup까지 전부 성공한
        #     실행 결과가 Dashboard 기록 단계의 예기치 못한 회귀 하나로 유실되지
        #     않도록, 같은 파일의 기존 방어 패턴과 동일하게 여기서도 감싼다.
        if dashboard_client is not None:
            try:
                resolved_dashboard_pending_path = (
                    Path(dashboard_pending_path)
                    if dashboard_pending_path is not None
                    else DEFAULT_DASHBOARD_PENDING_PATH
                )
                resolved_run_id = run_id or now.isoformat(timespec="seconds")

                # 밀린 기록 먼저 재시도한다(Retry Queue와 동일한 "먼저 처리" 원칙).
                drain_pending(resolved_dashboard_pending_path, dashboard_client)

                dashboard_result = dashboard_record_run(
                    dashboard_client,
                    run_id=resolved_run_id,
                    run_at=now,
                    intake_summary=intake_summary,
                    collector_summary=collector_summary,
                    scheduler_result=scheduler_result,
                    backup_entry=backup_entry,
                    notion_sync_results=notion_sync_results,
                )
                if (
                    dashboard_result.outcome is DashboardOutcome.FAILED
                    and dashboard_result.properties is not None
                ):
                    save_pending(
                        resolved_dashboard_pending_path,
                        run_id=resolved_run_id,
                        properties=dashboard_result.properties,
                        now=now,
                    )
            except Exception:  # noqa: BLE001  (CEO ④: Dashboard 실패가 Runtime을 막지 않는다)
                pass

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
