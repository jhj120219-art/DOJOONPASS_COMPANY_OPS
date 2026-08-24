"""Company Ops Production Entrypoint (Runtime Stabilization Sprint, P2).

    python run_company_ops.py

A single, non-looping `app.runner.run_once()` call using this
repository's real `runtime/` paths (git-ignored), instead of the ad-hoc
Python snippets every prior real Runner invocation actually was. The
Runtime Validation Sprint that preceded this one found no such script
existed at all in this repository — that gap is exactly how the
Runner/Scheduler lock collision (docs/07 §25; fixed this Sprint via
`scheduler.run_once(..., already_locked=True)`) went unnoticed.

This script does not loop, sleep, or register with any OS scheduler
(Windows Task Scheduler, cron, ...) — same principle collector/runtime.py
and scheduler/scheduler.py already establish: how often to run this is an
operational decision made outside this code, not inside it (docs/11
DEPLOYMENT_RUNBOOK owns that decision).

Notion Sync is optional: if NOTION_API_TOKEN / NOTION_PROJECTS_DATABASE_ID
are not set, this script proceeds WITHOUT Notion Sync
(docs/04_NOTION_SYNC_SPEC.md's own contract: `notion_sync=None`이면 그
단계를 건너뛴다) rather than failing outright — Company History must keep
working even before Notion is configured (README RULE 9: "Data Safety가
Convenience보다 우선한다").

Prerequisites this script does NOT create for you (by design — these are
one-time operational setup, not something to silently automate):
    - `runtime/backup_working_copy/` must already be a git repository
      with a configured, pushable `origin` remote (src/backup/git_ops.py
      requires this; see docs/08_BACKUP_SPEC.md). Real production remote
      setup is still open (남은 Backlog).
    - Notion Workspace setup: docs/13_NOTION_ENVIRONMENT_SETUP.md.
"""

from __future__ import annotations

import os
import sys
from typing import Sequence
from datetime import date
from pathlib import Path

# This script's own status/error messages are Korean (operator-facing, per
# every doc in this repository). Windows' console defaults to the system's
# legacy codepage (e.g. cp949 for a Korean locale), not UTF-8 — under it,
# printing a line with a plain "-" would work, but a punctuation character
# outside that codepage's repertoire (e.g. the "—" this file already uses)
# raises UnicodeEncodeError on stdout and crashes the process outright,
# exactly on the "Notion not configured yet" path docs/11 §18 promises is
# safe and non-fatal ("Notion 때문에 전체 Deployment를 중단하지 않는다").
# Forcing UTF-8 here makes that guarantee hold regardless of the console's
# codepage, without touching what any message actually says.
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

from app.runner import PROJECT_ROOT as runner_project_root  # noqa: E402
from app.runner import run_once  # noqa: E402
from backup.git_ops import GitOperationError, is_authentication_failure  # noqa: E402
from notion import (  # noqa: E402
    ExecutionPlanSync,
    NotionClient,
    NotionConfig,
    NotionConfigError,
    RealNotionTransport,
)
from app.runner import DEFAULT_RUN_SUMMARY_PATH  # noqa: E402
from runsummary import RunSummaryError, read_summary  # noqa: E402
from scheduler import SchedulerStatus  # noqa: E402

# The same two rules `oplog.append_line()` applies to every logged line, for
# strings of the same origin. `ops_status.py` already imports `one_line` for
# this reason; the pair is needed here because this script prints text that
# came out of a remote HTTP response.
from oplog import one_line, redact  # noqa: E402
from cli import CONFIG_ERROR_EXIT, unexpected_arguments  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"


