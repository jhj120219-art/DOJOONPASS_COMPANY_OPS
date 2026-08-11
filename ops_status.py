"""Company Ops Status — read-only. Prints, changes nothing.

    python ops_status.py

Three views, all built only from files that already exist:

    COMPANY   what Desktop 4 knows about every Desktop, from the Events it
              has collected (src/app/desktop_activity.py)
    HISTORY   where the Company Repository stands — Daily count, Monthly
              whether a month is waiting to be consolidated or rebuilt
    AGENT     this machine's own Agent, if one is configured here
              (src/agent/status.py)

Run it on Desktop 4 and all three appear. Run it on Desktop 1/2/3 and the
COMPANY and HISTORY views are empty (that machine collects and consolidates
nothing) while the AGENT view answers "is my own delivery stuck?".

Why this exists
---------------
An Agent's success or failure was recorded only on its own machine. The COO
seat, which is where someone would act on "Desktop 1 has not delivered for
three days", could not see it. This does not add a heartbeat or any new
Event — it reads the Events already collected and the Agent state already
written. See src/app/desktop_activity.py on why silence is reported rather
than diagnosed.

Nothing here acquires a lock, so it is safe to run while a Runner or Agent
is working. Nothing here writes, so it is safe to run at any time.

Exit codes:
    0   nothing needs a person
    1   configuration error
    3   at least one thing needs a person (see the ATTENTION section)
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime
from pathlib import Path

# `line_buffering=True` keeps this script's own output in the order it was
# written. Python block-buffers stdout when it is not a terminal and leaves
# stderr unbuffered, so under the redirection a scheduled task actually uses
# (`>log 2>&1`) every stderr line overtakes the stdout lines around it.
# Measured: a failure message printed above the context line explaining it.
# That is the one reading an operator gets from a captured log, and it makes
# a report look like it describes the wrong thing.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from agent.delivery import find_undelivered_events  # noqa: E402
from agent.status import read_status  # noqa: E402
from app.desktop_activity import read_company_activity  # noqa: E402
from app.runner import DEFAULT_RUN_SUMMARY_PATH  # noqa: E402
from history.reconciliation import find_orphaned_events  # noqa: E402
from monthly import MonthlyStateError  # noqa: E402
from monthly import load_state as load_monthly_state  # noqa: E402
from runsummary import (  # noqa: E402
    ComponentStatus,
    OverallStatus,
    Retryability,
    RunSummaryError,
    read_summary,
)
from scheduler.consistency import (  # noqa: E402
    ConsistencyStatus,
    check_state_consistency,
)

PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"
AGENT_DIR = RUNTIME_DIR / "agent"

# A Desktop that is simply switched off for a weekend is normal in this
# deployment (docs/07 section 58), so silence is only worth flagging after
# more than a couple of days. A threshold that fires every Monday gets
# ignored, and an ignored alert is worse than none.
SILENT_AFTER_DAYS = 3


def _agent_start_date() -> date | None:
    raw = os.environ.get("COMPANY_OPS_AGENT_START_DATE")
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _print_company(now: datetime) -> list[str]:
    snapshot = read_company_activity(
        processed_dir=RUNTIME_DIR / "events" / "processed",
        transport_dir=RUNTIME_DIR / "events" / "transport",
        incoming_dir=RUNTIME_DIR / "events" / "incoming",
        rejected_dir=RUNTIME_DIR / "events" / "rejected",
    )
    attention: list[str] = []

    print("COMPANY — Desktop 4가 수집한 Event 기준")
    print("-" * 60)
    for activity in snapshot.desktops:
        if not activity.has_ever_reported:
            print(f"  {activity.source:<11} 수집된 Event 없음")
            continue
        silent = activity.days_silent(now)
        arrival = activity.days_since_arrival(now)
        roles = "/".join(activity.roles) or "-"
        arrival_note = "" if arrival is None else f" 도착 {arrival}일 전"
        print(
            f"  {activity.source:<11} events={activity.event_count:<6} "
            f"role={roles:<14} 작업일 {silent}일 전{arrival_note}"
        )

    silent_sources = snapshot.silent_for(now, days=SILENT_AFTER_DAYS)
    if silent_sources:
        # Split the silent Desktops by whether anything arrived recently.
        # Both groups are still reported — the arrival time narrows the
        # explanation, it never clears the flag.
        caught_up = [
            s
            for s in silent_sources
            if snapshot.for_source(s).caught_up_recently(now, days=SILENT_AFTER_DAYS)
        ]
        truly_quiet = [s for s in silent_sources if s not in caught_up]

        if truly_quiet:
            attention.append(
                f"{SILENT_AFTER_DAYS}일 이상 아무것도 오지 않은 Desktop: "
                f"{', '.join(truly_quiet)} (꺼져 있거나, 보고할 일이 없었거나, "
                f"Agent가 멈췄다 — 현재 데이터로는 여기까지만 말할 수 있다)"
            )
        if caught_up:
            attention.append(
                f"작업일은 {SILENT_AFTER_DAYS}일 이상 지났지만 최근 파일이 도착한 "
                f"Desktop: {', '.join(caught_up)} (꺼져 있다가 밀린 분을 보낸 것으로 "
                f"보인다 — Agent는 살아 있다)"
            )

    backlog = snapshot.backlog
    print()
    print(
        f"  backlog: transport={backlog.awaiting_intake} "
        f"incoming={backlog.awaiting_collection} rejected={backlog.rejected}"
        + (f" unparseable={backlog.unparseable}" if backlog.unparseable else "")
    )
    if not backlog.is_clear:
        attention.append(
            f"수집되지 않고 남은 Event: transport={backlog.awaiting_intake} "
            f"incoming={backlog.awaiting_collection}"
        )
    if backlog.rejected:
        attention.append(f"Collector가 거부한 Event {backlog.rejected}건 — 사람이 확인해야 한다")
    if backlog.unparseable:
        # Reported for the right reason. These used to be counted as
        # "awaiting intake", which said an Event was queued for collection
        # when in fact it had been judged unparseable and would never be
        # collected — a standing alert no run could clear.
        attention.append(
            f"transport에 읽을 수 없는 파일 {backlog.unparseable}건 — 수집되지 않으며 "
            f"다음 실행에서도 그대로다. 사람이 확인해 옮기거나 지워야 한다"
        )
    if snapshot.unreadable_events:
        attention.append(
            f"읽을 수 없는 processed Event {len(snapshot.unreadable_events)}건: "
            f"{', '.join(snapshot.unreadable_events[:5])}"
        )
    return attention


def _print_history(now: datetime) -> list[str]:
    """Where Company History actually stands, from the state files.

    Reads `monthly_history_state.json` and the Company Repository directories —
    no new state, no new policy. Answers the two questions the COO would
    otherwise have to answer by listing directories: is last month written,
    and is anything waiting to be rebuilt.
    """
    attention: list[str] = []
    local_master = RUNTIME_DIR / "local_master"
    daily_dir = local_master / "daily"
    monthly_dir = local_master / "monthly"

    daily_count = len(list(daily_dir.glob("*.md"))) if daily_dir.is_dir() else 0
    monthly_files = sorted(p.stem for p in monthly_dir.glob("*.md")) if monthly_dir.is_dir() else []

    print("HISTORY — Company Repository")
    print("-" * 60)
    print(f"  daily 파일          : {daily_count}")
    print(f"  monthly 파일        : {len(monthly_files)}")
    if monthly_files:
        print(f"                        {', '.join(monthly_files[-6:])}")

    try:
        state = load_monthly_state(RUNTIME_DIR / "state" / "monthly_history_state.json")
    except MonthlyStateError as exc:
        print("  monthly state       : 읽을 수 없음")
        attention.append(f"monthly state 파일이 손상됨: {exc}")
        return attention

    # docs/10 §48: "Runner 시작 시 최소 확인 가능: State Last Success ->
    # Corresponding Local History 존재?". `scheduler/consistency.py`
    # implements exactly that check and is fully tested, but nothing ever
    # called it — a corruption detector that never runs detects nothing.
    #
    # Reported here rather than wired into the Runner on purpose. That module
    # refuses to enter Scheduler's control flow because deciding what to *do*
    # about an inconsistency is an operator call (§49 "History가 State보다
    # 우선", §64 "COO가 개입해야 하는 상황"). A read-only status view is the
    # one place that reports without deciding.
    consistency = check_state_consistency(
        RUNTIME_DIR / "state" / "daily_history_state.json", daily_dir
    )
    print(f"  daily state 정합성  : {consistency.status.value}")
    if consistency.status is ConsistencyStatus.STATE_INCONSISTENCY:
        attention.append(
            f"Daily State와 실제 History가 어긋난다: {consistency.detail}"
        )
    elif consistency.status is ConsistencyStatus.STATE_UNREADABLE:
        attention.append(f"Daily State를 읽을 수 없다: {consistency.detail}")

    # BACKLOG A-20: an Event consumed by the Collector whose History
    # Candidate was never written is lost from Company History permanently —
    # the event_id is already marked seen, so no later run reconsiders it.
    # The Run Manifest reports that a run failed and which component
    # aborted, but names no Event; this answers "which one".
    #
    # Reported, never repaired — the same restraint as the consistency check
    # above, for the same reason. Re-processing an orphan would be deciding
    # A-20's open question by implementation.
    reconciliation = find_orphaned_events(
        processed_dir=RUNTIME_DIR / "events" / "processed",
        keep_dir=RUNTIME_DIR / "history_candidates" / "keep",
        review_dir=RUNTIME_DIR / "history_candidates" / "review",
    )
    print(
        f"  Candidate 정합성    : "
        f"{'OK' if reconciliation.is_clean else 'ORPHANED_EVENT'} "
        f"(Event {reconciliation.checked}건 확인)"
    )
    if reconciliation.orphaned:
        for orphan in reconciliation.orphaned[:5]:
            print(f"                        ! {orphan.event_id} [{orphan.decision.value}]")
        attention.append(
            f"수집됐지만 History에 들어가지 못한 Event {len(reconciliation.orphaned)}건: "
            f"{', '.join(o.event_id for o in reconciliation.orphaned[:5])}"
            f"{' 외' if len(reconciliation.orphaned) > 5 else ''} — 재실행으로 "
            f"복구되지 않는다(BACKLOG A-20). 사람이 확인해야 한다"
        )
    if reconciliation.unreadable:
        attention.append(
            f"processed에 읽을 수 없는 Event {len(reconciliation.unreadable)}건 — "
            f"History 반영 여부를 판단할 수 없다"
        )

    print(f"  마지막 통합한 달    : {state.last_successful_monthly_close}")
    if state.dirty_months:
        print(f"  재생성 대기         : {', '.join(state.dirty_months)}")
        attention.append(
            f"Late Event로 다시 만들어야 할 달: {', '.join(state.dirty_months)} "
            f"(다음 Runner 실행에서 자동 처리된다)"
        )

    # A month that closed but was never consolidated is the one case worth
    # flagging: it means Daily Coverage never became COMPLETE for it.
    last_closed = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
    last_closed_key = f"{last_closed[0]:04d}-{last_closed[1]:02d}"
    if state.last_successful_monthly_close is None:
        if monthly_files:
            attention.append(
                "monthly 파일은 있는데 state에는 통합 기록이 없다 — state 파일 확인 필요"
            )
    elif state.last_successful_monthly_close < last_closed_key:
        attention.append(
            f"{last_closed_key} Monthly가 아직 없다 (마지막 통합: "
            f"{state.last_successful_monthly_close}) — 그 달 Daily가 아직 "
            f"완전하지 않다는 뜻이다"
        )

    return attention


def _print_agent(now: datetime) -> list[str]:
    if not AGENT_DIR.exists():
        print("AGENT — 이 머신에는 Agent가 설정되어 있지 않다 (runtime/agent 없음)")
        return []

    snapshot = read_status(
        agent_start_date=_agent_start_date(),
        now=now,
        state_path=AGENT_DIR / "state" / "agent_state.json",
        outbox_dir=AGENT_DIR / "outbox",
        sent_dir=AGENT_DIR / "sent",
        rejected_signals_dir=AGENT_DIR / "signals_rejected",
    )

    print("AGENT — 이 머신의 Agent")
    print("-" * 60)
    print(f"  desktop_id          : {snapshot.desktop_id}")
    print(f"  last_run            : {snapshot.last_run}")
    print(f"  마지막 수집 날짜    : {snapshot.last_successful_collection_date}")
    print(f"  미수집 날짜         : {len(snapshot.pending_dates)}")
    if snapshot.pending_dates:
        shown = ", ".join(d.isoformat() for d in snapshot.pending_dates[:7])
        more = " ..." if len(snapshot.pending_dates) > 7 else ""
        print(f"                        {shown}{more}")
    print(f"  outbox (미전송)     : {snapshot.outbox_count}")
    print(f"  sent (전송 완료)    : {snapshot.sent_count}")
    print(f"  거부된 Signal       : {snapshot.rejected_signal_count}")

    # BACKLOG E-9/E-9b: `sent/` records that `transport.send()` did not
    # raise — not that the Event reached the sync folder in readable form.
    # `send()` skips writing when the destination already exists in ANY
    # shape, and the OneDrive client produces such shapes on its own
    # (Files On-Demand placeholders are 0 bytes, interrupted transfers
    # truncate). Measured: an Event filed as sent while the destination
    # stayed 0 bytes, with the collection date advanced past it and no
    # warning anywhere.
    #
    # A MISSING destination is not reported — that is what a normally
    # consumed Event looks like. Only a destination that is present and is
    # not the Event is reportable, and all four such shapes were measured
    # to be distinguishable from both clean cases.
    #
    # Reported, never re-sent: re-sending means overwriting a sync-folder
    # entry, which is exactly the race E-9 is blocked on.
    delivery_attention: list[str] = []
    sync_folder = os.environ.get("COMPANY_OPS_AGENT_SYNC_FOLDER")
    if sync_folder:
        delivery = find_undelivered_events(
            sent_dir=AGENT_DIR / "sent", sync_folder=Path(sync_folder)
        )
        print(
            f"  전달 정합성         : "
            f"{'OK' if delivery.is_clean else 'UNDELIVERED'} "
            f"(확인 {delivery.checked}건, 이미 수거됨 {delivery.absent}건)"
        )
        for item in delivery.undelivered[:5]:
            print(f"                        ! {item.event_id} [{item.problem}]")
        if delivery.undelivered:
            delivery_attention.append(
                f"전송 완료로 기록됐지만 sync 폴더에 도착하지 않은 Event "
                f"{len(delivery.undelivered)}건: "
                f"{', '.join(i.event_id for i in delivery.undelivered[:5])} — "
                f"자동 재전송되지 않는다(BACKLOG E-9). 사람이 확인해야 한다"
            )
    else:
        print("  전달 정합성         : 확인 불가 (COMPANY_OPS_AGENT_SYNC_FOLDER 미설정)")
    if snapshot.pending_dates and _agent_start_date() is None:
        print("  (COMPANY_OPS_AGENT_START_DATE 미설정 — 미수집 날짜는 계산되지 않음)")

    return list(snapshot.needs_attention(now)) + delivery_attention


def _print_last_run() -> list[str]:
    """The last Runner execution, from its Run Manifest.

    This is the view that did not exist. `ops_status.py` could describe the
    *state* of things — how many Daily files, which Desktop is quiet — but
    not what the last run actually did, because nothing recorded it. A run
    whose Notion Sync failed and whose History succeeded looked identical to
    a clean one from here.

    Read-only and never fatal: a missing manifest means "no run has been
    recorded yet", and a damaged one is reported rather than repaired
    (docs/10 §46 — the program never deletes it).
    """
    attention: list[str] = []
    print("LAST RUN — Run Manifest")
    print("-" * 60)

    try:
        summary = read_summary(DEFAULT_RUN_SUMMARY_PATH)
    except RunSummaryError as exc:
        print(f"  손상된 Run Manifest: {exc}")
        return [f"Run Manifest를 읽을 수 없다: {DEFAULT_RUN_SUMMARY_PATH}"]

    if summary is None:
        print("  아직 기록된 실행이 없다.")
        return attention

    print(f"  실행 시각   : {summary.started_at}")
    print(f"  종합 상태   : {summary.overall_status.value} (exit {summary.exit_code})")

    for component in summary.components:
        if component.status is ComponentStatus.SUCCESS:
            continue
        if component.status is ComponentStatus.SKIPPED:
            print(f"  - {component.name}: SKIPPED (미설정)")
            continue
        failure = component.failure
        print(
            f"  ! {component.name}: {failure.classification} "
            f"[{failure.severity.value}/{failure.retryability.value}]"
        )
        if component.artifact_refs:
            print(f"      evidence: {', '.join(component.artifact_refs)}")

    # Only a PERMANENT failure needs a person now. A RETRYABLE one is what
    # the next scheduled run is for, and listing it here would put a
    # standing item in ATTENTION that clears itself — the kind of alert
    # that trains people to ignore the section.
    for component in summary.failures():
        if component.failure.retryability is Retryability.PERMANENT:
            attention.append(
                f"{component.name}: {component.failure.classification} — "
                f"재시도로 해결되지 않는다"
            )
    if summary.overall_status is OverallStatus.FAILED and not attention:
        attention.append(
            f"마지막 실행이 FAILED로 끝났다 ({DEFAULT_RUN_SUMMARY_PATH.name} 참고)"
        )

    return attention


def main() -> int:
    now = datetime.now().astimezone()
    print(f"DOJOONPASS Company Ops — Status @ {now.isoformat(timespec='seconds')}")
    print()

    attention = _print_company(now)
    print()
    attention.extend(_print_history(now))
    print()
    attention.extend(_print_last_run())
    print()
    attention.extend(_print_agent(now))
    print()

    if not attention:
        print("ATTENTION — 없음. 사람이 지금 할 일은 없다.")
        return 0

    print("ATTENTION")
    print("-" * 60)
    for item in attention:
        print(f"  ! {item}")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
