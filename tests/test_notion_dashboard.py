"""Notion Operations Dashboard tests (CEO Decision 4).

Mock transport only — no real Notion workspace is contacted anywhere here.
"""

import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
# `init_notion.py` lives at the repository root, beside `src/`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from notion import (  # noqa: E402
    TARGET_PROPERTIES,
    DASHBOARD_DATABASES,
    RUN_ID_PROPERTY,
    PropertyOutcome,
    bootstrap_dashboard_properties,
    OPS_BACKUP,
    OPS_NOTION_SYNC,
    OPS_READINESS,
    OPS_RISK,
    OPS_RUNS,
    BootstrapReadiness,
    DashboardBootstrapPartialError,
    DashboardOutcome,
    DashboardParentError,
    InMemoryNotionTransport,
    NotionAPIError,
    NotionClient,
    NotionTransport,
    SyncResult,
    SyncStatus,
    bootstrap_dashboard_databases,
    build_ops_run_properties,
    diagnose_dashboard_bootstrap,
    record_run,
    resolve_parent_page_id,
)
from notion.transport import RealNotionTransport  # noqa: E402
from notion.dashboard_pending import (  # noqa: E402
    DashboardPendingError,
    drain_pending,
    load_pending,
    remove_pending,
    save_pending,
)


class _FakeIntake:
    """Stands in for `transport.intake.IntakeSummary`.

    Carries every field the real one does, not just the fields the Dashboard
    happens to read today. `DoublesMatchTheRealResultObjectsTests` below
    keeps that true: a double narrower than the thing it doubles is how a
    test suite goes on passing after the real object has moved.
    """

    def __init__(
        self,
        moved=(),
        skipped_not_stable=(),
        skipped_already_present=(),
        skipped_invalid=(),
        skipped_incomplete=(),
        failed=(),
    ):
        self.moved = moved
        self.skipped_not_stable = skipped_not_stable
        self.skipped_already_present = skipped_already_present
        self.skipped_invalid = skipped_invalid
        self.skipped_incomplete = skipped_incomplete
        self.failed = failed


class _FakeCollector:
    """Stands in for `collector.runtime.RuntimeSummary`."""

    def __init__(self, accepted=0, duplicate=0, rejected=0, failed=0, files=()):
        self.accepted = accepted
        self.duplicate = duplicate
        self.rejected = rejected
        self.failed = failed
        self.files = files


class _FakeStatus:
    def __init__(self, value):
        self.value = value


class _FakeScheduler:
    """Stands in for `scheduler.result.SchedulerRunResult`.

    Every field, not just the ones `record_run()` happens to read today —
    `DoublesMatchTheRealResultObjectsTests` holds it to that. It used to
    carry two of five, and the three it was missing included `reused_dates`,
    which C39 added and C42 gave a column: the real object grew and this
    double did not, which is the exact drift that class exists to catch and
    was not catching, because it had no test for this double at all.
    """

    def __init__(
        self,
        status="COMPLETED",
        generated_dates=(),
        reused_dates=(),
        failed_date=None,
        error=None,
    ):
        self.status = _FakeStatus(status)
        self.generated_dates = generated_dates
        self.reused_dates = reused_dates
        self.failed_date = failed_date
        self.error = error


class _FakeBackup:
    """Stands in for `backup.log.BackupLogEntry`. Same rule as above."""

    def __init__(
        self,
        status="BACKUP_SUCCESS",
        run_id="R",
        backup_start=None,
        source="s",
        changed_files=(),
        deleted_files=(),
        commit_hash=None,
        push_result=None,
        backup_end=None,
    ):
        self.final_status = _FakeStatus(status)
        self.run_id = run_id
        self.backup_start = backup_start
        self.source = source
        self.changed_files = changed_files
        self.deleted_files = deleted_files
        self.commit_hash = commit_hash
        self.push_result = push_result
        self.backup_end = backup_end


def _sync(status, event_id="E1"):
    return SyncResult(status=status, event_id=event_id, project_id="P1")


class DashboardSchemaTests(unittest.TestCase):
    def test_all_five_databases_are_defined(self):
        self.assertEqual(
            set(DASHBOARD_DATABASES),
            {OPS_RUNS, OPS_BACKUP, OPS_NOTION_SYNC, OPS_RISK, OPS_READINESS},
        )

    def test_every_database_has_exactly_one_title_property(self):
        for name, props in DASHBOARD_DATABASES.items():
            with self.subTest(database=name):
                titles = [k for k, v in props.items() if "title" in v]
                self.assertEqual(len(titles), 1)

    def test_no_database_uses_the_non_creatable_status_type(self):
        # Same constraint notion/bootstrap.py documents: the Notion API
        # cannot create a "status"-type property, only Select.
        for name, props in DASHBOARD_DATABASES.items():
            for prop_name, definition in props.items():
                with self.subTest(database=name, prop=prop_name):
                    self.assertNotIn("status", definition)


class ARenameInAContractedDatabaseIsPermanentTests(unittest.TestCase):
    """The drift no cross-check can see.

    `DashboardSchemaMappingTests` holds `record_run()`'s payload and the
    `OPS_RUNS` schema to each other, and `ProjectsSchemaMappingTests` does
    the same for PROJECTS. Both compare **two derived sides**, so a rename
    applied to both at once passes cleanly — and that is exactly how a
    rename gets made, because a rename that only touched one side would fail
    the first test anyone ran.

    In a live workspace it is not clean at all. `create_database()` runs
    once. After that, renaming a property in this dict does not rename
    anything in Notion — it creates a **new** property. The old one stays,
    holding whatever it last held, with nothing writing to it, and no code
    path here can remove it: `bootstrap_dashboard_properties()` adds and
    never deletes, deliberately ("없는 Property만 추가하고 기존 Property는
    정의째 그대로 둔다"), and `rename_property()` exists only for the one
    case `notion/bootstrap.py` uses it for — Notion's default `Name` title.

    An operator then has two columns, one of them frozen at the moment of
    the rename, and every View built on the old name silently stops moving.

    These two databases are the ones docs/14 §1 contracts and the ones an
    operator actually creates, so the recorded names below are a real
    workspace's real columns. A Sprint that renames one has to say so here,
    which is the whole point.
    """

    #: `notion.dashboard.DASHBOARD_DATABASES["OPS_RUNS"]`, by name and type.
    OPS_RUNS_COLUMNS = {
        "Run ID": "title",
        "Run At": "date",
        "Transport Moved": "number",
        "Transport Blocked": "number",
        "Accepted": "number",
        "Duplicate": "number",
        "Rejected": "number",
        "Failed": "number",
        "Scheduler Status": "select",
        "Generated Days": "number",
        "Reused Days": "number",
        "Backup Status": "select",
        "Deleted Files": "number",
        "Notion Synced": "number",
        "Notion Skipped": "number",
        "Notion Retried": "number",
        "Notion Unreadable": "number",
        "Notion Queued": "number",
        "Failed Steps": "rich_text",
        "Desktops Reporting": "rich_text",
        "Role Mismatches": "number",
        "Overall": "select",
    }

    #: `notion.bootstrap.TARGET_PROPERTIES` — the PROJECTS database, written
    #: on **every Event** rather than once per run.
    PROJECTS_COLUMNS = {
        "Project": "title",
        "Project ID": "rich_text",
        "Owner": "select",
        "Source": "select",
        "Status": "select",
        "Current Milestone": "rich_text",
        "Blocker": "rich_text",
        "Last Updated": "date",
        "Completed Date": "date",
        "Last Event ID": "rich_text",
        "Last Event Type": "select",
    }

    @staticmethod
    def _typed(properties):
        return {name: next(iter(spec)) for name, spec in properties.items()}

    def test_the_ops_runs_columns_are_the_recorded_ones(self):
        self.assertEqual(
            self._typed(DASHBOARD_DATABASES[OPS_RUNS]), self.OPS_RUNS_COLUMNS
        )

    def test_the_projects_columns_are_the_recorded_ones(self):
        from notion.bootstrap import TARGET_PROPERTIES

        self.assertEqual(self._typed(TARGET_PROPERTIES), self.PROJECTS_COLUMNS)

    def test_no_recorded_ops_runs_column_has_lost_its_name(self):
        """Stated separately from the equality so the failure says *rename*.

        An addition is recoverable — `bootstrap_dashboard_properties()` adds
        it to a live database and docs/13 ⑧-4 is the runbook. A rename is
        not: the old column stays and nothing here can take it out.
        """
        current = self._typed(DASHBOARD_DATABASES[OPS_RUNS])
        missing = sorted(set(self.OPS_RUNS_COLUMNS) - set(current))

        self.assertEqual(
            missing,
            [],
            f"OPS_RUNS no longer declares {missing} — a database an operator "
            "already created keeps that column forever, frozen at its last "
            "value, and every View built on it stops moving",
        )

    def test_no_recorded_projects_column_has_lost_its_name(self):
        from notion.bootstrap import TARGET_PROPERTIES

        current = self._typed(TARGET_PROPERTIES)
        missing = sorted(set(self.PROJECTS_COLUMNS) - set(current))

        self.assertEqual(missing, [], f"PROJECTS no longer declares {missing}")

    def test_no_type_changed_under_an_existing_name(self):
        """Worse than a rename, and quieter: the Notion API cannot change a
        property's type in place, so the write fails against the real
        database while `InMemoryNotionTransport` — which
        `TestDoubleFidelityTests` records as accepting a wrong type — takes
        it happily."""
        from notion.bootstrap import TARGET_PROPERTIES

        for label, recorded, current in (
            ("OPS_RUNS", self.OPS_RUNS_COLUMNS, self._typed(DASHBOARD_DATABASES[OPS_RUNS])),
            ("PROJECTS", self.PROJECTS_COLUMNS, self._typed(TARGET_PROPERTIES)),
        ):
            for name, kind in recorded.items():
                if name not in current:
                    continue
                with self.subTest(database=label, property=name):
                    self.assertEqual(current[name], kind)

    def test_the_growth_history_and_the_record_agree(self):
        """docs/13 ⑧-4's history line names the columns each Sprint added,
        and `OpsRunsColumnHistoryIsCurrentTests` pins its last *count*. This
        pins the other half: every column that line names is still a column.

        A rename would leave the history naming something that no longer
        exists, which turns the recovery runbook into instructions for
        building the wrong database.
        """
        import re

        doc = (
            Path(__file__).resolve().parents[1]
            / "docs"
            / "13_NOTION_ENVIRONMENT_SETUP.md"
        ).read_text(encoding="utf-8")
        # The history is one Markdown paragraph on one line. Bounded to that
        # line rather than to a character count: a fixed window ran past the
        # end into ⑧-4's prose about Notion's default `Name` title, which is
        # not a column and never was — a parse that reads the next paragraph
        # reports on the wrong subject.
        start = doc.index("OPS_RUNS` 스키마는 자라 왔다")
        history = doc[start : doc.index(chr(10), start)]
        named = set(re.findall(r"`([A-Z][A-Za-z ]+)`", history))

        self.assertGreater(len(named), 6, "the history line stopped naming columns")
        for column in sorted(named):
            with self.subTest(column=column):
                self.assertIn(column, self.OPS_RUNS_COLUMNS)


class DashboardBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.transport = InMemoryNotionTransport()
        self.client = NotionClient(transport=self.transport, database_id="unused")

    def test_bootstrap_defaults_to_the_contracted_database_only(self):
        """C33 §2. The default used to be every schema in
        `DASHBOARD_DATABASES` — five databases, four of which no code writes
        and docs/14 §1 does not name.

        docs/14 §1 fixes the Operational Projection as
        "Notion (PROJECTS / OPS_RUNS)". `PROJECTS` belongs to `notion.sync`;
        the one this module owns is `OPS_RUNS`. So the default was creating
        four databases outside the contract, in a real workspace, with no
        delete path to undo them — and BACKLOG A-16 had recorded the
        question as *undecided* since C10 while docs/14 (written later)
        decided it.
        """
        result = bootstrap_dashboard_databases(self.client, parent_page_id="page-1")

        self.assertEqual(list(result.created), [OPS_RUNS])
        self.assertEqual(len(self.transport.created_databases), 1)
        self.assertIsNotNone(result.database_id(OPS_RUNS))

    def test_the_default_is_the_contract_not_a_hardcoded_name(self):
        """Pinned against the constant so the two cannot drift, and so that
        widening the contract is one edit rather than two."""
        from notion.dashboard import CONTRACTED_DATABASES

        result = bootstrap_dashboard_databases(self.client, parent_page_id="page-1")

        self.assertEqual(list(result.created), list(CONTRACTED_DATABASES))

    def test_every_contracted_name_has_a_schema(self):
        """The constant names databases; `DASHBOARD_DATABASES` defines them.
        A name in the first and not the second is a KeyError at setup time,
        in front of an operator, against a live workspace."""
        from notion.dashboard import CONTRACTED_DATABASES

        for name in CONTRACTED_DATABASES:
            with self.subTest(database=name):
                self.assertIn(name, DASHBOARD_DATABASES)

    def test_the_uncontracted_four_are_still_creatable_on_request(self):
        """Defaulting to the contract removes a default, not a capability.
        An operator or a future Sprint that decides to widen docs/14 §1 can
        still create them by asking."""
        result = bootstrap_dashboard_databases(
            self.client,
            parent_page_id="page-1",
            only=[OPS_BACKUP, OPS_NOTION_SYNC, OPS_RISK, OPS_READINESS],
        )

        self.assertEqual(len(result.created), 4)
        self.assertNotIn(OPS_RUNS, result.created)

    def test_the_four_uncontracted_schemas_are_kept_rather_than_deleted(self):
        """They are drafted intent, and deleting them would lose the design
        without recording it. What changed is that they are no longer
        *created* by default — see BACKLOG A-16."""
        from notion.dashboard import CONTRACTED_DATABASES

        uncontracted = set(DASHBOARD_DATABASES) - set(CONTRACTED_DATABASES)

        self.assertEqual(
            uncontracted, {OPS_BACKUP, OPS_NOTION_SYNC, OPS_RISK, OPS_READINESS}
        )

    def test_bootstrap_can_create_a_subset(self):
        result = bootstrap_dashboard_databases(
            self.client, parent_page_id="page-1", only=[OPS_RUNS]
        )

        self.assertEqual(list(result.created), [OPS_RUNS])
        self.assertEqual(len(self.transport.created_databases), 1)

    def test_created_database_carries_its_title_and_parent(self):
        bootstrap_dashboard_databases(self.client, parent_page_id="page-42", only=[OPS_RUNS])

        created = list(self.transport.created_databases.values())[0]
        self.assertEqual(created["title"], OPS_RUNS)
        self.assertEqual(created["parent_page_id"], "page-42")


class ParentPageResolutionTests(unittest.TestCase):
    """Reuse the existing Workspace structure: place the OPS_* databases in
    whatever Page already hosts the reference database, never a new Page.
    """

    def test_parent_page_is_derived_from_the_existing_database(self):
        transport = InMemoryNotionTransport(
            parent={"type": "page_id", "page_id": "company-ops-page"}
        )
        client = NotionClient(transport=transport, database_id="projects-db")

        self.assertEqual(resolve_parent_page_id(client), "company-ops-page")

    def test_workspace_root_database_has_no_reusable_parent_page(self):
        # The real PROJECTS database reports parent type "workspace": the
        # Notion API cannot create a database there, and creating a Page is
        # out of scope, so this must fail loudly rather than invent a home.
        transport = InMemoryNotionTransport()  # defaults to workspace root
        client = NotionClient(transport=transport, database_id="projects-db")

        with self.assertRaises(DashboardParentError):
            resolve_parent_page_id(client)

    def test_bootstrap_without_explicit_parent_uses_the_existing_page(self):
        transport = InMemoryNotionTransport(
            parent={"type": "page_id", "page_id": "company-ops-page"}
        )
        client = NotionClient(transport=transport, database_id="projects-db")

        bootstrap_dashboard_databases(client, only=[OPS_RUNS])

        created = list(transport.created_databases.values())[0]
        self.assertEqual(created["parent_page_id"], "company-ops-page")

    def test_bootstrap_at_workspace_root_refuses_instead_of_creating_a_page(self):
        transport = InMemoryNotionTransport()
        client = NotionClient(transport=transport, database_id="projects-db")

        with self.assertRaises(DashboardParentError):
            bootstrap_dashboard_databases(client)

        self.assertEqual(transport.created_databases, {})  # nothing created

    def test_explicit_parent_page_id_overrides_lookup(self):
        transport = InMemoryNotionTransport()  # workspace root, would raise
        client = NotionClient(transport=transport, database_id="projects-db")

        bootstrap_dashboard_databases(
            client, parent_page_id="explicit-page", only=[OPS_RUNS]
        )

        created = list(transport.created_databases.values())[0]
        self.assertEqual(created["parent_page_id"], "explicit-page")


