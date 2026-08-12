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
from notion.dashboard_pending import (  # noqa: E402
    DashboardPendingError,
    drain_pending,
    load_pending,
    remove_pending,
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


class PartialBootstrapTests(unittest.TestCase):
    """`bootstrap_dashboard_databases()` creates five databases in a loop.

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
            bootstrap_dashboard_databases(self._client(transport))

        self.assertEqual(
            sorted(caught.exception.created), [OPS_BACKUP, OPS_RUNS]
        )
        for database_id in caught.exception.created.values():
            self.assertTrue(database_id)

    def test_the_failure_names_which_database_did_not_get_created(self):
        transport = self._FailAfter(2, parent={"type": "page_id", "page_id": "p"})

        with self.assertRaises(DashboardBootstrapPartialError) as caught:
            bootstrap_dashboard_databases(self._client(transport))

        self.assertEqual(caught.exception.failed_database, OPS_NOTION_SYNC)

    def test_the_message_tells_the_operator_how_to_retry(self):
        transport = self._FailAfter(1, parent={"type": "page_id", "page_id": "p"})

        with self.assertRaises(DashboardBootstrapPartialError) as caught:
            bootstrap_dashboard_databases(self._client(transport))

        message = str(caught.exception)
        self.assertIn("only=", message)
        self.assertIn(OPS_RUNS, message)

    def test_the_original_cause_is_preserved(self):
        original = NotionAPIError("Notion API returned 401: Unauthorized", status_code=401)
        transport = self._FailAfter(
            0, exc=original, parent={"type": "page_id", "page_id": "p"}
        )

        with self.assertRaises(DashboardBootstrapPartialError) as caught:
            bootstrap_dashboard_databases(self._client(transport))

        self.assertIs(caught.exception.cause, original)
        self.assertIs(caught.exception.__cause__, original)

    def test_failing_on_the_very_first_database_reports_an_empty_map(self):
        transport = self._FailAfter(0, parent={"type": "page_id", "page_id": "p"})

        with self.assertRaises(DashboardBootstrapPartialError) as caught:
            bootstrap_dashboard_databases(self._client(transport))

        self.assertEqual(caught.exception.created, {})
        self.assertIn("none", str(caught.exception))

    def test_a_response_without_an_id_fails_loudly(self):
        """Recording None would make `database_id()` later report "not
        created" for a database that exists — the same duplicate trap by a
        quieter route."""
        transport = self._NoId(parent={"type": "page_id", "page_id": "p"})

        with self.assertRaises(DashboardBootstrapPartialError) as caught:
            bootstrap_dashboard_databases(self._client(transport))

        self.assertEqual(caught.exception.failed_database, OPS_RUNS)
        self.assertIn("no id", str(caught.exception))

    def test_retrying_with_only_the_remaining_names_completes_the_set(self):
        """The recovery the error message describes, carried out."""
        transport = self._FailAfter(2, parent={"type": "page_id", "page_id": "p"})
        client = self._client(transport)

        with self.assertRaises(DashboardBootstrapPartialError) as caught:
            bootstrap_dashboard_databases(client)
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
            bootstrap_dashboard_databases(self._client(transport))

        self.assertEqual(transport.created_databases, {})

    def test_a_successful_bootstrap_is_unchanged(self):
        transport = InMemoryNotionTransport(parent={"type": "page_id", "page_id": "p"})

        result = bootstrap_dashboard_databases(self._client(transport))

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


if __name__ == "__main__":
    unittest.main()
