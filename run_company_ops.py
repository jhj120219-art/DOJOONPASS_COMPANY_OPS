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
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from app.runner import run_once  # noqa: E402
from notion import (  # noqa: E402
    ExecutionPlanSync,
    NotionClient,
    NotionConfig,
    NotionConfigError,
    RealNotionTransport,
)

PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"


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
    except NotionConfigError:
        print(
            "[INFO] Notion 미설정 — Notion Sync / Operations Dashboard 단계를 "
            "건너뜁니다 (NOTION_API_TOKEN / NOTION_PROJECTS_DATABASE_ID 없음)."
        )
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


def main() -> int:
    history_start_date = _resolve_history_start_date()
    notion_sync, dashboard_client = _build_notion_clients()

    local_master_dir = RUNTIME_DIR / "local_master"
    local_master_dir.mkdir(parents=True, exist_ok=True)

    result = run_once(
        local_master_dir=local_master_dir,
        backup_working_copy_dir=RUNTIME_DIR / "backup_working_copy",
        history_start_date=history_start_date,
        runner_lock_path=RUNTIME_DIR / "locks" / "company_ops.lock",
        notion_sync=notion_sync,
        dashboard_client=dashboard_client,
    )

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
        suffix = f" [{r.error}]" if r.error else ""
        print(f"  - {r.event_id} ({r.project_id}): {r.status.value}{suffix}")
    print(
        f"Daily History (Scheduler): {scheduler_result.status.value}, "
        f"generated={scheduler_result.generated_dates}"
    )
    print(f"Backup: {backup_entry.final_status.value}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