def _page(page_id, title, parent_type):
    return {
        "id": page_id,
        "parent": {"type": parent_type},
        "properties": {
            "title": {"type": "title", "title": [{"plain_text": title}]}
        },
    }


class TheWireShapeOfEveryRequestTests(unittest.TestCase):
    """C49: what `RealNotionTransport` actually sends, checked without a
    network.

    Found by branch coverage — the six methods below are one line each and
    the suite had never executed any of them. That is exactly the code whose
    mistakes are invisible until a live Workspace answers 404 or 405: a verb,
    a path, and the shape of a body. `DashboardSchemaMappingTests` already
    pins the *properties* a row carries; nothing pinned the request that
    carries them.

    Everything here is asserted against a captured `urllib.request.Request`,
    so it needs no credentials — the last step the Dashboard is waiting on
    (BACKLOG A-8) is a Workspace, and this removes one class of first-run
    surprise from it.
    """

    def setUp(self):
        import urllib.request

        self.sent = []
        real_urlopen = urllib.request.urlopen

        class _Response:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *args):
                return False

            def read(self_inner):
                return b'{"ok": true}'

        def fake_urlopen(request, timeout=None):
            self.sent.append(request)
            return _Response()

        urllib.request.urlopen = fake_urlopen
        self.addCleanup(setattr, urllib.request, "urlopen", real_urlopen)

    def _transport(self):
        return RealNotionTransport(api_token="ntn_" + "T" * 20, timeout=7.5)

    def _last(self):
        import json as json_module

        request = self.sent[-1]
        body = json_module.loads(request.data.decode("utf-8")) if request.data else None
        return request.get_method(), request.full_url, body

    def test_retrieve_database_is_a_get_on_the_database(self):
        result = self._transport().retrieve_database("DB-1")
        method, url, body = self._last()

        self.assertEqual(result, {"ok": True})
        self.assertEqual(method, "GET")
        self.assertTrue(url.endswith("/databases/DB-1"), url)
        self.assertIsNone(body)

    def test_query_database_posts_the_filter_under_its_own_key(self):
        self._transport().query_database("DB-1", {"property": "Project ID"})
        method, url, body = self._last()

        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/databases/DB-1/query"), url)
        self.assertEqual(body, {"filter": {"property": "Project ID"}})

    def test_list_pages_posts_the_query_with_no_filter(self):
        """C51: the request `controltower/notion_projection.py`'s
        reconciliation pass rides on.

        Same endpoint as `query_database()` and a different body — *no*
        `filter` key at all, which is how Notion is asked for every row. A
        body carrying `{"filter": {}}` instead is a 400, and the difference
        is invisible until a live Workspace says so.
        """
        result = self._transport().list_pages("DB-1")
        method, url, body = self._last()

        self.assertEqual(result, [])
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/databases/DB-1/query"), url)
        self.assertNotIn("filter", body)
        self.assertEqual(body, {"page_size": 100})

    def test_list_pages_follows_the_cursor_and_stops(self):
        """Pagination, and the bound on it. A `has_more` with no cursor is a
        response this cannot page through — it stops and says so through
        `list_truncated`, rather than looping."""
        import json as json_module
        import urllib.request

        pages = [
            {"results": [{"id": "p1"}], "has_more": True, "next_cursor": "C1"},
            {"results": [{"id": "p2"}], "has_more": False},
        ]
        sent = []

        class _Paged:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *args):
                return False

            def read(self_inner):
                return json_module.dumps(pages[len(sent) - 1]).encode("utf-8")

        def fake_urlopen(request, timeout=None):
            sent.append(request)
            return _Paged()

        real_urlopen = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        self.addCleanup(setattr, urllib.request, "urlopen", real_urlopen)

        transport = self._transport()
        result = transport.list_pages("DB-1")

        self.assertEqual(result, [{"id": "p1"}, {"id": "p2"}])
        self.assertFalse(transport.list_truncated)
        second = json_module.loads(sent[1].data.decode("utf-8"))
        self.assertEqual(second, {"page_size": 100, "start_cursor": "C1"})

    def test_a_has_more_with_no_cursor_is_reported_as_truncated(self):
        """The one response that could hang the loop. Reported rather than
        retried, because a reconciliation over a partial listing retires
        every row it did not see."""
        import json as json_module
        import urllib.request

        class _Stuck:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *args):
                return False

            def read(self_inner):
                return json_module.dumps(
                    {"results": [{"id": "p1"}], "has_more": True}
                ).encode("utf-8")

        real_urlopen = urllib.request.urlopen
        urllib.request.urlopen = lambda request, timeout=None: _Stuck()
        self.addCleanup(setattr, urllib.request, "urlopen", real_urlopen)

        transport = self._transport()
        result = transport.list_pages("DB-1")

        self.assertEqual(result, [{"id": "p1"}])
        self.assertTrue(transport.list_truncated)

    def test_the_page_limit_stops_a_database_that_never_ends(self):
        """The bound itself — the reason this is a `for` over a fixed range
        and not a `while`.

        A remote that keeps answering `has_more` with a fresh cursor would
        page forever inside a Runner step. Ten requests is 1,000 rows, past
        any Control Tower database this projects (67 rows at 6,000 Events),
        and stopping there is reported through `list_truncated` rather than
        hidden — which is what makes the reconciliation pass decline to
        retire anything rather than retire everything it did not see.
        """
        import json as json_module
        import urllib.request

        sent = []

        class _Endless:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *args):
                return False

            def read(self_inner):
                return json_module.dumps(
                    {
                        "results": [{"id": f"p{len(sent)}"}],
                        "has_more": True,
                        "next_cursor": f"C{len(sent)}",
                    }
                ).encode("utf-8")

        real_urlopen = urllib.request.urlopen

        def fake_urlopen(request, timeout=None):
            sent.append(request)
            return _Endless()

        urllib.request.urlopen = fake_urlopen
        self.addCleanup(setattr, urllib.request, "urlopen", real_urlopen)

        transport = self._transport()
        result = transport.list_pages("DB-1")

        self.assertEqual(len(sent), transport._SEARCH_PAGE_LIMIT)
        self.assertEqual(len(result), transport._SEARCH_PAGE_LIMIT)
        self.assertTrue(transport.list_truncated)

    def test_a_clean_listing_leaves_the_flag_down(self):
        """So the flag means something. It is set at the top of every call,
        not only when it fires — a truncation two listings ago must not make
        this one look partial."""
        transport = self._transport()
        transport.list_truncated = True

        transport.list_pages("DB-1")

        self.assertFalse(transport.list_truncated)

    def test_the_flag_is_readable_before_the_first_listing(self):
        """Declared in `__init__` rather than only on the first call — a
        caller that reads it first would otherwise get AttributeError, and
        the caller that reads it is the one deciding whether to retire
        rows."""
        self.assertFalse(self._transport().list_truncated)

    def test_create_page_names_the_database_as_the_parent(self):
        """A page created without `parent.database_id` lands nowhere Notion
        will let this integration find it again."""
        self._transport().create_page("DB-1", {"Project ID": {"rich_text": []}})
        method, url, body = self._last()

        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/pages"), url)
        self.assertEqual(body["parent"], {"database_id": "DB-1"})
        self.assertEqual(body["properties"], {"Project ID": {"rich_text": []}})

    def test_update_page_patches_only_properties(self):
        """PATCH, not PUT: Notion merges the properties given and leaves the
        rest of the page alone, which is what docs/04 §45's reserved
        human-written fields depend on."""
        self._transport().update_page("PAGE-1", {"Status": {"select": {"name": "OK"}}})
        method, url, body = self._last()

        self.assertEqual(method, "PATCH")
        self.assertTrue(url.endswith("/pages/PAGE-1"), url)
        self.assertEqual(body, {"properties": {"Status": {"select": {"name": "OK"}}}})

    def test_update_database_patches_the_schema_not_a_row(self):
        """The one call that changes a Database definition — `/databases/`,
        never `/pages/`. Writing a schema to a page id is the mistake
        `record_run()`'s docstring warns about from the other direction."""
        self._transport().update_database("DB-1", {"Accepted": {"number": {}}})
        method, url, body = self._last()

        self.assertEqual(method, "PATCH")
        self.assertTrue(url.endswith("/databases/DB-1"), url)
        self.assertEqual(body, {"properties": {"Accepted": {"number": {}}}})

    def test_create_database_puts_it_under_a_page(self):
        """Notion cannot create a Database at the workspace root — the whole
        reason `diagnose_dashboard_bootstrap()` hunts for a shared Page."""
        self._transport().create_database(
            "PAGE-1", "OPS_RUNS", {"Run ID": {"title": {}}}
        )
        method, url, body = self._last()

        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/databases"), url)
        self.assertEqual(body["parent"], {"type": "page_id", "page_id": "PAGE-1"})
        self.assertEqual(
            body["title"], [{"type": "text", "text": {"content": "OPS_RUNS"}}]
        )
        self.assertEqual(body["properties"], {"Run ID": {"title": {}}})

    def test_every_request_carries_the_version_and_the_token(self):
        """Notion rejects a request with no `Notion-Version`, and the header
        is the only place the token ever travels.

        The version is read from the module rather than restated: pinning the
        string here would make an intentional upgrade fail as though it were
        a mistake, while the property that matters — the header is sent at
        all — holds either way."""
        from notion.transport import NOTION_API_VERSION

        self._transport().retrieve_database("DB-1")
        request = self.sent[-1]

        self.assertEqual(request.get_header("Notion-version"), NOTION_API_VERSION)
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertTrue(request.get_header("Authorization").startswith("Bearer ntn_"))

    def test_the_configured_timeout_is_the_one_used(self):
        """A default that silently replaced the configured value would make
        every deployment's timeout a surprise."""
        import urllib.request

        seen = []
        real_urlopen = urllib.request.urlopen

        class _Response:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *args):
                return False

            def read(self_inner):
                return b"{}"

        def capturing(request, timeout=None):
            seen.append(timeout)
            return _Response()

        urllib.request.urlopen = capturing
        self.addCleanup(setattr, urllib.request, "urlopen", real_urlopen)

        self._transport().retrieve_database("DB-1")

        self.assertEqual(seen, [7.5])


class NetworkFailuresBecomeNotionApiErrorsTests(unittest.TestCase):
    """The two conversions `_request()` documents and nothing exercised.

    Its contract is "network problems become `NotionAPIError`", and every
    caller — `ExecutionPlanSync`, `bootstrap`, `dashboard` — catches only
    that type. A failure that escapes as something else does not become
    `NOTION_RETRY_REQUIRED`; it propagates, and on the Runner that is a step
    that dies rather than a step that retries.
    """

    def _raising(self, exc):
        import urllib.request

        real_urlopen = urllib.request.urlopen

        def fake_urlopen(request, timeout=None):
            raise exc

        urllib.request.urlopen = fake_urlopen
        self.addCleanup(setattr, urllib.request, "urlopen", real_urlopen)
        return RealNotionTransport(api_token="ntn_" + "T" * 20, timeout=3.0)

    def test_a_read_timeout_becomes_a_notion_api_error(self):
        """urllib does NOT wrap a read timeout in URLError — it escapes as a
        bare `TimeoutError`, which is why this branch exists at all."""
        from notion.transport import NotionAPIError

        transport = self._raising(TimeoutError("timed out"))

        with self.assertRaises(NotionAPIError) as caught:
            transport.retrieve_database("DB-1")

        self.assertIn("timed out after 3.0s", str(caught.exception))
        self.assertIsNone(caught.exception.status_code)

    def test_any_other_socket_failure_becomes_one_too(self):
        from notion.transport import NotionAPIError

        transport = self._raising(OSError("network is unreachable"))

        with self.assertRaises(NotionAPIError) as caught:
            transport.retrieve_database("DB-1")

        self.assertIn("network is unreachable", str(caught.exception))

    def test_neither_message_carries_the_token(self):
        """The security property every path here has to keep."""
        from notion.transport import NotionAPIError

        for exc in (TimeoutError("t"), OSError("o")):
            transport = self._raising(exc)
            with self.subTest(exc=type(exc).__name__):
                with self.assertRaises(NotionAPIError) as caught:
                    transport.retrieve_database("DB-1")
                self.assertNotIn("ntn_", str(caught.exception))


class ATransportThatCannotSearchInheritsThatAnswerTests(unittest.TestCase):
    """C49: `NotionTransport.search_pages()`'s own body had never run.

    It is deliberately **not** an abstractmethod — its docstring says so —
    precisely so that a transport with no reason to implement workspace
    search inherits "I cannot search" instead of failing to instantiate. The
    diagnosis then reports UNKNOWN rather than treating it as an error.

    Every double in this suite overrides it, including the one that tests the
    unavailable case (it raises its own `NotImplementedError`). So the
    default the design depends on was never exercised by anything, and a
    change to it — dropping the message, raising a different type — would
    have gone unnoticed.
    """

    class _Minimal(NotionTransport):
        """Only the abstract methods, the way a real third-party double
        would be written."""

        def retrieve_database(self, database_id):
            return {"parent": {"type": "workspace"}, "properties": {}}

        def query_database(self, database_id, filter_):
            return {"results": []}

        def create_page(self, database_id, properties):
            return {"id": "page-1"}

        def update_page(self, page_id, properties):
            return {"id": page_id}

        def update_database(self, database_id, properties):
            return {"id": database_id}

        def create_database(self, parent_page_id, title, properties):
            return {"id": "db-1"}

    def test_the_inherited_default_refuses_rather_than_returning_nothing(self):
        """An empty list would say "no shared pages", which is a different —
        and wrong — answer from "I could not look"."""
        with self.assertRaises(NotImplementedError) as caught:
            self._Minimal().search_pages()

        self.assertIn("cannot search", str(caught.exception))

    def test_the_diagnosis_reports_it_as_unavailable_not_as_a_failure(self):
        client = NotionClient(transport=self._Minimal(), database_id="projects-db")

        diagnosis = diagnose_dashboard_bootstrap(client)

        self.assertFalse(diagnosis.search_available)
        self.assertEqual(diagnosis.hostable_pages, ())
        self.assertEqual(diagnosis.readiness, BootstrapReadiness.NEEDS_SHARED_PAGE)
        self.assertIn("NOTION_OPS_RUNS_DATABASE_ID", diagnosis.required_action)


class AnUntitledPageIsStillListedTests(unittest.TestCase):
    """C49: found by branch coverage — `_page_title()`'s fallback had never
    been executed.

    `diagnose_dashboard_bootstrap()` lists the Pages an operator can host the
    OPS_* databases under, and identifies each by its title. An untitled page
    is ordinary in Notion — a page created and not yet named has an empty
    title — and it is still a perfectly valid parent.

    Without the fallback the entry would be identified by an empty string,
    which in a list of candidates reads as a rendering fault rather than as a
    page. The `id` is what the operator actually passes to
    `bootstrap_dashboard_databases()`, so nothing is lost by the page having
    no name; what matters is that it is **listed** rather than dropped or
    blank.
    """

    def test_a_page_with_no_title_property_is_named_untitled(self):
        from notion.dashboard import _page_title

        self.assertEqual(_page_title({"properties": {}}), "(untitled)")

    def test_a_page_with_an_empty_title_is_named_untitled(self):
        from notion.dashboard import _page_title

        self.assertEqual(
            _page_title({"properties": {"Name": {"type": "title", "title": []}}}),
            "(untitled)",
        )

    def test_a_title_whose_runs_are_all_empty_is_named_untitled(self):
        """Notion stores one item per formatting run; every one of them can
        be empty without the property being absent."""
        from notion.dashboard import _page_title

        self.assertEqual(
            _page_title(
                {
                    "properties": {
                        "Name": {
                            "type": "title",
                            "title": [{"plain_text": ""}, {"plain_text": ""}],
                        }
                    }
                }
            ),
            "(untitled)",
        )

    def test_a_named_page_keeps_its_name(self):
        """The other side, so the fallback cannot pass by always firing."""
        from notion.dashboard import _page_title

        self.assertEqual(
            _page_title(
                {
                    "properties": {
                        "Name": {"type": "title", "title": [{"plain_text": "Company Ops"}]}
                    }
                }
            ),
            "Company Ops",
        )

    def test_the_title_is_found_past_other_properties(self):
        """A page carries whatever properties its database gave it, in
        whatever order Notion returns them — the title is not first, and the
        scan has to keep going rather than answer on the first entry."""
        from notion.dashboard import _page_title

        self.assertEqual(
            _page_title(
                {
                    "properties": {
                        "Status": {"type": "select", "select": {"name": "Live"}},
                        "Notes": {"type": "rich_text", "rich_text": []},
                        "Name": {"type": "title", "title": [{"plain_text": "Company Ops"}]},
                    }
                }
            ),
            "Company Ops",
        )

    def test_a_page_whose_only_properties_are_not_titles_is_untitled(self):
        from notion.dashboard import _page_title

        self.assertEqual(
            _page_title(
                {"properties": {"Status": {"type": "select"}, "Notes": {"type": "rich_text"}}}
            ),
            "(untitled)",
        )


