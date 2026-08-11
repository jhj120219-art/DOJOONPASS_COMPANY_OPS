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

from app.runner import run_once  # noqa: E402
from notion import (  # noqa: E402
    ExecutionPlanSync,
    InMemoryNotionTransport,
    NotionClient,
    SyncStatus,
)
from notion.dashboard_pending import load_pending  # noqa: E402
from reporter import DesktopProfile, Reporter  # noqa: E402
from scheduler import SchedulerStatus  # noqa: E402


class RunnerNotionIntegrationTests(unittest.TestCase):
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
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
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


if __name__ == "__main__":
    unittest.main()
