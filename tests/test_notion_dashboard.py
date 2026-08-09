"""Notion Operations Dashboard tests (CEO Decision 4).

Mock transport only — no real Notion workspace is contacted anywhere here.
"""

import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from notion import (  # noqa: E402
    DASHBOARD_DATABASES,
    OPS_BACKUP,
    OPS_NOTION_SYNC,
    OPS_READINESS,
    OPS_RISK,
    OPS_RUNS,
    BootstrapReadiness,
    DashboardOutcome,
    DashboardParentError,
    InMemoryNotionTransport,
    NotionClient,
    SyncResult,
    SyncStatus,
    bootstrap_dashboard_databases,
    build_ops_run_properties,
    diagnose_dashboard_bootstrap,
    record_run,
    resolve_parent_page_id,
)
from notion.dashboard_pending import (  # noqa: E402
    DashboardPendingError,
    drain_pending,
    load_pending,
    save_pending,
)


class _FakeIntake:
    def __init__(self, moved=()):
        self.moved = moved


class _FakeCollector:
    def __init__(self, accepted=0, duplicate=0, rejected=0, failed=0):
        self.accepted = accepted
        self.duplicate = duplicate
        self.rejected = rejected
        self.failed = failed


class _FakeStatus:
    def __init__(self, value):
        self.value = value


class _FakeScheduler:
    def __init__(self, status="COMPLETED", generated_dates=()):
        self.status = _FakeStatus(status)
        self.generated_dates = generated_dates


class _FakeBackup:
    def __init__(self, status="BACKUP_SUCCESS"):
        self.final_status = _FakeStatus(status)


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


class DashboardBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.transport = InMemoryNotionTransport()
        self.client = NotionClient(transport=self.transport, database_id="unused")

    def test_bootstrap_creates_all_five_databases(self):
        result = bootstrap_dashboard_databases(self.client, parent_page_id="page-1")

        self.assertEqual(len(result.created), 5)
        self.assertEqual(len(self.transport.created_databases), 5)
        for name in DASHBOARD_DATABASES:
            self.assertIsNotNone(result.database_id(name))

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
            accepted=2, duplicate=0, rejected=0, failed=0,
            scheduler_status="COMPLETED", generated_days=1,
            backup_status="BACKUP_SUCCESS", notion_synced=2, notion_retried=0,
        )
        self.assertEqual(props["Overall"]["select"]["name"], "OK")

    def test_overall_is_fail_when_scheduler_failed(self):
        props = build_ops_run_properties(
            run_id="r1", run_at=datetime(2026, 8, 1, 12, 0), transport_moved=0,
            accepted=0, duplicate=0, rejected=0, failed=0,
            scheduler_status="FAILED", generated_days=0,
            backup_status="BACKUP_SUCCESS", notion_synced=0, notion_retried=0,
        )
        self.assertEqual(props["Overall"]["select"]["name"], "FAIL")

    def test_overall_is_fail_when_backup_failed(self):
        props = build_ops_run_properties(
            run_id="r1", run_at=datetime(2026, 8, 1, 12, 0), transport_moved=0,
            accepted=0, duplicate=0, rejected=0, failed=0,
            scheduler_status="COMPLETED", generated_days=0,
            backup_status="BACKUP_FAILED", notion_synced=0, notion_retried=0,
        )
        self.assertEqual(props["Overall"]["select"]["name"], "FAIL")

    def test_overall_is_warn_when_backup_pending(self):
        props = build_ops_run_properties(
            run_id="r1", run_at=datetime(2026, 8, 1, 12, 0), transport_moved=0,
            accepted=0, duplicate=0, rejected=0, failed=0,
            scheduler_status="COMPLETED", generated_days=0,
            backup_status="BACKUP_PENDING", notion_synced=0, notion_retried=0,
        )
        self.assertEqual(props["Overall"]["select"]["name"], "WARN")

    def test_property_names_match_the_ops_runs_schema(self):
        props = build_ops_run_properties(
            run_id="r1", run_at=datetime(2026, 8, 1, 12, 0), transport_moved=0,
            accepted=0, duplicate=0, rejected=0, failed=0,
            scheduler_status="COMPLETED", generated_days=0,
            backup_status="BACKUP_SUCCESS", notion_synced=0, notion_retried=0,
        )
        self.assertEqual(set(props), set(DASHBOARD_DATABASES[OPS_RUNS]))


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
    """CHARACTERIZATION: pins today's behaviour, not the spec's claim.

    dashboard_pending.py's own docstring states "one Runner execution can
    never produce two OPS_RUNS rows, whether it is recorded on the first
    attempt or the tenth." Neither record_run() nor drain_pending() actually
    enforces that — both call client.create_project() unconditionally, with
    no find-before-create step (unlike notion.sync.ExecutionPlanSync, which
    calls find_project() first for exactly this reason). A false-negative
    network failure (the write reaches Notion, but the caller sees an
    exception) followed by a successful retry therefore creates a second
    page for the same run_id. If this test starts failing, a
    find-before-create guard was added and dashboard_pending.py's docstring
    claim became true — this test should then be rewritten as the
    guarantee."""

    def test_a_false_negative_failure_then_a_successful_retry_creates_two_rows(self):
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

            self.assertEqual((recorded, still_pending), (1, 0))
            # Two separate Notion pages now exist for the same run_id.
            self.assertEqual(len(transport._pages), 2)


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


if __name__ == "__main__":
    unittest.main()