class BootstrapDiagnosisTests(unittest.TestCase):
    """P0: decide from the *real* workspace shape whether bootstrap can run."""

    def test_database_inside_a_page_is_ready(self):
        transport = InMemoryNotionTransport(
            parent={"type": "page_id", "page_id": "company-ops-page"}
        )
        client = NotionClient(transport=transport, database_id="projects-db")

        d = diagnose_dashboard_bootstrap(client)

        self.assertEqual(d.readiness, BootstrapReadiness.READY)
        self.assertEqual(d.resolved_parent_page_id, "company-ops-page")

    def test_the_ready_verdict_does_not_say_there_is_nothing_to_do(self):
        """NEW. The success message pointed at a step nothing performs.

        It used to read:

            None — the reference database already lives in a Page;
            bootstrap_dashboard_databases(client) will use it.

        Both halves mislead. `init_notion.py` — the one command whose job is
        Notion setup — calls `diagnose_dashboard_bootstrap()` and stops; it
        never calls `bootstrap_dashboard_databases()`. Verified by AST across
        `src/**` and every root script with import aliases resolved: that
        function has **zero** call sites in production, and no document
        mentions it. So an operator on the happy path reads
        `다음 할 일: None`, concludes the Dashboard is configured, and gets
        `NOTION_OPS_RUNS_DATABASE_ID` unset — under which `record_run()` is
        skipped on every run, permanently and by design (.env.example says
        so).

        A clear signal for work nobody did is the inverse of this project's
        recurring alert-that-cannot-clear, and worse: nothing ever
        contradicts it.

        Automating the creation is out of scope — it writes to a real Notion
        Workspace. Telling the truth about it is not.
        """
        transport = InMemoryNotionTransport(
            parent={"type": "page_id", "page_id": "company-ops-page"}
        )
        client = NotionClient(transport=transport, database_id="projects-db")

        action = diagnose_dashboard_bootstrap(client).required_action

        self.assertNotIn("None —", action)
        self.assertIn("NOTION_OPS_RUNS_DATABASE_ID", action)
        self.assertIn("no entrypoint", action)

    def test_every_verdict_names_the_variable_that_actually_switches_it_on(self):
        """The one fact an operator needs in all three reachable states."""
        cases = {}

        cases["READY"] = InMemoryNotionTransport(
            parent={"type": "page_id", "page_id": "p"}
        )

        needs_choice = InMemoryNotionTransport()
        needs_choice.searchable_pages = [_page("page-1", "Company Ops", "workspace")]
        cases["NEEDS_PARENT_CHOICE"] = needs_choice

        cases["NEEDS_SHARED_PAGE"] = InMemoryNotionTransport()

        for label, transport in cases.items():
            with self.subTest(readiness=label):
                action = diagnose_dashboard_bootstrap(
                    NotionClient(transport=transport, database_id="projects-db")
                ).required_action

                self.assertIn("NOTION_OPS_RUNS_DATABASE_ID", action)

    def test_the_production_scan_finds_the_repository(self):
        """Guard against the guard silently matching nothing.

        `test_nothing_in_production_creates_the_dashboard_databases` asserts a **negative** over this scan — "nothing in the tree
        does X" — and a negative over an empty set is true. Measured (C66):
        with tree discovery neutered, it passed while checking nothing.

        The trigger is ordinary rather than exotic, and this repository
        already names it: `TheScansThisFileTrustsAreNotEmptyTests` was
        written when `git ls-files` came back empty outside a checkout. A
        renamed or moved `src/` does the same thing to `rglob`, and this
        project is deliberately worked on from several machines
        (AGENT.md §1).
        """
        repo_root = Path(__file__).resolve().parents[1]
        files = [
            p
            for p in list((repo_root / "src").glob("**/*.py")) + list(repo_root.glob("*.py"))
            if "__pycache__" not in str(p)
        ]
        self.assertGreater(len(files), 50)

    def test_nothing_in_production_creates_the_dashboard_databases(self):
        """The fact the messages above now state. Pinned so that wiring it up
        (which needs approval — it writes to a real Workspace) forces those
        messages to be rewritten in the same change."""
        import ast

        repo_root = Path(__file__).resolve().parents[1]
        files = [
            p
            for p in list((repo_root / "src").glob("**/*.py")) + list(repo_root.glob("*.py"))
            if "__pycache__" not in str(p)
        ]
        sites = []
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            aliases = {
                a.asname: a.name
                for node in ast.walk(tree)
                if isinstance(node, (ast.Import, ast.ImportFrom))
                for a in node.names
                if a.asname
            }
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if aliases.get(name, name) == "bootstrap_dashboard_databases":
                    sites.append(f"{path.name}:{node.lineno}")

        self.assertEqual(sites, [])

    def test_workspace_root_with_no_shared_page_needs_sharing(self):
        # This is the real workspace's current state.
        transport = InMemoryNotionTransport()
        client = NotionClient(transport=transport, database_id="projects-db")

        d = diagnose_dashboard_bootstrap(client)

        self.assertEqual(d.readiness, BootstrapReadiness.NEEDS_SHARED_PAGE)
        self.assertEqual(d.hostable_pages, ())
        self.assertIn("Share an existing Page", d.required_action)

    def test_database_rows_are_not_counted_as_hostable_pages(self):
        # Pages whose parent is a database are rows inside it, not
        # containers — the real workspace returns exactly this shape.
        transport = InMemoryNotionTransport()
        transport.searchable_pages = [
            _page("row-1", "Company Ops Runner E2E", "database_id"),
            _page("row-2", "Company Ops E2E Verification", "database_id"),
        ]
        client = NotionClient(transport=transport, database_id="projects-db")

        d = diagnose_dashboard_bootstrap(client)

        self.assertEqual(d.readiness, BootstrapReadiness.NEEDS_SHARED_PAGE)
        self.assertEqual(d.hostable_pages, ())

    def test_shared_container_page_offers_a_parent_choice(self):
        transport = InMemoryNotionTransport()
        transport.searchable_pages = [
            _page("row-1", "A Project Row", "database_id"),
            _page("page-1", "Company Ops", "workspace"),
        ]
        client = NotionClient(transport=transport, database_id="projects-db")

        d = diagnose_dashboard_bootstrap(client)

        self.assertEqual(d.readiness, BootstrapReadiness.NEEDS_PARENT_CHOICE)
        self.assertEqual([p.page_id for p in d.hostable_pages], ["page-1"])
        self.assertEqual(d.hostable_pages[0].title, "Company Ops")

    def test_diagnosis_never_raises_when_search_is_unavailable(self):
        class NoSearchTransport(InMemoryNotionTransport):
            def search_pages(self):
                raise NotImplementedError("cannot search")

        client = NotionClient(transport=NoSearchTransport(), database_id="projects-db")

        d = diagnose_dashboard_bootstrap(client)

        self.assertFalse(d.search_available)
        self.assertEqual(d.readiness, BootstrapReadiness.NEEDS_SHARED_PAGE)

    def test_diagnosis_creates_nothing(self):
        transport = InMemoryNotionTransport()
        client = NotionClient(transport=transport, database_id="projects-db")

        diagnose_dashboard_bootstrap(client)

        self.assertEqual(transport.created_databases, {})
        self.assertEqual(transport._pages, {})


class OpsRunPropertyTests(unittest.TestCase):
    def test_overall_is_ok_for_a_clean_run(self):
        props = build_ops_run_properties(
            run_id="r1", run_at=datetime(2026, 8, 1, 12, 0), transport_moved=1,
            transport_blocked=0,
            accepted=2, duplicate=0, rejected=0, failed=0,
            scheduler_status="COMPLETED", generated_days=1, reused_days=0,
            backup_status="BACKUP_SUCCESS", notion_synced=2, notion_skipped=0,
            deleted_files=0,
            notion_retried=0, notion_unreadable=0, notion_queued=0,
        )
        self.assertEqual(props["Overall"]["select"]["name"], "OK")

    def test_overall_is_fail_when_scheduler_failed(self):
        props = build_ops_run_properties(
            run_id="r1", run_at=datetime(2026, 8, 1, 12, 0), transport_moved=0,
            transport_blocked=0,
            accepted=0, duplicate=0, rejected=0, failed=0,
            scheduler_status="FAILED", generated_days=0, reused_days=0,
            backup_status="BACKUP_SUCCESS", notion_synced=0, notion_skipped=0,
            deleted_files=0,
            notion_retried=0, notion_unreadable=0, notion_queued=0,
        )
        self.assertEqual(props["Overall"]["select"]["name"], "FAIL")

    def test_overall_is_fail_when_backup_failed(self):
        props = build_ops_run_properties(
            run_id="r1", run_at=datetime(2026, 8, 1, 12, 0), transport_moved=0,
            transport_blocked=0,
            accepted=0, duplicate=0, rejected=0, failed=0,
            scheduler_status="COMPLETED", generated_days=0, reused_days=0,
            backup_status="BACKUP_FAILED", notion_synced=0, notion_skipped=0,
            deleted_files=0,
            notion_retried=0, notion_unreadable=0, notion_queued=0,
        )
        self.assertEqual(props["Overall"]["select"]["name"], "FAIL")

    def test_overall_is_warn_when_backup_pending(self):
        props = build_ops_run_properties(
            run_id="r1", run_at=datetime(2026, 8, 1, 12, 0), transport_moved=0,
            transport_blocked=0,
            accepted=0, duplicate=0, rejected=0, failed=0,
            scheduler_status="COMPLETED", generated_days=0, reused_days=0,
            backup_status="BACKUP_PENDING", notion_synced=0, notion_skipped=0,
            deleted_files=0,
            notion_retried=0, notion_unreadable=0, notion_queued=0,
        )
        self.assertEqual(props["Overall"]["select"]["name"], "WARN")

    def test_property_names_match_the_ops_runs_schema(self):
        props = build_ops_run_properties(
            run_id="r1", run_at=datetime(2026, 8, 1, 12, 0), transport_moved=0,
            transport_blocked=0,
            accepted=0, duplicate=0, rejected=0, failed=0,
            scheduler_status="COMPLETED", generated_days=0, reused_days=0,
            backup_status="BACKUP_SUCCESS", notion_synced=0, notion_skipped=0,
            deleted_files=0,
            notion_retried=0, notion_unreadable=0, notion_queued=0,
        )
        self.assertEqual(set(props), set(DASHBOARD_DATABASES[OPS_RUNS]))


class DoublesMatchTheRealResultObjectsTests(unittest.TestCase):
    """The doubles above must not be narrower than what they double.

    `record_run()` now reads its inputs by direct attribute access, so a
    double missing a field fails the test that uses it — loudly, which is
    the point. The reverse drift is the silent one: the real object grows a
    field, the double does not, and every test here goes on passing against
    a shape production never sees. That is how `Transport Blocked` could
    have been added, tested, and still been wrong about real runs.

    Compared by field name only. These tests deliberately do not import the
    real objects for their *behaviour* — `notion` knows nothing about
    `transport` or `collector`, and this suite contacts no workspace — but
    an import for a field list costs nothing and closes the drift.
    """

    def test_the_intake_double_carries_every_intake_summary_field(self):
        import dataclasses

        from transport.intake import IntakeSummary

        real = {f.name for f in dataclasses.fields(IntakeSummary)}
        self.assertEqual(set(vars(_FakeIntake())), real)

    def test_the_collector_double_carries_every_runtime_summary_field(self):
        import dataclasses

        from collector.runtime import RuntimeSummary

        real = {f.name for f in dataclasses.fields(RuntimeSummary)}
        self.assertEqual(set(vars(_FakeCollector())), real)

    def test_the_scheduler_double_carries_every_scheduler_result_field(self):
        """The one that had drifted. `SchedulerRunResult` grew `reused_dates`
        in C39 and this double stayed at two fields — so every test here went
        on passing against a shape production never sees, which is what this
        class's own docstring names as the silent direction.

        `status` is compared by name only: the real field holds a
        `SchedulerStatus` and the double holds `_FakeStatus`, and this suite
        deliberately does not import enums it does not need.
        """
        import dataclasses

        from scheduler.result import SchedulerRunResult

        real = {f.name for f in dataclasses.fields(SchedulerRunResult)}
        self.assertEqual(set(vars(_FakeScheduler())), real)

    def test_the_backup_double_carries_every_backup_log_entry_field(self):
        """The other one. `record_run()` reads `final_status`; the Dashboard's
        OPS_BACKUP schema reads five more, so a narrower double could not
        have caught a rename in any of them."""
        import dataclasses

        from backup.log import BackupLogEntry

        real = {f.name for f in dataclasses.fields(BackupLogEntry)}
        self.assertEqual(set(vars(_FakeBackup())), real)


class BlockedIntakeIsVisibleTests(unittest.TestCase):
    """C32 §4 (P0): a blocked inbound path produced a healthy-looking row.

    `run_intake()` sorts what it did not promote into five buckets, and the
    Dashboard read exactly one of them (`moved`). So a run in which nothing
    arrived because everything was stuck looked identical to a run in which
    nothing arrived because there was nothing to send. Measured, with ten
    unparseable files, one `.tmp-` staging file and one failed move:

        Transport Moved 0   Accepted 0   Rejected 0   Overall OK

    which is byte-for-byte a quiet Sunday. Desktop 1-3 could stop being
    delivered for a month with the Dashboard reporting OK every run.
    """

    def _blocked(self, **fields):
        from notion.dashboard import count_blocked_intake

        return count_blocked_intake(_FakeIntake(**fields))

    def test_unparseable_incomplete_and_failed_files_are_counted(self):
        self.assertEqual(
            self._blocked(
                skipped_invalid=("a.json", "b.json"),
                skipped_incomplete=(".tmp-c.json",),
                failed=("d.json",),
            ),
            4,
        )

    def test_the_two_self_clearing_buckets_are_not_counted(self):
        """A number a healthy system cannot clear is the standing-alert
        shape `IntakeBacklog` was written to remove. A file that arrived two
        seconds ago, and one whose Event is already downstream, are the
        pipeline working."""
        self.assertEqual(
            self._blocked(
                skipped_not_stable=("fresh.json",),
                skipped_already_present=("known.json",),
            ),
            0,
        )

    def test_the_blocked_run_no_longer_reads_as_a_quiet_day(self):
        transport = InMemoryNotionTransport()
        client = NotionClient(transport=transport, database_id="ops-runs-db")

        result = record_run(
            client,
            run_id="run-blocked",
            run_at=datetime(2026, 8, 17, 9, 0),
            intake_summary=_FakeIntake(
                skipped_not_stable=("fresh.json",),
                skipped_invalid=tuple(f"bad{i}.json" for i in range(10)),
                failed=("locked.json",),
            ),
            collector_summary=_FakeCollector(),
            scheduler_result=_FakeScheduler(),
            backup_entry=_FakeBackup(),
            notion_sync_results=(),
        )

        properties = transport._pages[result.page_id]["properties"]
        self.assertEqual(properties["Transport Blocked"]["number"], 11)
        self.assertEqual(properties["Overall"]["select"]["name"], "WARN")

    def test_a_genuinely_quiet_day_is_still_ok(self):
        """The other direction, and the more important one: an idle Sunday
        must not learn to cry wolf, or the WARN above stops meaning
        anything."""
        transport = InMemoryNotionTransport()
        client = NotionClient(transport=transport, database_id="ops-runs-db")

        result = record_run(
            client,
            run_id="run-quiet",
            run_at=datetime(2026, 8, 17, 9, 0),
            intake_summary=_FakeIntake(),
            collector_summary=_FakeCollector(),
            scheduler_result=_FakeScheduler(),
            backup_entry=_FakeBackup(status="BACKUP_NOT_REQUIRED"),
            notion_sync_results=(),
        )

        properties = transport._pages[result.page_id]["properties"]
        self.assertEqual(properties["Transport Blocked"]["number"], 0)
        self.assertEqual(properties["Overall"]["select"]["name"], "OK")


