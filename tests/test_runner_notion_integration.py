"""Runner <-> Notion Sync Integration Test (COMPANY_OPS Sprint — Notion
Runtime Integration Phase 2, 구현 범위 4).

Uses `InMemoryNotionTransport` (Mock) exclusively — no real Notion API call
is made anywhere in this file. Verifies:

    - Collector ACCEPTED Event -> ExecutionPlanSync.sync() called
    - REJECTED Event -> not synced
    - Duplicate delivery (same event_id twice) -> only synced once
    - Notion Sync failure does not stop History / Daily / Backup / Transport
"""

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from app.runner import PIPELINE_COMPONENTS, run_once  # noqa: E402
from backup.result import BackupStatus  # noqa: E402
from backup.state import (  # noqa: E402
    load_state as load_backup_state,
    save_state as save_backup_state,
)
from runsummary import (  # noqa: E402
    ComponentStatus,
    read_summary,
    OverallStatus,
    Retryability,
    Severity,
)
from notion import (  # noqa: E402
    ExecutionPlanSync,
    NotionAPIError,
    InMemoryNotionTransport,
    NotionClient,
    SyncStatus,
)
from notion.dashboard_pending import load_pending  # noqa: E402
from reporter import DesktopProfile, Reporter  # noqa: E402
from scheduler import SchedulerStatus  # noqa: E402


