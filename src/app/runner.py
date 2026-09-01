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
from typing import Any
from pathlib import Path

# src/app/runner.py 기준으로 src/를 sys.path에 추가한다.
# review_cli.py의 "Run directly from inside src/" 관례와 동일한 idiom을,
# app/ 한 단계 더 깊이 있는 위치에 맞춰 그대로 적용한 것뿐이다(parents[1] = src/).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# collector/runtime.py의 PROJECT_ROOT/DEFAULT_*_PATH 관례를 그대로 따른다
# (src/<module>/<file>.py 기준 parents[2] = Repository Root).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_NOTION_SYNC_LOG_PATH = PROJECT_ROOT / "runtime" / "logs" / "notion_sync.log"
DEFAULT_LATE_UPDATE_LOG_PATH = PROJECT_ROOT / "runtime" / "logs" / "daily_late_update.log"

import businessdate  # noqa: E402
from businessdate import business_date  # noqa: E402
from backup.git_ops import GitOperationError, is_permanent_failure  # noqa: E402
from backup.result import BackupStatus  # noqa: E402
from backup.runner import run_once as backup_run_once  # noqa: E402  (backup/__init__.py 없음 — 서브모듈 직접 import)
from collector import (  # noqa: E402
    Collector,
    PersistentSeenEventStore,
    RuntimeOutcome,
    run_once as collector_run_once,
)

# docs/02 §8's Desktop->role table, one hop from `reporter/profiles.py`
# where it lives. Used for exactly one number in the Dashboard row — see
# `desktops_reporting` at step 9b — and never to decide anything.
from controltower import (  # noqa: E402
    build_company_rollup,
    build_dashboard,
    event_instant_key,
    ops_runs_fields,
)
from daily import LateUpdateOutcome, update_daily_history  # noqa: E402
from events import Event, EventValidationError  # noqa: E402
from history import FileHistoryRepository, HistoryDecision, HistoryFilter  # noqa: E402
from monthly import (  # noqa: E402
    DEFAULT_STATE_PATH as DEFAULT_MONTHLY_STATE_PATH,
    MonthlyStatus,
    mark_month_dirty,
    run_once as monthly_run_once,
)
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
from oplog import (  # noqa: E402
    MAX_LOG_ERROR as _MAX_LOG_ERROR,
    append_line as _append_log_line,
    bounded as _bounded,
    bounded_error as _bounded_error,
    one_line as _one_line,
    redact as _redact,
)
from runsummary import (  # noqa: E402
    ComponentResult,
    ComponentStatus,
    Failure,
    Retryability,
    RunSummary,
    Severity,
    now_iso,
    write_summary,
)
from scheduler import SchedulerStatus, run_once as scheduler_run_once  # noqa: E402
from scheduler.lock import release_lock, try_acquire_lock  # noqa: E402  (scheduler/__init__.py가 재노출하지 않음)
from transport import run_intake  # noqa: E402


PROJECT_ROOT_RUN_SUMMARY_DIR = PROJECT_ROOT / "runtime" / "runs"
DEFAULT_RUN_SUMMARY_PATH = PROJECT_ROOT_RUN_SUMMARY_DIR / "last_run.json"

# Component names used in the Run Summary. Fixed strings rather than the
# step numbers in the comments below, because the numbers are a reading aid
# that has already been renumbered twice (6.5, 6.7, 9b) while these are a
# contract `ops_status.py` and the Dashboard read.
C_TRANSPORT = "transport"
C_COLLECTOR = "collector"
C_NOTION_SYNC = "notion_sync"
C_HISTORY_FILTER = "history_filter"
C_DAILY = "daily"
C_LATE_UPDATE = "late_update"
C_MONTHLY = "monthly"
C_BACKUP = "backup"
C_DASHBOARD = "dashboard"

# Severity per component — the codification of README RULE 5/9, not a new
# judgement. A step that records or protects Company History is CRITICAL; a
# step that projects it somewhere else is not, because the pipeline is
# explicitly designed to keep recording while those are down.
_SEVERITY = {
    C_TRANSPORT: Severity.CRITICAL,
    C_COLLECTOR: Severity.CRITICAL,
    C_HISTORY_FILTER: Severity.CRITICAL,
    C_DAILY: Severity.CRITICAL,
    C_BACKUP: Severity.CRITICAL,
    C_NOTION_SYNC: Severity.DEGRADED,
    C_LATE_UPDATE: Severity.DEGRADED,
    C_MONTHLY: Severity.DEGRADED,
    C_DASHBOARD: Severity.DEGRADED,
}


class _Recorder:
    """Collects `ComponentResult`s as the run proceeds.

    Kept as a plain accumulator with no knowledge of the pipeline: the
    Runner decides what each step's outcome means, this only remembers it
    and knows which step is currently in flight so that an exception
    escaping any step can still be attributed to it (see `run_once`'s
    `finally`).
    """

    def __init__(self) -> None:
        self.components: list[ComponentResult] = []
        self.current: str | None = None

    def begin(self, name: str) -> None:
        self.current = name

    def ok(self, name: str, **metrics: Any) -> None:
        self._add(name, ComponentStatus.SUCCESS, metrics=metrics)

    def skipped(self, name: str, **metrics: Any) -> None:
        self._add(name, ComponentStatus.SKIPPED, metrics=metrics)

    def failed(
        self,
        name: str,
        *,
        classification: str,
        reason: str,
        retryability: Retryability,
        severity: Severity | None = None,
        **metrics: Any,
    ) -> None:
        self._add(
            name,
            ComponentStatus.FAILED,
            failure=Failure(
                classification=classification,
                severity=severity or _SEVERITY.get(name, Severity.DEGRADED),
                retryability=retryability,
                reason=_bounded(reason),
            ),
            metrics=metrics,
        )

    def _add(self, name, status, *, failure=None, metrics=None) -> None:
        refs = _ARTIFACT_REFS.get(name, ())
        self.components.append(
            ComponentResult(
                name=name,
                status=status,
                failure=failure,
                metrics={k: v for k, v in (metrics or {}).items() if v is not None},
                artifact_refs=refs,
            )
        )
        if self.current == name:
            self.current = None


# Artifact Taxonomy, as measured rather than invented: every entry below is
# a path this pipeline already wrote before this module existed. Nothing new
# was created to satisfy the manifest — the manifest points at what was
# already there, which is what makes it a summary rather than a second log.
#
#   Execution Evidence   logs/ and events/  — proof of what a run did
#   Company Repository   daily/ and monthly/ — Policy / Knowledge truth
#   Operational State    state/ and locks/  — resumption points, not evidence
#
# Relative to the runtime root so a manifest stays readable when the
# repository moves, and so no absolute machine path is copied into an
# artifact that may be read on another machine.
_ARTIFACT_REFS = {
    C_TRANSPORT: ("events/transport/", "events/incoming/"),
    C_COLLECTOR: ("logs/collector.log", "events/processed/", "events/rejected/"),
    C_NOTION_SYNC: ("logs/notion_sync.log", "state/notion_retry_queue.json"),
    C_HISTORY_FILTER: ("history_candidates/keep/", "history_candidates/review/"),
    C_DAILY: ("daily/", "state/daily_history_state.json"),
    C_LATE_UPDATE: ("logs/daily_late_update.log",),
    C_MONTHLY: ("monthly/", "state/monthly_history_state.json"),
    C_BACKUP: ("backup_working_copy/", "state/backup_state.json"),
    C_DASHBOARD: ("logs/notion_sync.log", "state/dashboard_pending.json"),
}


# Every Component `run_once()` records, in the order the pipeline reaches
# them. Derived from `_ARTIFACT_REFS` rather than written out a second time:
# a hand-kept copy is a list that drifts, and this one is read by
# `ops_status.py` to notice a step that never started at all.
#
# On a run that completes, all nine appear in the manifest — each is
# recorded unconditionally as SUCCESS, SKIPPED or FAILED. A run that aborts
# records the step it died in and nothing after it, and that absence is the
# fact worth surfacing.
PIPELINE_COMPONENTS: tuple[str, ...] = tuple(_ARTIFACT_REFS)


class RunResult(tuple):
    """The Runner's return value.

    Subclasses `tuple` so every existing consumer keeps working unchanged —
    measured: 219 call sites, many doing five-way unpacking
    (`a, b, c, d, e = result`) and index access (`result[3]`). Adding a
    sixth element would have broken all of them, and adding a parallel
    return path would have created two contracts to keep in step.

    `.summary` is the addition: the Run Manifest for this execution.

    No `__slots__`: a tuple subclass that declares it cannot carry the
    instance attribute this class exists to add.
    """

    def __new__(cls, values, summary: RunSummary):
        self = super().__new__(cls, values)
        self._summary = summary  # type: ignore[attr-defined]
        return self

    @property
    def summary(self) -> RunSummary:
        return self._summary  # type: ignore[attr-defined]


_FAILED_SYNC_STATUSES = (SyncStatus.NOTION_RETRY_REQUIRED, SyncStatus.NOTION_FAILED)