class SyncCountsPartitionTheEventsTests(unittest.TestCase):
    """C32 §5: `Notion Synced` counted Events that Notion never wrote.

    docs/04 §35 defines NOTION_SKIPPED_OLD_EVENT as "적용하지 않았다" — the
    Event reached Notion and deliberately changed nothing. `synced` was
    computed as "everything that is not a failure", so those counted as
    writes. Measured: four Events, all skipped as older than the row they
    would have overwritten, reported as `Notion Synced: 4` with zero writes.
    """

    def _counts(self, statuses):
        transport = InMemoryNotionTransport()
        client = NotionClient(transport=transport, database_id="ops-runs-db")
        result = record_run(
            client,
            run_id="run-sync",
            run_at=datetime(2026, 8, 17, 9, 0),
            intake_summary=_FakeIntake(),
            collector_summary=_FakeCollector(),
            scheduler_result=_FakeScheduler(),
            backup_entry=_FakeBackup(),
            notion_sync_results=[_sync(s, f"E{i}") for i, s in enumerate(statuses)],
        )
        properties = transport._pages[result.page_id]["properties"]
        return {
            key: properties[key]["number"]
            for key in ("Notion Synced", "Notion Skipped", "Notion Retried")
        }

    def test_skipped_events_are_no_longer_reported_as_writes(self):
        counts = self._counts([SyncStatus.NOTION_SKIPPED_OLD_EVENT] * 4)

        self.assertEqual(counts["Notion Synced"], 0)
        self.assertEqual(counts["Notion Skipped"], 4)

    def test_a_skip_only_run_is_not_a_warning(self):
        """Nothing needed a human: every Event was correctly recognised as
        older than the state it would have replaced."""
        transport = InMemoryNotionTransport()
        client = NotionClient(transport=transport, database_id="ops-runs-db")
        result = record_run(
            client,
            run_id="run-skips",
            run_at=datetime(2026, 8, 17, 9, 0),
            intake_summary=_FakeIntake(),
            collector_summary=_FakeCollector(accepted=4),
            scheduler_result=_FakeScheduler(),
            backup_entry=_FakeBackup(),
            notion_sync_results=[
                _sync(SyncStatus.NOTION_SKIPPED_OLD_EVENT, f"E{i}") for i in range(4)
            ],
        )

        self.assertEqual(
            transport._pages[result.page_id]["properties"]["Overall"]["select"]["name"],
            "OK",
        )

    def test_the_three_counts_always_add_up_to_the_events_handled(self):
        """The property that makes the row's arithmetic readable. Checked
        across every status the Sync step can return, plus a status this
        module has never heard of."""
        class _UnknownStatus:
            value = "NOTION_SOMETHING_NEW"

        cases = {
            "all five real statuses": [
                SyncStatus.NOTION_CREATED,
                SyncStatus.NOTION_UPDATED,
                SyncStatus.NOTION_SKIPPED_OLD_EVENT,
                SyncStatus.NOTION_RETRY_REQUIRED,
                SyncStatus.NOTION_FAILED,
            ],
            "an unrecognised status": [SyncStatus.NOTION_CREATED, _UnknownStatus()],
        }
        for label, statuses in cases.items():
            with self.subTest(case=label):
                counts = self._counts(statuses)
                self.assertEqual(sum(counts.values()), len(statuses))

    def test_an_unrecognised_status_lands_where_it_will_be_noticed(self):
        """Not dropped, and not quietly called a success: an unknown sync
        status is a code defect, and `Notion Retried` is the count that
        raises WARN."""
        class _UnknownStatus:
            value = "NOTION_SOMETHING_NEW"

        counts = self._counts([_UnknownStatus()])

        self.assertEqual(counts["Notion Retried"], 1)
        self.assertEqual(counts["Notion Synced"], 0)


class ReusedDaysReachesTheDashboardTests(unittest.TestCase):
    """C39's split, one view further out.

    `SchedulerRunResult` was split into `generated_dates` (this run wrote it)
    and `reused_dates` (the file was already there) because a restored
    Desktop 4 closes days it did not write, and calling that "generated" told
    an operator the pipeline had rebuilt History it cannot rebuild. That
    split reached the Run Manifest and `run_company_ops.py`'s stdout.

    It did not reach the Dashboard — the view CEO Decision ④ made the
    operator's at-a-glance one, precisely so nobody has to read a CLI. So the
    row for the run an operator scrutinises hardest said

        Generated Days: 0

    and had no column that could say the other seventeen days came back from
    the backup. Zero is also what a completely idle Sunday writes.
    """

    def _row(self, scheduler):
        transport = InMemoryNotionTransport()
        client = NotionClient(transport=transport, database_id="ops-runs-db")
        result = record_run(
            client,
            run_id="r1",
            run_at=datetime(2026, 8, 5, 12, 0),
            intake_summary=_FakeIntake(),
            collector_summary=_FakeCollector(),
            scheduler_result=scheduler,
            backup_entry=_FakeBackup(),
            notion_sync_results=(),
        )
        self.assertIs(result.outcome, DashboardOutcome.RECORDED)
        return transport._pages[result.page_id]["properties"]

    def test_a_restore_shaped_run_is_not_a_quiet_day(self):
        """The measured shape from `test_e2e_disaster_scenarios.py`: one day
        written, four adopted from git."""
        row = self._row(
            _FakeScheduler(
                generated_dates=(date(2026, 8, 5),),
                reused_dates=tuple(date(2026, 8, day) for day in range(1, 5)),
            )
        )

        self.assertEqual(row["Generated Days"]["number"], 1)
        self.assertEqual(row["Reused Days"]["number"], 4)

    def test_a_quiet_day_and_a_restore_are_no_longer_the_same_row(self):
        quiet = self._row(_FakeScheduler())
        restored = self._row(
            _FakeScheduler(reused_dates=tuple(date(2026, 8, d) for d in range(1, 18)))
        )

        self.assertEqual(quiet["Generated Days"]["number"], 0)
        self.assertEqual(quiet["Reused Days"]["number"], 0)
        self.assertEqual(restored["Generated Days"]["number"], 0)
        self.assertEqual(restored["Reused Days"]["number"], 17)

    def test_reusing_a_day_does_not_change_the_verdict(self):
        """`_overall_status()`'s rule is that any input to the verdict has to
        earn a column first — not that every column is an input. Reusing a
        day is the pipeline working (docs/07 §28), and a restore that WARNed
        would put a standing alert on the one run an operator is already
        reading line by line."""
        row = self._row(
            _FakeScheduler(reused_dates=tuple(date(2026, 8, d) for d in range(1, 18)))
        )

        self.assertEqual(row["Overall"]["select"]["name"], "OK")

    def test_both_numbers_are_read_directly_not_defaulted(self):
        """The rule this module states beside its other inputs — "a default
        would only be able to hide the day one is renamed". `generated_days`
        was the one place that broke it (`getattr(..., "generated_dates", ())`),
        and C39 renamed what that very field means, which is how close that
        already came.

        A result object missing either field must now fail the build loudly
        rather than report 0 for a run that closed seventeen days.
        """
        class _Renamed:
            status = _FakeStatus("COMPLETED")
            written_dates = ()
            reused_dates = ()

        transport = InMemoryNotionTransport()
        client = NotionClient(transport=transport, database_id="ops-runs-db")

        result = record_run(
            client,
            run_id="r1",
            run_at=datetime(2026, 8, 5, 12, 0),
            intake_summary=_FakeIntake(),
            collector_summary=_FakeCollector(),
            scheduler_result=_Renamed(),
            backup_entry=_FakeBackup(),
            notion_sync_results=(),
        )

        self.assertIs(result.outcome, DashboardOutcome.FAILED)
        self.assertIn("generated_dates", result.error or "")
        # Nothing is queued for retry: the properties could not be built, so
        # there is no row to send later (the module's own contract).
        self.assertIsNone(result.properties)

    def test_the_column_is_in_the_schema_the_bootstrap_creates(self):
        """A property `record_run()` sends and the schema does not declare is
        a 400 on every run — the failure docs/13 §3-⑧ exists to get an
        operator out of."""
        self.assertIn("Reused Days", DASHBOARD_DATABASES[OPS_RUNS])
        self.assertEqual(DASHBOARD_DATABASES[OPS_RUNS]["Reused Days"], {"number": {}})


class TheTwoBackupFailuresAreTellableApartTests(unittest.TestCase):
    """BACKLOG E-25, as far as it can be taken without a spec decision.

    `BACKUP_FAILED` is written by two events with nothing in common:

        docs/08 §21        credentials / permissions — fix the token
        docs/08 §31, §44-47 the deletion gate refused to add/commit/push
                            because Local Master files are GONE

    docs/14 §5's vocabulary gives both the same value, and changing that is a
    spec decision (E-25, still open). C31 took the part that is not: it wrote
    the fact into the manifest's free-text `reason` and added a
    `deleted_files` metric beside it.

    The Dashboard is where an operator actually looks — CEO Decision ④ made
    it the at-a-glance view on purpose ("CLI 확장 금지, Dashboard는 Notion
    으로") — and the manifest's `reason` never appears there. So the row said

        Backup Status: BACKUP_FAILED    Overall: FAIL

    for both, and the next action ("renew the token" vs "find the missing
    Company History") was not derivable from it.

    One number, no new classification, no change to `Overall`.
    """

    def _row(self, backup):
        transport = InMemoryNotionTransport()
        client = NotionClient(transport=transport, database_id="ops-runs-db")
        result = record_run(
            client,
            run_id="r1",
            run_at=datetime(2026, 8, 5, 12, 0),
            intake_summary=_FakeIntake(),
            collector_summary=_FakeCollector(),
            scheduler_result=_FakeScheduler(),
            backup_entry=backup,
            notion_sync_results=(),
        )
        self.assertIs(result.outcome, DashboardOutcome.RECORDED)
        return transport._pages[result.page_id]["properties"]

    def test_a_deletion_blocked_backup_names_how_many_files_went_missing(self):
        row = self._row(
            _FakeBackup(
                status="BACKUP_FAILED",
                deleted_files=("daily/2026-08-01.md", "daily/2026-08-02.md"),
            )
        )

        self.assertEqual(row["Backup Status"]["select"]["name"], "BACKUP_FAILED")
        self.assertEqual(row["Deleted Files"]["number"], 2)

    def test_an_authentication_failure_is_the_same_status_and_zero_files(self):
        """The other half. Without this the column would be untested against
        the case it exists to separate the first one FROM."""
        row = self._row(_FakeBackup(status="BACKUP_FAILED", push_result="401"))

        self.assertEqual(row["Backup Status"]["select"]["name"], "BACKUP_FAILED")
        self.assertEqual(row["Deleted Files"]["number"], 0)

    def test_the_verdict_is_unchanged_by_the_new_column(self):
        """`BACKUP_FAILED` already makes the row FAIL. A second derivation of
        one fact is what `_overall_status()`'s docstring warns against."""
        deletion = self._row(
            _FakeBackup(status="BACKUP_FAILED", deleted_files=("daily/2026-08-01.md",))
        )
        auth = self._row(_FakeBackup(status="BACKUP_FAILED"))

        self.assertEqual(deletion["Overall"]["select"]["name"], "FAIL")
        self.assertEqual(auth["Overall"]["select"]["name"], "FAIL")

    def test_a_healthy_run_reports_zero_rather_than_nothing(self):
        """An absent number and a zero read differently in a Notion view that
        an operator sorts by. Every other count on this row is written on
        every run for the same reason."""
        row = self._row(_FakeBackup())

        self.assertEqual(row["Deleted Files"]["number"], 0)

    def test_the_real_deletion_gate_produces_the_shape_this_reads(self):
        """The premise, from `backup.runner` rather than from the double: the
        entry it returns on the deletion path carries `deleted_files`, and
        that is the field this column reads.
        """
        import dataclasses

        from backup.log import BackupLogEntry

        fields = {f.name for f in dataclasses.fields(BackupLogEntry)}

        self.assertIn("deleted_files", fields)
        self.assertIn(
            "deleted_files=sync_result.deleted",
            (
                Path(__file__).resolve().parents[1] / "src" / "backup" / "runner.py"
            ).read_text(encoding="utf-8"),
        )


class OverallVerdictAgreesWithItsOwnColumnsTests(unittest.TestCase):
    """`Overall` is the only column a glance reads, and it said OK for runs
    whose very next column said otherwise (C32 §1).

    `_overall_status()`'s docstring has always promised WARN for "rejected /
    failed events, Backup not successful". `rejected` was not a parameter of
    the function at all, and Notion sync failures were not considered
    anywhere — so two of the three promised causes had no branch. Measured
    against real `RuntimeSummary` / `SyncResult` objects before the fix:

        8 Events REJECTED by the Collector      Rejected 8   ->  OK
        5 Events that never reached Notion      Retried  5   ->  OK

    The third row is a spelling bug of the same age: the WARN branch tested
    for `"BACKUP_REVIEW"`, which `backup.result.BackupStatus` has never
    produced (docs/08 §34 spells the optional state `BACKUP_REVIEW_REQUIRED`),
    so it could never fire and would not have fired even if that state were
    added. Pinned here as "any Backup status outside the healthy pair warns",
    which is the form that cannot be mis-spelled into silence.
    """

    def _overall(self, **overrides):
        kwargs = dict(
            run_id="r1", run_at=datetime(2026, 8, 1, 12, 0), transport_moved=0,
            transport_blocked=0,
            accepted=0, duplicate=0, rejected=0, failed=0,
            scheduler_status="COMPLETED", generated_days=0, reused_days=0,
            backup_status="BACKUP_SUCCESS", notion_synced=0, notion_skipped=0,
            deleted_files=0,
            notion_retried=0, notion_unreadable=0, notion_queued=0,
        )
        kwargs.update(overrides)
        return build_ops_run_properties(**kwargs)["Overall"]["select"]["name"]

    def test_rejected_events_warn(self):
        self.assertEqual(self._overall(rejected=8), "WARN")

    def test_events_that_did_not_reach_notion_warn(self):
        self.assertEqual(self._overall(notion_retried=5), "WARN")

    def test_backup_not_required_is_not_a_warning(self):
        """The other half of the closed healthy set. A run with nothing to
        back up is the ordinary quiet day, not a condition."""
        self.assertEqual(self._overall(backup_status="BACKUP_NOT_REQUIRED"), "OK")

    def test_an_unknown_backup_status_warns_rather_than_reading_as_healthy(self):
        """Why the healthy set is closed rather than the unhealthy one listed.

        A Backup status this module has never heard of — the shape docs/08
        §34's `BACKUP_REVIEW_REQUIRED` would arrive in — must land on the
        side that gets a human, not on OK.
        """
        self.assertEqual(self._overall(backup_status="BACKUP_REVIEW_REQUIRED"), "WARN")
        self.assertEqual(self._overall(backup_status="SOMETHING_NEW"), "WARN")

    def test_failure_still_outranks_warning(self):
        self.assertEqual(
            self._overall(
                rejected=8,
                failed_steps=["daily"],
                critical_failed_steps=["daily"],
            ),
            "FAIL",
        )
        self.assertEqual(
            self._overall(notion_retried=5, backup_status="BACKUP_FAILED"), "FAIL"
        )

    def test_a_collector_file_failure_warns_rather_than_failing(self):
        """C37 changed this line, and it is the one place to say why.

        It used to read `self._overall(rejected=8, failed=1) == "FAIL"`, i.e.
        one unprocessable Event file made the whole run FAIL. `app/runner.py`
        says the opposite beside the same number — "not a component failure:
        docs/03 §53 makes per-file isolation the design" — and records the
        run SUCCESS / exit 0. Two artifacts about one run, disagreeing at the
        top of both.

        This is not a weakened assertion. The old one encoded a rule the
        specification does not have (docs/14 §4: FAILED means a CRITICAL
        *Component* failed) and `test_failure_still_outranks_warning` above
        now makes the same point with a case that really is a failure.
        """
        self.assertEqual(self._overall(failed=1), "WARN")
        self.assertEqual(self._overall(failed=1, rejected=8), "WARN")
        # And the file count is still visible in its own column, unchanged.
        properties = build_ops_run_properties(
            run_id="r1", run_at=datetime(2026, 8, 1, 12, 0), transport_moved=0,
            transport_blocked=0, accepted=9, duplicate=0, rejected=0, failed=1,
            scheduler_status="COMPLETED", generated_days=0, reused_days=0,
            backup_status="BACKUP_SUCCESS", notion_synced=0, notion_skipped=0,
            deleted_files=0,
            notion_retried=0, notion_unreadable=0, notion_queued=0,
        )
        self.assertEqual(properties["Failed"]["number"], 1)

    def test_a_lock_contended_run_is_still_ok(self):
        """`SKIPPED_ALREADY_RUNNING` means another Runner holds the lock —
        the design working, not a fault. Pinned so the widened WARN set does
        not start alarming on it."""
        self.assertEqual(
            self._overall(
                scheduler_status="SKIPPED_ALREADY_RUNNING",
                backup_status="BACKUP_NOT_REQUIRED",
                deleted_files=0,
            ),
            "OK",
        )

    def test_every_input_to_the_verdict_is_also_a_column_of_the_same_row(self):
        """The constraint that keeps a WARN actionable.

        A verdict computed from something the row does not show tells an
        operator to go looking with nothing to look at. Checked structurally
        so a future input has to earn a column first.
        """
        import inspect

        from notion import dashboard

        signature = inspect.signature(dashboard._overall_status)
        column_for_parameter = {
            "collector_failed": "Failed",
            "rejected": "Rejected",
            "transport_blocked": "Transport Blocked",
            "scheduler_status": "Scheduler Status",
            "backup_status": "Backup Status",
            "notion_retried": "Notion Retried",
            "notion_unreadable": "Notion Unreadable",
            "notion_queued": "Notion Queued",
            # Both read from the one column that names them. The severity
            # split is not a second column on purpose: an operator reading
            # "backup" in `Failed Steps` beside `Overall FAIL` has the whole
            # story, and a `Critical Steps` column would repeat the names
            # with no new fact in it.
            "failed_steps": "Failed Steps",
            "critical_failed_steps": "Failed Steps",
        }
        self.assertEqual(
            set(signature.parameters),
            set(column_for_parameter),
            "a new input to the Overall verdict needs a column mapping here",
        )
        for parameter, column in column_for_parameter.items():
            with self.subTest(parameter=parameter):
                self.assertIn(column, DASHBOARD_DATABASES[OPS_RUNS])


