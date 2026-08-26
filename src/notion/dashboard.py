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

from .bootstrap import (
    BootstrapResult,
    PropertyBootstrapReport,
    PropertyOutcome,
    _bootstrap_title_property,
    diff_properties,
)
from .client import NotionClient

# ---------------------------------------------------------------- schemas

# The title property of OPS_RUNS, and therefore the key a run is looked up
# by before a row is created for it. Named once here so `record_run()` and
# `dashboard_pending.drain_pending()` cannot drift to different keys — two
# writers keyed differently is exactly how the duplicate row appeared.
RUN_ID_PROPERTY = "Run ID"

OPS_RUNS = "OPS_RUNS"
OPS_BACKUP = "OPS_BACKUP"
OPS_NOTION_SYNC = "OPS_NOTION_SYNC"
OPS_RISK = "OPS_RISK"
OPS_READINESS = "OPS_READINESS"

# The databases the **specification** puts in Notion, as opposed to the ones
# this module happens to have a schema for.
#
# docs/14 §1 fixes the Operational Data Model, and its row for Notion reads:
#
#     | Operational Projection | Notion (PROJECTS / OPS_RUNS) | View이며 절대
#       Source가 아니다 |
#
# Two databases, named. PROJECTS belongs to `notion.sync`; the one this
# module owns is OPS_RUNS. The other four below have schemas and no place in
# that model — so creating them is not "getting ahead", it is widening the
# Operational Projection past what docs/14 defines, which is a spec change.
#
# This constant exists because the distinction had no name in the code, and
# an unnamed distinction is one `bootstrap_dashboard_databases()` could not
# default to. It defaulted to all five instead: an operator following the
# setup doc got four databases that no code writes and no spec sanctions,
# permanently empty in their workspace, with a prose warning in docs/13 as
# the only thing standing in the way.
#
# BACKLOG A-16 has recorded this since C10 as "docs/04 §53's 과잉 방지
# decision", i.e. as *undecided*. docs/14 §1 — written later — decides it.
# The four are out of contract, not awaiting one.
CONTRACTED_DATABASES: tuple[str, ...] = (OPS_RUNS,)