class RunnerNotionTestCase(unittest.TestCase):
    """Scaffolding only: a real Runner wired to in-memory Notion transports.

    Split out from the test class so a second suite can reuse the fixture
    without inheriting — and therefore re-running — every test in the first
    one.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

        self.local_master_dir = self.root / "local_master"
        self.local_master_dir.mkdir(parents=True, exist_ok=True)
        self.backup_working_copy_dir = self.root / "backup_working_copy"
        self.backup_working_copy_dir.mkdir(parents=True, exist_ok=True)
        self._init_backup_git_repo(self.backup_working_copy_dir)
        self.runner_lock_path = self.root / "runtime" / "locks" / "company_ops.lock"
        self.transport_dir = self.root / "runtime" / "transport"
        self.incoming_dir = self.root / "runtime" / "events" / "incoming"
        self.processed_dir = self.root / "runtime" / "events" / "processed"
        self.rejected_dir = self.root / "runtime" / "events" / "rejected"
        self.collector_log_path = self.root / "runtime" / "logs" / "collector.log"
        self.collector_state_path = self.root / "runtime" / "state" / "collector_state.json"
        self.keep_dir = self.root / "runtime" / "history_candidates" / "keep"
        self.review_dir = self.root / "runtime" / "history_candidates" / "review"
        self.scheduler_state_path = self.root / "runtime" / "state" / "daily_history_state.json"
        self.backup_state_path = self.root / "runtime" / "state" / "backup_state.json"
        self.notion_sync_log_path = self.root / "runtime" / "logs" / "notion_sync.log"
        self.notion_retry_queue_path = self.root / "runtime" / "state" / "notion_retry_queue.json"
        self.dashboard_pending_path = self.root / "runtime" / "state" / "dashboard_pending.json"
        self.dashboard_transport = InMemoryNotionTransport()
        self.dashboard_client = NotionClient(
            transport=self.dashboard_transport, database_id="ops-runs-db"
        )

        self.transport = InMemoryNotionTransport()
        client = NotionClient(transport=self.transport, database_id="DB-1")
        self.notion_sync = ExecutionPlanSync(client=client)

        self.reporter = Reporter(profile="DESKTOP_3")

    def _run_git(self, args, cwd):
        # `encoding="utf-8"` for the same reason `backup/git_ops._run_git`
        # sets it: `text=True` alone decodes with the Windows locale
        # codepage, and the decode runs in subprocess's reader thread, so a
        # failure yields a silent `stdout=None` rather than an error.
        # Company History contains em dashes, so `git show` of a Daily file
        # hits it immediately.
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def _init_backup_git_repo(self, working_copy_dir: Path) -> None:
        """Backup(src/backup) requires `working_copy_dir` to already be a git
        repo with a configured, pushable `origin` (git_status/git_add_all/
        git_commit/git_push in src/backup/git_ops.py). This Sprint does not
        touch Backup — it only needs to actually finish so History/Daily/
        Backup can be shown to keep running after a Notion failure (구현
        범위 3). A local bare repo stands in for a real remote; no network.
        """
        bare_remote_dir = self.root / "backup_remote.git"
        self._run_git(["init", "--bare", "-b", "main", str(bare_remote_dir)], cwd=self.root)
        self._run_git(["init", "-b", "main"], cwd=working_copy_dir)
        self._run_git(["config", "user.email", "test@example.invalid"], cwd=working_copy_dir)
        self._run_git(["config", "user.name", "Runner Integration Test"], cwd=working_copy_dir)
        self._run_git(["remote", "add", "origin", str(bare_remote_dir)], cwd=working_copy_dir)
        (working_copy_dir / ".gitkeep").write_text("", encoding="utf-8")
        self._run_git(["add", "-A"], cwd=working_copy_dir)
        self._run_git(["commit", "-m", "init"], cwd=working_copy_dir)
        self._run_git(["push", "-u", "origin", "main"], cwd=working_copy_dir)

    def _write_event(self, **overrides):
        self.incoming_dir.mkdir(parents=True, exist_ok=True)
        data = dict(
            project_id="SEARCH_FRONTEND",
            event_type="STARTED",
            status="IN_PROGRESS",
            summary="test event",
            evidence=[],
            history_candidate=True,
            timestamp="2026-08-01T10:00:00+09:00",
        )
        data.update(overrides)
        _, path = self.reporter.report_and_write(directory=self.incoming_dir, **data)
        return path

    def _run(self, *, notion_sync=None, now=None, dashboard_client=None):
        return run_once(
            dashboard_client=dashboard_client,
            dashboard_pending_path=self.dashboard_pending_path,
            local_master_dir=self.local_master_dir,
            backup_working_copy_dir=self.backup_working_copy_dir,
            history_start_date=date(2026, 8, 1),
            runner_lock_path=self.runner_lock_path,
            now=now or datetime(2026, 8, 1, 12, 0),
            transport_dir=self.transport_dir,
            incoming_dir=self.incoming_dir,
            processed_dir=self.processed_dir,
            rejected_dir=self.rejected_dir,
            collector_log_path=self.collector_log_path,
            collector_state_path=self.collector_state_path,
            notion_sync=notion_sync,
            notion_sync_log_path=self.notion_sync_log_path,
            late_update_log_path=self.notion_sync_log_path.parent / "daily_late_update.log",
            monthly_state_path=self.notion_sync_log_path.parent / "monthly_history_state.json",
            run_summary_path=self.notion_sync_log_path.parent / "last_run.json",
            notion_retry_queue_path=self.notion_retry_queue_path,
            keep_dir=self.keep_dir,
            review_dir=self.review_dir,
            scheduler_state_path=self.scheduler_state_path,
            backup_state_path=self.backup_state_path,
        )


class RunnerNotionIntegrationTests(RunnerNotionTestCase):
    def test_accepted_event_is_synced_to_notion(self):
        self._write_event(event_id="RUNNER-INT-001")

        result = self._run(notion_sync=self.notion_sync)

        self.assertIsNotNone(result)
        _, collector_summary, _, _, notion_sync_results = result
        self.assertEqual(collector_summary.accepted, 1)
        self.assertEqual(len(notion_sync_results), 1)
        self.assertEqual(notion_sync_results[0].status, SyncStatus.NOTION_CREATED)
        self.assertEqual(notion_sync_results[0].event_id, "RUNNER-INT-001")

    def test_rejected_event_is_not_synced(self):
        self.incoming_dir.mkdir(parents=True, exist_ok=True)
        (self.incoming_dir / "BAD-001.json").write_text("{not valid json", encoding="utf-8")

        result = self._run(notion_sync=self.notion_sync)

        _, collector_summary, _, _, notion_sync_results = result
        self.assertEqual(collector_summary.rejected, 1)
        self.assertEqual(notion_sync_results, ())

    def test_duplicate_event_is_synced_only_once(self):
        # Same event_id delivered twice under two different filenames in the
        # same incoming/ batch (e.g. a Transport-level retry) — Collector's
        # seen_store marks the second one DUPLICATE within this single run,
        # so it must never reach Notion Sync.
        original_path = self._write_event(event_id="RUNNER-INT-DUP-001")
        retry_path = self.incoming_dir / "RUNNER-INT-DUP-001-retry.json"
        retry_path.write_text(original_path.read_text(encoding="utf-8"), encoding="utf-8")

        result = self._run(notion_sync=self.notion_sync)

        _, collector_summary, _, _, notion_sync_results = result
        self.assertEqual(collector_summary.accepted, 1)
        self.assertEqual(collector_summary.duplicate, 1)
        self.assertEqual(len(notion_sync_results), 1)
        self.assertEqual(notion_sync_results[0].status, SyncStatus.NOTION_CREATED)

    def test_notion_failure_does_not_stop_history_daily_backup(self):
        self.transport.fail_next_call = True
        # event_type must be one HistoryFilter automatically KEEPs (docs/05
        # §25 — src/history/filter.py _KEEP_EVENT_TYPES) so a Daily History
        # file actually gets generated; STARTED is always DROP (§26) and
        # would make this assertion meaningless regardless of Notion.
        self._write_event(
            event_id="RUNNER-INT-FAIL-001",
            event_type="MILESTONE_COMPLETED",
            milestone="Search UI",
            history_candidate=True,
        )

        # Scheduler never closes "today" (src/scheduler/scheduler.py: "today
        # is never processed") — run a day later so 2026-08-01 gets closed.
        result = self._run(notion_sync=self.notion_sync, now=datetime(2026, 8, 2, 9, 0))

        self.assertIsNotNone(result)
        intake_summary, collector_summary, scheduler_result, backup_entry, notion_sync_results = result

        self.assertEqual(collector_summary.accepted, 1)
        self.assertEqual(len(notion_sync_results), 1)
        self.assertEqual(notion_sync_results[0].status, SyncStatus.NOTION_RETRY_REQUIRED)

        # Daily History still ran and produced the (now-closed) day's file.
        self.assertIsNotNone(scheduler_result)
        daily_path = self.local_master_dir / "daily" / "2026-08-01.md"
        self.assertTrue(daily_path.exists())

        # Backup still ran.
        self.assertIsNotNone(backup_entry)

    def test_failed_event_is_queued_then_retried_first_on_next_run(self):
        # CEO Policy Decision — Notion Retry Architecture Plan A: a failed
        # Notion Sync is queued, and the *next* Runner execution retries the
        # queue before touching anything newly collected that run.
        self.transport.fail_next_call = True
        self._write_event(
            event_id="RUNNER-INT-RETRY-001",
            event_type="MILESTONE_COMPLETED",
            milestone="Search UI",
            history_candidate=True,
        )

        first = self._run(notion_sync=self.notion_sync, now=datetime(2026, 8, 2, 9, 0))
        _, _, _, _, first_results = first
        self.assertEqual(first_results[0].status, SyncStatus.NOTION_RETRY_REQUIRED)

        queued_text = self.notion_retry_queue_path.read_text(encoding="utf-8")
        self.assertIn("RUNNER-INT-RETRY-001", queued_text)

        # Second run: nothing new arrives in incoming/, but the queued
        # event must still be retried and, on success, removed from the
        # queue (not duplicated, not left behind).
        second = self._run(notion_sync=self.notion_sync, now=datetime(2026, 8, 3, 9, 0))
        _, collector_summary, _, _, second_results = second

        self.assertEqual(collector_summary.accepted, 0)  # nothing new collected
        self.assertEqual(len(second_results), 1)
        self.assertEqual(second_results[0].event_id, "RUNNER-INT-RETRY-001")
        self.assertEqual(second_results[0].status, SyncStatus.NOTION_CREATED)

        remaining_queue = json.loads(self.notion_retry_queue_path.read_text(encoding="utf-8"))
        self.assertEqual(remaining_queue["entries"], [])

    def test_dashboard_records_one_row_per_run(self):
        # CEO Decision 4: Runner records the Operations Dashboard once, at
        # the very end of the execution.
        self._write_event(event_id="RUNNER-DASH-001")

        self._run(notion_sync=self.notion_sync, dashboard_client=self.dashboard_client)

        rows = list(self.dashboard_transport._pages.values())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["properties"]["Accepted"]["number"], 1)

    def test_dashboard_failure_does_not_stop_the_runtime(self):
        # The whole point of CEO Decision 4's constraint: History, Daily and
        # Backup must all still complete when the Dashboard write fails.
        self.dashboard_transport.fail_next_call = True
        self._write_event(
            event_id="RUNNER-DASH-FAIL-001",
            event_type="MILESTONE_COMPLETED",
            milestone="Search UI",
        )

        result = self._run(
            notion_sync=self.notion_sync,
            now=datetime(2026, 8, 2, 9, 0),
            dashboard_client=self.dashboard_client,
        )

        self.assertIsNotNone(result)
        _, collector_summary, scheduler_result, backup_entry, _ = result
        self.assertEqual(collector_summary.accepted, 1)
        self.assertTrue((self.local_master_dir / "daily" / "2026-08-01.md").exists())
        self.assertIsNotNone(backup_entry)
        self.assertEqual(scheduler_result.generated_dates, (date(2026, 8, 1),))

    def test_failed_dashboard_record_is_queued_and_retried_next_run(self):
        self.dashboard_transport.fail_next_call = True
        self._write_event(event_id="RUNNER-DASH-RETRY-001")

        self._run(
            notion_sync=self.notion_sync,
            now=datetime(2026, 8, 2, 9, 0),
            dashboard_client=self.dashboard_client,
        )
        pending = load_pending(self.dashboard_pending_path)
        self.assertEqual(len(pending), 1)

        # Second run: the queued record is retried first and drains.
        self._run(
            notion_sync=self.notion_sync,
            now=datetime(2026, 8, 3, 9, 0),
            dashboard_client=self.dashboard_client,
        )

        self.assertEqual(load_pending(self.dashboard_pending_path), [])

    def test_dashboard_is_skipped_when_not_configured(self):
        self._write_event(event_id="RUNNER-DASH-NONE-001")

        self._run(notion_sync=self.notion_sync, dashboard_client=None)

        self.assertEqual(self.dashboard_transport._pages, {})
        self.assertEqual(load_pending(self.dashboard_pending_path), [])

    def test_notion_sync_writes_log_entry(self):
        # docs/04_NOTION_SYNC_SPEC.md §55: event_id / project_id / sync
        # timestamp / result 최소 기록.
        self._write_event(event_id="RUNNER-INT-LOG-001")

        self._run(notion_sync=self.notion_sync)

        log_text = self.notion_sync_log_path.read_text(encoding="utf-8")
        self.assertIn("RUNNER-INT-LOG-001", log_text)
        self.assertIn("SEARCH_FRONTEND", log_text)
        self.assertIn("NOTION_CREATED", log_text)

    def test_notion_sync_skipped_when_not_configured(self):
        self._write_event(event_id="RUNNER-INT-NOSYNC-001")

        result = self._run(notion_sync=None)

        _, collector_summary, _, _, notion_sync_results = result
        self.assertEqual(collector_summary.accepted, 1)
        self.assertEqual(notion_sync_results, ())

    def test_runner_lock_path_matching_scheduler_default_name_no_longer_self_deadlocks(self):
        """Runtime Stabilization Sprint (Critical Fix), regression test.

        Reproduces the exact production incident: `self.runner_lock_path`
        (set in setUp) uses the identical relative path
        (`runtime/locks/company_ops.lock`) as the real
        `scheduler.lock.DEFAULT_LOCK_PATH` — the value a caller gets if
        they don't know to pick a different name for Scheduler's lock, as
        actually happened during the real Notion Workspace E2E run.
        Before the fix, Scheduler tried to re-acquire that same
        already-held lock and returned SKIPPED_ALREADY_RUNNING, so Daily
        History silently never ran. After the fix (already_locked=True,
        passed from app/runner.py), Scheduler doesn't attempt any lock of
        its own here and must complete normally.
        """
        from scheduler.lock import DEFAULT_LOCK_PATH as SCHEDULER_DEFAULT_LOCK_PATH

        self.assertEqual(
            self.runner_lock_path.parts[-3:], SCHEDULER_DEFAULT_LOCK_PATH.parts[-3:]
        )  # sanity check: this test is actually exercising the collision path

        self._write_event(
            event_id="RUNNER-LOCK-REGRESSION-001",
            event_type="MILESTONE_COMPLETED",
            milestone="Lock collision regression",
        )

        result = self._run(notion_sync=self.notion_sync, now=datetime(2026, 8, 2, 9, 0))

        _, _, scheduler_result, _, _ = result
        self.assertEqual(scheduler_result.status, SchedulerStatus.COMPLETED)
        self.assertEqual(scheduler_result.generated_dates, (date(2026, 8, 1),))


class TheQueueIsWrittenOncePerRunTests(RunnerNotionTestCase):
    """The Batch Save guarantee (CEO 승인 B안), asserted by RUNNING the
    Runner instead of by reading its source.

    `test_architecture_invariants.py::
    test_runner_batches_queue_writes_into_a_single_save` pins the property as
    source text: the load call is there, the in-memory helpers are there,
    `save_retry_queue(` appears exactly once, the per-file helpers do not.
    Every one of those is a claim about how step 4 is *written*.

    What the property is actually about is how many times the file is
    rewritten while Notion is down — the O(n^2) byte cost the batch replaced
    (measured then: 7.9 ms/enqueue at 50 entries, 19.3 ms at 800). A source
    assertion cannot see a second save added inside a helper, a loop that
    calls the surviving save per Event, or a refactor that renames it. This
    counts the writes a real run performs.

    Both are kept. The source one still catches "someone used the per-file
    helpers", which is invisible from the outside when n is 1.
    """

    class _AlwaysDown(InMemoryNotionTransport):
        """Notion refusing every write, which is what fills the queue."""

        def create_page(self, database_id, properties):
            raise NotionAPIError("service unavailable", status_code=503)

        def update_page(self, page_id, properties):
            raise NotionAPIError("service unavailable", status_code=503)

    def _run_counting_saves(self, event_count):
        import app.runner as runner_module
        from notion import NotionClient
        from notion.sync import ExecutionPlanSync

        for index in range(event_count):
            self._write_event(event_id=f"QUEUE-{index}", project_id=f"P{index}")

        sync = ExecutionPlanSync(
            client=NotionClient(transport=self._AlwaysDown(), database_id="DB-1")
        )

        original = runner_module.save_retry_queue
        calls = []

        def counting(path, entries):
            calls.append(len(entries))
            return original(path, entries)

        runner_module.save_retry_queue = counting
        try:
            result = self._run(notion_sync=sync)
        finally:
            runner_module.save_retry_queue = original
        return result, calls

    def test_one_write_however_many_events_are_queued(self):
        _result, calls = self._run_counting_saves(8)

        self.assertEqual(len(calls), 1, f"the queue file was rewritten {len(calls)} times")
        self.assertEqual(calls[0], 8)

    def test_the_queue_on_disk_still_holds_every_event(self):
        """Writing once must not mean writing less — the whole delta has to
        survive the run, or a Notion outage would lose the retries it was
        collecting."""
        _result, _calls = self._run_counting_saves(8)

        entries = json.loads(self.notion_retry_queue_path.read_text(encoding="utf-8"))
        queued = entries.get("entries", entries) if isinstance(entries, dict) else entries

        self.assertEqual(len(queued), 8)

    def test_a_run_with_nothing_to_queue_does_not_write_at_all(self):
        """The other side: `queue_dirty` gates the save, so a healthy run
        must not touch the file. A save-per-run would rewrite it on every
        scheduled execution forever."""
        import app.runner as runner_module

        self._write_event(event_id="HEALTHY-1")

        original = runner_module.save_retry_queue
        calls = []

        def counting(path, entries):
            calls.append(len(entries))
            return original(path, entries)

        runner_module.save_retry_queue = counting
        try:
            self._run(notion_sync=self.notion_sync)
        finally:
            runner_module.save_retry_queue = original

        self.assertEqual(calls, [])

    def test_the_second_run_drains_and_writes_once_more(self):
        """Cross-run: the drain path mutates the same in-memory list and must
        also settle on one write."""
        import app.runner as runner_module

        self._run_counting_saves(3)

        original = runner_module.save_retry_queue
        calls = []

        def counting(path, entries):
            calls.append(len(entries))
            return original(path, entries)

        runner_module.save_retry_queue = counting
        try:
            self._run(notion_sync=self.notion_sync,
                      now=datetime(2026, 8, 2, 12, 0))
        finally:
            runner_module.save_retry_queue = original

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], 0, "the drained queue should end empty")


class EveryDashboardNumberMatchesDiskTests(RunnerNotionTestCase):
    """The OPS_RUNS row against the filesystem, on one rich run.

    Every other Dashboard test asserts one column from one hand-built result
    object. That is the right way to test a mapping and it cannot answer the
    question docs/14 §1 actually poses — Notion is a **View, never a Source**,
    so the row is only worth reading if it agrees with the Source. The
    agreement had been checked by hand (C42, C43) and by nothing that runs.

    One run, deliberately messy, so that no column is zero by accident:

        3 Events that reach the Collector, one of whose project Notion
        refuses forever (503) -> it queues
        1 duplicate arriving through `transport/`
        1 unparseable file aged past the stability window -> intake invalid
        1 `.tmp-` staging residue                          -> intake incomplete
        1 unreadable file already in `incoming/`            -> collector reject

    Each assertion below reads the row on one side and the FILESYSTEM (or the
    result object the filesystem produced) on the other — never the same
    computation twice.
    """

    def setUp(self):
        super().setUp()
        import os
        import time

        transport = self._RefusingTransport()
        self.notion_sync = ExecutionPlanSync(
            client=NotionClient(transport=transport, database_id="DB-1")
        )

        for index, project in enumerate(("P1", "P2", "BADPROJ")):
            self._write_event(
                event_id=f"EV-{index}", project_id=project,
                timestamp="2026-08-01T10:00:00+09:00",
            )

        self.transport_dir.mkdir(parents=True, exist_ok=True)
        duplicate = json.loads((self.incoming_dir / "EV-0.json").read_text(encoding="utf-8"))
        duplicate["event_id"] = "EV-DUP"
        (self.transport_dir / "EV-DUP.json").write_text(json.dumps(duplicate), encoding="utf-8")

        garbage = self.transport_dir / "garbage.json"
        garbage.write_text("{nope", encoding="utf-8")
        aged = time.time() - 3600
        os.utime(garbage, (aged, aged))
        (self.transport_dir / ".tmp-half.json").write_text("{}", encoding="utf-8")
        (self.incoming_dir / "broken.json").write_text("{not json", encoding="utf-8")

        self.result = self._run(
            notion_sync=self.notion_sync,
            dashboard_client=self.dashboard_client,
            now=datetime(2026, 8, 3, 12, 0),
        )
        self.intake, self.collector, self.scheduler, self.backup, self.syncs = self.result
        page = list(self.dashboard_transport._pages.values())[-1]
        self.row = page["properties"]

    class _RefusingTransport(InMemoryNotionTransport):
        """Notion permanently refusing one project, so `Notion Retried` and
        `Notion Queued` are non-zero for a reason the disk can confirm."""

        def create_page(self, database_id, properties):
            if "BADPROJ" in json.dumps(properties):
                raise NotionAPIError("unavailable", status_code=503)
            return super().create_page(database_id, properties)

    def _number(self, name):
        return self.row[name]["number"]

    def _select(self, name):
        return self.row[name]["select"]["name"]

    # ---- transport -------------------------------------------------------

    def test_transport_moved_matches_what_left_the_transport_directory(self):
        moved = sorted(p.name for p in self.processed_dir.glob("*.json"))
        still_there = sorted(p.name for p in self.transport_dir.glob("*"))

        self.assertEqual(self._number("Transport Moved"), len(self.intake.moved))
        # Nothing this run promoted is still sitting in transport/.
        for name in self.intake.moved:
            self.assertNotIn(name, still_there)
        self.assertTrue(moved)

    def test_transport_blocked_matches_the_files_left_behind_for_good(self):
        """The three buckets `count_blocked_intake()` counts, read back off
        the disk rather than off the summary."""
        left = {p.name for p in self.transport_dir.glob("*")}

        self.assertIn("garbage.json", left)
        self.assertIn(".tmp-half.json", left)
        self.assertEqual(
            self._number("Transport Blocked"),
            len(self.intake.skipped_invalid)
            + len(self.intake.skipped_incomplete)
            + len(self.intake.failed),
        )
        self.assertGreaterEqual(self._number("Transport Blocked"), 2)

    # ---- collector -------------------------------------------------------

    def test_accepted_matches_the_processed_directory(self):
        self.assertEqual(
            self._number("Accepted"), len(list(self.processed_dir.glob("*.json")))
        )

    def test_rejected_matches_the_rejected_directory(self):
        self.assertEqual(self._number("Rejected"), len(list(self.rejected_dir.glob("*"))))
        self.assertGreaterEqual(self._number("Rejected"), 1)

    def test_duplicate_and_failed_match_the_collector_state(self):
        state = json.loads(self.collector_state_path.read_text(encoding="utf-8"))

        self.assertEqual(self._number("Duplicate"), self.collector.duplicate)
        self.assertEqual(self._number("Failed"), self.collector.failed)
        # Every accepted Event is remembered, which is what makes a re-run a
        # duplicate rather than a second Event.
        self.assertEqual(
            len(state["processed_event_ids"]), self._number("Accepted")
        )

    # ---- daily -----------------------------------------------------------

    def test_generated_days_matches_the_files_on_disk(self):
        written = sorted((self.local_master_dir / "daily").glob("*.md"))

        self.assertEqual(self._number("Generated Days"), len(written))
        self.assertEqual(self._select("Scheduler Status"), self.scheduler.status.value)

    def test_reused_days_is_zero_on_a_first_run_and_the_pair_covers_the_watermark(self):
        state = json.loads(self.scheduler_state_path.read_text(encoding="utf-8"))
        closed = self._number("Generated Days") + self._number("Reused Days")

        self.assertEqual(self._number("Reused Days"), 0)
        self.assertEqual(closed, len(self.scheduler.closed_dates))
        self.assertEqual(
            state["last_successful_daily_close"],
            max(self.scheduler.closed_dates).isoformat(),
        )

    # ---- backup ----------------------------------------------------------

    def test_backup_status_matches_the_backup_state_file(self):
        state = json.loads(self.backup_state_path.read_text(encoding="utf-8"))

        self.assertEqual(self._select("Backup Status"), state["backup_status"])

    def test_deleted_files_is_zero_and_the_remote_holds_the_history(self):
        pushed = self._run_git(
            ["ls-tree", "-r", "--name-only", "main"], cwd=self.backup_working_copy_dir
        ).split()
        written = sorted(p.name for p in (self.local_master_dir / "daily").glob("*.md"))

        self.assertEqual(self._number("Deleted Files"), 0)
        for name in written:
            self.assertIn(f"daily/{name}", pushed)

    # ---- notion ----------------------------------------------------------

    def test_the_three_sync_counts_partition_the_events_handled(self):
        total = (
            self._number("Notion Synced")
            + self._number("Notion Skipped")
            + self._number("Notion Retried")
        )

        self.assertEqual(total, len(self.syncs))

    def test_notion_queued_matches_the_queue_file(self):
        entries = json.loads(self.notion_retry_queue_path.read_text(encoding="utf-8"))
        queued = entries.get("entries", entries) if isinstance(entries, dict) else entries

        self.assertEqual(self._number("Notion Queued"), len(queued))
        self.assertGreaterEqual(self._number("Notion Queued"), 1)

    def test_notion_synced_matches_the_rows_notion_actually_holds(self):
        """The strongest of these: the column against the other side of the
        wire, not against this side's own count."""
        self.assertEqual(
            self._number("Notion Synced"), len(self.notion_sync._client._transport._pages)
        )

    # ---- the verdict -----------------------------------------------------

    def test_failed_steps_names_the_manifests_own_failed_components(self):
        summary = read_summary(self.notion_sync_log_path.parent / "last_run.json")
        failed = [
            component.name
            for component in summary.components
            if component.status is ComponentStatus.FAILED
        ]
        printed = "".join(
            part["text"]["content"] for part in self.row["Failed Steps"]["rich_text"]
        )

        self.assertEqual(sorted(printed.split(", ")) if printed else [], sorted(failed))

    def test_the_row_verdict_never_contradicts_the_manifest(self):
        """C37's one-directional relation, checked on a real run rather than
        on constructed inputs: Dashboard OK => manifest SUCCESS, and a
        DEGRADED manifest is never an OK row."""
        summary = read_summary(self.notion_sync_log_path.parent / "last_run.json")
        overall = self._select("Overall")

        self.assertEqual(overall, "WARN")
        self.assertIs(summary.overall_status, OverallStatus.DEGRADED)
        if overall == "OK":
            self.assertIs(summary.overall_status, OverallStatus.SUCCESS)

    def test_the_row_is_keyed_by_the_manifest_run_id(self):
        summary = read_summary(self.notion_sync_log_path.parent / "last_run.json")
        run_id = "".join(
            part["text"]["content"] for part in self.row["Run ID"]["title"]
        )

        self.assertEqual(run_id, summary.run_id)


