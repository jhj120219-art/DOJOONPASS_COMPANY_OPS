"""Notion Retry Queue Stress Test (CEO Policy Decision — Notion Retry
Architecture Plan A), run through the real Runner path end-to-end.

Required by this Sprint: 100 failures -> 100 recoveries -> duplicate check
-> loss check -> Recovery verification, all against actual code (not a
synthetic reasoning exercise).
"""

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
    NotionAPIError,
    NotionClient,
    NotionTransport,
    SyncStatus,
    load_queue,
)
from reporter import Reporter  # noqa: E402


class AlwaysFailingNotionTransport(NotionTransport):
    """Every call fails — simulates a sustained Notion outage across an
    entire Runner execution (not just "the next single call")."""

    def retrieve_database(self, database_id):
        raise NotionAPIError("simulated sustained outage", status_code=503)

    def query_database(self, database_id, filter_):
        raise NotionAPIError("simulated sustained outage", status_code=503)

    def create_page(self, database_id, properties):
        raise NotionAPIError("simulated sustained outage", status_code=503)

    def update_page(self, page_id, properties):
        raise NotionAPIError("simulated sustained outage", status_code=503)

    def update_database(self, database_id, properties):
        raise NotionAPIError("simulated sustained outage", status_code=503)

    def create_database(self, parent_page_id, title, properties):
        raise NotionAPIError("simulated sustained outage", status_code=503)


def _run_git(args, cwd):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git {args} failed: {result.stderr}")
    return result.stdout


class RetryQueueStressTest(unittest.TestCase):
    N_EVENTS = 100

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

        self.local_master_dir = self.root / "local_master"
        self.local_master_dir.mkdir(parents=True)
        self.backup_working_copy_dir = self.root / "backup_working_copy"
        self.backup_working_copy_dir.mkdir(parents=True)
        bare_remote = self.root / "remote.git"
        _run_git(["init", "--bare", "-b", "main", str(bare_remote)], self.root)
        _run_git(["init", "-b", "main"], self.backup_working_copy_dir)
        _run_git(["config", "user.email", "stress@example.invalid"], self.backup_working_copy_dir)
        _run_git(["config", "user.name", "Retry Queue Stress"], self.backup_working_copy_dir)
        _run_git(["remote", "add", "origin", str(bare_remote)], self.backup_working_copy_dir)
        (self.backup_working_copy_dir / ".gitkeep").write_text("", encoding="utf-8")
        _run_git(["add", "-A"], self.backup_working_copy_dir)
        _run_git(["commit", "-m", "init"], self.backup_working_copy_dir)
        _run_git(["push", "-u", "origin", "main"], self.backup_working_copy_dir)

        self.runner_lock_path = self.root / "runtime" / "locks" / "company_ops.lock"
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

        self.reporter = Reporter(profile="DESKTOP_3")

    def _run(self, *, notion_sync, now):
        return run_once(
            local_master_dir=self.local_master_dir,
            backup_working_copy_dir=self.backup_working_copy_dir,
            history_start_date=date(2026, 8, 1),
            runner_lock_path=self.runner_lock_path,
            now=now,
            incoming_dir=self.incoming_dir,
            processed_dir=self.processed_dir,
            rejected_dir=self.rejected_dir,
            collector_log_path=self.collector_log_path,
            collector_state_path=self.collector_state_path,
            notion_sync=notion_sync,
            notion_sync_log_path=self.notion_sync_log_path,
            notion_retry_queue_path=self.notion_retry_queue_path,
            keep_dir=self.keep_dir,
            review_dir=self.review_dir,
            scheduler_state_path=self.scheduler_state_path,
            backup_state_path=self.backup_state_path,
        )

    def test_100_failures_then_100_recoveries_no_duplicates_no_loss(self):
        self.incoming_dir.mkdir(parents=True, exist_ok=True)
        expected_ids = set()
        for i in range(self.N_EVENTS):
            event_id = f"STRESS-RETRY-{i:04d}"
            expected_ids.add(event_id)
            self.reporter.report_and_write(
                directory=self.incoming_dir,
                event_id=event_id,
                # Distinct project_id per event so each Sync is a clean
                # CREATE (docs/04 §6) — the point of this stress test is
                # the Retry Queue's dedup/loss/drain behavior, not the
                # Sync CREATE/UPDATE/Late-Event branching already covered
                # by test_notion_sync.py.
                project_id=f"STRESS_PROJECT_{i:04d}",
                event_type="STARTED",
                status="IN_PROGRESS",
                summary=f"stress event {i}",
                evidence=[],
                history_candidate=True,
                timestamp=f"2026-08-01T{10 + (i % 10):02d}:00:00+09:00",
            )

        # --- Pass 1: sustained Notion outage across all 100 events ---
        failing_sync = ExecutionPlanSync(
            client=NotionClient(transport=AlwaysFailingNotionTransport(), database_id="DB-1")
        )
        first = self._run(notion_sync=failing_sync, now=datetime(2026, 8, 1, 12, 0))
        self.assertIsNotNone(first)
        _, collector_summary, _, _, first_results = first

        self.assertEqual(collector_summary.accepted, self.N_EVENTS)
        self.assertEqual(len(first_results), self.N_EVENTS)
        self.assertTrue(all(r.status == SyncStatus.NOTION_RETRY_REQUIRED for r in first_results))

        queued = load_queue(self.notion_retry_queue_path)
        queued_ids = [e.event_id for e in queued]
        self.assertEqual(len(queued_ids), self.N_EVENTS)  # no duplicates
        self.assertEqual(set(queued_ids), expected_ids)  # no loss
        self.assertTrue(all(e.attempt_count == 1 for e in queued))

        # --- Pass 2: Notion recovers; nothing new arrives in incoming/ ---
        working_sync = ExecutionPlanSync(
            client=NotionClient(transport=InMemoryNotionTransport(), database_id="DB-1")
        )
        second = self._run(notion_sync=working_sync, now=datetime(2026, 8, 2, 9, 0))
        self.assertIsNotNone(second)
        _, collector_summary2, _, _, second_results = second

        self.assertEqual(collector_summary2.accepted, 0)  # nothing new collected
        self.assertEqual(len(second_results), self.N_EVENTS)  # all 100 retried from the queue
        recovered_ids = {r.event_id for r in second_results}
        self.assertEqual(recovered_ids, expected_ids)  # no loss
        self.assertTrue(all(r.status == SyncStatus.NOTION_CREATED for r in second_results))

        remaining = load_queue(self.notion_retry_queue_path)
        self.assertEqual(remaining, [])  # fully drained, nothing left behind

        # --- Pass 3 (Recovery verification): a third run finds nothing to
        # retry and nothing new — must be a clean no-op, not a re-send. ---
        third = self._run(notion_sync=working_sync, now=datetime(2026, 8, 3, 9, 0))
        _, collector_summary3, _, _, third_results = third
        self.assertEqual(collector_summary3.accepted, 0)
        self.assertEqual(third_results, ())


if __name__ == "__main__":
    unittest.main()