def _log_notion_sync(log_path: Path, sync_result: SyncResult) -> None:
    """docs/04_NOTION_SYNC_SPEC.md §55: event_id / project_id / sync
    timestamp / result을 최소 기록한다. §56에 따라 NOTION_API_TOKEN 등
    민감정보는 절대 기록하지 않는다 — SyncResult에 애초에 그런 값이 담기지
    않으므로(§56) 여기서 별도로 걸러낼 것이 없다.

    §55는 "최소"를 정한다. 실패한 Sync는 그 최소만으로는 진단할 수 없어서
    `REASON`을 덧붙인다: `NOTION_RETRY_REQUIRED`는 재시도하면 되는 장애와
    영원히 실패할 400을 같은 한 단어로 보고하며(BUG-13), 그 둘을 가르는
    문장은 `SyncResult.error`에만 있었다. 그 값은 `run_company_ops.py`의
    stdout과 반환 tuple에는 닿지만 로그에는 닿지 않았다 — 즉 Runner가
    스케줄러 뒤에서 돌 때, 다시 말해 운영 중 실제로 도는 방식일 때는
    사라졌다. Retry Queue가 같은 Event를 매 실행 재전송하는 것을 보면서도
    이유를 알 수 없던 것이 이 누락이다.

    조건은 "status가 실패인가"가 아니라 "`error`가 채워져 있는가"다. 원래
    의도("빈 필드를 매 줄에 더하지 않는다")는 그대로이고 — 성공한 Sync의
    `error`는 여전히 대부분 None이다 — 성공한 Sync가 *할 말이 있는* 경우를
    잃지 않는다. 그런 경우가 실제로 생겼다: Notion에 저장된 `Last Updated`가
    비교할 수 없는 값이면(사람이 날짜 선택기로 시간 없이 고른 경우, docs/04
    §43) `ExecutionPlanSync`는 Late Event 보호를 건너뛰고 Update를 진행하며
    그 사실을 `error`에 적는다. status로 거르면 그 줄이 사라지고, 보호가
    꺼진 채 돌아간 실행이 로그에 아무 흔적도 남기지 않는다.
    """
    suffix = ""
    if sync_result.error:
        # 줄바꿈은 `_append_log_line()`이 escape하고, 길이는 `_bounded()`가
        # 막는다. 보통은 `_error_detail()`이 이미 400자로 자른 뒤라 이 상한에
        # 닿지 않는다 — 닿는 것은 아무 층도 자르지 않은 문자열뿐이다.
        suffix = f" REASON {_bounded(sync_result.error)}"

    _append_log_line(
        log_path,
        f"EVENT {sync_result.event_id} "
        f"PROJECT {sync_result.project_id} "
        f"NOTION_RESULT {sync_result.status.value}{suffix}",
    )


def _log_late_update(log_path: Path, message: str) -> None:
    """docs/06 §41: a History update failure must be recorded, not swallowed.

    Only dates, counts, and event_ids are written — no Event content, no
    path outside this project.
    """
    _append_log_line(log_path, f"LATE_UPDATE {message}")