class RecordRunTests(unittest.TestCase):
    def setUp(self):
        self.transport = InMemoryNotionTransport()
        self.client = NotionClient(transport=self.transport, database_id="ops-runs-db")

    def _record(self, client, **overrides):
        kwargs = dict(
            run_id="run-1",
            run_at=datetime(2026, 8, 1, 12, 0),
            intake_summary=_FakeIntake(moved=("a.json",)),
            collector_summary=_FakeCollector(accepted=2),
            scheduler_result=_FakeScheduler(generated_dates=(date(2026, 8, 1),)),
            backup_entry=_FakeBackup(),
            notion_sync_results=[_sync(SyncStatus.NOTION_CREATED)],
        )
        kwargs.update(overrides)
        return record_run(client, **kwargs)

    def test_successful_record_creates_one_row(self):
        result = self._record(self.client)

        self.assertEqual(result.outcome, DashboardOutcome.RECORDED)
        self.assertIsNotNone(result.page_id)

    def test_unconfigured_client_is_skipped_not_failed(self):
        result = self._record(None)

        self.assertEqual(result.outcome, DashboardOutcome.SKIPPED_NOT_CONFIGURED)
        self.assertIsNone(result.error)

    def test_api_failure_is_returned_not_raised(self):
        # CEO Decision 4: a Dashboard failure must never interrupt Runtime.
        self.transport.fail_next_call = True

        result = self._record(self.client)

        self.assertEqual(result.outcome, DashboardOutcome.FAILED)
        self.assertIsNotNone(result.error)

    def test_a_malformed_result_object_fails_the_build_without_raising(self):
        """The OTHER except clause in record_run() (building properties, not
        the API call) — found via `python -m trace --count` to have zero
        coverage anywhere in the suite. `backup_entry.final_status` is
        accessed directly (not via getattr), so a caller passing a malformed
        result object (backup_entry=None here) must still hit CEO Decision
        ④'s "never raise" guarantee, one step earlier than every other test
        in this class exercises."""
        result = self._record(self.client, backup_entry=None)

        self.assertEqual(result.outcome, DashboardOutcome.FAILED)
        self.assertIsNotNone(result.error)
        self.assertIsNone(result.properties)

    def test_failed_record_carries_properties_for_retry(self):
        self.transport.fail_next_call = True

        result = self._record(self.client)

        self.assertIsNotNone(result.properties)
        self.assertEqual(set(result.properties), set(DASHBOARD_DATABASES[OPS_RUNS]))

    def test_a_renamed_source_field_fails_loudly_instead_of_recording_zero(self):
        """C32 §2: every Dashboard number was read with `getattr(x, name, 0)`.

        `app/runner.py` refuses that pattern for the sibling numbers it puts
        in the Run Manifest, and says why beside them: "a default would only
        be able to hide the day one is renamed — reporting 0 skipped files
        forever instead of failing". The Dashboard read all of its own
        numbers exactly that way.

        Measured before the fix, with a collector summary whose `accepted`
        had been renamed: `Accepted 0` for a run that accepted 50, `Overall
        OK`, on every run after the rename, with nothing anywhere saying so.

        Afterwards the missing attribute is a FAILED DashboardResult carrying
        the AttributeError — which `app/runner.py` logs as `DASHBOARD FAILED`
        and records as a component failure in the manifest. A Dashboard that
        stops and says why beats one that keeps publishing zeros.
        """
        class _RenamedCollectorSummary:
            accepted_count = 50   # was `accepted`
            duplicate = 0
            rejected = 0
            failed = 0

        result = self._record(self.client, collector_summary=_RenamedCollectorSummary())

        self.assertEqual(result.outcome, DashboardOutcome.FAILED)
        self.assertIn("accepted", result.error)
        # Nothing is queued for retry: re-sending a record that cannot be
        # built is a queue that never drains.
        self.assertIsNone(result.properties)

    def test_a_renamed_intake_field_fails_the_same_way(self):
        """The sibling half — `intake_summary.moved` had the same default."""
        class _RenamedIntakeSummary:
            promoted = ("a.json",)   # was `moved`

        result = self._record(self.client, intake_summary=_RenamedIntakeSummary())

        self.assertEqual(result.outcome, DashboardOutcome.FAILED)
        self.assertIn("moved", result.error)

    def test_sync_counts_split_success_from_retry(self):
        result = self._record(
            self.client,
            notion_sync_results=[
                _sync(SyncStatus.NOTION_CREATED, "E1"),
                _sync(SyncStatus.NOTION_UPDATED, "E2"),
                _sync(SyncStatus.NOTION_RETRY_REQUIRED, "E3"),
                _sync(SyncStatus.NOTION_FAILED, "E4"),
            ],
        )

        page = self.transport._pages[result.page_id]
        self.assertEqual(page["properties"]["Notion Synced"]["number"], 2)
        self.assertEqual(page["properties"]["Notion Retried"]["number"], 2)


class _FalseNegativeCreateTransport(InMemoryNotionTransport):
    """A create_page call that lands server-side (the page really is
    created) but raises anyway on the way back — e.g. the response was lost
    to a network glitch after Notion had already written it. Used to check
    the "can never produce two OPS_RUNS rows" claim in
    notion/dashboard_pending.py's module docstring."""

    def __init__(self, *args, fail_once=True, **kwargs):
        super().__init__(*args, **kwargs)
        self._fail_once = fail_once

    def create_page(self, database_id, properties):
        page = super().create_page(database_id, properties)
        if self._fail_once:
            self._fail_once = False
            raise ConnectionResetError("simulated: response lost after the write landed")
        return page


class RecordRunRetryDuplicationTests(unittest.TestCase):
    """GUARANTEE (was CHARACTERIZATION): one execution, at most one row.

    `dashboard_pending.py`'s docstring has always stated "one Runner
    execution can never produce two OPS_RUNS rows, whether it is recorded on
    the first attempt or the tenth". Neither `record_run()` nor
    `drain_pending()` enforced it — both called `client.create_project()`
    unconditionally, with no find-before-create step, unlike
    `notion.sync.ExecutionPlanSync`, which calls `find_project()` first for
    exactly this reason.

    The characterization this class used to hold said, in as many words, "if
    this test starts failing, a find-before-create guard was added and the
    docstring claim became true — rewrite it as the guarantee". That guard
    is now in `NotionClient.find_or_create_by_title()`, so this is that
    rewrite.

    The scenario is the one that actually happens: the write reaches Notion
    and the *response* is lost, so the caller sees an exception for a row
    that exists. Nothing downstream can tell the resulting pair apart —
    both rows are complete and both are plausible.
    """

    def test_a_false_negative_failure_then_a_successful_retry_makes_one_row(self):
        transport = _FalseNegativeCreateTransport()
        client = NotionClient(transport=transport, database_id="ops-runs-db")

        with tempfile.TemporaryDirectory() as tmp:
            pending_path = Path(tmp) / "dashboard_pending.json"

            first = record_run(
                client,
                run_id="run-dup",
                run_at=datetime(2026, 8, 8, 11, 0),
                intake_summary=_FakeIntake(),
                collector_summary=_FakeCollector(accepted=1),
                scheduler_result=_FakeScheduler(),
                backup_entry=_FakeBackup(),
                notion_sync_results=(),
            )
            self.assertEqual(first.outcome, DashboardOutcome.FAILED)
            save_pending(pending_path, run_id="run-dup", properties=first.properties)

            # The page from the "failed" first attempt already exists.
            self.assertEqual(len(transport._pages), 1)

            recorded, still_pending = drain_pending(pending_path, client)

            # Still reported as recorded — from the queue's point of view the
            # record did reach Notion, which is the truth.
            self.assertEqual((recorded, still_pending), (1, 0))
            # And there is still exactly one row for run-dup.
            self.assertEqual(len(transport._pages), 1)

    def test_record_run_itself_is_idempotent_for_one_run_id(self):
        """The same guarantee without the queue in the picture: a Runner
        re-invoked with the same run_id (a manual re-run of a partially
        failed execution) must not double the row either."""
        transport = InMemoryNotionTransport()
        client = NotionClient(transport=transport, database_id="ops-runs-db")

        for _ in range(3):
            result = record_run(
                client,
                run_id="run-same",
                run_at=datetime(2026, 8, 8, 11, 0),
                intake_summary=_FakeIntake(),
                collector_summary=_FakeCollector(accepted=1),
                scheduler_result=_FakeScheduler(),
                backup_entry=_FakeBackup(),
                notion_sync_results=(),
            )
            self.assertEqual(result.outcome, DashboardOutcome.RECORDED)

        self.assertEqual(len(transport._pages), 1)

    def test_two_different_runs_still_get_their_own_rows(self):
        """The guard must not collapse distinct executions — that would be
        the opposite defect and a worse one, since a missing run is
        unrecoverable while a duplicate is merely confusing."""
        transport = InMemoryNotionTransport()
        client = NotionClient(transport=transport, database_id="ops-runs-db")

        for run_id in ("run-a", "run-b", "run-c"):
            record_run(
                client,
                run_id=run_id,
                run_at=datetime(2026, 8, 8, 11, 0),
                intake_summary=_FakeIntake(),
                collector_summary=_FakeCollector(accepted=1),
                scheduler_result=_FakeScheduler(),
                backup_entry=_FakeBackup(),
                notion_sync_results=(),
            )

        self.assertEqual(len(transport._pages), 3)


class DashboardPendingTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "state" / "dashboard_pending.json"
        self.transport = InMemoryNotionTransport()
        self.client = NotionClient(transport=self.transport, database_id="ops-runs-db")

    def test_missing_file_is_empty(self):
        self.assertEqual(load_pending(self.path), [])

    def test_a_malformed_record_inside_otherwise_valid_json_raises_the_typed_error(self):
        """Coverage gap found via `python -m trace` this Sprint: the
        `except (AttributeError, KeyError, TypeError)` around
        `PendingDashboardRecord.from_dict()` had zero executions across the
        whole suite. Every existing corruption test uses invalid JSON text
        (`test_corrupt_dashboard_pending_no_longer_stops_the_runtime` in
        test_runner_failure_paths.py), which hits the earlier
        `except (OSError, ValueError)` around `json.loads()` instead --
        never this one, where the JSON itself parses fine but one entry is
        missing a required key.
        """
        import json

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"entries": [{"properties": {}, "queued_at": "x", "attempt_count": 0}]}),
            encoding="utf-8",
        )  # missing "run_id"

        with self.assertRaises(DashboardPendingError) as caught:
            load_pending(self.path)
        self.assertIn("malformed record", str(caught.exception))

    def test_save_then_load_round_trips(self):
        save_pending(self.path, run_id="r1", properties={"Run ID": {}})

        records = load_pending(self.path)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].run_id, "r1")
        self.assertEqual(records[0].attempt_count, 1)

    def test_same_run_id_is_upserted_never_duplicated(self):
        for _ in range(5):
            save_pending(self.path, run_id="r1", properties={"Run ID": {}})

        records = load_pending(self.path)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].attempt_count, 5)

    def test_drain_records_and_clears_pending(self):
        save_pending(self.path, run_id="r1", properties={"Run ID": {}})
        save_pending(self.path, run_id="r2", properties={"Run ID": {}})

        recorded, still_pending = drain_pending(self.path, self.client)

        self.assertEqual(recorded, 2)
        self.assertEqual(still_pending, 0)
        self.assertEqual(load_pending(self.path), [])

    def test_drain_keeps_records_that_fail_again(self):
        save_pending(self.path, run_id="r1", properties={"Run ID": {}})
        self.transport.fail_next_call = True

        recorded, still_pending = drain_pending(self.path, self.client)

        self.assertEqual(recorded, 0)
        self.assertEqual(still_pending, 1)
        remaining = load_pending(self.path)
        self.assertEqual(remaining[0].attempt_count, 2)  # incremented

    def test_drain_on_empty_queue_is_a_no_op(self):
        self.assertEqual(drain_pending(self.path, self.client), (0, 0))

    def test_drain_never_raises_even_if_every_attempt_fails(self):
        class ExplodingClient:
            def create_project(self, properties):
                raise RuntimeError("boom")

        save_pending(self.path, run_id="r1", properties={"Run ID": {}})

        recorded, still_pending = drain_pending(self.path, ExplodingClient())

        self.assertEqual(recorded, 0)
        self.assertEqual(still_pending, 1)


class RemovePendingCharacterizationTests(unittest.TestCase):
    """`dashboard_pending.remove_pending()` has no caller and, until now, no
    test either — `test_repository_hygiene.py` only asserted the absence of
    callers, which says nothing about whether the function works.

    That combination is the trap: an untested, unused, exported function
    looks available. The next person to need "drop one pending record" will
    reach for it and inherit whatever it actually does. So its behaviour is
    pinned here.

    It is NOT deleted and NOT wired in. `drain_pending()` rebuilds the
    remaining list and saves once, which is strictly better than calling
    this per record (one write instead of N), so adopting it would be a
    regression. Removing it is a deletion decision. Both are recorded in
    BACKLOG.md; this file just makes sure the thing is honest about itself.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "dashboard_pending.json"

    def _queue(self, *run_ids):
        for run_id in run_ids:
            save_pending(
                self.path,
                run_id=run_id,
                properties={"Run ID": {"title": [{"text": {"content": run_id}}]}},
                now=datetime(2026, 8, 10, 11, 0).astimezone(),
            )

    def test_it_removes_only_the_named_record(self):
        self._queue("RUN-A", "RUN-B", "RUN-C")

        remove_pending(self.path, "RUN-B")

        self.assertEqual(
            [r.run_id for r in load_pending(self.path)], ["RUN-A", "RUN-C"]
        )

    def test_removing_an_unknown_id_is_a_no_op(self):
        self._queue("RUN-A")
        before = self.path.read_text(encoding="utf-8")

        remove_pending(self.path, "NOT-QUEUED")

        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_it_does_not_write_when_nothing_matched(self):
        """The guard that makes it idempotent also keeps it from rewriting a
        file it has no change for."""
        self._queue("RUN-A")
        mtime_before = self.path.stat().st_mtime_ns

        remove_pending(self.path, "NOT-QUEUED")

        self.assertEqual(self.path.stat().st_mtime_ns, mtime_before)

    def test_removing_the_same_id_twice_is_safe(self):
        self._queue("RUN-A", "RUN-B")

        remove_pending(self.path, "RUN-A")
        remove_pending(self.path, "RUN-A")

        self.assertEqual([r.run_id for r in load_pending(self.path)], ["RUN-B"])

    def test_removing_the_last_record_leaves_an_empty_set_not_a_missing_file(self):
        self._queue("RUN-A")

        remove_pending(self.path, "RUN-A")

        self.assertTrue(self.path.exists())
        self.assertEqual(load_pending(self.path), [])

    def test_a_missing_file_is_an_empty_set_not_an_error(self):
        remove_pending(self.path, "RUN-A")

        self.assertFalse(self.path.exists())

    def test_a_corrupted_file_is_reported_rather_than_silently_rewritten(self):
        """Unlike `drain_pending()`, which must never interrupt the Runtime,
        this has no such contract — and silently overwriting a damaged file
        would destroy the evidence docs/10 §46 says to preserve."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{not json", encoding="utf-8")

        with self.assertRaises(DashboardPendingError):
            remove_pending(self.path, "RUN-A")

        self.assertEqual(self.path.read_text(encoding="utf-8"), "{not json")


