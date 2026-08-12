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


if __name__ == "__main__":
    unittest.main()