def _log_dashboard(log_path: Path, message: str) -> None:
    """CEO Decision ④ Operations Dashboard 단계의 진단 기록.

    The Dashboard is this system's metrics sink, and it was the one step
    whose failures reached no sink at all: `record_run()` returning FAILED
    was answered only by a silent re-queue, and an unexpected exception was
    answered by a bare `pass`. Both are deliberately non-fatal (④: Dashboard
    실패가 Runtime을 막지 않는다) — but non-fatal is not the same as
    invisible. An operator watching a Dashboard that has stopped updating
    had no way to tell "Notion is down" from "the recording code is
    broken", because neither left a trace anywhere: not stdout, not the
    return tuple, not a log.

    Written to notion_sync.log rather than a new file: the Dashboard is a
    Notion-side concern, and §55's log already carries every other Notion
    step's outcome. The `DASHBOARD` prefix keeps the two apart for grep,
    exactly as `LATE_UPDATE` does in its own log. Same §56 rule applies —
    nothing here carries a credential.
    """
    _append_log_line(log_path, f"DASHBOARD {message}")


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
    late_update_log_path: Path | None = None,
    monthly_state_path: Path | None = None,
    notion_retry_queue_path: Path | None = None,
    dashboard_client: NotionClient | None = None,
    dashboard_pending_path: Path | None = None,
    keep_dir: Path | None = None,
    review_dir: Path | None = None,
    scheduler_state_path: Path | None = None,
    backup_state_path: Path | None = None,
    run_id: str | None = None,
    run_summary_path: Path | None = None,
):
    """Runner 1회 실행. 반환값은 각 단계가 이미 반환하는 기존 객체들의 tuple이다
    (IntakeSummary, RuntimeSummary, SchedulerRunResult, BackupLogEntry,
    tuple[SyncResult, ...]) — 새 데이터 모델을 만들지 않기 위해 그대로 묶어서
    돌려준다. Lock을 얻지 못하면 None.

    `notion_sync`를 주지 않으면(Notion 미설정) Notion Sync 단계는 건너뛰고
    다섯 번째 값은 빈 tuple이 된다 — 나머지 단계는 영향받지 않는다.
    """
    now = now or businessdate.now()

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

    # Run Contract bookkeeping. `recorder` accumulates one ComponentResult
    # per step; the `finally` below turns whatever it holds into a Run
    # Summary — including on the abort paths, which is the point. A run that
    # dies in step 5 previously left the manifest-shaped question "what did
    # the first four steps do?" answerable only from logs.
    recorder = _Recorder()
    resolved_run_summary_path = (
        Path(run_summary_path) if run_summary_path is not None else DEFAULT_RUN_SUMMARY_PATH
    )
    resolved_manifest_run_id = run_id or now_iso(now)
    run_summary: RunSummary | None = None

    try:
        # 2. Transport — docs/07 §9(Collector 실행 이전 수신측 promotion),
        #    docs/12 §5.2 Transport 책임, §7 Runtime Sequence
        recorder.begin(C_TRANSPORT)
        intake_summary = run_intake(
            transport_dir=transport_dir,
            incoming_dir=incoming_dir,
            processed_dir=processed_dir,
            rejected_dir=rejected_dir,
        )
        # BUG-39: `failed` / `skipped_not_stable` / `skipped_invalid` were
        # computed here and read by nobody. They are the record of which
        # Events did not make it in, which is exactly what an operator asks
        # after a quiet day.
        # Direct attribute access, not `getattr(..., default)`: every field
        # below is declared on `IntakeSummary`, so a default would only be
        # able to hide the day one is renamed — reporting 0 skipped files
        # forever instead of failing. It would also make the consumption
        # invisible to `ResultFieldConsumptionTests`, which reads this file
        # as text to check that BUG-39's discarded fields are now read.
        recorder.ok(
            C_TRANSPORT,
            moved=len(intake_summary.moved),
            failed=len(intake_summary.failed),
            skipped_not_stable=len(intake_summary.skipped_not_stable),
            skipped_already_present=len(intake_summary.skipped_already_present),
            skipped_invalid=len(intake_summary.skipped_invalid),
            skipped_incomplete=len(intake_summary.skipped_incomplete),
        )

        # 3. Collector — docs/07 §9 "Collector 실행" + "Pending Event 처리"
        #    (incoming/ 전량 소진이 곧 Pending 처리, docs/03 §7 기본 처리 Pipeline)
        recorder.begin(C_COLLECTOR)
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
        # `failed` counts Event files this run could not process. It is not a
        # component failure: docs/03 §53 makes per-file isolation the design,
        # and one malformed Event must not make an ordinary run look broken
        # (the same reasoning the exit-code decision below rests on). It is
        # recorded as a metric so it is visible without being alarming.
        recorder.ok(
            C_COLLECTOR,
            accepted=collector_summary.accepted,
            duplicate=collector_summary.duplicate,
            rejected=collector_summary.rejected,
            failed=collector_summary.failed,
        )

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
        # Resolved outside the `if` because step 9b (Operations Dashboard)
        # logs here too, and `dashboard_client` is independent of
        # `notion_sync` — run_once()'s contract allows one without the other.
        # Resolution is pure (no file is created until something is logged).
        resolved_notion_sync_log_path = (
            Path(notion_sync_log_path)
            if notion_sync_log_path is not None
            else DEFAULT_NOTION_SYNC_LOG_PATH
        )
        # Two facts about this step that step 9b puts on the Dashboard, and
        # that therefore have to exist whether or not this step runs.
        #
        # `notion_unreadable` is declared inside the `if` below as well —
        # this is its zero value for the unconfigured case, so the Dashboard
        # reports "no Event files were unreadable" rather than crashing on a
        # name that was never bound. `notion_queue_depth` is the queue's size
        # *after* this step, which is the number an operator needs: how many
        # Events are still waiting for Notion right now.
        #
        # Both were computed and reached only the Run Manifest. The manifest
        # shows `queued=` only when the component is non-SUCCESS, and it
        # cannot see a queued entry whose `to_event()` fails at all — so the
        # Dashboard, which is the view meant to answer "is Notion keeping
        # up", had no column that could ever say no.
        notion_unreadable: list[str] = []
        notion_queue_depth = 0
        # `begin()` before anything this step can die in — C34 §1.
        #
        # `_Recorder.begin()` is what lets the `finally` attribute an escaping
        # exception to the step that raised it ("knows which step is currently
        # in flight so that an exception escaping any step can still be
        # attributed to it"). Two of the nine steps never called it: this one
        # and step 6. Both are steps whose FIRST action reads a state file
        # that docs/10 §46 explicitly expects to find damaged.
        #
        # Measured with a corrupt `notion_retry_queue.json` — `load_retry_queue()`
        # below raises `RetryQueueError` with nothing between it and the
        # `finally`:
        #
        #     STEP_ABORTED   NONE
        #     manifest       SUCCESS / exit 0     <- for a run that aborted
        #     ops_status     "시작되지 못한 단계: notion_sync …" blaming collector
        #
        # `overall_status()` folds recorded FAILED components, so recording
        # none makes an aborted run indistinguishable from a clean one, and
        # `ops_status.py`'s LAST RUN block — the first thing AGENT.md §6 tells
        # an operator to read — prints `종합 상태 : SUCCESS (exit 0)`.
        recorder.begin(C_NOTION_SYNC)
        if notion_sync is not None:
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
            # Collected Event files this step could not read back. Kept
            # separate from `notion_sync_results`, which holds real sync
            # outcomes — see the read guard in 4b for why nothing is
            # fabricated for them. (Bound above too, for the unconfigured
            # case; this rebinding keeps the declaration next to its use.)
            notion_unreadable = []
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
                        error=_bounded_error(exc),
                    )
                _log_notion_sync(resolved_notion_sync_log_path, sync_result)
                # CEO Policy Decision (Notion Retry Architecture Plan A):
                # a still-failing event stays queued (upsert, never
                # duplicated — dedup is by event_id); any non-error result
                # clears it from the queue, whether it arrived there via the
                # queue itself or fresh this run.
                if sync_result.status in _FAILED_SYNC_STATUSES:
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
                    # `to_event()` is `Event.from_json(self.event_data)`, and
                    # the queue is a JSON file on disk that `load_queue()`
                    # shape-checks but never re-validates as an Event. A
                    # hand-edited or truncated entry therefore raises here —
                    # the same unguarded-read hole 4b had, one loop up.
                    try:
                        queued_event = queued_entry.to_event()
                    except (ValueError, TypeError, KeyError, EventValidationError) as exc:
                        notion_unreadable.append(queued_entry.event_id)
                        _append_log_line(
                            resolved_notion_sync_log_path,
                            f"NOTION_UNREADABLE queued:{queued_entry.event_id} "
                            f"{_bounded_error(exc)}",
                        )
                        continue
                    notion_sync_results.append(_sync_and_record(queued_event))

                # 4b. 이번 실행에서 새로 수집된 ACCEPTED Event.
                #
                # **Read first, then apply oldest-first.** The loop used to
                # sync in `collector_summary.files` order, which is the
                # Collector's `sorted(glob("*.json"))` — filename order. An
                # `event_id` is `uuid5(namespace, "<Desktop>|<date>|<Signal
                # filename>")`, so that order has no relation to when the work
                # happened, and docs/04 §29-30's Late Event guard refuses any
                # Event not newer than the row's `Last Updated`.
                #
                # Measured (C47), two Events of one project in ONE batch:
                #
                #     file order == time order   BLOCKED then DECISION
                #                                -> row shows the blocker
                #     file order reversed        DECISION then BLOCKED
                #                                -> NOTION_SKIPPED_OLD_EVENT,
                #                                   row shows NO blocker
                #
                # Neither Event is late in any operational sense — they are
                # an hour apart in the same run — and which of the two
                # outcomes an operator gets was a coin flip on a uuid. The
                # guard is right; the order it was fed was arbitrary.
                #
                # Sorting changes the final row state only when the older
                # Event carries state the newer one does not touch (a
                # `RESUMED` clears a blocker, a `DECISION_APPROVED` touches
                # none), and in exactly those cases the sorted answer is the
                # one the fold over the same Events gives — the invariant
                # `TheRowIsExactlyTheFoldOverWhatReachedItTests` pins. It is
                # the same key that fold uses, imported rather than restated.
                #
                # Unreadable files are handled in the read pass, so they are
                # still counted and logged; only the order of the log lines
                # relative to the sync lines changes.
                collected: list[Event] = []
                for processed_file in collector_summary.files:
                    if processed_file.outcome is not RuntimeOutcome.ACCEPTED:
                        continue
                    try:
                        event = Event.from_json(
                            processed_file.destination_path.read_text(encoding="utf-8")
                        )
                    except (OSError, ValueError, EventValidationError) as read_exc:
                        # This read had no guard, and `_sync_and_record()`'s
                        # try only covers `notion_sync.sync()` — so a file
                        # that became unreadable between step 4 and here took
                        # the whole run down from a step docs/14 §5 grades
                        # DEGRADED.
                        #
                        # Notion Sync aborting Daily History and Backup is
                        # the inversion `daily/generator.update_daily_history`
                        # already names in as many words: "A component
                        # docs/14 §5 classifies as DEGRADED was aborting one
                        # it classifies as CRITICAL, which inverts the entire
                        # point of having the two severities." Same shape,
                        # other step — and this step's own comment three
                        # lines up already says "Notion 실패가 Runtime을 막지
                        # 않는다".
                        #
                        # **What this guard does and does not buy — C34 §4.**
                        # The measurement that used to sit here read:
                        #
                        #     run ABORTED: ValueError
                        #     Daily files : NONE
                        #     backup state: MISSING
                        #
                        # and it is still, today, exactly what happens. Step 5
                        # reads the same file with no guard at all, so the run
                        # dies eleven lines later on the identical exception.
                        # Re-measured on the same scenario, one collected file
                        # corrupted after the Collector moved it:
                        #
                        #     notion_sync    FAILED   unreadable=1   (logged)
                        #     STEP_ABORTED   history_filter
                        #     overall/exit   FAILED / 2
                        #     Daily files    NONE
                        #     backup state   MISSING
                        #     dashboard      never reached
                        #
                        # So the claim to keep is narrower than the one that
                        # was here. What the guard buys is real but is about
                        # *attribution and evidence*, not survival:
                        #
                        #   - the run is charged to `history_filter`, which
                        #     docs/14 §5 grades CRITICAL, instead of to a
                        #     DEGRADED step — the inversion above is gone;
                        #   - `NOTION_UNREADABLE <file>` reaches the log, so
                        #     the operator is told *which* file, which the
                        #     bare traceback from step 5 never says.
                        #
                        # Step 5 aborting is not a defect to fix here. It is
                        # BUG-20's deliberate design: History is the CRITICAL
                        # record, and a file that cannot be read must not be
                        # silently dropped from it. The consequence worth
                        # knowing is that `Notion Unreadable` on the Dashboard
                        # (C33 §1) can only ever be non-zero from the 4a
                        # retry-queue path above — a 4b unreadable file kills
                        # the run before step 9b writes the row.
                        #
                        # Reachable without corruption: `runtime/` sits under
                        # OneDrive in this deployment (docs/11), and a sync
                        # client or scanner holding a handle produces exactly
                        # this on Windows. The Collector read the same file
                        # minutes earlier, which is why nothing here expected
                        # it to fail.
                        #
                        # Counted, not fabricated into a SyncResult: the
                        # event_id is precisely what could not be read, and
                        # inventing one would put a made-up id in the log and
                        # the manifest.
                        notion_unreadable.append(processed_file.destination_path.name)
                        _append_log_line(
                            resolved_notion_sync_log_path,
                            f"NOTION_UNREADABLE {processed_file.destination_path.name} "
                            f"{_bounded_error(read_exc)}",
                        )
                        continue
                    collected.append(event)

                collected.sort(key=lambda item: (event_instant_key(item), item.event_id))
                for event in collected:
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
                        #
                        # 그런데 삼키는 쪽 가지는 **아무 흔적도 남기지 않았다**
                        # (C40). 원래 예외를 가리지 않는 것은 옳지만, 그 결정의
                        # 대가는 실제로 있다: 이 실행이 큐에 새로 넣으려던
                        # 재시도 대상이 디스크에 없으므로 **다음 실행이 그것을
                        # 다시 시도하지 않는다.** 중단 자체는 STEP_ABORTED로
                        # 보고되지만, 큐 쓰기까지 함께 실패했다는 사실은
                        # 어디에도 없었다 — 그 둘은 사람이 해야 할 일이 다르다.
                        #
                        # raise 하지 않고 **기록만** 더한다. 제어 흐름은 한 줄도
                        # 바뀌지 않는다.
                        try:
                            _append_log_line(
                                resolved_notion_sync_log_path,
                                f"RETRY_QUEUE_SAVE_FAILED entries={len(queue_entries)} "
                                f"{_bounded_error(save_exc)}",
                            )
                        except Exception:  # noqa: BLE001
                            # 로그도 못 쓰는 상황이라면 원래 예외를 가리는 것이
                            # 훨씬 나쁘다. 기록은 최선 노력이다.
                            pass
                        if save_exc.__context__ is None:
                            raise

            # The queue's size after this step — every entry that failed
            # again plus every one this step could not even parse. Read from
            # the in-memory list rather than by re-reading the file: 4c has
            # just written exactly this list, and re-reading would be a
            # second opinion about what was written.
            notion_queue_depth = len(queue_entries)

            # Notion Sync's component verdict. Per-Event, not per-call: a run
            # that synced nine Events and queued one is not a failed sync
            # step, it is a step that did its job and left one for the retry
            # queue — so `failed` counts Events, and the step is FAILED only
            # if at least one could not be applied.
            #
            # Retryability comes from the status the sync itself reported,
            # which is the distinction BUG-13 is about: NOTION_FAILED is the
            # unexpected-exception path (unknown — it may never clear), while
            # NOTION_RETRY_REQUIRED is what the queue exists for.
            queued = [r for r in notion_sync_results if r.status in _FAILED_SYNC_STATUSES]
            # Recognised by the note `notion.sync` attaches, not by
            # re-deriving the comparison here: the module that made the
            # decision is the only one that still has both timestamps, and a
            # second derivation is how the two answers drift.
            same_instant_skips = sum(
                1
                for r in notion_sync_results
                if r.status is SyncStatus.NOTION_SKIPPED_OLD_EVENT
                and r.error
                and r.error.startswith("same-instant skip:")
            )
            # What the run actually did to Notion, as three numbers instead
            # of one (C104).
            #
            # `processed` is every Event this run handed to `ExecutionPlanSync`
            # and nothing more. Measured end to end against the live PROJECTS
            # database, a run that changed **nothing** wrote:
            #
            #     notion_sync SUCCESS {"processed": 16}
            #
            # and a run that rewrote all sixteen rows would write the same
            # bytes. The manifest is the machine-readable artefact — the one
            # `ops_status.py` reads and the one a Task Scheduler deployment
            # keeps — and it could not answer "did Notion change?". Answering
            # it needed reading `notion_sync.log` line by line, which is
            # exactly what a manifest exists to make unnecessary.
            #
            # This is the C40 shape and it is the same argument: no status is
            # added (docs/04 §32-37 enumerates those, and adding one is a spec
            # change), no behaviour moves, and `RunComponent.metrics` is a
            # free-form `Mapping[str, Any]` that docs/14 does not constrain.
            # Only the count that was already implied becomes legible.
            #
            # Zeros are kept rather than dropped, unlike `same_instant_skips`.
            # The two metrics answer opposite questions: that one reports a
            # rare divergence, so its absence means "did not happen", while
            # `written=0` is the *informative* case here — it is how "Notion
            # is already current" and "nothing reached Notion" become
            # distinguishable at a glance instead of identical silence.
            #
            # `skipped_old` **includes** `same_instant_skips`: a same-instant
            # skip returns NOTION_SKIPPED_OLD_EVENT and is counted by both.
            # Said here because a reader who adds the four numbers up and
            # gets more than `processed` would otherwise conclude one of them
            # is wrong.
            notion_created = sum(
                1 for r in notion_sync_results if r.status is SyncStatus.NOTION_CREATED
            )
            notion_updated = sum(
                1 for r in notion_sync_results if r.status is SyncStatus.NOTION_UPDATED
            )
            notion_skipped_old = sum(
                1
                for r in notion_sync_results
                if r.status is SyncStatus.NOTION_SKIPPED_OLD_EVENT
            )
            if queued or notion_unreadable:
                # An unreadable file is an Event that did not reach Notion,
                # which is what `NOTION_SYNC_INCOMPLETE` means — so it fails
                # the component exactly as a queued Event does. UNKNOWN
                # retryability: whether the next run can read the file is not
                # something this step can tell, and BUG-13 is about not
                # pretending otherwise.
                unknown = (
                    bool(notion_unreadable)
                    or any(r.status is SyncStatus.NOTION_FAILED for r in queued)
                )
                # Notion answered, and the answer will not change by waiting
                # (400/401/403/404 — see `notion.sync`'s list and why it is
                # short). docs/14 §5 defines PERMANENT as "지금 개입해야 한다
                # (ops_status.py ATTENTION에 뜬다)", and that is exactly what
                # these are: a wrong token, a database that is not shared, a
                # property value Notion refuses.
                #
                # Until now every Notion failure was RETRYABLE, and
                # `ops_status.py` lists only PERMANENT ones — deliberately, so
                # that what the next run fixes does not become a standing
                # alert. The cost was the other half: a request Notion will
                # refuse forever could not reach ATTENTION *through the
                # manifest* at all. It surfaced only once C32 §14's NOTION
                # block noticed the queue entry had aged past three days.
                #
                # This changes no exit code. `runsummary.overall_status()`
                # folds **severity**, not retryability, and Notion Sync's
                # severity is unchanged (docs/14 §5: Notion is DEGRADED, off
                # the History critical path per README RULE 5). It changes
                # only whether an operator is told today or on Thursday.
                #
                # UNKNOWN still wins. An unreadable file, or an unexpected
                # exception, means this step cannot tell — and BUG-13 is about
                # not pretending otherwise. Claiming PERMANENT there would be
                # the same overreach in the opposite direction.
                permanently_refused = [r for r in queued if r.is_permanently_refused]
                # The reason names an unrecoverable failure in preference to a
                # transient one. With a 503 and a 400 in the same batch, the
                # 400 is the one a person can act on, and `reason` is the only
                # sentence that reaches them.
                first_actionable = (permanently_refused or queued or [None])[0]
                reason = (
                    first_actionable.error
                    if first_actionable is not None and first_actionable.error
                    else (
                        f"{len(notion_unreadable)} collected Event file(s) could not be "
                        f"read: {', '.join(notion_unreadable[:5])}"
                        if notion_unreadable
                        else ""
                    )
                )
                if unknown:
                    retryability = Retryability.UNKNOWN
                elif permanently_refused:
                    retryability = Retryability.PERMANENT
                else:
                    retryability = Retryability.RETRYABLE
                recorder.failed(
                    C_NOTION_SYNC,
                    classification="NOTION_SYNC_INCOMPLETE",
                    reason=reason,
                    retryability=retryability,
                    processed=len(notion_sync_results),
                    # On the failing path too, and for a sharper reason: a
                    # partly-failed run is exactly when "how much of it landed"
                    # decides whether an operator must do anything by hand.
                    created=notion_created,
                    updated=notion_updated,
                    skipped_old=notion_skipped_old,
                    queued=len(queued),
                    unreadable=len(notion_unreadable),
                    # The status codes Notion actually answered with, so the
                    # manifest carries the machine-readable form of `reason`
                    # rather than only its prose.
                    refused=len(permanently_refused),
                    same_instant_skips=same_instant_skips or None,
                )
            else:
                recorder.ok(
                    C_NOTION_SYNC,
                    processed=len(notion_sync_results),
                    created=notion_created,
                    updated=notion_updated,
                    skipped_old=notion_skipped_old,
                    # BACKLOG E-23, made countable in C40. A Signal written
                    # without its own timestamp gets that date's midnight
                    # (docs/06 §12), the same value for every Signal of that
                    # day, so for one project only the day's first Event
                    # reaches Notion — the rest are skipped by the Late Event
                    # guard as "not newer" (docs/04 §29-30). Both specs are
                    # right; the divergence lives between them and the
                    # decision that resolves it is not this module's.
                    #
                    # What was wrong meanwhile is that the run reported
                    # nothing: the skip is a SUCCESS component, and a
                    # same-instant skip was byte-identical to a genuine Late
                    # Event in the result object. `or None` so an ordinary
                    # run carries no metric at all — `_Recorder._add()` drops
                    # None — and the number appears only when it happened.
                    same_instant_skips=same_instant_skips or None,
                )
        else:
            # Not a fault. docs/04's own contract makes `notion_sync=None` a
            # supported deployment, and README RULE 9 keeps Company History
            # recording before Notion is ever configured. Reporting this as
            # FAILED would make every pre-Notion install look broken.
            recorder.skipped(C_NOTION_SYNC)

        # 5. History Filter — docs/07 §37 step 6 "History Pipeline",
        #    docs/05 §2 Event -> History Filter -> Candidate
        #    (ACCEPTED만 대상. DUPLICATE/REJECTED/FAILED는 History 대상이 아니다.
        #    processed_dir 재스캔이 아니라 이번 실행의 반환값만 사용한다 — Gap 3.)
        recorder.begin(C_HISTORY_FILTER)
        history_filter = HistoryFilter()
        repository = FileHistoryRepository(keep_dir=keep_dir, review_dir=review_dir)
        # 이번 실행에서 KEEP Candidate가 새로 생긴 날짜들. 6.5단계(Late Event
        # Update)가 어떤 날짜를 다시 확인해야 하는지 판단하는 근거이며, 그
        # 판단을 위해 Repository를 추가로 조회하지 않기 위한 것이다 — Architecture
        # Invariant("아무것도 pending이 아니면 repository.list()를 호출하지 않는다",
        # tests/test_architecture_invariants.py)를 그대로 지킨다.
        kept_dates: set[date] = set()
        # Where this run's Events came from — two facts the Dashboard row
        # could not answer before C47: which Desktops contributed, and
        # whether any Event claimed a `role` its Desktop does not own.
        #
        # Collected here rather than by re-reading `processed/`, which holds
        # every Event this project ever collected — a per-run row must not be
        # built from an all-time directory. The counting itself happens in
        # `controltower.build_company_rollup()` after this loop (C48): the
        # inline version was a second implementation of `_roll_desktops()`,
        # including a second copy of docs/02 §8's pair check, and the
        # Dashboard row is supposed to *be* the rollup rather than agree with
        # it by inspection.
        run_events: list[tuple[Event, str]] = []
        for processed_file in collector_summary.files:
            if processed_file.outcome is not RuntimeOutcome.ACCEPTED:
                continue
            event = Event.from_json(processed_file.destination_path.read_text(encoding="utf-8"))
            run_events.append((event, processed_file.destination_path.name))
            filter_result = history_filter.evaluate(event)
            repository.save(filter_result.candidate)
            if filter_result.decision is HistoryDecision.KEEP:
                # 오류 처리를 덧붙이지 않는다. 바로 위 Event.from_json()이 이미
                # events.schema의 timestamp 검증(_timestamp_error 역시
                # datetime.fromisoformat을 쓴다)을 통과시켰으므로 여기서 파싱이
                # 실패할 수 없다. 방어 코드를 넣으면 이 단계에 per-event 오류
                # 처리가 없다는 문서화된 성질(BUG-20 characterization,
                # tests/test_architecture_invariants.py)을 코드 텍스트상으로
                # 흐리게 만들 뿐 실제로 바뀌는 것은 없다.
                kept_dates.add(business_date(datetime.fromisoformat(event.timestamp)))
        recorder.ok(C_HISTORY_FILTER, kept_dates=len(kept_dates))


        # 6. Daily History — docs/07 §37 steps 7-8
        #    "Missing Daily Date 계산" + "Daily Catch-up"
        #    (scheduler.run_once가 내부에서 daily.generate_daily_history를 반복 호출)
        #    already_locked=True: 1번에서 이미 시스템 전체 Lock을 쥐고 있으므로
        #    Scheduler 자신의 Lock을 별도로 잡지 않는다(위 1번 주석 참고).
        #
        # `begin()` before the call, for step 4's reason and with a worse
        # consequence — C34 §1. This step is CRITICAL (`_SEVERITY`): it is the
        # one that writes Company History.
        #
        # Measured with a corrupt `daily_history_state.json`, which is the
        # first thing `scheduler.run_once()` reads:
        #
        #     raised         SchedulerStateError
        #     STEP_ABORTED   NONE
        #     manifest       SUCCESS / exit 0
        #
        # A run that never wrote Company History and never reached Backup,
        # recorded as a success. The same corrupt file aborts every following
        # run identically, so Company History stops advancing for good while
        # the manifest keeps saying SUCCESS. BUG-3 already records that a
        # corrupt state file aborts the Runner; what this adds is that the
        # abort was *unattributed*, which is why it read as success.
        recorder.begin(C_DAILY)
        scheduler_result = scheduler_run_once(
            repository,
            history_start_date=history_start_date,
            now=now,
            state_path=scheduler_state_path,
            daily_output_dir=local_master_dir / "daily",
            already_locked=True,
        )

        resolved_late_update_log_path = (
            Path(late_update_log_path)
            if late_update_log_path is not None
            else DEFAULT_LATE_UPDATE_LOG_PATH
        )

        # 6b. Daily Close 실패 진단 (BUG-39의 한 조각).
        #     `SchedulerRunResult`는 어느 날짜에서 멈췄는지(`failed_date`)와
        #     왜 멈췄는지(`error`)를 이미 채워서 돌려준다. 두 값 모두 읽는
        #     곳이 없었다 — `run_company_ops.py`는 `status`와
        #     `generated_dates`만 출력하므로, Daily Close가 실패하면 운영자가
        #     보는 것은 `FAILED, generated=[]` 한 줄뿐이었다. 그 다음에 할 수
        #     있는 일이 없는 메시지다.
        #
        #     Scheduler는 첫 실패 날짜에서 멈추므로(`scheduler.py`) 그 날짜와
        #     그 이후 날짜는 아직 Daily 파일이 없다. 즉 이것은 무시해도 되는
        #     실패가 아니라 다음 실행이 반드시 다시 시도해야 하는 지점이고,
        #     그 지점을 아는 유일한 방법이 이 두 필드다.
        #
        #     기록 위치는 Monthly 실패와 같은 daily_late_update.log다 — Daily
        #     History 도메인의 실패는 이미 전부 그 파일에 모인다. BUG-39가
        #     말하는 "종합 run summary artifact"를 만드는 것이 아니다(그건
        #     형식·위치 결정이 필요한 별건이다). 이미 있는 sink에, 이미 있는
        #     형식으로, 지금 버려지는 실패 정보만 흘려보낸다.
        if scheduler_result.status is SchedulerStatus.FAILED:
            _log_late_update(
                resolved_late_update_log_path,
                f"SCHEDULER_FAILED date="
                f"{scheduler_result.failed_date.isoformat() if scheduler_result.failed_date else 'unknown'} "
                f"{_bounded(scheduler_result.error or '')}",
            )
            # CRITICAL: this is the step that writes Company History. Its
            # failure is the one this pipeline exists to prevent, and it is
            # retryable — Scheduler stops at the first failing date and the
            # next run resumes from exactly there, which is why the manifest
            # carries `failed_date` rather than only a status.
            recorder.failed(
                C_DAILY,
                classification="DAILY_CLOSE_FAILED",
                reason=scheduler_result.error or "",
                retryability=Retryability.RETRYABLE,
                failed_date=(
                    scheduler_result.failed_date.isoformat()
                    if scheduler_result.failed_date
                    else None
                ),
                generated_days=len(scheduler_result.generated_dates),
                reused_days=len(scheduler_result.reused_dates) or None,
            )
        else:
            recorder.ok(
                C_DAILY,
                status=scheduler_result.status.value,
                generated_days=len(scheduler_result.generated_dates),
                # Days this run closed without writing, because the file was
                # already there — a crashed predecessor's (docs/07 §28) or,
                # after a disaster restore, git's (C39). `or None` so the
                # metric is absent on the ordinary run rather than a standing
                # `reused_days=0`; `_Recorder._add()` drops None values.
                reused_days=len(scheduler_result.reused_dates) or None,
            )

        # 6.5. Late Event Update — docs/06 §36-40.
        #     Audit BUG-17(P0) 해소. 이미 Daily Close가 끝난 날짜의 Event가
        #     뒤늦게 도착하면(Desktop이 며칠 꺼져 있었던 경우 — Multi-Desktop
        #     구성에서는 예외가 아니라 일상이다) Collector는 ACCEPTED,
        #     History Filter는 KEEP, Notion Sync는 성공으로 처리하지만
        #     scheduler.run_once()는 .md가 이미 있는 날짜를 건너뛰고
        #     generate_daily_history()는 덮어쓰기를 거부한다. 결과적으로 그
        #     Event는 Company History에 영원히 들어가지 못하면서 모든 지표는
        #     성공을 보고했다(README RULE 7 위반).
        #
        #     대상 날짜는 5단계에서 모은 `kept_dates`뿐이다 — 이번 실행에서
        #     KEEP Candidate가 새로 생기지 않았다면 Late Event도 있을 수 없으므로
        #     이 단계 전체가 아무 일도 하지 않는다(파일 조회조차 하지 않는다).
        #     방금 Scheduler가 생성한 날짜도 그 파일이 이미 새 Candidate를
        #     포함하고 있으므로 NO_LATE_EVENTS로 끝난다 — 이중 기록되지 않는다.
        #
        #     Backup(7단계)보다 먼저 실행해야 갱신된 Daily 파일이 같은 실행에서
        #     백업된다. backup/runner.py의 "backup: history late update" 커밋
        #     템플릿(docs/08 §65)이 바로 이 경우를 위해 이미 존재한다.
        #     반환값(tuple)은 바꾸지 않는다 — Late Event Update는 새로운
        #     파이프라인 단계가 아니라 Daily History 단계 안의 보정이고,
        #     Runner의 반환 계약은 이미 안정적인 공개 API다. §40이 요구하는
        #     "언제 수정됐는지 추적 가능"은 갱신된 Daily 파일 자신의
        #     `Last Updated At` / `Late Events Added`가 담당한다. 다만 실패는
        #     그 파일에 남지 않으므로(§41 "오류 기록") 로그로 남긴다.
        recorder.begin(C_LATE_UPDATE)
        late_updated_dates: list[date] = []
        late_update_failures: list[str] = []

        # One `repository.list()` for the whole loop, not one per date —
        # exactly the batch cache `scheduler.py` already applies to its own
        # per-date loop (CEO Decision ②, History Repository Cache A안), and
        # the reason `update_daily_history()` accepts `keep_candidates` at
        # all. This call site was the one that never got it.
        #
        # Measured with 3,000 stored Candidates and 7 late dates: 7 calls /
        # 0.97 s becomes 1 call / 0.17 s, and the gap grows with both
        # numbers because `keep/` is never pruned (BACKLOG B절 6번).
        #
        # A snapshot is correct here for the same reason it is in the
        # Scheduler: step 5 finished writing every Candidate before this
        # step begins, and nothing inside this loop writes one.
        #
        # On failure it falls back to `None`, which is the previous
        # behaviour byte for byte — each date then makes its own call and
        # catches its own error, so a broken repository still yields one
        # FAILED result per date rather than escaping the step.
        try:
            shared_keep = (
                repository.list(decision=HistoryDecision.KEEP) if kept_dates else None
            )
        except Exception:  # noqa: BLE001
            shared_keep = None

        for kept_date in sorted(kept_dates):
            # `update_daily_history()`'s docstring promises it never raises
            # for an I/O or rendering failure, and this loop believed it —
            # with no belt of its own. Measured by injecting a raise into
            # that call: the run ABORTED and `backup_state.json` was never
            # written. Late Event Update is `Severity.DEGRADED`; Backup is
            # CRITICAL, so a DEGRADED step was taking a CRITICAL one down,
            # which is the inversion this file's Notion step was just fixed
            # for and which `update_daily_history()` itself already names.
            #
            # The promise is now actually true (its `select_late_candidates()`
            # call had been sitting between two guards rather than inside
            # one), but the same reasoning `run_once()` applies to Monthly
            # applies here: *"삼키되, 흔적 없이 삼키지는 않는다"*. An
            # unexpected escape is recorded against this date and the loop
            # moves on, so one bad date cannot cost the run its Backup.
            try:
                late_result = update_daily_history(
                    repository,
                    kept_date,
                    output_dir=local_master_dir / "daily",
                    now=now,
                    keep_candidates=shared_keep,
                )
            except Exception as late_exc:  # noqa: BLE001  (§41 / docs/14 §5)
                late_update_failures.append(
                    f"{kept_date.isoformat()}: {_bounded_error(late_exc)}"
                )
                _log_late_update(
                    resolved_late_update_log_path,
                    f"LATE_UPDATE_UNEXPECTED {kept_date.isoformat()} "
                    f"{_bounded_error(late_exc)}",
                )
                continue
            if late_result.outcome is LateUpdateOutcome.UPDATED_LATE_EVENT:
                late_updated_dates.append(kept_date)
                _log_late_update(
                    resolved_late_update_log_path,
                    f"UPDATED_LATE_EVENT {kept_date.isoformat()} "
                    f"added={len(late_result.added_event_ids)} "
                    f"events={','.join(late_result.added_event_ids)}",
                )
            elif late_result.outcome is LateUpdateOutcome.FAILED:
                late_update_failures.append(late_result.error or kept_date.isoformat())
                _log_late_update(
                    resolved_late_update_log_path,
                    f"FAILED {kept_date.isoformat()} {_bounded(late_result.error or '')}",
                )

        # DEGRADED rather than CRITICAL: a Late Event that fails to merge is
        # not lost. Its Candidate is already durable in history_candidates/
        # (step 5 ran first, and that ordering is why it is recoverable at
        # all). docs/06 §41 asks for the failure to be recorded, which is
        # what this is.
        #
        # PERMANENT rather than RETRYABLE, and the difference is not
        # cosmetic. `kept_dates` holds only the dates whose Candidates were
        # written by *this* run, so the next run has no reason to look at a
        # date whose merge failed — nothing re-queues it. Measured: a merge
        # that failed on run 2 was still unmerged after runs 3 and 4, both of
        # which reported `late_update` SUCCESS with `updated=0` and an
        # overall SUCCESS / exit 0. It merged only on run 5, when an
        # unrelated new Event happened to land on the same date.
        #
        # docs/14 §5 defines RETRYABLE as "없음. 다음 실행이 재시도한다" and
        # PERMANENT as "지금 개입해야 한다 (ops_status.py ATTENTION에 뜬다)".
        # `ops_status.py` acts on that literally — it lists only PERMANENT
        # failures, deliberately, so a RETRYABLE one would not appear
        # anywhere. RETRYABLE was therefore the one classification that made
        # a Late Event silently missing from Company History with every
        # indicator green (README RULE 7).
        #
        # Making the retry real is the better fix and is not this one: it
        # needs either a persisted set of failed dates or a reconciliation
        # pass over keep/ against the Daily files, and both are new
        # mechanisms rather than a corrected value (BACKLOG E-17).
        if late_update_failures:
            recorder.failed(
                C_LATE_UPDATE,
                classification="LATE_EVENT_MERGE_FAILED",
                reason=late_update_failures[0],
                retryability=Retryability.PERMANENT,
                updated=len(late_updated_dates),
                failed=len(late_update_failures),
            )
        else:
            recorder.ok(C_LATE_UPDATE, updated=len(late_updated_dates))

        # 6.7. Monthly Consolidation — docs/09 §50-51.
        #      docs/09 §50이 정한 순서 그대로다: Daily Catch-up 다음, Backup
        #      앞. §51은 Daily와 Monthly가 별도 프로세스로 경쟁하지 않도록
        #      "동일 Company Ops Runner → Daily 작업 먼저 → Monthly 작업"을
        #      권장하며, 이 위치가 정확히 그것이다 — Monthly는 이미 이 실행에서
        #      확정된 Daily 파일만 읽는다(§12-13).
        #
        #      Late Event로 Daily가 바뀐 달은 먼저 DIRTY로 표시한다(§54-56).
        #      다음 줄의 monthly_run_once()가 같은 실행 안에서 그 달을 다시
        #      만들어 MONTHLY_UPDATED로 끝낸다(§57) — Monthly가 자기 Daily와
        #      어긋난 채 남아 있는 창이 없다.
        #
        #      Monthly 실패는 Runtime을 중단시키지 않는다. Daily/Backup은 이미
        #      끝났고, PENDING/FAILED인 달은 다음 실행에서 다시 시도된다
        #      (§39, §44, §74). Notion Sync·Dashboard 단계와 같은 방어 방식이다.
        resolved_monthly_state_path = (
            Path(monthly_state_path)
            if monthly_state_path is not None
            else DEFAULT_MONTHLY_STATE_PATH
        )
        recorder.begin(C_MONTHLY)
        monthly_failures: list[str] = []
        monthly_done = 0
        try:
            for updated_date in late_updated_dates:
                # `monthly_dir` matters: without it the "has this month been
                # consolidated?" question is answered from state alone, and
                # state can lag the file it describes (a run that wrote the
                # Monthly and died before saving). See `mark_month_dirty()`.
                mark_month_dirty(
                    resolved_monthly_state_path,
                    updated_date,
                    monthly_dir=local_master_dir / "monthly",
                )

            monthly_result = monthly_run_once(
                daily_dir=local_master_dir / "daily",
                monthly_dir=local_master_dir / "monthly",
                history_start_date=history_start_date,
                now=now,
                state_path=resolved_monthly_state_path,
            )
            for month_result in monthly_result.results:
                if month_result.status in (
                    MonthlyStatus.MONTHLY_GENERATED,
                    MonthlyStatus.MONTHLY_UPDATED,
                ):
                    monthly_done += 1
                    _log_late_update(
                        resolved_late_update_log_path,
                        f"{month_result.status.value} {month_result.key} "
                        f"items={month_result.item_count}",
                    )
                    # Company History that is in the Daily file and did not
                    # reach the Monthly. A separate line, because the one
                    # above is a success and this is not — `item_count`
                    # counts what arrived, so a dropped item leaves no trace
                    # in it. AGENT.md §6a already sends an operator to this
                    # log for "돌긴 돌았는데 뭔가 안 됐다", which is exactly
                    # this shape.
                    #
                    # Measured cause: `monthly/parser.py` loses a whole
                    # section when one Event's `project_id` carries a `##`
                    # heading — three Events, two of them innocent, all
                    # dropped, MONTHLY_GENERATED reported. Escaping that is
                    # docs/06's rendering contract (BUG-11/27), so the loss
                    # stays; being unable to see it does not.
                    if month_result.unconsolidated_days:
                        _log_late_update(
                            resolved_late_update_log_path,
                            f"MONTHLY_UNCONSOLIDATED {month_result.key} "
                            f"{_bounded(', '.join(month_result.unconsolidated_days))} "
                            f"— Daily에 있는 `- Event ID:` 줄이 Monthly 항목이 "
                            f"되지 못했다. 해당 Daily 파일을 확인해야 한다",
                        )
                elif month_result.status in (
                    MonthlyStatus.MONTHLY_PENDING,
                    MonthlyStatus.MONTHLY_FAILED,
                ):
                    # PENDING is not a failure: docs/09 §10 declines to build
                    # a month whose Daily set has a hole, on purpose, and the
                    # next run builds it once Daily Catch-up fills the gap.
                    # Only FAILED is recorded as one.
                    if month_result.status is MonthlyStatus.MONTHLY_FAILED:
                        monthly_failures.append(month_result.error or month_result.key)
                    _log_late_update(
                        resolved_late_update_log_path,
                        f"{month_result.status.value} {month_result.key} "
                        f"{_bounded(month_result.error or '')}",
                    )
        except Exception as monthly_exc:  # noqa: BLE001  (§74: Monthly 실패가 Runtime을 막지 않는다)
            # 삼키되, 흔적 없이 삼키지는 않는다. monthly_run_once()가 스스로
            # 반환하는 PENDING/FAILED는 위에서 기록되지만, 그 바깥으로 새는
            # 예기치 못한 예외는 이전에 아무 기록도 남기지 않았다 — Monthly가
            # 조용히 사라지고 운영자가 알아챌 방법이 없었다.
            # docs/09 §44/§74는 실패를 "기록하되 Runtime을 막지 않는다"로
            # 정하고 있으므로, 여기서 막지 않는 것과 기록하는 것은 양립한다.
            _log_late_update(
                resolved_late_update_log_path,
                f"MONTHLY_FAILED (unexpected) {_bounded_error(monthly_exc)}",
            )
            monthly_failures.append(_bounded_error(monthly_exc))

        if monthly_failures:
            recorder.failed(
                C_MONTHLY,
                classification="MONTHLY_CONSOLIDATION_FAILED",
                reason=monthly_failures[0],
                retryability=Retryability.RETRYABLE,
                consolidated=monthly_done,
                failed=len(monthly_failures),
            )
        else:
            recorder.ok(C_MONTHLY, consolidated=monthly_done)

        # 7. Backup — docs/07 §37 step 9 "Backup Pending 처리", docs/08 §14-15
        recorder.begin(C_BACKUP)
        try:
            backup_entry = backup_run_once(
                local_master_dir,
                backup_working_copy_dir,
                state_path=backup_state_path,
                now=now,
                run_id=run_id,
            )
        except GitOperationError as exc:
            # Classify, then re-raise unchanged.
            #
            # Measured against a real broken remote: without this, the
            # manifest recorded `STEP_ABORTED [CRITICAL/UNKNOWN]` from the
            # generic `finally` fallback — while `backup_state.json` said
            # BACKUP_PENDING and `run_company_ops.py` told the operator "다음
            # Runner 실행이 자동으로 다시 push합니다". The manifest was the
            # least precise account of a failure the rest of the system had
            # already classified correctly, and it is the account
            # `ops_status.py` reads.
            #
            # That mattered concretely: ATTENTION only surfaces PERMANENT
            # failures, so a genuinely permanent failure arriving by this
            # path would have been filed as UNKNOWN and never surfaced.
            # `is_permanent_failure()` answers docs/08 §21/§62's question —
            # can a retry fix this — and is what `run_company_ops.py` and
            # `backup/runner.py` already use, reused here rather than
            # restated, so the three cannot disagree.
            #
            # It used to read `is_authentication_failure(str(exc))`, the
            # same rule asked of the message text alone. That could not see
            # `WorkingCopyNotAGitRepositoryError`, which `backup/git_ops.py`
            # raises before any git command runs — so an unconfigured Backup
            # was recorded RETRYABLE/DEGRADED next to a `reason` telling the
            # operator to go and create the repository, and ATTENTION stayed
            # silent about it. That is the state of every fresh deployment.
            #
            # Re-raised because whether run_once() should absorb a Backup
            # failure is BUG-4, a contract decision (BACKLOG A-18). This
            # changes what the run *records*, not what it does.
            permanent = is_permanent_failure(exc)
            recorder.failed(
                C_BACKUP,
                classification="BACKUP_FAILED" if permanent else "BACKUP_PENDING",
                reason=str(exc),
                retryability=(
                    Retryability.PERMANENT if permanent else Retryability.RETRYABLE
                ),
                severity=Severity.CRITICAL if permanent else Severity.DEGRADED,
            )
            raise
        # docs/08 §19/§21 already classify these two; the manifest carries
        # that classification rather than inventing one.
        #
        #   PENDING  a transient push failure. The commit exists locally and
        #            the next run re-pushes it — routine, so DEGRADED.
        #   FAILED   credential/permission. §62 forbids retrying it forever;
        #            it needs a person, so CRITICAL.
        #
        # This is precisely the distinction that makes DEGRADED worth having:
        # collapsing both into "backup broke" would page someone for the
        # ordinary case, and collapsing both into success would hide the one
        # that never clears. BUG-39: `push_result` and `commit_hash` reach an
        # artifact here for the first time.
        #
        # **This first arm is unreachable today** (C41, found by a line
        # coverage pass and then read out of `backup/runner.py`). Every
        # `BackupLogEntry` that module *returns* carries SUCCESS,
        # NOT_REQUIRED or FAILED; `BackupStatus.PENDING` is only ever written
        # to `state.backup_status` immediately before a `raise`. So a pending
        # push arrives here as the `except GitOperationError` above, which
        # produces the same classification without these two metrics —
        # measured, the manifest for a real rejected push carries `{}`.
        #
        # Kept rather than deleted: it is the path this branch takes the day
        # BUG-4 / A-18 is decided the other way and `run_once()` absorbs the
        # Backup exception. Named as unreachable so nobody reads it as the
        # explanation for a BACKUP_PENDING they are looking at.
        # `BackupPendingReachesTheManifestTests` pins both halves — what the
        # reachable path actually records, and the premise that makes this
        # one unreachable.
        if backup_entry.final_status is BackupStatus.PENDING:
            recorder.failed(
                C_BACKUP,
                classification="BACKUP_PENDING",
                reason=backup_entry.push_result or "",
                retryability=Retryability.RETRYABLE,
                severity=Severity.DEGRADED,
                commit_hash=backup_entry.commit_hash,
                changed_files=len(backup_entry.changed_files),
            )
        elif backup_entry.final_status is BackupStatus.FAILED:
            # `BACKUP_FAILED`는 두 가지 서로 다른 일에서 나온다. docs/08 §21의
            # 인증/권한 실패와, docs/08 §31/§44-47의 **Local Master 파일 삭제
            # 감지**다. 후자는 `sync_to_working_copy()`가 add/commit/push를
            # 통째로 막고 돌아오는 경우로, Company History 파일이 사라졌다는
            # 뜻이다 — 이 실행에서 일어날 수 있는 일 중 가장 무거운 축이다.
            #
            # Manifest에는 그 구분이 없었다. 실측, Daily 하루치를 지우고 실행:
            #
            #     classification  BACKUP_FAILED
            #     reason          ""            <- 비어 있다
            #     metrics         changed_files=1
            #
            # `deleted_files`는 성공 분기에만 실려 있었고, 삭제 분기에는
            # `push_result`가 None이라 `reason`도 비었다. 즉 Run Manifest만
            # 읽어서는 **Company History가 지워졌다는 사실 자체를 알 수 없고**
            # 자격증명 실패와 구별되지도 않는다. docs/14 §3이 Manifest에게
            # 요구하는 것이 정확히 그 한 줄이다.
            #
            # 새 classification 값을 만들지는 않았다 — 그 어휘는 docs/14 §5의
            # 것이고 §5의 예시는 `BACKUP_FAILED`를 인증 실패에 묶고 있다
            # (BACKLOG 참조, 승인 대상). `reason`은 `Failure`의 docstring이
            # 자유 텍스트라고 명시한 필드이므로 여기에 사실을 적는다.
            deleted = backup_entry.deleted_files
            reason = backup_entry.push_result or ""
            if deleted:
                reason = _bounded(
                    "Local Master에서 파일 %d건이 사라져 Backup이 add/commit/push를 "
                    "중단했다 (docs/08 §31): %s"
                    % (len(deleted), ", ".join(_one_line(name) for name in deleted[:5]))
                    + (" 외" if len(deleted) > 5 else "")
                )
            recorder.failed(
                C_BACKUP,
                classification="BACKUP_FAILED",
                reason=reason,
                retryability=Retryability.PERMANENT,
                commit_hash=backup_entry.commit_hash,
                changed_files=len(backup_entry.changed_files),
                deleted_files=len(deleted),
            )
        else:
            recorder.ok(
                C_BACKUP,
                status=backup_entry.final_status.value,
                commit_hash=backup_entry.commit_hash,
                changed_files=len(backup_entry.changed_files),
                deleted_files=len(backup_entry.deleted_files),
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
        recorder.begin(C_DASHBOARD)
        if dashboard_client is not None:
            try:
                resolved_dashboard_pending_path = (
                    Path(dashboard_pending_path)
                    if dashboard_pending_path is not None
                    else DEFAULT_DASHBOARD_PENDING_PATH
                )
                # The manifest's run_id, not a second derivation of it. Both
                # expressions produced the same string — `now_iso()` *is*
                # `isoformat(timespec="seconds")` — so this was one rule
                # written twice, and the OPS_RUNS row correlating with its
                # Run Manifest depended on the two staying in step. Reusing
                # the value makes the correlation structural: the Dashboard
                # row is keyed by the manifest's own run_id or it is keyed by
                # nothing.
                resolved_run_id = resolved_manifest_run_id

                # The Control Tower half of this row, derived here rather
                # than back at step 5 — and the placement is the point.
                #
                # One derivation, two readers: `ops_status.py`'s CONTROL
                # TOWER block and this row come from the same fold, and one
                # *arrangement*, two readers — the Dashboard Model is what
                # the screen renders and `ops_runs_fields()` projects the
                # same model onto this row's columns. Formatting the string
                # inline would be the fork C49 removed.
                #
                # It sits **inside this try** because it is Dashboard data
                # and CEO Decision ④ fixes what that means: "Dashboard 기록
                # 실패는 Runtime을 절대 중단시키면 안 된다." Computed at step
                # 5 it would have been a Dashboard-only derivation standing
                # in front of Daily History, Monthly and Backup — three
                # CRITICAL steps — with nothing catching it. Both calls are
                # pure transforms over Events that already parsed, so this
                # is a placement argument rather than a suspicion; the
                # difference is that the argument no longer has to hold.
                #
                # Also skipped entirely when no Dashboard is configured,
                # which is the supported deployment shape (docs/04).
                #
                # Built over **this run's** Events, never over `processed/`:
                # an all-time number on a per-run row is the mistake step
                # 5's own comment warns about. Measured at 5,000 Events in
                # one run: rollup 24 ms, model 0.3 ms, projection 0.009 ms.
                run_rollup = build_company_rollup(events=run_events, now=now)
                run_control_tower = ops_runs_fields(
                    build_dashboard(run_rollup, now=now)
                )

                # 밀린 기록 먼저 재시도한다(Retry Queue와 동일한 "먼저 처리" 원칙).
                drain_result = drain_pending(
                    resolved_dashboard_pending_path, dashboard_client
                )
                drained, still_pending = drain_result
                # drain_pending()'s return value was previously discarded, so a
                # backlog that kept failing every run was invisible: the queue
                # grew on disk and nothing said so. Logged only when there was
                # actually a backlog — a healthy run stays silent.
                #
                # REASON carries why the last one failed. Without it a record
                # Notion permanently refuses looks exactly like one waiting out
                # a network outage: both just reappear next run with
                # attempt_count one higher. Same reason 4단계's NOTION_SYNC
                # line carries REASON (BUG-13), same bounded formatting.
                if drained or still_pending:
                    reason = drain_result.last_reason
                    _log_dashboard(
                        resolved_notion_sync_log_path,
                        f"DRAIN_PENDING drained={drained} still_pending={still_pending}"
                        + (f" REASON {_bounded_error(reason)}" if reason else ""),
                    )

                dashboard_result = dashboard_record_run(
                    dashboard_client,
                    run_id=resolved_run_id,
                    run_at=now,
                    intake_summary=intake_summary,
                    collector_summary=collector_summary,
                    scheduler_result=scheduler_result,
                    backup_entry=backup_entry,
                    notion_sync_results=notion_sync_results,
                    # The two Notion facts a per-run result set cannot carry:
                    # an Event file that could not be parsed never becomes a
                    # SyncResult, and the queue's depth is a property of the
                    # queue rather than of this run's results. Passed rather
                    # than defaulted — `record_run()`'s defaults exist for
                    # callers that do not have these numbers, and this one
                    # does.
                    notion_unreadable=len(notion_unreadable),
                    notion_queued=notion_queue_depth,
                    # Step 5's Dashboard Model, projected onto this row's
                    # two Control Tower columns. Which Desktops reported and
                    # how the string is ordered, why silent Desktops are
                    # dropped here but present-and-empty on the panel, and
                    # the rich_text bound all live in
                    # `controltower/projection.py` — one place, read by both
                    # this row and anything else that projects the model.
                    desktops_reporting=run_control_tower["desktops_reporting"],
                    role_mismatches=run_control_tower["role_mismatches"],
                    # The manifest's own verdict inputs, so the Dashboard
                    # row cannot contradict the exit code of the run that
                    # produced it (C37). Every step except this one has
                    # already recorded by now — `dashboard` is last, and a
                    # Dashboard failure is reported in the row it failed to
                    # write only in the sense that the row is not written.
                    #
                    # Measured before this existed: `late_update` or
                    # `monthly` FAILED gave manifest DEGRADED / exit 3 and a
                    # Dashboard row saying `Overall OK`. Those two are
                    # exactly the steps that record FAILED without raising,
                    # so they were the two the row could not see.
                    failed_steps=[
                        component.name
                        for component in recorder.components
                        if component.status is ComponentStatus.FAILED
                    ],
                    critical_failed_steps=[
                        component.name
                        for component in recorder.components
                        if component.status is ComponentStatus.FAILED
                        and component.failure is not None
                        and component.failure.severity is Severity.CRITICAL
                    ],
                )
                if dashboard_result.outcome is DashboardOutcome.FAILED:
                    _log_dashboard(
                        resolved_notion_sync_log_path,
                        f"FAILED run_id={resolved_run_id} {_bounded(dashboard_result.error or '')}",
                    )
                    if dashboard_result.properties is not None:
                        save_pending(
                            resolved_dashboard_pending_path,
                            run_id=resolved_run_id,
                            properties=dashboard_result.properties,
                            now=now,
                        )
                    recorder.failed(
                        C_DASHBOARD,
                        classification="DASHBOARD_RECORD_FAILED",
                        reason=dashboard_result.error or "",
                        retryability=Retryability.RETRYABLE,
                        still_pending=still_pending,
                    )
                elif dashboard_result.outcome is DashboardOutcome.SKIPPED_NOT_CONFIGURED:
                    recorder.skipped(C_DASHBOARD)
                else:
                    recorder.ok(
                        C_DASHBOARD, drained=drained, still_pending=still_pending
                    )
            except Exception as dashboard_exc:  # noqa: BLE001  (CEO ④: Dashboard 실패가 Runtime을 막지 않는다)
                # 삼키되, 흔적 없이 삼키지는 않는다 — 6.7단계 Monthly와 같은
                # 처리다. `pass`였을 때는 이 경로로 빠진 실행이 Dashboard를
                # 갱신하지 않은 이유를 어디에서도 알 수 없었다.
                # `resolved_run_id`는 이 예외가 그 줄 이전에 발생했다면 아직
                # 없으므로 `run_id`(호출자가 준 원본)를 쓴다.
                _log_dashboard(
                    resolved_notion_sync_log_path,
                    f"FAILED (unexpected) run_id={run_id} "
                    f"{_bounded_error(dashboard_exc)}",
                )
                recorder.failed(
                    C_DASHBOARD,
                    classification="DASHBOARD_UNEXPECTED_ERROR",
                    reason=_bounded_error(dashboard_exc),
                    retryability=Retryability.UNKNOWN,
                )
        else:
            # CEO ④'s Dashboard is optional in exactly the way Notion Sync
            # is. An unconfigured one is not a fault.
            recorder.skipped(C_DASHBOARD)

        run_summary = RunSummary(
            run_id=resolved_manifest_run_id,
            started_at=now_iso(now),
            finished_at=now_iso(businessdate.now()),
            components=tuple(recorder.components),
        )
        return RunResult(
            (
                intake_summary,
                collector_summary,
                scheduler_result,
                backup_entry,
                tuple(notion_sync_results),
            ),
            run_summary,
        )

    finally:
        # 10. Runner Lock Release — docs/07 §37 step 12
        release_lock(runner_lock_path)

        # Run Manifest — written on EVERY exit path, including the aborting
        # ones. That is the property worth having: a run that dies in step 5
        # used to leave the question "what did the first four steps do?"
        # answerable only by reading logs, and `run_company_ops.py` printed
        # nothing at all because it never reached its reporting code.
        #
        # The step that raised is attributed to whichever component was in
        # flight (`recorder.current`) rather than guessed at, so a Backup
        # GitOperationError shows up as a FAILED backup component with a
        # classification — and the exception still propagates, unchanged.
        # Whether run_once() should absorb it instead remains BUG-4, and is
        # deliberately not decided here.
        if run_summary is None:
            in_flight = recorder.current
            if in_flight is not None:
                # …and *what* aborted it. The attribution above was added
                # because an unattributed abort read as success; the reason
                # stayed the constant string, so the manifest could say which
                # step died and never why.
                #
                # That gap is widest exactly where it costs most — the aborts
                # that repeat. Measured with a corrupted
                # `collector_state.json` (a truncated write, a partial
                # restore, a hand edit):
                #
                #     collector  FAILED  STEP_ABORTED
                #     reason     "the run aborted inside this step"
                #
                # `PersistentSeenEventStore._load()` raises a
                # `CollectorStateError` that names the file *and* the parse
                # position, and that sentence existed only in the scheduled
                # task's console output. Every following run dies the same
                # way, so Company History stops advancing while the one
                # artifact docs/14 §3 tells an operator to read says nothing
                # they can act on.
                #
                # `sys.exc_info()` rather than a new `except` clause: this
                # `finally` must not change what propagates, and Python
                # restores the in-flight exception after the inner handlers
                # here (`release_lock()`) return. `redact()` because an
                # aborting exception can carry a remote response body — the
                # same reason `oplog.append_line()` redacts, applied at the
                # one write point into the manifest that can reach one.
                # `one_line()` beside it for the sibling reason: the backup
                # gate a hundred lines up already guards the filenames it
                # puts in `reason`, and `run_company_ops.py` prints this
                # field as one indented row per component. Guarding at the
                # write point keeps that true for a reader that forgets to.
                #
                # The `is not None` arm is the only one that runs today and
                # is the only one tested: reaching here at all means
                # `run_summary` was never assigned, and the sole way out of
                # the `try` without assigning it is an exception, which is
                # exactly what `sys.exc_info()` then holds. The guard stays
                # because this is a `finally` — code here must not be the
                # thing that raises, and `_bounded_error(None)` would.
                aborted_by = sys.exc_info()[1]
                reason = "the run aborted inside this step"
                if aborted_by is not None:
                    reason = f"{reason}: {_one_line(_redact(_bounded_error(aborted_by)))}"
                recorder.failed(
                    in_flight,
                    classification="STEP_ABORTED",
                    reason=reason,
                    retryability=Retryability.UNKNOWN,
                    severity=_SEVERITY.get(in_flight, Severity.CRITICAL),
                )
            run_summary = RunSummary(
                run_id=resolved_manifest_run_id,
                started_at=now_iso(now),
                finished_at=now_iso(businessdate.now()),
                components=tuple(recorder.components),
            )
        write_summary(resolved_run_summary_path, run_summary)