class DiagnosisNeverRaisesTests(unittest.TestCase):
    """`diagnose_dashboard_bootstrap()` states it "never raises for an
    unusable workspace — an unusable workspace IS the answer it is meant to
    report".

    The `search_pages()` call honoured that; the `get_database_parent()`
    call above it did not. A network failure, an expired token, or a deleted
    reference database made the *diagnostic* explode — the tool an operator
    reaches for precisely because Notion is misbehaving.

    Also: this function was exported and tested with **zero production
    callers** until `init_notion.py` began printing it. A diagnosis nobody
    runs diagnoses nothing.
    """

    class _Exploding(NotionTransport):
        def __init__(self, exc):
            self.exc = exc

        def retrieve_database(self, database_id):
            raise self.exc

        def query_database(self, database_id, filter_):
            raise self.exc

        def create_page(self, database_id, properties):
            raise self.exc

        def update_page(self, page_id, properties):
            raise self.exc

        def update_database(self, database_id, properties):
            raise self.exc

        def create_database(self, parent_page_id, title, properties):
            raise self.exc

    def _diagnose(self, exc):
        client = NotionClient(transport=self._Exploding(exc), database_id="DB-1")
        return diagnose_dashboard_bootstrap(client)

    def test_an_unreachable_notion_is_reported_not_raised(self):
        diagnosis = self._diagnose(NotionAPIError("Notion API request timed out after 10s"))

        self.assertEqual(diagnosis.reference_parent_type, "unreachable")
        self.assertIn("timed out", diagnosis.required_action)

    def test_an_expired_token_is_reported_not_raised(self):
        diagnosis = self._diagnose(
            NotionAPIError("Notion API returned 401: Unauthorized", status_code=401)
        )

        self.assertEqual(diagnosis.readiness, BootstrapReadiness.NEEDS_SHARED_PAGE)
        self.assertIn("NOTION_API_TOKEN", diagnosis.required_action)

    def test_an_unexpected_exception_type_is_also_absorbed(self):
        """The contract is about the caller, not about which exception the
        transport happened to choose."""
        diagnosis = self._diagnose(RuntimeError("something nobody predicted"))

        self.assertEqual(diagnosis.reference_parent_type, "unreachable")
        self.assertFalse(diagnosis.search_available)

    def test_an_unreachable_diagnosis_promises_nothing_about_pages(self):
        diagnosis = self._diagnose(NotionAPIError("down"))

        self.assertEqual(diagnosis.hostable_pages, ())
        self.assertIsNone(diagnosis.resolved_parent_page_id)

    def test_the_setup_cli_actually_calls_the_diagnosis(self):
        """The gap that made all of the above moot."""
        entrypoint = (
            Path(__file__).resolve().parents[1] / "init_notion.py"
        ).read_text(encoding="utf-8")

        self.assertIn("diagnose_dashboard_bootstrap(client)", entrypoint)

    def test_the_setup_cli_does_not_create_anything_from_the_diagnosis(self):
        """Read-only and advisory: choosing a parent Page is an operator
        decision, and the Dashboard is optional."""
        entrypoint = (
            Path(__file__).resolve().parents[1] / "init_notion.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("bootstrap_dashboard_databases", entrypoint)
        self.assertNotIn("create_database", entrypoint)


class SetupCliPrintsRemoteTextSafelyTests(unittest.TestCase):
    """C32 §3: `init_notion.py` printed four remote-authored strings raw.

    C31 §7 and §8 found this exact shape at `run_company_ops.py`'s two sinks
    and fixed it there. Nobody looked at the sibling entrypoint, which by
    then printed *more* remote text than the one that was audited: a Notion
    error body, the reference database's `parent.type`, Page titles written
    by whoever named those Pages, and a `required_action` that embeds `{exc}`
    on the unreachable path.

    The guard cannot live inside `notion/` — that package may import only
    `events` (LayeringInvariantTests) — so it belongs at the sink, which is
    what `run_company_ops.py` already concluded for the same reason.

    Both halves are exercised end to end through `main()`, because a unit
    test of the helper would not have caught the original defect: the helper
    did not exist and every `print()` was its own unguarded sink.
    """

    FORGED_TITLE = (
        "Ops Page\n  다음 할 일     : 없음 — Dashboard 설정이 이미 끝났습니다"
    )
    # Assembled at runtime rather than written out. A literal token here
    # would be a real `ntn_…` string in a tracked file, which
    # `test_repository_hygiene.SecretExposureGuardTests` refuses — correctly,
    # and by exactly the patterns this test is about.
    FAKE_TOKEN = "ntn_" + "a" * 28
    LEAKY_BODY = (
        "Notion API returned 502: Bad Gateway | <html>\n"
        f"Authorization: Bearer {FAKE_TOKEN}\n</html>"
    )

    class _RenameFailsTransport(InMemoryNotionTransport):
        """A workspace whose Title rename is answered by a proxy, not Notion."""

        def __init__(self, body, **kwargs):
            super().__init__(**kwargs)
            self._body = body

        def update_database(self, database_id, properties):
            if all(set(v) == {"name"} for v in properties.values()):
                raise NotionAPIError(self._body, status_code=502)
            return super().update_database(database_id, properties)

    def _run_main(self, transport):
        import contextlib
        import importlib
        import io

        init_notion = importlib.import_module("init_notion")

        out, err = io.StringIO(), io.StringIO()
        original_transport_factory = init_notion.RealNotionTransport
        original_environ = dict(os.environ)
        try:
            init_notion.RealNotionTransport = lambda **kwargs: transport
            os.environ["NOTION_API_TOKEN"] = "ntn_" + "testtokenvalue0000"
            os.environ["NOTION_PROJECTS_DATABASE_ID"] = "projects-db"
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                init_notion.main()
        finally:
            init_notion.RealNotionTransport = original_transport_factory
            os.environ.clear()
            os.environ.update(original_environ)
        return out.getvalue(), err.getvalue()

    def test_a_page_title_cannot_forge_the_line_an_operator_acts_on(self):
        transport = InMemoryNotionTransport(initial_properties={"Project": {"type": "title", "title": {}}})
        transport.searchable_pages = [
            {
                "id": "page-1",
                "parent": {"type": "workspace"},
                "properties": {
                    "title": {
                        "type": "title",
                        "title": [{"plain_text": self.FORGED_TITLE}],
                    }
                },
            }
        ]

        stdout, _ = self._run_main(transport)

        # The forged line's text still appears — escaped, on the Page's own
        # row. What must not appear is a *second line* whose whole content is
        # the forgery, which is what an operator would read as the verdict.
        self.assertIn("\\n", stdout)
        for line in stdout.splitlines():
            with self.subTest(line=line):
                self.assertFalse(
                    line.strip().startswith("다음 할 일") and "이미 끝났습니다" in line,
                    "a Page title forged the report's conclusion line",
                )
        # And the real conclusion is still there, exactly once.
        conclusions = [l for l in stdout.splitlines() if l.startswith("  다음 할 일")]
        self.assertEqual(len(conclusions), 1)

    def test_a_proxy_response_body_is_redacted_and_kept_to_one_line(self):
        transport = self._RenameFailsTransport(
            self.LEAKY_BODY, initial_properties={"Name": {"type": "title", "title": {}}}
        )

        stdout, stderr = self._run_main(transport)
        combined = stdout + stderr

        self.assertNotIn(self.FAKE_TOKEN, combined)
        self.assertIn("[REDACTED]", combined)
        # `format_report()` promises one line per Property; the body carried
        # two newlines, and the report must still have exactly one row per
        # Property name.
        rows = [l for l in stdout.splitlines() if " ...." in l]
        self.assertEqual(len(rows), len(TARGET_PROPERTIES), rows)

    def test_an_unreachable_workspace_does_not_leak_its_error_body_either(self):
        """The `required_action` path: `diagnose_dashboard_bootstrap()`
        embeds `{exc}` in the sentence this script prints last."""
        leaked = "ntn_" + "b" * 24

        class _LeakyHealthCheck(InMemoryNotionTransport):
            calls = 0

            def retrieve_database(self, database_id):
                _LeakyHealthCheck.calls += 1
                # Health check and bootstrap succeed; the diagnosis' own
                # lookup is the one that fails, which is the only path that
                # reaches `required_action`'s `{exc}` branch.
                if _LeakyHealthCheck.calls >= 3:
                    raise NotionAPIError(
                        "Notion API returned 502: Bad Gateway | "
                        f"NOTION_API_TOKEN={leaked}\nforged line"
                    )
                return super().retrieve_database(database_id)

        stdout, _ = self._run_main(
            _LeakyHealthCheck(initial_properties={"Project": {"type": "title", "title": {}}})
        )

        self.assertNotIn(leaked, stdout)
        self.assertNotIn("\nforged line", stdout)

    def test_the_page_list_says_when_it_is_truncated(self):
        """Five of twelve shared Pages, printed as if they were all of them,
        tells an operator who cannot find theirs that the sharing did not
        take. `NotionTransport.search_pages()` is itself capped at Notion's
        first /search page (100)."""
        transport = InMemoryNotionTransport(initial_properties={"Project": {"type": "title", "title": {}}})
        transport.searchable_pages = [
            {
                "id": f"page-{i}",
                "parent": {"type": "workspace"},
                "properties": {
                    "title": {"type": "title", "title": [{"plain_text": f"Page {i}"}]}
                },
            }
            for i in range(12)
        ]

        stdout, _ = self._run_main(transport)

        self.assertIn("외 7개", stdout)


ALL_DASHBOARD_DATABASES = [
    OPS_RUNS,
    OPS_BACKUP,
    OPS_NOTION_SYNC,
    OPS_RISK,
    OPS_READINESS,
]


class PartialBootstrapTests(unittest.TestCase):
    """`bootstrap_dashboard_databases()` creating several databases in a loop.

    Every test here passes `only=ALL_DASHBOARD_DATABASES` explicitly. The
    default is `CONTRACTED_DATABASES` — one name — and a one-element loop
    cannot fail part-way through, which is the whole subject of this class.
    Asking for all five is now a deliberate act (C33 §2), and that is exactly
    the caller this class stands in for.

    A failure part-way used to let the exception through and discard
    `created` — but those databases really exist in the operator's
    workspace, and this function is not allowed to delete them. The
    docstring says the caller "is responsible for not re-creating databases
    it already has (their ids belong in configuration)", which is impossible
    without the ids. Retrying therefore produced a second OPS_RUNS, a second
    OPS_BACKUP, and so on, with nothing able to say which is which.

    This is the last `src/` code path that had no coverage. The happy path
    still needs a real Notion workspace; every failure path does not.
    """

    class _FailAfter(InMemoryNotionTransport):
        def __init__(self, limit, exc=None, **kwargs):
            super().__init__(**kwargs)
            self.limit = limit
            self.calls = 0
            self.exc = exc or NotionAPIError("rate limited", status_code=429)

        def create_database(self, parent_page_id, title, properties):
            self.calls += 1
            if self.calls > self.limit:
                raise self.exc
            return super().create_database(parent_page_id, title, properties)

    class _NoId(InMemoryNotionTransport):
        def create_database(self, parent_page_id, title, properties):
            super().create_database(parent_page_id, title, properties)
            return {"object": "database"}  # no "id"

    def _client(self, transport):
        return NotionClient(transport=transport, database_id="projects-db")

    def test_the_ids_created_before_the_failure_survive(self):
        transport = self._FailAfter(2, parent={"type": "page_id", "page_id": "p"})

        with self.assertRaises(DashboardBootstrapPartialError) as caught:
            bootstrap_dashboard_databases(
                self._client(transport), only=ALL_DASHBOARD_DATABASES
            )

        self.assertEqual(
            sorted(caught.exception.created), [OPS_BACKUP, OPS_RUNS]
        )
        for database_id in caught.exception.created.values():
            self.assertTrue(database_id)

    def test_the_failure_names_which_database_did_not_get_created(self):
        transport = self._FailAfter(2, parent={"type": "page_id", "page_id": "p"})

        with self.assertRaises(DashboardBootstrapPartialError) as caught:
            bootstrap_dashboard_databases(
                self._client(transport), only=ALL_DASHBOARD_DATABASES
            )

        self.assertEqual(caught.exception.failed_database, OPS_NOTION_SYNC)

    def test_the_message_tells_the_operator_how_to_retry(self):
        transport = self._FailAfter(1, parent={"type": "page_id", "page_id": "p"})

        with self.assertRaises(DashboardBootstrapPartialError) as caught:
            bootstrap_dashboard_databases(
                self._client(transport), only=ALL_DASHBOARD_DATABASES
            )

        message = str(caught.exception)
        self.assertIn("only=", message)
        self.assertIn(OPS_RUNS, message)

    def test_the_original_cause_is_preserved(self):
        original = NotionAPIError("Notion API returned 401: Unauthorized", status_code=401)
        transport = self._FailAfter(
            0, exc=original, parent={"type": "page_id", "page_id": "p"}
        )

        with self.assertRaises(DashboardBootstrapPartialError) as caught:
            bootstrap_dashboard_databases(
                self._client(transport), only=ALL_DASHBOARD_DATABASES
            )

        self.assertIs(caught.exception.cause, original)
        self.assertIs(caught.exception.__cause__, original)

    def test_failing_on_the_very_first_database_reports_an_empty_map(self):
        transport = self._FailAfter(0, parent={"type": "page_id", "page_id": "p"})

        with self.assertRaises(DashboardBootstrapPartialError) as caught:
            bootstrap_dashboard_databases(
                self._client(transport), only=ALL_DASHBOARD_DATABASES
            )

        self.assertEqual(caught.exception.created, {})
        self.assertIn("none", str(caught.exception))

    def test_a_response_without_an_id_fails_loudly(self):
        """Recording None would make `database_id()` later report "not
        created" for a database that exists — the same duplicate trap by a
        quieter route."""
        transport = self._NoId(parent={"type": "page_id", "page_id": "p"})

        with self.assertRaises(DashboardBootstrapPartialError) as caught:
            bootstrap_dashboard_databases(
                self._client(transport), only=ALL_DASHBOARD_DATABASES
            )

        self.assertEqual(caught.exception.failed_database, OPS_RUNS)
        self.assertIn("no id", str(caught.exception))

    def test_retrying_with_only_the_remaining_names_completes_the_set(self):
        """The recovery the error message describes, carried out."""
        transport = self._FailAfter(2, parent={"type": "page_id", "page_id": "p"})
        client = self._client(transport)

        with self.assertRaises(DashboardBootstrapPartialError) as caught:
            bootstrap_dashboard_databases(client, only=ALL_DASHBOARD_DATABASES)
        already = caught.exception.created

        transport.limit = 99  # the outage clears
        remaining = [n for n in DASHBOARD_DATABASES if n not in already]
        result = bootstrap_dashboard_databases(client, only=remaining)

        everything = {**already, **result.created}
        self.assertEqual(sorted(everything), sorted(DASHBOARD_DATABASES))
        # Five databases in the workspace, not eight.
        self.assertEqual(len(transport.created_databases), len(DASHBOARD_DATABASES))

    def test_a_missing_parent_page_still_raises_its_own_error(self):
        """`resolve_parent_page_id()` runs before the loop, so that failure
        must keep its own type rather than being reported as a partial
        bootstrap — nothing was created."""
        transport = InMemoryNotionTransport()  # workspace root

        with self.assertRaises(DashboardParentError):
            bootstrap_dashboard_databases(
                self._client(transport), only=ALL_DASHBOARD_DATABASES
            )

        self.assertEqual(transport.created_databases, {})

    def test_a_successful_bootstrap_is_unchanged(self):
        transport = InMemoryNotionTransport(parent={"type": "page_id", "page_id": "p"})

        result = bootstrap_dashboard_databases(
                self._client(transport), only=ALL_DASHBOARD_DATABASES
            )

        self.assertEqual(sorted(result.created), sorted(DASHBOARD_DATABASES))
        for name in DASHBOARD_DATABASES:
            self.assertIsNotNone(result.database_id(name))


class DrainPendingReasonTests(unittest.TestCase):
    """Why a pending Dashboard record failed used to be discarded.

    `drain_pending()`'s `except Exception:` swallowed the exception whole,
    so a record Notion refuses permanently — a Select value it will not
    accept, a deleted database — came back every run with nothing changed
    but `attempt_count`, and no trace anywhere of what Notion said. An
    operator watching the queue could see it never drained and could not see
    why. That is the diagnostic blank BUG-13 closed for Notion Sync; the
    Dashboard queue still had it.

    `DrainPendingResult` is a tuple subclass so the existing
    `recorded, still_pending = drain_pending(...)` call sites are untouched
    (the same technique `app.runner.RunResult` uses).
    """

    class FailingClient:
        def __init__(self, message="Notion says no"):
            self.message = message

        def find_or_create_by_title(self, *, property_name, value, properties):
            raise RuntimeError(self.message)

    class WorkingClient:
        def find_or_create_by_title(self, *, property_name, value, properties):
            return {"id": "page-1"}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "dashboard_pending.json"

    def _seed(self, count=1):
        for i in range(count):
            save_pending(self.path, run_id=f"R-{i}", properties={"i": i})

    def test_the_result_still_unpacks_as_the_old_two_tuple(self):
        self._seed(2)

        recorded, still_pending = drain_pending(self.path, self.WorkingClient())

        self.assertEqual((recorded, still_pending), (2, 0))

    def test_a_failure_reason_reaches_the_caller(self):
        self._seed(1)

        result = drain_pending(self.path, self.FailingClient("400 invalid select"))

        self.assertEqual((result.recorded, result.still_pending), (0, 1))
        self.assertIn("400 invalid select", result.last_reason)

    def test_a_clean_drain_has_no_reason(self):
        """A reason that is always present is one nobody reads."""
        self._seed(2)

        result = drain_pending(self.path, self.WorkingClient())

        self.assertIsNone(result.last_reason)

    def test_an_empty_queue_has_no_reason(self):
        result = drain_pending(self.path, self.WorkingClient())

        self.assertEqual((result.recorded, result.still_pending), (0, 0))
        self.assertIsNone(result.last_reason)

    def test_a_corrupt_queue_file_is_distinguishable_from_an_empty_one(self):
        """Both report (0, 0) — the numbers cannot tell them apart, and one
        of them means a file on disk needs a human."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{not json", encoding="utf-8")

        result = drain_pending(self.path, self.WorkingClient())

        self.assertEqual((result.recorded, result.still_pending), (0, 0))
        self.assertIn("corrupted", result.last_reason)

    def test_it_still_never_raises_on_a_failing_client(self):
        self._seed(3)

        result = drain_pending(self.path, self.FailingClient())

        self.assertEqual(result.still_pending, 3)


# --------------------------------------------------------------------- C36
# Upgrading an OPS_RUNS database that was created before the schema grew.


# Every column added to OPS_RUNS after `bootstrap_dashboard_databases()`
# had already been run once — C32's two, C33's two, C37's one. Named here,
# rather than derived, because the point of these tests is the gap between
# two *versions* of the schema, and a derivation from today's schema could
# only ever produce today's schema.
#
# The list grows every time the row learns to say something new, which is
# the whole reason `bootstrap_dashboard_properties()` has to exist rather
# than being a one-off migration script for C32/C33.
_COLUMNS_ADDED_AFTER_C31 = (
    "Transport Blocked",
    "Notion Skipped",
    "Notion Unreadable",
    "Notion Queued",
    "Failed Steps",
)


def _pre_widening_schema(title_name=RUN_ID_PROPERTY):
    """OPS_RUNS as it stood before C32/C33, in `retrieve_database` shape.

    `title_name` defaults to what `bootstrap_dashboard_databases()` creates.
    Pass "Name" for the other real starting point: a database a person made
    by hand in the Notion UI, still carrying the Title name Notion forces on
    every new database.
    """
    schema = {}
    for name, payload in DASHBOARD_DATABASES[OPS_RUNS].items():
        if name in _COLUMNS_ADDED_AFTER_C31:
            continue
        kind = next(iter(payload))
        key = title_name if name == RUN_ID_PROPERTY else name
        schema[key] = {"type": kind, kind: {}}
    return schema


class DashboardPropertyBootstrapTests(unittest.TestCase):
    """`bootstrap_dashboard_properties()` — the way out of a schema that no
    longer matches the code writing to it.

    Every assertion here is about a database that *already exists*. The
    create-from-nothing path is `bootstrap_dashboard_databases()`, tested
    above; this is the path an operator who ran that command in C31 needs
    and did not have.
    """

    def _client(self, schema):
        transport = InMemoryNotionTransport(initial_properties=schema)
        return NotionClient(transport=transport, database_id="OPS-RUNS-DB"), transport

    def test_a_pre_widening_database_gains_exactly_the_columns_it_lacks(self):
        client, transport = self._client(_pre_widening_schema())

        result = bootstrap_dashboard_properties(client)

        self.assertEqual(set(result.created), set(_COLUMNS_ADDED_AFTER_C31))
        self.assertEqual(result.failed, ())
        # The Title was already right, so it is SKIPPED — not renamed, and
        # not counted as created.
        self.assertEqual(result.skipped, (RUN_ID_PROPERTY,))
        self.assertEqual(
            set(result.existing),
            set(DASHBOARD_DATABASES[OPS_RUNS]) - set(_COLUMNS_ADDED_AFTER_C31) - {RUN_ID_PROPERTY},
        )
        # And the database now matches the schema `record_run()` writes.
        self.assertEqual(
            set(client.get_database_schema()), set(DASHBOARD_DATABASES[OPS_RUNS])
        )
        # Created with the payload the schema declares, not a guessed type —
        # a Number where the row writes Rich Text is a 400 that survives the
        # upgrade and looks like the upgrade did not work.
        for name in _COLUMNS_ADDED_AFTER_C31:
            self.assertEqual(
                transport._schema_properties[name],
                DASHBOARD_DATABASES[OPS_RUNS][name],
                name,
            )

    def test_an_up_to_date_database_is_left_completely_alone(self):
        """Not "creates nothing" — *calls* nothing.

        An update_database request that happens to be a no-op is still a
        write against the operator's real workspace, and still shows up in
        Notion's page history. Asserting on the resulting schema cannot tell
        the two apart, so this asserts on the call.
        """
        current = {
            name: {"type": next(iter(payload)), next(iter(payload)): {}}
            for name, payload in DASHBOARD_DATABASES[OPS_RUNS].items()
        }
        client, transport = self._client(current)
        calls = []
        original = transport.update_database
        transport.update_database = lambda *a, **k: (calls.append(a), original(*a, **k))[1]

        result = bootstrap_dashboard_properties(client)

        self.assertEqual(calls, [])
        self.assertEqual(result.created, ())
        self.assertEqual(result.renamed, ())
        self.assertEqual(result.failed, ())
        self.assertEqual(result.skipped, (RUN_ID_PROPERTY,))

    def test_a_hand_made_database_still_titled_name_is_renamed_to_run_id(self):
        """docs/13 lets the operator create OPS_RUNS by hand. Notion then
        forces the Title to be called "Name", and the API cannot create a
        second Title — so renaming is not a convenience here, it is the only
        move available. Same V1.1 exception `notion.bootstrap` already makes
        for PROJECTS, reused rather than re-decided."""
        client, transport = self._client(_pre_widening_schema(title_name="Name"))

        result = bootstrap_dashboard_properties(client)

        self.assertEqual(result.renamed, (RUN_ID_PROPERTY,))
        schema = client.get_database_schema()
        self.assertNotIn("Name", schema)
        self.assertEqual(schema[RUN_ID_PROPERTY]["type"], "title")
        # Exactly one Title survives — a duplicate would be un-writable.
        titles = [n for n, d in schema.items() if d.get("type") == "title"]
        self.assertEqual(titles, [RUN_ID_PROPERTY])

    def test_a_column_whose_name_the_rename_freed_is_still_created(self):
        """The re-read after a rename, measured rather than asserted about.

        If the Title was called "Overall", renaming it to "Run ID" *removes*
        an "Overall" from the schema. Diffing against the pre-rename snapshot
        would see "Overall" and report EXISTS, leaving the database without
        the Select column every row writes — and `record_run()` would 400 on
        the one property this command was run to fix.
        """
        schema = _pre_widening_schema()
        del schema[RUN_ID_PROPERTY]
        # The Title *is* the only "Overall" there is — a database can hold
        # exactly one property of that name, and here it is the Title.
        schema["Overall"] = {"type": "title", "title": {}}
        client, _ = self._client(schema)

        result = bootstrap_dashboard_properties(client)

        self.assertEqual(result.renamed, (RUN_ID_PROPERTY,))
        self.assertIn("Overall", result.created)
        self.assertEqual(client.get_database_schema()["Overall"], {"select": {}})

    def test_an_existing_property_is_never_redefined(self):
        """A Select the operator has configured with real options keeps them.
        The target payload for every Select here is a bare `{"select": {}}`,
        which would erase the options if it were ever sent for a property
        that already exists."""
        schema = _pre_widening_schema()
        schema["Overall"] = {
            "type": "select",
            "select": {"options": [{"name": "OK", "color": "green"}]},
        }
        client, transport = self._client(schema)

        bootstrap_dashboard_properties(client)

        self.assertEqual(
            transport._schema_properties["Overall"],
            {"type": "select", "select": {"options": [{"name": "OK", "color": "green"}]}},
        )

    def test_a_failing_create_call_is_not_reported_as_success(self):
        """Deliberately unlike `record_run()`, which swallows everything
        because CEO Decision ④ forbids a Dashboard failure from stopping the
        Runtime. Nothing is running here — an operator typed a command — and
        the worst outcome would be a printed report claiming columns exist
        that Notion refused to create. `bootstrap_database()` makes the same
        choice for PROJECTS.
        """
        client, transport = self._client(_pre_widening_schema())
        transport.fail_next_method = "update_database"

        with self.assertRaises(NotionAPIError):
            bootstrap_dashboard_properties(client)

        for name in _COLUMNS_ADDED_AFTER_C31:
            self.assertNotIn(name, client.get_database_schema())

    def test_a_rename_failure_is_reported_and_the_rest_still_runs(self):
        """The Title branch is the one exception: `_bootstrap_title_property()`
        absorbs its NotionAPIError. The four missing columns are still worth
        creating, and the report says the Title was not."""
        client, transport = self._client(_pre_widening_schema(title_name="Name"))
        transport.fail_next_method = "update_database"

        result = bootstrap_dashboard_properties(client)

        self.assertEqual(result.failed, (RUN_ID_PROPERTY,))
        self.assertEqual(set(result.created), set(_COLUMNS_ADDED_AFTER_C31))
        self.assertIn("Name", client.get_database_schema())

    def test_the_report_is_ordered_like_the_schema(self):
        client, _ = self._client(_pre_widening_schema())

        result = bootstrap_dashboard_properties(client)

        self.assertEqual(
            [r.name for r in result.reports], list(DASHBOARD_DATABASES[OPS_RUNS])
        )

    def test_running_it_twice_changes_nothing_the_second_time(self):
        client, _ = self._client(_pre_widening_schema())

        bootstrap_dashboard_properties(client)
        second = bootstrap_dashboard_properties(client)

        self.assertEqual(second.created, ())
        self.assertEqual(second.renamed, ())
        self.assertEqual(second.failed, ())
        self.assertEqual(
            set(second.existing), set(DASHBOARD_DATABASES[OPS_RUNS]) - {RUN_ID_PROPERTY}
        )


class _SchemaEnforcingTransport(InMemoryNotionTransport):
    """The one behaviour of real Notion the in-memory double does not have:
    a page write naming a property the database does not define is rejected
    with a 400 ("X is not a property that exists").

    Without it every test in this file passes against a database missing
    half its columns, which is precisely the situation C36 exists for — so
    the bug it guards against could not be reproduced by the double that
    every other test here uses.
    """

    def create_page(self, database_id, properties):
        self._reject_unknown(properties)
        return super().create_page(database_id, properties)

    def update_page(self, page_id, properties):
        self._reject_unknown(properties)
        return super().update_page(page_id, properties)

    def _reject_unknown(self, properties):
        for name in properties:
            if name not in self._schema_properties:
                raise NotionAPIError(
                    f"{name} is not a property that exists", status_code=400
                )


class _TypeEnforcingTransport(_SchemaEnforcingTransport):
    """The **second** behaviour of real Notion the doubles did not have.

    `_SchemaEnforcingTransport` rejects a property the database does not
    define. Notion also rejects one whose *value shape* does not match the
    declared type — a `{"rich_text": [...]}` written into a `number` column
    is a 400, and every double in this suite accepted it.

    That is the failure mode `DashboardSchemaMappingTests` reasons about
    ("A name or type mismatch ... would stay invisible until the day the
    Dashboard is finally wired — and then every run would fail with an HTTP
    400"). It checked the two dicts against each other; nothing drove a real
    write through a double that would say no. This does.
    """

    def _reject_unknown(self, properties):
        super()._reject_unknown(properties)
        for name, value in properties.items():
            declared = self._schema_properties[name].get("type")
            if declared is None:
                continue
            written = next(iter(value), None)
            if written != declared:
                raise NotionAPIError(
                    f"{name} is expected to be {declared}, got {written}",
                    status_code=400,
                )


def _full_schema():
    """`OPS_RUNS` exactly as `DASHBOARD_DATABASES` declares it, in
    `retrieve_database` shape."""
    return {
        name: {"type": next(iter(payload)), next(iter(payload)): {}}
        for name, payload in DASHBOARD_DATABASES[OPS_RUNS].items()
    }


class EveryValueMatchesItsColumnTypeTests(unittest.TestCase):
    """C49: a real run, written through a double that enforces column types.

    The Dashboard is waiting on a Workspace, not on code (BACKLOG A-8), so
    the useful thing to remove before that day is a class of first-run
    surprise. A wrong value shape is the one that costs most: `record_run()`
    never raises, so the row is queued to `dashboard_pending.json` and
    retried — failing identically — on every subsequent run.
    """

    def _client(self):
        return NotionClient(
            transport=_TypeEnforcingTransport(initial_properties=_full_schema()),
            database_id="OPS-RUNS-DB",
        )

    def _record(self, client, **overrides):
        kwargs = dict(
            run_id="RUN-C49",
            run_at=datetime(2026, 8, 20, 9, 0, 0),
            intake_summary=_FakeIntake(moved=("a.json",)),
            collector_summary=_FakeCollector(accepted=2),
            scheduler_result=_FakeScheduler(),
            backup_entry=_FakeBackup(),
            notion_sync_results=(_sync(SyncStatus.NOTION_CREATED),),
            desktops_reporting="DESKTOP_1:2",
            role_mismatches=1,
            failed_steps=("monthly",),
        )
        kwargs.update(overrides)
        return record_run(client, **kwargs)

    def test_a_whole_run_is_accepted_by_a_type_enforcing_database(self):
        result = self._record(self._client())

        self.assertIs(result.outcome, DashboardOutcome.RECORDED)

    def test_the_control_tower_columns_are_accepted_too(self):
        """The two the Dashboard Model projects — one rich_text, one number.
        Swapping them is the exact mistake this double now catches."""
        from controltower import (
            OPS_RUNS_CONTROL_TOWER_COLUMNS,
            build_company_rollup,
            build_dashboard,
            ops_runs_fields,
        )

        fields = ops_runs_fields(
            build_dashboard(
                build_company_rollup(events=[], now=datetime(2026, 8, 20, 9, 0, 0)),
                now=datetime(2026, 8, 20, 9, 0, 0),
            )
        )
        result = self._record(self._client(), **fields)

        self.assertIs(result.outcome, DashboardOutcome.RECORDED)
        self.assertEqual(set(fields), set(OPS_RUNS_CONTROL_TOWER_COLUMNS))

    def test_the_double_actually_rejects_a_mismatched_type(self):
        """Guards the guard — a double that accepts everything would make
        the two tests above meaningless."""
        client = self._client()

        with self.assertRaises(NotionAPIError) as caught:
            client.create_project(
                {"Role Mismatches": {"rich_text": [{"text": {"content": "1"}}]}}
            )

        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("expected to be number", str(caught.exception))

    def test_it_still_rejects_an_unknown_column(self):
        """The behaviour inherited from `_SchemaEnforcingTransport` must not
        be lost by overriding its hook."""
        client = self._client()

        with self.assertRaises(NotionAPIError) as caught:
            client.create_project({"No Such Column": {"number": 1}})

        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("not a property that exists", str(caught.exception))

    def test_every_declared_column_is_writable_with_its_own_builder(self):
        """Each property `build_ops_run_properties()` emits, checked against
        its declared type one at a time — so a failure names the column
        rather than the whole row."""
        from notion.dashboard import build_ops_run_properties

        properties = build_ops_run_properties(
            run_id="RUN-C49",
            run_at=datetime(2026, 8, 20, 9, 0, 0).astimezone(),
            transport_moved=1,
            transport_blocked=0,
            accepted=2,
            duplicate=0,
            rejected=0,
            failed=0,
            scheduler_status="COMPLETED",
            generated_days=1,
            reused_days=0,
            backup_status="BACKUP_SUCCESS",
            deleted_files=0,
            notion_synced=2,
            notion_skipped=0,
            notion_retried=0,
            notion_unreadable=0,
            notion_queued=0,
            # A sequence, not a string: `Failed Steps` is rich_text but the
            # builder joins the names itself, and passing the rendered form
            # would test the test rather than the builder. `Overall` is not a
            # parameter at all — the builder derives the verdict from the
            # numbers above (C37), which is the property that keeps the row
            # from contradicting the run's own exit code.
            failed_steps=("monthly",),
            desktops_reporting="DESKTOP_1:2",
            role_mismatches=1,
        )
        schema = _full_schema()

        for name, value in sorted(properties.items()):
            with self.subTest(column=name):
                self.assertIn(name, schema)
                self.assertEqual(next(iter(value)), schema[name]["type"])


class DashboardMigrationClosesTheLoopTests(unittest.TestCase):
    """Why the command exists, end to end: the same run recorded against the
    same database, before and after."""

    def _record(self, client):
        return record_run(
            client,
            run_id="RUN-C36",
            run_at=datetime(2026, 8, 17, 9, 0, 0),
            intake_summary=_FakeIntake(moved=("a.json",)),
            collector_summary=_FakeCollector(accepted=1),
            scheduler_result=_FakeScheduler(),
            backup_entry=_FakeBackup(),
            notion_sync_results=(_sync(SyncStatus.NOTION_CREATED),),
        )

    def test_a_pre_widening_database_rejects_every_run_until_it_is_upgraded(self):
        transport = _SchemaEnforcingTransport(initial_properties=_pre_widening_schema())
        client = NotionClient(transport=transport, database_id="OPS-RUNS-DB")

        before = self._record(client)
        self.assertEqual(before.outcome, DashboardOutcome.FAILED)
        # The reason names the column, which is what makes the failure
        # actionable — and what the runner logs verbatim (C32 §11).
        self.assertIn("Transport Blocked", before.error)
        # The row is not lost: the Runner queues exactly these properties.
        self.assertIsNotNone(before.properties)

        bootstrap_dashboard_properties(client)

        after = self._record(client)
        self.assertEqual(after.outcome, DashboardOutcome.RECORDED)
        self.assertIsNotNone(after.page_id)

    def test_the_upgraded_database_accepts_a_queued_row_too(self):
        """The pending row written while the schema was stale is the one an
        operator most wants back, and it is drained through the same client.
        """
        transport = _SchemaEnforcingTransport(initial_properties=_pre_widening_schema())
        client = NotionClient(transport=transport, database_id="OPS-RUNS-DB")
        failed = self._record(client)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dashboard_pending.json"
            save_pending(path, run_id=failed.run_id, properties=failed.properties)

            # Draining before the upgrade fails again — the schema, not the
            # queue, is what is broken.
            recorded, still_pending = drain_pending(path, client)
            self.assertEqual((recorded, still_pending), (0, 1))

            bootstrap_dashboard_properties(client)

            recorded, still_pending = drain_pending(path, client)
            self.assertEqual((recorded, still_pending), (1, 0))
            self.assertEqual(load_pending(path), [])


class WorkspaceSearchPaginationTests(unittest.TestCase):
    """C32 §21: `/search` was asked for one page and treated as the whole
    workspace.

    Notion returns at most 100 results per `/search` request and reports
    `has_more` / `next_cursor`. `RealNotionTransport.search_pages()` sent
    `page_size: 100` and read only `results`, so a workspace with more than
    100 shared pages produced a truncated list with no sign of it.

    That matters because of the one question the list exists to answer.
    `diagnose_dashboard_bootstrap()` uses it to decide between
    NEEDS_PARENT_CHOICE ("a shared Page exists, pick one") and
    NEEDS_SHARED_PAGE ("share a Page first"), and a truncated list answers
    "is the Company Ops page shared?" with a confident, wrong "no" — sending
    the operator to re-share a Page that is already shared.

    Bounded, not unbounded: this runs inside an operator command against a
    remote API, which is exactly where a `while` loop becomes a hang. The
    stop is reported rather than hidden.
    """

    class _PagedTransport(RealNotionTransport):
        """Answers `/search` from a scripted list of pages."""

        def __init__(self, responses, **kwargs):
            super().__init__(api_token="ntn_" + "t" * 20, **kwargs)
            self.responses = list(responses)
            self.requests = []

        def _request(self, method, path, body=None):
            self.requests.append((method, path, dict(body or {})))
            return self.responses[len(self.requests) - 1]

    @staticmethod
    def _page(index):
        return {"id": f"page-{index}", "parent": {"type": "workspace"}, "properties": {}}

    def test_a_single_page_of_results_makes_one_request(self):
        transport = self._PagedTransport(
            [{"results": [self._page(1)], "has_more": False}]
        )

        self.assertEqual(len(transport.search_pages()), 1)
        self.assertEqual(len(transport.requests), 1)
        self.assertFalse(transport.search_truncated)

    def test_the_first_request_sends_no_cursor(self):
        transport = self._PagedTransport([{"results": [], "has_more": False}])
        transport.search_pages()

        self.assertNotIn("start_cursor", transport.requests[0][2])

    def test_it_follows_the_cursor_until_has_more_is_false(self):
        transport = self._PagedTransport(
            [
                {"results": [self._page(1)], "has_more": True, "next_cursor": "c1"},
                {"results": [self._page(2)], "has_more": True, "next_cursor": "c2"},
                {"results": [self._page(3)], "has_more": False},
            ]
        )

        pages = transport.search_pages()

        self.assertEqual([p["id"] for p in pages], ["page-1", "page-2", "page-3"])
        self.assertEqual(
            [r[2].get("start_cursor") for r in transport.requests], [None, "c1", "c2"]
        )
        self.assertFalse(transport.search_truncated)

    def test_the_page_limit_is_bounded_and_the_stop_is_reported(self):
        """A remote that always says `has_more` must not hang the command."""
        endless = [
            {"results": [self._page(i)], "has_more": True, "next_cursor": f"c{i}"}
            for i in range(50)
        ]
        transport = self._PagedTransport(endless)

        pages = transport.search_pages()

        self.assertEqual(len(transport.requests), RealNotionTransport._SEARCH_PAGE_LIMIT)
        self.assertEqual(len(pages), RealNotionTransport._SEARCH_PAGE_LIMIT)
        self.assertTrue(transport.search_truncated)

    def test_has_more_without_a_cursor_stops_rather_than_loops(self):
        """A response this cannot page through. Looping on it would re-send
        the same request forever."""
        transport = self._PagedTransport(
            [{"results": [self._page(1)], "has_more": True, "next_cursor": None}]
        )

        pages = transport.search_pages()

        self.assertEqual(len(pages), 1)
        self.assertEqual(len(transport.requests), 1)
        self.assertTrue(transport.search_truncated)

    def test_the_flag_exists_before_any_search(self):
        """Readable without first having called `search_pages()`."""
        transport = RealNotionTransport(api_token="ntn_" + "t" * 20)

        self.assertFalse(transport.search_truncated)

    def test_the_diagnosis_sees_pages_beyond_the_first_hundred(self):
        """The end-to-end shape: a Page on the second /search page used to be
        invisible, and the diagnosis said NEEDS_SHARED_PAGE."""
        first = [
            {
                "id": f"noise-{i}",
                "parent": {"type": "database_id"},
                "properties": {},
            }
            for i in range(100)
        ]
        second = [
            {
                "id": "company-ops-page",
                "parent": {"type": "workspace"},
                "properties": {
                    "title": {"type": "title", "title": [{"plain_text": "Company Ops"}]}
                },
            }
        ]
        transport = self._PagedTransport(
            [
                {"object": "database", "id": "db", "parent": {"type": "workspace"}},
                {"results": first, "has_more": True, "next_cursor": "c1"},
                {"results": second, "has_more": False},
            ]
        )
        client = NotionClient(transport=transport, database_id="db")

        diagnosis = diagnose_dashboard_bootstrap(client)

        self.assertEqual(diagnosis.readiness, BootstrapReadiness.NEEDS_PARENT_CHOICE)
        self.assertEqual(
            [p.page_id for p in diagnosis.hostable_pages], ["company-ops-page"]
        )


class UnreadableAndQueuedReachTheDashboardTests(unittest.TestCase):
    """C33 §1: the two Notion facts the Dashboard structurally could not show.

    C32 §6 recorded them as the next step and named why they were hard:
    neither is derivable from `notion_sync_results`.

        an unparseable Event file   never becomes a SyncResult at all —
                                    `app/runner.py` refuses to fabricate one
                                    because the `event_id` is precisely what
                                    could not be read
        the retry queue's depth     is a property of the queue after the run,
                                    not of the run's results

    So a run in which ten collected Event files became unreadable produced
    `Notion Synced 0 / Skipped 0 / Retried 0` — byte-identical to a run with
    nothing to sync — and a queue that had been stuck at 800 entries for a
    month produced the same row as an empty one.

    The Run Manifest is not a substitute, three ways over, and the third is
    the one that matters: it shows `queued=` only when the component is
    non-SUCCESS, it is the last run's number only, and an entry whose
    `to_event()` fails is counted as `notion_unreadable` and left in the
    queue — so it appears in no `queued` count anywhere.
    """

    def _record(self, **overrides):
        transport = InMemoryNotionTransport()
        client = NotionClient(transport=transport, database_id="ops-runs-db")
        kwargs = dict(
            run_id="run-1",
            run_at=datetime(2026, 8, 17, 9, 0),
            intake_summary=_FakeIntake(),
            collector_summary=_FakeCollector(),
            scheduler_result=_FakeScheduler(),
            backup_entry=_FakeBackup(),
            notion_sync_results=(),
        )
        kwargs.update(overrides)
        result = record_run(client, **kwargs)
        return transport._pages[result.page_id]["properties"]

    def test_unreadable_event_files_are_no_longer_invisible(self):
        properties = self._record(notion_unreadable=10)

        self.assertEqual(properties["Notion Unreadable"]["number"], 10)
        self.assertEqual(properties["Overall"]["select"]["name"], "WARN")

    def test_a_standing_queue_backlog_is_no_longer_invisible(self):
        properties = self._record(notion_queued=800)

        self.assertEqual(properties["Notion Queued"]["number"], 800)
        self.assertEqual(properties["Overall"]["select"]["name"], "WARN")

    def test_a_clean_run_reports_zero_for_both_and_stays_ok(self):
        """The other direction. A column that is non-zero on a healthy run is
        a column an operator learns to ignore."""
        properties = self._record()

        self.assertEqual(properties["Notion Unreadable"]["number"], 0)
        self.assertEqual(properties["Notion Queued"]["number"], 0)
        self.assertEqual(properties["Overall"]["select"]["name"], "OK")

    def test_a_queue_that_drained_this_run_is_ok(self):
        """`Notion Retried` counts this run's failures and `Notion Queued`
        counts what is left. A run that retried three queued Events and got
        all three through has retried=0 (they succeeded) and queued=0."""
        properties = self._record(
            notion_sync_results=[
                _sync(SyncStatus.NOTION_UPDATED, f"E{i}") for i in range(3)
            ],
            notion_queued=0,
        )

        self.assertEqual(properties["Notion Synced"]["number"], 3)
        self.assertEqual(properties["Overall"]["select"]["name"], "OK")

    def test_the_defaults_are_the_did_not_run_value_not_a_mask(self):
        """`record_run()` defaults both to 0 so existing callers keep
        working. That is safe only because 0 means "this step did not run",
        unlike the `getattr(..., 0)` defaults C32 §2 removed, which masked a
        renamed field on a step that HAD run."""
        properties = self._record()

        self.assertEqual(properties["Notion Unreadable"]["number"], 0)
        self.assertEqual(properties["Notion Queued"]["number"], 0)

    def test_the_runner_passes_both_rather_than_defaulting(self):
        """The half a default cannot enforce. A caller that has the numbers
        and lets them default reports a healthier run than happened."""
        source = (
            Path(__file__).resolve().parents[1] / "src" / "app" / "runner.py"
        ).read_text(encoding="utf-8")

        self.assertIn("notion_unreadable=len(notion_unreadable)", source)
        self.assertIn("notion_queued=notion_queue_depth", source)

    def test_both_are_bound_even_when_notion_is_unconfigured(self):
        """`run_once()`'s contract allows a Dashboard client without a Notion
        Sync client. Both names live inside `if notion_sync is not None`
        historically, so the Dashboard step would have raised NameError on
        that supported configuration."""
        import ast

        source = (
            Path(__file__).resolve().parents[1] / "src" / "app" / "runner.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        run_once = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "run_once"
        )

        # Both must be assigned at the function's own statement level, not
        # only inside a conditional branch.
        top_level_targets = set()
        for stmt in ast.walk(run_once):
            if isinstance(stmt, ast.Try):
                body = stmt.body
            else:
                continue
            for inner in body:
                if isinstance(inner, (ast.Assign, ast.AnnAssign)):
                    target = (
                        inner.targets[0] if isinstance(inner, ast.Assign) else inner.target
                    )
                    if isinstance(target, ast.Name):
                        top_level_targets.add(target.id)

        self.assertIn("notion_unreadable", top_level_targets)
        self.assertIn("notion_queue_depth", top_level_targets)


class DashboardRowArithmeticClosesTests(unittest.TestCase):
    """Every Event the Sync step touched is on the row exactly once.

    The row now carries five Notion counts, and an operator reading it has
    to be able to add them up. `synced + skipped + retried` partitions
    `notion_sync_results`; `unreadable` counts what never became a result at
    all; `queued` is the standing backlog and is deliberately NOT part of
    that sum — it spans runs.

    Written as a property test over every status combination rather than a
    few examples, because the counts are computed by subtraction
    (`retried = len - synced - skipped`) and subtraction is where an
    off-by-one hides.
    """

    STATUSES = (
        SyncStatus.NOTION_CREATED,
        SyncStatus.NOTION_UPDATED,
        SyncStatus.NOTION_SKIPPED_OLD_EVENT,
        SyncStatus.NOTION_RETRY_REQUIRED,
        SyncStatus.NOTION_FAILED,
    )

    def test_the_three_per_run_counts_always_sum_to_the_events_handled(self):
        import itertools

        transport = InMemoryNotionTransport()
        client = NotionClient(transport=transport, database_id="ops-runs-db")

        for size in (0, 1, 2, 3):
            for combo in itertools.combinations_with_replacement(self.STATUSES, size):
                with self.subTest(statuses=[s.value for s in combo]):
                    result = record_run(
                        client,
                        run_id=f"run-{size}-{'-'.join(s.name for s in combo)}",
                        run_at=datetime(2026, 8, 17, 9, 0),
                        intake_summary=_FakeIntake(),
                        collector_summary=_FakeCollector(),
                        scheduler_result=_FakeScheduler(),
                        backup_entry=_FakeBackup(),
                        notion_sync_results=[
                            _sync(s, f"E{i}") for i, s in enumerate(combo)
                        ],
                    )
                    properties = transport._pages[result.page_id]["properties"]
                    total = sum(
                        properties[key]["number"]
                        for key in ("Notion Synced", "Notion Skipped", "Notion Retried")
                    )
                    self.assertEqual(total, len(combo))

    def test_queued_is_not_part_of_that_sum(self):
        """It spans runs. Folding it in would make a row whose Events all
        succeeded look like it had more Events than it handled."""
        transport = InMemoryNotionTransport()
        client = NotionClient(transport=transport, database_id="ops-runs-db")
        result = record_run(
            client,
            run_id="run-spanning",
            run_at=datetime(2026, 8, 17, 9, 0),
            intake_summary=_FakeIntake(),
            collector_summary=_FakeCollector(),
            scheduler_result=_FakeScheduler(),
            backup_entry=_FakeBackup(),
            notion_sync_results=[_sync(SyncStatus.NOTION_CREATED, "E1")],
            notion_queued=42,
        )

        properties = transport._pages[result.page_id]["properties"]
        per_run = sum(
            properties[key]["number"]
            for key in ("Notion Synced", "Notion Skipped", "Notion Retried")
        )
        self.assertEqual(per_run, 1)
        self.assertEqual(properties["Notion Queued"]["number"], 42)


if __name__ == "__main__":
    unittest.main()