class NothingIsLostBetweenEventAndMonthlyTests(RunnerNotionTestCase):
    """The whole delivery chain, counted at every stage in one run.

    Each stage of this pipeline has its own suite, and each one asserts its
    own contract. What none of them asks is the question an operator asks —
    **did the Event I sent end up in Company History, and in the Monthly?**
    That is a property of the seams, not of any stage, and every loss this
    repository has found lived in a seam.

    Nine Events across three days, three per day, one of each Event Type that
    routes differently:

        MILESTONE_COMPLETED  -> KEEP    -> a Daily item -> a Monthly item
        DECISION_APPROVED    -> KEEP    -> a Daily item -> a Monthly item
        BLOCKED              -> REVIEW  -> not rendered until a human acts

    Then August is closed out and consolidated, and the three sets are
    compared: stored KEEP ids, ids parseable out of the Daily files by
    `monthly/parser.py`, and ids the Monthly file carries.
    """

    EVENT_TYPES = ("MILESTONE_COMPLETED", "DECISION_APPROVED", "BLOCKED")
    DAYS = ("2026-08-01", "2026-08-02", "2026-08-03")

    def setUp(self):
        super().setUp()
        self.planned = []
        for day in self.DAYS:
            for index, event_type in enumerate(self.EVENT_TYPES):
                event_id = f"EV-{day[-2:]}-{index}"
                extra = {}
                if event_type == "BLOCKED":
                    extra["blocker"] = "waiting on review"
                if event_type == "MILESTONE_COMPLETED":
                    extra["milestone"] = f"M{index}"
                self._write_event(
                    event_id=event_id,
                    project_id=f"PRJ_{index}",
                    event_type=event_type,
                    status="IN_PROGRESS",
                    summary=f"work {event_id}",
                    history_candidate=True,
                    timestamp=f"{day}T1{index}:00:00+09:00",
                    **extra,
                )
                self.planned.append(event_id)

        self.result = self._run(
            notion_sync=self.notion_sync, now=datetime(2026, 8, 5, 12, 0)
        )
        # Close the rest of the month so it is consolidatable at all
        # (docs/09 §10/§39 refuse a month with a hole).
        self._run(notion_sync=self.notion_sync, now=datetime(2026, 9, 1, 12, 0))

        self.daily_dir = self.local_master_dir / "daily"
        self.monthly_dir = self.local_master_dir / "monthly"

    def _stored(self, directory):
        if not directory.is_dir():
            return set()
        return {
            json.loads(path.read_text(encoding="utf-8"))["event_id"]
            for path in directory.glob("*.json")
        }

    def _daily_ids(self):
        from monthly.parser import read_daily_document

        found, unconsolidated = set(), 0
        for path in sorted(self.daily_dir.glob("*.md")):
            document = read_daily_document(path, date.fromisoformat(path.stem))
            found |= {item.event_id for item in document.items}
            unconsolidated += document.unconsolidated
        return found, unconsolidated

    def _monthly_ids(self):
        text = (self.monthly_dir / "2026-08.md").read_text(encoding="utf-8")
        return {
            line.split(":", 1)[1].strip()
            for line in text.splitlines()
            if line.startswith("- Event ID:")
        }, text

    # ---- stage by stage --------------------------------------------------

    def test_every_event_reaches_the_collector(self):
        _intake, collector, _scheduler, _backup, _syncs = self.result

        self.assertEqual(collector.accepted, len(self.planned))
        self.assertEqual(
            len(list(self.processed_dir.glob("*.json"))), len(self.planned)
        )

    def test_the_filter_routes_each_type_where_docs_05_says(self):
        keep = self._stored(self.keep_dir)
        review = self._stored(self.review_dir)

        self.assertEqual(keep | review, set(self.planned))
        self.assertEqual(keep & review, set())
        # BLOCKED is index 2 of every day.
        self.assertEqual(review, {f"EV-{d[-2:]}-2" for d in self.DAYS})

    def test_every_kept_candidate_reaches_company_history(self):
        keep = self._stored(self.keep_dir)
        in_daily, unconsolidated = self._daily_ids()

        self.assertEqual(keep - in_daily, set(), "KEEP Candidates missing from Daily")
        self.assertEqual(unconsolidated, 0)

    def test_a_reviewed_candidate_is_not_in_history_yet(self):
        """The other direction — REVIEW must NOT appear, or the filter would
        be doing nothing."""
        review = self._stored(self.review_dir)
        in_daily, _ = self._daily_ids()

        self.assertEqual(review & in_daily, set())

    def test_every_daily_item_reaches_the_monthly(self):
        in_daily, _ = self._daily_ids()
        in_monthly, _text = self._monthly_ids()

        self.assertEqual(in_daily - in_monthly, set(), "items lost between Daily and Monthly")

    def test_the_monthly_invents_nothing(self):
        in_daily, _ = self._daily_ids()
        in_monthly, _text = self._monthly_ids()

        self.assertEqual(in_monthly - in_daily, set(), "Monthly carries an id no Daily has")

    def test_the_monthly_total_matches_what_it_carries(self):
        """`- Consolidated Items:` against the ids in the same file — the
        pair `ops_status._monthly_counts_more_than_it_shows()` compares."""
        in_monthly, text = self._monthly_ids()
        claimed = next(
            int(line.split(":", 1)[1])
            for line in text.splitlines()
            if line.startswith("- Consolidated Items:")
        )

        self.assertEqual(claimed, len(in_monthly))
        self.assertEqual(claimed, len(self._stored(self.keep_dir)))

    def test_the_two_standing_detectors_are_quiet_on_this_run(self):
        """The chain being intact and the detectors saying so are two facts,
        and a green pipeline with a noisy detector is still a bug."""
        import contextlib
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "ops_status.py"
        spec = importlib.util.spec_from_file_location("ops_status_chain", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        self.assertEqual(module._daily_counts_more_than_it_shows(self.daily_dir), ())
        self.assertEqual(module._monthly_counts_more_than_it_shows(self.monthly_dir), ())


class ANonStringFieldIsRejectedNotCrashedIntoTests(RunnerNotionTestCase):
    """The consequence of the schema gap, and its fix, through the pipeline.

    `docs/02` §4 declares `event_id` / `project_id` / `summary` as `string`
    and `validate_event()` did not enforce it, so the Collector ACCEPTED such
    an Event and the failure surfaced deep inside a CRITICAL step. Measured
    before the fix, one crafted Event arriving beside one ordinary one:

        summary=12345    daily FAILED, 0 Daily files, exit 2 — and the KEEP
                         Candidate is on disk, so EVERY later run fails the
                         same way until a human deletes it
        project_id=7     notion_sync and daily both FAILED, 0 Daily files
        event_id=99      TypeError escaped run_once() entirely

    docs/03 §7 already says where an invalid Event goes — `rejected/`, run
    continuing — and that is what these assert now. The innocent Event of the
    same run must survive, which is the property the old behaviour destroyed.
    """

    BASE = {
        "schema_version": "1.0",
        "timestamp": "2026-08-01T10:00:00+09:00",
        "source": "DESKTOP_1",
        "role": "CTO_BACKEND",
        "event_type": "MILESTONE_COMPLETED",
        "status": "IN_PROGRESS",
        "evidence": [],
        "history_candidate": True,
        "milestone": "M",
    }

    CRAFTED = {
        "summary=int": {"event_id": "EV-A", "project_id": "P", "summary": 12345},
        "summary=dict": {"event_id": "EV-B", "project_id": "P", "summary": {"t": "x"}},
        "project_id=int": {"event_id": "EV-C", "project_id": 7, "summary": "ok"},
        "event_id=int": {"event_id": 99, "project_id": "P", "summary": "ok"},
        "event_id=list": {"event_id": ["a"], "project_id": "P", "summary": "ok"},
    }

    def _run_with(self, override):
        import os
        import time

        self.transport_dir.mkdir(parents=True, exist_ok=True)
        data = dict(self.BASE)
        data.update(override)
        crafted = self.transport_dir / "crafted.json"
        crafted.write_text(json.dumps(data), encoding="utf-8")
        aged = time.time() - 3600
        os.utime(crafted, (aged, aged))

        self._write_event(
            event_id="EV-OK", project_id="PRJ_OK",
            timestamp="2026-08-01T11:00:00+09:00",
        )
        return self._run(
            notion_sync=self.notion_sync,
            dashboard_client=self.dashboard_client,
            now=datetime(2026, 8, 3, 12, 0),
        )

    def test_it_is_rejected_and_the_run_survives(self):
        for label, override in self.CRAFTED.items():
            with self.subTest(case=label):
                self.setUp()
                result = self._run_with(override)
                summary = read_summary(self.notion_sync_log_path.parent / "last_run.json")

                self.assertEqual(
                    sorted(p.name for p in self.rejected_dir.glob("*")), ["crafted.json"]
                )
                self.assertEqual(
                    sorted(p.name for p in self.processed_dir.glob("*")), ["EV-OK.json"]
                )
                self.assertIs(summary.overall_status, OverallStatus.SUCCESS)
                self.assertEqual(summary.exit_code, 0)

    def test_company_history_is_still_written(self):
        """The property the old behaviour destroyed: one malformed Event from
        one Desktop must not stop the day being closed."""
        for label, override in self.CRAFTED.items():
            with self.subTest(case=label):
                self.setUp()
                self._run_with(override)

                self.assertTrue(
                    sorted((self.local_master_dir / "daily").glob("*.md")),
                    "no Daily History was written",
                )

    def test_nothing_reaches_the_history_candidates(self):
        """The reason the old failure repeated forever: the Candidate was
        written to `keep/` before the renderer choked on it, so the next run
        found it again and died the same way.

        Asserted as "the crafted id is in no stored Candidate" rather than
        "keep/ holds EV-OK" — the ordinary Event here is a `STARTED`, which
        `history.filter` does not keep, and pinning that would be asserting
        the filter's mapping in the wrong file.
        """
        for label, override in self.CRAFTED.items():
            with self.subTest(case=label):
                self.setUp()
                self._run_with(override)
                stored = []
                for directory in (self.keep_dir, self.review_dir):
                    if directory.is_dir():
                        stored += [
                            json.loads(path.read_text(encoding="utf-8"))["event_id"]
                            for path in directory.glob("*.json")
                        ]

                self.assertNotIn(override["event_id"], stored)

    def test_the_rejection_is_counted_where_an_operator_reads_it(self):
        """`rejected` is a WARN input on the Dashboard, so the row says a
        person should look — a refused Event is not a silent one."""
        self._run_with(self.CRAFTED["summary=int"])
        row = list(self.dashboard_transport._pages.values())[-1]["properties"]

        self.assertEqual(row["Rejected"]["number"], 1)
        self.assertEqual(row["Overall"]["select"]["name"], "WARN")

    def test_the_collector_log_names_the_refused_file(self):
        self._run_with(self.CRAFTED["project_id=int"])
        log = self.collector_log_path.read_text(encoding="utf-8")

        self.assertIn("REJECTED", log)
        self.assertIn("project_id", log)


class DashboardRunIdTraceabilityTests(RunnerNotionTestCase):
    """The OPS_RUNS row and the Run Manifest must name the same run.

    An operator reading a FAIL row in the Operations Dashboard has exactly
    one way back to the evidence: look up that `Run ID` in
    `runtime/runs/last_run.json`. That only works if the two carry the same
    string.

    They did, but by coincidence rather than by construction — the manifest
    used `now_iso(now)` and the Dashboard used
    `now.isoformat(timespec="seconds")`, which are two spellings of one rule
    (`now_iso()` *is* that call). Either could have been changed alone. The
    Dashboard now reuses the manifest's own value, so the correlation cannot
    drift; these tests pin it.
    """

    def test_the_dashboard_row_is_keyed_by_the_manifest_run_id(self):
        self._write_event(event_id="RUNNER-DASH-TRACE-001")

        result = self._run(
            notion_sync=self.notion_sync, dashboard_client=self.dashboard_client
        )

        rows = list(self.dashboard_transport._pages.values())
        self.assertEqual(len(rows), 1)
        row_run_id = rows[0]["properties"]["Run ID"]["title"][0]["text"]["content"]
        self.assertEqual(row_run_id, result.summary.run_id)

    def test_a_queued_row_still_carries_the_run_id_of_the_run_that_made_it(self):
        """The retry path must not re-key the record to the *retrying* run —
        that would attach a failure to the wrong execution's evidence."""
        self.dashboard_transport.fail_next_call = True
        self._write_event(event_id="RUNNER-DASH-TRACE-002")

        first = self._run(
            notion_sync=self.notion_sync,
            now=datetime(2026, 8, 2, 9, 0),
            dashboard_client=self.dashboard_client,
        )
        pending = load_pending(self.dashboard_pending_path)
        self.assertEqual([r.run_id for r in pending], [first.summary.run_id])

        self._run(
            notion_sync=self.notion_sync,
            now=datetime(2026, 8, 3, 9, 0),
            dashboard_client=self.dashboard_client,
        )

        drained = [
            page["properties"]["Run ID"]["title"][0]["text"]["content"]
            for page in self.dashboard_transport._pages.values()
        ]
        self.assertIn(first.summary.run_id, drained)

    def test_two_runs_produce_two_distinct_run_ids(self):
        """The guard added for duplicate rows must not collapse real runs."""
        self._write_event(event_id="RUNNER-DASH-TRACE-003")
        first = self._run(
            notion_sync=self.notion_sync,
            now=datetime(2026, 8, 2, 9, 0),
            dashboard_client=self.dashboard_client,
        )
        self._write_event(event_id="RUNNER-DASH-TRACE-004")
        second = self._run(
            notion_sync=self.notion_sync,
            now=datetime(2026, 8, 3, 9, 0),
            dashboard_client=self.dashboard_client,
        )

        self.assertNotEqual(first.summary.run_id, second.summary.run_id)
        run_ids = {
            page["properties"]["Run ID"]["title"][0]["text"]["content"]
            for page in self.dashboard_transport._pages.values()
        }
        self.assertEqual(run_ids, {first.summary.run_id, second.summary.run_id})

    def test_rerunning_the_same_instant_does_not_add_a_second_row(self):
        """`run_id` is derived from `now`, so a Runner re-invoked for the
        same instant is the same run. find-before-create must recognise it —
        this is the end-to-end form of the idempotency the unit tests pin."""
        self._write_event(event_id="RUNNER-DASH-TRACE-005")
        self._run(
            notion_sync=self.notion_sync,
            now=datetime(2026, 8, 2, 9, 0),
            dashboard_client=self.dashboard_client,
        )
        self._write_event(event_id="RUNNER-DASH-TRACE-006")
        self._run(
            notion_sync=self.notion_sync,
            now=datetime(2026, 8, 2, 9, 0),
            dashboard_client=self.dashboard_client,
        )

        self.assertEqual(len(self.dashboard_transport._pages), 1)


class DegradedStepDoesNotAbortCriticalStepsTests(RunnerNotionTestCase):
    """docs/14 §5's whole purpose, checked at the seam where it broke.

    Severity exists to separate a step that records or protects Company
    History (CRITICAL) from one that projects or corrects it (DEGRADED). The
    property that makes the distinction worth anything is that a DEGRADED
    failure must not prevent a CRITICAL step from running.

    Measured before the fix, with an undecodable Daily file and a Late Event
    for that same date: `update_daily_history()` raised `UnicodeDecodeError`
    out of step 6.5, so **Monthly, Backup and Dashboard never started** — 6
    of 9 components recorded, no commit, no Dashboard row, and the manifest
    blamed `late_update` with `STEP_ABORTED`. A DEGRADED step had aborted a
    CRITICAL one.

    The unit-level guard lives in
    `test_runner_failure_paths.py::UndecodableFileIsolationTests`. This is
    the part that unit test cannot see: what the rest of the pipeline does
    afterwards.
    """

    UNDECODABLE_BYTES = b"\xff\xfe\x00 not utf-8 \xff"

    def _close_a_day_then_corrupt_it(self):
        self._write_event(
            event_id="ISO-001", event_type="MILESTONE_COMPLETED", milestone="M1"
        )
        self._run(notion_sync=self.notion_sync, now=datetime(2026, 8, 2, 9, 0))
        daily = self.local_master_dir / "daily" / "2026-08-01.md"
        self.assertTrue(daily.exists())
        daily.write_bytes(self.UNDECODABLE_BYTES)
        return daily

    def _run_with_a_late_event(self):
        self._write_event(
            event_id="ISO-LATE-001", event_type="MILESTONE_COMPLETED", milestone="M2"
        )
        return self._run(
            notion_sync=self.notion_sync,
            now=datetime(2026, 8, 3, 9, 0),
            dashboard_client=self.dashboard_client,
        )

    def test_a_late_update_failure_does_not_stop_the_run(self):
        self._close_a_day_then_corrupt_it()

        result = self._run_with_a_late_event()

        self.assertIsNotNone(result)
        names = [c.name for c in result.summary.components]
        self.assertEqual(len(names), 9)
        self.assertEqual(names, list(PIPELINE_COMPONENTS))

    def test_the_failure_is_classified_rather_than_reported_as_an_abort(self):
        self._close_a_day_then_corrupt_it()

        result = self._run_with_a_late_event()

        late = result.summary.component("late_update")
        self.assertEqual(late.status, ComponentStatus.FAILED)
        self.assertEqual(late.failure.classification, "LATE_EVENT_MERGE_FAILED")
        self.assertEqual(late.failure.severity, Severity.DEGRADED)
        # PERMANENT, not RETRYABLE: nothing re-queues a failed merge, so
        # only PERMANENT puts it in front of a person (docs/14 §5, and
        # `ops_status.py` lists PERMANENT failures only).
        self.assertEqual(late.failure.retryability, Retryability.PERMANENT)

    def test_backup_still_runs_and_commits(self):
        """The one that matters most: Company History still reaches the
        remote on a run whose Late Event merge failed."""
        self._close_a_day_then_corrupt_it()
        before = self._run_git(["log", "--oneline"], cwd=self.backup_working_copy_dir)

        result = self._run_with_a_late_event()

        _, _, _, backup_entry, _ = result
        self.assertEqual(backup_entry.final_status, BackupStatus.SUCCESS)
        after = self._run_git(["log", "--oneline"], cwd=self.backup_working_copy_dir)
        self.assertGreater(
            len(after.strip().splitlines()), len(before.strip().splitlines())
        )

    def test_monthly_and_dashboard_still_run(self):
        self._close_a_day_then_corrupt_it()

        result = self._run_with_a_late_event()

        self.assertEqual(
            result.summary.component("monthly").status, ComponentStatus.SUCCESS
        )
        self.assertEqual(
            result.summary.component("dashboard").status, ComponentStatus.SUCCESS
        )
        self.assertEqual(len(self.dashboard_transport._pages), 1)

    def test_the_dashboard_row_does_not_call_this_run_ok(self):
        """C37: the same run, described twice, disagreeing at the top of both.

        Measured before the fix, on exactly this scenario — a Late Event
        whose merge fails against an undecodable Daily file:

            manifest    DEGRADED / exit 3
            Dashboard   Overall OK

        `Overall` is the column an operator sorts a Notion view by, so it is
        the one place the disagreement is certain to be seen and certain to
        be believed. `late_update` and `monthly` are the two steps that can
        record FAILED without stopping the run, and neither had a column on
        the row, so neither could reach the verdict at all.
        """
        self._close_a_day_then_corrupt_it()

        result = self._run_with_a_late_event()

        row = list(self.dashboard_transport._pages.values())[0]["properties"]
        self.assertEqual(result.summary.overall_status, OverallStatus.DEGRADED)
        self.assertNotEqual(row["Overall"]["select"]["name"], "OK")
        self.assertEqual(row["Overall"]["select"]["name"], "WARN")
        # And it names the step, which is the question a WARN raises.
        self.assertEqual(
            row["Failed Steps"]["rich_text"][0]["text"]["content"], "late_update"
        )

    def test_the_run_is_degraded_not_failed(self):
        """A DEGRADED-severity failure must not produce exit 2 — that is
        reserved for a CRITICAL component, and crying wolf is the reason
        DEGRADED exists at all."""
        self._close_a_day_then_corrupt_it()

        result = self._run_with_a_late_event()

        self.assertEqual(result.summary.overall_status, OverallStatus.DEGRADED)
        self.assertEqual(result.summary.exit_code, 3)

    def test_the_failure_reaches_the_operator_s_attention_section(self):
        """The point of the PERMANENT classification, checked where it lands.

        `ops_status.py::_print_last_run()` lists PERMANENT failures only —
        deliberately, so a self-clearing RETRYABLE one does not become a
        standing alert. This failure does not clear itself, so it has to be
        in that list or nobody ever learns the Late Event is missing.
        """
        import contextlib
        import importlib.util
        import io as _io

        self._close_a_day_then_corrupt_it()
        result = self._run_with_a_late_event()

        spec = importlib.util.spec_from_file_location(
            "ops_status_under_test",
            Path(__file__).resolve().parents[1] / "ops_status.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUNTIME_DIR = self.root / "runtime"
        module.DEFAULT_RUN_SUMMARY_PATH = (
            self.notion_sync_log_path.parent / "last_run.json"
        )

        with contextlib.redirect_stdout(_io.StringIO()):
            attention = module._print_last_run()

        self.assertTrue(
            any("late_update" in item for item in attention),
            f"late_update missing from ATTENTION: {attention}",
        )
        self.assertEqual(result.summary.exit_code, 3)

    def test_the_damaged_history_file_is_left_exactly_as_it_was(self):
        """docs/06 §41: a History write failure leaves the existing History
        intact and deletes no Candidate."""
        daily = self._close_a_day_then_corrupt_it()

        self._run_with_a_late_event()

        self.assertEqual(daily.read_bytes(), self.UNDECODABLE_BYTES)
        keep = list(self.keep_dir.glob("*.json"))
        self.assertTrue(keep, "the Candidate must survive for the next run")

    def test_the_corrupt_file_is_mirrored_to_the_backup_not_rejected_by_it(self):
        """Worth knowing before relying on the backup to recover: the Backup
        step mirrors Local Master faithfully, so the corrupt file is
        committed too. The clean copy survives in the *previous* commit, not
        at HEAD — which is where a recovery has to look."""
        self._close_a_day_then_corrupt_it()

        self._run_with_a_late_event()

        head = self.backup_working_copy_dir / "daily" / "2026-08-01.md"
        self.assertEqual(head.read_bytes(), self.UNDECODABLE_BYTES)
        previous = self._run_git(
            ["show", "HEAD~1:daily/2026-08-01.md"], cwd=self.backup_working_copy_dir
        )
        self.assertIn("2026-08-01", previous)

    def test_repairing_the_file_alone_does_not_bring_the_late_event_back(self):
        """CHARACTERIZATION of BACKLOG E-17, and the reason the failure is
        classified PERMANENT.

        `kept_dates` holds only the dates whose Candidates this run wrote, so
        no later run revisits a date whose merge failed. Measured across two
        further runs after the file was repaired: `late_update` SUCCESS with
        `updated=0`, overall SUCCESS, exit 0 — and the Event still missing.

        The Candidate is not lost (it is still in keep/), and an unrelated
        new Event on the same date does pull it in — see the next test. What
        is missing is anything that makes that happen on its own.
        """
        daily = self._close_a_day_then_corrupt_it()
        self._run_with_a_late_event()

        restored = self._run_git(
            ["show", "HEAD~1:daily/2026-08-01.md"], cwd=self.backup_working_copy_dir
        )
        daily.write_text(restored, encoding="utf-8")

        for day in (4, 5):
            result = self._run(
                notion_sync=self.notion_sync,
                now=datetime(2026, 8, day, 9, 0),
                dashboard_client=self.dashboard_client,
            )
            self.assertEqual(
                result.summary.component("late_update").status, ComponentStatus.SUCCESS
            )

        self.assertNotIn("ISO-LATE-001", daily.read_text(encoding="utf-8"))
        # Not lost, just unreachable by any automatic path.
        self.assertTrue(list(self.keep_dir.glob("*.json")))

    def test_a_new_event_on_the_same_date_pulls_the_stranded_one_in(self):
        """The only path that currently recovers it, pinned so the scope of
        E-17 is exact rather than approximate."""
        daily = self._close_a_day_then_corrupt_it()
        self._run_with_a_late_event()
        restored = self._run_git(
            ["show", "HEAD~1:daily/2026-08-01.md"], cwd=self.backup_working_copy_dir
        )
        daily.write_text(restored, encoding="utf-8")

        self._write_event(
            event_id="ISO-THIRD-001", event_type="MILESTONE_COMPLETED", milestone="M3"
        )
        result = self._run(
            notion_sync=self.notion_sync,
            now=datetime(2026, 8, 4, 9, 0),
            dashboard_client=self.dashboard_client,
        )

        self.assertEqual(
            result.summary.component("late_update").status, ComponentStatus.SUCCESS
        )
        text = daily.read_text(encoding="utf-8")
        self.assertIn("ISO-LATE-001", text)
        self.assertIn("ISO-THIRD-001", text)


class WholePipelineIdempotencyTests(RunnerNotionTestCase):
    """Running the Runner again with nothing new must change nothing that
    matters.

    Every stage has its own dedup — Collector's seen store, intake's
    already-present check, `generate_daily_history()`'s refusal to
    overwrite, `select_late_candidates()`, `ExecutionPlanSync`'s
    find-before-create, `consolidate_month()`'s UNCHANGED, Backup's
    NOT_REQUIRED. Each is tested in isolation. Nothing asserted the property
    they exist to produce *together*, which is the one an operator relies on
    every time a run is retried by hand or a scheduled trigger double-fires.

    Measured across the whole runtime tree: a second identical run rewrites
    exactly three files, all of them legitimately — the append-only
    `collector.log`, the new Run Manifest, and `backup_state.json` moving
    from BACKUP_SUCCESS to BACKUP_NOT_REQUIRED. Company History, the git
    history, the Notion projection and the Dashboard are untouched.
    """

    def _snapshot(self):
        import hashlib

        return {
            str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(self.root.rglob("*"))
            if p.is_file() and ".git" not in p.parts
        }

    def _commits(self):
        return len(
            self._run_git(["log", "--oneline"], cwd=self.backup_working_copy_dir)
            .strip()
            .splitlines()
        )

    def _daily_entries(self):
        daily = self.local_master_dir / "daily" / "2026-08-01.md"
        return daily.read_text(encoding="utf-8").count("- Event ID:")

    def _first_run(self, now=datetime(2026, 8, 2, 9, 0)):
        self._write_event(
            event_id="IDEM-1", event_type="MILESTONE_COMPLETED", milestone="M1"
        )
        return self._run(
            notion_sync=self.notion_sync, now=now, dashboard_client=self.dashboard_client
        )

    def test_a_second_identical_run_writes_no_new_history(self):
        self._first_run()
        entries, commits = self._daily_entries(), self._commits()
        notion, dashboard = len(self.transport._pages), len(
            self.dashboard_transport._pages
        )

        second = self._run(
            notion_sync=self.notion_sync,
            now=datetime(2026, 8, 2, 9, 0),
            dashboard_client=self.dashboard_client,
        )

        self.assertEqual(second[1].accepted, 0)
        self.assertEqual(len(second[2].generated_dates), 0)
        self.assertEqual(second[3].final_status, BackupStatus.NOT_REQUIRED)
        self.assertEqual(self._daily_entries(), entries)
        self.assertEqual(self._commits(), commits)
        self.assertEqual(len(self.transport._pages), notion)
        self.assertEqual(len(self.dashboard_transport._pages), dashboard)

    def test_only_the_log_the_manifest_and_the_backup_status_change(self):
        """The whole-tree form. A file appearing here that is not one of the
        three is a stage that did work it should have recognised as done."""
        self._first_run()
        before = self._snapshot()

        self._run(
            notion_sync=self.notion_sync,
            now=datetime(2026, 8, 2, 9, 0),
            dashboard_client=self.dashboard_client,
        )
        after = self._snapshot()

        self.assertEqual(sorted(set(after) - set(before)), [])
        self.assertEqual(sorted(set(before) - set(after)), [])
        changed = {k for k in before if before[k] != after[k]}
        self.assertEqual(
            {Path(k).name for k in changed},
            {"collector.log", "last_run.json", "backup_state.json"},
            f"unexpected rewrite: {sorted(changed)}",
        )

    def test_a_later_run_on_a_new_day_still_adds_nothing_to_history(self):
        """The realistic shape: the scheduled task fires again tomorrow and
        no Desktop reported anything. A new run_id means a new Dashboard row
        — that is one row per execution, by design — but Company History,
        the git history and the Notion projection must not move."""
        self._first_run()
        entries, commits = self._daily_entries(), self._commits()
        notion = len(self.transport._pages)

        third = self._run(
            notion_sync=self.notion_sync,
            now=datetime(2026, 8, 3, 9, 0),
            dashboard_client=self.dashboard_client,
        )

        self.assertEqual(third[1].accepted, 0)
        self.assertEqual(self._daily_entries(), entries)
        self.assertEqual(len(self.transport._pages), notion)
        # 2026-08-02 closes as an empty day, so one commit is correct — what
        # must not happen is the 08-01 content being written again.
        self.assertLessEqual(self._commits() - commits, 1)
        self.assertEqual(len(self.dashboard_transport._pages), 2)

    def test_five_consecutive_reruns_never_accumulate(self):
        """Non-idempotency usually shows as slow growth rather than an
        immediate double, so once is not a strong enough check."""
        self._first_run()
        entries, commits = self._daily_entries(), self._commits()

        for _ in range(5):
            self._run(
                notion_sync=self.notion_sync,
                now=datetime(2026, 8, 2, 9, 0),
                dashboard_client=self.dashboard_client,
            )

        self.assertEqual(self._daily_entries(), entries)
        self.assertEqual(self._commits(), commits)
        self.assertEqual(len(self.transport._pages), 1)
        self.assertEqual(len(self.dashboard_transport._pages), 1)
        self.assertEqual(load_pending(self.dashboard_pending_path), [])

    def test_the_manifest_of_a_no_op_run_is_still_a_clean_success(self):
        """A run that correctly did nothing must not look like a failure —
        otherwise every quiet day pages someone."""
        self._first_run()

        second = self._run(
            notion_sync=self.notion_sync,
            now=datetime(2026, 8, 2, 9, 0),
            dashboard_client=self.dashboard_client,
        )

        self.assertEqual(second.summary.overall_status, OverallStatus.SUCCESS)
        self.assertEqual(second.summary.exit_code, 0)
        self.assertEqual(len(second.summary.components), 9)
        self.assertEqual(second.summary.failures(), ())


class FailedBackupLeavesNoTraceTests(RunnerNotionTestCase):
    """CHARACTERIZATION — BUG-41 + E-14 measured together.

    Each half is recorded on its own and each looks survivable:

        BUG-41   a later run overwrites `backup_state.backup_status`, so a
                 BACKUP_FAILED is replaced rather than preserved
        E-14     docs/08 §68-69's Backup Log is never written, so there is
                 no per-run history of backup outcomes

    Measured together, a Backup failure has **no durable record anywhere**
    after a single subsequent run:

        backup_state.json   BACKUP_SUCCESS
        last_run.json       backup SUCCESS, overall SUCCESS, exit 0
        runtime/logs/       collector.log, notion_sync.log — no backup log
        logs/backup/        does not exist

    Worse than BUG-41's own description, which blames the *no-change* path
    (FAILED -> NOT_REQUIRED). The run below has a change and therefore takes
    the *success* path, writing BACKUP_SUCCESS outright — so both routes
    erase it, and the manifest that recorded the failure is itself
    overwritten by the next run's manifest.

    Exit 0 is what Task Scheduler records as Last Run Result, so the only
    automatic signal an unattended deployment has says the system is fine.

    Why this is pinned rather than fixed: preserving a FAILED status across
    runs changes what `backup_status` means (docs/08 §19/§21; the analogous
    change for PENDING was made under an explicit CEO approval), and writing
    the Backup Log creates a new persistent artifact path that docs/14 §2's
    Artifact Taxonomy fixes. Both are decisions.

    What this test adds is the number that was missing from both entries:
    the trace count is zero, not "reduced". It fails the day either half is
    closed, and at that point the surviving record should be asserted here.
    """

    def _seed_failed_backup(self):
        state = load_backup_state(self.backup_state_path)
        state.backup_status = BackupStatus.FAILED
        save_backup_state(self.backup_state_path, state)

    def _summary_path(self):
        return self.notion_sync_log_path.parent / "last_run.json"

    def test_a_failed_backup_status_is_gone_after_one_more_run(self):
        self._write_event(
            event_id="TRACE-1", event_type="MILESTONE_COMPLETED", milestone="M"
        )
        self._run(notion_sync=self.notion_sync, now=datetime(2026, 8, 2, 9, 0))
        self._seed_failed_backup()
        self.assertEqual(
            load_backup_state(self.backup_state_path).backup_status,
            BackupStatus.FAILED,
        )

        self._run(notion_sync=self.notion_sync, now=datetime(2026, 8, 3, 9, 0))

        self.assertNotEqual(
            load_backup_state(self.backup_state_path).backup_status,
            BackupStatus.FAILED,
        )

    def test_the_manifest_does_not_remember_it_either(self):
        """The manifest is per-run and the next run replaces it, so it
        cannot serve as the durable record."""
        self._write_event(
            event_id="TRACE-2", event_type="MILESTONE_COMPLETED", milestone="M"
        )
        self._run(notion_sync=self.notion_sync, now=datetime(2026, 8, 2, 9, 0))
        self._seed_failed_backup()

        result = self._run(notion_sync=self.notion_sync, now=datetime(2026, 8, 3, 9, 0))

        backup = result.summary.component("backup")
        self.assertEqual(backup.status, ComponentStatus.SUCCESS)
        self.assertEqual(result.summary.overall_status, OverallStatus.SUCCESS)
        self.assertEqual(result.summary.exit_code, 0)

    def test_no_backup_log_exists_to_hold_the_history(self):
        """E-14's half, stated as the reason the other two cannot be
        recovered from."""
        self._write_event(
            event_id="TRACE-3", event_type="MILESTONE_COMPLETED", milestone="M"
        )
        self._run(notion_sync=self.notion_sync, now=datetime(2026, 8, 2, 9, 0))

        logs = self.root / "runtime" / "logs"
        written = sorted(p.name for p in logs.glob("*")) if logs.is_dir() else []

        self.assertNotIn("backup", written)
        self.assertFalse((logs / "backup").exists())

    def test_the_trace_count_across_every_durable_location_is_zero(self):
        """The compound, as one assertion. If any half is closed this fails,
        and the surviving record belongs here instead."""
        self._write_event(
            event_id="TRACE-4", event_type="MILESTONE_COMPLETED", milestone="M"
        )
        self._run(notion_sync=self.notion_sync, now=datetime(2026, 8, 2, 9, 0))
        self._seed_failed_backup()
        self._run(notion_sync=self.notion_sync, now=datetime(2026, 8, 3, 9, 0))

        traces = []
        if load_backup_state(self.backup_state_path).backup_status is BackupStatus.FAILED:
            traces.append("backup_state.json")
        manifest = read_summary(self._summary_path())
        if manifest.component("backup").status is ComponentStatus.FAILED:
            traces.append("last_run.json")
        if (self.root / "runtime" / "logs" / "backup").exists():
            traces.append("logs/backup/")

        self.assertEqual(traces, [])


class DashboardCarriesUnreadableAndQueuedTests(RunnerNotionTestCase):
    """C33 §1 end to end, through a real `run_once()`.

    The unit tests in `test_notion_dashboard.py` prove the two columns carry
    what they are handed. These prove the Runner hands over the right
    numbers — which is the half that was missing, since both values live in
    local variables of step 4 and the Dashboard step is five steps later.
    """

    def _row(self):
        pages = list(self.dashboard_transport._pages.values())
        self.assertEqual(len(pages), 1, pages)
        return pages[0]["properties"]

    def test_a_queued_event_shows_as_queued_on_the_dashboard(self):
        """Notion refuses the sync, the Event lands in the retry queue, and
        the row says so. Before this the row was indistinguishable from a
        run with nothing to sync."""
        self._write_event(event_id="EVT-QUEUE")
        self.transport.fail_next_call = True

        self._run(
            notion_sync=self.notion_sync, dashboard_client=self.dashboard_client
        )

        row = self._row()
        self.assertEqual(row["Notion Retried"]["number"], 1)
        self.assertEqual(row["Notion Queued"]["number"], 1)
        self.assertEqual(row["Overall"]["select"]["name"], "WARN")

    def test_the_queue_depth_is_what_the_queue_file_actually_holds(self):
        """Read from the in-memory list the step just saved. Pinned against
        the file so the two cannot drift."""
        self._write_event(event_id="EVT-QUEUE-2")
        self.transport.fail_next_call = True

        self._run(
            notion_sync=self.notion_sync, dashboard_client=self.dashboard_client
        )

        on_disk = json.loads(self.notion_retry_queue_path.read_text(encoding="utf-8"))
        self.assertEqual(
            self._row()["Notion Queued"]["number"], len(on_disk["entries"])
        )

    def test_an_unparseable_queued_entry_is_counted_and_stays_queued(self):
        """The case no `queued=` metric can see: `to_event()` raises, so the
        entry never becomes a SyncResult, and `app/runner.py` leaves it in
        the queue. It must appear in `Notion Unreadable`, and the queue it
        is still sitting in must appear in `Notion Queued`."""
        self.notion_retry_queue_path.parent.mkdir(parents=True, exist_ok=True)
        self.notion_retry_queue_path.write_text(
            json.dumps(
                {
                    "entries": [
                        {
                            "event_id": "EVT-BROKEN",
                            "project_id": "PRJ",
                            "event_data": {"event_id": "EVT-BROKEN"},
                            "added_at": "2026-08-01T10:00:00+09:00",
                            "attempt_count": 3,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        self._run(
            notion_sync=self.notion_sync, dashboard_client=self.dashboard_client
        )

        row = self._row()
        self.assertEqual(row["Notion Unreadable"]["number"], 1)
        self.assertEqual(row["Notion Queued"]["number"], 1)
        self.assertEqual(row["Overall"]["select"]["name"], "WARN")

    def test_a_clean_run_reports_zero_for_both(self):
        self._write_event(event_id="EVT-CLEAN")

        self._run(
            notion_sync=self.notion_sync, dashboard_client=self.dashboard_client
        )

        row = self._row()
        self.assertEqual(row["Notion Unreadable"]["number"], 0)
        self.assertEqual(row["Notion Queued"]["number"], 0)

    def test_the_dashboard_records_when_notion_sync_is_unconfigured(self):
        """`run_once()`'s contract allows a Dashboard client with **no**
        Notion Sync client, and this pins that the new pass-through did not
        quietly break it.

        Honest about what this is: not a bug that was found, but a trap that
        was avoided. Both counters live in step 4, inside
        `if notion_sync is not None`. Passing them to step 9b without also
        binding them outside that branch would raise NameError on exactly
        this supported configuration — absorbed by the step's own `except`,
        logged as `DASHBOARD FAILED`, and the row lost for good, every run,
        for every deployment that has a Dashboard and no Sync. The test
        exists because that failure would have been invisible in every other
        test in this file, all of which pass a `notion_sync`.
        """
        self._write_event(event_id="EVT-NO-SYNC")

        self._run(notion_sync=None, dashboard_client=self.dashboard_client)

        row = self._row()
        self.assertEqual(row["Notion Unreadable"]["number"], 0)
        self.assertEqual(row["Notion Queued"]["number"], 0)
        self.assertEqual(row["Overall"]["select"]["name"], "OK")
        log = self.notion_sync_log_path
        if log.exists():
            self.assertNotIn("DASHBOARD FAILED", log.read_text(encoding="utf-8"))


class PermanentlyRefusedSyncReachesAttentionTests(RunnerNotionTestCase):
    """C33 §5: the transport classified, and nothing read the classification.

    `NotionAPIError.status_code` was set on every HTTP failure, asserted by
    four tests, and read by **zero** production code. It is precisely the
    signal BUG-13 is about — the one separating "Notion was briefly down"
    from "Notion will refuse this forever" — and BUG-13's fix was to append
    the reason *string* to the log so a person could tell them apart by
    reading prose.

    The cost was structural, not cosmetic. `ops_status.py` lists only
    PERMANENT failures in ATTENTION (deliberately: a RETRYABLE one is what
    the next run is for, and a self-clearing alert trains people to skim).
    Every Notion failure was RETRYABLE. So a request Notion will refuse
    forever could not reach ATTENTION through the manifest at all — it
    surfaced only once C32 §14's NOTION block noticed the queue entry had
    aged past three days.

    Nothing about the queue changes: docs/04 §38 forbids dropping the Event,
    and it stays queued exactly as before. Nothing about the exit code
    changes either: `runsummary.overall_status()` folds **severity**, and
    Notion Sync's severity is untouched.
    """

    class _StatusTransport(InMemoryNotionTransport):
        """Answers the first query with a chosen HTTP status."""

        def __init__(self, status_code, **kwargs):
            super().__init__(**kwargs)
            self.status_code = status_code
            self.first = True

        def query_database(self, database_id, filter_):
            if self.first:
                self.first = False
                raise NotionAPIError(
                    f"Notion API returned {self.status_code}: refused",
                    status_code=self.status_code,
                )
            return super().query_database(database_id, filter_)

    def _run_with_status(self, status_code):
        transport = self._StatusTransport(status_code)
        sync = ExecutionPlanSync(
            client=NotionClient(transport=transport, database_id="DB-1")
        )
        self._write_event(event_id=f"EVT-{status_code}")

        self._run(notion_sync=sync)

        summary = read_summary(self.notion_sync_log_path.parent / "last_run.json")
        return summary.component("notion_sync")

    def test_a_permanently_refusing_status_is_classified_permanent(self):
        for status_code in (400, 401, 403, 404):
            with self.subTest(status_code=status_code):
                self.setUp()
                component = self._run_with_status(status_code)
                self.assertEqual(component.status, ComponentStatus.FAILED)
                self.assertIs(component.failure.retryability, Retryability.PERMANENT)

    def test_a_transient_status_stays_retryable(self):
        """The other direction, and the one that keeps ATTENTION usable: a
        503 is exactly what the retry queue exists for."""
        for status_code in (429, 500, 502, 503):
            with self.subTest(status_code=status_code):
                self.setUp()
                component = self._run_with_status(status_code)
                self.assertIs(component.failure.retryability, Retryability.RETRYABLE)

    def test_the_severity_and_exit_code_are_unchanged(self):
        """This reclassification must not promote Notion onto the critical
        path — README RULE 5 and docs/14 §5 both put it off it."""
        component = self._run_with_status(401)
        summary = read_summary(self.notion_sync_log_path.parent / "last_run.json")

        self.assertIs(component.failure.severity, Severity.DEGRADED)
        self.assertIs(summary.overall_status, OverallStatus.DEGRADED)
        self.assertEqual(summary.exit_code, 3)

    def test_the_event_is_still_queued_not_dropped(self):
        """docs/04 §38: a Notion failure must never delete the Event. The
        classification says "a person must act", not "give up"."""
        self._run_with_status(400)

        queued = json.loads(self.notion_retry_queue_path.read_text(encoding="utf-8"))
        self.assertEqual(len(queued["entries"]), 1)

    def test_the_reason_prefers_the_unrecoverable_failure(self):
        """With a transient and a permanent failure in one batch, `reason` is
        the only sentence that reaches the operator — it must name the one
        they can act on."""
        statuses = iter([503, 400])

        class _Mixed(InMemoryNotionTransport):
            def query_database(self, database_id, filter_):
                try:
                    code = next(statuses)
                except StopIteration:
                    return super().query_database(database_id, filter_)
                raise NotionAPIError(
                    f"Notion API returned {code}: refused", status_code=code
                )

        sync = ExecutionPlanSync(
            client=NotionClient(transport=_Mixed(), database_id="DB-1")
        )
        self._write_event(event_id="EVT-A", project_id="PRJ_A")
        self._write_event(event_id="EVT-B", project_id="PRJ_B")

        self._run(notion_sync=sync)

        component = read_summary(self.notion_sync_log_path.parent / "last_run.json").component("notion_sync")
        self.assertIs(component.failure.retryability, Retryability.PERMANENT)
        self.assertIn("400", component.failure.reason)
        self.assertEqual(component.metrics["refused"], 1)
        self.assertEqual(component.metrics["queued"], 2)

    def test_an_unreadable_entry_still_wins_as_unknown(self):
        """UNKNOWN outranks PERMANENT. Claiming a request is permanently
        refused when this step could not even read the Event is the same
        overreach BUG-13 warns about, pointed the other way."""
        self.notion_retry_queue_path.parent.mkdir(parents=True, exist_ok=True)
        self.notion_retry_queue_path.write_text(
            json.dumps(
                {
                    "entries": [
                        {
                            "event_id": "EVT-BROKEN",
                            "project_id": "PRJ",
                            "event_data": {"event_id": "EVT-BROKEN"},
                            "added_at": "2026-08-01T10:00:00+09:00",
                            "attempt_count": 1,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        component = self._run_with_status(400)

        self.assertIs(component.failure.retryability, Retryability.UNKNOWN)

    def test_a_network_failure_has_no_status_and_stays_retryable(self):
        """No status at all is information: the request never got an answer,
        which is the retryable case."""

        class _Down(InMemoryNotionTransport):
            def query_database(self, database_id, filter_):
                raise NotionAPIError("Notion API request failed: [Errno 111]")

        sync = ExecutionPlanSync(
            client=NotionClient(transport=_Down(), database_id="DB-1")
        )
        self._write_event(event_id="EVT-DOWN")

        self._run(notion_sync=sync)

        component = read_summary(self.notion_sync_log_path.parent / "last_run.json").component("notion_sync")
        self.assertIs(component.failure.retryability, Retryability.RETRYABLE)

    def test_the_status_code_survives_onto_the_sync_result(self):
        """The field the transport was already setting, now actually read."""
        transport = self._StatusTransport(403)
        sync = ExecutionPlanSync(
            client=NotionClient(transport=transport, database_id="DB-1")
        )
        self._write_event(event_id="EVT-403")

        result = self._run(notion_sync=sync)
        _intake, _collector, _scheduler, _backup, sync_results = result

        self.assertEqual([r.status_code for r in sync_results], [403])
        self.assertTrue(sync_results[0].is_permanently_refused)

    def test_the_permanent_set_is_short_and_deliberate(self):
        """A blanket "any 4xx" would sweep in 408, 429 and 409 — three that
        DO clear by waiting or by a retry winning the conflict."""
        from notion.sync import PERMANENTLY_REFUSING_STATUS_CODES

        self.assertEqual(set(PERMANENTLY_REFUSING_STATUS_CODES), {400, 401, 403, 404})
        for transient in (408, 409, 429):
            with self.subTest(status_code=transient):
                self.assertNotIn(transient, PERMANENTLY_REFUSING_STATUS_CODES)


class AnAbortedRunNeverReportsSuccessTests(RunnerNotionTestCase):
    """C34 §1, behavioural: a run that aborted must not fold to SUCCESS.

    `overall_status()` folds the FAILED components that were *recorded*. The
    `finally` records one — `STEP_ABORTED` — for whatever step was in flight,
    and "in flight" means "called `recorder.begin()`". Two steps did not, so
    for them the fold saw nothing and returned SUCCESS.

    Both trigger files are the ordinary ones: docs/10 §46 is written around
    the expectation that a runtime state file can be found damaged, and each
    of these two steps reads one as its first action.

    Measured before the fix, and pinned here after it:

        crash in step 4   STEP_ABORTED NONE   ->  SUCCESS / 0
        crash in step 6   STEP_ABORTED NONE   ->  SUCCESS / 0
        crash in step 7   STEP_ABORTED backup ->  FAILED  / 2   (control)

    The step 6 row is the one that matters most. That step writes Company
    History; the same corrupt file aborts every following run identically, so
    Company History stops advancing for good while every manifest says the
    run succeeded.
    """

    def _corrupt(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")

    def _summary_after_abort(self, expected_error):
        with self.assertRaises(expected_error):
            self._run(notion_sync=self.notion_sync)
        return read_summary(self.notion_sync_log_path.parent / "last_run.json")

    def _aborted(self, summary):
        return [
            c.name
            for c in summary.components
            if c.failure and c.failure.classification == "STEP_ABORTED"
        ]

    def test_a_corrupt_retry_queue_is_attributed_to_notion_sync(self):
        from notion.retry_queue import RetryQueueError

        self._write_event(event_id="EVT-Q")
        self._corrupt(self.notion_retry_queue_path)

        summary = self._summary_after_abort(RetryQueueError)

        self.assertEqual(self._aborted(summary), ["notion_sync"])

    def test_that_abort_is_degraded_not_a_false_success(self):
        """Notion is off the History critical path (README RULE 5), so
        DEGRADED is right — but SUCCESS never was."""
        from notion.retry_queue import RetryQueueError

        self._write_event(event_id="EVT-Q2")
        self._corrupt(self.notion_retry_queue_path)

        summary = self._summary_after_abort(RetryQueueError)

        self.assertIs(summary.overall_status, OverallStatus.DEGRADED)
        self.assertEqual(summary.exit_code, 3)

    def test_a_corrupt_scheduler_state_is_attributed_to_daily(self):
        from scheduler.state import SchedulerStateError

        self._write_event(event_id="EVT-D")
        self._corrupt(self.scheduler_state_path)

        summary = self._summary_after_abort(SchedulerStateError)

        self.assertEqual(self._aborted(summary), ["daily"])

    def test_that_abort_is_a_failure_because_daily_is_critical(self):
        """The row that matters: this step writes Company History, and the
        run neither wrote it nor reached Backup."""
        from scheduler.state import SchedulerStateError

        self._write_event(event_id="EVT-D2")
        self._corrupt(self.scheduler_state_path)

        summary = self._summary_after_abort(SchedulerStateError)

        self.assertIs(summary.overall_status, OverallStatus.FAILED)
        self.assertEqual(summary.exit_code, 2)
        self.assertIs(summary.component("daily").failure.severity, Severity.CRITICAL)

    def test_the_steps_before_the_abort_keep_their_real_outcomes(self):
        """The manifest is still a summary of what happened, not just of the
        crash — that is the property the `finally` exists for."""
        from scheduler.state import SchedulerStateError

        self._write_event(event_id="EVT-D3")
        self._corrupt(self.scheduler_state_path)

        summary = self._summary_after_abort(SchedulerStateError)
        recorded = {c.name: c.status for c in summary.components}

        self.assertEqual(recorded["transport"], ComponentStatus.SUCCESS)
        self.assertEqual(recorded["collector"], ComponentStatus.SUCCESS)
        self.assertEqual(recorded["history_filter"], ComponentStatus.SUCCESS)

    def test_the_steps_after_the_abort_are_reported_as_never_started(self):
        """And `daily` is no longer among them — it started."""
        from app.runner import PIPELINE_COMPONENTS
        from scheduler.state import SchedulerStateError

        self._write_event(event_id="EVT-D4")
        self._corrupt(self.scheduler_state_path)

        summary = self._summary_after_abort(SchedulerStateError)
        recorded = {c.name for c in summary.components}
        never_started = [n for n in PIPELINE_COMPONENTS if n not in recorded]

        self.assertNotIn("daily", never_started)
        self.assertEqual(
            never_started, ["late_update", "monthly", "backup", "dashboard"]
        )

    def test_the_backup_control_is_unchanged(self):
        """Step 7 always called `begin()`. Pinned so the fix is shown to have
        aligned the other two with it rather than altered it."""
        from backup.state import BackupStateError

        self._write_event(event_id="EVT-B")
        self._corrupt(self.backup_state_path)

        summary = self._summary_after_abort(BackupStateError)

        self.assertEqual(self._aborted(summary), ["backup"])
        self.assertIs(summary.overall_status, OverallStatus.FAILED)

    def test_a_clean_run_still_records_no_abort(self):
        """The other direction. `begin()` on two more steps must not invent a
        STEP_ABORTED on a run that finished."""
        self._write_event(event_id="EVT-OK")

        self._run(notion_sync=self.notion_sync)

        summary = read_summary(self.notion_sync_log_path.parent / "last_run.json")

        self.assertEqual(self._aborted(summary), [])
        self.assertIs(summary.overall_status, OverallStatus.SUCCESS)

    def test_an_unconfigured_notion_still_records_skipped_not_aborted(self):
        """`begin(C_NOTION_SYNC)` now runs even when `notion_sync is None`.
        The `skipped()` in the else-branch has to clear it, or every
        Notion-less deployment would report an abort."""
        self._write_event(event_id="EVT-NONE")

        self._run(notion_sync=None)

        summary = read_summary(self.notion_sync_log_path.parent / "last_run.json")

        self.assertEqual(self._aborted(summary), [])
        self.assertEqual(
            summary.component("notion_sync").status, ComponentStatus.SKIPPED
        )
        self.assertIs(summary.overall_status, OverallStatus.SUCCESS)


class AnUnreadableCollectedFileAbortsAtHistoryFilterTests(RunnerNotionTestCase):
    """C34 §4: step 4 handles it, step 5 dies on it. Both are correct.

    Step 4b guards its read of a collected Event file and records
    `NOTION_UNREADABLE`. Step 5 reads the *same* file with no guard at all,
    eleven lines later. So the guard does not keep the run alive — and the
    comment beside it used to say it did, with a measurement block:

        run ABORTED: ValueError / Daily files : NONE / backup state: MISSING

    Re-measured on the same scenario, that block is still what happens. The
    abort simply moved from step 4 to step 5.

    Neither half is a defect to fix:

      * step 5 aborting is BUG-20's deliberate design — History is the
        CRITICAL record and a file that cannot be read must not be silently
        dropped from it;
      * step 4's guard still buys two real things, and this class pins both.

    What it buys is attribution and evidence, not survival:

        the run is charged to `history_filter` (CRITICAL) rather than to
        `notion_sync` (DEGRADED) — the severity inversion is gone
        `NOTION_UNREADABLE <file>` reaches the log, naming the file, which
        step 5's bare traceback never does

    The consequence worth knowing, and pinned here: `Notion Unreadable` on
    the Dashboard (C33 §1) can only be non-zero from the 4a retry-queue
    path. A 4b unreadable file kills the run before step 9b writes the row.
    """

    def _corrupt_after_collection(self):
        """Corrupt the Event file in the window step 4's guard is for: after
        the Collector moved it, before Notion Sync reads it back. Reachable
        on this deployment — `runtime/` sits under OneDrive (docs/11)."""
        import app.runner as runner_module
        import collector.runtime as collector_runtime

        original = collector_runtime.run_once

        def corrupting(**kwargs):
            summary = original(**kwargs)
            for processed in summary.files:
                if processed.destination_path and processed.destination_path.is_file():
                    processed.destination_path.write_bytes(b"\xff\xfe not utf-8")
            return summary

        runner_module.collector_run_once = corrupting
        self.addCleanup(setattr, runner_module, "collector_run_once", original)

    def _run_and_read_manifest(self):
        self._write_event(event_id="EVT-UNREADABLE")
        self._corrupt_after_collection()

        with self.assertRaises(UnicodeDecodeError):
            self._run(notion_sync=self.notion_sync)

        return read_summary(self.notion_sync_log_path.parent / "last_run.json")

    def test_step_four_records_it_and_does_not_abort(self):
        summary = self._run_and_read_manifest()
        notion_sync = summary.component("notion_sync")

        self.assertEqual(notion_sync.status, ComponentStatus.FAILED)
        self.assertEqual(notion_sync.metrics["unreadable"], 1)
        self.assertNotEqual(notion_sync.failure.classification, "STEP_ABORTED")

    def test_step_five_is_the_step_that_aborts(self):
        """The severity inversion the guard removed: a DEGRADED step is no
        longer recorded as the one that killed the run."""
        summary = self._run_and_read_manifest()
        aborted = [
            c.name
            for c in summary.components
            if c.failure and c.failure.classification == "STEP_ABORTED"
        ]

        self.assertEqual(aborted, ["history_filter"])
        self.assertIs(
            summary.component("history_filter").failure.severity, Severity.CRITICAL
        )

    def test_the_run_still_dies_and_says_so(self):
        """The half the old comment got wrong. The guard did not keep Daily
        History or Backup alive, and the manifest must not pretend it did."""
        summary = self._run_and_read_manifest()

        self.assertIs(summary.overall_status, OverallStatus.FAILED)
        self.assertEqual(summary.exit_code, 2)
        self.assertFalse((self.local_master_dir / "daily").exists())
        self.assertFalse(self.backup_state_path.exists())

    def test_the_operator_is_told_which_file(self):
        """The other thing the guard genuinely buys. Step 5's traceback names
        an exception, not a filename."""
        self._run_and_read_manifest()
        log = self.notion_sync_log_path.read_text(encoding="utf-8")

        self.assertIn("NOTION_UNREADABLE", log)
        self.assertIn("EVT-UNREADABLE", log)

    def test_the_dashboard_row_is_never_written_on_this_path(self):
        """Why `Notion Unreadable` (C33 §1) can only be non-zero from the 4a
        retry-queue path: step 9b is five steps after the abort."""
        summary = self._run_and_read_manifest()

        self.assertIsNone(summary.component("dashboard"))
        self.assertEqual(self.dashboard_transport._pages, {})

    def test_step_five_still_has_no_per_event_guard(self):
        """BUG-20's characterization, restated where it now matters. If this
        ever changes, the whole class above needs rewriting — the run would
        survive and the Dashboard row would appear."""
        import inspect

        from app import runner

        source = inspect.getsource(runner.run_once)
        step5 = source[source.index("# 5. History Filter"):source.index("# 6. Daily History")]

        self.assertIn("Event.from_json(", step5)
        self.assertNotIn("except", step5)


# The Scheduler never processes "today" (docs/07), and the shared fixture's
# default `now` is the day the test Events are dated — so a run at the default
# renders no Daily at all and every recovery assertion would pass vacuously.
RUN_ONE = datetime(2026, 8, 2, 9, 0)
RUN_TWO = datetime(2026, 8, 3, 9, 0)


class RerunAfterAbortTests(RunnerNotionTestCase):
    """C35: what does run N+1 make of run N's leftovers?

    `WholePipelineIdempotencyTests` above covers **success -> rerun**: a
    second identical run changes nothing that matters. The other half was
    covered by nothing — **abort -> rerun**, which is the case an operator
    actually meets, because a scheduled Runner retries on its next trigger
    whatever happened last time.

    The two halves need different assertions. After a success, the property
    is "nothing changed". After an abort, run N has already written *some*
    of its artifacts, and the property is one of exactly two things:

        recovered   run N+1 finishes the work run N started, without
                    duplicating the part that was already done
        detected    the work is unrecoverable and something says so —
                    silence is the failure mode, not the loss

    Each test below aborts one step, then runs a clean second time, and
    asserts which of the two happened. Measured first, then pinned.

    Why per-step rather than one generic case: each step leaves a different
    half-state behind. Step 5 leaves a consumed Event with no Candidate
    (A-20); step 6 leaves a Candidate with no Daily file; step 7 leaves
    Company History that is not yet backed up. Only step 6 and step 7 are
    recoverable, and it matters that the tests say which.
    """

    # ------------------------------------------------------------ helpers
    def _keepable_event(self, event_id):
        """An Event the History Filter KEEPs.

        The shared fixture's default is `STARTED`, which docs/05 always
        DROPs — so a Candidate is never written and every assertion about
        recovery would pass vacuously.
        """
        return self._write_event(
            event_id=event_id,
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            milestone="M1",
        )

    def _manifest(self):
        return read_summary(self.notion_sync_log_path.parent / "last_run.json")

    def _abort_in(self, attribute, exception=RuntimeError("simulated crash")):
        """Replace one of run_once's step callables with a raise, for one run."""
        import app.runner as runner_module

        original = getattr(runner_module, attribute)

        def boom(*args, **kwargs):
            raise exception

        setattr(runner_module, attribute, boom)
        self.addCleanup(setattr, runner_module, attribute, original)
        return original

    def _daily_dir(self):
        return self.local_master_dir / "daily"

    def _daily_event_ids(self):
        ids = []
        if self._daily_dir().is_dir():
            for path in sorted(self._daily_dir().glob("*.md")):
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.startswith("- Event ID: "):
                        ids.append(line[len("- Event ID: ") :])
        return ids

    def _candidates(self):
        return sorted(p.stem for p in self.keep_dir.glob("*.json")) \
            if self.keep_dir.is_dir() else []

    def _commits(self):
        return len(
            self._run_git(["log", "--oneline"], cwd=self.backup_working_copy_dir)
            .strip()
            .splitlines()
        )

    # -------------------------------------------------- abort in step 6
    def test_an_abort_in_daily_is_recovered_by_the_next_run(self):
        """Run 1 wrote the Candidate and died before rendering it. The
        Candidate is durable, so run 2 must finish the job."""
        self._keepable_event("EVT-D6")
        restore = self._abort_in("scheduler_run_once")

        with self.assertRaises(RuntimeError):
            self._run(notion_sync=self.notion_sync, now=RUN_ONE)

        self.assertEqual(self._candidates(), ["HIST-EVT-D6"])
        self.assertEqual(self._daily_event_ids(), [], "nothing rendered yet")
        self.assertEqual(
            [c.name for c in self._manifest().components
             if c.failure and c.failure.classification == "STEP_ABORTED"],
            ["daily"],
        )

        import app.runner as runner_module
        runner_module.scheduler_run_once = restore
        self._run(notion_sync=self.notion_sync, now=RUN_TWO)

        self.assertIn("EVT-D6", self._daily_event_ids())
        self.assertIs(self._manifest().overall_status, OverallStatus.SUCCESS)

    def test_that_recovery_does_not_duplicate_the_candidate(self):
        """Run 1 already wrote it. Re-writing would raise FileExistsError
        (BUG-10); re-rendering would double the Event in Company History."""
        self._keepable_event("EVT-D6B")
        restore = self._abort_in("scheduler_run_once")
        with self.assertRaises(RuntimeError):
            self._run(notion_sync=self.notion_sync, now=RUN_ONE)

        import app.runner as runner_module
        runner_module.scheduler_run_once = restore
        self._run(notion_sync=self.notion_sync, now=RUN_TWO)

        self.assertEqual(self._candidates(), ["HIST-EVT-D6B"])
        self.assertEqual(self._daily_event_ids().count("EVT-D6B"), 1)

    # -------------------------------------------------- abort in step 7
    def test_an_abort_in_backup_leaves_history_intact_and_is_recovered(self):
        """Company History is written before Backup on purpose. An abort
        there must cost the backup, never the history."""
        self._keepable_event("EVT-B7")
        restore = self._abort_in("backup_run_once")

        with self.assertRaises(RuntimeError):
            self._run(notion_sync=self.notion_sync, now=RUN_ONE)

        self.assertIn("EVT-B7", self._daily_event_ids())
        self.assertFalse(self.backup_state_path.exists())
        before = self._commits()

        import app.runner as runner_module
        runner_module.backup_run_once = restore
        self._run(notion_sync=self.notion_sync, now=RUN_TWO)

        self.assertGreater(self._commits(), before, "run 2 must ship the backlog")
        tracked = self._run_git(["ls-files"], cwd=self.backup_working_copy_dir)
        self.assertIn("daily/2026-08-01.md", tracked)

    # -------------------------------------------------- abort in step 5
    def test_an_abort_in_history_filter_is_not_recovered_but_is_detected(self):
        """A-20's window. The Collector already consumed the Event, so no
        later run reconsiders it — the Event is gone from Company History
        for good. The requirement is therefore detection, not recovery."""
        from history.reconciliation import find_orphaned_events

        self._keepable_event("EVT-H5")

        # The window itself: the Collector moves the file and marks it seen,
        # then the run dies before step 5 writes the Candidate. Injected at
        # the Collector boundary rather than inside step 5, because that is
        # where the irreversible half happens — once `mark_seen()` is saved
        # and the file has moved out of `incoming/`, no later run
        # reconsiders the Event.
        import app.runner as runner_module
        import collector.runtime as collector_runtime
        original = collector_runtime.run_once

        def consume_then_die(**kwargs):
            original(**kwargs)
            raise RuntimeError("crash between Collector and History Filter")

        runner_module.collector_run_once = consume_then_die
        self.addCleanup(setattr, runner_module, "collector_run_once", original)

        with self.assertRaises(RuntimeError):
            self._run(notion_sync=self.notion_sync, now=RUN_ONE)

        runner_module.collector_run_once = original
        self._run(notion_sync=self.notion_sync, now=RUN_TWO)

        # Not recovered: no Candidate, not in Company History.
        self.assertEqual(self._candidates(), [])
        self.assertNotIn("EVT-H5", self._daily_event_ids())
        # And run 2 reports success, which is why detection has to exist.
        self.assertIs(self._manifest().overall_status, OverallStatus.SUCCESS)

        # Detected: the reconciler names the Event.
        result = find_orphaned_events(
            processed_dir=self.processed_dir,
            keep_dir=self.keep_dir,
            review_dir=self.review_dir,
        )
        self.assertEqual([o.event_id for o in result.orphaned], ["EVT-H5"])

    def test_an_unreadable_consumed_event_is_reported_separately(self):
        """The same window with a file that cannot be parsed. "I cannot tell
        whether this one is missing" is a different statement from "this one
        is missing", and the reconciler must not conflate them."""
        from history.reconciliation import find_orphaned_events

        self._keepable_event("EVT-U5")
        import app.runner as runner_module
        import collector.runtime as collector_runtime
        original = collector_runtime.run_once

        def corrupt_then_die(**kwargs):
            summary = original(**kwargs)
            for processed in summary.files:
                if processed.destination_path and processed.destination_path.is_file():
                    processed.destination_path.write_bytes(b"\xff\xfe")
            raise RuntimeError("crash after corruption")

        runner_module.collector_run_once = corrupt_then_die
        self.addCleanup(setattr, runner_module, "collector_run_once", original)
        with self.assertRaises(RuntimeError):
            self._run(notion_sync=self.notion_sync, now=RUN_ONE)

        runner_module.collector_run_once = original
        self._run(notion_sync=self.notion_sync, now=RUN_TWO)

        result = find_orphaned_events(
            processed_dir=self.processed_dir,
            keep_dir=self.keep_dir,
            review_dir=self.review_dir,
        )
        self.assertEqual(result.orphaned, ())
        self.assertEqual([u.event_path.name for u in result.unreadable], ["EVT-U5.json"])

    # -------------------------------------------------- lock left behind
    def test_a_stale_lock_from_a_dead_run_is_taken_over(self):
        """A crashed run cannot release its lock. If the next run refused it,
        every later run would skip — and `run_company_ops.py` returns 0 for a
        skip, so the scheduler would see success forever."""
        self.runner_lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.runner_lock_path.write_text(
            json.dumps({"pid": 999999, "acquired_at": "2026-08-01T09:00:00+09:00"}),
            encoding="utf-8",
        )
        self._keepable_event("EVT-LOCK")

        result = self._run(notion_sync=self.notion_sync, now=RUN_ONE)

        self.assertIsNotNone(result, "the run must take over a dead run's lock")
        self.assertIn("EVT-LOCK", self._daily_event_ids())

    # -------------------------------------------------- manifest honesty
    def test_the_second_runs_manifest_describes_the_second_run(self):
        """`runs/last_run.json` is one file. Run N's abort record is replaced
        by run N+1 — by design, the name says "last run" — so the manifest
        must never carry a stale component from the aborted run."""
        self._keepable_event("EVT-M")
        restore = self._abort_in("backup_run_once")
        with self.assertRaises(RuntimeError):
            self._run(notion_sync=self.notion_sync, now=RUN_ONE)
        self.assertEqual(
            [c.name for c in self._manifest().components
             if c.failure and c.failure.classification == "STEP_ABORTED"],
            ["backup"],
        )

        import app.runner as runner_module
        runner_module.backup_run_once = restore
        self._run(notion_sync=self.notion_sync, now=RUN_TWO)

        summary = self._manifest()
        self.assertEqual(
            [c.name for c in summary.components
             if c.failure and c.failure.classification == "STEP_ABORTED"],
            [],
        )
        self.assertEqual(summary.component("backup").status, ComponentStatus.SUCCESS)
        self.assertIs(summary.overall_status, OverallStatus.SUCCESS)


class SameInstantSkipReachesTheManifestTests(RunnerNotionTestCase):
    """BACKLOG E-23, made countable without touching either specification.

    Two Signals written for one date with no timestamp of their own get the
    same midnight (docs/06 §12), so for one project the Late Event guard
    (docs/04 §29-30) lets only the first reach Notion. Company History keeps
    both. Neither spec is wrong and neither is changed here — what C40 adds
    is that the run can now *say* it happened.

    Driven through the real Runner rather than the sync module alone,
    because the claim is about the Run Manifest an operator reads, not about
    a return value.
    """

    STAMP = "2026-08-10T00:00:00+09:00"

    def _two_signals_of_the_same_instant(self):
        # `MILESTONE_COMPLETED`, not the fixture's default `STARTED`: the
        # History Filter DROPs STARTED, so a default-typed Event produces no
        # Candidate and the "Company History keeps both" assertion below
        # would pass or fail for a reason that has nothing to do with E-23.
        # Measured while writing it — the first run left `Event Count: 1`.
        self._write_event(
            event_id="TIE-RUNNER-1",
            event_type="MILESTONE_COMPLETED",
            milestone="first signal of the day",
            summary="first signal of the day",
            timestamp=self.STAMP,
        )
        self._write_event(
            event_id="TIE-RUNNER-2",
            event_type="MILESTONE_COMPLETED",
            milestone="second signal of the day",
            summary="second signal of the day",
            timestamp=self.STAMP,
        )

    def test_the_manifest_counts_the_skip(self):
        self._two_signals_of_the_same_instant()

        result = self._run(notion_sync=self.notion_sync, now=datetime(2026, 8, 11, 9, 0))

        component = result.summary.component("notion_sync")
        self.assertEqual(component.status, ComponentStatus.SUCCESS)
        self.assertEqual(component.metrics["same_instant_skips"], 1)

    def test_an_ordinary_run_carries_no_such_metric(self):
        """`or None` in the Runner: the number appears only when it happened,
        so it never becomes a standing `same_instant_skips=0` that a reader
        learns to scroll past."""
        self._write_event(event_id="TIE-RUNNER-SOLO", timestamp=self.STAMP)

        result = self._run(notion_sync=self.notion_sync, now=datetime(2026, 8, 11, 9, 0))

        component = result.summary.component("notion_sync")
        self.assertNotIn("same_instant_skips", component.metrics)

    def test_the_reason_reaches_the_notion_log(self):
        """The existing sink, unchanged: `_log_notion_sync()` writes whenever
        a result carries an error, and this note is why that condition was
        widened from status-based in C34."""
        self._two_signals_of_the_same_instant()

        self._run(notion_sync=self.notion_sync, now=datetime(2026, 8, 11, 9, 0))

        log = self.notion_sync_log_path.read_text(encoding="utf-8")
        self.assertIn("same-instant skip", log)
        self.assertIn("TIE-RUNNER-2", log)

    def test_the_run_is_still_a_success(self):
        """The severity claim, pinned. Company History has both Events and
        the Notion row is a View (docs/14 §1). Turning this into a failure
        would cry wolf on a run that lost nothing."""
        self._two_signals_of_the_same_instant()

        result = self._run(notion_sync=self.notion_sync, now=datetime(2026, 8, 11, 9, 0))

        self.assertEqual(result.summary.overall_status, OverallStatus.SUCCESS)
        self.assertEqual(result.summary.exit_code, 0)

    def test_company_history_kept_the_event_notion_dropped(self):
        """The half that makes the severity judgement true. If this ever
        stops holding, the metric above is no longer 'a View is behind' — it
        is data loss, and the severity has to be revisited with it."""
        self._two_signals_of_the_same_instant()

        self._run(notion_sync=self.notion_sync, now=datetime(2026, 8, 11, 9, 0))

        daily = self.local_master_dir / "daily" / "2026-08-10.md"
        text = daily.read_text(encoding="utf-8")
        self.assertIn("TIE-RUNNER-1", text)
        self.assertIn("TIE-RUNNER-2", text)


class TheManifestSaysWhetherNotionChangedTests(RunnerNotionTestCase):
    """C104: `processed` counted attempts, and nothing counted outcomes.

    Measured through the real Runner against the live PROJECTS database. The
    whole Event corpus synced, and the manifest said:

        notion_sync SUCCESS {"processed": 16}

    Every one of those sixteen was `NOTION_SKIPPED_OLD_EVENT` -- Notion
    already held newer state, so **not one row changed**. A run that rewrote
    all sixteen rows would have written the same bytes into the manifest.

    That matters because of where the manifest sits. It is the
    machine-readable record: `ops_status.py` reads it, a Task Scheduler
    deployment keeps it, and it is what survives after `notion_sync.log`
    rotates. Answering "did Notion actually change?" meant reading that log
    line by line -- which is the work a manifest exists to remove.

    No status was added (docs/04 section 32-37 enumerates those and adding one
    is a spec change) and no behaviour moved. `RunComponent.metrics` is a
    free-form `Mapping[str, Any]` that docs/14 does not constrain, and C40
    already established that adding a key to it is not a decision.
    """

    def test_a_run_that_changes_nothing_says_so(self):
        """The measured case, reproduced. First run creates; the identical
        second run changes nothing, and the two manifests must differ."""
        self._write_event(event_id="MET-NOOP-1", timestamp="2026-08-05T10:00:00+09:00")
        first = self._run(notion_sync=self.notion_sync)
        first_metrics = dict(first.summary.component("notion_sync").metrics)

        second = self._run(
            notion_sync=self.notion_sync, now=datetime(2026, 8, 5, 13, 0)
        )
        second_metrics = dict(second.summary.component("notion_sync").metrics)

        self.assertEqual(first_metrics.get("created"), 1)
        self.assertEqual(first_metrics.get("updated"), 0)
        self.assertEqual(second_metrics.get("created"), 0)
        self.assertEqual(second_metrics.get("updated"), 0)
        self.assertNotEqual(
            first_metrics,
            second_metrics,
            "a run that wrote a row and a run that wrote nothing must not "
            "produce the same manifest -- that identity is the defect",
        )

    def test_a_created_row_is_counted_as_created(self):
        self._write_event(event_id="MET-CREATE-1")

        result = self._run(notion_sync=self.notion_sync)

        metrics = result.summary.component("notion_sync").metrics
        self.assertEqual(metrics["processed"], 1)
        self.assertEqual(metrics["created"], 1)
        self.assertEqual(metrics["updated"], 0)
        self.assertEqual(metrics["skipped_old"], 0)

    def test_an_updated_row_is_counted_as_updated(self):
        """A second, strictly newer Event for the same project takes the
        UPDATE branch (docs/04 section 6), which `created` must not absorb."""
        self._write_event(event_id="MET-UPD-1", timestamp="2026-08-05T10:00:00+09:00")
        self._run(notion_sync=self.notion_sync)

        self._write_event(
            event_id="MET-UPD-2",
            event_type="MILESTONE_COMPLETED",
            milestone="second",
            summary="second",
            timestamp="2026-08-05T11:00:00+09:00",
        )
        result = self._run(
            notion_sync=self.notion_sync, now=datetime(2026, 8, 5, 13, 0)
        )

        metrics = result.summary.component("notion_sync").metrics
        self.assertEqual(metrics["created"], 0)
        self.assertEqual(metrics["updated"], 1)
        self.assertEqual(metrics["skipped_old"], 0)

    def test_an_older_event_is_counted_as_skipped_old(self):
        self._write_event(event_id="MET-OLD-1", timestamp="2026-08-05T18:00:00+09:00")
        self._run(notion_sync=self.notion_sync)

        self._write_event(
            event_id="MET-OLD-2",
            event_type="MILESTONE_COMPLETED",
            milestone="late",
            summary="late",
            timestamp="2026-08-04T09:00:00+09:00",
        )
        result = self._run(
            notion_sync=self.notion_sync, now=datetime(2026, 8, 6, 13, 0)
        )

        metrics = result.summary.component("notion_sync").metrics
        self.assertEqual(metrics["created"], 0)
        self.assertEqual(metrics["updated"], 0)
        self.assertEqual(metrics["skipped_old"], 1)

    def test_the_three_counts_never_exceed_processed(self):
        """The arithmetic an operator will do without being asked to.

        `skipped_old` deliberately **includes** `same_instant_skips` -- a
        same-instant skip returns NOTION_SKIPPED_OLD_EVENT and is counted by
        both -- so the three new keys partition `processed` while the older
        key overlaps one of them. Pinned here so the overlap stays a
        documented property rather than being discovered as a discrepancy.
        """
        for i, stamp in enumerate(
            ("2026-08-05T10:00:00+09:00", "2026-08-05T11:00:00+09:00")
        ):
            self._write_event(
                event_id=f"MET-SUM-{i}",
                event_type="MILESTONE_COMPLETED",
                milestone=f"m{i}",
                summary=f"m{i}",
                timestamp=stamp,
            )

        result = self._run(notion_sync=self.notion_sync)

        m = result.summary.component("notion_sync").metrics
        self.assertEqual(
            m["created"] + m["updated"] + m["skipped_old"],
            m["processed"],
            "every sync result must land in exactly one of the three",
        )

    def test_zeros_are_kept_rather_than_dropped(self):
        """The opposite choice from `same_instant_skips`, on purpose.

        That metric reports a rare divergence, so its absence means "did not
        happen". Here `written = 0` is the *informative* case: it is how
        "Notion is already current" stops being byte-identical to "nothing
        reached Notion". Dropping the zero would restore the defect.
        """
        self._write_event(event_id="MET-ZERO-1")
        self._run(notion_sync=self.notion_sync)

        result = self._run(
            notion_sync=self.notion_sync, now=datetime(2026, 8, 5, 13, 0)
        )

        m = result.summary.component("notion_sync").metrics
        for key in ("created", "updated", "skipped_old"):
            self.assertIn(key, m, f"{key} must be present even when it is 0")
        self.assertEqual(m["created"], 0)

    def test_the_failing_path_carries_the_counts_too(self):
        """A partly-failed run is exactly when "how much of it landed"
        decides whether a person must finish the job by hand."""
        self._write_event(event_id="MET-FAIL-1")
        self.transport.fail_next_call = True

        result = self._run(notion_sync=self.notion_sync)

        component = result.summary.component("notion_sync")
        self.assertEqual(component.status, ComponentStatus.FAILED)
        for key in ("created", "updated", "skipped_old"):
            self.assertIn(key, component.metrics)
        self.assertEqual(component.metrics["created"], 0)
        self.assertGreaterEqual(component.metrics["queued"], 1)


if __name__ == "__main__":
    unittest.main()
