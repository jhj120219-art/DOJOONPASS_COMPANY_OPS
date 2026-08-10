"""Notion Operations Dashboard (CEO Decision ④).

Records one row per Runner execution into Notion, so operational results
are visible without any CLI dashboard and without expanding stdout — the
explicitly chosen direction ("CLI 확장 금지, Dashboard는 Notion으로").

Two strictly separated responsibilities:

    bootstrap_dashboard_databases()  one-time setup: create whichever of
                                     the five OPS_* databases don't exist
                                     yet under a parent page. Never called
                                     by the Runtime pipeline.
    record_run()                     called once, at the very end of a
                                     Runner execution, with the results
                                     that execution already produced.

Non-negotiable Runtime property (CEO Decision ④): a Dashboard failure must
never stop the Runtime. `record_run()` therefore catches every exception
and reports the outcome in its return value instead of raising. Nothing in
this module writes History, Local Master, or any Event file.

Data source: the existing objects `app.runner.run_once()` already returns
(IntakeSummary / RuntimeSummary / SchedulerRunResult / BackupLogEntry /
tuple[SyncResult, ...]). No new measurement, counter, or state is invented
here — this module only reshapes what the Runner already computed.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from .client import NotionClient

# ---------------------------------------------------------------- schemas

OPS_RUNS = "OPS_RUNS"
OPS_BACKUP = "OPS_BACKUP"
OPS_NOTION_SYNC = "OPS_NOTION_SYNC"
OPS_RISK = "OPS_RISK"
OPS_READINESS = "OPS_READINESS"

# Property payloads use only shapes the Notion API accepts when *creating*
# a database — same constraint notion.bootstrap already documents (Status
# is Select, never Notion's non-creatable "status" type).
DASHBOARD_DATABASES: dict[str, dict[str, Any]] = {
    OPS_RUNS: {
        "Run ID": {"title": {}},
        "Run At": {"date": {}},
        "Transport Moved": {"number": {}},
        "Accepted": {"number": {}},
        "Duplicate": {"number": {}},
        "Rejected": {"number": {}},
        "Failed": {"number": {}},
        "Scheduler Status": {"select": {}},
        "Generated Days": {"number": {}},
        "Backup Status": {"select": {}},
        "Notion Synced": {"number": {}},
        "Notion Retried": {"number": {}},
        "Overall": {"select": {}},
    },
    OPS_BACKUP: {
        "Run ID": {"title": {}},
        "Backup At": {"date": {}},
        "Commit Hash": {"rich_text": {}},
        "Changed Files": {"number": {}},
        "Deleted Files": {"number": {}},
        "Push Result": {"rich_text": {}},
        "Final Status": {"select": {}},
    },
    OPS_NOTION_SYNC: {
        "Event ID": {"title": {}},
        "Run ID": {"rich_text": {}},
        "Project ID": {"rich_text": {}},
        "Result": {"select": {}},
        "Synced At": {"date": {}},
    },
    OPS_RISK: {
        "Risk": {"title": {}},
        "Severity": {"select": {}},
        "Area": {"select": {}},
        "Status": {"select": {}},
        "First Seen": {"date": {}},
        "Resolved At": {"date": {}},
    },
    OPS_READINESS: {
        "Date": {"title": {}},
        "Runtime": {"number": {}},
        "Execution": {"number": {}},
        "Recovery": {"number": {}},
        "History": {"number": {}},
        "Backup": {"number": {}},
        "Scheduler": {"number": {}},
        "Lock": {"number": {}},
        "Logging": {"number": {}},
        "Observability": {"number": {}},
        "Maintainability": {"number": {}},
        "Overall": {"number": {}},
    },
}


class DashboardOutcome(enum.Enum):
    RECORDED = "RECORDED"
    FAILED = "FAILED"
    SKIPPED_NOT_CONFIGURED = "SKIPPED_NOT_CONFIGURED"


@dataclass(frozen=True)
class DashboardResult:
    outcome: DashboardOutcome
    run_id: str
    page_id: str | None = None
    error: str | None = None
    # On FAILED, the properties that were already built for this run (None
    # only if the failure happened while building them). The caller queues
    # exactly these for retry, so the property-building logic lives in one
    # place — here — and is never duplicated at the call site.
    properties: dict[str, Any] | None = None


# ------------------------------------------------------------- bootstrap


@dataclass(frozen=True)
class DashboardBootstrapResult:
    created: dict[str, str]

    def database_id(self, name: str) -> str | None:
        return self.created.get(name)


class DashboardParentError(Exception):
    """Raised when no existing Page can host the OPS_* databases.

    Notion's API can only create a database under a **Page**
    (`parent: {"type": "page_id", ...}`); it cannot create one at
    workspace root. So when the reference database itself sits at
    workspace root, there is no existing Page to reuse — and creating one
    is explicitly out of scope (operator instruction: "새로운 Page 생성
    금지"). The operator resolves this by sharing an existing Page with the
    integration, not by this code inventing a location.
    """


class DashboardBootstrapPartialError(Exception):
    """Raised when creating the OPS_* databases fails part-way through.

    Carries `created` — the `{name: database_id}` map for the databases that
    were created *before* the failure. Those databases really exist in the
    workspace, and this function is not allowed to delete them, so the ids
    are the only thing standing between the operator and a duplicate set on
    the next attempt.

    `failed_database` names the one that did not get created, and `cause` is
    the original error (also chained, so a traceback still shows it).

    Retry guidance for a caller: pass the names that are still missing via
    `only=`, never the whole set again.
    """

    def __init__(self, *, created: dict, failed_database: str, cause: BaseException):
        self.created = dict(created)
        self.failed_database = failed_database
        self.cause = cause
        already = ", ".join(f"{k}={v}" for k, v in self.created.items()) or "none"
        super().__init__(
            f"creating {failed_database!r} failed: {cause}. "
            f"Already created (record these ids before retrying, and retry with "
            f"only= the remaining names): {already}"
        )


def resolve_parent_page_id(client: NotionClient) -> str:
    """Reuse the existing Workspace structure: return the Page that already
    hosts the database `client` is bound to (e.g. PROJECTS).

    Raises DashboardParentError when that database is not inside a Page —
    see that exception's docstring for why this cannot be worked around
    here.
    """
    parent = client.get_database_parent()
    parent_type = parent.get("type")

    if parent_type == "page_id":
        return parent["page_id"]

    raise DashboardParentError(
        f"the reference database's parent is {parent_type!r}, not a Page, so there is "
        "no existing Page to create the OPS_* databases under. Notion's API cannot "
        "create a database at workspace root. Share an existing Page (the Company Ops "
        "page) with this integration and pass its id as parent_page_id."
    )


class BootstrapReadiness(enum.Enum):
    READY = "READY"
    # A Page exists and is shared, but is not the reference database's own
    # parent — the operator must confirm which Page to use.
    NEEDS_PARENT_CHOICE = "NEEDS_PARENT_CHOICE"
    # Nothing usable is shared with the integration yet.
    NEEDS_SHARED_PAGE = "NEEDS_SHARED_PAGE"


@dataclass(frozen=True)
class HostablePage:
    """A Page this integration can see that could host a database.

    Excludes pages whose parent is a database — those are *rows* inside a
    database (e.g. a PROJECTS record), not container pages, and nesting the
    OPS_* databases inside one would be structurally wrong.
    """

    page_id: str
    title: str
    parent_type: str


@dataclass(frozen=True)
class BootstrapDiagnosis:
    readiness: BootstrapReadiness
    reference_parent_type: str
    resolved_parent_page_id: str | None
    hostable_pages: tuple[HostablePage, ...]
    search_available: bool
    required_action: str


def _page_title(page: Mapping[str, Any]) -> str:
    for value in (page.get("properties") or {}).values():
        if value.get("type") == "title":
            parts = value.get("title") or []
            text = "".join(part.get("plain_text", "") for part in parts)
            if text:
                return text
    return "(untitled)"


def diagnose_dashboard_bootstrap(client: NotionClient) -> BootstrapDiagnosis:
    """Work out, from the real Workspace, whether the OPS_* databases can be
    created — and if not, exactly what the operator has to do.

    Read-only: this inspects the reference database's parent and the pages
    the integration can see. It never creates a Page or a Database, and it
    never raises for an unusable workspace — an unusable workspace IS the
    answer it is meant to report.
    """
    try:
        parent = client.get_database_parent()
    except Exception as exc:  # noqa: BLE001  (진단은 절대 실패하지 않는다)
        # This function's stated contract is that it never raises for an
        # unusable workspace, and the `search_pages()` call below already
        # honours it. The parent lookup did not: a network failure, an
        # expired token, or a deleted reference database made the
        # *diagnostic* explode — the tool an operator reaches for precisely
        # because Notion is not behaving. Unreachable is itself a finding,
        # so it is reported in the same shape as every other one.
        return BootstrapDiagnosis(
            readiness=BootstrapReadiness.NEEDS_SHARED_PAGE,
            reference_parent_type="unreachable",
            resolved_parent_page_id=None,
            hostable_pages=(),
            search_available=False,
            required_action=(
                f"Could not read the reference database: {exc}. Check "
                "NOTION_API_TOKEN and NOTION_PROJECTS_DATABASE_ID, and that the "
                "database is still shared with this integration. Nothing about "
                "the Dashboard can be determined until that call succeeds."
            ),
        )

    parent_type = parent.get("type", "unknown")
    resolved_parent_page_id = parent.get("page_id") if parent_type == "page_id" else None

    try:
        raw_pages = client.search_pages()
        search_available = True
    except (NotImplementedError, Exception):  # noqa: BLE001  (진단은 절대 실패하지 않는다)
        raw_pages = []
        search_available = False

    hostable = tuple(
        HostablePage(
            page_id=page.get("id", ""),
            title=_page_title(page),
            parent_type=(page.get("parent") or {}).get("type", "unknown"),
        )
        for page in raw_pages
        if (page.get("parent") or {}).get("type") != "database_id"
    )

    if resolved_parent_page_id is not None:
        return BootstrapDiagnosis(
            readiness=BootstrapReadiness.READY,
            reference_parent_type=parent_type,
            resolved_parent_page_id=resolved_parent_page_id,
            hostable_pages=hostable,
            search_available=search_available,
            required_action=(
                "None — the reference database already lives in a Page; "
                "bootstrap_dashboard_databases(client) will use it."
            ),
        )

    if hostable:
        return BootstrapDiagnosis(
            readiness=BootstrapReadiness.NEEDS_PARENT_CHOICE,
            reference_parent_type=parent_type,
            resolved_parent_page_id=None,
            hostable_pages=hostable,
            search_available=search_available,
            required_action=(
                "The reference database sits at workspace root, but shared Page(s) exist. "
                "Pass one of hostable_pages' page_id as parent_page_id "
                "(choosing the Page is an operator decision, not this code's)."
            ),
        )

    return BootstrapDiagnosis(
        readiness=BootstrapReadiness.NEEDS_SHARED_PAGE,
        reference_parent_type=parent_type,
        resolved_parent_page_id=None,
        hostable_pages=(),
        search_available=search_available,
        required_action=(
            "Share an existing Page (the Company Ops page) with this integration in "
            "Notion (Share -> Connections). Notion's API cannot create a database at "
            "workspace root, and creating a Page is out of scope."
        ),
    )


def bootstrap_dashboard_databases(
    client: NotionClient,
    *,
    parent_page_id: str | None = None,
    only: Sequence[str] | None = None,
) -> DashboardBootstrapResult:
    """Create the OPS_* databases under an existing Page, returning their ids.

    `parent_page_id` omitted -> the location is derived from the database
    `client` is already bound to (`resolve_parent_page_id`), so the OPS_*
    databases land beside the existing PROJECTS database rather than in a
    newly invented location. Passing `parent_page_id` explicitly overrides
    that lookup.

    One-time operational setup, deliberately NOT called from the Runtime
    pipeline (same principle as notion.bootstrap). This only ever creates
    databases — it never creates a Page, and never deletes or reshapes an
    existing database. The caller decides which names to create via `only`,
    and is responsible for not re-creating databases it already has (their
    ids belong in configuration).
    """
    resolved_parent_page_id = (
        parent_page_id if parent_page_id is not None else resolve_parent_page_id(client)
    )

    names = list(only) if only is not None else list(DASHBOARD_DATABASES)
    created: dict[str, str] = {}
    for name in names:
        properties = DASHBOARD_DATABASES[name]
        try:
            response = client.create_database(
                parent_page_id=resolved_parent_page_id, title=name, properties=properties
            )
        except Exception as exc:  # noqa: BLE001
            # Databases created before this point EXIST in the operator's
            # workspace. Letting the exception through discarded their ids,
            # and this function's own contract says the caller "is
            # responsible for not re-creating databases it already has
            # (their ids belong in configuration)" — which is impossible
            # without the ids. Re-running then silently produced a second
            # OPS_RUNS, a second OPS_BACKUP, and so on, with nothing able to
            # say which is which and no delete path to clean up.
            #
            # So the partial result is carried out on the exception instead
            # of being thrown away. Nothing is retried or rolled back here:
            # deciding that is the caller's, and creating is all this
            # function is allowed to do.
            raise DashboardBootstrapPartialError(
                created=created, failed_database=name, cause=exc
            ) from exc

        database_id = response.get("id")
        if not database_id:
            # Created, but Notion did not hand back an id to record. Failing
            # loudly beats returning None, which `database_id()` would later
            # report as "not created" for a database that exists.
            raise DashboardBootstrapPartialError(
                created=created,
                failed_database=name,
                cause=ValueError(
                    f"Notion returned no id for the created database {name!r}"
                ),
            )
        created[name] = database_id
    return DashboardBootstrapResult(created=created)


# ------------------------------------------------------- property builders


def _title(text: str) -> dict:
    return {"title": [{"type": "text", "text": {"content": text}}]}


def _rich_text(text: str) -> dict:
    return {"rich_text": [{"type": "text", "text": {"content": text}}]}


def _number(value: int | float) -> dict:
    return {"number": value}


def _select(name: str) -> dict:
    return {"select": {"name": name}}


def _date(iso_timestamp: str) -> dict:
    return {"date": {"start": iso_timestamp}}


def _overall_status(collector_failed: int, scheduler_status: str, backup_status: str) -> str:
    """Single at-a-glance verdict for one run.

    Deliberately derived only from statuses the Runner already produced —
    no new health policy is invented here. FAIL when a stage reported an
    outright failure; WARN when nothing failed outright but something needs
    a human look (rejected/failed events, Backup not successful); else OK.
    """
    if collector_failed > 0 or scheduler_status == "FAILED" or backup_status == "BACKUP_FAILED":
        return "FAIL"
    if backup_status in ("BACKUP_PENDING", "BACKUP_REVIEW"):
        return "WARN"
    return "OK"


def build_ops_run_properties(
    *,
    run_id: str,
    run_at: datetime,
    transport_moved: int,
    accepted: int,
    duplicate: int,
    rejected: int,
    failed: int,
    scheduler_status: str,
    generated_days: int,
    backup_status: str,
    notion_synced: int,
    notion_retried: int,
) -> dict[str, Any]:
    return {
        "Run ID": _title(run_id),
        "Run At": _date(run_at.isoformat(timespec="seconds")),
        "Transport Moved": _number(transport_moved),
        "Accepted": _number(accepted),
        "Duplicate": _number(duplicate),
        "Rejected": _number(rejected),
        "Failed": _number(failed),
        "Scheduler Status": _select(scheduler_status),
        "Generated Days": _number(generated_days),
        "Backup Status": _select(backup_status),
        "Notion Synced": _number(notion_synced),
        "Notion Retried": _number(notion_retried),
        "Overall": _select(_overall_status(failed, scheduler_status, backup_status)),
    }


def build_ops_backup_properties(
    *,
    run_id: str,
    backup_at: datetime,
    commit_hash: str | None,
    changed_files: int,
    deleted_files: int,
    push_result: str | None,
    final_status: str,
) -> dict[str, Any]:
    return {
        "Run ID": _title(run_id),
        "Backup At": _date(backup_at.isoformat(timespec="seconds")),
        "Commit Hash": _rich_text(commit_hash or ""),
        "Changed Files": _number(changed_files),
        "Deleted Files": _number(deleted_files),
        "Push Result": _rich_text(push_result or ""),
        "Final Status": _select(final_status),
    }


# ------------------------------------------------------------- recording


def record_run(
    client: NotionClient | None,
    *,
    run_id: str,
    run_at: datetime,
    intake_summary: Any,
    collector_summary: Any,
    scheduler_result: Any,
    backup_entry: Any,
    notion_sync_results: Sequence[Any],
) -> DashboardResult:
    """Write one OPS_RUNS row for this execution. Never raises.

    `client` must be a NotionClient bound to the **OPS_RUNS** database id
    (not the PROJECTS one ExecutionPlanSync uses) — this writes operational
    run records, never Current State.

    CEO Decision ④: "Dashboard 기록 실패는 Runtime을 절대 중단시키면 안
    된다." Every failure path here returns a DashboardResult instead of
    propagating — including a missing/unconfigured client, which is simply
    SKIPPED_NOT_CONFIGURED rather than an error.
    """
    if client is None:
        return DashboardResult(
            outcome=DashboardOutcome.SKIPPED_NOT_CONFIGURED, run_id=run_id
        )

    try:
        failed_statuses = ("NOTION_RETRY_REQUIRED", "NOTION_FAILED")
        statuses = [getattr(r.status, "value", "") for r in notion_sync_results]
        synced = sum(1 for s in statuses if s and s not in failed_statuses)
        retried = sum(1 for s in statuses if s in failed_statuses)
        properties = build_ops_run_properties(
            run_id=run_id,
            run_at=run_at,
            transport_moved=len(getattr(intake_summary, "moved", ()) or ()),
            accepted=getattr(collector_summary, "accepted", 0),
            duplicate=getattr(collector_summary, "duplicate", 0),
            rejected=getattr(collector_summary, "rejected", 0),
            failed=getattr(collector_summary, "failed", 0),
            scheduler_status=getattr(scheduler_result.status, "value", str(scheduler_result.status)),
            generated_days=len(getattr(scheduler_result, "generated_dates", ()) or ()),
            backup_status=getattr(
                backup_entry.final_status, "value", str(backup_entry.final_status)
            ),
            notion_synced=synced,
            notion_retried=retried,
        )
    except Exception as exc:  # noqa: BLE001  (CEO ④: Runtime을 절대 중단시키지 않는다)
        return DashboardResult(
            outcome=DashboardOutcome.FAILED, run_id=run_id, error=str(exc), properties=None
        )

    try:
        page = client.create_project(properties)
    except Exception as exc:  # noqa: BLE001  (CEO ④: Runtime을 절대 중단시키지 않는다)
        return DashboardResult(
            outcome=DashboardOutcome.FAILED,
            run_id=run_id,
            error=str(exc),
            properties=properties,
        )

    return DashboardResult(
        outcome=DashboardOutcome.RECORDED, run_id=run_id, page_id=page.get("id")
    )
