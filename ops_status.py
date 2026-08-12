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

import json
import os
import subprocess
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
from app.runner import DEFAULT_RUN_SUMMARY_PATH, PIPELINE_COMPONENTS  # noqa: E402
from backup.working_copy import scan_for_secrets  # noqa: E402
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
from scheduler.lock import (  # noqa: E402
    is_locked,
    lock_held_since,
    stale_lock_cannot_be_cleared,
)

PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"
AGENT_DIR = RUNTIME_DIR / "agent"

# A Desktop that is simply switched off for a weekend is normal in this
# deployment (docs/07 section 58), so silence is only worth flagging after
# more than a couple of days. A threshold that fires every Monday gets
# ignored, and an ignored alert is worse than none.
SILENT_AFTER_DAYS = 3

# How long a held Runner lock stops looking like work and starts looking
# like a lock nobody will ever release. Measured elsewhere in this project:
# a 20,000-Event batch reconciles in seconds, and the git subprocess timeout
# is 300 s, so a real run is orders of magnitude under this. Generous on
# purpose — the cost of firing early is telling an operator to interrupt a
# healthy long run.
LOCK_STUCK_AFTER_HOURS = 2


def _would_reach_the_commit(working_copy: Path, candidates: tuple[str, ...]) -> tuple[str, ...]:
    """Of `candidates`, the ones git would actually put in a commit.

    `scan_for_secrets()` answers "is this a secret-shaped filename", which
    is the right question for the Backup gate and the wrong one for this
    report. What reaches the remote is what `git add -A` stages, and git
    ignores whatever `.gitignore` tells it to — including the very entries
    docs/08 §28 asks a Backup Repo to carry (`.env`, `.env.*`, `*.tmp`,
    `*.log`).

    Without this the report was a standing false alarm for a *correctly
    configured* Working Copy: measured, an operator who added §28's
    `.gitignore` still saw "이 파일들은 ... 원격에 올라간다" on every run,
    for a file git was correctly refusing to commit. That is the
    alert-that-cannot-clear this project keeps warning about
    (`IntakeBacklog`'s own docstring), introduced by the C24 check itself.

    git is asked rather than second-guessed. `ls-files -c -o
    --exclude-standard` lists exactly what is tracked plus what is untracked
    and not ignored — the set `add -A` would end up with. Parsing
    `.gitignore` here instead would be a second opinion about git's rules,
    which is the disagreement this codebase closes elsewhere by reusing the
    authority (`_count_transport` reuses intake's own parse test).

    Fail-safe: any failure — no git, not a repository, a timeout — returns
    the candidates unchanged, so a broken probe over-reports rather than
    hiding a real exposure. Only run when there is something to check, so a
    clean Working Copy costs no subprocess at all.
    """
    if not candidates:
        return ()
    try:
        result = subprocess.run(
            ["git", "ls-files", "-c", "-o", "--exclude-standard"],
            cwd=working_copy,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return candidates
    if result.returncode != 0 or result.stdout is None:
        return candidates
    listed = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    return tuple(
        name for name in candidates if name.replace("\\", "/") in listed
    )


def _split_reviewed(review_dir: Path) -> tuple[int, int]:
    """(not yet reviewed, already reviewed) in `review/`.

    "Reviewed" means a human has written at least one Decision Context
    field — exactly what `RepositoryHistoryReviewer.submit_review()` does,
    read back from the stored candidate rather than tracked separately.

    The split exists so the alert can clear. A candidate nobody has looked
    at is work waiting for a person, and doing that work removes it from the
    count. A candidate that HAS been reviewed is still in `review/` — there
    is no promotion path (BACKLOG E-20) — and alerting on it would stand
    forever no matter what the operator did.

    Unreadable files count as not-yet-reviewed: they need a person either
    way, and a diagnostic must answer when part of the evidence is damaged.
    `FileHistoryRepository.list()` is deliberately not used — it raises on
    the first unreadable candidate (BUG-38), which would take the whole
    status view down.
    """
    if not review_dir.is_dir():
        return (0, 0)
    waiting = reviewed = 0
    for path in sorted(review_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            waiting += 1
            continue
        if isinstance(data, dict) and any(
            data.get(field) is not None
            for field in (
                "decision_context",
                "expected_outcome",
                "actual_outcome",
                "lessons_learned",
            )
        ):
            reviewed += 1
        else:
            waiting += 1
    return (waiting, reviewed)


def _runner_lock_path() -> Path:
    """Resolved per call, not at import: `RUNTIME_DIR` is rebound by tests
    (and would be by any future relocation), and a path frozen at import
    would keep pointing at the old one."""
    return RUNTIME_DIR / "locks" / "company_ops.lock"


def _agent_start_date() -> date | None:
    raw = os.environ.get("COMPANY_OPS_AGENT_START_DATE")
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _source_note(*breakdowns) -> str:
    """` (DESKTOP_1=2 unattributed=1)`, or empty when nothing is attributed.

    Merged across the breakdowns given so one ATTENTION sentence covering
    two piles names each Desktop once rather than twice. Empty rather than
    "(unknown)" when there is nothing to say — a parenthetical that always
    appears is one an operator stops reading.
    """
    merged: dict[str, int] = {}
    unattributed = 0
    for breakdown in breakdowns:
        for source, count in breakdown.by_source:
            merged[source] = merged.get(source, 0) + count
        unattributed += breakdown.unattributed

    parts = [f"{source}={count}" for source, count in sorted(merged.items())]
    if unattributed:
        parts.append(f"출처불명={unattributed}")
    return f" ({' '.join(parts)})" if parts else ""


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
        + (f" future_dated={backlog.future_dated}" if backlog.future_dated else "")
        + (f" name_collision={backlog.name_collision}" if backlog.name_collision else "")
    )
    # Who each pile came from (BACKLOG E-10). The counts above are the
    # authority and are unchanged; these lines only answer "which Desktop",
    # which previously required opening runtime/events/ by hand — and that
    # is the difference between one Desktop misbehaving and every Desktop
    # hitting the same problem, two situations needing opposite reactions.
    for label, breakdown in (
        ("transport", backlog.awaiting_intake_sources),
        ("incoming", backlog.awaiting_collection_sources),
        ("rejected", backlog.rejected_sources),
    ):
        if breakdown.total:
            print(f"           {label:<10} {breakdown.describe()}")

    if not backlog.is_clear:
        # `future_dated` is appended to the same sentence rather than given
        # its own: without it "transport=1" stands forever with no reason,
        # which is the standing-alert-with-no-explanation shape the
        # `unparseable` split was created to remove (see `IntakeBacklog`).
        reasons = []
        if backlog.future_dated:
            reasons.append(
                f"{backlog.future_dated}건은 파일 시각이 이 머신의 시계보다 앞서 "
                f"있어 시계가 따라잡을 때까지 수집되지 않는다 "
                f"(보낸 Desktop의 시계 확인 필요)"
            )
        if backlog.name_collision:
            reasons.append(
                f"{backlog.name_collision}건은 같은 이름이 이미 processed/ 또는 "
                f"rejected/에 있어 매 실행 실패한다 — 재실행으로 해결되지 않는다"
                f"(BACKLOG BUG-43)"
            )
        stalled = f" — 그중 {'; 그중 '.join(reasons)}" if reasons else ""
        attention.append(
            f"수집되지 않고 남은 Event: transport={backlog.awaiting_intake} "
            f"incoming={backlog.awaiting_collection}"
            + _source_note(backlog.awaiting_intake_sources, backlog.awaiting_collection_sources)
            + stalled
        )
    if backlog.rejected:
        attention.append(
            f"Collector가 거부한 Event {backlog.rejected}건"
            + _source_note(backlog.rejected_sources)
            + " — 사람이 확인해야 한다"
        )
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

    # Candidates parked for a human. `HistoryFilter` sends BLOCKED /
    # COMPLETED / CANCELLED here (docs/05 §24), and nothing in the pipeline
    # renders them: `generate_daily_history()` reads only KEEP, and
    # `submit_review()` fills Decision Context without touching
    # `filter_result`, so a reviewed candidate stays REVIEW. Measured — a
    # COMPLETED Event was still absent from its Daily file after a review
    # and two further runs.
    #
    # docs/05 §50 makes the count itself the point: "REVIEW가 너무 많다 ->
    # 자동화 실패 신호", and "COO가 매일 수십 개의 REVIEW를 수동 처리해야
    # 하는 구조를 만들지 않는다". That signal had no reader — this was the
    # one pile of human-owned work with no counter, while `rejected/`,
    # `signals_rejected/` and orphaned Events all had one (BACKLOG E-20).
    # Split by whether a human has actually been through them. The pile as
    # a whole is not an actionable alert — E-20's decision is what would
    # empty it, and nothing an operator does today can. What IS actionable
    # is the part nobody has looked at yet, and that part clears the moment
    # they do.
    #
    # C22 alerted on the whole pile, so running `review_cli.py` — the
    # documented correct action — left the warning standing forever
    # (`submit_review()` fills Decision Context without touching
    # `filter_result`, so the file never leaves `review/`). Measured. That
    # is the alert-that-cannot-clear this project keeps warning about, and
    # C26 found the same shape in the Working Copy check.
    review_dir = RUNTIME_DIR / "history_candidates" / "review"
    review_waiting, review_done = _split_reviewed(review_dir)
    review_count = review_waiting + review_done

    print("HISTORY — Company Repository")
    print("-" * 60)
    print(f"  daily 파일          : {daily_count}")
    print(f"  monthly 파일        : {len(monthly_files)}")
    if monthly_files:
        print(f"                        {', '.join(monthly_files[-6:])}")
    print(
        f"  검토 대기 Candidate : {review_count}"
        + (f" (미검토 {review_waiting} / 검토됨 {review_done})" if review_done else "")
    )
    if review_waiting:
        attention.append(
            f"사람 검토를 기다리는 History Candidate {review_waiting}건 "
            f"(runtime/history_candidates/review/) — BLOCKED/COMPLETED/CANCELLED는 "
            f"자동 규칙으로 판정하지 않는다(docs/05 §24). 이 건들은 아직 Company "
            f"History에 없고 어떤 실행도 넣지 않는다(BACKLOG E-20)"
        )

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
        # `find_orphaned_events()` is a pure function and knows nothing about
        # the lock, but the pipeline it inspects has a window where a
        # perfectly healthy Event looks orphaned: Collector moves the WHOLE
        # batch into `processed/` (step 4) before the History Filter loop
        # starts writing Candidates one at a time (step 5). Run this view
        # during a large catch-up — the usage `ops_status.py` explicitly
        # promises is safe — and Events whose Candidate turn has simply not
        # come yet are reported as permanently lost.
        #
        # The list is NOT filtered or suppressed: a real loss hidden behind
        # "probably just running" is far worse than a false alarm, and this
        # cannot tell the two apart. A sentence is added, nothing is removed.
        running = " (Runner 실행 중 — 완료 후 재확인 권장)" if is_locked(_runner_lock_path()) else ""
        attention.append(
            f"수집됐지만 History에 들어가지 못한 Event {len(reconciliation.orphaned)}건: "
            f"{', '.join(o.event_id for o in reconciliation.orphaned[:5])}"
            f"{' 외' if len(reconciliation.orphaned) > 5 else ''} — 재실행으로 "
            f"복구되지 않는다(BACKLOG A-20). 사람이 확인해야 한다" + running
        )
    if reconciliation.unreadable:
        attention.append(
            f"processed에 읽을 수 없는 Event {len(reconciliation.unreadable)}건 — "
            f"History 반영 여부를 판단할 수 없다"
        )

    # Secret-shaped files sitting in the Backup Working Copy (E-21).
    #
    # `backup.run_once()` scans **Local Master**, but `git add -A` commits
    # the **Working Copy** — so a file that reached the Working Copy by any
    # route other than sync is ungated and gets pushed. Measured: a `.env`
    # and an `id_rsa` placed there went to the remote while the backup
    # reported BACKUP_SUCCESS.
    #
    # This changes no gate: `scan_for_secrets()` is applied here exactly as
    # it is applied to Master, with the same decided list of names, to a
    # directory nobody was looking at. Reporting is late by construction —
    # a scheduled Backup may already have pushed — but late is the
    # difference between rotating a leaked credential and never knowing.
    # Choosing what the gate guards is the decision E-15/E-21 record.
    working_copy = RUNTIME_DIR / "backup_working_copy"
    if working_copy.is_dir():
        exposed = _would_reach_the_commit(
            working_copy, scan_for_secrets(working_copy)
        )
        if exposed:
            attention.append(
                f"Backup Working Copy에 Secret 형태의 파일 {len(exposed)}건: "
                f"{', '.join(exposed[:5])}"
                f"{' 외' if len(exposed) > 5 else ''} — Backup은 Local Master만 "
                f"검사하고 git은 이 파일들을 무시하지 않으므로 `git add -A`로 "
                f"원격에 올라간다(BACKLOG E-21). 이미 push됐다면 자격증명 교체가 "
                f"필요하다"
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

    # A lock still held long after any real run could have finished.
    #
    # `_is_process_running()` asks whether *a* process has the recorded pid,
    # not whether it is the one that wrote the lock. After a power cut the
    # dead Runner's pid stays in the file; once Windows reassigns that
    # number, every subsequent run is denied the lock and skips — silently,
    # forever, until someone deletes the file by hand. Making the identity
    # check exact means widening the lock file's pinned on-disk contract,
    # which is a decision and stays in BACKLOG.
    #
    # This decides nothing and takes nothing: it reports that a lock has
    # been held longer than plausible. A genuinely long run and a
    # pid-reuse ghost both deserve the same sentence — go and look.
    # A stale lock nothing can remove. `lock_held_since()` below only sees a
    # lock a LIVE process holds, so this condition — dead process, file not
    # writable — is invisible to it, and `try_acquire_lock()` reports it as
    # ordinary contention. The Runner then skips on schedule forever while
    # every automatic signal reads healthy (BUG-42 / BACKLOG F-1).
    if stale_lock_cannot_be_cleared(_runner_lock_path()):
        print("  Runner Lock : 남아 있으나 제거할 수 없음 (읽기 전용)")
        attention.append(
            f"Runner Lock 파일이 남아 있고 제거할 수 없다 ({_runner_lock_path()}) — "
            f"기록된 프로세스는 이미 종료됐지만 파일이 읽기 전용이라 어떤 실행도 "
            f"인수하지 못한다. 모든 실행이 '다른 Runner 실행 중'으로 조용히 "
            f"건너뛰어진다. 사람이 파일을 확인해 지워야 한다"
        )

    held_since = lock_held_since(_runner_lock_path())
    if held_since is not None:
        reference = datetime.now().astimezone()
        if held_since.tzinfo is None:
            reference = reference.replace(tzinfo=None)
        held_hours = (reference - held_since).total_seconds() / 3600
        print(f"  Runner Lock : 보유 중 (획득 {held_since.isoformat(timespec='seconds')})")
        if held_hours >= LOCK_STUCK_AFTER_HOURS:
            attention.append(
                f"Runner Lock이 {held_hours:.1f}시간째 잡혀 있다 (획득 "
                f"{held_since.isoformat(timespec='seconds')}) — 실제로 긴 실행이 "
                f"진행 중이거나, 죽은 Runner의 PID가 재사용돼 Lock이 영구히 "
                f"잡힌 것으로 보이는 상태다. 확인이 필요하다"
            )

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

    # Steps that never started. This loop only ever walked the components
    # that ARE in the manifest, so a run that aborted in Backup — taking
    # Dashboard with it, since the exception propagates out of `run_once()`
    # before `recorder.begin(C_DASHBOARD)` — showed eight components and
    # said nothing about the ninth. An operator reading LAST RUN saw a
    # Backup failure and no reason to think anything else had been missed,
    # while that run's Dashboard row was gone for good and not even queued
    # for retry (BACKLOG A-18).
    #
    # "Never started" is deliberately its own word rather than SKIPPED:
    # SKIPPED means the Runner reached the step and chose not to run it (no
    # Notion configured, say), which is fine. This means the step was never
    # reached, which is not.
    recorded = {component.name for component in summary.components}
    never_started = [name for name in PIPELINE_COMPONENTS if name not in recorded]
    if never_started:
        print(f"  ! 시작되지 못한 단계: {', '.join(never_started)}")
        attention.append(
            f"마지막 실행에서 시작조차 되지 못한 단계: {', '.join(never_started)} — "
            f"앞 단계가 중단시켰다. 그 단계의 결과는 이번 실행에서 기록되지 않았고 "
            f"자동으로 재시도되지도 않는다"
        )

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