def _one_runtime_root_or_refuse() -> None:
    """Refuse to run half-redirected — C34 §3.

    `main()` derives three of `run_once()`'s **nineteen** path parameters
    from `RUNTIME_DIR`:

        local_master_dir  backup_working_copy_dir  runner_lock_path

    The other sixteen are left to defaults, and those defaults do not come
    from here. They come from six other modules' own `PROJECT_ROOT`
    constants — `app.runner`, `collector.runtime`, `scheduler.state`,
    `backup.state`, `history.file_repository`, `notion.retry_queue` — each
    frozen at import. So `RUNTIME_DIR` looks like the knob that points this
    script at a runtime tree, and it moves less than a sixth of one.

    In production the two roots are the same directory and nothing is
    wrong. The danger is the other case, and it is not hypothetical: this
    guard exists because rebinding `RUNTIME_DIR` — which is exactly how
    every test and probe in this repository isolates `ops_status.py` — ran
    a **real** pipeline that wrote Company History into a temp tree while
    advancing the *live* `daily_history_state.json` past it. Measured:

        daily/            six files, in a directory that no longer exists
        live pointer      2026-08-10 -> 2026-08-16
        consistency       CONSISTENT -> STATE_INCONSISTENCY
        six days of Company History that no future run will ever create,
        because the pointer is already past them

    A run that believes it is sandboxed and corrupts production instead is
    the worst shape a knob can have. C31 §10 recorded this same trap in
    `ops_status.py` (`AGENT_DIR` frozen at import while `RUNTIME_DIR` moved)
    and fixed it by deriving per call. That fix is not available here: the
    sixteen defaults belong to other modules, and re-deriving them would put
    a second — really a seventh — opinion about the layout in this file,
    which is what `ops_status._agent_dir()` explicitly argues against.

    So the incompleteness stays and stops being silent. In production this
    check always passes; the only way to fail it is to have rebound
    `RUNTIME_DIR`, which is precisely when the run must not proceed.
    """
    expected = runner_project_root / "runtime"
    if RUNTIME_DIR.resolve() == expected.resolve():
        return
    print(
        f"[FAILED] RUNTIME_DIR이 app.runner의 runtime 루트와 다릅니다.\n"
        f"           RUNTIME_DIR : {RUNTIME_DIR}\n"
        f"           app.runner  : {expected}\n"
        f"         이 스크립트는 19개 경로 중 3개만 RUNTIME_DIR에서 만들고 나머지 16개는\n"
        f"         각 모듈의 기본값(= app.runner 루트)을 씁니다. 두 루트가 다르면 실행이\n"
        f"         반으로 갈라져 Company History는 한쪽에, State는 다른 쪽에 쓰입니다 —\n"
        f"         실측된 결과는 영구 STATE_INCONSISTENCY입니다(BACKLOG C34 §3).\n"
        f"         격리된 실행이 필요하면 이 스크립트가 아니라 app.runner.run_once()에\n"
        f"         모든 경로를 명시적으로 넘기세요.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _build_notion_clients() -> tuple[ExecutionPlanSync | None, NotionClient | None]:
    """(Notion Sync, Operations Dashboard client). Either may be None.

    Notion Dashboard Production 연결 (CEO 승인 A안): the Dashboard client is
    built here, from configuration, exactly like the Sync client — previously
    nothing constructed it, so `app.runner.run_once()`'s `dashboard_client`
    parameter was permanently None in production and CEO Decision ④'s
    Operations Dashboard never recorded a single run.

    Both are optional and independent:
        no NOTION_API_TOKEN / NOTION_PROJECTS_DATABASE_ID -> no Sync, no Dashboard
        no NOTION_OPS_RUNS_DATABASE_ID                    -> Sync only
    """
    try:
        config = NotionConfig.from_env()
    except NotionConfigError as exc:
        # Print the actual reason rather than a fixed guess. The message
        # distinguishes "never set" from "set but blank", and those need
        # opposite reactions: the first is a normal pre-Notion deployment,
        # the second is a typo the operator can see in their own `.env` and
        # would otherwise be told is "없음" while looking straight at it.
        print(f"[INFO] Notion 미설정 — Notion Sync / Operations Dashboard 단계를 건너뜁니다: {exc}")
        return None, None

    transport = RealNotionTransport(api_token=config.api_token)
    notion_sync = ExecutionPlanSync(
        client=NotionClient(transport=transport, database_id=config.projects_database_id)
    )

    if not config.ops_runs_database_id:
        print(
            "[INFO] Operations Dashboard 미설정 — 해당 단계를 건너뜁니다 "
            "(NOTION_OPS_RUNS_DATABASE_ID 없음). Notion Sync는 정상 동작합니다."
        )
        return notion_sync, None

    dashboard_client = NotionClient(
        transport=transport, database_id=config.ops_runs_database_id
    )
    return notion_sync, dashboard_client


def _resolve_history_start_date() -> date:
    """docs/07 §50: history_start_date는 절대 추측하지 않는다 — 이미 State가
    있는 재실행이라면 이 값은 무시되지만(scheduler.py), 최초 1회 실행에는
    반드시 필요하므로 항상 명시적으로 요구한다.
    """
    raw = os.environ.get("COMPANY_OPS_HISTORY_START_DATE")
    if not raw:
        print(
            "[FAILED] COMPANY_OPS_HISTORY_START_DATE 환경변수가 없습니다. "
            "Company History를 언제부터 기록할지는 추측하지 않습니다(docs/07 §50) — "
            "YYYY-MM-DD 형식으로 설정하세요.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    try:
        return date.fromisoformat(raw)
    except ValueError:
        print(
            f"[FAILED] COMPANY_OPS_HISTORY_START_DATE 형식이 올바르지 않습니다: {raw!r} (YYYY-MM-DD 필요)",
            file=sys.stderr,
        )
        raise SystemExit(1)


def main(argv: Sequence[str] = ()) -> int:
    _one_runtime_root_or_refuse()
    # Stays the first statement, and `OneRuntimeRootOrRefuseTests` asserts
    # that by reading this source line. The argument refusal below writes
    # nothing and would be safe in either order, but a maintainer reading
    # that test should find the statement it names -- and a split runtime
    # root is the more serious of the two mistakes anyway.

    refusal = unexpected_arguments(
        argv,
        tool="run_company_ops.py",
        # The names this entrypoint actually reads — `os.environ.get()` here
        # for the first, `NotionConfig.from_env()` for the rest. Three of the
        # four it used to print did not exist: `COMPANY_OPS_NOTION_API_TOKEN`
        # and `COMPANY_OPS_NOTION_PROJECTS_DB` are misspellings of the two
        # this file's own module docstring names, and `COMPANY_OPS_RUNTIME_DIR`
        # is not a knob at all — `RUNTIME_DIR` is a constant, and the guard
        # below exists precisely because rebinding it is unsafe. So the
        # message told an operator to set a variable that would be ignored,
        # and pointed them at AGENT.md, which does not mention it either.
        configured_by=(
            "COMPANY_OPS_HISTORY_START_DATE",
            "NOTION_API_TOKEN",
            "NOTION_PROJECTS_DATABASE_ID",
            "NOTION_OPS_RUNS_DATABASE_ID",
        ),
    )
    if refusal is not None:
        print(f"[FAILED] {refusal}", file=sys.stderr)
        return CONFIG_ERROR_EXIT
    history_start_date = _resolve_history_start_date()
    notion_sync, dashboard_client = _build_notion_clients()

    local_master_dir = RUNTIME_DIR / "local_master"
    local_master_dir.mkdir(parents=True, exist_ok=True)

    # What the manifest said BEFORE this run. See `_exit_code_from_manifest()`
    # — an aborted run's exit code may only come from a manifest that this
    # run actually wrote.
    manifest_before = _manifest_run_id(DEFAULT_RUN_SUMMARY_PATH)

    try:
        result = run_once(
            local_master_dir=local_master_dir,
            backup_working_copy_dir=RUNTIME_DIR / "backup_working_copy",
            history_start_date=history_start_date,
            runner_lock_path=RUNTIME_DIR / "locks" / "company_ops.lock",
            notion_sync=notion_sync,
            dashboard_client=dashboard_client,
        )
    except GitOperationError as exc:
        # docs/08 §19 calls a failed push a routine, recoverable condition:
        # BACKUP_PENDING, retried by the next Runner. `app.runner.run_once()`
        # nevertheless lets it propagate (a known, characterized gap — the
        # Runner's return tuple has no shape for "Backup failed", and
        # inventing one is a contract decision this script may not take).
        #
        # What this script CAN fix is what the operator sees. A raw Python
        # traceback for an expected condition reads like the system broke,
        # when in fact Backup runs last and everything before it is already
        # durable on disk.
        return _report_backup_failure(
            exc, DEFAULT_RUN_SUMMARY_PATH, superseding=manifest_before
        )
    except Exception as exc:  # noqa: BLE001 — see below
        # Every OTHER way `run_once()` can abort, and the reason this clause
        # exists is the one `_report_backup_failure()` already wrote down:
        # *"Two answers to 'how bad was this run' is one too many, and the
        # scheduled task only ever sees this one."*
        #
        # That fix was applied to `GitOperationError` and to nothing else.
        # `run_once()` writes the manifest in its `finally`, so an aborted
        # run classifies itself correctly — and then the exception kept
        # travelling, out of `main()`, out of `raise SystemExit(main(...))`,
        # and Python exited **1**.
        #
        # docs/14 section 4 does not leave 1 free for that:
        #
        #     SUCCESS 0 | DEGRADED 3 | FAILED 2
        #     "1 is for configuration errors only (the run never started)"
        #
        # Measured as a real process, isolated copy of this entrypoint:
        #
        #     ordinary run                  process 0   manifest SUCCESS/0
        #     duplicate Candidate (abort)   process 1   manifest FAILED/2
        #     corrupt scheduler state       process 1   manifest FAILED/2
        #
        # Both aborted runs had started, taken the lock, collected an Event
        # and written a manifest. Task Scheduler's Last Run Result — the
        # only signal an unattended deployment has (BACKLOG F-7) — read
        # "configuration error, the run never started" for a CRITICAL
        # failure mid-pipeline.
        #
        # The traceback is NOT swallowed: it is printed first, because it
        # names the file and the line and nothing else does. What changes is
        # the number the process leaves behind.
        import traceback

        print(f"[FAILED] {redact(one_line(exc))}", file=sys.stderr)
        print(file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        print(
            "\n이 실행은 시작된 뒤 중단됐습니다. Run Manifest가 어느 단계에서\n"
            "멈췄는지를 기록했습니다 — `python ops_status.py`로 확인하세요.",
            file=sys.stderr,
        )
        return _exit_code_from_manifest(
            DEFAULT_RUN_SUMMARY_PATH, superseding=manifest_before
        )

    return _print_result(result)


def _manifest_run_id(run_summary_path: "Path | None") -> "str | None":
    """The `run_id` recorded at `run_summary_path` right now, or None.

    Unreadable and absent collapse to None on purpose: both mean "there is no
    identity here to compare against", and the caller treats that the same
    way — by not trusting the file.
    """
    if run_summary_path is None:
        return None
    try:
        summary = read_summary(run_summary_path)
    except RunSummaryError:
        return None
    return summary.run_id if summary is not None else None


def _exit_code_from_manifest(
    run_summary_path: "Path | None", *, superseding: "str | None" = None
) -> int:
    """The exit code this run already classified for itself.

    Extracted from `_report_backup_failure()` rather than restated: that
    function reached this answer first, for one exception type, and a second
    copy of the rule is how the two would come to disagree. Its own comment
    is the argument for reading the manifest instead of hardcoding, and it
    applies unchanged to every other abort.

    Falls back to 2 for the same stated reason: an abort with no readable
    manifest is genuinely unclassified, and 2 is the conservative reading of
    an unclassified failure. Never 1 — docs/14 section 4 reserves that for a
    run that never started, and by the time anything here is reached, it did.

    **`superseding` is C82, and it is the fix for a defect C78 introduced.**
    `run_once()` writes the manifest in its `finally` — but that `finally`
    belongs to a `try:` that begins *after* the lock is acquired. A run that
    dies before it (an unusable `locks/` directory is the reachable case)
    writes nothing, and the file on disk still describes the **previous**
    run. Measured, isolated tree, real subprocess:

        run 1 (clean)        process 0   manifest SUCCESS/0
        run 2 (dies early)   process 0   manifest SUCCESS/0   <- run 1's

    A crashed run reporting success to Task Scheduler, which is worse than
    the wrong-but-loud 1 it did before C78. `_report_backup_failure()`'s own
    docstring records the same shape from a different cause — "a test
    calling it directly picked up the repository's own live manifest, which
    said SUCCESS, and got exit 0 for a Backup failure" — and its fix
    (passing the path in) does nothing about staleness.

    So the caller passes the `run_id` the manifest carried before the run
    started, and a manifest still carrying it is not this run's. Identity,
    not a timestamp comparison: two runs in the same second would collide on
    a clock, and the direction that collision breaks in matters. Here it
    breaks toward 2 — the conservative answer — rather than toward the
    other run's verdict.
    """
    if run_summary_path is None:
        return 2
    try:
        summary = read_summary(run_summary_path)
    except RunSummaryError:
        return 2
    if summary is None:
        return 2
    if superseding is not None and summary.run_id == superseding:
        # This run never wrote a manifest. The one on disk is not about it.
        return 2
    return summary.exit_code


def _report_backup_failure(
    exc: "GitOperationError",
    run_summary_path: Path | None = None,
    *,
    superseding: "str | None" = None,
) -> int:
    """Explain a failed Backup in terms of what is and is not at risk.

    `run_summary_path` is a parameter rather than a module-level default
    because the exit code is now read from that file. Reaching for the
    default inside here made this function depend on a path its caller never
    named: measured, a test calling it directly picked up the repository's
    own live manifest — which said SUCCESS — and got exit 0 for a Backup
    failure. The one thing this function exists to report, decided by an
    unrelated file.
    """
    # Classified on the RAW message, printed guarded. The order matters:
    # `is_authentication_failure()` matches git's own wording, and running it
    # on a redacted string would let a substitution eat the phrase the
    # classification depends on.
    permanent = is_authentication_failure(str(exc))

    # `GitOperationError` embeds `result.stderr.strip()` verbatim
    # (`backup/git_ops._run_git`), so this is multi-line output from another
    # program — and on a push failure git echoes the remote URL, which in a
    # `https://<token>@github.com/...` remote carries the credential.
    # `oplog.SECRET_PATTERNS` already knows the GitHub token shapes; nothing
    # was applying it here. Same guard, same reason, as the `SyncResult.error`
    # line below and the manifest block under it.
    print(f"[FAILED] Backup: {redact(one_line(exc))}", file=sys.stderr)
    print(file=sys.stderr)
    print(
        "Event 수집 · History Filter · Daily · Monthly 단계는 Backup보다 먼저\n"
        "끝났고 이미 디스크에 저장되어 있습니다. 유실된 데이터는 없습니다.",
        file=sys.stderr,
    )
    if permanent:
        print(
            "\n이 실패는 인증/권한 문제로 분류되어 BACKUP_FAILED로 기록됐습니다.\n"
            "일정에 맡겨 재시도해도 해결되지 않습니다 — 자격증명을 갱신한 뒤\n"
            "다시 실행하세요(docs/08 §21, §62).",
            file=sys.stderr,
        )
    else:
        print(
            "\n이 실패는 일시적인 것으로 분류되어 BACKUP_PENDING으로 기록됐습니다.\n"
            "다음 Runner 실행이 같은 commit을 자동으로 다시 push합니다(docs/08 §19).\n"
            "따로 할 일은 없습니다.",
            file=sys.stderr,
        )
    print("\n현재 상태는 `python ops_status.py`로 확인할 수 있습니다.", file=sys.stderr)

    # Exit code from the Run Manifest, not from a literal here.
    #
    # `run_once()` writes the manifest in its `finally`, so it exists even
    # though the run aborted — and it has already classified this exact
    # failure (BACKUP_PENDING/RETRYABLE vs BACKUP_FAILED/PERMANENT, by
    # docs/08 §21's own rule). Returning a hardcoded 2 here made the process
    # disagree with its own manifest: measured against a broken remote, the
    # manifest said DEGRADED/exit 3 while the process exited 2. Two answers
    # to "how bad was this run" is one too many, and the scheduled task only
    # ever sees this one.
    #
    # Falls back to 2 if the manifest cannot be read: a Backup failure with
    # no manifest is genuinely unclassified, and 2 is the conservative
    # reading of an unclassified failure.
    # C78 moved the body of this to `_exit_code_from_manifest()`, unchanged,
    # when a second abort path needed the same answer. The reasoning above is
    # left here because this is where it was worked out.
    return _exit_code_from_manifest(run_summary_path, superseding=superseding)


# How many dates `_dates()` spells out before it starts counting instead.
# Ten is a fortnight's catch-up minus weekends — enough that every ordinary
# run lists everything, and small enough that the abnormal one still fits on
# a line an operator can read.
_MAX_LISTED_DATES = 10


def _dates(days) -> str:
    """A day list an operator can read, from a tuple of `date` objects.

    `f"{scheduler_result.generated_dates}"` interpolates the tuple's **repr**.
    Measured, the production entrypoint run end to end in an isolated copy of
    this repository, first-ever run with `COMPANY_OPS_HISTORY_START_DATE`
    seventeen days back:

        Daily History (Scheduler): COMPLETED, generated=(datetime.date(2026,
        8, 1), datetime.date(2026, 8, 2), … datetime.date(2026, 8, 17))

    606 characters of Python repr on the one line that answers "what did this
    run close". `AGENT.md` §6a-3 shows the operator a different line —

        Daily History (Scheduler): COMPLETED, generated=(2026-08-05,) reused=(…)

    — and tells them to compare the two numbers ("복구 직후에는 `reused`가 크고
    `generated`가 작은 것이 정상이며, 그 반대라면 즉시 멈추고 원격을 확인해야
    한다"). That instruction is at its most important right after a disaster
    restore, which is exactly when both lists are longest: sixty restored days
    print as ~1,800 characters of `datetime.date(...)`.

    So the count comes first — it is the number §6a-3 actually asks for — and
    the dates follow in ISO, which is how every other date in this pipeline is
    written (filenames, Metadata blocks, the manifest). Past
    `_MAX_LISTED_DATES` the remainder is COUNTED rather than dropped: a
    truncation that does not say it truncated would make a long catch-up look
    like a short one, which is the same misreading this function exists to
    remove.
    """
    if not days:
        return "0"
    shown = ", ".join(day.isoformat() for day in days[:_MAX_LISTED_DATES])
    remaining = len(days) - _MAX_LISTED_DATES
    if remaining > 0:
        shown += f", 외 {remaining}일"
    return f"{len(days)} ({shown})"


def _print_result(result) -> int:
    if result is None:
        print("[SKIPPED] 다른 Runner가 이미 실행 중입니다(Lock 획득 실패).")
        return 0

    intake_summary, collector_summary, scheduler_result, backup_entry, notion_sync_results = result

    print(f"Transport: moved={len(intake_summary.moved)}")
    print(
        f"Collector: accepted={collector_summary.accepted} "
        f"duplicate={collector_summary.duplicate} "
        f"rejected={collector_summary.rejected} "
        f"failed={collector_summary.failed}"
    )
    print(f"Notion Sync: {len(notion_sync_results)}건 처리")
    for r in notion_sync_results:
        # `SyncResult.error` is a remote HTTP response body carried verbatim
        # (`notion/transport._error_detail()` appends it so an operator can
        # see *which* property Notion rejected — BUG-58). That makes it the
        # same class of string `oplog.append_line()` guards, and it reached
        # here guarded by nothing.
        #
        # Measured, a proxy answering 502 in Notion's place and echoing the
        # request headers back — the exact scenario `append_line()`'s own
        # docstring names as the reason redaction exists:
        #
        #     notion_sync.log   token redacted, 1 line
        #     this stdout       `Authorization: Bearer ntn_…` in full, 4 lines
        #
        # Both halves matter. `redact()` is docs/04 §56; `one_line()` stops a
        # multi-line body from forging further `  - <event_id> …` result
        # lines in a report an operator reads to decide what happened.
        #
        # Applied here rather than in `notion/transport.py`, which is where
        # the string is built: `notion` may import only `events`
        # (LayeringInvariantTests), and widening that table is an
        # architecture decision. This script is the composition root's
        # entrypoint and already sits above everything, so the guard goes at
        # the sink — which is also where the exposure is.
        suffix = f" [{redact(one_line(r.error))}]" if r.error else ""
        # `event_id` and `project_id` need `one_line()` too, and this line
        # shipped without it — a blind spot in the fix directly above.
        # Guarding the interpolation that obviously came from a remote
        # response, while leaving two Event fields that cross the same
        # transport unguarded, is half a fix. Measured: an `event_id` of
        # `"EVT-1\n  - EVT-GHOST (PRJ): SYNCED"` put a fully attacker-authored
        # result row in this report, indistinguishable from a real one.
        #
        # `redact()` is not applied to these two: they are identifiers the
        # operator needs verbatim to find the Event, and unlike `r.error`
        # neither carries a remote response body. docs/02 constrains both to
        # "present and non-null" only (BACKLOG A-15), which is why they need
        # the line guard at all.
        print(
            f"  - {one_line(r.event_id)} ({one_line(r.project_id)}): "
            f"{r.status.value}{suffix}"
        )
    print(
        f"Daily History (Scheduler): {scheduler_result.status.value}, "
        f"generated={_dates(scheduler_result.generated_dates)}"
        + (
            f" reused={_dates(scheduler_result.reused_dates)}"
            if scheduler_result.reused_dates
            else ""
        )
    )
    # A failed Daily Close used to print as "FAILED, generated=[]" and nothing
    # else, even though the result object carries which date died and why
    # (BUG-39). Scheduler stops at the first failing date, so that date and
    # every later one still have no Daily file — this is the line that says
    # where the next run has to resume from.
    #
    # stdout, not stderr, and deliberately so: this run *completed* (main()
    # returns 0), so these lines are part of the run report rather than a
    # process error, and `_report_backup_failure()` — which does use stderr —
    # is the opposite case, an aborted run whose every line goes there.
    # Mixing the two streams here also reordered the output: Python flushes
    # them independently, so the explanation printed above the "Daily History
    # (Scheduler): FAILED" line it explains. Verified by running it.
    if scheduler_result.status is SchedulerStatus.FAILED:
        failed_date = (
            scheduler_result.failed_date.isoformat()
            if scheduler_result.failed_date
            else "알 수 없음"
        )
        print(
            f"  실패 날짜: {failed_date}\n"
            f"  원인: {scheduler_result.error}\n"
            f"  이 날짜와 이후 날짜의 Daily History는 아직 없습니다. 원인을 해결하면\n"
            f"  다음 실행이 같은 날짜부터 이어서 생성합니다."
        )
    print(f"Backup: {backup_entry.final_status.value}")

    return _report_run_summary(result)


def _report_run_summary(result) -> int:
    """The Run Contract's last link: Overall Status -> Exit Code.

    Before this, `main()` returned 0 whatever it printed. The Runner is
    launched by Windows Task Scheduler, whose only automatic health signal
    is the exit code ("Last Run Result") — stdout is not captured by
    default. So a run whose Backup failed the Secret Scan reported 0x0 /
    success, and the failures that were handled *gracefully* were exactly
    the ones that became invisible.

    Three values, because two are not enough for this pipeline. README RULE
    5 puts Notion off the History critical path and RULE 9 keeps Company
    History recording while everything downstream is down — so most failures
    here are genuinely neither "fine" nor "broken". Collapsing DEGRADED into
    SUCCESS hides real breakage; collapsing it into FAILED cries wolf until
    nobody looks at either.

        SUCCESS   0
        DEGRADED  3   something needs a person, History is intact
        FAILED    2   a critical component failed

    3 matches `ops_status.py`'s existing "something needs a person", so the
    two entrypoints agree on what a 3 means. 1 stays reserved for a
    configuration error, which happens before a run exists to summarise.
    """
    summary = getattr(result, "summary", None)
    if summary is None:
        # A caller that predates the Run Contract. Nothing to classify, so
        # keep the old behaviour rather than guess.
        return 0

    status = summary.overall_status
    failures = summary.failures()

    if failures:
        print()
        print(f"실행 상태: {status.value}")
        for component in failures:
            failure = component.failure
            # `redact(one_line(...))`, and this block is where it was most
            # obviously missing. `_print_result()` twenty lines above already
            # guards `SyncResult.error` because it is "a remote HTTP response
            # body carried verbatim" (C31 §7) — and `failure.reason` is
            # **that same string**: `app/runner.py` records
            # `reason=queued[0].error` for NOTION_SYNC_INCOMPLETE, and
            # `runsummary` persists it to `run_summary.json` verbatim. So the
            # body redacted on one line of this file was printed in full,
            # from disk, three functions later.
            #
            # `ops_status.py` renders the same manifest and reached the
            # opposite conclusion twice over: it guards `component.name` and
            # `failure.classification` with `one_line()`, and it does not
            # print `reason` at all ("`reason` carries that and is
            # deliberately not printed here"). This entrypoint prints it, so
            # it needs the guard the other one avoided needing.
            #
            # `read_summary()` validates only the three enums; `name`,
            # `classification`, `reason` and `artifact_refs` come back out of
            # JSON as whatever the file holds — which on a restored or
            # hand-edited manifest is a DR path, not an exotic one.
            print(
                f"  [{one_line(component.name)}] {one_line(failure.classification)} "
                f"(severity={failure.severity.value}, "
                f"retry={failure.retryability.value})"
            )
            if failure.reason:
                print(f"      {redact(one_line(failure.reason))}")
            if component.artifact_refs:
                # The Run Summary is a manifest, not a log: it names where
                # the detail is rather than reproducing it.
                print(
                    "      evidence: "
                    + ", ".join(one_line(ref) for ref in component.artifact_refs)
                )

    return summary.exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