# Property payloads use only shapes the Notion API accepts when *creating*
# a database — same constraint notion.bootstrap already documents (Status
# is Select, never Notion's non-creatable "status" type).
DASHBOARD_DATABASES: dict[str, dict[str, Any]] = {
    OPS_RUNS: {
        "Run ID": {"title": {}},
        "Run At": {"date": {}},
        "Transport Moved": {"number": {}},
        # Files sitting in `transport/` that this run refused to promote for
        # a reason the next run will reach again — see
        # `count_blocked_intake()`. Without it the Dashboard had no column
        # that could ever be non-zero when the inbound path is broken, which
        # made a broken inbound path indistinguishable from a quiet day.
        "Transport Blocked": {"number": {}},
        "Accepted": {"number": {}},
        "Duplicate": {"number": {}},
        "Rejected": {"number": {}},
        "Failed": {"number": {}},
        "Scheduler Status": {"select": {}},
        "Generated Days": {"number": {}},
        # Days this run closed WITHOUT writing, because the file was already
        # there — a crashed predecessor's (docs/07 §28) or, after a disaster
        # restore, git's (C39). `Generated Days` alone cannot tell "nothing
        # happened" from "seventeen days came back from the backup", and the
        # run an operator scrutinises hardest is exactly the second one.
        #
        # C39 split `SchedulerRunResult.generated_dates` for this reason and
        # the split reached the Run Manifest and `run_company_ops.py`'s
        # stdout. It did not reach here — the view CEO Decision ④ made the
        # operator's at-a-glance one ("CLI 확장 금지, Dashboard는 Notion으로").
        # Measured on the restore `test_e2e_disaster_scenarios.py` performs:
        # manifest `generated_days=1 reused_days=4`, Dashboard row
        # `Generated Days: 1` and nothing else.
        #
        # Not an input to `Overall`. Reusing a day is the pipeline working;
        # the verdict's rule ("any future input has to earn a column first")
        # is about causes of WARN, and this is not one.
        "Reused Days": {"number": {}},
        "Backup Status": {"select": {}},
        # Local Master files that disappeared, which is why `Backup Status`
        # alone is not enough. `BACKUP_FAILED` is written by two completely
        # different events (BACKLOG E-25): docs/08 §21's credential failure,
        # and docs/08 §31/§44-47's deletion gate refusing to add/commit/push
        # because Company History files are gone. The operator's next action
        # is a token in one case and a search for missing History in the
        # other, and the row could not tell them apart.
        #
        # C31 put the distinction in the Run Manifest (`reason` plus a
        # `deleted_files` metric) and deliberately left the classification
        # value alone, because the docs/14 §5 vocabulary is a spec decision.
        # This changes no value either — it is the same number, in the view
        # CEO Decision ④ made the at-a-glance one, where the manifest's
        # `reason` never appears.
        #
        # Not an input to `Overall`: `BACKUP_FAILED` already makes the row
        # FAIL, and a second path to the same verdict would just be two
        # derivations of one fact.
        "Deleted Files": {"number": {}},
        "Notion Synced": {"number": {}},
        # Events that reached Notion and deliberately changed nothing
        # (docs/04 §35 NOTION_SKIPPED_OLD_EVENT). Split out of "Synced",
        # which used to include them and therefore reported writes that did
        # not happen.
        "Notion Skipped": {"number": {}},
        "Notion Retried": {"number": {}},
        # Collected Event files, or queued entries, this run could not read
        # as an Event at all. They never become a SyncResult — inventing an
        # `event_id` for a file that could not be parsed would put a made-up
        # id in the log — so without this column they were in none of the
        # three counts above and the row's arithmetic silently lost them.
        "Notion Unreadable": {"number": {}},
        # How many Events are still waiting for Notion *after* this run.
        # The three counts above are per-run; this is the standing backlog,
        # and it is the difference between "one Event failed this morning"
        # and "eight hundred Events have been stuck for a month".
        "Notion Queued": {"number": {}},
        # The steps this run recorded as FAILED, by name — the manifest's
        # own `components`, not a second judgement. Two of the nine steps
        # (`late_update`, `monthly`) can fail without stopping the run and
        # had no column at all, so the row said `Overall OK` for a run whose
        # exit code was 3. Rich Text rather than a count: "which step" is
        # the question an operator asks next, and it fits in the same glance.
        "Failed Steps": {"rich_text": {}},
        # Which Desktops contributed Events to this run, and how many each.
        # Every other column here is a pipeline-stage number; this is the one
        # that says *where the work came from*, which is the question layer ④
        # of the Control Tower asks and the row could not answer at all — a
        # run that collected fifty Events looked identical whether they came
        # from four Desktops or from one.
        #
        # Rich Text rather than four numbers: `events.SOURCES` is a schema
        # value that can grow (docs/02 §8), and a column per Desktop would
        # make every such growth a Database migration. `DESKTOP_1:3` reads at
        # a glance and costs one column forever.
        "Desktops Reporting": {"rich_text": {}},
        # Events in this run whose `source`/`role` pair contradicts docs/02
        # §8's table. `validate_event()` checks the two fields independently
        # and never the pair, so a hand-written or restored Event can say it
        # came from DESKTOP_1 and did the CMO's work — and this row would
        # then carry an `Owner` and a `Source` pointing at different
        # Desktops with nothing flagging it.
        #
        # Not an input to `Overall`: it is a data-integrity fact about the
        # Events, not a failed pipeline step, and the verdict's rule is that
        # any future input has to earn a column before it earns a verdict.
        "Role Mismatches": {"number": {}},
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
    # `NOTION_OPS_RUNS_DATABASE_ID` is set and the database it names answers.
    # There is nothing to create, and the three values above would all tell
    # the operator to create it (C114).
    ALREADY_CREATED = "ALREADY_CREATED"
    # The variable is set and the database it names does **not** answer. Not
    # the same as unset: unset means "decide whether you want a Dashboard",
    # this means "the one you have is pointed somewhere broken".
    CONFIGURED_BUT_UNREACHABLE = "CONFIGURED_BUT_UNREACHABLE"
    # The variable is set, the database answers, and it is not an OPS_RUNS —
    # PROJECTS being the overwhelmingly likely one. Reachable and wrong is
    # not the same as unreachable, and the difference matters: the repair for
    # every other answer here would write OPS_RUNS' columns into it.
    CONFIGURED_TO_THE_WRONG_DATABASE = "CONFIGURED_TO_THE_WRONG_DATABASE"


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
    # `OPS_RUNS` columns the schema is missing, when the database was
    # inspected (readiness ALREADY_CREATED). Empty both when nothing is
    # missing and when nothing was inspected — `readiness` is what tells
    # those apart, and only ALREADY_CREATED means this field was computed.
    #
    # Defaulted so that every existing construction of this dataclass — in
    # this module and in tests — keeps working unchanged.
    ops_runs_missing_properties: tuple[str, ...] = ()


def _page_title(page: Mapping[str, Any]) -> str:
    for value in (page.get("properties") or {}).values():
        if value.get("type") == "title":
            parts = value.get("title") or []
            text = "".join(part.get("plain_text", "") for part in parts)
            if text:
                return text
    return "(untitled)"


def diagnose_dashboard_bootstrap(
    client: NotionClient, *, ops_runs_client: NotionClient | None = None
) -> BootstrapDiagnosis:
    """Work out, from the real Workspace, whether the OPS_* databases can be
    created — and if not, exactly what the operator has to do.

    Read-only: this inspects the reference database's parent and the pages
    the integration can see. It never creates a Page or a Database, and it
    never raises for an unusable workspace — an unusable workspace IS the
    answer it is meant to report.

    `ops_runs_client` — a client bound to `NOTION_OPS_RUNS_DATABASE_ID`, when
    a deployment has set it. **Omitted, this function behaves exactly as it
    did**, which is why it is optional: every existing caller and test keeps
    its answer.

    Why it was added (C114). Measured against the live workspace: `OPS_RUNS`
    existed, its 22 columns matched `DASHBOARD_DATABASES[OPS_RUNS]` name for
    name and type for type, `NOTION_OPS_RUNS_DATABASE_ID` named it, and six
    rows had been written into it through `record_run()`. This function
    answered `NEEDS_PARENT_CHOICE` and `init_notion.py` printed its
    `required_action` verbatim under **다음 할 일**:

        "... the creation step is yours to perform. Then set
         NOTION_OPS_RUNS_DATABASE_ID."

    Both halves were already done. The instruction is not merely stale — it
    is *irreversible* if followed: `bootstrap_dashboard_databases()` creates
    unconditionally, this module has no delete path by design, and the result
    is two databases named OPS_RUNS with nothing able to say which one the
    variable points at. The diagnosis reached that answer honestly, because
    it never looked at the database it was diagnosing. This gives it eyes.
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

    # Asked here, after `parent` and `search_pages()`, and not at the top of
    # the function. Two reasons, both about not printing something untrue:
    #
    #  - The unreachable-reference early return above must keep winning. If
    #    the token cannot read PROJECTS, that is the thing to fix first, and
    #    "your OPS_RUNS is fine" would be an answer to a question nobody has.
    #  - Every field below is filled from calls that have now actually
    #    happened. Returning early would leave `search_available=False` on a
    #    workspace that searches perfectly well, and `init_notion.py` prints
    #    that field as "이 integration은 Workspace 검색 권한이 없어" — a
    #    fabricated permissions problem, which is the class of bug this whole
    #    change exists to remove.
    #
    # The cost is the search call, which this function was making anyway.
    if ops_runs_client is not None:
        try:
            # The parse is inside the `try`, not after it, and that placement
            # is measured rather than tidy. `get_database_schema()` returns
            # `database.get("properties", {})`, and `.get(k, {})` yields
            # **None** — not `{}` — when Notion sends `"properties": null`
            # explicitly. `None.items()` is an AttributeError, raised past
            # this function's stated contract that it never raises for an
            # unusable workspace. Injected both shapes and confirmed the
            # crash before moving these two lines in:
            #
            #     "properties": null            AttributeError: 'NoneType' ... 'items'
            #     {"Run ID": "not-a-dict"}      AttributeError: 'str' ... 'get'
            #
            # A malformed response from Notion is exactly when an operator
            # runs the diagnostic, so it is the worst possible moment for the
            # diagnostic to be the thing that breaks. Same family as the
            # null-properties crash this repository already fixed once in
            # `notion.bootstrap`.
            schema = ops_runs_client.get_database_schema()
            present = {
                name: definition.get("type") for name, definition in schema.items()
            }
        except Exception as exc:  # noqa: BLE001  (진단은 절대 실패하지 않는다)
            return BootstrapDiagnosis(
                readiness=BootstrapReadiness.CONFIGURED_BUT_UNREACHABLE,
                reference_parent_type=parent_type,
                resolved_parent_page_id=resolved_parent_page_id,
                hostable_pages=hostable,
                search_available=search_available,
                required_action=(
                    f"NOTION_OPS_RUNS_DATABASE_ID is set, but the database it "
                    f"names could not be read: {exc}. Do NOT create a new "
                    "OPS_RUNS to get past this — that leaves two, and nothing "
                    "here can delete either. Check that the id is the right "
                    "one and that the database is still shared with this "
                    "integration (Share -> Connections). Until it answers, "
                    "every run records the Dashboard step as failed rather "
                    "than skipped."
                ),
            )

        # Is this an OPS_RUNS at all? Asked before anything is recommended,
        # because of what the recommendation below is.
        #
        # This check exists because the fix above created a worse bug than
        # the one it removed, and it was caught by asking C82's question of
        # it — "what does my own change make possible?" — against the live
        # API rather than in the abstract.
        #
        # Measured: point NOTION_OPS_RUNS_DATABASE_ID at the PROJECTS id (one
        # transposed variable in a shell profile — the single most likely
        # operator typo, and `NotionConfig` cannot tell the two ids apart)
        # and the database answers perfectly. Every one of the 22 columns is
        # "missing", so the branch below tells the operator to run
        # `bootstrap_dashboard_properties()` "against a client bound to this
        # database" — which would add 22 OPS_RUNS columns to the live
        # PROJECTS database. docs/13 §3-⑧ already warns that that command
        # has no way to check what it is bound to; before this change nothing
        # pointed at it, and the fix above started pointing at it.
        #
        # Notion gives every database exactly one Title, and the Title is the
        # one property that cannot be added later (`_bootstrap_title_property`
        # renames instead, for that reason). So it identifies the database:
        #
        #     "Run ID"  -> an OPS_RUNS this code has already shaped
        #     "Name"    -> a fresh database, Notion's default, step ⑧-4's case
        #     anything  -> some other database. PROJECTS' Title is "Project".
        #
        # A response with no Title at all is not treated as wrong — that
        # shape should not occur, and guessing "wrong database" from an
        # unexpected response would refuse a correct setup.
        title_property = next(
            (name for name, type_ in present.items() if type_ == "title"), None
        )
        if title_property is not None and title_property not in (RUN_ID_PROPERTY, "Name"):
            return BootstrapDiagnosis(
                readiness=BootstrapReadiness.CONFIGURED_TO_THE_WRONG_DATABASE,
                reference_parent_type=parent_type,
                resolved_parent_page_id=resolved_parent_page_id,
                hostable_pages=hostable,
                search_available=search_available,
                required_action=(
                    "NOTION_OPS_RUNS_DATABASE_ID names a database that answers, "
                    f"but its Title column is {title_property!r} — an OPS_RUNS "
                    f"has {RUN_ID_PROPERTY!r}, and a brand-new one has 'Name'. "
                    "This is some other database, most likely PROJECTS. Do NOT "
                    "run bootstrap_dashboard_properties() against it: that "
                    "command cannot tell what it is bound to and would add the "
                    "OPS_RUNS columns to whatever this is. Point the variable "
                    "at the right id, or create OPS_RUNS if there is none "
                    "(docs/13_NOTION_ENVIRONMENT_SETUP.md step 8)."
                ),
            )

        # Name and type both, because a column of the right name and the
        # wrong type is rejected by the same 400 as a missing one, and an
        # operator who built the database by hand from docs/13 step 8 is
        # exactly who gets the type wrong.
        missing = tuple(
            name
            for name, definition in DASHBOARD_DATABASES[OPS_RUNS].items()
            if present.get(name) != next(iter(definition))
        )
        if missing:
            action = (
                "OPS_RUNS already exists and NOTION_OPS_RUNS_DATABASE_ID "
                "names it — there is nothing to create, and creating one "
                f"would leave two. But {len(missing)} column(s) the code "
                f"writes are missing or the wrong type: {', '.join(missing)}. "
                "Every run is rejected with a 400 until they exist. Run "
                "bootstrap_dashboard_properties() against a client bound to "
                "this database (docs/13_NOTION_ENVIRONMENT_SETUP.md step 8) — "
                "it only adds what is absent and never reshapes a column."
            )
        else:
            action = (
                "Nothing to do. OPS_RUNS exists, NOTION_OPS_RUNS_DATABASE_ID "
                "names it, and every column the code writes is present with "
                "the right type. Do NOT run bootstrap_dashboard_databases() — "
                "it creates unconditionally and this module has no delete "
                "path, so a second run leaves two databases named OPS_RUNS."
            )
        return BootstrapDiagnosis(
            readiness=BootstrapReadiness.ALREADY_CREATED,
            reference_parent_type=parent_type,
            resolved_parent_page_id=resolved_parent_page_id,
            hostable_pages=hostable,
            search_available=search_available,
            required_action=action,
            ops_runs_missing_properties=missing,
        )

    if resolved_parent_page_id is not None:
        return BootstrapDiagnosis(
            readiness=BootstrapReadiness.READY,
            reference_parent_type=parent_type,
            resolved_parent_page_id=resolved_parent_page_id,
            hostable_pages=hostable,
            search_available=search_available,
            required_action=(
                "The workspace is ready, but nothing creates the OPS_* "
                "databases on its own: no entrypoint in this repository calls "
                "bootstrap_dashboard_databases(), so this command diagnoses "
                "and stops. To finish, either run that function yourself "
                "against this client, or create the OPS_RUNS database by hand "
                "in the Page above. Either way, set "
                "NOTION_OPS_RUNS_DATABASE_ID to its id — until that variable "
                "is set, Dashboard recording is skipped on every run. "
                "The exact steps, including the one-liner, are in "
                "docs/13_NOTION_ENVIRONMENT_SETUP.md step 8."
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
                "Choose one and pass its page_id as parent_page_id to "
                "bootstrap_dashboard_databases() (choosing the Page is an "
                "operator decision, not this code's) — and note that no "
                "entrypoint here runs that function, so the creation step is "
                "yours to perform. Then set NOTION_OPS_RUNS_DATABASE_ID. "
                "The exact steps are in docs/13_NOTION_ENVIRONMENT_SETUP.md "
                "step 8."
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
            "workspace root, and creating a Page is out of scope. That only clears "
            "the prerequisite — creating the OPS_* databases and setting "
            "NOTION_OPS_RUNS_DATABASE_ID is still a step no command here performs. "
            "The exact steps are in docs/13_NOTION_ENVIRONMENT_SETUP.md step 8."
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

    `only` omitted -> **`CONTRACTED_DATABASES`**, not every schema in
    `DASHBOARD_DATABASES`. The default used to be all five, and the two are
    not the same thing: docs/14 §1 names the Operational Projection as
    "Notion (PROJECTS / OPS_RUNS)", so four of the five schemas here have no
    place in the model and no code that writes them. Creating them left four
    permanently-empty databases in a real workspace, undoable by this module
    (it has no delete path, by design), with a prose warning in docs/13 as
    the only thing in the way.

    Defaulting to the contract does not remove the capability — `only=` still
    takes any name in `DASHBOARD_DATABASES`. It changes which choice a
    caller has to make deliberately, and puts the irreversible one on that
    side.
    """
    resolved_parent_page_id = (
        parent_page_id if parent_page_id is not None else resolve_parent_page_id(client)
    )

    names = list(only) if only is not None else list(CONTRACTED_DATABASES)
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


def bootstrap_dashboard_properties(client: NotionClient) -> BootstrapResult:
    """Add whichever `OPS_RUNS` properties are missing from an **existing**
    database, leaving every existing one untouched — C36.

    `client` must be bound to the OPS_RUNS database id (the one in
    `NOTION_OPS_RUNS_DATABASE_ID`), not the PROJECTS one. Nothing here can
    check that: both are databases and both answer `retrieve_database`. The
    only consequence of getting it wrong is thirteen unwanted properties on
    PROJECTS, which is why the caller is the operator running a documented
    command rather than the Runtime.

    **Why this exists.** `bootstrap_dashboard_databases()` creates OPS_RUNS
    from `DASHBOARD_DATABASES`, and that schema has grown: 13 properties
    through C31, 15 in C32 (`Transport Blocked`, `Notion Skipped`), 17 in
    C33 (`Notion Unreadable`, `Notion Queued`). Every one of those was added
    because the Dashboard could not otherwise say something true. But an
    operator who created the database before a widening has a database that
    no longer matches, and `record_run()` then sends a property Notion has
    never heard of — a 400 on every run, forever.

    That failure is safe but not free: `record_run()` returns FAILED, the row
    goes to `dashboard_pending.json`, and `app/runner.py` logs
    `DASHBOARD DRAIN_PENDING … REASON <Notion's own message>`. No data is
    lost and the reason is legible (C32 §11 measured this). What was missing
    was the way *out* — the operator could read exactly which property Notion
    rejected and had no command to add it.

    Deliberately not wired to any entrypoint, for the reason
    `test_the_setup_cli_does_not_create_anything_from_the_diagnosis` pins:
    `init_notion.py` must not mutate the Dashboard side of a real Workspace
    on its own. This is a capability the operator invokes, documented in
    docs/13 §3-⑧.

    The logic is `notion.bootstrap`'s, not a second copy of it. Two modules
    asking "which properties are missing" in two different ways is how the
    two answers drift; `diff_properties()` and `_bootstrap_title_property()`
    grew parameters in C36 precisely so this could reuse them.
    """
    current_properties = client.get_database_schema()

    # `Run ID` is OPS_RUNS' Title, and Notion will not let a second Title be
    # created — so a hand-made database still carrying Notion's default
    # `Name` can only be fixed by renaming. That is the likely state for
    # anyone who followed docs/13's "or create it by hand in the Page above".
    title_report = _bootstrap_title_property(
        client, current_properties, title_property=RUN_ID_PROPERTY
    )
    if title_report.outcome is PropertyOutcome.RENAMED:
        # Re-read for `bootstrap_database()`'s reason: the rename just
        # mutated the live schema, and diffing against the pre-rename
        # snapshot would treat the old name as an existing property.
        current_properties = client.get_database_schema()

    to_create, decided = diff_properties(
        current_properties,
        targets=DASHBOARD_DATABASES[OPS_RUNS],
        title_property=RUN_ID_PROPERTY,
    )
    reports = [title_report, *decided]

    if to_create:
        client.create_database_properties(to_create)
        reports.extend(
            PropertyBootstrapReport(name, PropertyOutcome.CREATED) for name in to_create
        )

    order = {name: index for index, name in enumerate(DASHBOARD_DATABASES[OPS_RUNS])}
    reports.sort(key=lambda report: order[report.name])
    return BootstrapResult(reports=tuple(reports))


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


# The two Backup outcomes that need nobody. Written as the healthy set
# rather than as a list of unhealthy ones because the previous code did the
# opposite and got it wrong in a way nothing could see: it warned on
# `"BACKUP_REVIEW"`, a string `backup.result.BackupStatus` has never had —
# docs/08 §34's optional state is spelled `BACKUP_REVIEW_REQUIRED`, so even
# adding that state would not have made the branch fire. A closed set of
# *healthy* values cannot fail that way: a status this module has never
# heard of reads as "needs a human", which is the safe direction.
_BACKUP_NEEDS_NOBODY = ("BACKUP_SUCCESS", "BACKUP_NOT_REQUIRED")


def count_blocked_intake(intake_summary: Any) -> int:
    """Files `transport/` is holding that the next run will hold again.

    `run_intake()` sorts what it did not promote into five buckets, and they
    are not the same kind of thing:

        skipped_not_stable        arrived seconds ago; the next run takes it
        skipped_already_present   the same Event is already downstream
        skipped_invalid           not parseable — re-judged, and refused,
                                  on every run from now on
        skipped_incomplete        `.tmp-…json` residue from a writer that
                                  died; nothing on disk ever removes it
        failed                    the move itself raised

    Only the last three are counted. The first two are the pipeline working;
    counting them would put a number on the Dashboard that a healthy system
    cannot clear, which is the standing-alert-with-no-explanation shape
    `app.desktop_activity.IntakeBacklog` was written to remove — same
    reasoning, same three-way split, applied one layer up.

    `failed` is included even though an `os.replace` failure *can* be
    transient: it means an Event that should have moved did not, and the
    Runner already treats it as a metric worth recording in the manifest.
    A false WARN that clears on the next run costs one glance; the silence
    it replaces cost the whole inbound path.

    Why this exists at all — measured, before it did, on a run holding ten
    unparseable files, one staging file and one failed move:

        Transport Moved 0   Accepted 0   Rejected 0   Overall OK

    which is byte-for-byte the row a completely healthy idle Sunday
    produces. The absence of data read as health.
    """
    return (
        len(intake_summary.skipped_invalid)
        + len(intake_summary.skipped_incomplete)
        + len(intake_summary.failed)
    )


def _overall_status(
    *,
    collector_failed: int,
    rejected: int,
    transport_blocked: int,
    scheduler_status: str,
    backup_status: str,
    notion_retried: int,
    notion_unreadable: int,
    notion_queued: int,
    failed_steps: int = 0,
    critical_failed_steps: int = 0,
) -> str:
    """Single at-a-glance verdict for one run.

    Deliberately derived only from statuses the Runner already produced —
    no new health policy is invented here. FAIL when a stage reported an
    outright failure; WARN when nothing failed outright but something needs
    a human look (rejected events, Events that did not reach Notion, Backup
    not successful); else OK.

    Two of those three WARN causes were in the sentence above and in no
    branch below, so the verdict disagreed with its own description.
    Measured against real result objects:

        8 Events REJECTED by the Collector          Rejected 8   Overall OK
        5 Events that never reached Notion (401)    Retried  5   Overall OK

    Both rows say the run was fine while naming, in the very next column,
    the number that says it was not. `Overall` is the column an operator
    sorts and filters a Notion view by — it is the only one a glance reads —
    so a wrong verdict there is worse than no verdict.

    Every input is also a column of the same row (`Rejected`, `Failed`,
    `Notion Retried`, `Backup Status`, `Scheduler Status`, `Failed Steps`).
    That is a deliberate constraint, not a coincidence: a WARN whose cause
    is not visible beside it tells an operator to go looking with nothing to
    look at. Any future input to this verdict has to earn a column first.

    **This verdict and the Run Manifest's are two answers to one question
    about one run, and they must not contradict each other** (C37). They are
    not the same verdict — the Dashboard is a row an operator scans, so it
    also warns about per-row facts that do not degrade a run (8 rejected
    Events, a queue that is not draining). The relation is one-directional
    and exact, against docs/14 §4's SUCCESS / DEGRADED / FAILED:

        Dashboard OK       => manifest SUCCESS      (never quieter)
        manifest DEGRADED  => WARN or FAIL, never OK
        Dashboard FAIL    <=> manifest FAILED       (a CRITICAL step failed)

    Both directions were broken, in exactly the two ways docs/14 §4 warns
    about ("DEGRADED를 SUCCESS로 접으면 실제 고장이 숨고, FAILED로 접으면
    늑대 소년이 되어 아무도 안 본다"). Measured:

        collector failed=1     Dashboard FAIL   manifest SUCCESS  / exit 0
        late_update FAILED     Dashboard OK     manifest DEGRADED / exit 3
        monthly     FAILED     Dashboard OK     manifest DEGRADED / exit 3

    The first is the wolf: `failed` counts Event *files*, and `app/runner.py`
    states beside the number that it is "not a component failure — docs/03
    §53 makes per-file isolation the design, and one malformed Event must
    not make an ordinary run look broken". The Dashboard escalated it to the
    same level as a lost Daily Close. It is now WARN — the level its sibling
    `rejected` has always had, and the level "a person should look at this
    row" means.

    The last two are the hidden breakage, and they are structural rather
    than a slip: `late_update` and `monthly` are the two steps that can
    record FAILED *without* raising, and neither had a column, so neither
    could reach this function at all. `failed_steps` fixes the class, not
    the two instances — a step added later is folded in whether or not
    anyone remembers to give it a number of its own.

    `scheduler_status` and `backup_status` still appear in the FAIL branch
    below even though a failed `daily`/`backup` component would also arrive
    in `critical_failed_steps`. Two derivations of one fact that must agree,
    kept because they come from different places: the count comes from the
    manifest recorder, the two statuses from the result objects themselves,
    and a caller that has one but not the other still gets a true verdict.
    """
    if (
        critical_failed_steps > 0
        or scheduler_status == "FAILED"
        or backup_status == "BACKUP_FAILED"
    ):
        return "FAIL"
    if (
        collector_failed > 0
        or failed_steps > 0
        or rejected > 0
        or transport_blocked > 0
        or notion_retried > 0
        or notion_unreadable > 0
        or notion_queued > 0
        or backup_status not in _BACKUP_NEEDS_NOBODY
    ):
        return "WARN"
    return "OK"


def build_ops_run_properties(
    *,
    run_id: str,
    run_at: datetime,
    transport_moved: int,
    transport_blocked: int,
    accepted: int,
    duplicate: int,
    rejected: int,
    failed: int,
    scheduler_status: str,
    generated_days: int,
    reused_days: int,
    backup_status: str,
    deleted_files: int,
    notion_synced: int,
    notion_skipped: int,
    notion_retried: int,
    notion_unreadable: int,
    notion_queued: int,
    desktops_reporting: str = "",
    role_mismatches: int = 0,
    failed_steps: Sequence[str] = (),
    critical_failed_steps: Sequence[str] = (),
) -> dict[str, Any]:
    # Names, not `ComponentResult`s. `notion` may import only `events`
    # (the layering invariant), so the severity split has to arrive already
    # made — `app/runner.py` owns `_SEVERITY` and is the only place that
    # can make it. `critical_failed_steps` is a subset of `failed_steps`;
    # nothing here enforces that, and nothing needs to: the FAIL branch
    # reads one and the WARN branch the other, so a caller that got the
    # subset wrong can only make the verdict too loud, never too quiet.
    failed_step_names = ", ".join(failed_steps)
    return {
        "Run ID": _title(run_id),
        "Run At": _date(run_at.isoformat(timespec="seconds")),
        "Transport Moved": _number(transport_moved),
        "Transport Blocked": _number(transport_blocked),
        "Accepted": _number(accepted),
        "Duplicate": _number(duplicate),
        "Rejected": _number(rejected),
        "Failed": _number(failed),
        "Scheduler Status": _select(scheduler_status),
        "Generated Days": _number(generated_days),
        "Reused Days": _number(reused_days),
        "Backup Status": _select(backup_status),
        "Deleted Files": _number(deleted_files),
        "Notion Synced": _number(notion_synced),
        "Notion Skipped": _number(notion_skipped),
        "Notion Retried": _number(notion_retried),
        "Notion Unreadable": _number(notion_unreadable),
        "Notion Queued": _number(notion_queued),
        "Failed Steps": _rich_text(failed_step_names),
        "Desktops Reporting": _rich_text(desktops_reporting),
        "Role Mismatches": _number(role_mismatches),
        "Overall": _select(
            _overall_status(
                collector_failed=failed,
                rejected=rejected,
                transport_blocked=transport_blocked,
                scheduler_status=scheduler_status,
                backup_status=backup_status,
                notion_retried=notion_retried,
                notion_unreadable=notion_unreadable,
                notion_queued=notion_queued,
                failed_steps=len(failed_steps),
                critical_failed_steps=len(critical_failed_steps),
            )
        ),
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
    notion_unreadable: int = 0,
    notion_queued: int = 0,
    desktops_reporting: str = "",
    role_mismatches: int = 0,
    failed_steps: Sequence[str] = (),
    critical_failed_steps: Sequence[str] = (),
) -> DashboardResult:
    """Write one OPS_RUNS row for this execution. Never raises.

    `client` must be a NotionClient bound to the **OPS_RUNS** database id
    (not the PROJECTS one ExecutionPlanSync uses) — this writes operational
    run records, never Current State.

    CEO Decision ④: "Dashboard 기록 실패는 Runtime을 절대 중단시키면 안
    된다." Every failure path here returns a DashboardResult instead of
    propagating — including a missing/unconfigured client, which is simply
    SKIPPED_NOT_CONFIGURED rather than an error.

    `notion_unreadable` and `notion_queued` are the two Notion facts that
    are NOT derivable from `notion_sync_results`, and that is exactly why
    they are parameters rather than something computed here:

        notion_unreadable   an Event file, or a queued entry, that could not
                            be parsed. `app/runner.py` deliberately does not
                            fabricate a SyncResult for it — the `event_id`
                            is precisely what could not be read — so it is
                            in none of the three counts derived below.
        notion_queued       the retry queue's depth *after* the run. A
                            per-run result set cannot know it; only the
                            Runner, which just wrote the queue, can.

    Both default to 0 so every existing caller keeps working. That default
    is safe in a way the ones this module removed were not: it is the value
    for "this step did not run", not a mask over a renamed field. A caller
    that has the numbers and does not pass them is reporting a healthier
    run than happened, which is why `app/runner.py` passes both explicitly
    and a test asserts it does.

    `failed_steps` / `critical_failed_steps` are the same kind of fact one
    level up: which *steps* this run recorded as FAILED, and which of those
    were CRITICAL. They come from the manifest recorder rather than from any
    result object, because two of the nine steps can record FAILED without
    stopping the run and produce no result object this module ever sees
    (C37). Same defaulting reasoning, same test.
    """
    if client is None:
        return DashboardResult(
            outcome=DashboardOutcome.SKIPPED_NOT_CONFIGURED, run_id=run_id
        )

    try:
        # A partition, not two overlapping filters. `synced` used to be
        # "everything that is not a failure", which swept
        # NOTION_SKIPPED_OLD_EVENT — docs/04 §35's "적용하지 않았다" — in with
        # the writes. Measured: four Events, all of them skipped as older
        # than the row they would have overwritten, reported as
        # `Notion Synced: 4`. Zero writes reached Notion.
        #
        # A status this module does not recognise lands in `retried` rather
        # than being dropped, so `synced + skipped + retried` always equals
        # the number of Events the Sync step handled and the arithmetic on
        # the row closes. `retried` is the right side to fail towards: it is
        # the count that raises WARN, and an unrecognised sync status is a
        # thing a person should look at.
        written_statuses = ("NOTION_CREATED", "NOTION_UPDATED")
        skipped_statuses = ("NOTION_SKIPPED_OLD_EVENT",)
        statuses = [getattr(r.status, "value", "") for r in notion_sync_results]
        synced = sum(1 for s in statuses if s in written_statuses)
        skipped = sum(1 for s in statuses if s in skipped_statuses)
        retried = len(statuses) - synced - skipped
        # Direct attribute access, not `getattr(..., <default>)`.
        #
        # `app/runner.py` states the rule beside the sibling numbers it feeds
        # into the Run Manifest — "a default would only be able to hide the
        # day one is renamed — reporting 0 skipped files forever instead of
        # failing" — and every one of the Dashboard's own numbers was read
        # the way that comment forbids. Measured: a `RuntimeSummary` whose
        # `accepted` had been renamed produced `Accepted 0` for a run that
        # accepted 50, with `Overall OK`, silently, on every run after the
        # rename.
        #
        # `backup_entry.final_status` below was already direct, and
        # `test_a_malformed_result_object_fails_the_build_without_raising`
        # pins what that buys: the missing attribute lands in this `try`,
        # comes back as DashboardOutcome.FAILED with the AttributeError as
        # its reason, and the Runner logs it and queues nothing (properties
        # is None). A Dashboard that stops updating and says why beats one
        # that keeps updating with zeros.
        properties = build_ops_run_properties(
            run_id=run_id,
            run_at=run_at,
            transport_moved=len(intake_summary.moved),
            transport_blocked=count_blocked_intake(intake_summary),
            accepted=collector_summary.accepted,
            duplicate=collector_summary.duplicate,
            rejected=collector_summary.rejected,
            failed=collector_summary.failed,
            scheduler_status=getattr(scheduler_result.status, "value", str(scheduler_result.status)),
            # Direct, like every sibling — and unlike what these two used to
            # be. `generated_days` read `getattr(..., "generated_dates", ())`,
            # which is precisely the shape the comment above forbids: the day
            # the field is renamed, the column reports 0 forever instead of
            # failing. C39 renamed what that field *means* in this very
            # object, which is how close that already came.
            generated_days=len(scheduler_result.generated_dates),
            reused_days=len(scheduler_result.reused_dates),
            backup_status=getattr(
                backup_entry.final_status, "value", str(backup_entry.final_status)
            ),
            deleted_files=len(backup_entry.deleted_files),
            notion_synced=synced,
            notion_skipped=skipped,
            notion_retried=retried,
            notion_unreadable=notion_unreadable,
            notion_queued=notion_queued,
            # Where this run's Events came from, and whether any of them
            # claimed a role its Desktop does not own. Passed rather than
            # derived here for the same reason `notion_unreadable` and
            # `notion_queued` are: `app/runner.py` is the only place that
            # has read this run's Events, and re-reading `processed/` from
            # here would count every Event this project ever collected
            # instead of the ones this run handled.
            desktops_reporting=desktops_reporting,
            role_mismatches=role_mismatches,
            failed_steps=failed_steps,
            critical_failed_steps=critical_failed_steps,
        )
    except Exception as exc:  # noqa: BLE001  (CEO ④: Runtime을 절대 중단시키지 않는다)
        return DashboardResult(
            outcome=DashboardOutcome.FAILED, run_id=run_id, error=str(exc), properties=None
        )

    try:
        # Find-before-create, not create. `dashboard_pending.py`'s docstring
        # already promised "one Runner execution can never produce two
        # OPS_RUNS rows, whether it is recorded on the first attempt or the
        # tenth" — this is the line that makes that true. A write that
        # reaches Notion but surfaces as an exception (response lost) used to
        # be retried into a second row for the same run_id.
        page = client.find_or_create_by_title(
            property_name=RUN_ID_PROPERTY, value=run_id, properties=properties
        )
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
