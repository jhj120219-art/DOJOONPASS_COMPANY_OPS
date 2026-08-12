"""Runner Failure-Path Characterization Tests (Audit Sprint).

Every other Runner-level test in this repository exercises a happy path or a
Notion-specific failure. Nothing covered what `app.runner.run_once()` does
when the *filesystem*, the *git remote*, or a *state file* fails — which is
where this Sprint's audit found its highest-severity defects.

These are CHARACTERIZATION tests: each one asserts the behaviour the code
has TODAY, not the behaviour the spec asks for. Where the two differ, the
test says so explicitly and names the audit finding. That is deliberate:

  * it stops the current behaviour from drifting unnoticed;
  * it makes the gap executable and reviewable instead of prose in a report;
  * when a fix is approved, the failing assertion is the checklist.

Nothing here changes production code, Runtime behaviour, or any spec. Uses
the same real-filesystem / real-git / InMemoryNotionTransport approach the
rest of the suite already uses (docs/10 section 10: Mock-only 검증 금지).

Audit findings referenced below:
    BUG-1   BACKUP_PENDING never re-pushes once the remote recovers
    BUG-3   a corrupt runtime state file aborts the whole Runner
    BUG-4   a Backup GitOperationError escapes run_once()
    BUG-9   mark_seen() is persisted before the file move succeeds
    BUG-10  FileExistsError on re-save aborts the Runner
    BUG-13  a permanent Notion failure is queued and retried forever
    BUG-17  a Daily History file already written is never updated
"""

import ast
import inspect
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import app.runner as runner_module  # noqa: E402
from app.runner import run_once  # noqa: E402
from backup.git_ops import GitOperationError  # noqa: E402
from backup.result import BackupStatus  # noqa: E402
from backup.state import BackupStateError  # noqa: E402
from collector import Collector, InMemorySeenEventStore, RuntimeOutcome  # noqa: E402
from collector.runtime import run_once as collector_run_once  # noqa: E402
from collector.state import CollectorStateError  # noqa: E402
from agent.delivery import DeliveryProblem, find_undelivered_events  # noqa: E402
from agent.signals import load_signals  # noqa: E402
from daily import (  # noqa: E402
    LateUpdateOutcome,
    generate_daily_history,
    update_daily_history,
)
from history import FileHistoryRepository, HistoryCandidate, HistoryDecision  # noqa: E402
from events import Event, create_event  # noqa: E402
from notion import (  # noqa: E402
    ExecutionPlanSync,
    InMemoryNotionTransport,
    NotionAPIError,
    NotionClient,
)
from notion.retry_queue import RetryQueueError  # noqa: E402
from notion.retry_queue import save_queue as save_retry_queue  # noqa: E402
from notion.sync import SyncResult, SyncStatus  # noqa: E402
from runsummary import ComponentStatus, read_summary  # noqa: E402
from scheduler.state import SchedulerStateError  # noqa: E402
from reporter import Reporter  # noqa: E402


def _force_rmtree(path: Path) -> None:
    """git object files are read-only on Windows; clear the flag first.

    shutil.rmtree's `onexc` callback was added in Python 3.12; `onerror`
    (deprecated there, still the only option before it) has a different
    callback signature, so which kwarg to pass has to be chosen at runtime.
    """

    def onexc(func, target, exc):
        try:
            Path(target).chmod(stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    def onerror(func, target, exc_info):
        onexc(func, target, exc_info[1])

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=onexc)
    else:
        shutil.rmtree(path, onerror=onerror)


class StrictNotionTransport(InMemoryNotionTransport):
    """InMemoryNotionTransport that also enforces Notion's own documented
    payload limits, so a spec-violating payload fails the way the live API
    would (HTTP 400) instead of silently succeeding.

    Only the limit this audit actually exercises is implemented: rich_text /
    title content is capped at 2000 characters by the Notion API.
    """

    MAX_TEXT = 2000

    def _reject_oversized(self, properties):
        for name, prop in (properties or {}).items():
            for key in ("rich_text", "title"):
                for item in prop.get(key, []) or []:
                    content = item.get("text", {}).get("content", "")
                    if len(content) > self.MAX_TEXT:
                        raise NotionAPIError(
                            f"Notion API returned 400: properties.{name} content "
                            f"length {len(content)} exceeds {self.MAX_TEXT}",
                            status_code=400,
                        )

    def create_page(self, database_id, properties):
        self._reject_oversized(properties)
        return super().create_page(database_id, properties)

    def update_page(self, page_id, properties):
        self._reject_oversized(properties)
        return super().update_page(page_id, properties)


class RunnerFailurePathTestCase(unittest.TestCase):
    """Shared workspace: same layout the other Runner tests build."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

        self.local_master_dir = self.root / "local_master"
        self.local_master_dir.mkdir(parents=True, exist_ok=True)
        self.backup_working_copy_dir = self.root / "backup_working_copy"
        self.backup_working_copy_dir.mkdir(parents=True, exist_ok=True)
        self.bare_remote_dir = self.root / "backup_remote.git"
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
        self.run_summary_path = self.root / "runtime" / "runs" / "last_run.json"

        self.reporter = Reporter(profile="DESKTOP_3")

    # ---------------------------------------------------------------- git
    def _run_git(self, args, cwd, check=True):
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def _init_backup_git_repo(self, working_copy_dir: Path) -> None:
        self._run_git(["init", "--bare", "-b", "main", str(self.bare_remote_dir)], cwd=self.root)
        self._run_git(["init", "-b", "main"], cwd=working_copy_dir)
        self._run_git(["config", "user.email", "test@example.invalid"], cwd=working_copy_dir)
        self._run_git(["config", "user.name", "Runner Failure Path Test"], cwd=working_copy_dir)
        self._run_git(["remote", "add", "origin", str(self.bare_remote_dir)], cwd=working_copy_dir)
        (working_copy_dir / ".gitkeep").write_text("", encoding="utf-8")
        self._run_git(["add", "-A"], cwd=working_copy_dir)
        self._run_git(["commit", "-m", "init"], cwd=working_copy_dir)
        self._run_git(["push", "-u", "origin", "main"], cwd=working_copy_dir)

    def _unpushed_commit_count(self) -> int:
        result = self._run_git(
            ["rev-list", "--count", "origin/main..HEAD"], cwd=self.backup_working_copy_dir
        )
        return int(result.stdout.strip())

    # -------------------------------------------------------------- event
    def _write_event(self, **overrides):
        self.incoming_dir.mkdir(parents=True, exist_ok=True)
        data = dict(
            project_id="SEARCH_FRONTEND",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            summary="failure path test event",
            milestone="M1",
            evidence=[],
            history_candidate=True,
            timestamp="2026-08-01T10:00:00+09:00",
        )
        data.update(overrides)
        _, path = self.reporter.report_and_write(directory=self.incoming_dir, **data)
        return path

    def _run(self, *, now=None, notion_sync=None, dashboard_client=None, run_id=None):
        return run_once(
            local_master_dir=self.local_master_dir,
            backup_working_copy_dir=self.backup_working_copy_dir,
            history_start_date=date(2026, 8, 1),
            runner_lock_path=self.runner_lock_path,
            now=now or datetime(2026, 8, 2, 12, 0).astimezone(),
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
            run_summary_path=self.run_summary_path,
            notion_retry_queue_path=self.notion_retry_queue_path,
            dashboard_client=dashboard_client,
            dashboard_pending_path=self.dashboard_pending_path,
            keep_dir=self.keep_dir,
            review_dir=self.review_dir,
            scheduler_state_path=self.scheduler_state_path,
            backup_state_path=self.backup_state_path,
            run_id=run_id,
        )


class BackupFailurePathTests(RunnerFailurePathTestCase):
    def test_backup_push_failure_propagates_out_of_run_once(self):
        """BUG-4: backup/runner.py re-raises GitOperationError by design, but
        app/runner.py does not absorb it, so the whole Runner aborts.

        Spec position: docs/08 section 19 calls a transient push failure
        BACKUP_PENDING and expects the next Runner to retry it — i.e. a
        recoverable condition, not a Runtime-ending one.
        """
        self._write_event(event_id="FAILPATH-BACKUP-001")
        _force_rmtree(self.bare_remote_dir)

        with self.assertRaises(GitOperationError):
            self._run()

    def test_history_and_daily_survive_a_backup_push_failure(self):
        """The important half of BUG-4: Backup runs last, so Local History is
        already durable when the push fails. Data safety holds even though the
        Runner aborts."""
        self._write_event(event_id="FAILPATH-BACKUP-002")
        _force_rmtree(self.bare_remote_dir)

        with self.assertRaises(GitOperationError):
            self._run()

        self.assertTrue((self.keep_dir / "HIST-FAILPATH-BACKUP-002.json").exists())
        self.assertTrue((self.local_master_dir / "daily" / "2026-08-01.md").exists())

    def test_backup_push_failure_is_recorded_as_pending(self):
        """docs/08 section 19: a transient push failure is BACKUP_PENDING."""
        self._write_event(event_id="FAILPATH-BACKUP-003")
        _force_rmtree(self.bare_remote_dir)

        with self.assertRaises(GitOperationError):
            self._run()

        state = json.loads(self.backup_state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["backup_status"], BackupStatus.PENDING.value)

    def test_lock_is_released_even_when_backup_raises(self):
        """run_once()'s finally: block must always release the Runner lock."""
        self._write_event(event_id="FAILPATH-BACKUP-004")
        _force_rmtree(self.bare_remote_dir)

        with self.assertRaises(GitOperationError):
            self._run()

        self.assertFalse(self.runner_lock_path.exists())

    def test_pending_backup_is_pushed_once_the_remote_recovers(self):
        """BUG-1 FIXED (CEO-approved A안).

        The commit is created locally before the push fails, so the working
        tree is clean on the next run. backup/runner.py used to stop at its
        `git status` check with BACKUP_NOT_REQUIRED and never push — and the
        BACKUP_PENDING marker was overwritten, destroying the only signal that
        a backup was outstanding.

        docs/08 section 19 / docs/10 section 29 require "다음 실행: Retry".
        Step 6 now checks the persisted state first: a PENDING backup retries
        the push before any NOT_REQUIRED decision is taken.
        """
        self._write_event(event_id="FAILPATH-BACKUP-005")
        saved_remote = self.root / "remote_saved"
        shutil.copytree(self.bare_remote_dir, saved_remote)
        _force_rmtree(self.bare_remote_dir)

        with self.assertRaises(GitOperationError):
            self._run()

        # The commit exists locally; only the push failed.
        self.assertEqual(self._unpushed_commit_count(), 1)
        state = json.loads(self.backup_state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["backup_status"], BackupStatus.PENDING.value)

        shutil.copytree(saved_remote, self.bare_remote_dir)
        result = self._run(now=datetime(2026, 8, 2, 13, 0).astimezone())

        self.assertIsNotNone(result)
        self.assertEqual(result[3].final_status, BackupStatus.SUCCESS)
        self.assertEqual(result[3].push_result, "SUCCESS (pending backup retried)")
        self.assertEqual(self._unpushed_commit_count(), 0)

        state = json.loads(self.backup_state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["backup_status"], BackupStatus.SUCCESS.value)
        self.assertIsNotNone(state["last_backup_commit"])
        self.assertIsNotNone(state["last_successful_backup"])

    def test_pending_retry_that_still_fails_stays_pending(self):
        """The retry must not mask a still-broken remote."""
        self._write_event(event_id="FAILPATH-BACKUP-006")
        _force_rmtree(self.bare_remote_dir)

        with self.assertRaises(GitOperationError):
            self._run()

        # Remote still gone: the second run retries, fails, and stays PENDING.
        with self.assertRaises(GitOperationError):
            self._run(now=datetime(2026, 8, 2, 13, 0).astimezone())

        state = json.loads(self.backup_state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["backup_status"], BackupStatus.PENDING.value)
        self.assertEqual(self._unpushed_commit_count(), 1)

    def test_a_clean_tree_with_no_pending_backup_is_still_not_required(self):
        """The fix must not turn every idle run into a push."""
        self._write_event(event_id="FAILPATH-BACKUP-007")
        first = self._run()
        self.assertEqual(first[3].final_status, BackupStatus.SUCCESS)

        second = self._run(now=datetime(2026, 8, 2, 13, 0).astimezone())
        self.assertEqual(second[3].final_status, BackupStatus.NOT_REQUIRED)
        self.assertIsNone(second[3].push_result)


class CorruptStateFilePathTests(RunnerFailurePathTestCase):
    """BUG-3 FIXED (CEO-approved A안: State Recovery 통일).

    Only collector/state.py used to raise a typed, documented error for a
    damaged state file; the other four loaders let a raw JSONDecodeError out.
    docs/10 section 46 treats a damaged state file as a normal operational
    situation to be *reported*, so every loader now names its own failure.

    A damaged file still aborts the run — that is A안's deliberate scope, and
    the loaders' callers are unchanged — but the abort now identifies which
    state file is broken instead of surfacing as an anonymous JSON error.
    The one exception is the Dashboard, where CEO Decision ④ requires the
    Runtime to survive; drain_pending() absorbs its typed error accordingly.
    """

    def test_each_state_loader_raises_its_own_named_error(self):
        """The unification itself: one contract, five loaders."""
        cases = (
            (self.collector_state_path, CollectorStateError),
            (self.scheduler_state_path, SchedulerStateError),
            (self.backup_state_path, BackupStateError),
        )
        for path, expected in cases:
            with self.subTest(state_file=path.name):
                # Fresh workspace state for each case.
                for other, _ in cases:
                    if other.exists():
                        other.unlink()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{not json", encoding="utf-8")

                with self.assertRaises(expected):
                    self._run()

                # docs/10 section 46: the damaged file is never deleted.
                self.assertEqual(path.read_text(encoding="utf-8"), "{not json")

    def test_corrupt_retry_queue_raises_the_notion_specific_error(self):
        """README RULE 5 puts Notion off the History critical path, so an
        operator must be able to tell a damaged Notion queue apart from a
        damaged History state immediately.

        Note the remaining scope: A안 unified the *loaders*, not their callers,
        so app/runner.py still lets this abort the run before History Filter.
        Making the Runner absorb it was 병목 #3 B안, which was not approved.
        """
        self.notion_retry_queue_path.parent.mkdir(parents=True, exist_ok=True)
        self.notion_retry_queue_path.write_text("{not json", encoding="utf-8")
        self._write_event(event_id="FAILPATH-QUEUE-001")

        sync = ExecutionPlanSync(
            client=NotionClient(transport=InMemoryNotionTransport(), database_id="DB-1")
        )

        with self.assertRaises(RetryQueueError):
            self._run(notion_sync=sync)

        self.assertEqual(
            self.notion_retry_queue_path.read_text(encoding="utf-8"), "{not json"
        )

    def test_corrupt_dashboard_pending_no_longer_stops_the_runtime(self):
        """CEO Decision ④: a Dashboard failure must never interrupt the
        Runtime, and drain_pending()'s contract is "Never raises". It now
        absorbs DashboardPendingError and treats the damaged file as
        "nothing to drain", leaving it on disk for an operator.
        """
        self.dashboard_pending_path.parent.mkdir(parents=True, exist_ok=True)
        self.dashboard_pending_path.write_text("<<<not json>>>", encoding="utf-8")
        self._write_event(event_id="FAILPATH-DASH-001")

        dashboard_client = NotionClient(
            transport=InMemoryNotionTransport(), database_id="ops-runs-db"
        )

        result = self._run(dashboard_client=dashboard_client, run_id="RUN-DASH-1")

        self.assertIsNotNone(result)
        self.assertTrue((self.keep_dir / "HIST-FAILPATH-DASH-001.json").exists())
        self.assertEqual(result[3].final_status, BackupStatus.SUCCESS)
        # The damaged file is preserved, not deleted or silently rewritten.
        self.assertEqual(
            self.dashboard_pending_path.read_text(encoding="utf-8"), "<<<not json>>>"
        )

    def test_lock_is_released_after_a_corrupt_state_abort(self):
        """Whatever else breaks, the lock must not be stranded."""
        self.scheduler_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.scheduler_state_path.write_text("{not json", encoding="utf-8")

        with self.assertRaises(SchedulerStateError):
            self._run()

        self.assertFalse(self.runner_lock_path.exists())


class CollectorOrderingFailurePathTests(RunnerFailurePathTestCase):
    """BUG-9 FIXED (CEO-approved B안).

    collector/runtime.py persists mark_seen() inside collector.collect(), i.e.
    BEFORE it attempts to move the file. When the move failed, the event_id was
    already burned, so the retry the module's own docstring promises ("the file
    stays in incoming/ so the next run retries it") could only ever produce a
    DUPLICATE — which app/runner.py steps 4b and 5 skip, losing the History
    Candidate permanently in violation of README RULE 7.

    The failure paths now roll the mark back via Collector.unmark_seen(), so a
    retried file is judged ACCEPTED again and flows on normally.
    """

    def test_a_failed_move_rolls_the_seen_mark_back(self):
        self._write_event(event_id="FAILPATH-ORDER-001")
        # Force the move to fail: the destination name is already taken.
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        (self.processed_dir / "FAILPATH-ORDER-001.json").write_text(
            '{"placeholder": true}', encoding="utf-8"
        )

        result = self._run()

        self.assertEqual(result[1].failed, 1)
        self.assertEqual(result[1].accepted, 0)
        # The file is still queued for a retry...
        self.assertTrue((self.incoming_dir / "FAILPATH-ORDER-001.json").exists())
        # ...and the id is NOT burned, so that retry can succeed.
        state = json.loads(self.collector_state_path.read_text(encoding="utf-8"))
        self.assertNotIn("FAILPATH-ORDER-001", state["processed_event_ids"])

    def test_a_move_that_fails_with_an_oserror_also_rolls_the_mark_back(self):
        """The *second* rollback call site, which nothing reached.

        There are two ways the move can fail: the destination already exists
        (covered above), and `os.replace` itself raising — on Windows that is
        WinError 5, a destination held open by another process, which is
        precisely what concurrent runs produce. Both call `unmark_seen()`, but
        only the first had a test; a coverage run showed the OSError branch
        executed by nothing in-process.

        Without the rollback here, a transient sharing violation would burn the
        event_id permanently and the retry would come back DUPLICATE.
        """
        self._write_event(event_id="FAILPATH-ORDER-004")
        real_replace = os.replace

        def failing_replace(src, dst):
            if Path(src).parent == self.incoming_dir:
                raise OSError(5, "simulated sharing violation")
            return real_replace(src, dst)

        os.replace = failing_replace
        self.addCleanup(setattr, os, "replace", real_replace)

        result = self._run()

        self.assertEqual(result[1].failed, 1)
        self.assertTrue((self.incoming_dir / "FAILPATH-ORDER-004.json").exists())
        state = json.loads(self.collector_state_path.read_text(encoding="utf-8"))
        self.assertNotIn("FAILPATH-ORDER-004", state["processed_event_ids"])

        # And the retry, once the transient failure clears, really is accepted.
        os.replace = real_replace
        retry = self._run(now=datetime(2026, 8, 2, 13, 0).astimezone())
        self.assertEqual(retry[1].accepted, 1)
        self.assertTrue((self.keep_dir / "HIST-FAILPATH-ORDER-004.json").exists())

    def test_the_retry_is_accepted_and_reaches_history(self):
        self._write_event(event_id="FAILPATH-ORDER-002")
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        (self.processed_dir / "FAILPATH-ORDER-002.json").write_text(
            '{"placeholder": true}', encoding="utf-8"
        )
        self._run()

        # Clear the collision so the retry can succeed.
        (self.processed_dir / "FAILPATH-ORDER-002.json").unlink()
        result = self._run(now=datetime(2026, 8, 2, 13, 0).astimezone())

        self.assertEqual(result[1].accepted, 1)
        self.assertEqual(result[1].duplicate, 0)
        # ACCEPTED -> History Filter runs -> the Candidate is preserved.
        self.assertTrue((self.keep_dir / "HIST-FAILPATH-ORDER-002.json").exists())

    def test_a_genuine_duplicate_is_still_detected(self):
        """The rollback must not weaken duplicate protection for Events that
        really were consumed (docs/03 sections 36-38)."""
        self._write_event(event_id="FAILPATH-ORDER-003")
        first = self._run()
        self.assertEqual(first[1].accepted, 1)

        # Same event_id delivered again under a different file name.
        resent = self.incoming_dir / "resent.json"
        resent.write_text(
            (self.processed_dir / "FAILPATH-ORDER-003.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        second = self._run(now=datetime(2026, 8, 2, 13, 0).astimezone())

        self.assertEqual(second[1].duplicate, 1)
        self.assertEqual(second[1].accepted, 0)

    def test_rollback_only_applies_to_accepted_events(self):
        """A REJECTED event was never marked seen, so nothing is rolled back
        and the rejected file still moves to rejected/."""
        self.incoming_dir.mkdir(parents=True, exist_ok=True)
        (self.incoming_dir / "invalid.json").write_text(
            json.dumps({"schema_version": "1.0", "event_id": "X"}), encoding="utf-8"
        )

        result = self._run()

        self.assertEqual(result[1].rejected, 1)
        self.assertTrue((self.rejected_dir / "invalid.json").exists())


class CrashBetweenCollectAndHistoryTests(RunnerFailurePathTestCase):
    """BUG-25 (P0, NOT FIXED — the fix needs a decision that has not been made).

    CHARACTERIZATION: this test asserts what the Runner does TODAY, including
    the loss. It will fail — deliberately — the moment the gap is closed, and
    at that point it should be rewritten as the guarantee.

    The Runner consumes an Event in step 3 (move to processed/ + mark_seen)
    but does not write its History Candidate until step 5. If the process dies
    in between, the Event is gone from incoming/ and its id is burned, while no
    candidate exists. Nothing rescans processed/, so the next run has nothing
    to retry and the History Candidate is lost permanently.

    Measured with a real Runner killed mid-run (60 Events, kill swept in 20 ms
    steps): the number lost equals exactly the number already moved to
    processed/ at the moment of death.

        kill at 120 ms ->  0 moved,  0 lost
        kill at 160 ms -> 17 moved, 17 lost
        kill at 240 ms -> 60 moved, 60 lost   (100%)
        kill at 300 ms -> 60 moved,  0 lost   (step 5 had finished)

    This is the same outcome as BUG-20 but reached by a crash rather than by
    concurrency, so the Lock 원자성 fix does not help: a single Runner holding
    the lock correctly still loses the data if it dies in the window. Ordinary
    causes — power loss, OOM kill, a Windows update reboot, Ctrl+C, a
    scheduler timeout — all land in it.

    Violates README RULE 7 ("Event와 History가 영구 손실되어서는 안 된다") and
    docs/10 section 52 (Critical Data).

    The crash is simulated by making the History Filter raise, which leaves the
    process in exactly the state a kill leaves it in, without depending on
    timing (a sleep-and-kill test would flake).
    """

    def test_a_crash_after_collection_loses_the_history_candidate(self):
        for i in range(3):
            self._write_event(event_id=f"CRASHGAP-{i:03d}")

        original = runner_module.HistoryFilter

        class ExplodingHistoryFilter:
            def __init__(self, *args, **kwargs):
                raise RuntimeError("simulated crash before the History Filter ran")

        runner_module.HistoryFilter = ExplodingHistoryFilter
        self.addCleanup(setattr, runner_module, "HistoryFilter", original)

        with self.assertRaises(RuntimeError):
            self._run()

        # The Events were consumed: moved out of incoming/ and marked seen.
        self.assertEqual(len(list(self.processed_dir.glob("*.json"))), 3)
        self.assertEqual(list(self.incoming_dir.glob("*.json")), [])
        state = json.loads(self.collector_state_path.read_text(encoding="utf-8"))
        self.assertEqual(len(state["processed_event_ids"]), 3)

        # But no History Candidate exists for any of them.
        self.assertFalse(self.keep_dir.exists() and list(self.keep_dir.glob("*.json")))

    def test_the_next_run_does_not_recover_them(self):
        """The part that makes it permanent rather than merely delayed."""
        for i in range(3):
            self._write_event(event_id=f"CRASHGAP-1{i:02d}")

        original = runner_module.HistoryFilter

        class ExplodingHistoryFilter:
            def __init__(self, *args, **kwargs):
                raise RuntimeError("simulated crash")

        runner_module.HistoryFilter = ExplodingHistoryFilter
        with self.assertRaises(RuntimeError):
            self._run()
        runner_module.HistoryFilter = original

        # A perfectly healthy run afterwards.
        result = self._run(now=datetime(2026, 8, 2, 13, 0).astimezone())

        self.assertEqual(result[1].accepted, 0, "nothing left to collect")
        self.assertFalse(
            self.keep_dir.exists() and list(self.keep_dir.glob("*.json")),
            "BUG-25 fixed? Rewrite this test as the guarantee.",
        )
        # The raw Events do survive — recovery is possible, just not implemented.
        self.assertEqual(len(list(self.processed_dir.glob("*.json"))), 3)


class CollectorBatchResilienceTests(unittest.TestCase):
    """docs/03_COLLECTOR_SPEC.md section 53: one bad Event must never stop the
    rest of the batch.

    collector/runtime.py implements that with two guards — an unreadable file
    and a `collect()` that raises are each logged as FAILED and skipped. The
    spec states the guarantee and the code has the handlers, but a coverage run
    showed neither handler executed by any test, so the guarantee itself was
    unverified. If either regressed, a single unreadable file would abort the
    whole collection run and every Event behind it would sit in incoming/
    untouched — with nothing in the summary to say why.

    Uses collector.runtime.run_once directly: the failure has to be injected
    into the collector, which the full Runner constructs itself.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.incoming = self.root / "incoming"
        self.processed = self.root / "processed"
        self.rejected = self.root / "rejected"
        self.log_path = self.root / "logs" / "collector.log"
        self.reporter = Reporter(profile="DESKTOP_3")

    def _write_event(self, event_id):
        self.incoming.mkdir(parents=True, exist_ok=True)
        _, path = self.reporter.report_and_write(
            directory=self.incoming,
            event_id=event_id,
            project_id="SEARCH_FRONTEND",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            summary="batch resilience probe",
            milestone="M1",
            evidence=[],
            history_candidate=True,
            timestamp="2026-08-01T10:00:00+09:00",
        )
        return path

    def _run(self, collector):
        return collector_run_once(
            collector=collector,
            incoming_dir=self.incoming,
            processed_dir=self.processed,
            rejected_dir=self.rejected,
            log_path=self.log_path,
        )

    def test_an_unreadable_file_does_not_stop_the_rest_of_the_batch(self):
        self._write_event("BATCH-A")
        self._write_event("BATCH-C")
        # Sorts between the two, and read_text() on a directory raises OSError.
        (self.incoming / "BATCH-B.json").mkdir(parents=True, exist_ok=True)

        summary = self._run(Collector(seen_store=InMemorySeenEventStore()))

        self.assertEqual(summary.accepted, 2)
        self.assertEqual(summary.failed, 1)
        accepted = sorted(
            p.source_path.name
            for p in summary.files
            if p.outcome is RuntimeOutcome.ACCEPTED
        )
        self.assertEqual(accepted, ["BATCH-A.json", "BATCH-C.json"])
        self.assertIn("could not read file", self.log_path.read_text(encoding="utf-8"))

    def test_a_collector_that_raises_does_not_stop_the_rest_of_the_batch(self):
        self._write_event("BATCH-D")
        self._write_event("BATCH-E")
        self._write_event("BATCH-F")

        class ExplodingCollector(Collector):
            def collect(self, raw):
                if "BATCH-E" in raw:
                    raise RuntimeError("simulated collector defect")
                return super().collect(raw)

        summary = self._run(ExplodingCollector(seen_store=InMemorySeenEventStore()))

        self.assertEqual(summary.accepted, 2)
        self.assertEqual(summary.failed, 1)
        # The failed one stays in incoming/ for the next run, as the module says.
        self.assertTrue((self.incoming / "BATCH-E.json").exists())
        self.assertFalse((self.incoming / "BATCH-D.json").exists())
        self.assertIn("simulated collector defect", self.log_path.read_text(encoding="utf-8"))

    def test_an_unwritable_log_never_breaks_collection(self):
        """`_log()` swallows OSError on purpose — logging is not the job."""
        self._write_event("BATCH-G")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.mkdir()  # open(..., "a") on a directory raises

        summary = self._run(Collector(seen_store=InMemorySeenEventStore()))

        self.assertEqual(summary.accepted, 1)


class HistoryRepositoryFailurePathTests(RunnerFailurePathTestCase):
    """BUG-10: FileHistoryRepository.save() defaults to overwrite=False and
    raises FileExistsError. app/runner.py step 5 does not catch it.

    Reachable through docs/10 section 45 (Desktop 4 recovery): collector state
    is lost, the same event is re-sent under a different filename, so neither
    the intake filename check nor the collector event_id check stops it.
    """

    def test_resend_after_state_loss_aborts_the_runner(self):
        self.reporter.report_and_write(
            directory=self.incoming_dir,
            project_id="SEARCH_FRONTEND",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            summary="first delivery",
            milestone="M1",
            evidence=[],
            history_candidate=True,
            timestamp="2026-08-01T10:00:00+09:00",
            event_id="FAILPATH-RESEND-001",
        )
        self._run()
        self.assertTrue((self.keep_dir / "HIST-FAILPATH-RESEND-001.json").exists())

        # Desktop 4 recovery: collector state is gone.
        self.collector_state_path.unlink()
        # The same event arrives again under a different file name.
        resent = self.incoming_dir / "resent-under-a-new-name.json"
        resent.write_text(
            (self.processed_dir / "FAILPATH-RESEND-001.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        with self.assertRaises(FileExistsError):
            self._run(now=datetime(2026, 8, 2, 13, 0).astimezone())

    def test_a_late_arriving_event_is_added_to_the_already_written_daily(self):
        """BUG-17 (P0), FIXED. This asserted the loss; now it asserts the fix.

        An Event whose date was already closed used to be accepted, stored in
        keep/, and synced to Notion — while `scheduler.run_once()` skipped any
        date whose .md existed and `generate_daily_history()` refused to
        overwrite. The Event never reached Company History and every other
        indicator reported success. With four Desktops the trigger (one
        machine offline across a Daily Close) is routine, not exceptional.

        docs/06 §36-40 already specified the remedy, and docs/08 §65's
        "backup: history late update" commit template existed for exactly
        this case; runner.py step 6.5 now implements it.
        """
        self._write_event(event_id="FAILPATH-LATE-001", summary="on-time work")
        self._run(now=datetime(2026, 8, 5, 12, 0).astimezone())

        daily_file = self.local_master_dir / "daily" / "2026-08-01.md"
        before = daily_file.read_text(encoding="utf-8")
        self.assertIn("on-time work", before)

        self._write_event(
            event_id="FAILPATH-LATE-002",
            summary="critical late-arriving decision",
            timestamp="2026-08-01T18:00:00+09:00",
        )
        result = self._run(now=datetime(2026, 8, 10, 12, 0).astimezone())

        self.assertEqual(result[1].accepted, 1)
        self.assertTrue((self.keep_dir / "HIST-FAILPATH-LATE-002.json").exists())

        after = daily_file.read_text(encoding="utf-8")
        # The late Event is now in Company History...
        self.assertIn("critical late-arriving decision", after)
        self.assertIn("FAILPATH-LATE-002", after)
        # ...the original content survived verbatim (docs/06 §57)...
        self.assertIn("on-time work", after)
        self.assertIn("- Generated At: ", after)
        # ...and the change is recorded rather than silent (docs/06 §39-40).
        self.assertIn("- Last Updated At: ", after)
        self.assertIn("- Late Events Added: 1", after)

    def test_a_late_update_is_not_repeated_on_the_next_run(self):
        """docs/06 §38: an event_id already present is not added again."""
        self._write_event(event_id="FAILPATH-LATE-011", summary="on-time work")
        self._run(now=datetime(2026, 8, 5, 12, 0).astimezone())

        self._write_event(
            event_id="FAILPATH-LATE-012",
            summary="the late one",
            timestamp="2026-08-01T18:00:00+09:00",
        )
        self._run(now=datetime(2026, 8, 10, 12, 0).astimezone())

        daily_file = self.local_master_dir / "daily" / "2026-08-01.md"
        after_first = daily_file.read_text(encoding="utf-8")

        # A later run that collects nothing new for this date must not touch
        # the file at all — not even to refresh `Last Updated At`.
        self._run(now=datetime(2026, 8, 11, 12, 0).astimezone())

        self.assertEqual(daily_file.read_text(encoding="utf-8"), after_first)
        self.assertEqual(after_first.count("FAILPATH-LATE-012"), 1)
        self.assertIn("- Late Events Added: 1", after_first)


class NotionPermanentFailurePathTests(RunnerFailurePathTestCase):
    """BUG-13 (P0): ExecutionPlanSync maps every NotionAPIError to
    NOTION_RETRY_REQUIRED, so an HTTP 400 (a payload the API will never
    accept) is queued and retried on every subsequent run. attempt_count is
    incremented but never read, and nothing caps or drains it.

    docs/08 section 62 forbids this loop shape on the Backup side, and
    backup/git_ops.is_authentication_failure() implements that distinction —
    the Notion path has no equivalent.
    """

    def _oversized_sync(self):
        transport = StrictNotionTransport()
        return ExecutionPlanSync(
            client=NotionClient(transport=transport, database_id="DB-1")
        )

    def test_permanent_notion_rejection_is_queued_for_retry(self):
        self._write_event(event_id="FAILPATH-NOTION-001", milestone="M" * 2500)
        sync = self._oversized_sync()

        self._run(notion_sync=sync)

        queue = json.loads(self.notion_retry_queue_path.read_text(encoding="utf-8"))
        self.assertEqual(len(queue["entries"]), 1)
        self.assertEqual(queue["entries"][0]["event_id"], "FAILPATH-NOTION-001")

    def test_attempt_count_grows_without_bound(self):
        self._write_event(event_id="FAILPATH-NOTION-002", milestone="M" * 2500)
        sync = self._oversized_sync()

        counts = []
        for hour in (12, 13, 14):
            self._run(now=datetime(2026, 8, 2, hour, 0).astimezone(), notion_sync=sync)
            queue = json.loads(self.notion_retry_queue_path.read_text(encoding="utf-8"))
            counts.append(queue["entries"][0]["attempt_count"])

        self.assertEqual(counts, [1, 2, 3])

    def test_history_and_backup_still_complete_despite_the_notion_failure(self):
        """README RULE 5 holds here: the Notion failure itself is contained."""
        self._write_event(event_id="FAILPATH-NOTION-003", milestone="M" * 2500)
        sync = self._oversized_sync()

        result = self._run(notion_sync=sync)

        self.assertIsNotNone(result)
        self.assertTrue((self.keep_dir / "HIST-FAILPATH-NOTION-003.json").exists())
        self.assertEqual(result[3].final_status, BackupStatus.SUCCESS)


class RetryQueueBatchSaveDurabilityTests(RunnerFailurePathTestCase):
    """BUG-24: Batch Save (CEO 승인 B안) must not cost durability.

    Batch Save replaced a per-Event write of the retry queue with one write at
    the end of the Notion step, to stop a 200-Event outage rewriting the queue
    200 times. But an exception anywhere inside that step then skipped the one
    write, so the entire delta vanished — measured at 6 of 6 Events lost, where
    per-Event saving would have kept the ones already processed. Those Events
    are marked collected before Notion runs, so they are never re-collected:
    dropping them from the queue means they never reach Notion at all.

    The write now happens in a `finally`, restoring exactly the pre-Batch-Save
    guarantee: everything processed before the exception stays queued.
    """

    def _failing_sync(self):
        class AlwaysFailSync:
            def sync(self, event):
                return SyncResult(
                    status=SyncStatus.NOTION_FAILED,
                    event_id=event.event_id,
                    project_id=event.project_id,
                    error="simulated Notion outage",
                )

        return AlwaysFailSync()

    def _queued_ids(self):
        if not self.notion_retry_queue_path.exists():
            return []
        queue = json.loads(self.notion_retry_queue_path.read_text(encoding="utf-8"))
        return sorted(entry["event_id"] for entry in queue["entries"])

    def _run_with_failure_at(self, nth):
        """Raise on the nth Event parsed inside the Notion step."""
        for i in range(4):
            self._write_event(event_id=f"BATCHSAVE-{i:03d}")

        original = Event.from_json
        calls = {"n": 0}

        def counting_from_json(raw):
            calls["n"] += 1
            if calls["n"] == nth:
                raise ValueError("injected failure inside the Notion step")
            return original(raw)

        Event.from_json = staticmethod(counting_from_json)
        self.addCleanup(lambda: setattr(Event, "from_json", original))
        with self.assertRaises(ValueError):
            self._run(notion_sync=self._failing_sync())

    def test_a_failure_midway_keeps_everything_already_processed(self):
        self._run_with_failure_at(3)

        self.assertEqual(self._queued_ids(), ["BATCHSAVE-000", "BATCHSAVE-001"])

    def test_a_failure_on_the_first_event_still_writes_no_bogus_queue(self):
        """Nothing was processed yet, so there is no delta to keep — and the
        queue must not be left holding an entry that was never synced."""
        self._run_with_failure_at(1)

        self.assertEqual(self._queued_ids(), [])

    def test_the_normal_path_still_writes_the_queue_exactly_once(self):
        """The point of B안: one write, not one per Event."""
        for i in range(4):
            self._write_event(event_id=f"BATCHOK-{i:03d}")

        original = save_retry_queue
        calls = []

        def counting_save(path, entries):
            calls.append(len(entries))
            return original(path, entries)

        runner_module.save_retry_queue = counting_save
        self.addCleanup(lambda: setattr(runner_module, "save_retry_queue", original))
        self._run(notion_sync=self._failing_sync())

        self.assertEqual(calls, [4])
        self.assertEqual(len(self._queued_ids()), 4)

    def test_a_save_failure_with_no_other_error_is_not_silently_swallowed(self):
        """Found via `python -m trace --count`: this except clause's `if
        sys.exc_info()[0] is None: raise` could never be true — inside its
        own except block, sys.exc_info() always refers to the exception that
        block just caught (the save failure itself), never None. So a
        save_retry_queue() failure was silently swallowed unconditionally,
        contradicting the comment's own stated intent ("정상 경로에서는 여기서
        삼킬 것이 없다") and losing every Retry Queue delta computed this run
        with no signal at all. Fixed via `save_exc.__context__` (set by
        Python's exception chaining to whatever was already propagating when
        save_retry_queue() raised — None here, since the try block above
        succeeded)."""
        for i in range(2):
            self._write_event(event_id=f"BATCHSAVEFAIL-{i:03d}")

        def failing_save(path, entries):
            raise OSError("simulated disk failure during save_retry_queue")

        runner_module.save_retry_queue = failing_save
        self.addCleanup(lambda: setattr(runner_module, "save_retry_queue", save_retry_queue))

        with self.assertRaises(OSError):
            self._run(notion_sync=self._failing_sync())

    def test_a_save_failure_during_an_active_exception_does_not_mask_it(self):
        """The other half of the same fix: when the try block already raised,
        save_retry_queue() ALSO failing in the finally must not replace the
        original exception with the save's."""
        self._run_with_failure_at_and_failing_save(nth=3)

    def _run_with_failure_at_and_failing_save(self, nth):
        for i in range(4):
            self._write_event(event_id=f"BATCHSAVEFAIL2-{i:03d}")

        original = Event.from_json
        calls = {"n": 0}

        def counting_from_json(raw):
            calls["n"] += 1
            if calls["n"] == nth:
                raise ValueError("ORIGINAL injected failure inside the Notion step")
            return original(raw)

        Event.from_json = staticmethod(counting_from_json)
        self.addCleanup(lambda: setattr(Event, "from_json", original))

        def failing_save(path, entries):
            raise OSError("save ALSO fails while the ORIGINAL exception is propagating")

        runner_module.save_retry_queue = failing_save
        self.addCleanup(lambda: setattr(runner_module, "save_retry_queue", save_retry_queue))

        with self.assertRaises(ValueError):
            self._run(notion_sync=self._failing_sync())


class NotionLastUpdatedParsingTests(unittest.TestCase):
    """BUG-29 (NOT FIXED — the fix is a decision about what to trust).

    CHARACTERIZATION: asserts today's behaviour, including the crash.

    docs/04 sections 29-30's Late Event guard compares the incoming Event's
    timestamp against the page's current `Last Updated`:

        datetime.fromisoformat(event.timestamp)
            <= datetime.fromisoformat(current_last_updated)

    `event.timestamp` is always offset-aware — docs/02 requires an offset. The
    Notion value is whatever is in the page, and the Projects database is meant
    to be looked at and edited by people. Notion's date picker writes a
    date-only value when no time is chosen, so `Last Updated` can perfectly
    ordinarily come back as "2026-08-05". That parses to a NAIVE datetime, and
    comparing naive with aware raises TypeError. An empty string raises
    ValueError from fromisoformat itself.

    Neither is caught: `sync()` only handles NotionAPIError. The exception
    escapes into app/runner.py, whose broad `except Exception` turns it into
    NOTION_FAILED and queues the Event. The Notion page still holds the same
    unparseable value, so the next run raises again — the Event is queued
    forever with attempt_count climbing and nothing capping it, which is the
    BUG-13/BUG-14 loop reached through a human's edit rather than an
    oversized payload.

    One human setting a date in the Notion UI therefore wedges that project's
    sync permanently, and the only visible symptom is a retry queue entry.

    The §62 duplicate guard runs FIRST and returns before the comparison, so a
    re-arriving Event with a matching Last Event ID is unaffected — the damage
    is confined to genuinely new Events for that project. That asymmetry is
    asserted below so a fix cannot quietly change it.
    """

    def _event(self, event_id="LATE-1", timestamp="2026-08-06T10:00:00+09:00"):
        return create_event(
            source="DESKTOP_1",
            role="COO",
            project_id="SEARCH_FRONTEND",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            summary="late event guard probe",
            milestone="M1",
            history_candidate=True,
            event_id=event_id,
            timestamp=timestamp,
        )

    def _sync_against(self, last_updated, last_event_id="OTHER-1"):
        page = {
            "id": "pg-1",
            "properties": {
                "Last Updated": (
                    {"date": {"start": last_updated}}
                    if last_updated is not None
                    else {"date": None}
                ),
                "Last Event ID": {"rich_text": [{"text": {"content": last_event_id}}]},
                "Project": {"title": [{"text": {"content": "SEARCH_FRONTEND"}}]},
            },
        }

        class PageTransport(InMemoryNotionTransport):
            def query_database(self, database_id, payload=None, **kwargs):
                return {"results": [page]}

        return ExecutionPlanSync(
            client=NotionClient(transport=PageTransport(), database_id="DB-1")
        )

    def test_an_offset_aware_last_updated_is_handled_normally(self):
        """The working path, so the failures below are specific."""
        result = self._sync_against("2026-08-05T10:00:00+09:00").sync(self._event())
        self.assertIsNotNone(result.status)

    def test_a_missing_last_updated_is_handled_normally(self):
        result = self._sync_against(None).sync(self._event())
        self.assertIsNotNone(result.status)

    def test_a_date_only_last_updated_raises_out_of_sync(self):
        """What Notion's date picker writes when no time is chosen."""
        with self.assertRaises(TypeError):
            self._sync_against("2026-08-05").sync(self._event())

    def test_an_offset_less_timestamp_raises_out_of_sync(self):
        with self.assertRaises(TypeError):
            self._sync_against("2026-08-05T10:00:00").sync(self._event())

    def test_an_empty_last_updated_raises_out_of_sync(self):
        with self.assertRaises(ValueError):
            self._sync_against("").sync(self._event())

    def test_the_duplicate_guard_short_circuits_before_the_comparison(self):
        """§62 runs first, so a re-arriving Event survives a broken page."""
        result = self._sync_against("2026-08-05", last_event_id="LATE-1").sync(
            self._event("LATE-1")
        )
        self.assertIs(result.status, SyncStatus.NOTION_SKIPPED_OLD_EVENT)

    def test_sync_only_guards_against_notion_api_errors(self):
        """The structural cause: nothing else is caught around the compare."""
        source = inspect.getsource(ExecutionPlanSync._update)
        self.assertIn("except NotionAPIError", source)
        self.assertNotIn("except (TypeError", source)
        self.assertNotIn("except ValueError", source)


class IntakeClockSkewTests(unittest.TestCase):
    """BUG-30 (NOT FIXED — the fix is a decision about trusting mtime).

    CHARACTERIZATION: asserts today's behaviour, including the stall.

    `transport/intake._is_stable()` decides a file has finished arriving with

        (now - mtime) >= stable_after_seconds

    which assumes mtime is in the past. The Event file is written on Desktop 3
    and carried to Desktop 4 by the OneDrive client, which preserves the
    source machine's mtime. If Desktop 3's clock is ahead of Desktop 4's —
    ordinary drift, a wrong timezone, a manually set clock, a resumed VM — the
    file arrives stamped in the future and the subtraction goes negative.

    The file is then never intaken until wall-clock time catches up:

        mtime 1 hour ahead   -> stalled ~1 hour
        mtime 1 day ahead    -> stalled ~1 day
        mtime 1 year ahead   -> stalled ~1 year

    Nothing is lost — the file stays in transport/ and eventually arrives —
    but the delay is unbounded. It used to be invisible as well:
    run_company_ops.py prints only
    `Transport: moved={len(intake_summary.moved)}`, so a stalled Event reads
    as "moved=0", exactly like an idle run, and while `skipped_not_stable`
    does reach the Run Manifest, `_print_last_run()` prints only components
    that are NOT SUCCESS — and transport succeeds.

    C23 closed the visibility half only: `IntakeBacklog.future_dated` counts
    files whose mtime is ahead of this clock and `ops_status.py` says so in
    the same sentence that reports the backlog
    (`test_observability.py::FutureDatedTransportFileTests`). The stall
    itself is untouched, and the count is deliberately NOT subtracted from
    `awaiting_intake` — whether such a file is "in flight" is exactly the
    judgement below.

    Not fixed: clamping a future mtime, or treating it as stable immediately,
    or reading the clock differently are all judgement calls about how much to
    trust a filesystem timestamp from another machine.
    """

    STABLE_AFTER = 5.0

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.transport_dir = self.root / "transport"
        self.incoming_dir = self.root / "incoming"

    def _stage(self, event_id, mtime_offset):
        self.transport_dir.mkdir(parents=True, exist_ok=True)
        event = create_event(
            source="DESKTOP_3",
            role="COO",
            project_id="SEARCH_FRONTEND",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            summary="clock skew probe",
            milestone="M1",
            history_candidate=True,
            event_id=event_id,
            timestamp="2026-08-05T10:00:00+09:00",
        )
        path = self.transport_dir / f"{event_id}.json"
        path.write_text(event.to_json(), encoding="utf-8")
        stamp = time.time() + mtime_offset
        os.utime(path, (stamp, stamp))
        return path

    def _intake(self):
        from transport import run_intake

        return run_intake(
            transport_dir=self.transport_dir,
            incoming_dir=self.incoming_dir,
            processed_dir=self.root / "processed",
            rejected_dir=self.root / "rejected",
            stable_after_seconds=self.STABLE_AFTER,
        )

    def test_a_past_mtime_is_intaken_normally(self):
        """The working path, so the stalls below are specific."""
        self._stage("SKEW-PAST", -3600)
        summary = self._intake()
        self.assertEqual(len(summary.moved), 1)

    def test_a_recent_file_is_correctly_held_until_stable(self):
        """The feature working as intended — not the bug."""
        self._stage("SKEW-RECENT", -1)
        summary = self._intake()
        self.assertEqual(len(summary.moved), 0)
        self.assertEqual(len(summary.skipped_not_stable), 1)

    def test_a_future_mtime_stalls_the_event_indefinitely(self):
        for label, offset in (("1분", 60), ("1시간", 3600), ("1일", 86400), ("1년", 31_536_000)):
            with self.subTest(skew=label):
                self.setUp()
                self._stage("SKEW-FUTURE", offset)
                summary = self._intake()

                self.assertEqual(len(summary.moved), 0)
                self.assertEqual(len(summary.skipped_not_stable), 1)
                # Not lost — still queued, which is why this is a stall.
                self.assertTrue((self.transport_dir / "SKEW-FUTURE.json").exists())

    def test_the_stall_is_invisible_to_the_operator(self):
        """The half that makes it dangerous rather than merely slow."""
        entrypoint = (
            Path(__file__).resolve().parents[1] / "run_company_ops.py"
        ).read_text(encoding="utf-8")

        self.assertIn("intake_summary.moved", entrypoint)
        self.assertNotIn("skipped_not_stable", entrypoint)


class BootstrapTypeBlindnessTests(unittest.TestCase):
    """BUG-31 (NOT FIXED — detecting a mismatch needs a decided response).

    CHARACTERIZATION: asserts today's behaviour, including the blind spot.

    `notion/bootstrap.diff_properties()` decides whether a Property needs
    creating with a single membership test:

        if name in current_properties: -> EXISTS

    The property's TYPE is never looked at. A database where `Status` exists
    as a checkbox, a number, or Notion's native `status` type is reported
    EXISTS and bootstrap creates nothing — it reports success.

    `PropertyOutcome` has no value that could even express the problem:
    EXISTS / CREATED / SKIPPED / RENAMED / FAILED.

    Then `build_create_properties()` sends `{"select": {"name": ...}}` into a
    property that is not a select, Notion returns 400, and
    ExecutionPlanSync maps every NotionAPIError to NOTION_RETRY_REQUIRED — so
    the project is retried forever with no cap, exactly like BUG-13, BUG-14
    and BUG-29.

    The likely trigger is not exotic. Notion's own UI creates a field named
    "Status" as its native `status` type by default, while this code expects
    `select`. Anyone setting the database up by hand rather than by running
    bootstrap lands on the mismatch, and bootstrap then confirms the database
    is fine.

    Not fixed: reporting a mismatch is easy, deciding what to do about it is
    not — changing a live property's type in Notion is destructive to the data
    already in it, so the response has to be a policy (fail the run, warn and
    continue, or refuse to sync that project).
    """

    def _current(self, **overrides):
        from notion.bootstrap import TARGET_PROPERTIES, TITLE_PROPERTY_NAME

        current = {
            name: {"type": next(iter(payload))}
            for name, payload in TARGET_PROPERTIES.items()
            if name != TITLE_PROPERTY_NAME
        }
        current.update(overrides)
        return current

    def test_a_correctly_typed_database_reports_exists(self):
        """The baseline, so the blindness below is not vacuous."""
        from notion.bootstrap import diff_properties, PropertyOutcome

        to_create, decided = diff_properties(self._current())

        self.assertEqual(to_create, {})
        self.assertTrue(all(r.outcome is PropertyOutcome.EXISTS for r in decided))

    def test_a_missing_property_is_still_detected(self):
        """Name-based detection does work for what it was built for."""
        from notion.bootstrap import diff_properties

        current = self._current()
        del current["Status"]

        to_create, _ = diff_properties(current)

        self.assertIn("Status", to_create)

    def test_every_wrong_type_is_reported_as_exists(self):
        from notion.bootstrap import diff_properties, PropertyOutcome

        for wrong_type in ("status", "checkbox", "number", "rich_text", "people"):
            with self.subTest(actual_type=wrong_type):
                to_create, decided = diff_properties(
                    self._current(Status={"type": wrong_type})
                )

                self.assertNotIn("Status", to_create)
                status_report = next(r for r in decided if r.name == "Status")
                self.assertIs(status_report.outcome, PropertyOutcome.EXISTS)

    def test_no_outcome_value_can_express_a_type_mismatch(self):
        """The structural reason it cannot currently be reported."""
        from notion.bootstrap import PropertyOutcome

        names = {outcome.name for outcome in PropertyOutcome}
        self.assertEqual(names, {"EXISTS", "CREATED", "SKIPPED", "RENAMED", "FAILED"})

    def test_sync_would_send_a_select_payload_regardless(self):
        """Why the mismatch turns into a permanent 400 rather than a no-op."""
        from notion.properties import build_create_properties

        event = create_event(
            source="DESKTOP_1",
            role="COO",
            project_id="SEARCH_FRONTEND",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            summary="bootstrap mismatch probe",
            milestone="M1",
            history_candidate=True,
            event_id="BOOTSTRAP-1",
            timestamp="2026-08-05T10:00:00+09:00",
        )

        properties = build_create_properties(event, project_name="Search Frontend")

        self.assertEqual(properties["Status"], {"select": {"name": "IN_PROGRESS"}})


class NotionRateLimitTests(unittest.TestCase):
    """BUG-32 (NOT FIXED — pacing policy is a decision).

    CHARACTERIZATION: asserts today's behaviour.

    Notion documents an average rate limit of roughly 3 requests per second
    and answers 429 above it. This codebase sends requests back to back with
    no pacing of any kind: `notion/transport.py`, `notion/client.py`,
    `notion/sync.py` and `app/runner.py` contain no sleep, no throttle, and no
    Retry-After handling. `RealNotionTransport` sets a 10s timeout and has no
    retry loop.

    Each Event costs TWO requests — one query_database to find the page, then
    one create_page or update_page. Measured: 50 Events -> 100 requests, which
    at Notion's documented rate needs at least ~33 seconds and is instead
    issued as fast as the network allows.

    A 429 becomes NotionAPIError -> NOTION_RETRY_REQUIRED -> queued. The next
    run replays the whole queue at the same speed and gets 429 again, and
    nothing caps the attempts (BUG-13/BUG-14). The queue therefore grows
    fastest exactly when the API is pushing back hardest.

    This matters most on the first real connection: an initial sync of a
    backlog of Events is precisely the burst shape that triggers it.

    Not fixed: how to pace (fixed delay, token bucket, honouring Retry-After)
    and whether to retry inside the request or only via the queue are design
    decisions, and any sleep inside run_once changes the Runner's timing
    contract.
    """

    class CountingTransport(InMemoryNotionTransport):
        def __init__(self):
            super().__init__()
            self.calls = []

        def query_database(self, *args, **kwargs):
            self.calls.append("query")
            return super().query_database(*args, **kwargs)

        def create_page(self, *args, **kwargs):
            self.calls.append("create")
            return super().create_page(*args, **kwargs)

        def update_page(self, *args, **kwargs):
            self.calls.append("update")
            return super().update_page(*args, **kwargs)

    def test_no_module_in_the_notion_path_paces_its_requests(self):
        import app.runner
        import notion.client
        import notion.sync
        import notion.transport

        for module in (notion.transport, notion.client, notion.sync, app.runner):
            with self.subTest(module=module.__name__):
                source = inspect.getsource(module)
                self.assertNotIn("time.sleep", source)
                self.assertNotIn("Retry-After", source)

    def test_the_real_transport_has_a_timeout_but_no_retry(self):
        source = inspect.getsource(sys.modules["notion.transport"].RealNotionTransport)
        self.assertIn("timeout", source)
        self.assertNotIn("for attempt", source)

    def test_each_event_costs_two_requests(self):
        transport = self.CountingTransport()
        sync = ExecutionPlanSync(
            client=NotionClient(transport=transport, database_id="DB-1")
        )

        for i in range(10):
            sync.sync(
                create_event(
                    source="DESKTOP_1",
                    role="COO",
                    project_id=f"PRJ-{i:03d}",
                    event_type="MILESTONE_COMPLETED",
                    status="IN_PROGRESS",
                    summary="rate limit probe",
                    milestone="M1",
                    history_candidate=True,
                    event_id=f"RATE-{i:03d}",
                    timestamp="2026-08-05T10:00:00+09:00",
                )
            )

        self.assertEqual(len(transport.calls), 20)
        self.assertEqual(transport.calls.count("query"), 10)


class NonFastForwardPushTests(RunnerFailurePathTestCase):
    """The Backup remote having moved ahead — verified SAFE, pinned so it
    stays that way.

    docs/08 section 5 forbids force-push, pull, merge and rebase, so a Working
    Copy that has fallen behind the remote CANNOT be reconciled automatically.
    What matters is that the attempt fails safely rather than destructively,
    and it does:

      * git_push raises GitOperationError rather than returning success;
      * the failure is NOT classified as an authentication failure, so
        BackupStatus stays PENDING (retryable) instead of FAILED;
      * the remote's history is left intact — the other writer's commit
        survives;
      * the local commit is kept, so nothing the Runner produced is lost.

    The operational consequence is worth stating plainly: because no allowed
    git command can fast-forward the Working Copy, BACKUP_PENDING will repeat
    on every run until a human intervenes, and the Backup Pending retry path
    will keep re-attempting the same doomed push silently. That is a runbook
    gap, not a safety failure — nothing is lost or overwritten.
    """

    def _make_remote_ahead(self):
        other = self.root / "other_clone"
        self._run_git(["clone", str(self.bare_remote_dir), str(other)], cwd=self.root)
        self._run_git(["config", "user.email", "other@example.invalid"], cwd=other)
        self._run_git(["config", "user.name", "Other Writer"], cwd=other)
        (other / "remote_only.md").write_text("written elsewhere\n", encoding="utf-8")
        self._run_git(["add", "-A"], cwd=other)
        self._run_git(["commit", "-m", "commit from another clone"], cwd=other)
        self._run_git(["push", "origin", "main"], cwd=other)

    def test_a_non_fast_forward_push_fails_without_damaging_the_remote(self):
        from backup.git_ops import (
            git_add_all,
            git_commit,
            git_push,
            is_authentication_failure,
        )

        self._make_remote_ahead()

        (self.backup_working_copy_dir / "local.md").write_text("local\n", encoding="utf-8")
        git_add_all(self.backup_working_copy_dir)
        local_commit = git_commit(self.backup_working_copy_dir, "backup: local change")

        with self.assertRaises(GitOperationError) as caught:
            git_push(self.backup_working_copy_dir)

        # Not an auth failure -> BackupStatus stays PENDING, not FAILED.
        self.assertFalse(is_authentication_failure(str(caught.exception)))

        # The other writer's commit is still on the remote.
        remote_log = self._run_git(["log", "--oneline", "main"], cwd=self.bare_remote_dir)
        self.assertIn("commit from another clone", remote_log.stdout)

        # And our own commit is still here to retry with.
        local_log = self._run_git(["log", "--oneline", "-1"], cwd=self.backup_working_copy_dir)
        self.assertIn(local_commit[:7], local_log.stdout)


class BackupLogPersistenceTests(unittest.TestCase):
    """BUG-37 (NOT FIXED — spec conformance gap, docs/08 sections 68-69).

    CHARACTERIZATION: asserts today's behaviour.

    docs/08 section 68 lists the Backup Log's minimum required fields and
    section 69 gives its location:

        D:\\DOJOONPASS_COMPANY_OPS\\runtime\\logs\\backup\\

    `BackupLogEntry` implements section 68 exactly — all nine fields are
    present, plus to_dict/to_json/from_dict/from_json. Section 69 is not
    implemented at all: no code in src/backup/ writes a file, and
    `runtime/logs/` contains only collector.log.

    The entry is built in backup/runner.py, returned up through
    app/runner.py, and run_company_ops.py prints only
    `backup_entry.final_status`. Everything else — push_result,
    changed_files, deleted_files, commit_hash, run_id, the timestamps — exists
    only in memory and is discarded when the process exits.

    That the serialisation half was built and the writing half was not is the
    tell: this was intended and left incomplete, not decided against.

    Consequence: when a Backup fails, the REASON is printed to stdout once and
    then gone. Task Scheduler does not capture stdout by default, and the exit
    code is 0 (BUG-36), so a failed backup leaves no durable trace anywhere.
    """

    REQUIRED_BY_SECTION_68 = [
        "run_id",
        "backup_start",
        "source",
        "changed_files",
        "deleted_files",
        "commit_hash",
        "push_result",
        "backup_end",
        "final_status",
    ]

    def test_section_68_fields_are_all_implemented(self):
        """The half that IS done."""
        import dataclasses

        from backup.log import BackupLogEntry

        fields = {f.name for f in dataclasses.fields(BackupLogEntry)}
        self.assertTrue(set(self.REQUIRED_BY_SECTION_68) <= fields)

    def test_the_entry_can_serialise_itself(self):
        """Which is why the missing writer is a gap and not a design choice."""
        from backup.log import BackupLogEntry

        for method in ("to_dict", "to_json", "from_dict", "from_json"):
            self.assertTrue(hasattr(BackupLogEntry, method))

    def test_section_69_has_no_writer_anywhere_in_backup(self):
        backup_src = Path(__file__).resolve().parents[1] / "src" / "backup"
        sources = sorted(backup_src.glob("*.py"))
        # Every assertion below is an assertNotIn, so an empty glob makes all
        # of them pass against an empty string — the test would report "no
        # writer anywhere in backup" precisely because it read nothing.
        self.assertTrue(sources, f"no sources under {backup_src}")
        text = "\n".join(p.read_text(encoding="utf-8") for p in sources)

        self.assertNotIn("logs/backup", text)
        self.assertNotIn("logs\\\\backup", text)
        # No file-writing call at all in the Backup package.
        self.assertNotIn("write_text", text)

    def test_the_entrypoint_prints_only_the_final_status(self):
        """So push_result — the actual failure reason — is never persisted."""
        entrypoint = (
            Path(__file__).resolve().parents[1] / "run_company_ops.py"
        ).read_text(encoding="utf-8")

        self.assertIn("backup_entry.final_status", entrypoint)
        self.assertNotIn("push_result", entrypoint)
        self.assertNotIn("backup_entry.to_json", entrypoint)


class CorruptCandidateFileTests(unittest.TestCase):
    """BUG-38 (NOT FIXED): one unreadable candidate file blocks the whole day.

    CHARACTERIZATION: asserts today's behaviour.

    `FileHistoryRepository.list()` walks the keep directory and calls
    `json.loads` on every file with no per-file guard. A single corrupt file
    therefore raises JSONDecodeError out of `list()`, which propagates into
    `generate_daily_history()` — so the healthy candidates for that day are
    not written either. One bad file costs the whole day's Company History.

    docs/03 section 53 states this exact rule for the Collector ("one
    malformed Event must never stop the rest of the batch") and
    collector/runtime.py implements it with two guards. The History repository
    has no equivalent, even though it sits closer to the permanent record.

    Also characterized here: `HistoryCandidate.from_dict()` accepts a string
    where `evidence` should be a list and silently expands it to a tuple of
    single characters — corruption rather than rejection. A missing key or a
    bad filter_result value does raise, so the field-level behaviour is
    inconsistent rather than uniformly strict or uniformly lenient.

    Not fixed: skipping a corrupt candidate silently would hide data loss,
    while failing the run is what happens now. Which one is right — and where
    the skipped file should be reported — is a decision.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.keep = self.root / "keep"
        self.review = self.root / "review"
        self.keep.mkdir(parents=True)
        self.review.mkdir()
        self.repo = FileHistoryRepository(keep_dir=self.keep, review_dir=self.review)

    def _save_healthy(self, event_id="OK-1"):
        self.repo.save(
            HistoryCandidate(
                history_id=f"HIST-{event_id}",
                event_id=event_id,
                timestamp="2026-08-05T10:00:00+09:00",
                category="MILESTONE",
                project_id="SEARCH_FRONTEND",
                role="COO",
                summary="healthy candidate",
                evidence=(),
                filter_result=HistoryDecision.KEEP,
            )
        )

    def test_a_healthy_repository_lists_and_renders(self):
        """Baseline, so the failures below are caused by the corrupt file."""
        self._save_healthy()

        self.assertEqual(len(self.repo.list(decision=HistoryDecision.KEEP)), 1)
        body = generate_daily_history(
            self.repo, date(2026, 8, 5), output_dir=self.root / "daily"
        ).read_text(encoding="utf-8")
        self.assertIn("OK-1", body)

    def test_one_corrupt_file_breaks_listing_entirely(self):
        self._save_healthy()
        (self.keep / "HIST-BROKEN.json").write_text("{ not json", encoding="utf-8")

        with self.assertRaises(json.JSONDecodeError):
            self.repo.list(decision=HistoryDecision.KEEP)

    def test_one_corrupt_file_blocks_the_whole_days_history(self):
        """The healthy candidate is lost too — that is the severity."""
        self._save_healthy()
        (self.keep / "HIST-BROKEN.json").write_text("{ not json", encoding="utf-8")

        with self.assertRaises(json.JSONDecodeError):
            generate_daily_history(
                self.repo, date(2026, 8, 5), output_dir=self.root / "daily"
            )
        self.assertFalse((self.root / "daily" / "2026-08-05.md").exists())

    def test_from_dict_is_inconsistently_strict_about_evidence(self):
        base = {
            "history_id": "HIST-1",
            "event_id": "E-1",
            "timestamp": "2026-08-05T10:00:00+09:00",
            "category": "MILESTONE",
            "project_id": "SEARCH_FRONTEND",
            "role": "COO",
            "summary": "s",
            "evidence": [],
            "filter_result": "KEEP",
        }

        # A missing key or a bad enum value is rejected...
        with self.assertRaises(KeyError):
            HistoryCandidate.from_dict({k: v for k, v in base.items() if k != "history_id"})
        with self.assertRaises(ValueError):
            HistoryCandidate.from_dict({**base, "filter_result": "KEEEP"})

        # ...but a string where a list belongs is silently exploded per character.
        candidate = HistoryCandidate.from_dict({**base, "evidence": "abc"})
        self.assertEqual(candidate.evidence, ("a", "b", "c"))


class IntakeRecursionErrorTests(unittest.TestCase):
    """BUG-40 — **FIXED in C22.** Kept as the guarantee it became.

    Was: one deeply-nested file permanently halted the Runner.

    CHARACTERIZATION: asserts today's behaviour.

    `transport/intake._is_parseable_json()` exists precisely so an unparseable
    file is skipped rather than crashing the run. It catches (OSError,
    ValueError) — which covers JSONDecodeError and unreadable files, but NOT
    RecursionError. `json.loads` raises RecursionError on deeply nested input
    (measured: fine at depth 200, raises at depth 5000).

    Blast radius, measured with 3 healthy Events and one nested file sorting
    ahead of them:

        RecursionError propagates out of run_intake()
        healthy Events delivered : 0 of 3
        bad file after the run   : still in transport/

    run_intake is step 2 of the Runner, immediately after the lock. So the
    whole pipeline — Collector, Notion, History, Daily, Backup — never runs.
    And because nothing removes the file, EVERY subsequent run dies at the
    same point. One file produces a permanent, self-perpetuating total outage.

    The file arrives from another Desktop through a shared folder, which
    docs/02 treats as untrusted input.

    Contrast with the Collector, which docs/03 section 53 requires to survive
    one bad Event and which does. Intake has the same intent and the guard is
    simply one exception class short.

    Not fixed: RecursionError is not a ValueError, and widening the except
    clause changes which failures are silently skipped versus surfaced — that
    boundary is a decision.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.transport_dir = self.root / "transport"
        self.transport_dir.mkdir(parents=True)

    def _stage(self, name, payload_bytes):
        path = self.transport_dir / name
        path.write_bytes(payload_bytes)
        old = time.time() - 60
        os.utime(path, (old, old))
        return path

    def _stage_event(self, event_id):
        event = create_event(
            source="DESKTOP_3",
            role="COO",
            project_id="SEARCH_FRONTEND",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            summary="healthy event",
            milestone="M1",
            history_candidate=True,
            event_id=event_id,
            timestamp="2026-08-05T10:00:00+09:00",
        )
        return self._stage(f"{event_id}.json", event.to_json().encode("utf-8"))

    def _intake(self):
        from transport import run_intake

        return run_intake(
            transport_dir=self.transport_dir,
            incoming_dir=self.root / "incoming",
            processed_dir=self.root / "processed",
            rejected_dir=self.root / "rejected",
            stable_after_seconds=0.0,
        )

    def test_ordinary_unparseable_json_is_skipped_not_raised(self):
        """The guard working as intended, so the failure below is specific."""
        self._stage("BAD.json", b"{ not json")
        self._stage_event("ZZ-OK")

        summary = self._intake()

        self.assertEqual(len(summary.moved), 1)
        self.assertEqual(len(summary.skipped_invalid), 1)

    def test_moderate_nesting_is_fine(self):
        self._stage("NEST.json", b"[" * 200 + b"]" * 200)

        summary = self._intake()

        self.assertEqual(len(summary.skipped_invalid) + len(summary.moved), 1)

    def test_deep_nesting_is_skipped_and_the_rest_of_the_batch_proceeds(self):
        """GUARANTEE (was CHARACTERIZATION): BUG-40 is closed.

        This used to assert the opposite — that the `RecursionError` escaped
        and took the run with it, leaving `incoming/` empty and the file in
        place so the next run died identically. `json.loads()` raises
        `RecursionError` on deeply nested input and it is a `RuntimeError`
        subclass, so `except (OSError, ValueError)` never covered it.

        `agent/signals.py` had already answered this for its own
        `json.loads`, in a comment beside the catch. Naming `RecursionError`
        here applies that decision where it was missing rather than making a
        new one: this predicate's entire purpose is that "a file it cannot
        parse is skipped rather than crashing the run".
        """
        for i in range(3):
            self._stage_event(f"ZZ-OK-{i}")
        # Sorts ahead of the healthy files, so it is reached first.
        self._stage("AA-DEEP.json", b"[" * 200000 + b"]" * 200000)

        summary = self._intake()

        self.assertEqual(summary.skipped_invalid, ("AA-DEEP.json",))
        self.assertEqual(len(summary.moved), 3)
        # Never promoted, never deleted — left for a human, re-judged next run.
        self.assertTrue((self.transport_dir / "AA-DEEP.json").exists())

    def test_the_guard_now_names_recursion_error(self):
        """The structural half, so a refactor cannot quietly undo it."""
        source = inspect.getsource(sys.modules["transport.intake"]._is_parseable_json)

        self.assertIn("RecursionError", source)


class BackupFailedStatusClearingTests(RunnerFailurePathTestCase):
    """BUG-41 (NOT FIXED): BACKUP_FAILED is silently cleared by the next
    no-op run — the same hazard that was fixed for BACKUP_PENDING.

    CHARACTERIZATION: asserts today's behaviour.

    backup/runner.py's own comment, written for the approved Backup Pending
    자동복구 change, states the problem exactly:

        backup_status가 PENDING -> NOT_REQUIRED로 덮어써져 미완료 신호까지
        사라진다. CEO 승인 A안: State가 PENDING이면 push를 먼저 재시도한다.

    That fix covers PENDING. FAILED has the identical hole and was outside the
    approved scope, so it was left. Measured: seed BACKUP_FAILED, run again
    with no changes, and the state comes back BACKUP_NOT_REQUIRED.

    FAILED is not a transient state — it is produced by an authentication
    failure, a detected secret, or detected deletions, all of which need a
    human. Most runs have no new Daily file, so the very next run usually
    takes the no-change path and erases the signal.

    Combined with BUG-36 (always exit 0) and BUG-37 (no Backup Log file),
    a failed backup can leave no trace at all within one run cycle.

    There is also no transition validation anywhere: `BackupState.backup_status`
    is plain assignment, so any status can overwrite any other.
    """

    def _seed_status(self, status):
        from backup.state import load_state, save_state

        state = load_state(self.backup_state_path)
        state.backup_status = status
        save_state(self.backup_state_path, state)

    def _run_backup(self):
        from backup.runner import run_once as backup_run_once

        return backup_run_once(
            master_dir=self.local_master_dir,
            working_copy_dir=self.backup_working_copy_dir,
            state_path=self.backup_state_path,
        )

    def _current_status(self):
        from backup.state import load_state

        return load_state(self.backup_state_path).backup_status

    def test_a_successful_backup_records_success(self):
        """Baseline."""
        (self.local_master_dir / "daily").mkdir(parents=True, exist_ok=True)
        (self.local_master_dir / "daily" / "2026-08-05.md").write_text("x", encoding="utf-8")

        entry = self._run_backup()

        self.assertEqual(entry.final_status, BackupStatus.SUCCESS)

    def test_a_failed_status_is_erased_by_the_next_no_change_run(self):
        (self.local_master_dir / "daily").mkdir(parents=True, exist_ok=True)
        (self.local_master_dir / "daily" / "2026-08-05.md").write_text("x", encoding="utf-8")
        self._run_backup()

        self._seed_status(BackupStatus.FAILED)
        self.assertEqual(self._current_status(), BackupStatus.FAILED)

        entry = self._run_backup()

        self.assertEqual(entry.final_status, BackupStatus.NOT_REQUIRED)
        self.assertEqual(self._current_status(), BackupStatus.NOT_REQUIRED)

    def test_the_pending_sibling_is_handled(self):
        """The approved fix, asserted so the asymmetry is explicit."""
        source = inspect.getsource(sys.modules["backup.runner"].run_once)

        self.assertIn("if state.backup_status is BackupStatus.PENDING", source)
        self.assertNotIn("if state.backup_status is BackupStatus.FAILED", source)


class StateLossVersusProcessedFilesTests(unittest.TestCase):
    """BUG-43 (NOT FIXED): losing collector_state.json while processed/ files
    remain produces a permanent stuck loop.

    CHARACTERIZATION: asserts today's behaviour.

    SCOPE CORRECTED after measuring. The first framing blamed state loss; the
    state file turns out not to be the deciding factor.

    Any Event file that appears in incoming/ under a name already present in
    processed/ fails permanently, whatever the state says:

        state intact  -> collect() returns DUPLICATE, target_dir = processed_dir
        state lost    -> collect() returns ACCEPTED,  target_dir = processed_dir

    Both verdicts move to the same directory, the move fails on the existing
    name, and the outcome is FAILED either way. Nothing moves the file aside,
    so it stays in incoming/ and the next run repeats identically. Measured
    over three consecutive runs: accepted=0, failed=1 each time.

    State loss is one route in (below). Another is any process that writes
    directly into incoming/ — reporter.local_output does exactly that — with
    an event_id that has already been through the pipeline.

    The realistic trigger is not disk corruption — it is an operator. The
    approved State Recovery 통일 change makes a corrupt state file raise a
    named error and stop the Runner. The natural response to "collector state
    file is corrupted" is to delete the file, which lands exactly here.

    Note the rollback in step 3 is NOT the bug and must not be removed:
    without it the id would be burned and the Event would come back DUPLICATE
    and be silently dropped, which is worse (that was BUG-9). The rollback
    converts silent loss into a visible, repeating failure. The missing piece
    is that nothing reconciles the two notions of "already handled".

    Visibility, restated after measuring (C24). `collector_summary.failed`
    is printed by run_company_ops.py — but to stdout, which Task Scheduler
    does not capture, and the Run Manifest carries it as a *metric on a
    SUCCESS component*, which `_print_last_run()` deliberately does not
    print. BUG-36 has since been fixed and the exit code is still 0 here,
    correctly: the collector component is SUCCESS by design (docs/03 §53
    per-file isolation).

    So the only operator-facing signal was `ops_status.py`'s
    "수집되지 않고 남은 Event: incoming=1", which is accurate and stood
    forever with no way to tell that no future run would clear it. C24 added
    `IntakeBacklog.name_collision`, which names the reason in that same
    sentence (`test_observability.py::NameCollisionInIncomingTests`). The
    stuck loop below is unchanged.

    Not fixed: reconciling them means either rebuilding state from processed/
    or treating a name collision as a duplicate rather than a failure. Both
    change what "already handled" means, which is a decision.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.incoming = self.root / "incoming"
        self.processed = self.root / "processed"
        self.rejected = self.root / "rejected"
        for d in (self.incoming, self.processed, self.rejected):
            d.mkdir(parents=True)
        self.state_path = self.root / "collector_state.json"

    def _stage(self, event_id="STATELOSS-1"):
        event = create_event(
            source="DESKTOP_1",
            role="COO",
            project_id="SEARCH_FRONTEND",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            summary="state loss probe",
            milestone="M1",
            history_candidate=True,
            event_id=event_id,
            timestamp="2026-08-05T10:00:00+09:00",
        )
        (self.incoming / f"{event_id}.json").write_text(event.to_json(), encoding="utf-8")

    def _collect(self):
        from collector.state import PersistentSeenEventStore

        return collector_run_once(
            collector=Collector(seen_store=PersistentSeenEventStore(state_path=self.state_path)),
            incoming_dir=self.incoming,
            processed_dir=self.processed,
            rejected_dir=self.rejected,
            log_path=self.root / "collector.log",
        )

    def test_even_with_state_intact_a_restaged_event_fails_permanently(self):
        """Corrected after measuring: the state file is not the deciding
        factor at all.

        collector.collect() DOES return DUPLICATE — the id is in the seen set.
        But DUPLICATE and ACCEPTED both target processed_dir, and that move
        fails because the name is already there, so the outcome is FAILED
        either way. The file is never moved aside, so it repeats forever.
        """
        self._stage()
        self.assertEqual(self._collect().accepted, 1)

        self._stage()
        summary = self._collect()

        self.assertEqual(summary.failed, 1)
        self.assertEqual(summary.duplicate, 0)
        # The id IS recognised as seen — the collision is in the file move.
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["processed_event_ids"], ["STATELOSS-1"])

    def test_both_duplicate_and_accepted_target_the_same_directory(self):
        """The structural cause: neither verdict has anywhere else to put it."""
        source = inspect.getsource(collector_run_once)
        duplicate_at = source.index("CollectorStatus.DUPLICATE")
        accepted_tail = source[duplicate_at:]

        self.assertIn("target_dir = processed_dir", accepted_tail)

    def test_state_loss_turns_it_into_a_permanent_failure(self):
        self._stage()
        self.assertEqual(self._collect().accepted, 1)

        self.state_path.unlink()  # operator deletes a state file they were told is corrupt
        self._stage()

        for _ in range(3):
            summary = self._collect()
            self.assertEqual(summary.accepted, 0)
            self.assertEqual(summary.failed, 1)
            # Still queued, so the next run does exactly the same thing.
            self.assertTrue((self.incoming / "STATELOSS-1.json").exists())

    def test_the_seen_mark_is_rolled_back_each_time(self):
        """The rollback is correct — it is why this repeats instead of
        silently dropping the Event (BUG-9). Asserted so a future fix does
        not remove it by mistake."""
        import json as json_module

        self._stage()
        self._collect()
        self.state_path.unlink()
        self._stage()

        self._collect()

        state = json_module.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["processed_event_ids"], [])

    def test_the_failure_is_at_least_visible_in_the_summary(self):
        """Unlike BUG-40/BUG-42, something is reported — via a field the
        entrypoint actually prints."""
        entrypoint = (
            Path(__file__).resolve().parents[1] / "run_company_ops.py"
        ).read_text(encoding="utf-8")

        self.assertIn("collector_summary.failed", entrypoint)


class RetryQueueDrainTests(RunnerFailurePathTestCase):
    """VERIFIED CORRECT — the property that stops the Retry Queue growing
    without bound after a Notion outage, asserted end to end for the first
    time.

    The CEO Policy Decision (Notion Retry Architecture Plan A) is that a
    still-failing Event stays queued and ANY non-error result clears it. The
    approved Retry Queue Batch Save change rewrote this branch, so it is worth
    an explicit test rather than relying on the unit-level upsert/remove tests:

        if status in (NOTION_RETRY_REQUIRED, NOTION_FAILED): upsert
        elif retry_queue_remove(...):                        removed

    NOTION_SKIPPED_OLD_EVENT matters most here. It is what docs/04 section 62's
    duplicate guard returns when a queued Event is replayed after Notion has
    already been updated — so if it did NOT clear the queue, every Event
    synced during an outage would stay queued forever and BUG-13's unbounded
    retry would apply to Events that had actually succeeded.

    Measured through the real Runner: 5 Events queued by a simulated outage,
    then a second run returning each status in turn.

        NOTION_SKIPPED_OLD_EVENT   5 -> 0
        NOTION_CREATED             5 -> 0
        NOTION_UPDATED             5 -> 0
        still failing (control)    5 -> 5
    """

    class ScriptedSync:
        """Fails on the first run, returns `second` on every run after."""

        def __init__(self, second):
            self.second = second
            self.round = 1

        def sync(self, event):
            status = SyncStatus.NOTION_FAILED if self.round == 1 else self.second
            return SyncResult(
                status=status,
                event_id=event.event_id,
                project_id=event.project_id,
                error="simulated outage" if status is SyncStatus.NOTION_FAILED else None,
            )

    def _queue_size(self):
        from notion.retry_queue import load_queue

        if not self.notion_retry_queue_path.exists():
            return 0
        return len(load_queue(self.notion_retry_queue_path))

    def _outage_then(self, second_status, *, events=5):
        for i in range(events):
            self._write_event(event_id=f"QDRAIN-{i:03d}", project_id=f"PRJ-{i:03d}")

        sync = self.ScriptedSync(second_status)
        self._run(notion_sync=sync)
        after_outage = self._queue_size()

        sync.round = 2
        self._run(now=datetime(2026, 8, 2, 13, 0).astimezone(), notion_sync=sync)
        return after_outage, self._queue_size()

    def test_a_skipped_old_event_clears_the_queue(self):
        """The docs/04 section 62 replay path — the one that matters most."""
        after_outage, after_recovery = self._outage_then(
            SyncStatus.NOTION_SKIPPED_OLD_EVENT
        )

        self.assertEqual(after_outage, 5)
        self.assertEqual(after_recovery, 0)

    def test_created_and_updated_also_clear_the_queue(self):
        for status in (SyncStatus.NOTION_CREATED, SyncStatus.NOTION_UPDATED):
            with self.subTest(status=status.name):
                self.setUp()
                after_outage, after_recovery = self._outage_then(status)

                self.assertEqual(after_outage, 5)
                self.assertEqual(after_recovery, 0)

    def test_a_still_failing_event_stays_queued(self):
        """The control — clearing must depend on the result, not on the replay."""
        after_outage, after_recovery = self._outage_then(SyncStatus.NOTION_FAILED)

        self.assertEqual(after_outage, 5)
        self.assertEqual(after_recovery, 5)


class RunIdCollisionTests(unittest.TestCase):
    """BUG-44 (NOT FIXED): `run_id` has one-second resolution, and one second
    is longer than a Runner execution.

    CHARACTERIZATION: asserts today's behaviour.

    app/runner.py derives it as:

        resolved_run_id = run_id or now.isoformat(timespec="seconds")

    Two runs in the same second therefore share a run_id. notion/
    dashboard_pending.py's module docstring states the assumption this breaks:
    "Dedup key is `run_id`: one Runner execution can never produce two..." —
    the dedup is only sound if run_id identifies exactly one execution.

    Measured consequence: saving twice under one run_id leaves ONE record
    whose properties are the second run's, with attempt_count incremented. The
    first run's Dashboard payload is gone, not queued.

    Reachability, measured rather than assumed: an idle run (0 Events)
    completes in 32ms. So a manual run immediately after a scheduled one, or
    any two triggers landing in the same second, collide. The Runner lock
    prevents them from being concurrent but not from being consecutive.

    Not fixed: adding microseconds or a counter changes the Run ID format that
    appears in the Ops Runs database and in BackupLogEntry, which is a
    user-visible identifier.
    """

    def test_the_run_id_is_second_resolution(self):
        source = inspect.getsource(runner_module.run_once)
        self.assertIn('now.isoformat(timespec="seconds")', source)

    def test_two_times_in_one_second_produce_the_same_run_id(self):
        from datetime import timezone

        kst = timezone(timedelta(hours=9))
        first = datetime(2026, 8, 5, 11, 0, 0, 0, tzinfo=kst)
        second = datetime(2026, 8, 5, 11, 0, 0, 900_000, tzinfo=kst)

        self.assertEqual(
            first.isoformat(timespec="seconds"), second.isoformat(timespec="seconds")
        )

    def test_a_colliding_run_id_overwrites_the_earlier_pending_record(self):
        from notion.dashboard_pending import load_pending, save_pending

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "dashboard_pending.json"
        now = datetime(2026, 8, 5, 11, 0).astimezone()

        save_pending(path, run_id="SAME-ID", properties={"first": True}, now=now)
        save_pending(path, run_id="SAME-ID", properties={"second": True}, now=now)

        records = load_pending(path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].properties, {"second": True})
        self.assertEqual(records[0].attempt_count, 2)

    def test_an_explicit_run_id_avoids_the_collision(self):
        """The parameter exists, so callers CAN supply a unique id."""
        signature = inspect.signature(runner_module.run_once)
        self.assertIn("run_id", signature.parameters)
        self.assertIsNone(signature.parameters["run_id"].default)


class HealthCheckCoverageTests(unittest.TestCase):
    """BUG-45 (NOT FIXED): `health_check()` cannot predict whether a sync will
    work, but reads as though it can.

    CHARACTERIZATION: asserts today's behaviour.

    It performs exactly one operation:

        self._transport.retrieve_database(self._database_id)

    So it answers "is the token valid and the database reachable?" and nothing
    else. It does not look at the schema at all.

    Measured: against a database whose `Status` property is a checkbox rather
    than a select — precisely the BUG-31 situation — health_check returns
    ok=True with no error. Every subsequent sync of that project would then
    fail with HTTP 400 and be retried forever.

    This matters for the first real connection specifically: health_check is
    the natural thing to run to confirm the setup is good, and it will say yes
    to a database that cannot actually be written to.

    Not fixed: making health_check schema-aware duplicates
    bootstrap.diff_properties, and deciding whether a schema mismatch makes
    the connection "unhealthy" or merely "needs bootstrap" is a decision.
    """

    class ReachableButBrokenSchema(InMemoryNotionTransport):
        def retrieve_database(self, database_id):
            return {
                "id": database_id,
                "title": [{"text": {"content": "Projects"}}],
                "properties": {
                    "Project": {"type": "title"},
                    "Status": {"type": "checkbox"},  # sync sends {"select": ...}
                },
            }

    def test_health_check_only_calls_retrieve_database(self):
        source = inspect.getsource(NotionClient.health_check)

        self.assertIn("retrieve_database", source)
        for schema_word in ("properties", "TARGET_PROPERTIES", "diff_properties"):
            self.assertNotIn(schema_word, source)

    def test_it_reports_healthy_despite_a_type_mismatch(self):
        client = NotionClient(
            transport=self.ReachableButBrokenSchema(), database_id="DB-1"
        )

        result = client.health_check()

        self.assertTrue(result.ok)
        self.assertIsNone(result.error)

    def test_it_does_report_an_unreachable_database(self):
        """The thing it IS for still works — so this is a scope gap, not a bug
        in what it does."""

        class Unreachable(InMemoryNotionTransport):
            def retrieve_database(self, database_id):
                raise NotionAPIError("Notion API returned 404", status_code=404)

        result = NotionClient(transport=Unreachable(), database_id="DB-1").health_check()

        self.assertFalse(result.ok)
        self.assertIn("404", result.error)


class BackupTimestampProtectionTests(RunnerFailurePathTestCase):
    """VERIFIED CORRECT — and the contrast with BUG-41 is the point.

    `backup/runner.py` protects the two timestamp-ish fields from being
    rewritten by a run that did no work. Its own comment says so:

        last_successful_backup and last_backup_commit are intentionally
        left as loaded — only a [real backup updates them]

    Measured: a BACKUP_NOT_REQUIRED run leaves both untouched, so
    "when did we last actually back up" stays truthful however many no-op
    runs happen in between. That is exactly right, and nothing asserted it.

    The contrast matters. The SAME function, in the SAME no-change path,
    overwrites `backup_status` — which is how BACKUP_FAILED gets erased
    (BUG-41). So the hazard was understood and defended against for two
    fields and not for the third. That makes BUG-41 look less like an
    oversight in analysis and more like an incomplete application of a rule
    the author had already worked out.

    This test exists so a future fix for BUG-41 cannot "simplify" the state
    write and take the timestamp protection down with it.
    """

    def _write_daily(self, name="2026-08-05.md", content="entry"):
        daily = self.local_master_dir / "daily"
        daily.mkdir(parents=True, exist_ok=True)
        (daily / name).write_text(content, encoding="utf-8")

    def _run_backup(self):
        from backup.runner import run_once as backup_run_once

        return backup_run_once(
            master_dir=self.local_master_dir,
            working_copy_dir=self.backup_working_copy_dir,
            state_path=self.backup_state_path,
        )

    def _state(self):
        from backup.state import load_state

        return load_state(self.backup_state_path)

    def test_a_real_backup_records_the_time_and_commit(self):
        self._write_daily()

        entry = self._run_backup()
        state = self._state()

        self.assertEqual(entry.final_status, BackupStatus.SUCCESS)
        self.assertIsNotNone(state.last_successful_backup)
        self.assertTrue(state.last_backup_commit)

    def test_a_no_change_run_does_not_rewrite_them(self):
        self._write_daily()
        self._run_backup()
        before = self._state()

        entry = self._run_backup()
        after = self._state()

        self.assertEqual(entry.final_status, BackupStatus.NOT_REQUIRED)
        self.assertEqual(after.last_successful_backup, before.last_successful_backup)
        self.assertEqual(after.last_backup_commit, before.last_backup_commit)

    def test_the_status_field_is_not_given_the_same_protection(self):
        """BUG-41, stated as the asymmetry it is. If this starts failing,
        BUG-41 was fixed and this class should be re-read as a whole."""
        self._write_daily()
        self._run_backup()
        before = self._state()

        self._run_backup()
        after = self._state()

        # Protected...
        self.assertEqual(after.last_successful_backup, before.last_successful_backup)
        # ...not protected.
        self.assertEqual(after.backup_status, BackupStatus.NOT_REQUIRED)


class DrainPendingPartialSuccessTests(unittest.TestCase):
    """`drain_pending()` partial-success behaviour — correct — plus one edge
    where its "Never raises" contract does not hold.

    The main behaviour is right and nothing asserted it. Draining 5 records
    where 2 fail:

        recorded=3, still_pending=2
        the 3 that succeeded are removed
        the 2 that failed stay, with attempt_count incremented

    So a partial Notion outage neither loses the recorded rows nor drops the
    failed ones.

    BUG-50 (P3, NOT FIXED): the docstring says "Never raises", and the guard
    is `except Exception`. That does not cover BaseException, so a
    KeyboardInterrupt or SystemExit from the client propagates — and it
    propagates from inside the loop, BEFORE `save_all()` runs. Records already
    written to Notion in that same call therefore stay in the pending file
    and are attempted again on the next run.

    Same shape as BUG-25: the side effect happened, the state recording it did
    not. Lower severity — Dashboard rows are a reporting artifact.

    What the consequence *is* has since narrowed. This used to end "producing
    duplicate Ops Runs rows"; the find-before-create guard added for the
    duplicate-row defect means the re-attempt now finds the row it already
    wrote and creates nothing. The lost-progress fact is unchanged and still
    pinned below — only its blast radius shrank, from a wrong Dashboard to a
    redundant lookup.

    Not fixed: catching BaseException to save progress conflicts with letting
    Ctrl+C actually interrupt, and which one wins is a decision.
    """

    class SelectiveClient:
        """Fails for records whose properties['i'] is in `fail`.

        Implements `find_or_create_by_title()` rather than
        `create_project()`: that is the call `drain_pending()` makes since
        the find-before-create guard was added for the duplicate-row defect.
        A double still offering only the old method would make every record
        fail with AttributeError and quietly turn these partial-success
        assertions into total-failure ones.
        """

        def __init__(self, fail=()):
            self.fail = set(fail)

        def find_or_create_by_title(self, *, property_name, value, properties):
            if properties.get("i") in self.fail:
                raise RuntimeError("simulated Notion outage")
            return {"id": "page-1"}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "dashboard_pending.json"
        self.now = datetime(2026, 8, 5, 11, 0).astimezone()

    def _seed(self, count):
        from notion.dashboard_pending import save_pending

        for i in range(count):
            save_pending(self.path, run_id=f"R-{i}", properties={"i": i}, now=self.now)

    def _remaining(self):
        from notion.dashboard_pending import load_pending

        return load_pending(self.path)

    def test_only_the_failures_stay_queued(self):
        from notion.dashboard_pending import drain_pending

        self._seed(5)

        recorded, still_pending = drain_pending(self.path, self.SelectiveClient(fail={1, 3}))

        self.assertEqual((recorded, still_pending), (3, 2))
        self.assertEqual(sorted(r.run_id for r in self._remaining()), ["R-1", "R-3"])

    def test_a_failed_record_has_its_attempt_count_incremented(self):
        from notion.dashboard_pending import drain_pending

        self._seed(3)

        drain_pending(self.path, self.SelectiveClient(fail={0, 1, 2}))

        self.assertEqual([r.attempt_count for r in self._remaining()], [2, 2, 2])

    def test_a_full_success_empties_the_queue(self):
        from notion.dashboard_pending import drain_pending

        self._seed(3)

        recorded, still_pending = drain_pending(self.path, self.SelectiveClient())

        self.assertEqual((recorded, still_pending), (3, 0))
        self.assertEqual(self._remaining(), [])

    def test_a_base_exception_escapes_before_progress_is_saved(self):
        """BUG-50. If this starts passing, the contract was tightened."""
        from notion.dashboard_pending import drain_pending

        class Interrupting:
            def __init__(self):
                self.calls = 0

            def find_or_create_by_title(self, *, property_name, value, properties):
                self.calls += 1
                if self.calls == 1:
                    return {"id": "page-1"}  # this one really is recorded
                raise KeyboardInterrupt("operator stopped the run")

        self._seed(3)

        with self.assertRaises(KeyboardInterrupt):
            drain_pending(self.path, Interrupting())

        # The first record WAS written to Notion, but all three are still
        # queued — so it will be attempted a second time on the next run.
        self.assertEqual(len(self._remaining()), 3)

    def test_the_docstring_claims_it_never_raises(self):
        """The claim that BUG-50 contradicts, pinned so the two move together."""
        from notion.dashboard_pending import drain_pending

        self.assertIn("Never raises", inspect.getdoc(drain_pending))


# Values that look set but contain nothing — exactly what a trailing space
# after `=`, a stray tab, or a copied newline produces in a hand-written
# `.env`. Built with escapes rather than literal characters so the intent
# survives an editor that trims whitespace.
BLANK_VALUES = ("   ", "\t", "\n", " \t\n ")


class NotionConfigWhitespaceTests(unittest.TestCase):
    """BUG-51 (NOT FIXED): configuration values are never trimmed, so the
    ordinary ways of mistyping a `.env` line are accepted and fail later.

    CHARACTERIZATION: asserts today's behaviour.

    `NotionConfig.from_env()` rejects a variable only when it is falsy —
    absent or "". Anything else is passed through byte for byte:

        NOTION_API_TOKEN="   "        accepted -> Notion answers 401
        NOTION_API_TOKEN="  ntn_x  "  accepted -> 401 (spaces are part of it)
        NOTION_API_TOKEN='"ntn_x"'    accepted -> 401 (quotes are part of it)
        NOTION_API_TOKEN="ntn_x\\n"    accepted -> 401
        NOTION_OPS_RUNS_DATABASE_ID="   "  truthy -> Dashboard runs against a
                                            whitespace id and gets 404

    Those are exactly the mistakes a hand-written `.env` produces: a trailing
    space after `=`, quoting the value the way many `.env` examples show, or a
    stray newline. This project deliberately loads no `.env` file (stdlib
    only) — values arrive as real OS environment variables, so whatever sets
    them can carry the quotes and spaces through unchanged.

    The failure lands in the worst place: a 401/404 becomes NotionAPIError ->
    NOTION_RETRY_REQUIRED, which nothing caps (BUG-13/BUG-14), so a mistyped
    token means every Event queues forever rather than failing once loudly.

    One mitigation already exists and is worth knowing: `health_check()` DOES
    catch a bad token, because a 401 makes `retrieve_database` raise. It is
    schema problems it cannot see (BUG-45). So checking health before the
    first run catches this class of mistake and not the other.

    PARTLY FIXED, split along the line where judgement starts.

    Fixed — a BLANK value is treated as absent. `""` was already rejected,
    so accepting `"   "` was an inconsistency in the existing rule rather
    than a decision anyone had made. No legitimate deployment sets a
    whitespace-only token or database id.

    Not fixed — a value that does contain characters is still passed through
    byte for byte: `"  ntn_x  "` and `'"ntn_x"'` reach Notion unchanged.
    Trimming or unquoting those would be second-guessing what the operator
    set, which is a judgement call recorded in BACKLOG.md. The tests below
    pin both halves so the boundary stays where it was drawn.
    """

    def _from_env(self, **env):
        from notion.config import NotionConfig

        base = {"NOTION_API_TOKEN": "ntn_valid", "NOTION_PROJECTS_DATABASE_ID": "db-valid"}
        base.update(env)
        return NotionConfig.from_env(base)

    def test_an_absent_or_empty_value_is_rejected(self):
        """The guard that does exist."""
        from notion.config import NotionConfigError

        for value in (None, ""):
            with self.subTest(value=value):
                env = {"NOTION_PROJECTS_DATABASE_ID": "db-valid"}
                if value is not None:
                    env["NOTION_API_TOKEN"] = value
                with self.assertRaises(NotionConfigError):
                    from notion.config import NotionConfig

                    NotionConfig.from_env(env)

    def test_a_blank_token_is_now_refused(self):
        """FIXED (the blank half). `""` was already rejected; `"   "` was
        not, though it is the same mistake with an invisible character.

        It mattered because of where the failure landed: a blank token
        reached Notion as a 401, became NOTION_RETRY_REQUIRED, and queued
        every Event forever since nothing caps that retry. Now it is one
        loud message before anything runs.
        """
        from notion.config import NotionConfigError

        for blank in BLANK_VALUES:
            with self.subTest(value=blank):
                with self.assertRaises(NotionConfigError) as caught:
                    self._from_env(NOTION_API_TOKEN=blank)
                self.assertIn("NOTION_API_TOKEN", str(caught.exception))

    def test_surrounding_whitespace_and_quotes_are_preserved(self):
        for raw in ("  ntn_valid  ", '"ntn_valid"', "'ntn_valid'", "ntn_valid\n"):
            with self.subTest(raw=raw):
                config = self._from_env(NOTION_API_TOKEN=raw)

                self.assertEqual(config.api_token, raw)
                self.assertNotEqual(config.api_token, "ntn_valid")

    def test_a_blank_optional_id_is_treated_as_unset(self):
        """FIXED. `""` already meant "no Dashboard"; `"   "` meant "run the
        Dashboard against an id Notion will 404 on". Same intention, two
        different outcomes — now both mean unset."""
        for blank in ("",) + BLANK_VALUES:
            with self.subTest(value=blank):
                config = self._from_env(NOTION_OPS_RUNS_DATABASE_ID=blank)
                self.assertIsNone(config.ops_runs_database_id)

    def test_nothing_in_the_project_loads_a_dotenv_file(self):
        """Why the raw OS value is what reaches the config unchanged."""
        src = Path(__file__).resolve().parents[1] / "src"
        sources = sorted(src.rglob("*.py"))
        # assertNotIn against an empty string always passes, so an empty
        # rglob would report "nothing loads a .env file" without reading one.
        self.assertTrue(sources, f"no sources under {src}")
        text = "\n".join(p.read_text(encoding="utf-8") for p in sources)

        self.assertNotIn("dotenv", text)
        self.assertNotIn("load_dotenv", text)

    def test_health_check_does_catch_a_bad_token(self):
        """The mitigation: unlike a schema mismatch (BUG-45), a 401 surfaces."""

        class Unauthorized(InMemoryNotionTransport):
            def retrieve_database(self, database_id):
                raise NotionAPIError("Notion API returned 401", status_code=401)

        result = NotionClient(transport=Unauthorized(), database_id="db").health_check()

        self.assertFalse(result.ok)
        self.assertIn("401", result.error)


class AuthFailureClassificationTests(unittest.TestCase):
    """BUG-52 (NOT FIXED): three real credential failures are classified as
    transient, so they are retried forever — the exact loop docs/08 section 62
    forbids and this function exists to prevent.

    CHARACTERIZATION: asserts today's behaviour.

    `is_authentication_failure()` decides BACKUP_FAILED (give up, a human must
    act) versus BACKUP_PENDING (retry next run) by substring-matching ten
    markers. docs/08 section 21 names what belongs in FAILED:

        Repository 설정 오류
        Permission 오류
        인증 설정 오류

    Measured against the messages git actually prints:

        CAUGHT (correct)
          "fatal: Authentication failed for ..."   -> authentication failed
          "Permission denied (publickey)."         -> permission denied

        MISSED (should be FAILED, treated as retryable)
          "remote: Permission to x/y.git denied to user."   403, PAT lacks
              the repo scope. The marker is "permission denied"; git writes
              "Permission to ... denied to ...", so the substring never matches.
          "remote: Repository not found."                   a private repo the
              token cannot see — GitHub reports not-found rather than 403 so
              it does not leak existence. docs/08 section 21's "Repository
              설정 오류", and there is no marker for it at all.
          "The requested URL returned error: 401"           expired or revoked
              token. "403 forbidden" is a marker but git prints the bare
              numeric code, and 401 has no marker.

        CORRECTLY TRANSIENT (unchanged, must stay False)
          Could not resolve host / Timed out / non-fast-forward / 500 /
          cannot lock ref

    Why it matters today specifically: the three missed cases are the three
    most common PAT mistakes — a classic token without `repo` scope, a
    fine-grained token without access to the repository, and an expired token.
    Each produces BACKUP_PENDING on every run instead of a FAILED that says a
    human must fix the credential. And BUG-41 then erases even that PENDING on
    the next no-change run.

    Not fixed: widening the markers risks the opposite error — classifying a
    transient failure as permanent stops retrying something that would have
    healed — so where to draw the line is a decision.
    """

    AUTH_MESSAGES = {
        "bad credentials": (
            "remote: Invalid username or password.\n"
            "fatal: Authentication failed for 'https://github.com/x/y.git/'"
        ),
        "ssh key rejected": (
            "git@github.com: Permission denied (publickey).\n"
            "fatal: Could not read from remote repository."
        ),
    }
    MISSED_AUTH_MESSAGES = {
        "403 scope missing": (
            "remote: Permission to x/y.git denied to user.\n"
            "fatal: unable to access '...': The requested URL returned error: 403"
        ),
        "repository not found": (
            "remote: Repository not found.\n"
            "fatal: repository 'https://github.com/x/y.git/' not found"
        ),
        "401 expired token": (
            "fatal: unable to access '...': The requested URL returned error: 401"
        ),
    }
    TRANSIENT_MESSAGES = {
        "dns": "fatal: unable to access '...': Could not resolve host: github.com",
        "timeout": (
            "fatal: unable to access '...': Failed to connect to github.com port 443: Timed out"
        ),
        "non fast forward": (
            "! [rejected]        main -> main (fetch first)\nerror: failed to push some refs"
        ),
        "server error": "fatal: unable to access '...': The requested URL returned error: 500",
        "local lock": "error: cannot lock ref 'refs/heads/main': Unable to create lock file",
    }

    def test_the_two_recognised_auth_failures_are_classified_correctly(self):
        from backup.git_ops import is_authentication_failure

        for label, message in self.AUTH_MESSAGES.items():
            with self.subTest(case=label):
                self.assertTrue(is_authentication_failure(message))

    def test_three_credential_failures_are_missed(self):
        """If these start passing, BUG-52 was fixed."""
        from backup.git_ops import is_authentication_failure

        for label, message in self.MISSED_AUTH_MESSAGES.items():
            with self.subTest(case=label):
                self.assertFalse(is_authentication_failure(message))

    def test_transient_failures_stay_retryable(self):
        """The property a fix must not break."""
        from backup.git_ops import is_authentication_failure

        for label, message in self.TRANSIENT_MESSAGES.items():
            with self.subTest(case=label):
                self.assertFalse(is_authentication_failure(message))

    def test_a_missed_credential_failure_becomes_pending_not_failed(self):
        """The consequence, at the status level: retried forever."""
        from backup.result import BackupStatus
        from backup.git_ops import is_authentication_failure

        message = self.MISSED_AUTH_MESSAGES["repository not found"]
        status = (
            BackupStatus.FAILED
            if is_authentication_failure(message)
            else BackupStatus.PENDING
        )

        self.assertEqual(status, BackupStatus.PENDING)

    def test_the_marker_list_is_substring_based(self):
        """The structural cause: git's wording need only differ slightly."""
        source = inspect.getsource(sys.modules["backup.git_ops"])

        self.assertIn('"permission denied"', source)
        self.assertNotIn('"repository not found"', source)
        self.assertNotIn('"401"', source)


class IntakeAlreadyElsewhereTests(unittest.TestCase):
    """BUG-53 (NOT FIXED): intake's duplicate check is existence-based, so any
    entry with the right NAME suppresses delivery — same root cause as BUG-47,
    different function.

    CHARACTERIZATION: asserts today's behaviour.

    `run_intake()` skips a file when

        any((directory / path.name).exists()
            for directory in (incoming_dir, processed_dir, rejected_dir))

    `Path.exists()` is true for anything at that path. Measured, all with a
    genuinely new Event still sitting in transport/ afterwards:

        processed/<id>.json is a directory     -> skipped_already_present
        incoming/<id>.json is 0 bytes          -> skipped_already_present
        rejected/<id>.json holds other content -> skipped_already_present

    The Event is not lost — it stays in transport/ — but it is never delivered
    and never will be while the impostor entry exists. And it is invisible:
    `skipped_already_present` is one of the IntakeSummary fields the
    entrypoint never prints (BUG-39), so the run reports "moved=0", identical
    to having nothing to do.

    Lower severity than BUG-47 in one respect and higher in another. Lower:
    these three directories are local `runtime/` paths this project controls,
    not the OneDrive-managed folder, so placeholder files are far less likely.
    Higher: BUG-47 mis-delivers, this one silently does not deliver at all,
    and BUG-43 already showed a stray same-named file in processed/ is
    reachable (state loss, or reporter.local_output writing straight into
    incoming/).

    Not fixed: the correct check is a decision — is_file() plus a size or
    content test changes what "already arrived" means, which is the same
    judgement BUG-47 needs.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.transport = self.root / "transport"
        self.transport.mkdir()
        for name in ("incoming", "processed", "rejected"):
            (self.root / name).mkdir()

    def _stage(self, event_id="NEW-1"):
        event = create_event(
            source="DESKTOP_3",
            role="COO",
            project_id="SEARCH_FRONTEND",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            summary="a genuinely new event",
            milestone="M1",
            history_candidate=True,
            event_id=event_id,
            timestamp="2026-08-05T10:00:00+09:00",
        )
        path = self.transport / f"{event_id}.json"
        path.write_text(event.to_json(), encoding="utf-8")
        old = time.time() - 60
        os.utime(path, (old, old))
        return path

    def _intake(self):
        from transport import run_intake

        return run_intake(
            transport_dir=self.transport,
            incoming_dir=self.root / "incoming",
            processed_dir=self.root / "processed",
            rejected_dir=self.root / "rejected",
            stable_after_seconds=0.0,
        )

    def test_a_new_event_is_delivered_when_nothing_collides(self):
        """Baseline."""
        self._stage()

        summary = self._intake()

        self.assertEqual(len(summary.moved), 1)
        self.assertEqual(list(self.transport.glob("*.json")), [])

    def test_a_genuine_duplicate_is_correctly_skipped(self):
        """The feature working as intended."""
        (self.root / "processed" / "NEW-1.json").write_text("{}", encoding="utf-8")
        self._stage()

        summary = self._intake()

        self.assertEqual(len(summary.skipped_already_present), 1)

    def test_an_impostor_entry_suppresses_delivery(self):
        cases = {
            "directory in processed": lambda: (
                self.root / "processed" / "NEW-1.json"
            ).mkdir(),
            "empty file in incoming": lambda: (
                self.root / "incoming" / "NEW-1.json"
            ).write_bytes(b""),
            "unrelated content in rejected": lambda: (
                self.root / "rejected" / "NEW-1.json"
            ).write_text("not an event", encoding="utf-8"),
        }
        for label, make_impostor in cases.items():
            with self.subTest(case=label):
                self.setUp()
                make_impostor()
                self._stage()

                summary = self._intake()

                self.assertEqual(len(summary.moved), 0)
                self.assertEqual(len(summary.skipped_already_present), 1)
                # Not lost, but never delivered while the impostor exists.
                self.assertTrue((self.transport / "NEW-1.json").exists())

    def test_the_skip_is_invisible_to_the_operator(self):
        """`skipped_already_present` is never printed (BUG-39)."""
        entrypoint = (
            Path(__file__).resolve().parents[1] / "run_company_ops.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("skipped_already_present", entrypoint)


class RunIdCorrelationTests(RunnerFailurePathTestCase):
    """One Runner execution must produce ONE run_id across every artifact,
    and it does — for a reason subtle enough to be worth pinning.

    VERIFIED CORRECT.

    Two modules derive the id independently:

        app/runner.py     resolved_manifest_run_id = run_id or now_iso(now)
        backup/runner.py  resolved_run_id = run_id or now.isoformat(...)

    app/runner passes the RAW `run_id` down (`run_id=run_id`, normally None),
    not its own resolved value — so backup/runner really does compute its own.
    They agree only because app/runner also threads the same `now` through
    (`now=now`), and because `now_iso()` *is*
    `isoformat(timespec="seconds")`. If either module called `datetime.now()`
    itself instead, the two would drift and `BackupLogEntry.run_id` would no
    longer match the Ops Runs "Run ID" for the same execution — the Backup
    Log (docs/08 section 68) and the Dashboard could not be correlated, and
    nothing would fail loudly.

    One of the three used to be a *fourth* spelling of the same rule: the
    Dashboard step recomputed `run_id or now.isoformat(timespec="seconds")`
    of its own. It now reuses `resolved_manifest_run_id`, so the Dashboard
    row and the manifest cannot drift at all. The remaining pair — app and
    backup — still can, which is what these tests watch.

    Measured: with now = 2026-08-05T11:00:00+09:00, the BackupLogEntry's
    run_id is exactly that string.

    Note this is the same second-resolution id as BUG-44, so consecutive runs
    inside one second still collide — that is a separate defect. What is
    asserted here is only that a SINGLE run is internally consistent.
    """

    def test_the_backup_entry_uses_the_runs_own_now(self):
        (self.local_master_dir / "daily").mkdir(parents=True, exist_ok=True)
        (self.local_master_dir / "daily" / "2026-08-05.md").write_text("x", encoding="utf-8")
        now = datetime(2026, 8, 2, 12, 0).astimezone()

        result = self._run(now=now)

        self.assertEqual(result[3].run_id, now.isoformat(timespec="seconds"))

    def test_an_explicit_run_id_reaches_the_backup_entry(self):
        (self.local_master_dir / "daily").mkdir(parents=True, exist_ok=True)
        (self.local_master_dir / "daily" / "2026-08-05.md").write_text("x", encoding="utf-8")

        result = self._run(run_id="EXPLICIT-RUN-ID")

        self.assertEqual(result[3].run_id, "EXPLICIT-RUN-ID")

    def test_the_runner_threads_now_into_the_backup_call(self):
        """The structural reason the two agree. If `now=now` is dropped,
        backup/runner falls back to its own clock and the ids drift."""
        source = inspect.getsource(runner_module.run_once)
        call_start = source.index("backup_run_once(")
        call = source[call_start : source.index(")", call_start + 200)]

        self.assertIn("now=now", call)
        self.assertIn("run_id=run_id", call)

    def test_neither_module_calls_datetime_now_for_the_run_id(self):
        """The property, not the spelling.

        This used to assert one exact source line in both modules, which
        made it fail the moment the Dashboard step stopped re-deriving the
        id and started reusing the manifest's — a change that strengthens
        the very correlation this class exists to protect. What matters is
        that each run_id comes from the threaded `now` (or an explicit
        `run_id`) and never from a fresh clock reading.
        """
        src_root = Path(__file__).resolve().parents[1] / "src"

        app_source = (src_root / "app" / "runner.py").read_text(encoding="utf-8")
        self.assertIn("resolved_manifest_run_id = run_id or now_iso(now)", app_source)
        # The Dashboard row is keyed by that same value, not a second one.
        self.assertIn("resolved_run_id = resolved_manifest_run_id", app_source)

        backup_source = (src_root / "backup" / "runner.py").read_text(encoding="utf-8")
        self.assertIn(
            'resolved_run_id = run_id or now.isoformat(timespec="seconds")', backup_source
        )

        for module_path, source in (
            ("app/runner.py", app_source),
            ("backup/runner.py", backup_source),
        ):
            with self.subTest(module=module_path):
                for line in source.splitlines():
                    if "resolved_run_id" in line or "resolved_manifest_run_id" in line:
                        if line.lstrip().startswith("#"):
                            continue
                        self.assertNotIn("datetime.now(", line)

    def test_the_backup_entry_and_the_manifest_carry_the_same_run_id(self):
        """The correlation itself, end to end — the docstring above explains
        why it holds, and nothing checked that it actually does."""
        (self.local_master_dir / "daily").mkdir(parents=True, exist_ok=True)
        (self.local_master_dir / "daily" / "2026-08-05.md").write_text("x", encoding="utf-8")

        result = self._run(now=datetime(2026, 8, 2, 12, 0).astimezone())

        self.assertEqual(result[3].run_id, result.summary.run_id)

    def test_an_explicit_run_id_reaches_both_the_backup_entry_and_the_manifest(self):
        (self.local_master_dir / "daily").mkdir(parents=True, exist_ok=True)
        (self.local_master_dir / "daily" / "2026-08-05.md").write_text("x", encoding="utf-8")

        result = self._run(run_id="EXPLICIT-RUN-ID")

        self.assertEqual(result[3].run_id, "EXPLICIT-RUN-ID")
        self.assertEqual(result.summary.run_id, "EXPLICIT-RUN-ID")


class BackupScopeCaseSensitivityTests(unittest.TestCase):
    """BUG-55 (NOT FIXED): the backup scope check is case-sensitive on a
    case-INsensitive filesystem, so Company History can be written correctly
    and silently never backed up.

    CHARACTERIZATION: asserts today's behaviour.

    docs/08 section 26 limits the backup to `daily/` and `monthly/`, and
    `_is_in_scope()` implements that as

        Path(rel_path).parts[0] in {"daily", "monthly"}

    an exact, case-sensitive comparison. Windows filesystems are
    case-INsensitive but case-PRESERVING: if the directory already exists as
    `Daily`, then `mkdir("daily", exist_ok=True)` succeeds against it and
    writes land in it, while `rglob()` reports the on-disk name `Daily\\...`.

    Measured end to end:

        on disk                 ['Daily']
        _relative_files()       []                      <- file not seen
        sync_to_working_copy()  added=() modified=() deleted=()
        working copy            []                      <- nothing copied

    So the Daily History file is written to Local Master exactly as intended,
    the Runner reports BACKUP_NOT_REQUIRED, and the file is never pushed
    anywhere. Every other signal agrees that the run was fine — and per this
    Sprint's earlier findings the exit code is 0 (BUG-36) and no Backup Log
    file is written (BUG-37), so nothing contradicts it. The one artifact that
    would betray it is `last_successful_backup` never advancing, which nothing
    surfaces.

    Reachability is a deployment-time question, not a code one. The Runner
    always creates the directory in lowercase, so a system that has only ever
    been driven by this code is safe. The case gets fixed on disk by whatever
    creates the directory FIRST: an operator making `Daily` by hand, a restore
    from an archive that normalised names, or a copy from another machine.
    docs/11's deployment steps have a human prepare directories, which is
    exactly that window.

    Not fixed: case-folding the comparison is one line, but it changes which
    files a backup includes — on a case-SENSITIVE filesystem `Daily/` and
    `daily/` are genuinely different directories, and deciding to merge them
    is a scope decision, not a cleanup.
    """

    def _master_with(self, directory_name):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        master = root / "master"
        (master / directory_name).mkdir(parents=True)

        # The Runner always writes to the lowercase path; on Windows this
        # resolves to the existing directory whatever its case.
        daily = master / "daily"
        daily.mkdir(parents=True, exist_ok=True)
        (daily / "2026-08-05.md").write_text(
            "# DOJOONPASS Company History\n", encoding="utf-8"
        )
        return master, root / "working_copy"

    def test_a_lowercase_directory_is_backed_up(self):
        """Baseline — the path the Runner itself creates."""
        from backup.working_copy import sync_to_working_copy

        master, working_copy = self._master_with("daily")

        result = sync_to_working_copy(master, working_copy)

        self.assertEqual(len(result.added), 1)
        self.assertTrue((working_copy / "daily" / "2026-08-05.md").exists())

    def test_the_file_really_is_written_either_way(self):
        """The data is not lost — only the backup misses it."""
        master, _ = self._master_with("Daily")

        written = list(master.rglob("2026-08-05.md"))

        self.assertEqual(len(written), 1)
        self.assertIn("Company History", written[0].read_text(encoding="utf-8"))

    def test_a_differently_cased_directory_is_excluded_from_backup(self):
        from backup.working_copy import _relative_files, sync_to_working_copy

        master, working_copy = self._master_with("Daily")

        self.assertEqual(_relative_files(master), set())

        result = sync_to_working_copy(master, working_copy)

        self.assertEqual(result.added, ())
        self.assertEqual(result.modified, ())
        self.assertEqual(result.deleted, ())
        self.assertEqual([p for p in working_copy.rglob("*") if p.is_file()], [])

    def test_the_scope_check_compares_case_sensitively(self):
        """The structural cause."""
        from backup.working_copy import _is_in_scope

        self.assertTrue(_is_in_scope("daily/2026-08-05.md"))
        self.assertFalse(_is_in_scope("Daily/2026-08-05.md"))
        self.assertFalse(_is_in_scope("MONTHLY/2026-08.md"))

    def test_scope_matching_is_otherwise_correct(self):
        """Everything else about the check is right — the finding is only
        about case, not about the matching rule."""
        from backup.working_copy import _is_in_scope

        for rel_path, expected in (
            ("daily/2026-08-05.md", True),
            ("monthly/2026-08.md", True),
            ("daily/sub/nested.md", True),
            ("decisions/d.md", False),
            ("stray.md", False),
            (".env", False),
            ("dailyx/a.md", False),
            ("x/daily/a.md", False),
        ):
            with self.subTest(rel_path=rel_path):
                self.assertEqual(_is_in_scope(rel_path), expected)


class CollectorLogCorrelationTests(unittest.TestCase):
    """BUG-56 (P3, NOT FIXED): the collector log keys success lines on
    event_id and every other line on filename, so a sanitised Event cannot be
    traced through the log.

    CHARACTERIZATION: asserts today's behaviour.

    collector/runtime.py logs:

        PROCESSING {path.name}          filename
        FAILED     {path.name}: ...     filename
        REJECTED   {path.name}: ...     filename
        ACCEPTED   {result.event.event_id}    event_id
        DUPLICATE  {result.event.event_id}    event_id

    That was consistent before this Sprint, because a file was always named
    `{event_id}.json`. The approved Event ID Sanitize change (CEO 승인 B안)
    deliberately broke that equality for ids that are not filesystem-safe —
    which is the point of it. The log was not updated to match.

    Measured:

        event_id "NORMAL-001"        PROCESSING NORMAL-001.json
                                     ACCEPTED   NORMAL-001            correlates

        event_id "../target/ESCAPED" PROCESSING target_ESCAPED-5f9f....json
                                     ACCEPTED   ../target/ESCAPED     no shared token

    So grepping the log for one identifier finds either the arrival or the
    outcome, never both — precisely for the Events whose ids were strange
    enough to need sanitising, which are the ones most likely to be under
    investigation.

    This is a consequence of an approved change, not a pre-existing defect:
    Event ID Sanitize traded filename fidelity for filesystem safety, which
    was correct, and the logging was simply not revisited.

    P3: log correlation only. No Event, History Candidate, or state is
    affected, and both facts ARE recorded — just not joinable.

    Not fixed: logging both identifiers on every line is the obvious answer
    but changes the log format operators may already grep, and the raw
    event_id in a log line is separately BUG-6 (log injection), so the two
    should be decided together.
    """

    def _collect_one(self, event_id):
        from collector import Collector, InMemorySeenEventStore
        from collector.runtime import run_once as collector_run
        from reporter.local_output import safe_event_filename

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        incoming = root / "incoming"
        incoming.mkdir()

        event = create_event(
            source="DESKTOP_1",
            role="COO",
            project_id="SEARCH_FRONTEND",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            summary="log correlation probe",
            milestone="M1",
            history_candidate=True,
            event_id=event_id,
            timestamp="2026-08-05T10:00:00+09:00",
        )
        filename = safe_event_filename(event_id)
        (incoming / filename).write_text(event.to_json(), encoding="utf-8")

        collector_run(
            collector=Collector(seen_store=InMemorySeenEventStore()),
            incoming_dir=incoming,
            processed_dir=root / "processed",
            rejected_dir=root / "rejected",
            log_path=root / "collector.log",
        )
        log = (root / "collector.log").read_text(encoding="utf-8")
        return filename, log

    def test_an_ordinary_event_correlates(self):
        """Baseline — filename and event_id agree, so the log joins up."""
        filename, log = self._collect_one("NORMAL-001")

        self.assertEqual(filename, "NORMAL-001.json")
        self.assertIn("PROCESSING NORMAL-001.json", log)
        self.assertIn("ACCEPTED NORMAL-001", log)

    def test_a_sanitised_event_does_not_correlate(self):
        event_id = "../target/ESCAPED"
        filename, log = self._collect_one(event_id)

        # The name really did change — that is Event ID Sanitize working.
        self.assertNotEqual(filename, f"{event_id}.json")

        self.assertIn(f"PROCESSING {filename}", log)
        self.assertIn(f"ACCEPTED {event_id}", log)

        # ...and the two lines share no identifier.
        stem = filename[: -len(".json")]
        self.assertNotIn(stem, f"ACCEPTED {event_id}")

    def test_the_two_line_kinds_use_different_keys(self):
        """The structural cause, so a refactor cannot lose the finding."""
        source = inspect.getsource(sys.modules["collector.runtime"])

        self.assertIn('f"PROCESSING {path.name}"', source)
        self.assertIn('f"ACCEPTED {result.event.event_id}"', source)
        self.assertIn('f"DUPLICATE {result.event.event_id}"', source)

    def test_both_facts_are_still_recorded(self):
        """Why this is P3: nothing is missing, only unjoinable."""
        event_id = "../target/ESCAPED"
        filename, log = self._collect_one(event_id)

        self.assertIn(filename, log)
        self.assertIn(event_id, log)


class ExceptionPropagationBoundaryTests(RunnerFailurePathTestCase):
    """Which stage failures abort the run, and whether the lock survives them.

    VERIFIED CORRECT — a system-level map nothing asserted, and the second
    half of it is what stops every crash-type defect in this Sprint from
    becoming permanent.

    Measured by making each stage's entry point raise:

        stage                    exception escapes run_once   lock afterwards
        Transport intake                    yes                  released
        Collector                           yes                  released
        History Filter                      yes                  released
        Scheduler / Daily                   yes                  released
        Backup                              yes                  released

    So the policy is consistent: every stage except Notion aborts the run, and
    Notion is the deliberate exception — `_sync_and_record` wraps the sync in
    `except Exception` and the Dashboard absorbs its own failures (CEO ④,
    README RULE 5), so a Notion outage never stops Company History.

    The lock column is the important half. `run_once` releases the lock in a
    `finally`, so a crash never leaves the Runner locked out. That is why
    BUG-40 (a nested JSON file aborting intake) repeats forever because the
    FILE is still there — not because the lock is stuck. Had the lock leaked,
    every one of this Sprint's crash findings would additionally have meant a
    permanently blocked Runner, and BUG-42 shows what that looks like: silent,
    total, and reported as success.

    A regression here would be invisible: the run still fails the same way,
    and only the NEXT run reveals the leaked lock.
    """

    STAGE_ENTRY_POINTS = (
        ("transport intake", "run_intake"),
        ("collector", "collector_run_once"),
        ("history filter", "HistoryFilter"),
        ("scheduler", "scheduler_run_once"),
        ("backup", "backup_run_once"),
    )

    def _run_with_failing_stage(self, attribute):
        original = getattr(runner_module, attribute)

        def boom(*args, **kwargs):
            raise RuntimeError(f"injected failure in {attribute}")

        setattr(runner_module, attribute, boom)
        self.addCleanup(setattr, runner_module, attribute, original)

        with self.assertRaises(RuntimeError):
            self._run()

    def test_every_stage_failure_aborts_the_run(self):
        for label, attribute in self.STAGE_ENTRY_POINTS:
            with self.subTest(stage=label):
                self.setUp()
                self._write_event(event_id=f"PROP-{label.replace(' ', '-')}")
                self._run_with_failing_stage(attribute)

    def test_every_stage_failure_still_releases_the_lock(self):
        """The property that keeps a crash from blocking all future runs."""
        for label, attribute in self.STAGE_ENTRY_POINTS:
            with self.subTest(stage=label):
                self.setUp()
                self._write_event(event_id=f"LOCK-{label.replace(' ', '-')}")
                self._run_with_failing_stage(attribute)

                self.assertFalse(
                    self.runner_lock_path.exists(),
                    f"a {label} failure leaked the Runner lock",
                )

    def test_the_lock_release_is_in_a_finally(self):
        """The structural reason, so a refactor cannot lose it."""
        source = inspect.getsource(runner_module.run_once)
        finally_at = source.rindex("finally:")

        self.assertIn("release_lock(runner_lock_path)", source[finally_at:])

    def test_a_later_run_still_works_after_a_stage_crash(self):
        """End to end: the crash costs one run, not the system."""
        original = runner_module.backup_run_once
        self._write_event(event_id="RECOVER-1")
        self._run_with_failing_stage("backup_run_once")

        # Restore the healthy code path explicitly — addCleanup only runs at
        # teardown, which is after this second run.
        runner_module.backup_run_once = original

        # Same workspace, no cleanup: the lock was released, so this proceeds.
        result = self._run(now=datetime(2026, 8, 2, 13, 0).astimezone())

        self.assertIsNotNone(result)
        self.assertFalse(self.runner_lock_path.exists())

    def test_notion_is_the_documented_exception(self):
        """Notion failures are contained rather than aborting — CEO ④."""
        source = inspect.getsource(runner_module.run_once)

        self.assertIn("sync_result = notion_sync.sync(event)", source)
        self.assertIn("except Exception as exc:  # noqa: BLE001", source)

    def test_a_regression_inside_the_dashboard_block_does_not_abort_a_successful_run(self):
        """CEO Decision ④ ("Dashboard 기록 실패는 Runtime을 절대 중단시키면
        안 된다") is honored today only because drain_pending() and
        dashboard_record_run() each promise, internally, never to raise. This
        call site trusted that promise with no defence of its own — unlike
        step 4's Notion Sync call, which wraps notion_sync.sync() in its own
        `except Exception` for the identical reason. Fault injection (a
        drain_pending() that raises, simulating a future regression in its
        own internal safety net) confirmed the gap: an otherwise fully
        successful run (History written, Backup pushed) was lost entirely.
        Hardened to match the Notion Sync call site's existing pattern."""
        self._write_event(event_id="DASHBOARD-DEFENSE-001")

        original = runner_module.drain_pending

        def exploding_drain(*args, **kwargs):
            raise RuntimeError("simulated regression inside drain_pending()")

        runner_module.drain_pending = exploding_drain
        self.addCleanup(setattr, runner_module, "drain_pending", original)

        dashboard_transport = InMemoryNotionTransport(
            initial_properties={"Run ID": {"type": "title", "title": {}}}
        )
        dashboard_client = NotionClient(transport=dashboard_transport, database_id="OPS_RUNS_DB")

        result = self._run(dashboard_client=dashboard_client)

        self.assertIsNotNone(result, "a Dashboard-block regression must not lose an otherwise successful run")
        backup_entry = result[3]
        self.assertEqual(backup_entry.final_status.value, "BACKUP_SUCCESS")


class BackupJunctionTraversalTests(unittest.TestCase):
    """BUG-57 (NOT FIXED): the backup scan follows directory links, so content
    from outside Local Master is copied into the Working Copy and pushed
    off-device.

    CHARACTERIZATION: asserts today's behaviour.

    `_relative_files()` walks `root.rglob("*")` and keeps anything for which
    `path.is_file()` is true. `is_file()` follows links by default, and
    `rglob` descends through linked directories, so the scan does not stay
    inside Local Master.

    Measured with a Windows junction — which, unlike a symlink, needs NO
    elevated privileges (`mklink /J` succeeds as an ordinary user):

        master/daily/linked  ->  <a directory outside Local Master>

        _relative_files()  ['daily\\linked\\secret.md', 'daily\\real.md']
        working copy       ['real.md', 'secret.md']

    So a file that was never in Local Master is committed to the Backup
    Working Copy and pushed to the off-device GitHub repository.

    This defeats a restriction docs/08 states deliberately. Sections 26-28
    limit the backup to `daily/` and `monthly/` and say stray files, `.env`,
    logs and caches under Master are "never looked at" — `_is_in_scope()`
    enforces that for path SHAPE (verified separately: `dailyx/`, `x/daily/`
    and `decisions/` are all correctly excluded). A junction bypasses it at
    the filesystem layer instead, and the scope check cannot see the
    difference because the relative path really does start with `daily`.

    The Secret Scan is not a backstop: it matches filenames only, and this
    Sprint measured it catching 3 of 12 realistic secrets (BUG-7).

    Not necessarily adversarial. Junctions are a normal Windows way to
    redirect a folder to another drive — someone moving `daily/` for disk
    space would silently widen what gets pushed, with nothing reporting it.

    Not fixed (junctions specifically): deciding whether a redirected
    `daily/` should be backed up AT ALL is a deployment policy question,
    not a code cleanup — refusing it would break a legitimate storage
    layout. Still true after this Sprint's `_relative_files()` change
    below.

    That same Sprint added an `is_symlink()` check to `_relative_files()`,
    but for a *different, narrower* bug (BUG, this Sprint: a single file
    under `daily/` symlinked to an external secret, invisible to the
    filename-only Secret Scan — no legitimate-storage-layout justification
    exists for that shape, unlike a whole-folder junction redirect). It
    does not touch this class's scenario: measured directly, a junction's
    `Path.is_symlink()` is `False` on Windows (junctions are NTFS reparse
    points, not POSIX-style symlinks), so `test_a_junction_pulls_outside_content_into_the_scan`
    and `test_that_content_is_copied_into_the_working_copy` below still
    pass — junction traversal is exactly as unfixed as before.
    """

    def _make_junction(self, link_path, target):
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link_path), str(target)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            self.skipTest(f"junction unavailable in this environment: {result.stderr.strip()}")

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.master = self.root / "master"
        (self.master / "daily").mkdir(parents=True)
        (self.master / "daily" / "real.md").write_text(
            "# a genuine Daily History file\n", encoding="utf-8"
        )
        self.outside = self.root / "outside"
        self.outside.mkdir()
        (self.outside / "secret.md").write_text(
            "content that was never in Local Master\n", encoding="utf-8"
        )

    def test_without_a_junction_only_master_content_is_scanned(self):
        """Baseline."""
        from backup.working_copy import _relative_files

        self.assertEqual(_relative_files(self.master), {str(Path("daily") / "real.md")})

    def test_a_junction_pulls_outside_content_into_the_scan(self):
        from backup.working_copy import _relative_files

        self._make_junction(self.master / "daily" / "linked", self.outside)

        found = _relative_files(self.master)

        self.assertIn(str(Path("daily") / "linked" / "secret.md"), found)

    def test_that_content_is_copied_into_the_working_copy(self):
        """The consequence: it reaches what gets committed and pushed."""
        from backup.working_copy import sync_to_working_copy

        self._make_junction(self.master / "daily" / "linked", self.outside)
        working_copy = self.root / "working_copy"

        sync_to_working_copy(self.master, working_copy)

        copied = {p.name for p in working_copy.rglob("*") if p.is_file()}
        self.assertIn("secret.md", copied)
        self.assertIn("real.md", copied)

    def test_the_scope_check_cannot_see_the_difference(self):
        """Why `_is_in_scope` does not stop it: the path genuinely starts
        with `daily`, so shape-based filtering has nothing to reject."""
        from backup.working_copy import _is_in_scope

        self.assertTrue(_is_in_scope(str(Path("daily") / "linked" / "secret.md")))

    def test_the_scan_follows_links_by_default(self):
        """The structural cause, for JUNCTIONS specifically: `is_file()`
        alone follows a junction, and `_relative_files()` now guards with
        `is_symlink()` (added this Sprint for the unrelated file-symlink
        bug above) -- which measured `False` for a junction, so that guard
        provides no protection here. `test_a_junction_pulls_outside_content_into_the_scan`
        is the direct behavioural proof; this test pins the source-level
        reason it still happens.
        """
        source = inspect.getsource(sys.modules["backup.working_copy"]._relative_files)

        self.assertIn("path.is_file()", source)
        self.assertNotIn("follow_symlinks=False", source)

    def test_no_walk_setting_would_have_prevented_the_descent(self):
        """Re-measured: `os.walk(followlinks=False)` — the obvious "just turn
        off link following" fix — still descends into a junction, because
        `followlinks` governs POSIX symlinks only.

        Pinned because it closes off a fix that looks free. Stopping the
        descent requires a hand-written walk that tests each directory
        before recursing, which is a larger change than it appears and is
        gated on the policy question in this class's docstring, not on
        finding the right flag.
        """
        outside = self.root / "outside_walk"
        outside.mkdir()
        (outside / "secret.md").write_text("s\n", encoding="utf-8")
        holder = self.root / "holder"
        (holder / "daily").mkdir(parents=True)
        # `_make_junction` skips the test itself if the machine cannot make one.
        self._make_junction(holder / "daily" / "linked", outside)

        walked = [
            os.path.join(dirpath, name)
            for dirpath, _dirnames, filenames in os.walk(holder, followlinks=False)
            for name in filenames
        ]

        self.assertTrue(
            any("secret.md" in path for path in walked),
            "premise changed: os.walk no longer descends into a junction",
        )

    def test_the_stdlib_can_identify_a_junction_even_though_is_symlink_cannot(self):
        """The detection primitive exists — `os.path.isjunction()`, Python
        3.12+ — so the reason this stays open is the policy question, not a
        missing capability. Worth pinning separately: "we cannot detect it"
        and "we have not decided whether to refuse it" are different
        statements, and only the second one is true.
        """
        outside = self.root / "outside_detect"
        outside.mkdir()
        holder = self.root / "holder_detect"
        holder.mkdir()
        link = holder / "linked"
        self._make_junction(link, outside)

        self.assertFalse(link.is_symlink())
        if hasattr(os.path, "isjunction"):
            self.assertTrue(os.path.isjunction(link))
        else:  # pragma: no cover - Python < 3.12
            attributes = getattr(link.lstat(), "st_file_attributes", 0)
            self.assertTrue(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


class NotionErrorBodyTests(unittest.TestCase):
    """BUG-58 (NOT FIXED): Notion's explanation of a failure is discarded,
    leaving only the HTTP status text.

    CHARACTERIZATION: asserts today's behaviour, including one property that
    is correct and must stay correct (no token in the message).

    `RealNotionTransport._request()` builds its error from `exc.code` and
    `exc.reason` only:

        raise NotionAPIError(f"Notion API returned {exc.code}: {exc.reason}")

    `urllib`'s HTTPError also carries the RESPONSE BODY, and Notion puts the
    actual reason there:

        {"object": "error", "status": 400, "code": "validation_error",
         "message": "Status is expected to be status."}

    Measured — the body is never read:

        produced   "Notion API returned 400: Bad Request"
        available  "Status is expected to be status."

    That one discarded sentence is the diagnosis for BUG-31 (a property whose
    TYPE does not match), which this Sprint could otherwise only find by
    reading source. The lost message then propagates: it becomes
    `SyncResult.error`, is written to notion_sync.log, and is what an operator
    sees on every retry — forever, since nothing caps retries (BUG-13/14).

    So this multiplies the cost of every Notion finding in this Sprint —
    BUG-31 (wrong property type), BUG-45 (health_check cannot see schema),
    BUG-51 (whitespace in the token), BUG-52's sibling on the Notion side.
    All of them surface as an opaque 400 or 401.

    VERIFIED CORRECT and pinned below: the API token never appears in the
    error message. Widening the message to include the body must not change
    that — Notion's error body does not echo the token, but a naive "include
    everything" fix could start including request details.

    FIXED. `_error_detail()` now appends Notion's own message, bounded to
    400 characters and wrapped so that a failure to read the body cannot
    itself raise. The retry queue stores the Event rather than the error, and
    `_log_notion_sync()` writes only the status value, so the widened message
    reaches the operator's console without entering either the queue file or
    notion_sync.log — which is why the log-injection concern (BUG-6) does not
    apply to it.
    """

    ERROR_BODY = {
        "object": "error",
        "status": 400,
        "code": "validation_error",
        "message": "Status is expected to be status.",
    }

    def _transport(self, token="ntn_secret_token_value"):
        from notion.transport import RealNotionTransport

        transport = RealNotionTransport.__new__(RealNotionTransport)
        transport._base_url = "https://api.notion.com/v1"
        transport._api_token = token
        transport._timeout = 10.0
        return transport

    def _request_raising(self, status, reason, body):
        import io
        import json as json_module
        import urllib.error
        import urllib.request

        real_urlopen = urllib.request.urlopen

        def fake_urlopen(request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url,
                status,
                reason,
                {},
                io.BytesIO(json_module.dumps(body).encode("utf-8")),
            )

        urllib.request.urlopen = fake_urlopen
        self.addCleanup(setattr, urllib.request, "urlopen", real_urlopen)

    def test_the_response_body_now_reaches_the_error(self):
        """FIXED. This asserted the loss; now it asserts the diagnosis.

        The message Notion supplies is the whole diagnosis for a rejected
        property, and it used to be dropped on the floor — an operator saw
        "Bad Request" on every retry, forever, with no way to learn which
        property was wrong.
        """
        from notion.transport import NotionAPIError

        self._request_raising(400, "Bad Request", self.ERROR_BODY)

        with self.assertRaises(NotionAPIError) as caught:
            self._transport()._request("POST", "/pages", {"x": 1})

        self.assertIn("Notion API returned 400: Bad Request", str(caught.exception))
        self.assertIn("Status is expected to be status", str(caught.exception))

    def test_the_status_code_is_preserved(self):
        """What IS kept — enough to classify, not enough to diagnose."""
        from notion.transport import NotionAPIError

        self._request_raising(400, "Bad Request", self.ERROR_BODY)

        with self.assertRaises(NotionAPIError) as caught:
            self._transport()._request("POST", "/pages", {"x": 1})

        self.assertEqual(caught.exception.status_code, 400)

    def test_the_api_token_never_appears_in_the_error(self):
        """VERIFIED CORRECT — a security property any fix must preserve."""
        from notion.transport import NotionAPIError

        token = "ntn_secret_token_value"
        self._request_raising(401, "Unauthorized", {"message": "API token is invalid."})

        with self.assertRaises(NotionAPIError) as caught:
            self._transport(token)._request("POST", "/pages", {"x": 1})

        self.assertNotIn(token, str(caught.exception))
        self.assertNotIn("Bearer", str(caught.exception))

    def test_the_handler_reads_the_body_through_a_bounded_helper(self):
        """The structural half of the fix.

        Reading the body must stay bounded and must never turn one failure
        into two — an error path that can itself raise is worse than the
        opaque message it replaced. Both properties live in
        `_error_detail()`, so the handler is expected to delegate rather
        than inline an `exc.read()`.
        """
        import notion.transport as transport_module

        source = inspect.getsource(transport_module.RealNotionTransport._request)
        self.assertIn("exc.reason", source)
        self.assertIn("_error_detail(exc)", source)

        helper = inspect.getsource(transport_module._error_detail)
        self.assertIn("exc.read()", helper)
        self.assertIn("_MAX_ERROR_DETAIL", helper)
        self.assertIn("except", helper)


class NetworkFailureTransportTests(unittest.TestCase):
    """Coverage gap found via `python -m trace` this Sprint: `_request()`'s
    `except urllib.error.URLError` branch (docs/11 section 62's "장애 —
    Internet": DNS failure / connection refused / offline) had zero test
    coverage -- every existing transport test drives the `HTTPError`
    branch (a real HTTP response, just a bad one) via `NotionErrorBodyTests`
    above, never the lower-level "could not even connect" branch.

    VERIFIED CORRECT: `URLError` is converted to `NotionAPIError` the same
    way `HTTPError` is (docs/04 section 66-5's "Notion 실패해도 Runner는
    계속 진행" relies on every Notion failure surfacing as this one
    exception type), and `status_code` stays `None` since `URLError` --
    unlike `HTTPError` -- never has an HTTP status at all.
    """

    def _transport(self):
        from notion.transport import RealNotionTransport

        transport = RealNotionTransport.__new__(RealNotionTransport)
        transport._base_url = "https://api.notion.com/v1"
        transport._api_token = "ntn_test_token"
        transport._timeout = 10.0
        return transport

    def _request_raising_url_error(self, reason):
        import urllib.error
        import urllib.request

        real_urlopen = urllib.request.urlopen

        def fake_urlopen(request, timeout=None):
            raise urllib.error.URLError(reason)

        urllib.request.urlopen = fake_urlopen
        self.addCleanup(setattr, urllib.request, "urlopen", real_urlopen)

    def test_a_connection_failure_becomes_a_notion_api_error(self):
        from notion.transport import NotionAPIError

        self._request_raising_url_error("[Errno 11001] getaddrinfo failed")

        with self.assertRaises(NotionAPIError) as caught:
            self._transport()._request("POST", "/pages", {"x": 1})

        self.assertIn("Notion API request failed", str(caught.exception))
        self.assertIn("getaddrinfo failed", str(caught.exception))

    def test_a_connection_failure_has_no_status_code(self):
        """Unlike HTTPError, URLError never carries an HTTP status -- a
        caller that assumes `status_code` is always set (e.g. to decide
        retry-vs-fail) would see None here, not a made-up value."""
        from notion.transport import NotionAPIError

        self._request_raising_url_error("Connection refused")

        with self.assertRaises(NotionAPIError) as caught:
            self._transport()._request("GET", "/databases/x")

        self.assertIsNone(caught.exception.status_code)

    def test_the_api_token_never_appears_in_a_connection_failure_error(self):
        from notion.transport import NotionAPIError

        self._request_raising_url_error("Connection refused")

        with self.assertRaises(NotionAPIError) as caught:
            self._transport()._request("POST", "/pages", {"x": 1})

        self.assertNotIn("ntn_test_token", str(caught.exception))

    def test_the_api_version_is_pinned(self):
        """Unrelated but verified while here: the Notion-Version header is a
        fixed date, so a server-side API change cannot silently alter
        behaviour."""
        import notion.transport as transport_module

        self.assertEqual(transport_module.NOTION_API_VERSION, "2022-06-28")


class LockFailurePathTests(RunnerFailurePathTestCase):
    """docs/07 sections 24-28. These already behave correctly; the tests pin
    the behaviour so a future change cannot weaken mutual exclusion silently.
    """

    def test_lock_held_by_a_live_process_skips_the_run(self):
        import os

        self.runner_lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.runner_lock_path.write_text(
            json.dumps(
                {
                    "process_id": os.getpid(),
                    "created_at": datetime(2026, 8, 2, 11, 0).astimezone().isoformat(),
                }
            ),
            encoding="utf-8",
        )

        self.assertIsNone(self._run())
        # A live holder's lock must survive the skipped run.
        self.assertTrue(self.runner_lock_path.exists())

    def test_stale_lock_from_a_dead_process_is_taken_over(self):
        self.runner_lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.runner_lock_path.write_text(
            json.dumps({"process_id": 999999, "created_at": "2020-01-01T00:00:00+09:00"}),
            encoding="utf-8",
        )

        self.assertIsNotNone(self._run())
        self.assertFalse(self.runner_lock_path.exists())

    def test_unparseable_lock_file_is_treated_as_stale(self):
        self.runner_lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.runner_lock_path.write_text("!!! not json !!!", encoding="utf-8")

        self.assertIsNotNone(self._run())
        self.assertFalse(self.runner_lock_path.exists())


class NotionSyncLogWriteFailureTests(unittest.TestCase):
    """`_log_notion_sync()`'s `except OSError: pass` had zero test coverage
    across this entire suite (found via `python -m trace --count`, not by
    reading — no existing test drives a log write failure). Verified here
    directly: a write failure while logging one Notion Sync result must not
    propagate, matching every other "diagnostic logging must not block the
    Runtime" guarantee already tested elsewhere (collector/runtime.py's own
    log, dashboard recording)."""

    def test_a_log_write_failure_is_swallowed_not_propagated(self):
        from app.runner import _log_notion_sync
        from notion.sync import SyncResult, SyncStatus

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)

        # log_path's PARENT is a file, not a directory -- mkdir(parents=True)
        # raises FileExistsError (an OSError subclass) before any write is
        # attempted.
        blocker_file = root / "logs"
        blocker_file.write_text("not a directory", encoding="utf-8")
        bad_log_path = blocker_file / "notion_sync.log"

        sync_result = SyncResult(status=SyncStatus.NOTION_CREATED, event_id="E1", project_id="P1")

        try:
            _log_notion_sync(bad_log_path, sync_result)
        except OSError:
            self.fail("_log_notion_sync() must swallow OSError, not propagate it")


class BackupFailurePresentationTests(unittest.TestCase):
    """The other half of BUG-4.

    `app/runner.py` still propagates a Backup `GitOperationError` — the
    Runner's return tuple has no shape for "Backup failed", and inventing
    one is a contract decision. That characterization stands above.

    What WAS fixable is what the operator sees. docs/08 section 19 calls a
    failed push routine and recoverable, yet `run_company_ops.py` answered
    it with a raw Python traceback — which reads like the system broke, when
    in fact Backup runs last and every earlier stage is already durable.
    Nothing about the Runner's contract changes here; only the CLI's
    presentation of an exception it already received.
    """

    def _entrypoint(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "run_company_ops.py"
        spec = importlib.util.spec_from_file_location("run_company_ops_probe", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _report(self, message):
        import io
        import contextlib

        module = self._entrypoint()
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            code = module._report_backup_failure(GitOperationError(message))
        return code, buffer.getvalue()

    def test_a_transient_failure_exits_two_not_with_a_traceback(self):
        code, _ = self._report("git push failed (exit 128): Could not read from remote")

        self.assertEqual(code, 2)

    def test_the_operator_is_told_no_data_was_lost(self):
        """Backup runs last; History is already on disk. That is the single
        most important thing to say and it was not being said."""
        _, output = self._report("git push failed (exit 128): connection reset")

        self.assertIn("유실된 데이터는 없습니다", output)

    def test_a_transient_failure_says_the_next_run_retries(self):
        _, output = self._report("git push failed (exit 128): connection reset")

        self.assertIn("BACKUP_PENDING", output)
        self.assertIn("따로 할 일은 없습니다", output)

    def test_an_authentication_failure_says_a_human_must_act(self):
        """docs/08 section 21/62: retrying a credential problem on a schedule
        cannot fix it. Telling the operator "nothing to do" would be wrong."""
        _, output = self._report(
            "git push failed (exit 128): fatal: Authentication failed for 'https://github.com'"
        )

        self.assertIn("BACKUP_FAILED", output)
        self.assertIn("자격증명", output)
        self.assertNotIn("따로 할 일은 없습니다", output)

    def test_the_classification_matches_what_backup_recorded(self):
        """The message must agree with the persisted state, or the operator
        is told one thing while backup_state.json says another."""
        module = self._entrypoint()

        for message, expected_permanent in (
            ("git push failed: Authentication failed", True),
            ("git push failed: could not read Username", True),
            ("git push failed: Could not resolve host", False),
            ("git push timed out after 300s", False),
        ):
            with self.subTest(message=message):
                self.assertEqual(
                    module.is_authentication_failure(message), expected_permanent
                )

    def test_the_original_git_error_is_still_shown(self):
        """Explaining the situation must not hide what git actually said."""
        _, output = self._report("git push failed (exit 128): some very specific reason")

        self.assertIn("some very specific reason", output)

    def test_the_operator_is_pointed_at_the_status_command(self):
        _, output = self._report("git push failed (exit 128): x")

        self.assertIn("ops_status.py", output)


class MonthlyStepIsolationTests(RunnerFailurePathTestCase):
    """docs/09 §74: a Monthly failure must not stop the Runtime — and §44
    says it must be recorded.

    The Runner absorbs the Monthly step's exceptions so that Backup still
    runs and the run still completes. What it did not do was leave any
    trace: `monthly_run_once()`'s own PENDING/FAILED results were logged,
    but an unexpected exception escaping that call was swallowed with
    `pass`. Monthly simply did not happen and nothing said so.
    """

    def _explode_monthly(self, exc):
        import app.runner as runner_module

        original = runner_module.monthly_run_once

        def exploding(**kwargs):
            raise exc

        runner_module.monthly_run_once = exploding
        self.addCleanup(setattr, runner_module, "monthly_run_once", original)

    def _late_update_log(self) -> str:
        path = self.notion_sync_log_path.parent / "daily_late_update.log"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def test_a_monthly_explosion_does_not_stop_the_run(self):
        self._write_event(event_id="FAILPATH-MONTHLY-001")
        self._explode_monthly(RuntimeError("monthly blew up"))

        result = self._run()

        self.assertIsNotNone(result, "the Runner aborted on a Monthly failure")
        self.assertEqual(result[1].accepted, 1)

    def test_backup_still_runs_after_a_monthly_explosion(self):
        """Monthly sits between Daily and Backup; swallowing its failure is
        only correct if Backup genuinely still happens."""
        self._write_event(event_id="FAILPATH-MONTHLY-002")
        self._explode_monthly(RuntimeError("monthly blew up"))

        result = self._run()

        self.assertEqual(result[3].final_status, BackupStatus.SUCCESS)

    def test_history_and_daily_survive_a_monthly_explosion(self):
        self._write_event(event_id="FAILPATH-MONTHLY-003")
        self._explode_monthly(RuntimeError("monthly blew up"))

        self._run()

        self.assertTrue((self.keep_dir / "HIST-FAILPATH-MONTHLY-003.json").exists())
        self.assertTrue((self.local_master_dir / "daily" / "2026-08-01.md").exists())

    def test_the_explosion_is_recorded_rather_than_silently_swallowed(self):
        self._write_event(event_id="FAILPATH-MONTHLY-004")
        self._explode_monthly(RuntimeError("monthly blew up"))

        self._run()

        log = self._late_update_log()
        self.assertIn("MONTHLY_FAILED (unexpected)", log)
        self.assertIn("RuntimeError", log)
        self.assertIn("monthly blew up", log)

    def test_a_state_error_from_the_dirty_marker_is_also_absorbed(self):
        """`mark_month_dirty()` runs inside the same block and reads state;
        a corrupted monthly_history_state.json must not end the run."""
        import app.runner as runner_module

        original = runner_module.mark_month_dirty

        def exploding(*args, **kwargs):
            raise ValueError("monthly state file is corrupted")

        runner_module.mark_month_dirty = exploding
        self.addCleanup(setattr, runner_module, "mark_month_dirty", original)

        self._write_event(event_id="FAILPATH-MONTHLY-005")
        result = self._run()

        self.assertIsNotNone(result)
        self.assertEqual(result[3].final_status, BackupStatus.SUCCESS)

    def test_a_normal_run_writes_no_unexpected_failure_line(self):
        self._write_event(event_id="FAILPATH-MONTHLY-006")

        self._run()

        self.assertNotIn("(unexpected)", self._late_update_log())


class SchedulerFailureDiagnosticsTests(RunnerFailurePathTestCase):
    """BUG-39's sharpest instance, fixed.

    `SchedulerRunResult` always carried `failed_date` and `error`, and no
    consumer read either. A failed Daily Close printed

        Daily History (Scheduler): FAILED, generated=[]

    and stopped there. Scheduler closes dates in order and stops at the first
    failure (docs section 30, so as not to leave a gap in the sequence), which
    means that date and every later one still have no Daily file. The two
    discarded fields were the only record of where the next run resumes —
    the single most useful thing to know, computed correctly and thrown away.

    Now written to daily_late_update.log, where Monthly failures already go.
    Deliberately NOT a new run-summary artifact: BUG-39's general fix needs a
    format and location decision and stays open.
    """

    def _late_update_log(self) -> str:
        path = self.notion_sync_log_path.parent / "daily_late_update.log"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def _break_daily_generation(self, exc):
        """Make the Daily Close fail for a date the Scheduler will attempt."""
        import scheduler.scheduler as scheduler_module

        original = scheduler_module.generate_daily_history

        def exploding(*args, **kwargs):
            raise exc

        scheduler_module.generate_daily_history = exploding
        self.addCleanup(
            setattr, scheduler_module, "generate_daily_history", original
        )

    def test_the_failing_date_and_reason_reach_the_log(self):
        self._write_event(event_id="FAILPATH-SCHED-001")
        self._break_daily_generation(RuntimeError("daily close blew up"))

        result = self._run()

        self.assertIsNotNone(result)
        self.assertEqual(result[2].status.value, "FAILED")

        log = self._late_update_log()
        self.assertIn("SCHEDULER_FAILED", log)
        # The date the run must resume from — 2026-08-01 is history_start_date.
        self.assertIn("date=2026-08-01", log)
        self.assertIn("daily close blew up", log)

    def test_the_run_still_completes_and_the_candidate_is_safe(self):
        """The failure is recorded, not escalated.

        History Filter (step 5) runs before the Daily Close, so the Candidate
        is already durable when the close dies — which is why this failure is
        recoverable at all: the next run regenerates the Daily file from it.
        Backup reports NOT_REQUIRED rather than SUCCESS precisely because the
        close produced no Daily file to commit; it ran, and correctly found
        nothing.
        """
        self._write_event(event_id="FAILPATH-SCHED-002")
        self._break_daily_generation(RuntimeError("daily close blew up"))

        result = self._run()

        self.assertIsNotNone(result)
        self.assertTrue((self.keep_dir / "HIST-FAILPATH-SCHED-002.json").exists())
        self.assertEqual(result[3].final_status, BackupStatus.NOT_REQUIRED)
        # And the lock is not stranded by the failing step.
        self.assertFalse(self.runner_lock_path.exists())

    def test_a_successful_close_writes_no_scheduler_failure_line(self):
        self._write_event(event_id="FAILPATH-SCHED-003")

        self._run()

        self.assertNotIn("SCHEDULER_FAILED", self._late_update_log())

    def test_an_unbounded_scheduler_error_is_truncated(self):
        """Same rule as the Notion path: an error string of unknown origin
        does not get to choose how many bytes it writes, once per run."""
        self._write_event(event_id="FAILPATH-SCHED-004")
        self._break_daily_generation(RuntimeError("z" * 5000))

        self._run()

        line = next(
            ln for ln in self._late_update_log().splitlines() if "SCHEDULER_FAILED" in ln
        )
        self.assertTrue(line.endswith("..."), "the scheduler error was not truncated")
        self.assertLess(len(line), runner_module._MAX_LOG_ERROR + 200)

    def test_a_scheduler_error_cannot_forge_a_log_line(self):
        self._write_event(event_id="FAILPATH-SCHED-005")
        self._break_daily_generation(
            RuntimeError("\n2026-01-01T00:00:00+09:00 LATE_UPDATE FORGED")
        )

        self._run()

        forged = [
            ln
            for ln in self._late_update_log().splitlines()
            if ln.startswith("2026-01-01T00:00:00+09:00")
        ]
        self.assertEqual(forged, [], "a forged late-update line got through")


class SyncFailureReasonLoggingTests(RunnerFailurePathTestCase):
    """docs/04 §55 fixes the *minimum* a sync log line carries. For a failed
    sync that minimum is not diagnosable.

    `NOTION_RETRY_REQUIRED` is reported identically for a 503 that will clear
    on its own and a 400 that never will (BUG-13) — the sentence separating
    them lived only in `SyncResult.error`, which reaches `run_company_ops.py`'s
    stdout and the return tuple but not the log. Under a scheduler, which is
    how this Runner actually runs in production, both of those are gone: an
    operator watching the Retry Queue re-send the same Event every run had no
    way to learn why.
    """

    def _sync_log(self) -> str:
        if not self.notion_sync_log_path.exists():
            return ""
        return self.notion_sync_log_path.read_text(encoding="utf-8")

    # Event ids below deliberately avoid the substring "REASON": an id
    # containing the field name would make `assertNotIn("REASON", log)` pass
    # or fail for the wrong reason.
    class _StatusTransport(InMemoryNotionTransport):
        """Fails `query_database` with a caller-chosen status and message,
        so a permanent 4xx and a transient 5xx can be told apart in a test
        the way an operator must tell them apart in a log."""

        def __init__(self, *, status_code, message):
            super().__init__()
            self._status_code = status_code
            self._message = message

        def query_database(self, database_id, filter_):
            raise NotionAPIError(self._message, status_code=self._status_code)

    def test_a_failed_sync_records_the_reason_not_only_the_status(self):
        transport = InMemoryNotionTransport()
        transport.fail_next_method = "query_database"
        sync = ExecutionPlanSync(
            client=NotionClient(transport=transport, database_id="DB-1")
        )

        self._write_event(event_id="FAILPATH-RSN-001")
        self._run(notion_sync=sync)

        log = self._sync_log()
        self.assertIn("NOTION_RESULT NOTION_RETRY_REQUIRED", log)
        self.assertIn("REASON", log)
        self.assertIn("simulated Notion API failure", log)

    def test_a_permanent_rejection_is_distinguishable_from_a_transient_one(self):
        """The whole point of BUG-13: both are NOTION_RETRY_REQUIRED, so the
        status alone cannot tell an operator to stop waiting and go fix the
        payload. The reason can."""
        permanent = ExecutionPlanSync(
            client=NotionClient(
                transport=self._StatusTransport(
                    status_code=400,
                    message=(
                        "Notion API returned 400: Bad Request | "
                        '{"message":"body.properties.Status is not a valid select"}'
                    ),
                ),
                database_id="DB-1",
            )
        )
        self._write_event(event_id="FAILPATH-PERM-001")
        self._run(notion_sync=permanent)
        after_permanent = self._sync_log()

        transient = ExecutionPlanSync(
            client=NotionClient(
                transport=self._StatusTransport(
                    status_code=503,
                    message="Notion API returned 503: Service Unavailable",
                ),
                database_id="DB-1",
            )
        )
        self._write_event(event_id="FAILPATH-TRAN-001")
        self._run(notion_sync=transient)
        # Second run's lines only. It logs three: the queued PERM-001 retried
        # first (Retry Queue 우선 처리), then the fresh TRAN-001 — the queued
        # one now failing against the *transient* transport, which is why this
        # test reads each run's slice rather than counting the whole file.
        after_transient = self._sync_log()[len(after_permanent) :]

        # Same status for both — that is the defect the reason works around.
        self.assertIn("NOTION_RESULT NOTION_RETRY_REQUIRED", after_permanent)
        self.assertIn("NOTION_RESULT NOTION_RETRY_REQUIRED", after_transient)
        # Different reasons — that is what makes them actionable.
        self.assertIn("is not a valid select", after_permanent)
        self.assertNotIn("503", after_permanent)
        self.assertIn("503: Service Unavailable", after_transient)

    def test_a_successful_sync_adds_no_reason_field(self):
        """§55's format stays clean on the path that has nothing to explain."""
        sync = ExecutionPlanSync(
            client=NotionClient(transport=InMemoryNotionTransport(), database_id="DB-1")
        )

        self._write_event(event_id="FAILPATH-OK-001")
        self._run(notion_sync=sync)

        log = self._sync_log()
        self.assertIn("NOTION_RESULT NOTION_CREATED", log)
        self.assertNotIn("REASON", log)

    def test_an_unexpected_sync_exception_is_logged_and_bounded(self):
        """The `except Exception` fallback is the one path whose message no
        other layer has already truncated. It must not be able to write an
        unbounded string to disk once per Event, per run, forever."""

        class ExplodingSync:
            def sync(self, event):
                raise RuntimeError("boom " + "y" * 5000)

        self._write_event(event_id="FAILPATH-REASON-004")
        self._run(notion_sync=ExplodingSync())

        line = self._sync_log().strip()
        self.assertIn("NOTION_RESULT NOTION_FAILED", line)
        self.assertIn("REASON RuntimeError: boom", line)
        self.assertTrue(line.endswith("..."), f"reason was not truncated: {line[-80:]}")
        # Derived from the bound, not a number copied next to it: a magic
        # constant here would have to be re-tuned every time the bound moves,
        # and would pass for the wrong reason if it were ever raised.
        self.assertLess(
            len(line),
            runner_module._MAX_LOG_ERROR + 200,
            "an unbounded exception reached the log",
        )

    def test_a_reason_containing_a_newline_cannot_forge_a_line(self):
        """The reason is the newest untrusted-ish field on this line — a
        Notion error body is echoed back from a remote server. It goes
        through the same single escaping point as every other field."""

        class ForgingSync:
            def sync(self, event):
                raise RuntimeError(
                    "\n2026-01-01T00:00:00+09:00 EVENT FORGED PROJECT P "
                    "NOTION_RESULT NOTION_CREATED"
                )

        self._write_event(event_id="FAILPATH-REASON-005")
        self._run(notion_sync=ForgingSync())

        lines = self._sync_log().splitlines()
        self.assertEqual(len(lines), 1, f"a forged line got through: {lines}")
        self.assertIn("\\n2026-01-01T00:00:00+09:00 EVENT FORGED", lines[0])


class DashboardStepDiagnosticsTests(RunnerFailurePathTestCase):
    """CEO Decision ④ keeps a Dashboard failure non-fatal. It did not ask
    for it to be *invisible*, which is what the step actually was.

    The Dashboard is this system's metrics sink, so it is the one step whose
    silence is self-concealing: when it stops recording, the place an
    operator would look to notice that is the very thing that stopped. Three
    paths reached no sink at all — not stdout, not the return tuple, not a
    log:

        record_run() -> FAILED        answered only by a silent re-queue
        an unexpected exception       answered by a bare `pass`
        a growing pending backlog     drain_pending()'s counts discarded

    Same treatment the Monthly step above already got: still absorbed, now
    recorded. Written to notion_sync.log under a `DASHBOARD` prefix — the
    Dashboard is a Notion-side concern and docs/04 §55's log already carries
    every other Notion step's outcome.
    """

    def _dashboard_log_lines(self) -> list[str]:
        if not self.notion_sync_log_path.exists():
            return []
        return [
            line
            for line in self.notion_sync_log_path.read_text(encoding="utf-8").splitlines()
            if " DASHBOARD " in line
        ]

    def _client(self, transport=None) -> NotionClient:
        return NotionClient(
            transport=transport or InMemoryNotionTransport(), database_id="ops-runs-db"
        )

    def test_a_failed_record_run_is_logged_not_only_requeued(self):
        """The failure an operator most needs to see: Notion rejected the
        row. Before, the only trace was a line silently appended to
        dashboard_pending.json."""
        transport = InMemoryNotionTransport()
        transport.fail_next_method = "create_page"

        self._write_event(event_id="FAILPATH-DASHLOG-001")
        result = self._run(
            dashboard_client=self._client(transport), run_id="RUN-DASHLOG-1"
        )

        # Absorbed: the run still finished and Backup still succeeded.
        self.assertIsNotNone(result)
        self.assertEqual(result[3].final_status, BackupStatus.SUCCESS)

        lines = self._dashboard_log_lines()
        self.assertTrue(lines, "the Dashboard failure left no trace at all")
        self.assertIn("FAILED run_id=RUN-DASHLOG-1", lines[-1])
        self.assertIn("simulated Notion API failure", lines[-1])

        # And it is still queued for retry — logging replaces nothing.
        self.assertTrue(self.dashboard_pending_path.exists())

    def test_an_unexpected_dashboard_exception_is_logged_not_swallowed(self):
        """The `except Exception: pass` path. `record_run()` documents
        "Never raises", so reaching here means that contract itself
        regressed — precisely the case where a silent `pass` is worst."""
        import app.runner as runner_module

        original = runner_module.dashboard_record_run

        def exploding(*args, **kwargs):
            raise RuntimeError("dashboard blew up")

        runner_module.dashboard_record_run = exploding
        self.addCleanup(setattr, runner_module, "dashboard_record_run", original)

        self._write_event(event_id="FAILPATH-DASHLOG-002")
        result = self._run(dashboard_client=self._client(), run_id="RUN-DASHLOG-2")

        self.assertIsNotNone(result)
        self.assertEqual(result[3].final_status, BackupStatus.SUCCESS)

        joined = "\n".join(self._dashboard_log_lines())
        self.assertIn("FAILED (unexpected)", joined)
        self.assertIn("RuntimeError", joined)
        self.assertIn("dashboard blew up", joined)
        self.assertIn("run_id=RUN-DASHLOG-2", joined)

    def test_a_pending_backlog_is_reported_instead_of_growing_unseen(self):
        """drain_pending() already returned (recorded, still_pending); the
        Runner discarded both. A backlog that fails every run is exactly the
        condition those counts exist to surface."""
        from notion.dashboard_pending import save_pending as save_dashboard_pending

        save_dashboard_pending(
            self.dashboard_pending_path,
            run_id="RUN-OLD-1",
            properties={"Run ID": {"title": [{"text": {"content": "RUN-OLD-1"}}]}},
        )

        transport = InMemoryNotionTransport()
        transport.fail_next_method = "create_page"  # fails the drained record

        self._write_event(event_id="FAILPATH-DASHLOG-003")
        self._run(dashboard_client=self._client(transport), run_id="RUN-DASHLOG-3")

        joined = "\n".join(self._dashboard_log_lines())
        self.assertIn("DRAIN_PENDING drained=0 still_pending=1", joined)

    def test_a_successful_drain_is_reported_too(self):
        """Recovery is as worth seeing as failure: it tells an operator the
        backlog they were watching is gone."""
        from notion.dashboard_pending import save_pending as save_dashboard_pending

        save_dashboard_pending(
            self.dashboard_pending_path,
            run_id="RUN-OLD-2",
            properties={"Run ID": {"title": [{"text": {"content": "RUN-OLD-2"}}]}},
        )

        self._write_event(event_id="FAILPATH-DASHLOG-004")
        self._run(dashboard_client=self._client(), run_id="RUN-DASHLOG-4")

        joined = "\n".join(self._dashboard_log_lines())
        self.assertIn("DRAIN_PENDING drained=1 still_pending=0", joined)

    def test_a_healthy_run_writes_no_dashboard_lines(self):
        """The log must stay a signal. A run where the Dashboard worked and
        nothing was queued says nothing — otherwise every run would add
        noise and the failure lines above would stop standing out."""
        self._write_event(event_id="FAILPATH-DASHLOG-005")

        self._run(dashboard_client=self._client(), run_id="RUN-DASHLOG-5")

        self.assertEqual(self._dashboard_log_lines(), [])

    def test_dashboard_logging_survives_an_unwritable_log_path(self):
        """Logging must never be the thing that fails a run — the rule
        `_append_log_line()` inherits from collector/runtime.py::_log().
        Here the log path is a *directory*, so opening it raises OSError."""
        self.notion_sync_log_path.mkdir(parents=True, exist_ok=True)

        transport = InMemoryNotionTransport()
        transport.fail_next_method = "create_page"

        self._write_event(event_id="FAILPATH-DASHLOG-006")
        result = self._run(
            dashboard_client=self._client(transport), run_id="RUN-DASHLOG-6"
        )

        self.assertIsNotNone(result)
        self.assertEqual(result[3].final_status, BackupStatus.SUCCESS)


if __name__ == "__main__":
    unittest.main()


class ConsumedEventWithoutCandidateTests(RunnerFailurePathTestCase):
    """BUG-20's loss mechanism, reachable with NO concurrency (NOT FIXED).

    CHARACTERIZATION: asserts today's behaviour.

    BUG-20 measured 36% of History Candidates permanently lost under three
    concurrent Runners, and named three composing defects. Two of them —
    BUG-18/BUG-19's lock race — were fixed by O_EXCL lock atomicity, and
    that fix holds. But the third link in the chain is a property of the
    pipeline's ordering, not of concurrency:

        Collector marks the event_id seen and moves the file to processed/
        ...
        step 5 writes the History Candidate

    Anything that ends the run between those two points loses the Event
    from Company History permanently. BUG-20 reached that window through a
    lock race. Measured here by crashing `HistoryFilter.evaluate()` on a
    single Runner with an empty lock directory:

        processed/fi-crash.json          exists   (Event survived)
        history_candidates/keep/         empty    (Candidate never written)
        next run: accepted=0                      (never reconsidered)
        Daily History                    absent   (permanently)

    So "BUG-20 is fixed" is true of the concurrency trigger and false of the
    loss window. The Run Manifest does report the run as FAILED with the
    aborting component named, so an operator is told *that* something broke
    — but nothing names which Event was consumed without being recorded, and
    re-running does not recover it.

    Why not fixed here: closing it means either persisting the Candidate
    before/atomically with `mark_seen()` (a Collector contract change) or
    adding a reconciliation pass over `processed/` for Events with no
    Candidate (a new recovery mechanism, i.e. new architecture). Both are
    decisions, not cleanups. Recorded in BACKLOG as A-20.

    This test exists so the claim "데이터 유실 0" is qualified by the one
    window where it is not true, rather than resting on a fixed trigger.
    """

    def _crash_history_filter(self):
        original = runner_module.HistoryFilter

        class Exploding(original):
            def evaluate(self, event):
                raise RuntimeError("history filter crashed mid-pipeline")

        runner_module.HistoryFilter = Exploding
        self.addCleanup(setattr, runner_module, "HistoryFilter", original)

    def test_the_event_is_consumed_before_the_candidate_is_written(self):
        """The ordering that creates the window, stated directly."""
        self._write_event(event_id="LOSS-WINDOW-1")
        self._crash_history_filter()

        with self.assertRaises(RuntimeError):
            self._run()

        # Consumed: the file left incoming/ and reached processed/.
        self.assertEqual(list(self.incoming_dir.glob("*.json")), [])
        self.assertEqual(len(list(self.processed_dir.glob("*.json"))), 1)
        # But no Candidate exists for it.
        self.assertFalse((self.keep_dir / "HIST-LOSS-WINDOW-1.json").exists())

    def test_a_later_run_does_not_reconsider_it(self):
        """The reason the loss is permanent rather than merely delayed: the
        event_id is already in the seen store, so the Event is never
        re-collected even though nothing was built from it."""
        self._write_event(event_id="LOSS-WINDOW-2")
        self._crash_history_filter()
        with self.assertRaises(RuntimeError):
            self._run()

        runner_module.HistoryFilter = runner_module.HistoryFilter.__mro__[1]
        result = self._run()

        self.assertEqual(result[1].accepted, 0)
        self.assertFalse((self.keep_dir / "HIST-LOSS-WINDOW-2.json").exists())

    def test_the_run_manifest_does_report_the_failure(self):
        """The mitigation that does exist. The operator is told the run
        failed and which component aborted — they are not told which Event
        went missing, which is the gap A-20 is about."""
        self._write_event(event_id="LOSS-WINDOW-3")
        self._crash_history_filter()

        with self.assertRaises(RuntimeError):
            self._run()

        summary = read_summary(self.run_summary_path)
        self.assertEqual(summary.overall_status.value, "FAILED")
        self.assertEqual(
            summary.component("history_filter").failure.classification, "STEP_ABORTED"
        )
        # Nothing in the manifest names the lost Event.
        self.assertNotIn("LOSS-WINDOW-3", summary.to_json())

    def test_the_lock_is_not_stranded_by_the_crash(self):
        """A stranded lock would turn one lost Event into a stopped system."""
        self._write_event(event_id="LOSS-WINDOW-4")
        self._crash_history_filter()

        with self.assertRaises(RuntimeError):
            self._run()

        self.assertFalse(self.runner_lock_path.exists())


# A file that exists and is readable at the OS level but is not valid UTF-8.
# The same constant `test_monthly_history.py` already uses, for the same
# reason: it is the shape a truncated write, a legacy-codepage editor, or an
# interrupted OneDrive transfer actually produces — distinct from a *missing*
# file and from an *empty* one, both of which decode fine.
UNDECODABLE_BYTES = b"\xff\xfe\x00 not utf-8 \xff"


class UndecodableFileIsolationTests(unittest.TestCase):
    """`except OSError` around a decode lets `UnicodeDecodeError` through.

    Found by tracing which lines of `src/` the suite never executes, then
    scanning every `try` that decodes or parses for one that does not catch
    `ValueError`. Four sites had it, in four modules, all with the same
    consequence: a file that is not valid UTF-8 escaped a function whose
    documented contract is to report the problem rather than raise.

    `monthly/generator._existing_generated_at()` had already been fixed for
    exactly this, and says so in its docstring ("it used to catch only
    `OSError`, so a previous Monthly that was not valid UTF-8 raised
    `UnicodeDecodeError` (a ValueError) out of here and failed the entire
    rebuild"). The other four were the same bug, unfound.

    Measured blast radius of the worst one, through the real Runner: an
    undecodable Daily file made step 6.5 raise out of `run_once()`, so
    Monthly, **Backup** and Dashboard never started — 6 of 9 components
    recorded, no commit, no Dashboard row. A component docs/14 §5 classifies
    DEGRADED was aborting one it classifies CRITICAL.

    Each test below pins the *contract* the site already claimed, not a new
    behaviour.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    # ---------------------------------------------- daily/generator.py

    def test_an_undecodable_daily_is_reported_not_raised(self):
        """`update_daily_history()`: "Never raises for an I/O or rendering
        failure — docs/06 §41 requires that a History write failure leave
        the existing History intact ... and be retried on the next run"."""
        daily = self.root / "daily"
        daily.mkdir(parents=True)
        repo = FileHistoryRepository(
            keep_dir=self.root / "keep", review_dir=self.root / "review"
        )
        repo.save(
            HistoryCandidate(
                history_id="HIST-LATE",
                event_id="LATE",
                timestamp="2026-08-05T10:00:00+09:00",
                category="MILESTONE",
                project_id="P",
                role="COO",
                summary="late work",
                evidence=(),
                filter_result=HistoryDecision.KEEP,
            )
        )
        target = daily / "2026-08-05.md"
        target.write_bytes(UNDECODABLE_BYTES)

        result = update_daily_history(
            repo,
            date(2026, 8, 5),
            output_dir=daily,
            now=datetime(2026, 8, 7, 9, 0).astimezone(),
        )

        self.assertEqual(result.outcome, LateUpdateOutcome.FAILED)
        self.assertIsNotNone(result.error)
        # §41: the existing History is left exactly as it was.
        self.assertEqual(target.read_bytes(), UNDECODABLE_BYTES)
        # And the Candidate is untouched, so the next run retries the date.
        self.assertEqual(len(repo.list(decision=HistoryDecision.KEEP)), 1)

    # ---------------------------------------------- collector/runtime.py

    def test_an_undecodable_incoming_file_does_not_stop_the_batch(self):
        """docs/03 §53, which this module quotes as the reason
        `collector.collect()` is wrapped. The read above it was the one step
        in the loop that did not honour it."""
        incoming = self.root / "incoming"
        incoming.mkdir(parents=True)
        good = create_event(
            source="DESKTOP_1",
            role="CTO_BACKEND",
            project_id="P",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            summary="ok",
            history_candidate=True,
            event_id="GOOD-1",
            timestamp="2026-08-05T10:00:00+09:00",
        )
        # Sorted first, so it is reached before the good one.
        (incoming / "a-bad.json").write_bytes(UNDECODABLE_BYTES)
        (incoming / "b-good.json").write_text(good.to_json(), encoding="utf-8")

        summary = collector_run_once(
            collector=Collector(seen_store=InMemorySeenEventStore()),
            incoming_dir=incoming,
            processed_dir=self.root / "processed",
            rejected_dir=self.root / "rejected",
            log_path=self.root / "collector.log",
        )

        self.assertEqual(summary.accepted, 1)
        self.assertEqual(summary.failed, 1)
        # The unreadable file stays in incoming/ for the next run, never
        # deleted and never silently dropped.
        self.assertTrue((incoming / "a-bad.json").exists())

    # ---------------------------------------------- agent/signals.py

    def test_an_undecodable_signal_is_rejected_not_fatal(self):
        """One unusable Signal is quarantined for a human; the rest of the
        date proceeds."""
        day_dir = self.root / "signals" / "2026-08-05"
        day_dir.mkdir(parents=True)
        (day_dir / "good.json").write_text(
            json.dumps(
                {
                    "project_id": "P",
                    "event_type": "MILESTONE_COMPLETED",
                    "status": "IN_PROGRESS",
                    "summary": "ok",
                    "history_candidate": True,
                }
            ),
            encoding="utf-8",
        )
        (day_dir / "bad.json").write_bytes(UNDECODABLE_BYTES)

        valid, invalid = load_signals(self.root / "signals", date(2026, 8, 5))

        self.assertEqual(len(valid), 1)
        self.assertEqual(len(invalid), 1)
        self.assertIn("could not read file", str(invalid[0][1]))

    # ---------------------------------------------- agent/delivery.py

    def test_an_undecodable_sync_destination_is_reported_as_unreadable(self):
        """`UNREADABLE` is one of this module's four declared verdicts, and a
        truncated transfer is one of the shapes its docstring lists as the
        reason it exists. It simply did not classify undecodable as
        unreadable."""
        sent = self.root / "sent"
        sync = self.root / "sync"
        sent.mkdir()
        sync.mkdir()
        (sent / "E1.json").write_text(json.dumps({"event_id": "E1"}), encoding="utf-8")
        (sync / "E1.json").write_bytes(UNDECODABLE_BYTES)

        result = find_undelivered_events(sent_dir=sent, sync_folder=sync)

        self.assertEqual(len(result.undelivered), 1)
        self.assertEqual(result.undelivered[0].problem, DeliveryProblem.UNREADABLE)

    def test_the_status_view_still_answers_when_a_destination_is_undecodable(self):
        """The reason this one mattered: `ops_status.py` is the read-only
        view whose whole contract is that it answers even when the evidence
        is damaged. It crashed instead."""
        sent = self.root / "sent"
        sync = self.root / "sync"
        sent.mkdir()
        sync.mkdir()
        for index in range(3):
            (sent / f"E{index}.json").write_text(
                json.dumps({"event_id": f"E{index}"}), encoding="utf-8"
            )
        (sync / "E0.json").write_bytes(UNDECODABLE_BYTES)
        (sync / "E1.json").write_text(json.dumps({"event_id": "E1"}), encoding="utf-8")
        # E2 has no destination — the normal consumed case.

        result = find_undelivered_events(sent_dir=sent, sync_folder=sync)

        self.assertEqual(result.checked, 3)
        self.assertEqual(result.absent, 1)
        self.assertEqual([u.event_id for u in result.undelivered], ["E0"])

    # ---------------------------------------------- the family itself

    def test_no_decode_site_catches_oserror_alone(self):
        """The scan that found these, kept as a guard.

        Any `try` whose body decodes or parses must catch `ValueError` too —
        `UnicodeDecodeError` and `JSONDecodeError` are both `ValueError`
        subclasses, and `OSError` does not cover either.
        """
        import ast

        DECODERS = ("read_text", "read_bytes", "decode")
        FORGIVING = {
            "ValueError",
            "UnicodeDecodeError",
            "Exception",
            "BaseException",
            "<bare>",
        }

        def caught_by(handler):
            node = handler.type
            if node is None:
                return {"<bare>"}
            if isinstance(node, ast.Name):
                return {node.id}
            if isinstance(node, ast.Tuple):
                return {e.id for e in node.elts if isinstance(e, ast.Name)}
            return {ast.unparse(node)}

        offenders = []
        for path in sorted((Path(__file__).resolve().parents[1] / "src").rglob("*.py")):
            if "__pycache__" in str(path):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))

            # Innermost enclosing `try` per node. Walking every `Try` and
            # asking "does my body mention a decode?" flags `run_once()`'s
            # outer try/finally, whose body is the entire pipeline — the
            # question is which handler would actually catch the decode, and
            # that is the nearest one that has handlers at all.
            enclosing = {}

            def descend(node, current):
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, ast.Try):
                        inner = child if child.handlers else current
                        for stmt in child.body:
                            descend(stmt, inner)
                        for handler in child.handlers:
                            for stmt in handler.body:
                                descend(stmt, current)
                        for stmt in child.orelse + child.finalbody:
                            descend(stmt, current)
                        continue
                    enclosing[child] = current
                    descend(child, current)

            descend(tree, None)

            for node, guard in enclosing.items():
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if name not in DECODERS:
                    continue
                if guard is None:
                    # Unguarded entirely. Legitimate where the caller is the
                    # one that converts (the scan's job is the mismatched
                    # guard, not the absent one), so only a guard that exists
                    # and is too narrow is reported.
                    continue
                caught = set()
                for handler in guard.handlers:
                    caught |= caught_by(handler)
                if caught & FORGIVING:
                    continue
                offenders.append(
                    f"{path.name}:{node.lineno} guarded at {guard.lineno} "
                    f"catching {sorted(caught)}"
                )

        self.assertEqual(sorted(offenders), [])


class NaiveAwareComparisonGuardTests(unittest.TestCase):
    """Two `fromisoformat()` results compared without allowing for one of
    them being naive.

    `datetime` raises `TypeError`, not `ValueError`, for
    "can't compare offset-naive and offset-aware datetimes", so a guard
    written as `except ValueError` — the obvious one, since that is what a
    bad *value* raises — lets it straight through. Two live sites had
    exactly that:

        notion/sync.py       §29-30's Late Event guard, against the
                             `Last Updated` value Notion returns. Measured:
                             `notion_sync` FAILED every run and the retry
                             queue grew 1 -> 2 -> 3 without bound.
        app/desktop_activity `_before()`, whose own docstring promises a
                             corrupted Event "affects only its own ordering".
                             Measured: one naive Event in `processed/` took
                             the whole COMPANY view of `ops_status.py` down.

    Both are fixed and pinned behaviourally
    (`test_notion_sync.py::LateEventGuardTimezoneTests`,
    `test_observability.py::NaiveTimestampInProcessedEventsTests`). This is
    the structural half: the next one written gets caught at the shape.

    A site is accepted when either the innermost enclosing `try` catches
    `TypeError` (or something broader), or the surrounding function checks
    `tzinfo` explicitly — which is how `agent/status.py` and
    `history/result.py` handle it.
    """

    SRC = Path(__file__).resolve().parents[1] / "src"

    def _innermost_guards(self, tree):
        guards = {}

        def descend(node, current):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.Try):
                    inner = child if child.handlers else current
                    for stmt in child.body:
                        descend(stmt, inner)
                    for handler in child.handlers:
                        for stmt in handler.body:
                            descend(stmt, current)
                    for stmt in child.orelse + child.finalbody:
                        descend(stmt, current)
                    continue
                guards[child] = current
                descend(child, current)

        descend(tree, None)
        return guards

    def _caught(self, try_node):
        names = set()
        for handler in try_node.handlers:
            node = handler.type
            if node is None:
                names.add("<bare>")
            elif isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Tuple):
                names |= {e.id for e in node.elts if isinstance(e, ast.Name)}
        return names

    def _enclosing_function(self, tree, lineno):
        best = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end = getattr(node, "end_lineno", node.lineno)
                if node.lineno <= lineno <= end:
                    if best is None or node.lineno > best.lineno:
                        best = node
        return best

    def test_no_timestamp_comparison_is_guarded_against_value_error_only(self):
        FORGIVING = {"TypeError", "Exception", "BaseException", "<bare>"}
        offenders = []

        for path in sorted(self.SRC.rglob("*.py")):
            if "__pycache__" in str(path):
                continue
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, str(path))
            guards = self._innermost_guards(tree)

            for node, guard in guards.items():
                comparison = isinstance(node, ast.Compare) and any(
                    isinstance(op, (ast.Lt, ast.Gt, ast.LtE, ast.GtE))
                    for op in node.ops
                )
                subtraction = isinstance(node, ast.BinOp) and isinstance(
                    node.op, ast.Sub
                )
                if not (comparison or subtraction):
                    continue
                if "fromisoformat" not in ast.unparse(node):
                    continue

                if guard is not None and self._caught(guard) & FORGIVING:
                    continue
                function = self._enclosing_function(tree, node.lineno)
                if function is not None and "tzinfo" in ast.unparse(function):
                    continue
                offenders.append(path.name)

        # BUG-29 is the one known, deliberately unfixed site:
        # `NotionLastUpdatedParsingTests` above characterises it, and the
        # fix is a decision about what to trust when Notion holds a value
        # that cannot be compared (proceed? skip? refuse?) — every option
        # is a judgement, so it stays in BACKLOG (E-19) rather than being
        # chosen here. Listed by name so a SECOND site cannot hide behind
        # it, and so this guard fails loudly the day BUG-29 is fixed and
        # the entry becomes stale.
        # Filename rather than line number: pinning the line would make
        # this fail every time anything above it moves, which is churn for
        # the wrong reason. A SECOND site in the same file still fails —
        # the list would then read ["sync.py", "sync.py"].
        self.assertEqual(sorted(offenders), ["sync.py"])

    def test_the_fixed_site_still_handles_a_naive_value(self):
        """The scan is structural, so it can be satisfied by a `try` that
        happens to be there. This checks the behaviour it stands for.

        Only `_before()` is asserted: it is the site whose own docstring
        already states what "cannot be compared" must do ("falls back to
        string order"), so widening its guard implements a written contract.
        The Notion site (BUG-29) has no such written fallback — what to do
        with an uncomparable stored value is the open decision — so it is
        listed as known above rather than fixed here.
        """
        from app.desktop_activity import _before

        # Naive on either side, and unparseable — none of these may raise.
        self.assertIsInstance(_before("2026-08-05T10:00:00", "2026-08-06T10:00:00+09:00"), bool)
        self.assertIsInstance(_before("2026-08-05T10:00:00+09:00", "2026-08-06T10:00:00"), bool)
        self.assertIsInstance(_before("nope", "2026-08-06T10:00:00+09:00"), bool)
        self.assertIsInstance(_before("", ""), bool)


class LateUpdateBatchReadTests(RunnerFailurePathTestCase):
    """Step 6.5 read the Candidate repository once per late date.

    `scheduler.py` already solved this for its own per-date loop — CEO
    Decision ② (History Repository Cache A안), quoted in its source: "이
    배치에서 실제로 History 생성이 필요할 수 있는 경우에만,
    repository.list()를 배치당 정확히 1회 호출". `update_daily_history()`
    even takes `keep_candidates` for exactly that purpose, and says so in
    its module docstring. Step 6.5 was the one caller that never passed it.

    Measured with 3,000 stored Candidates and 7 late dates: 7 calls / 0.97 s
    against 1 call / 0.17 s. The gap grows with both numbers, and `keep/` is
    never pruned (BACKLOG B절 6번), so the backlog side only goes up.

    The snapshot is safe for the Scheduler's reason: step 5 finished writing
    every Candidate before this step begins, and nothing inside the loop
    writes one.
    """

    def _counting_repository(self):
        calls = []
        original = runner_module.FileHistoryRepository

        class Counting(original):
            def list(self, decision=None):
                calls.append(decision)
                return super().list(decision=decision)

        runner_module.FileHistoryRepository = Counting
        self.addCleanup(setattr, runner_module, "FileHistoryRepository", original)
        return calls

    def _close_days_then_add_late_candidates(self, count):
        """`count` already-written Daily files, each with a Candidate that
        arrived after it was closed."""
        from daily import generate_daily_history

        repo = FileHistoryRepository(keep_dir=self.keep_dir, review_dir=self.review_dir)
        daily_dir = self.local_master_dir / "daily"
        days = [date(2026, 8, 1) + timedelta(days=i) for i in range(count)]
        for day in days:
            generate_daily_history(
                repo, day, output_dir=daily_dir, generated_at="2026-08-20T11:00:00+09:00"
            )
        for index, day in enumerate(days):
            self._write_event(
                event_id=f"LATE-BATCH-{index}",
                event_type="MILESTONE_COMPLETED",
                milestone=f"M{index}",
                timestamp=f"{day.isoformat()}T10:00:00+09:00",
            )
        return days

    def test_the_step_reads_the_repository_once_however_many_dates_are_late(self):
        days = self._close_days_then_add_late_candidates(5)
        calls = self._counting_repository()

        self._run(now=datetime(2026, 8, 20, 12, 0).astimezone())

        # Scheduler's batch read is allowed one call; step 6.5 is allowed
        # one more. Before the hoist this was 1 + 5.
        self.assertLessEqual(
            len(calls),
            2,
            f"one read per late date is back: {len(calls)} calls for {len(days)} dates",
        )

    def test_every_late_event_still_reaches_its_own_day(self):
        """Speed must not cost correctness: the shared list spans many
        dates, and each call still has to take only its own."""
        days = self._close_days_then_add_late_candidates(5)

        self._run(now=datetime(2026, 8, 20, 12, 0).astimezone())

        daily_dir = self.local_master_dir / "daily"
        for index, day in enumerate(days):
            with self.subTest(day=day):
                text = (daily_dir / f"{day.isoformat()}.md").read_text(encoding="utf-8")
                self.assertIn(f"LATE-BATCH-{index}", text)
                for other in range(5):
                    if other != index:
                        self.assertNotIn(f"LATE-BATCH-{other}", text)

    def test_no_repository_read_happens_when_nothing_arrived_late(self):
        """The same guard the Scheduler has: `if kept_dates` gates the read
        entirely, so a run with no late Event pays nothing for it.

        The Scheduler's own batch read still happens — it has pending dates
        to close — so the bound is one, not zero. What is asserted is that
        step 6.5 adds none.
        """
        calls = self._counting_repository()

        self._run(now=datetime(2026, 8, 20, 12, 0).astimezone())

        self.assertLessEqual(len(calls), 1)

    def test_a_failing_repository_still_fails_per_date_rather_than_escaping(self):
        """The fallback. Hoisting a call that used to live inside each
        date's own try/except moves where the failure surfaces, so the
        hoisted call falls back to `None` and every date behaves exactly as
        it did before."""
        self._close_days_then_add_late_candidates(3)
        original = runner_module.FileHistoryRepository

        class Failing(original):
            def list(self, decision=None):
                raise OSError("simulated: candidate directory unreadable")

        runner_module.FileHistoryRepository = Failing
        self.addCleanup(setattr, runner_module, "FileHistoryRepository", original)

        result = self._run(now=datetime(2026, 8, 20, 12, 0).astimezone())

        self.assertIsNotNone(result)
        late = result.summary.component("late_update")
        self.assertEqual(late.status, ComponentStatus.FAILED)
        self.assertEqual(late.metrics.get("failed"), 3)
        # And the run still completed every later stage.
        self.assertEqual(len(result.summary.components), 9)


# Deeply nested JSON. `json.loads()` answers this with `RecursionError`,
# which is a `RuntimeError` subclass — so `except (OSError, ValueError)`,
# the guard every one of these sites was written with, does not cover it.
_DEEPLY_NESTED_JSON = "[" * 200_000 + "]" * 200_000


class RecursionErrorIsUnparseableTests(unittest.TestCase):
    """BUG-40's family, and the line between fixing it and deciding it.

    `agent/signals.py` already answered this question for itself, in a
    comment written beside the catch: "`json.loads` raises RecursionError,
    not ValueError, on deeply nested input. Uncaught, one corrupt Signal
    file would take down the entire Agent run instead of being rejected on
    its own." Five other `json.loads` sites had the same shape and none of
    them had the same guard.

    Measured, before: `_is_parseable_json`, `run_intake`,
    `read_company_activity`, `Collector.collect` and
    `find_undelivered_events` all raised `RecursionError` out to their
    caller. Four of them are fixed here; the fifth is not, and the
    difference is the same test C21 used — does the function's own
    documentation already say what "cannot be parsed" means for it?

        _is_parseable_json      "exists precisely so an unparseable file is
                                skipped rather than crashing the run"
        _read_one               "Both failure kinds collapse to None on
                                purpose ... this file cannot contribute"
        _problem                UNREADABLE is one of its four declared verdicts
        Collector.collect       already returns REJECTED "invalid JSON"
        list()                  says nothing  <- BUG-38, stays a decision

    `FileHistoryRepository.list()` therefore keeps raising, and BACKLOG
    F-1/A-7 keeps the question (quarantine / skip / stop is a Data Safety
    call). `test_a_candidate_repository_still_raises` pins that boundary so
    it is a stated position rather than an omission.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def _deep_file(self, directory, name="deep.json"):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_text(_DEEPLY_NESTED_JSON, encoding="utf-8")
        return path

    def _backdate(self, path):
        old = time.time() - 3600
        os.utime(path, (old, old))

    def test_intakes_parse_test_answers_false_instead_of_raising(self):
        from transport.intake import _is_parseable_json

        path = self._deep_file(self.root / "transport")

        self.assertFalse(_is_parseable_json(path))

    def test_intake_leaves_it_in_place_and_keeps_going(self):
        """The Runner's step 2. A file it cannot parse is left in
        `transport/` and re-judged next run — never promoted, never deleted,
        and never allowed to stop the batch."""
        from transport.intake import run_intake

        transport = self.root / "transport"
        deep = self._deep_file(transport)
        good = transport / "good.json"
        good.write_text(json.dumps({"event_id": "OK-1"}), encoding="utf-8")
        for path in (deep, good):
            self._backdate(path)

        summary = run_intake(
            transport_dir=transport,
            incoming_dir=self.root / "incoming",
            processed_dir=self.root / "processed",
            rejected_dir=self.root / "rejected",
        )

        self.assertEqual(summary.skipped_invalid, ("deep.json",))
        self.assertEqual(summary.moved, ("good.json",))
        self.assertTrue(deep.exists())

    def test_the_company_view_reports_it_rather_than_crashing(self):
        from app.desktop_activity import read_company_activity

        processed = self.root / "processed"
        self._deep_file(processed)

        snapshot = read_company_activity(
            processed_dir=processed,
            transport_dir=self.root / "t",
            incoming_dir=self.root / "i",
            rejected_dir=self.root / "r",
        )

        self.assertEqual(snapshot.unreadable_events, ("deep.json",))

    def test_the_collector_rejects_it_as_invalid_json(self):
        """It already classified unparseable input as REJECTED; a
        `RecursionError` is the same condition reached by a different
        route. Leaving it FAILED would park the file in `incoming/` to be
        retried on every run forever."""
        from collector.collector import Collector
        from collector.result import CollectorStatus
        from collector.seen_store import InMemorySeenEventStore

        result = Collector(seen_store=InMemorySeenEventStore()).collect(
            _DEEPLY_NESTED_JSON
        )

        self.assertEqual(result.status, CollectorStatus.REJECTED)

    def test_the_collector_batch_survives_and_routes_it_to_rejected(self):
        from collector.collector import Collector
        from collector.runtime import run_once as collector_run_once
        from collector.seen_store import InMemorySeenEventStore

        incoming = self.root / "incoming"
        self._deep_file(incoming, "a-deep.json")
        good = create_event(
            source="DESKTOP_1",
            role="CTO_BACKEND",
            project_id="P",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            summary="ok",
            history_candidate=True,
            event_id="DEEP-GOOD",
            timestamp="2026-08-05T10:00:00+09:00",
        )
        (incoming / "b-good.json").write_text(good.to_json(), encoding="utf-8")

        summary = collector_run_once(
            collector=Collector(seen_store=InMemorySeenEventStore()),
            incoming_dir=incoming,
            processed_dir=self.root / "processed",
            rejected_dir=self.root / "rejected",
            log_path=self.root / "collector.log",
        )

        self.assertEqual(summary.accepted, 1)
        self.assertEqual(summary.rejected, 1)
        self.assertEqual(summary.failed, 0)
        self.assertTrue((self.root / "rejected" / "a-deep.json").exists())

    def test_delivery_reconciliation_reports_it_as_unreadable(self):
        from agent.delivery import DeliveryProblem, find_undelivered_events

        sent = self.root / "sent"
        sync = self.root / "sync"
        sent.mkdir()
        sync.mkdir()
        (sent / "E1.json").write_text(json.dumps({"event_id": "E1"}), encoding="utf-8")
        self._deep_file(sync, "E1.json")

        result = find_undelivered_events(sent_dir=sent, sync_folder=sync)

        self.assertEqual(
            [u.problem for u in result.undelivered], [DeliveryProblem.UNREADABLE]
        )

    def test_a_damaged_sent_record_is_skipped_rather_than_fatal(self):
        """The other `json.loads` in the same function — the local record."""
        from agent.delivery import find_undelivered_events

        sent = self.root / "sent"
        sync = self.root / "sync"
        sent.mkdir()
        sync.mkdir()
        self._deep_file(sent, "E1.json")

        result = find_undelivered_events(sent_dir=sent, sync_folder=sync)

        self.assertEqual(result.checked, 0)
        self.assertEqual(result.undelivered, ())

    def test_a_candidate_repository_still_raises(self):
        """CHARACTERIZATION — BUG-38 / BACKLOG F-1, deliberately unfixed.

        `FileHistoryRepository.list()` has no documented per-file
        tolerance, and choosing one (quarantine the file? skip it? stop the
        day?) is the Data Safety decision A-7 records. Pinned so the
        boundary is a position rather than an oversight, and so this test
        fails the day that decision is taken.
        """
        keep = self.root / "keep"
        self._deep_file(keep)

        with self.assertRaises(RecursionError):
            FileHistoryRepository(
                keep_dir=keep, review_dir=self.root / "review"
            ).list(decision=HistoryDecision.KEEP)

    def test_the_signal_parser_already_handled_it(self):
        """The precedent the four fixes follow, pinned so it cannot regress
        into the shape the others were in."""
        from agent.signals import SignalError, parse_signal

        with self.assertRaises(SignalError) as caught:
            parse_signal(
                _DEEPLY_NESTED_JSON,
                signal_id="s",
                target_date=date(2026, 8, 5),
                path=Path("s.json"),
            )

        self.assertIn("nested too deeply", str(caught.exception))
