"""Architecture Invariant Tests (Audit Sprint).

Six architecture decisions were adopted by the CEO as "A안" and are named in
this Sprint's brief as fixed:

    Retry Queue Architecture
    History Repository (Index)
    Backup Working Copy Index
    Notion Dashboard
    State Consistency
    Dashboard Bootstrap

Each of them makes a specific, checkable claim about *how* the code behaves —
"the index is built once per batch", "the queue upserts and never duplicates",
"comparison is content-based, not mtime-based", "the queue is drained before
new events". None of those claims had a test asserting it: the existing suite
verifies the resulting outputs, so an implementation that quietly dropped the
optimisation or the ordering would still pass.

This file pins the invariants themselves, plus the one system-wide property
that has no unit-level home at all: real mutual exclusion between concurrent
OS processes.

Nothing here changes production code, Runtime behaviour, or any spec.
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import re
import shutil
import stat
import subprocess
import textwrap
import sys
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))

import scheduler.scheduler as scheduler_module  # noqa: E402
from backup.log import BackupLogEntry  # noqa: E402
from backup.result import BackupStatus  # noqa: E402
from events import Event  # noqa: E402
from history import HistoryCandidate, HistoryDecision  # noqa: E402
from notion.dashboard_pending import PendingDashboardRecord  # noqa: E402
from notion.retry_queue import RetryQueueEntry  # noqa: E402
from runsummary import (  # noqa: E402
    ComponentResult,
    ComponentStatus,
    Failure,
    Retryability,
    RunSummary,
    Severity,
    read_summary,
    write_summary,
)
from app import runner as runner_module  # noqa: E402
from backup.working_copy import sync_to_working_copy  # noqa: E402
from daily import build_keep_index, generate_daily_history  # noqa: E402
from events import create_event  # noqa: E402
from history import (  # noqa: E402
    FileHistoryRepository,
    HistoryCandidate,
    HistoryDecision,
)
from notion.dashboard_pending import load_pending, save_pending  # noqa: E402
from notion.retry_queue import dequeue, enqueue, load_queue  # noqa: E402


def _candidate(index: int, day: date) -> HistoryCandidate:
    return HistoryCandidate(
        history_id=f"HIST-INV-{index:04d}",
        event_id=f"INV-{index:04d}",
        timestamp=f"{day.isoformat()}T10:00:00+09:00",
        category="MILESTONE",
        project_id="PRJ-INV",
        role="COO",
        summary=f"invariant candidate {index}",
        evidence=(),
        filter_result=HistoryDecision.KEEP,
    )


class CountingRepository(FileHistoryRepository):
    """Wraps the real repository and counts how often list() is called."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.list_calls = 0

    def list(self, decision=None):
        self.list_calls += 1
        return super().list(decision=decision)


class HistoryRepositoryIndexInvariantTests(unittest.TestCase):
    """Adopted decision: History Repository (Index).

    CEO Decision 2 as recorded in scheduler.py: "이 배치에서 실제로 History
    생성이 필요할 수 있는 경우에만, repository.list()를 배치당 정확히 1회
    호출하고, 그 결과로 날짜별 History Index를 1회만 만들어 모든 날짜가
    재사용한다."
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo = CountingRepository(
            keep_dir=self.root / "keep", review_dir=self.root / "review"
        )
        self.daily_dir = self.root / "daily"
        start = date(2026, 7, 1)
        for i in range(40):
            self.repo.save(_candidate(i, start + timedelta(days=i % 30)))
        self.repo.list_calls = 0

    def test_repository_list_is_called_exactly_once_for_a_30_day_batch(self):
        result = scheduler_module.run_once(
            self.repo,
            history_start_date=date(2026, 7, 1),
            now=datetime(2026, 7, 31, 11, 0).astimezone(),
            state_path=self.root / "state.json",
            daily_output_dir=self.daily_dir,
            already_locked=True,
        )

        self.assertEqual(len(result.generated_dates), 30)
        self.assertEqual(
            self.repo.list_calls,
            1,
            "the batch must read the repository once, not once per date",
        )

    def test_no_repository_read_happens_when_nothing_is_pending(self):
        """`if pending_dates:` guards the index build entirely."""
        (self.root / "state.json").write_text(
            json.dumps({"last_successful_daily_close": "2026-07-30"}), encoding="utf-8"
        )

        result = scheduler_module.run_once(
            self.repo,
            history_start_date=date(2026, 7, 1),
            now=datetime(2026, 7, 31, 11, 0).astimezone(),
            state_path=self.root / "state.json",
            daily_output_dir=self.daily_dir,
            already_locked=True,
        )

        self.assertEqual(result.generated_dates, ())
        self.assertEqual(self.repo.list_calls, 0)

    def test_indexed_output_is_byte_identical_to_the_unindexed_path(self):
        """The optimisation must not change a single byte of Company History."""
        target = date(2026, 7, 5)
        fixed_generated_at = "2026-07-31T11:00:00+09:00"

        unindexed_dir = self.root / "unindexed"
        indexed_dir = self.root / "indexed"

        plain = generate_daily_history(
            self.repo, target, output_dir=unindexed_dir, generated_at=fixed_generated_at
        )
        index = build_keep_index(self.repo.list(decision=HistoryDecision.KEEP))
        via_index = generate_daily_history(
            self.repo,
            target,
            output_dir=indexed_dir,
            generated_at=fixed_generated_at,
            keep_index=index,
        )

        self.assertEqual(
            plain.read_text(encoding="utf-8"), via_index.read_text(encoding="utf-8")
        )

    def test_index_buckets_every_candidate_exactly_once(self):
        candidates = self.repo.list(decision=HistoryDecision.KEEP)
        index = build_keep_index(candidates)
        total = sum(len(bucket) for bucket in index.values())
        self.assertEqual(total, len(candidates))


class BackupWorkingCopyIndexInvariantTests(unittest.TestCase):
    """Adopted decision: Backup Working Copy Index.

    working_copy.py's `_content_differs()` documents a deliberate choice:
    comparison stays content-based (`filecmp.cmp(shallow=False)`), because a
    stat/mtime signature "silently weakens what 'modified' means, which is a
    Backup contract change, not an optimization".
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.master = self.root / "local_master"
        self.working_copy = self.root / "working_copy"
        (self.master / "daily").mkdir(parents=True, exist_ok=True)

    def _write(self, name: str, content: str, mtime: float | None = None):
        path = self.master / "daily" / name
        path.write_text(content, encoding="utf-8")
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def test_identical_content_with_a_different_mtime_is_not_modified(self):
        """An mtime-based index would report a false 'modified' here."""
        self._write("2026-08-01.md", "same content")
        sync_to_working_copy(self.master, self.working_copy)

        future = time.time() + 10_000
        self._write("2026-08-01.md", "same content", mtime=future)
        result = sync_to_working_copy(self.master, self.working_copy)

        self.assertEqual(result.added, ())
        self.assertEqual(result.modified, ())
        self.assertEqual(result.deleted, ())

    def test_changed_content_with_an_identical_mtime_is_modified(self):
        """And it would miss a real change here."""
        path = self._write("2026-08-02.md", "original")
        sync_to_working_copy(self.master, self.working_copy)
        original_mtime = path.stat().st_mtime

        self._write("2026-08-02.md", "CHANGED!", mtime=original_mtime)
        result = sync_to_working_copy(self.master, self.working_copy)

        self.assertEqual(result.modified, ("daily\\2026-08-02.md".replace("\\", os.sep),))

    def test_only_daily_and_monthly_are_in_scope(self):
        """docs/08 section 26. Anything else in Local Master is invisible."""
        self._write("2026-08-03.md", "in scope")
        (self.master / "monthly").mkdir(parents=True, exist_ok=True)
        (self.master / "monthly" / "2026-08.md").write_text("in scope", encoding="utf-8")
        (self.master / "stray.md").write_text("out of scope", encoding="utf-8")
        (self.master / "decisions").mkdir(parents=True, exist_ok=True)
        (self.master / "decisions" / "d1.md").write_text("out of scope", encoding="utf-8")

        result = sync_to_working_copy(self.master, self.working_copy)

        self.assertEqual(len(result.added), 2)
        self.assertFalse((self.working_copy / "stray.md").exists())
        self.assertFalse((self.working_copy / "decisions").exists())

    def test_a_detected_deletion_applies_nothing_at_all(self):
        """docs/08 sections 44-47: the one-run block must actually hold — an
        unrelated add must not be applied either, or the next run would see no
        deletion and silently proceed."""
        self._write("2026-08-04.md", "first")
        sync_to_working_copy(self.master, self.working_copy)

        (self.master / "daily" / "2026-08-04.md").unlink()
        self._write("2026-08-05.md", "unrelated new file")
        result = sync_to_working_copy(self.master, self.working_copy)

        self.assertEqual(len(result.deleted), 1)
        self.assertFalse((self.working_copy / "daily" / "2026-08-05.md").exists())
        self.assertTrue((self.working_copy / "daily" / "2026-08-04.md").exists())


class RetryQueueInvariantTests(unittest.TestCase):
    """Adopted decision: Retry Queue Architecture.

    retry_queue.py's contract: "One entry per event_id (upsert, never
    duplicated)", the full Event stored inline so a retry never has to locate
    the source file, and a missing file meaning an empty queue.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "notion_retry_queue.json"

    def _event(self, event_id, summary="retry invariant"):
        return create_event(
            source="DESKTOP_1",
            role="COO",
            project_id="PRJ-RQ",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            summary=summary,
            milestone="M1",
            history_candidate=True,
            event_id=event_id,
            timestamp="2026-08-01T10:00:00+09:00",
        )

    def test_missing_file_is_an_empty_queue(self):
        self.assertEqual(load_queue(self.path), [])

    def test_requeueing_the_same_event_upserts_instead_of_duplicating(self):
        now = datetime(2026, 8, 1, 12, 0).astimezone()
        for _ in range(5):
            enqueue(self.path, self._event("RQ-001"), now=now)

        entries = load_queue(self.path)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].attempt_count, 5)

    def test_added_at_is_preserved_across_retries(self):
        enqueue(self.path, self._event("RQ-002"), now=datetime(2026, 8, 1, 12, 0).astimezone())
        first = load_queue(self.path)[0].added_at
        enqueue(self.path, self._event("RQ-002"), now=datetime(2026, 8, 9, 12, 0).astimezone())
        self.assertEqual(load_queue(self.path)[0].added_at, first)

    def test_event_data_is_refreshed_on_requeue(self):
        enqueue(self.path, self._event("RQ-003", summary="old"))
        enqueue(self.path, self._event("RQ-003", summary="new"))
        self.assertEqual(load_queue(self.path)[0].to_event().summary, "new")

    def test_entry_round_trips_back_into_a_full_event(self):
        """The queue is self-contained: no source file lookup is ever needed."""
        original = self._event("RQ-004")
        enqueue(self.path, original)
        restored = load_queue(self.path)[0].to_event()
        self.assertEqual(restored.to_dict(), original.to_dict())

    def test_dequeue_is_idempotent(self):
        enqueue(self.path, self._event("RQ-005"))
        dequeue(self.path, "RQ-005")
        dequeue(self.path, "RQ-005")
        dequeue(self.path, "NEVER-QUEUED")
        self.assertEqual(load_queue(self.path), [])

    def test_runner_batches_queue_writes_into_a_single_save(self):
        """Retry Queue Batch Save (CEO 승인 B안).

        app/runner.py step 4 loads the queue once, mutates it in memory via
        upsert_entry()/remove_entry(), and writes it back at most once. It must
        not call the file-level enqueue()/dequeue() helpers, which re-read and
        rewrite the whole file per Event — the O(n^2) behaviour this replaced
        (200 Events with Notion down: 5.68s -> 3.16s, and ms/Event now flat
        instead of rising with queue size).
        """
        source = inspect.getsource(runner_module.run_once)

        self.assertIn("load_retry_queue(resolved_retry_queue_path)", source)
        self.assertIn("retry_queue_upsert(queue_entries", source)
        self.assertIn("retry_queue_remove(queue_entries", source)
        self.assertEqual(source.count("save_retry_queue("), 1)
        # The per-call file helpers must not be used inside the Runner.
        self.assertNotIn("retry_queue_enqueue(", source)
        self.assertNotIn("retry_queue_dequeue(", source)

    def test_batch_helpers_preserve_upsert_semantics(self):
        """The in-memory helpers must behave exactly like enqueue()/dequeue()."""
        from notion.retry_queue import remove_entry, upsert_entry

        entries: list = []
        now = datetime(2026, 8, 1, 12, 0).astimezone()
        for _ in range(4):
            upsert_entry(entries, self._event("BATCH-1"), now=now)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].attempt_count, 4)
        self.assertEqual(entries[0].added_at, now.isoformat(timespec="seconds"))

        self.assertTrue(remove_entry(entries, "BATCH-1"))
        self.assertEqual(entries, [])
        self.assertFalse(remove_entry(entries, "BATCH-1"))

    def test_file_level_enqueue_still_matches_the_batch_helpers(self):
        """One-off callers keep the old contract; both paths agree."""
        from notion.retry_queue import upsert_entry

        now = datetime(2026, 8, 1, 12, 0).astimezone()
        enqueue(self.path, self._event("PARITY-1"), now=now)
        enqueue(self.path, self._event("PARITY-1"), now=now)

        expected: list = []
        upsert_entry(expected, self._event("PARITY-1"), now=now)
        upsert_entry(expected, self._event("PARITY-1"), now=now)

        self.assertEqual(
            [e.to_dict() for e in load_queue(self.path)],
            [e.to_dict() for e in expected],
        )

    def test_runner_drains_the_queue_before_this_run_s_new_events(self):
        """CEO Policy Decision: "Retry Queue를 가장 먼저 처리한다". Asserted
        against the Runner's source, because ordering is not observable from
        the return value alone."""
        source = inspect.getsource(runner_module.run_once)
        queue_first = source.index("load_retry_queue(resolved_retry_queue_path)")
        new_events = source.index("for processed_file in collector_summary.files")
        self.assertLess(queue_first, new_events)


class DashboardPendingInvariantTests(unittest.TestCase):
    """Adopted decisions: Notion Dashboard / Dashboard Bootstrap.

    dashboard_pending.py mirrors the Retry Queue mechanism but keeps Dashboard
    records in a separate file, because a Dashboard record is not an Event and
    must not corrupt the queue's to_event() contract.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "dashboard_pending.json"

    def test_missing_file_is_an_empty_list(self):
        self.assertEqual(load_pending(self.path), [])

    def test_same_run_id_upserts_instead_of_duplicating(self):
        for _ in range(4):
            save_pending(self.path, run_id="RUN-1", properties={"a": 1})
        records = load_pending(self.path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].attempt_count, 4)

    def test_distinct_run_ids_are_kept_separately(self):
        save_pending(self.path, run_id="RUN-1", properties={"a": 1})
        save_pending(self.path, run_id="RUN-2", properties={"a": 2})
        self.assertEqual({r.run_id for r in load_pending(self.path)}, {"RUN-1", "RUN-2"})

    def test_dashboard_records_never_enter_the_event_retry_queue(self):
        """The separation the module docstring insists on: Dashboard records
        are Notion properties, not Events, so this module must never import
        or construct an Event (the docstring *discusses* Event.from_dict, so
        the check is on imports and executable code, not on prose)."""
        import notion.dashboard_pending as dashboard_pending

        module_source = Path(dashboard_pending.__file__).read_text(encoding="utf-8")
        tree = __import__("ast").parse(module_source)
        imported = set()
        for node in tree.body:
            if isinstance(node, __import__("ast").ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, __import__("ast").Import):
                imported.update(a.name for a in node.names)

        self.assertNotIn("events", imported)
        self.assertFalse(hasattr(dashboard_pending.PendingDashboardRecord, "to_event"))
        self.assertNotEqual(
            dashboard_pending.DEFAULT_DASHBOARD_PENDING_PATH.name,
            "notion_retry_queue.json",
        )


class SingleSystemWideLockInvariantTests(unittest.TestCase):
    """Adopted decision: State Consistency / docs/07 section 25 — "Lock으로
    하나의 Runner만 실행되게 한다" is ONE system-wide invariant, not one lock
    per component. The Runtime Stabilization Sprint fixed a self-deadlock
    caused by Scheduler taking a second lock inside the Runner's critical
    section; this pins that fix.
    """

    def test_runner_passes_already_locked_to_the_scheduler(self):
        source = inspect.getsource(runner_module.run_once)
        self.assertIn("already_locked=True", source)

    def test_scheduler_takes_no_lock_when_already_locked(self):
        source = inspect.getsource(scheduler_module.run_once)
        already_locked_branch = source.index("if already_locked:")
        acquire = source.index("try_acquire_lock")
        self.assertLess(
            already_locked_branch, acquire, "the already_locked branch must return first"
        )

    def test_standalone_scheduler_still_locks_itself(self):
        """docs/07: 수동 실행도 동일한 Lock 규칙을 따른다."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        repo = FileHistoryRepository(keep_dir=root / "keep", review_dir=root / "review")
        lock_path = root / "scheduler.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(
            json.dumps({"process_id": os.getpid(), "created_at": "2026-08-01T00:00:00+09:00"}),
            encoding="utf-8",
        )

        result = scheduler_module.run_once(
            repo,
            history_start_date=date(2026, 8, 1),
            now=datetime(2026, 8, 5, 11, 0).astimezone(),
            state_path=root / "state.json",
            lock_path=lock_path,
            daily_output_dir=root / "daily",
        )

        self.assertEqual(result.status.value, "SKIPPED_ALREADY_RUNNING")


class ConcurrentProcessMutualExclusionTests(unittest.TestCase):
    """The only property that cannot be shown in-process: four real OS
    processes racing for the same workspace must produce exactly one run and
    exactly one set of History.
    """

    def test_four_concurrent_runners_produce_one_run_and_no_duplicate_history(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)

        working_copy = root / "backup_working_copy"
        bare_remote = root / "backup_remote.git"
        working_copy.mkdir(parents=True, exist_ok=True)

        def git(args, cwd):
            subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)

        git(["init", "--bare", "-b", "main", str(bare_remote)], root)
        git(["init", "-b", "main"], working_copy)
        git(["config", "user.email", "t@example.invalid"], working_copy)
        git(["config", "user.name", "Concurrency Test"], working_copy)
        git(["remote", "add", "origin", str(bare_remote)], working_copy)
        (working_copy / ".gitkeep").write_text("", encoding="utf-8")
        git(["add", "-A"], working_copy)
        git(["commit", "-m", "init"], working_copy)
        git(["push", "-u", "origin", "main"], working_copy)

        incoming = root / "incoming"
        incoming.mkdir(parents=True, exist_ok=True)
        event = create_event(
            source="DESKTOP_1",
            role="COO",
            project_id="PRJ-CONC",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            summary="concurrency probe",
            milestone="M1",
            history_candidate=True,
            event_id="CONC-001",
            timestamp="2026-08-01T10:00:00+09:00",
        )
        (incoming / "CONC-001.json").write_text(event.to_json(), encoding="utf-8")

        script = root / "concurrent_runner.py"
        script.write_text(
            "import sys\n"
            "from datetime import date, datetime\n"
            "from pathlib import Path\n"
            f"sys.path.insert(0, r'{SRC}')\n"
            "from app.runner import run_once\n"
            f"root = Path(r'{root}')\n"
            "res = run_once(\n"
            "    local_master_dir=root / 'local_master',\n"
            "    backup_working_copy_dir=root / 'backup_working_copy',\n"
            "    history_start_date=date(2026, 8, 1),\n"
            "    runner_lock_path=root / 'locks' / 'company_ops.lock',\n"
            "    transport_dir=root / 'transport',\n"
            "    incoming_dir=root / 'incoming',\n"
            "    processed_dir=root / 'processed',\n"
            "    rejected_dir=root / 'rejected',\n"
            "    collector_log_path=root / 'logs' / 'collector.log',\n"
            "    late_update_log_path=root / 'logs' / 'daily_late_update.log',\n"
            "    monthly_state_path=root / 'state' / 'monthly_history_state.json',\n"
            "    run_summary_path=root / 'runs' / 'last_run.json',\n"
            "    collector_state_path=root / 'state' / 'collector_state.json',\n"
            "    keep_dir=root / 'keep',\n"
            "    review_dir=root / 'review',\n"
            "    scheduler_state_path=root / 'state' / 'daily_history_state.json',\n"
            "    backup_state_path=root / 'state' / 'backup_state.json',\n"
            "    now=datetime(2026, 8, 5, 11, 0).astimezone())\n"
            "print('SKIPPED' if res is None else 'RAN')\n",
            encoding="utf-8",
        )

        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        procs = [
            subprocess.Popen(
                [sys.executable, str(script)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            for _ in range(4)
        ]
        outputs = [p.communicate() for p in procs]
        verdicts = [
            (out or "").strip().splitlines()[-1] if (out or "").strip() else "ERR"
            for out, _ in outputs
        ]

        # With the atomic lock (BUG-18 fix) this is now a strict assertion:
        # exactly one Runner may execute, the rest must be cleanly skipped.
        # It was previously written loosely because mutual exclusion was not
        # actually guaranteed — an earlier `RAN == 1` draft passed in
        # isolation and failed inside the full suite, which was itself
        # evidence for BUG-18.
        kept = [p.stem for p in (root / "keep").glob("*.json")]

        self.assertEqual(verdicts.count("RAN"), 1, f"verdicts={verdicts}")
        self.assertEqual(verdicts.count("SKIPPED"), 3, f"verdicts={verdicts}")
        self.assertEqual(len(kept), 1, kept)
        self.assertEqual(len(list((root / "local_master" / "daily").glob("*.md"))), 4)
        # The raw Execution Event is never destroyed.
        surviving = len(list((root / "processed").glob("*.json"))) + len(
            list((root / "incoming").glob("*.json"))
        )
        self.assertEqual(surviving, 1)
        # Whoever ran, the lock is always released (run_once's finally block).
        self.assertFalse((root / "locks" / "company_ops.lock").exists())


class LockAtomicityCharacterizationTests(unittest.TestCase):
    """Audit finding BUG-18 (P0).

    `scheduler.lock.try_acquire_lock()` is a check-then-write sequence:

        if lock_path.exists():        # <-- check
            ...decide it is held or stale...
        ...                           # <-- window
        os.replace(tmp_path, lock_path)   # <-- write, unconditional

    Nothing makes that atomic. When several processes reach it together on a
    path that does not exist yet, all of them pass the existence check and all
    of them "acquire". `os.replace()` overwrites unconditionally, so the last
    writer simply wins the file while every caller was already told True.

    A second defect rides along: on Windows, `os.replace()` onto a target that
    another process is replacing at the same instant raises
    PermissionError (WinError 5). try_acquire_lock() does not catch it, so a
    contended acquisition can crash the Runner instead of returning False.

    Measured with a spin barrier (8 processes, 12 trials):
        every trial produced 2-3 simultaneous holders, DENIED was 0 in all 12,
        and the remaining processes died with PermissionError.

    docs/07 section 25 states the invariant this breaks: "Lock으로 하나의
    Runner만 실행되게 한다."

    Why the suite's other lock tests still pass: normal process startup takes
    hundreds of milliseconds, which staggers real runners far outside the race
    window. The bug needs near-simultaneous starts to appear — which is exactly
    the case docs/07 section 23 describes (a manual or startup trigger firing
    alongside the scheduled one).
    """

    def _spawn(self, lock_path: Path, count: int):
        script = lock_path.parent / "acquire_probe.py"
        script.write_text(
            "import sys, time\n"
            "from datetime import datetime\n"
            "from pathlib import Path\n"
            f"sys.path.insert(0, r'{SRC}')\n"
            "from scheduler.lock import try_acquire_lock\n"
            "lock = Path(sys.argv[1])\n"
            "until = float(sys.argv[2])\n"
            "while time.time() < until:\n"
            "    pass\n"
            "try:\n"
            "    ok = try_acquire_lock(lock, now=datetime.now().astimezone())\n"
            "except Exception as exc:\n"
            "    print('RAISED:' + type(exc).__name__)\n"
            "else:\n"
            "    print('ACQUIRED' if ok else 'DENIED')\n"
            "time.sleep(0.3)\n",
            encoding="utf-8",
        )
        start_at = time.time() + 0.4
        procs = [
            subprocess.Popen(
                [sys.executable, str(script), str(lock_path), str(start_at)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            for _ in range(count)
        ]
        return [(p.communicate()[0] or "").strip().splitlines()[-1] for p in procs]

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.lock_path = Path(tmp.name) / "company_ops.lock"

    def test_exactly_one_contender_acquires_the_lock(self):
        """BUG-18 FIXED (CEO-approved: Lock 원자성 O_EXCL).

        Eight processes released from a spin barrier at the same instant must
        produce exactly one holder — docs/07 section 25's "Lock으로 하나의
        Runner만 실행되게 한다".

        Before the fix this was 2-3 simultaneous holders in every one of 12
        measured trials, with zero clean denials, because acquisition was a
        check-then-write pair rather than one atomic operation.
        """
        verdicts = self._spawn(self.lock_path, 8)

        self.assertEqual(verdicts.count("ACQUIRED"), 1, verdicts)
        self.assertEqual(verdicts.count("DENIED"), 7, verdicts)
        self.assertTrue(self.lock_path.exists())

    def test_contended_acquisition_never_raises(self):
        """The other half of BUG-18: a contended os.replace() raised
        PermissionError on Windows, so a losing contender could crash the
        Runner instead of being told False. O_CREAT|O_EXCL reports
        FileExistsError, which is handled as "someone else holds it"."""
        verdicts = self._spawn(self.lock_path, 8)

        self.assertEqual([v for v in verdicts if v.startswith("RAISED:")], [], verdicts)
        self.assertTrue(set(verdicts) <= {"ACQUIRED", "DENIED"}, verdicts)

    def test_acquisition_is_a_single_atomic_operation(self):
        """The structural guarantee, asserted against the source so a future
        refactor cannot quietly reintroduce a check-then-write pair."""
        from scheduler import lock as lock_module

        source = inspect.getsource(lock_module.try_acquire_lock)
        # Compare executable statements only — the docstring legitimately
        # describes the old non-atomic pair it replaced.
        body = ast.parse(textwrap.dedent(source)).body[0]
        code = "\n".join(
            ast.unparse(node)
            for node in body.body
            if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
        )

        self.assertIn("os.O_CREAT | os.O_EXCL", code)
        self.assertNotIn("lock_path.exists()", code)
        self.assertNotIn("os.replace(", code)
        self.assertNotIn("mkstemp", code)

    def test_stale_takeover_still_follows_section_27(self):
        """docs/07 section 27: staleness is decided by whether the recorded
        process is running, never by elapsed time — unchanged by the fix."""
        from scheduler import lock as lock_module

        source = inspect.getsource(lock_module.try_acquire_lock)
        self.assertIn("_is_process_running(pid)", source)
        self.assertIn("_take_over_stale(lock_path, observed)", source)

        takeover = inspect.getsource(lock_module._take_over_stale)
        # A stale lock is removed only if unchanged since it was read, so two
        # concurrent takeovers cannot both succeed.
        self.assertIn("_read_lock(lock_path) != observed", takeover)

class AtomicStateWriteInvariantTests(unittest.TestCase):
    """Every runtime state file is written with the same idiom:

        fd, tmp = tempfile.mkstemp(dir=<state dir>)
        ...write...
        os.replace(tmp, final)

    collector/state.py states the goal: "a crash right after either call never
    leaves a torn file". That property is the reason a damaged state file is
    supposed to be impossible in normal operation, so it deserves a guard.

    Audit finding BUG-19 (measured alongside BUG-18): under genuine
    concurrency the *integrity* guarantee holds — every file stayed valid JSON
    in all five writers — but the *availability* one does not: on Windows a
    contended `os.replace()` raises PermissionError, and none of the writers
    catch it. With 8 concurrent processes per writer:

        collector_state.json       4 OK / 4 raised   valid JSON
        daily_history_state.json   3 OK / 5 raised   valid JSON
        backup_state.json          2 OK / 6 raised   valid JSON
        notion_retry_queue.json    4 OK / 4 raised   valid JSON
        dashboard_pending.json     3 OK / 5 raised   valid JSON

    Severity note: this is only reachable because BUG-18 lets more than one
    Runner into the critical section at once. With an atomic lock, exactly one
    process writes state at a time and this never fires. BUG-19 is therefore
    the blast radius of BUG-18 rather than an independent defect — but it is
    what makes BUG-18 expensive: any state write can abort the run.
    """

    WRITERS = {
        "collector": "collector_state.json",
        "queue": "notion_retry_queue.json",
    }

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def _run_writers(self, target: str, filename: str, count: int = 6):
        state_dir = self.root / target
        state_dir.mkdir(parents=True, exist_ok=True)
        path = state_dir / filename

        script = state_dir / "writer_probe.py"
        script.write_text(
            "import sys, time\n"
            "from pathlib import Path\n"
            f"sys.path.insert(0, r'{SRC}')\n"
            "from collector.state import PersistentSeenEventStore\n"
            "from notion.retry_queue import enqueue\n"
            "from events import create_event\n"
            "target, path, until, idx = sys.argv[1], Path(sys.argv[2]), float(sys.argv[3]), sys.argv[4]\n"
            "while time.time() < until:\n"
            "    pass\n"
            "try:\n"
            "    if target == 'collector':\n"
            "        PersistentSeenEventStore(state_path=path).mark_seen('E-' + idx)\n"
            "    else:\n"
            "        enqueue(path, create_event(source='DESKTOP_1', role='COO',\n"
            "            project_id='P', event_type='STARTED', status='IN_PROGRESS',\n"
            "            summary='s', history_candidate=True, event_id='Q-' + idx,\n"
            "            timestamp='2026-08-01T10:00:00+09:00'))\n"
            "except Exception as exc:\n"
            "    print('RAISED:' + type(exc).__name__)\n"
            "else:\n"
            "    print('OK')\n",
            encoding="utf-8",
        )

        start_at = time.time() + 0.4
        procs = [
            subprocess.Popen(
                [sys.executable, str(script), target, str(path), str(start_at), str(i)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            for i in range(count)
        ]
        verdicts = [(p.communicate()[0] or "").strip().splitlines()[-1] for p in procs]
        return verdicts, path

    def test_concurrent_writes_never_leave_a_torn_or_invalid_state_file(self):
        """The integrity guarantee — this must hold forever."""
        for target, filename in self.WRITERS.items():
            with self.subTest(writer=target):
                _, path = self._run_writers(target, filename)
                self.assertTrue(path.exists())
                data = json.loads(path.read_text(encoding="utf-8"))  # must not raise
                self.assertIsInstance(data, dict)

    def test_at_least_one_concurrent_writer_succeeds(self):
        for target, filename in self.WRITERS.items():
            with self.subTest(writer=target):
                verdicts, _ = self._run_writers(target, filename)
                self.assertGreaterEqual(verdicts.count("OK"), 1, verdicts)

    def test_contended_writes_fail_only_in_the_documented_way(self):
        """BUG-19: a contended `os.replace()` raises PermissionError on
        Windows, and no state writer catches it.

        This is now unreachable from the Runtime — the atomic lock (BUG-18
        fix) admits exactly one Runner, so state writes are never concurrent
        in practice, which is what the CEO-approved resolution relies on. The
        writers themselves are still not concurrency-safe, so this keeps the
        property visible for anyone who calls them directly.

        Written so it cannot flake: whether the window is hit depends on
        scheduling, so only the *shape* of the failure is asserted — a
        contended write either succeeds or fails with exactly
        PermissionError, and the file is never corrupted either way.
        """
        verdicts, path = self._run_writers("collector", "collector_state.json", count=8)

        self.assertTrue(set(verdicts) <= {"OK", "RAISED:PermissionError"}, verdicts)
        self.assertGreaterEqual(verdicts.count("OK"), 1, verdicts)
        json.loads(path.read_text(encoding="utf-8"))  # still valid, always

    def test_the_runtime_never_writes_state_concurrently(self):
        """Why BUG-19 is resolved rather than merely mitigated: every state
        write happens inside the Runner's critical section, and the lock that
        guards it is now atomic."""
        source = inspect.getsource(runner_module.run_once)
        acquire = source.index("try_acquire_lock(runner_lock_path")
        release = source.index("release_lock(runner_lock_path)")
        self.assertLess(acquire, release)

        lock_source = inspect.getsource(
            sys.modules["scheduler.lock"].try_acquire_lock
        )
        self.assertIn("os.O_CREAT | os.O_EXCL", lock_source)

    def test_every_state_writer_uses_the_same_atomic_idiom(self):
        """Structural guard: a future writer that skips tempfile+os.replace
        would silently lose the no-torn-file property."""
        writers = {
            "collector/state.py": "_save",
            "scheduler/state.py": "save_state",
            "backup/state.py": "save_state",
            "notion/retry_queue.py": "save_queue",
            "notion/dashboard_pending.py": "save_all",
            "history/file_repository.py": "save",
            "daily/generator.py": "generate_daily_history",
        }
        for module, function in writers.items():
            with self.subTest(module=module):
                source = (SRC / module).read_text(encoding="utf-8")
                self.assertIn("tempfile.mkstemp", source)
                self.assertIn("os.replace", source)
                self.assertIn(f"def {function}", source)


class AtomicWriteFailureCleanupTests(unittest.TestCase):
    """The other half of the atomic-write idiom, which nothing exercised.

    `test_every_state_writer_uses_the_same_atomic_idiom` proves each writer
    stages through `tempfile.mkstemp` and commits with `os.replace`. What it
    cannot prove is what happens when that commit *fails* — every writer wraps
    it in `except BaseException: os.remove(tmp); raise`, and a coverage run
    over the changed sources showed those cleanup lines unexecuted in all
    eight of them.

    That path is not exotic. `os.replace` on Windows raises WinError 5 when
    the destination is held open by another process, which is exactly what the
    concurrency tests provoke. A writer that leaked its temp file on each such
    failure would slowly fill `runtime/` with `.tmp-*` files that no code ever
    reads or removes, and nothing would report it.

    Failure is injected at `os.replace` rather than at each writer's
    serialiser because it is the one step all eight share, so the same
    assertion applies uniformly.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

        real_replace = os.replace

        def failing_replace(src, dst):
            raise OSError(5, self.MARKER)

        os.replace = failing_replace
        self.addCleanup(setattr, os, "replace", real_replace)

    MARKER = "simulated: destination held open by another process"

    def _assert_the_injected_failure_propagates(self, write, directory, name):
        """Assert the *injected* error surfaced, not merely some error.

        A plain `assertRaises(Exception)` passed even when the writer was
        never reached — a mistake in an earlier draft of this test, caught by
        a coverage run showing `save_queue`'s body unexecuted. Matching the
        marker means the writer really did get as far as its commit step.

        The exception *type* is deliberately not asserted: OneDriveTransport
        translates OSError into TransportError at its own boundary, which is
        its documented contract. The marker still travels in the message.
        """
        with self.assertRaises(Exception) as caught:  # noqa: B017
            write(directory)
        self.assertIn(self.MARKER, str(caught.exception), f"{name} never reached os.replace")

    def _writers(self):
        """(name, callable) for every atomic writer, each writing under self.root."""
        from backup.result import BackupStatus
        from backup.state import BackupState
        from backup.state import save_state as backup_save_state
        from collector.state import PersistentSeenEventStore
        from history import FileHistoryRepository, HistoryCandidate, HistoryDecision
        from notion.dashboard_pending import save_pending
        from notion.retry_queue import RetryQueueEntry
        from notion.retry_queue import save_queue
        from reporter.local_output import write_event_json
        from scheduler.state import SchedulerState
        from scheduler.state import save_state as scheduler_save_state
        from transport.onedrive import OneDriveTransport

        now = datetime(2026, 8, 6, 11, 0).astimezone()
        event = create_event(
            source="DESKTOP_1",
            role="CTO_BACKEND",
            project_id="PRJ-ATOMIC",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            summary="atomic write failure probe",
            milestone="M1",
            history_candidate=True,
            timestamp="2026-08-06T10:00:00+09:00",
            event_id="ATOMIC-001",
        )
        candidate = HistoryCandidate(
            history_id="HIST-ATOMIC-001",
            event_id="ATOMIC-001",
            timestamp="2026-08-06T10:00:00+09:00",
            category="MILESTONE",
            project_id="PRJ-ATOMIC",
            role="COO",
            summary="probe",
            evidence=(),
            filter_result=HistoryDecision.KEEP,
        )

        return [
            (
                "collector/state.py::_save",
                self.root / "collector",
                lambda d: PersistentSeenEventStore(
                    state_path=d / "collector_state.json"
                ).mark_seen("ATOMIC-001"),
            ),
            (
                "scheduler/state.py::save_state",
                self.root / "scheduler",
                lambda d: scheduler_save_state(
                    d / "daily_history_state.json", SchedulerState()
                ),
            ),
            (
                "backup/state.py::save_state",
                self.root / "backup",
                lambda d: backup_save_state(
                    d / "backup_state.json",
                    BackupState(backup_status=BackupStatus.PENDING),
                ),
            ),
            (
                "notion/retry_queue.py::save_queue",
                self.root / "retry",
                lambda d: save_queue(
                    d / "notion_retry_queue.json",
                    [
                        RetryQueueEntry(
                            event_id=event.event_id,
                            project_id=event.project_id,
                            event_data=event.to_dict(),
                            added_at=now.isoformat(timespec="seconds"),
                            attempt_count=1,
                        )
                    ],
                ),
            ),
            (
                "notion/dashboard_pending.py::save_all",
                self.root / "pending",
                lambda d: save_pending(
                    d / "dashboard_pending.json",
                    run_id="RUN-ATOMIC-001",
                    properties={"Name": {"title": []}},
                    now=now,
                ),
            ),
            (
                "history/file_repository.py::save",
                self.root / "history",
                lambda d: FileHistoryRepository(
                    keep_dir=d, review_dir=d / "review"
                ).save(candidate),
            ),
            (
                "reporter/local_output.py::write_event_json",
                self.root / "reporter",
                lambda d: write_event_json(event, directory=d),
            ),
            (
                "transport/onedrive.py::send",
                self.root / "onedrive",
                lambda d: OneDriveTransport(
                    sync_folder=d / "sync", outgoing_dir=d
                ).send(event),
            ),
        ]

    def test_a_failed_commit_leaves_no_temp_file_behind(self):
        for name, directory, write in self._writers():
            with self.subTest(writer=name):
                directory.mkdir(parents=True, exist_ok=True)
                self._assert_the_injected_failure_propagates(write, directory, name)

                leftovers = [
                    p.name
                    for p in directory.rglob("*")
                    if p.is_file() and p.name.startswith(".tmp-")
                ]
                self.assertEqual(leftovers, [], f"{name} leaked {leftovers}")

    def test_a_failed_commit_leaves_no_partial_destination_file(self):
        """The destination must be absent, not present-and-empty — a truncated
        state file would be read back as corruption on the next run."""
        for name, directory, write in self._writers():
            with self.subTest(writer=name):
                directory.mkdir(parents=True, exist_ok=True)
                self._assert_the_injected_failure_propagates(write, directory, name)

                written = [p.name for p in directory.rglob("*.json") if p.is_file()]
                self.assertEqual(written, [], f"{name} left {written}")


class TestDoubleFidelityTests(unittest.TestCase):
    """BUG-35: `InMemoryNotionTransport` is more permissive than the real API,
    which bounds how much any Notion test in this repository can prove.

    CHARACTERIZATION: asserts the divergence that exists today.

    Interface parity is exact — both transports implement all seven methods of
    `NotionTransport` with identical signatures (asserted below). Behavioural
    parity is not. Of eight payloads the live Notion API rejects, the double
    accepts six:

        rich_text over 2000 chars        accepted   (real: 400)
        property name not in the schema  accepted   (real: 400)
        wrong property type              accepted   (real: 400)
        unknown database_id on query     accepted   (real: 404)
        empty properties on create       accepted   (real: 400, title required)
        select name = ""                 accepted   (real: 400)

        unknown page_id on update        rejected   (matches real 404)
        properties=None                  TypeError  (real: 400 — wrong kind)

    This is why the audit had to build `StrictNotionTransport` in
    test_runner_failure_paths.py to reproduce BUG-13 at all: against the plain
    double, an oversized payload simply succeeds. It also explains how BUG-31
    (bootstrap never checks property TYPE) and BUG-32 (no request pacing)
    could exist with a green suite — the double never pushes back, so no test
    could have caught either.

    The honest reading: green Notion tests here demonstrate that OUR logic is
    self-consistent, not that Notion will accept what we send. Only the real
    connection can show that.

    Not fixed: tightening the double changes what every existing Notion test
    exercises, and deciding how faithful it should be (full schema validation?
    just the documented limits?) is a design decision. This test at least
    makes the gap explicit rather than implicit.
    """

    def _double(self):
        from notion.transport import InMemoryNotionTransport

        return InMemoryNotionTransport()

    def test_the_two_transports_expose_the_same_interface(self):
        """The part that IS trustworthy — no method drift."""
        from notion.transport import (
            InMemoryNotionTransport,
            NotionTransport,
            RealNotionTransport,
        )

        def public_methods(cls):
            return {n for n, v in vars(cls).items() if callable(v) and not n.startswith("_")}

        abstract = public_methods(NotionTransport)
        self.assertEqual(public_methods(InMemoryNotionTransport), abstract)
        self.assertEqual(public_methods(RealNotionTransport), abstract)

        for name in abstract:
            with self.subTest(method=name):
                self.assertEqual(
                    inspect.signature(getattr(InMemoryNotionTransport, name)),
                    inspect.signature(getattr(RealNotionTransport, name)),
                )

    def test_the_double_accepts_payloads_the_real_api_rejects(self):
        from notion.transport import NotionAPIError

        transport = self._double()
        accepted = []

        probes = {
            "oversized_rich_text": lambda: transport.create_page(
                "DB-1",
                {
                    "Project": {"title": [{"text": {"content": "P"}}]},
                    "Blocker": {"rich_text": [{"text": {"content": "X" * 2001}}]},
                },
            ),
            "unknown_property_name": lambda: transport.create_page(
                "DB-1", {"NotInSchema": {"rich_text": [{"text": {"content": "x"}}]}}
            ),
            "wrong_property_type": lambda: transport.create_page(
                "DB-1", {"Status": {"rich_text": [{"text": {"content": "x"}}]}}
            ),
            "unknown_database_id": lambda: transport.query_database("DB-NOPE", {}),
            "empty_properties": lambda: transport.create_page("DB-1", {}),
            "empty_select_name": lambda: transport.create_page(
                "DB-1", {"Status": {"select": {"name": ""}}}
            ),
        }
        for name, probe in probes.items():
            try:
                probe()
                accepted.append(name)
            except NotionAPIError:
                pass

        self.assertEqual(sorted(accepted), sorted(probes))

    def test_an_unknown_page_id_is_the_one_case_the_double_does_reject(self):
        """So the test above is a real divergence, not a broken double."""
        from notion.transport import NotionAPIError

        with self.assertRaises(NotionAPIError):
            self._double().update_page("page-does-not-exist", {"Status": {"select": {"name": "X"}}})

    def test_a_stricter_double_exists_for_the_limit_that_was_needed(self):
        """The partial mitigation, so it is not deleted as unused."""
        transport_source = (
            REPO_ROOT / "tests" / "test_runner_failure_paths.py"
        ).read_text(encoding="utf-8")

        self.assertIn("class StrictNotionTransport", transport_source)
        self.assertIn("MAX_TEXT = 2000", transport_source)


class ExitCodeContractTests(unittest.TestCase):
    """BUG-36 — RESOLVED. The exit-code contract now exists, so this is a
    guard on it rather than a record of its absence.

    What it was: `main()` had exactly two return statements and both
    returned 0. It printed every failure it knew about and acted on none of
    them. That is not hypothetical — planting a `.env` under the History
    directory trips the Secret Scan, giving `BACKUP_FAILED` with
    `push_result = "secret files detected: .env"`, and main() still returned
    0. The Runner is launched by Windows Task Scheduler, whose only
    automatic health signal is the exit code, and stdout is not captured by
    default. Combined with what the Observability Audit measured — a
    lock-skipped run writes no artifact, a Candidate lost to a crash writes
    none — that closed the last automatic channel: nothing told anyone.

    Worse, the one nonzero exit was an *uncaught exception*. So the failures
    handled gracefully were exactly the invisible ones, while an unhandled
    crash was the only thing that reported.

    What it is now (`runsummary`): every component's failure is classified
    by severity, folded into one Overall Status, and mapped to an exit code.

        SUCCESS   0
        DEGRADED  3   something needs a person; Company History is intact
        FAILED    2   a CRITICAL component failed

    Three values rather than two, because this pipeline's whole design is
    that most failures are neither fine nor fatal (README RULE 5/9). The
    open question the previous version of this docstring named — "a non-zero
    code on `collector_summary.failed > 0` would make an ordinary malformed
    Event look like a system failure" — is answered by that middle value
    plus severity: a malformed Event is a *metric* on the collector
    component, not a component failure, so it does not change the exit code
    at all.
    """

    def _function(self, name):
        source = (REPO_ROOT / "run_company_ops.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        return next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == name
        )

    def _main_function(self):
        return self._function("main")

    def test_the_reporting_path_derives_its_exit_code_from_the_summary(self):
        """The inversion of the old property: `_print_result()` used to
        return a literal 0 on every path. It must now return whatever the
        Run Summary's classification says, and must not hardcode a code."""
        returns = [
            ast.unparse(node.value) if node.value else "None"
            for node in ast.walk(self._function("_print_result"))
            if isinstance(node, ast.Return)
        ]

        self.assertTrue(returns, "_print_result() has no return statement")
        self.assertIn("_report_run_summary(result)", returns)
        self.assertNotIn("2", returns, "an exit code is hardcoded here")
        self.assertNotIn("3", returns, "an exit code is hardcoded here")

    def test_every_overall_status_maps_to_exactly_one_exit_code(self):
        """The mapping is total and injective — no status can fall through
        to an accidental 0, and no two statuses can be confused."""
        from runsummary import OverallStatus, exit_code_for

        codes = {status: exit_code_for(status) for status in OverallStatus}

        self.assertEqual(len(set(codes.values())), len(OverallStatus))
        self.assertEqual(codes[OverallStatus.SUCCESS], 0)

    def test_the_degraded_code_agrees_with_ops_status(self):
        """Both entrypoints report to the same operator. `ops_status.py`
        already used 3 for "something needs a person"; the Runner must not
        pick a different number for the same meaning."""
        from runsummary import EXIT_DEGRADED

        ops_status = (REPO_ROOT / "ops_status.py").read_text(encoding="utf-8")

        self.assertEqual(EXIT_DEGRADED, 3)
        self.assertIn("3   at least one thing needs a person", ops_status)

    def test_the_config_error_code_is_not_reused_by_the_run_contract(self):
        """1 means "the run never started". Reusing it for a run that
        finished would make a scheduled task's history unreadable."""
        from runsummary import OverallStatus, exit_code_for

        self.assertNotIn(1, {exit_code_for(s) for s in OverallStatus})

    def test_an_ordinary_malformed_event_does_not_change_the_exit_code(self):
        """The question the old docstring left open, now answered in code.

        `collector_summary.failed > 0` means one Event file could not be
        processed — docs/03 §53 makes that per-file isolation by design. It
        is recorded as a *metric* on a SUCCESS component, so it cannot make
        an ordinary day look like a system failure.
        """
        from runsummary import ComponentResult, ComponentStatus, OverallStatus, overall_status

        collector = ComponentResult(
            name="collector",
            status=ComponentStatus.SUCCESS,
            metrics={"accepted": 3, "failed": 1},
        )

        self.assertEqual(overall_status([collector]), OverallStatus.SUCCESS)

    def test_the_scheduler_failure_detail_prints_in_report_order(self):
        """Executes `_print_result()` instead of parsing it.

        Every other test in this class reads the function with `ast`, and
        that is what let a real defect through: the failure detail was first
        written to stderr, and because Python flushes the two streams
        independently, it appeared ABOVE the "Daily History (Scheduler):
        FAILED" line it explains — an explanation detached from the thing
        explained. AST analysis cannot see stream ordering; running it can.

        Two properties, both of which the bug broke or nearly broke:
        the detail follows its own line, and the exit code is still 0
        (this run completed — `_report_backup_failure()` is the aborted
        case, and that one does belong on stderr).
        """
        import io
        import contextlib
        import importlib

        from backup.log import BackupLogEntry
        from backup.result import BackupStatus
        from collector.runtime import RuntimeSummary
        from scheduler.result import SchedulerRunResult, SchedulerStatus

        sys.path.insert(0, str(REPO_ROOT))
        try:
            run_company_ops = importlib.import_module("run_company_ops")
        finally:
            sys.path.remove(str(REPO_ROOT))

        now = datetime(2026, 8, 11, 11, 0).astimezone()
        result = (
            type("Intake", (), {"moved": ()})(),
            RuntimeSummary(accepted=1, duplicate=0, rejected=0, failed=0, files=()),
            SchedulerRunResult(
                status=SchedulerStatus.FAILED,
                generated_dates=(),
                failed_date=date(2026, 8, 7),
                error="PermissionError: daily/2026-08-07.md",
            ),
            BackupLogEntry(
                run_id="RUN-1",
                backup_start=now,
                source="local_master",
                changed_files=(),
                deleted_files=(),
                commit_hash=None,
                push_result=None,
                backup_end=now,
                final_status=BackupStatus.NOT_REQUIRED,
            ),
            (),
        )

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = run_company_ops._print_result(result)

        self.assertEqual(code, 0, "a completed run must still exit 0")

        lines = out.getvalue().splitlines()
        status_at = next(i for i, ln in enumerate(lines) if "Scheduler): FAILED" in ln)
        detail_at = next(i for i, ln in enumerate(lines) if "실패 날짜" in ln)
        self.assertGreater(
            detail_at, status_at, "the explanation printed before the line it explains"
        )
        self.assertIn("2026-08-07", lines[detail_at])
        self.assertIn("PermissionError", "\n".join(lines))
        # One stream, so redirection and ordering stay predictable.
        self.assertEqual(err.getvalue(), "")

    def test_a_raised_backup_failure_exits_nonzero_with_an_explanation(self):
        """NOT a change to the exit-code policy this class characterizes.

        A Backup `GitOperationError` always exited nonzero — it propagated
        as an unhandled exception, which is exactly what this docstring
        calls "the one nonzero exit". What changed is that the operator now
        gets an explanation and a defined code instead of a traceback. The
        signals BUG-36 is about — `final_status`, `scheduler_result.status`,
        `collector_summary.failed` — are still printed and still ignored, on
        the `_print_result()` path above.
        """
        reporter = ast.unparse(self._function("_report_backup_failure"))

        # It still exits nonzero and still explains itself...
        self.assertIn("is_authentication_failure", reporter)
        self.assertIn("return 2", reporter)
        # ...but 2 is now only the FALLBACK. The code the process actually
        # returns comes from the Run Manifest, which `run_once()` writes in
        # its `finally` and which has already classified this failure.
        #
        # Measured before this change, against a real broken git remote: the
        # manifest said DEGRADED/exit 3 while the process exited 2. Two
        # answers to "how bad was this run", and the scheduled task only
        # ever sees the process one.
        self.assertIn("summary.exit_code", reporter)
        self.assertIn("read_summary", reporter)

    def test_a_backup_failure_is_reachable_and_would_exit_zero(self):
        """The failure really happens — the Secret Scan gate produces it."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)

        master = root / "master"
        (master / "daily").mkdir(parents=True)
        (master / ".env").write_text("NOTION_API_TOKEN=placeholder", encoding="utf-8")

        working_copy = root / "wc"
        working_copy.mkdir()
        bare = root / "remote.git"

        def git(args, cwd):
            subprocess.run(
                ["git", *args], cwd=cwd, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )

        git(["init", "--bare", "-b", "main", str(bare)], root)
        git(["init", "-b", "main"], working_copy)
        git(["config", "user.email", "t@example.invalid"], working_copy)
        git(["config", "user.name", "Exit Code Test"], working_copy)
        git(["remote", "add", "origin", str(bare)], working_copy)
        (working_copy / ".gitkeep").write_text("", encoding="utf-8")
        git(["add", "-A"], working_copy)
        git(["commit", "-m", "init"], working_copy)
        git(["push", "-u", "origin", "main"], working_copy)

        self.addCleanup(_force_rmtree_if_present, working_copy)

        result = runner_module.run_once(
            local_master_dir=master,
            backup_working_copy_dir=working_copy,
            history_start_date=date(2026, 8, 1),
            runner_lock_path=root / "lock",
            transport_dir=root / "transport",
            incoming_dir=root / "incoming",
            processed_dir=root / "processed",
            rejected_dir=root / "rejected",
            collector_log_path=root / "collector.log",
            late_update_log_path=root / "daily_late_update.log",
            monthly_state_path=root / "monthly_history_state.json",
            run_summary_path=root / "last_run.json",
            collector_state_path=root / "collector_state.json",
            keep_dir=root / "keep",
            review_dir=root / "review",
            scheduler_state_path=root / "scheduler_state.json",
            backup_state_path=root / "backup_state.json",
        )

        backup_entry = result[3]
        self.assertEqual(backup_entry.final_status.value, "BACKUP_FAILED")
        self.assertIn("secret files detected", backup_entry.push_result)


def _force_rmtree_if_present(path: Path) -> None:
    """shutil.rmtree's `onexc` callback was added in Python 3.12; `onerror`
    (deprecated there, still the only option before it) has a different
    callback signature, so which kwarg to pass has to be chosen at runtime.
    """
    if path.exists():
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


class ResultFieldConsumptionTests(unittest.TestCase):
    """BUG-39 — RESOLVED. Kept as the guard that it stays resolved.

    Each pipeline stage returns a result object. Measured across the two
    consumers (`app/runner.py` and `run_company_ops.py`), only 9 of 23
    fields were ever read; the other 14 were computed correctly and then
    discarded at process exit. They were not incidental — they were the
    diagnostics:

        IntakeSummary.failed / skipped_*        which Events did not make
                                                it in, and why (BUG-30)
        SchedulerRunResult.failed_date / error  where the Daily Close died
        BackupLogEntry.push_result / commit_hash / changed_files / ...
                                                docs/08 section 68's Backup Log
                                                (BUG-37)

    That framing is what made the fix one change instead of several: this
    was never several missing features, it was **one missing sink**. Adding
    the Run Summary consumed all of them at once.

    Now 19 of 23. The four exceptions are deliberate and named in
    `test_the_backup_diagnostics_are_now_consumed`: they duplicate fields
    the manifest already carries at run level, and copying them down into a
    component would let the manifest disagree with itself.

    Still open, and a different problem: BUG-36 (the process exit code) was
    the same shape at the process boundary. It is addressed by
    `runsummary.exit_code_for()` — see `ExitCodeContractTests`.
    """

    CONSUMERS = ("src/app/runner.py", "run_company_ops.py")

    def _consumer_text(self):
        return "\n".join(
            (REPO_ROOT / path).read_text(encoding="utf-8") for path in self.CONSUMERS
        )

    def _unread_fields(self, cls, variable_name):
        import dataclasses

        text = self._consumer_text()
        return [
            f.name
            for f in dataclasses.fields(cls)
            if f"{variable_name}.{f.name}" not in text
        ]

    def test_the_collector_summary_is_fully_consumed(self):
        """The one stage that is — so the others are a real gap, not a style."""
        from collector.runtime import RuntimeSummary

        self.assertEqual(self._unread_fields(RuntimeSummary, "collector_summary"), [])

    def test_the_backup_diagnostics_are_now_consumed(self):
        """docs/08 section 68's Backup Log content (BUG-37), which existed on
        the entry and reached no artifact.

        The four still unread are not diagnostics — `run_id`, `backup_start`
        and `backup_end` are carried by the Run Summary itself (its own
        `run_id` / `started_at` / `finished_at`), and `source` is constant
        for this pipeline. Copying them into a component's metrics would
        make the manifest disagree with itself the first time they drifted.
        """
        from backup.log import BackupLogEntry

        unread = self._unread_fields(BackupLogEntry, "backup_entry")

        for diagnostic in ("push_result", "commit_hash", "changed_files", "deleted_files"):
            with self.subTest(field=diagnostic):
                self.assertNotIn(diagnostic, unread)
        self.assertEqual(sorted(unread), ["backup_end", "backup_start", "run_id", "source"])

    def test_the_intake_summary_is_fully_consumed(self):
        """BUG-30's blind half: which Events did not make it in, and why.

        Read by direct attribute access rather than `getattr(..., default)`,
        which is what this test can actually see — and the reason to prefer
        it: a default would report 0 skipped files forever on the day a
        field is renamed, instead of failing.
        """
        from transport.intake import IntakeSummary

        self.assertEqual(self._unread_fields(IntakeSummary, "intake_summary"), [])

    def test_the_scheduler_failure_detail_is_now_consumed(self):
        """FIXED — this was the sharpest instance of BUG-39.

        `failed_date` and `error` were populated on every failed Daily Close
        and read by nobody, so the operator saw `FAILED, generated=[]` and
        nothing more. Scheduler stops at the first failing date, which means
        that date and every later one still have no Daily file — the two
        discarded fields were the only record of where the next run must
        resume.

        Both consumers now read them: `app/runner.py` writes a
        `SCHEDULER_FAILED date=... <reason>` line to daily_late_update.log
        (where Monthly failures already go), and `run_company_ops.py` prints
        them to stderr. No new artifact, no new format — BUG-39's general
        "one run-summary sink" is still open, and still a decision.
        """
        from scheduler.result import SchedulerRunResult

        self.assertEqual(self._unread_fields(SchedulerRunResult, "scheduler_result"), [])

    def test_the_scheduler_still_sets_the_fields_its_consumers_read(self):
        """The producing half of the same contract: if scheduler.py stopped
        populating these, both consumers would silently print "unknown"."""
        scheduler_source = (SRC / "scheduler" / "scheduler.py").read_text(encoding="utf-8")

        self.assertIn("failed_date=", scheduler_source)
        self.assertIn("error=str(exc)", scheduler_source)


class DashboardSchemaMappingTests(unittest.TestCase):
    """Adopted decisions: Notion Dashboard / Dashboard Bootstrap.

    These matter *because* of audit finding GAP-1: no entrypoint passes a
    dashboard_client, so none of this code has ever run against real Notion.
    A name or type mismatch between what `record_run()` emits and what
    `bootstrap_dashboard_databases()` creates would therefore stay invisible
    until the day the Dashboard is finally wired — and then every run would
    fail with an HTTP 400.

    Verified: OPS_RUNS is exact — 13 properties, every name present in the
    schema, every Notion type identical. Nothing extra, nothing missing.

    Audit finding GAP-11 (new): bootstrap creates FIVE databases, but only
    OPS_RUNS is ever written to.

        OPS_RUNS         13 props   record_run()                  writes
        OPS_BACKUP        7 props   build_ops_backup_properties() exists,
                                    exported, but no caller anywhere
        OPS_NOTION_SYNC   5 props   no builder, no writer
        OPS_RISK          6 props   no builder, no writer
        OPS_READINESS    12 props   no builder, no writer

    So an operator who runs the one-time bootstrap gets four permanently
    empty databases in the Company Ops Notion page. Recorded, not asserted as
    correct — the schemas may well be intentional groundwork for a later
    Sprint, but nothing documents that.
    """

    def _sample_run_properties(self):
        from notion.dashboard import build_ops_run_properties

        return build_ops_run_properties(
            run_id="RUN-1",
            run_at=datetime(2026, 8, 5, 11, 0).astimezone(),
            transport_moved=1,
            accepted=2,
            duplicate=0,
            rejected=0,
            failed=0,
            scheduler_status="COMPLETED",
            generated_days=1,
            backup_status="BACKUP_SUCCESS",
            notion_synced=2,
            notion_retried=0,
        )

    def test_record_run_emits_no_property_absent_from_the_ops_runs_schema(self):
        """The mismatch that would 400 on the first real Dashboard write."""
        from notion.dashboard import DASHBOARD_DATABASES, OPS_RUNS

        schema = set(DASHBOARD_DATABASES[OPS_RUNS])
        emitted = set(self._sample_run_properties())
        self.assertEqual(emitted - schema, set())

    def test_every_ops_runs_schema_property_is_populated(self):
        from notion.dashboard import DASHBOARD_DATABASES, OPS_RUNS

        schema = set(DASHBOARD_DATABASES[OPS_RUNS])
        emitted = set(self._sample_run_properties())
        self.assertEqual(schema - emitted, set())

    def test_every_emitted_property_uses_the_schema_declared_notion_type(self):
        from notion.dashboard import DASHBOARD_DATABASES, OPS_RUNS

        schema = DASHBOARD_DATABASES[OPS_RUNS]
        for name, value in self._sample_run_properties().items():
            with self.subTest(property=name):
                self.assertEqual(next(iter(value)), next(iter(schema[name])))

    def test_ops_backup_builder_matches_its_schema_too(self):
        from notion.dashboard import DASHBOARD_DATABASES, OPS_BACKUP, build_ops_backup_properties

        schema = DASHBOARD_DATABASES[OPS_BACKUP]
        properties = build_ops_backup_properties(
            run_id="RUN-1",
            backup_at=datetime(2026, 8, 5, 11, 0).astimezone(),
            commit_hash="abc1234",
            changed_files=3,
            deleted_files=0,
            push_result="SUCCESS",
            final_status="BACKUP_SUCCESS",
        )
        self.assertEqual(set(schema) - set(properties), set())
        self.assertEqual(set(properties) - set(schema), set())
        for name, value in properties.items():
            with self.subTest(property=name):
                self.assertEqual(next(iter(value)), next(iter(schema[name])))

    def test_only_ops_runs_has_a_writer(self):
        """GAP-11 characterization.

        The single write went through `client.create_project()` until a
        find-before-create guard was added for the duplicate-row defect; it
        now goes through `client.find_or_create_by_title()`. Still exactly
        one writer, which is what this pins — GAP-11 is about the other four
        OPS_* databases having none, not about how the one writer writes.
        """
        import notion.dashboard as dashboard

        source = Path(dashboard.__file__).read_text(encoding="utf-8")
        # record_run() is the single function that creates an OPS_RUNS row.
        writers = re.findall(r"client\.(?:create_project|find_or_create_by_title)\(", source)
        self.assertEqual(len(writers), 1)
        self.assertIn("def record_run(", source)
        for absent in ("def record_backup(", "def record_notion_sync(",
                       "def record_risk(", "def record_readiness("):
            self.assertNotIn(absent, source)

    def test_ops_backup_builder_has_no_caller(self):
        """It is built and exported, but nothing ever invokes it."""
        callers = 0
        for path in list(SRC.rglob("*.py")) + list(REPO_ROOT.glob("*.py")):
            if "__pycache__" in str(path):
                continue
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"build_ops_backup_properties\s*\(", text):
                line_start = text.rfind("\n", 0, match.start()) + 1
                line = text[line_start : text.find("\n", match.start())]
                if line.strip().startswith("def "):
                    continue
                callers += 1
        self.assertEqual(callers, 0)

    def test_bootstrap_creates_more_databases_than_are_ever_populated(self):
        from notion.dashboard import DASHBOARD_DATABASES

        self.assertEqual(len(DASHBOARD_DATABASES), 5)
        # Only OPS_RUNS receives rows today.
        self.assertEqual(len(DASHBOARD_DATABASES) - 1, 4)


class TransportIntakeConcurrencySafetyTests(unittest.TestCase):
    """Transport intake does the same thing Collector does — move a file into
    another directory — and it does it CORRECTLY under concurrency.

    Measured: 4 concurrent processes, 10 Events, 5 trials (50 Events total)
    -> 0 Events lost, 0 exceptions raised. Every process reported a clean
    summary; the losers of each move race simply recorded `failed`, and the
    file was already safely in incoming/ because the winner had moved it.

    Why it works, and why Collector (BUG-9) does not:

        transport/intake.py     try: os.replace(...) except OSError: failed
                                -> the race has NO side effect; nothing is
                                   recorded before the move succeeds.

        collector/runtime.py    collector.collect() persists mark_seen()
                                BEFORE run_once() attempts the move
                                -> the loser has already burned the event_id.

    So the correct pattern is not missing from this repository — it is one
    module away from the defect. That is the same "the principle is applied
    in some paths but not others" shape as the sanitisation asymmetry
    (reporter.local_output vs transport.onedrive) and the permanent/transient
    failure asymmetry (backup vs notion).

    These tests pin the safe behaviour so it is not lost in a refactor.
    """

    EVENTS = 8
    PROCESSES = 4

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for name in ("transport", "incoming", "processed", "rejected"):
            (self.root / name).mkdir(parents=True, exist_ok=True)

        for i in range(self.EVENTS):
            event = create_event(
                source="DESKTOP_1", role="COO", project_id="PRJ-INTAKE",
                event_type="STARTED", status="IN_PROGRESS", summary="intake race",
                history_candidate=True, event_id=f"INTAKE-{i:03d}",
                timestamp="2026-08-01T10:00:00+09:00",
            )
            path = self.root / "transport" / f"INTAKE-{i:03d}.json"
            path.write_text(event.to_json(), encoding="utf-8")
            old = time.time() - 60
            os.utime(path, (old, old))

        self.script = self.root / "intake_probe.py"
        self.script.write_text(
            "import sys, time\n"
            "from pathlib import Path\n"
            f"sys.path.insert(0, r'{SRC}')\n"
            "from transport import run_intake\n"
            "root, until = Path(sys.argv[1]), float(sys.argv[2])\n"
            "while time.time() < until:\n"
            "    pass\n"
            "try:\n"
            "    s = run_intake(transport_dir=root / 'transport',\n"
            "        incoming_dir=root / 'incoming', processed_dir=root / 'processed',\n"
            "        rejected_dir=root / 'rejected', stable_after_seconds=0.0)\n"
            "    print(f'OK {len(s.moved)} {len(s.failed)}')\n"
            "except Exception as exc:\n"
            "    print('RAISED:' + type(exc).__name__)\n",
            encoding="utf-8",
        )

    def _race(self):
        start_at = time.time() + 0.4
        procs = [
            subprocess.Popen(
                [sys.executable, str(self.script), str(self.root), str(start_at)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            for _ in range(self.PROCESSES)
        ]
        return [(p.communicate()[0] or "").strip().splitlines()[-1] for p in procs]

    def test_concurrent_intake_never_loses_an_event(self):
        self._race()
        landed = len(list((self.root / "incoming").glob("*.json")))
        still_queued = len(list((self.root / "transport").glob("*.json")))
        self.assertEqual(landed + still_queued, self.EVENTS)

    def test_concurrent_intake_never_raises(self):
        """The move race is absorbed into the summary, not propagated."""
        verdicts = self._race()
        raised = [v for v in verdicts if v.startswith("RAISED:")]
        self.assertEqual(raised, [], verdicts)

    def test_intake_guards_its_move_with_an_oserror_handler(self):
        """The structural reason it is safe — and precisely what
        collector/runtime.py does before its own move instead."""
        intake = inspect.getsource(sys.modules["transport.intake"].run_intake)
        move_block = intake[intake.index("os.replace(path, incoming_dir"):]
        self.assertIn("except OSError", move_block)
        self.assertIn("failed.append", move_block)

    def test_intake_records_nothing_before_the_move_succeeds(self):
        """`moved.append(...)` happens only after os.replace() returns."""
        intake = inspect.getsource(sys.modules["transport.intake"].run_intake)
        replace_at = intake.index("os.replace(path, incoming_dir")
        moved_at = intake.index("moved.append(path.name)")
        self.assertLess(replace_at, moved_at)


class WriteAmplificationCharacterizationTests(unittest.TestCase):
    """Performance characterization (audit priority item 16).

    Three runtime stores rewrite their ENTIRE file on every single-item
    update, which makes a batch of n updates cost O(n^2) bytes:

        notion/retry_queue.enqueue()          load_queue() + save_queue()
        notion/dashboard_pending.save_pending() load_pending() + save_all()
        collector/state.mark_seen()           rewrites the full sorted id set

    Measured this Sprint (single process, warm cache):

        queue size   total     ms/enqueue   file
                50    0.40s          7.93    34KB
               200    2.49s         12.46   138KB
               400    6.30s         15.76   275KB
               800   15.48s         19.34   551KB

        collector_state.json: 8000 ids -> 141KB, 2.22 ms/mark_seen

    At the intended volume (a few Events per day) this is irrelevant, and the
    simplicity buys atomicity and inspectability — a deliberate trade, not an
    oversight. It only matters after a long Notion outage backs the queue up,
    which is exactly when BUG-13's unbounded retry makes the queue grow.

    These tests assert the *structure* rather than wall-clock timings, so they
    stay meaningful on any machine and never flake. If a future change adds
    incremental/append writes, they should be rewritten, not deleted.
    """

    def test_retry_queue_enqueue_reads_and_rewrites_the_whole_file(self):
        source = inspect.getsource(sys.modules["notion.retry_queue"].enqueue)
        self.assertIn("load_queue(path)", source)
        self.assertIn("save_queue(path, entries)", source)

    def test_dashboard_save_pending_reads_and_rewrites_the_whole_file(self):
        import notion.dashboard_pending as dashboard_pending

        source = inspect.getsource(dashboard_pending.save_pending)
        self.assertIn("load_pending(path)", source)
        self.assertIn("save_all(path, records)", source)

    def test_collector_mark_seen_rewrites_the_full_id_set(self):
        from collector.state import PersistentSeenEventStore

        mark_seen = inspect.getsource(PersistentSeenEventStore.mark_seen)
        save = inspect.getsource(PersistentSeenEventStore._save)
        self.assertIn("self._save()", mark_seen)
        self.assertIn("sorted(self._seen_ids)", save)

    def test_collector_state_has_no_retention_or_pruning_rule(self):
        """collector_state.json grows for the lifetime of the deployment
        (~141KB at 8000 events, measured this Sprint): docs/03 defines no
        retention policy, and nothing time- or size-based ever removes an id.

        The id set has exactly two mutations, and neither is a retention rule:

            add()      mark_seen()   — a new Event was accepted
            discard()  unmark_seen() — roll back an Event that was NOT
                                       consumed after all (BUG-9 fix, B안)

        The rollback removes an id that should never have been recorded, so
        it does not bound growth for genuinely-processed Events.
        """
        source = (SRC / "collector" / "state.py").read_text(encoding="utf-8")

        mutations = set(re.findall(r"self\._seen_ids\.(\w+)\(", source))
        self.assertEqual(mutations, {"add", "discard"}, mutations)

        # The only discard() is the explicit rollback, not a pruning pass.
        # Compare executable lines only — the docstring legitimately contains
        # prose like "for why this exists".
        unmark = inspect.getsource(
            sys.modules["collector.state"].PersistentSeenEventStore.unmark_seen
        )
        body = ast.parse(textwrap.dedent(unmark)).body[0]
        statements = [
            ast.unparse(node)
            for node in body.body
            if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
        ]
        self.assertIn("self._seen_ids.discard(event_id)", statements)
        joined = "\n".join(statements)
        for pruning_marker in ("for ", "while ", "sorted(", "[:", "len(self._seen_ids)"):
            self.assertNotIn(pruning_marker, joined)

        # And nothing rebinds the set to a filtered/truncated version: the only
        # assignments are the empty initialiser and the full reload.
        self.assertIn("self._seen_ids: set[str] = set()", source)
        assignments = re.findall(r"self\._seen_ids\s*=\s*(.+)", source)
        self.assertEqual(assignments, ["set(ids)"], assignments)

    def test_enqueue_cost_grows_with_queue_size(self):
        """A loose, ratio-based sanity check — no absolute threshold, so it
        cannot flake on a slow or fast machine. Only asserts that the queue is
        not O(1) per insert, which is the property being documented."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)

        def bytes_written(count: int) -> int:
            path = root / f"q{count}.json"
            for i in range(count):
                enqueue(
                    path,
                    create_event(
                        source="DESKTOP_1", role="COO", project_id="P",
                        event_type="STARTED", status="IN_PROGRESS",
                        summary="s" * 100, history_candidate=True,
                        event_id=f"WA-{i:05d}",
                        timestamp="2026-08-01T10:00:00+09:00",
                    ),
                    now=datetime(2026, 8, 1, 10, 0).astimezone(),
                )
            return path.stat().st_size

        small = bytes_written(25)
        large = bytes_written(100)
        # 4x the entries -> ~4x the file, and each insert rewrote all of it.
        self.assertGreater(large, small * 3)


class ConcurrentRunnerDataLossTests(unittest.TestCase):
    """Audit finding BUG-20 (P0, the most severe in this Sprint).

    BUG-18 lets more than one Runner into the critical section. This measures
    what that actually costs, and the answer is not "a crash" — it is
    permanent loss of Company History.

    Three concurrent Runners over 12 pre-staged Events, six trials
    (72 Events total):

        History Candidates permanently lost : 26  (36%)
        History Candidates duplicated       :  0
        Events left in incoming/ to retry   :  0
        Events moved to processed/          : 12  (every trial)

    Mechanism — three defects composing:

      1. BUG-18  both Runners acquire the lock and scan the same incoming/.
      2. BUG-9   collector.collect() persists mark_seen() BEFORE the file is
                 moved, so the loser of the move race has already burned the
                 event_id and reports FAILED (or the winner already moved it).
      3. BUG-19  the surviving Runner can then die on a contended state write
                 (PermissionError) or on FileHistoryRepository.save()
                 (FileExistsError) before step 5 has written its candidates.

    The Event file ends up in processed/ either way, and incoming/ is empty,
    so nothing is ever retried. The Execution Event survives as a raw file;
    the History Candidate — the thing Company History is built from — does
    not, and no state anywhere records that it is missing.

    This violates README RULE 7 ("Event와 History가 영구 손실되어서는 안 된다"),
    RULE 9, and docs/10 section 52 (Critical Data).

    Correction to an earlier conclusion in this audit: the state *files* are
    never corrupted (AtomicStateWriteInvariantTests proves that, and it still
    holds). But "no file corruption" is not "no data loss" — the loss happens
    at the pipeline level, above the file layer.
    """

    EVENTS_PER_TRIAL = 12
    TRIALS = 3
    RUNNERS = 3

    def _build_workspace(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)

        working_copy = root / "backup_working_copy"
        bare_remote = root / "backup_remote.git"
        working_copy.mkdir(parents=True, exist_ok=True)

        def git(args, cwd):
            subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)

        git(["init", "--bare", "-b", "main", str(bare_remote)], root)
        git(["init", "-b", "main"], working_copy)
        git(["config", "user.email", "t@example.invalid"], working_copy)
        git(["config", "user.name", "Concurrent Loss Test"], working_copy)
        git(["remote", "add", "origin", str(bare_remote)], working_copy)
        (working_copy / ".gitkeep").write_text("", encoding="utf-8")
        git(["add", "-A"], working_copy)
        git(["commit", "-m", "init"], working_copy)
        git(["push", "-u", "origin", "main"], working_copy)

        incoming = root / "incoming"
        incoming.mkdir(parents=True, exist_ok=True)
        for i in range(self.EVENTS_PER_TRIAL):
            event = create_event(
                source="DESKTOP_1",
                role="COO",
                project_id="PRJ-LOSS",
                event_type="MILESTONE_COMPLETED",
                status="IN_PROGRESS",
                summary=f"event {i}",
                milestone="M1",
                history_candidate=True,
                event_id=f"LOSS-{i:03d}",
                timestamp="2026-08-01T10:00:00+09:00",
            )
            (incoming / f"LOSS-{i:03d}.json").write_text(event.to_json(), encoding="utf-8")

        script = root / "concurrent_runner.py"
        script.write_text(
            "import sys, time\n"
            "from datetime import date, datetime\n"
            "from pathlib import Path\n"
            f"sys.path.insert(0, r'{SRC}')\n"
            "from app.runner import run_once\n"
            f"root = Path(r'{root}')\n"
            "until = float(sys.argv[1])\n"
            "while time.time() < until:\n"
            "    pass\n"
            "try:\n"
            "    run_once(local_master_dir=root / 'local_master',\n"
            "        backup_working_copy_dir=root / 'backup_working_copy',\n"
            "        history_start_date=date(2026, 8, 1),\n"
            "        runner_lock_path=root / 'locks' / 'company_ops.lock',\n"
            "        transport_dir=root / 'transport', incoming_dir=root / 'incoming',\n"
            "        processed_dir=root / 'processed', rejected_dir=root / 'rejected',\n"
            "        collector_log_path=root / 'logs' / 'collector.log',\n"
            "        late_update_log_path=root / 'logs' / 'daily_late_update.log',\n"
            "        monthly_state_path=root / 'state' / 'monthly_history_state.json',\n"
            "        run_summary_path=root / 'runs' / 'last_run.json',\n"
            "        collector_state_path=root / 'state' / 'collector_state.json',\n"
            "        keep_dir=root / 'keep', review_dir=root / 'review',\n"
            "        scheduler_state_path=root / 'state' / 'daily_history_state.json',\n"
            "        backup_state_path=root / 'state' / 'backup_state.json',\n"
            "        now=datetime(2026, 8, 5, 11, 0).astimezone())\n"
            "except Exception:\n"
            "    pass\n",
            encoding="utf-8",
        )
        return root

    def _run_trial(self) -> tuple[int, int, int]:
        root = self._build_workspace()
        start_at = time.time() + 0.4
        procs = [
            subprocess.Popen(
                [sys.executable, str(root / "concurrent_runner.py"), str(start_at)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for _ in range(self.RUNNERS)
        ]
        for proc in procs:
            proc.wait()

        expected = {f"LOSS-{i:03d}" for i in range(self.EVENTS_PER_TRIAL)}
        kept = [p.stem.replace("HIST-", "") for p in (root / "keep").glob("*.json")]
        retryable = {p.stem for p in (root / "incoming").glob("*.json")}
        lost = len(expected - set(kept) - retryable)
        duplicated = len(kept) - len(set(kept))
        return lost, duplicated, len(retryable)

    def test_concurrent_runners_lose_no_history_at_all(self):
        """BUG-20 FIXED (CEO-approved: Lock 원자성 O_EXCL).

        Three concurrent Runners over pre-staged Events must produce zero
        loss. Measured across the fix sequence, 12 Events x 8 trials:

            before any fix          26 / 72 lost  (36%)
            after BUG-9 (mark_seen) 17 / 72 lost  (24%)
            after BUG-18 (O_EXCL)    0 / 96 lost  ( 0%)

        The lock now admits exactly one Runner, so the composing defects have
        nothing to compose: BUG-9's move race cannot occur, and BUG-19's
        contended state write is unreachable from the Runtime.

        README RULE 7 ("Event와 History가 영구 손실되어서는 안 된다") holds.
        """
        for _ in range(self.TRIALS):
            lost, duplicated, retryable = self._run_trial()

            self.assertEqual(lost, 0, "a History Candidate was lost")
            self.assertEqual(duplicated, 0, "a candidate was written twice")
            self.assertEqual(retryable, 0, "an Event was left unconsumed")

    def test_raw_events_are_always_accounted_for_after_a_concurrent_run(self):
        """No Execution Event ever disappears, whoever wins the race."""
        root = self._build_workspace()
        start_at = time.time() + 0.4
        procs = [
            subprocess.Popen(
                [sys.executable, str(root / "concurrent_runner.py"), str(start_at)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for _ in range(self.RUNNERS)
        ]
        for proc in procs:
            proc.wait()

        accounted = len(list((root / "processed").glob("*.json"))) + len(
            list((root / "incoming").glob("*.json"))
        ) + len(list((root / "rejected").glob("*.json")))
        self.assertEqual(accounted, self.EVENTS_PER_TRIAL)

    def test_runner_has_no_per_event_error_handling_in_the_history_step(self):
        """The deterministic reason a mid-step failure loses every remaining
        candidate in the batch: step 5 is a bare loop with no try/except, so
        one failure abandons the rest."""
        source = inspect.getsource(runner_module.run_once)
        history_step = source[source.index("# 5. History Filter") : source.index("# 6. Daily History")]
        self.assertIn("for processed_file in collector_summary.files", history_step)
        self.assertNotIn("try:", history_step)
        self.assertNotIn("except", history_step)


if __name__ == "__main__":
    unittest.main()


class AgentBoundaryInvariantTests(unittest.TestCase):
    """The Agent is the SENDING side. It must not grow a dependency on the
    Desktop 4 collection layer.

    This project has enforced the same kind of boundary since Phase 3 —
    `transport/intake.py` is asserted not to import collector/reporter/daily
    (tests/test_transport_intake.py) — and the Multi-Desktop Agent adds a
    second one worth stating explicitly: an Agent runs on a machine that has
    no Collector, no History Repository, no Notion credentials, and no
    Backup remote. Importing any of them would compile fine here and fail
    (or, worse, quietly work against the wrong directories) on Desktop 1.

    `reporter`, `transport`, `events`, and `scheduler.lock` ARE allowed:
    identity, delivery, the Event Schema, and mutual exclusion are exactly
    what a sending machine needs, and reusing them is why the Agent added no
    parallel implementations.
    """

    FORBIDDEN_FOR_AGENT = ("collector", "notion", "backup", "daily", "history", "app")

    def _imported_top_level_modules(self, path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    names.add(node.module.split(".")[0])
        return names

    def test_the_agent_package_never_imports_the_desktop4_layer(self):
        for path in sorted((SRC / "agent").glob("*.py")):
            with self.subTest(module=path.name):
                imported = self._imported_top_level_modules(path)
                for forbidden in self.FORBIDDEN_FOR_AGENT:
                    self.assertNotIn(
                        forbidden,
                        imported,
                        f"src/agent/{path.name} imports {forbidden!r} — an Agent "
                        f"machine has no such layer",
                    )

    def test_the_agent_package_does_use_the_shared_layers(self):
        """The boundary above is only meaningful if the Agent is in fact
        built on the existing shared modules rather than reimplementing
        them."""
        imported: set[str] = set()
        for path in (SRC / "agent").glob("*.py"):
            imported |= self._imported_top_level_modules(path)

        for expected in ("events", "reporter", "transport", "scheduler"):
            with self.subTest(module=expected):
                self.assertIn(expected, imported)

    def test_the_desktop4_status_view_does_not_depend_on_the_agent(self):
        """Desktop 4 reads Events, not another machine's Agent internals.
        A dependency here would mean the COO view could only be built on a
        machine that also runs an Agent."""
        imported = self._imported_top_level_modules(SRC / "app" / "desktop_activity.py")
        self.assertNotIn("agent", imported)

    def test_late_event_update_runs_before_backup_in_the_runner(self):
        """Ordering invariant, not a style preference: an updated Daily file
        that is written after the Backup step would not reach the backup
        remote until the next run that happens to change something else.
        docs/08 §65's "backup: history late update" commit template exists
        precisely because the update is expected to be in the same run.
        """
        source = inspect.getsource(runner_module.run_once)
        self.assertLess(
            source.index("update_daily_history("),
            source.index("backup_run_once("),
            "Late Event Update must precede Backup",
        )


class MonthlyBoundaryInvariantTests(unittest.TestCase):
    """docs/09 §12-13 do not merely prefer Daily as Monthly's input — they
    give a reason: re-deriving from Events would duplicate the History
    Filter and let Daily and Monthly disagree about the same day.

    The strongest way to guarantee that is structural. `monthly` imports
    nothing from this project at all: not `history`, not `collector`, not
    `events`. It cannot re-apply a filter it has no access to, and it cannot
    read a Repository it cannot import. A future change that reaches for one
    of them fails here rather than producing a Monthly that quietly
    contradicts its own Daily files.
    """

    LOCAL_PACKAGES = {
        "agent", "app", "backup", "collector", "daily", "events",
        "history", "notion", "reporter", "scheduler", "transport",
    }

    def _project_imports(self, path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
        return found & self.LOCAL_PACKAGES

    def test_monthly_imports_nothing_from_the_rest_of_the_project(self):
        for path in sorted((SRC / "monthly").glob("*.py")):
            with self.subTest(module=path.name):
                self.assertEqual(
                    self._project_imports(path),
                    set(),
                    f"src/monthly/{path.name} reaches outside the package; "
                    f"docs/09 §13 requires Monthly to consolidate Daily files only",
                )

    def test_monthly_reads_no_event_or_repository_path(self):
        """The same rule from the other side: even without an import, a
        hardcoded path into `history_candidates/` or `events/` would
        reintroduce the coupling."""
        for path in sorted((SRC / "monthly").glob("*.py")):
            text = path.read_text(encoding="utf-8")
            code = "\n".join(
                line for line in text.splitlines() if not line.strip().startswith("#")
            )
            for forbidden in ("history_candidates", "events/incoming", "processed"):
                with self.subTest(module=path.name, path=forbidden):
                    self.assertNotIn(forbidden, code)

    def test_monthly_consolidation_runs_after_daily_and_before_backup(self):
        """docs/09 §50's order. Before Daily, the month would be consolidated
        from files the Scheduler is about to create; after Backup, a new
        Monthly would sit unbacked-up until something else changed."""
        source = inspect.getsource(runner_module.run_once)
        self.assertLess(
            source.index("scheduler_run_once("),
            source.index("monthly_run_once("),
            "Monthly must follow Daily Catch-up",
        )
        self.assertLess(
            source.index("monthly_run_once("),
            source.index("backup_run_once("),
            "Monthly must precede Backup",
        )

    def test_a_dirty_month_is_marked_before_it_is_consolidated(self):
        """docs/09 §55-57: the Late Event marks the month, and the same run
        rebuilds it. Marking after consolidation would leave the Monthly
        disagreeing with its Daily until the following run."""
        # Comment lines stripped: the step's own comment names
        # monthly_run_once() while explaining the ordering, which would put
        # the mention before the call it is describing.
        source = "\n".join(
            line
            for line in inspect.getsource(runner_module.run_once).splitlines()
            if not line.strip().startswith("#")
        )
        self.assertLess(
            source.index("mark_month_dirty("),
            source.index("monthly_run_once("),
        )


class LayeringInvariantTests(unittest.TestCase):
    """The whole dependency graph in one place, derived from disk.

    Eight test files carry a near-identical pair of boundary tests ("package
    X must not import Y", "no hardcoded absolute paths"). Each covers exactly
    one package, and each was written when that package was written — so the
    rule a new package must obey is enforced only if somebody remembers to
    copy the pair into a new file. Nothing failed when `oplog.py` was added.

    Two properties those per-package tests structurally cannot give:

      * **completeness** — every package is checked, because the package list
        comes from `SRC.iterdir()` rather than from whichever files exist;
      * **acyclicity** — a cycle is a property of the graph, not of any one
        package, so no per-package test can see one.

    The allowed-edge table below is the layering this project already has,
    written down. It is deliberately a table of what each package MAY import
    rather than what it may not: a forbidden-list silently permits anything
    nobody thought to forbid, which is how a new package would slip in.
    """

    # package -> packages it is allowed to import. Leaf packages map to an
    # empty set. `app` is the composition root and may use anything.
    ALLOWED = {
        "events": set(),
        "oplog": set(),
        # Like `oplog`: vocabulary and arithmetic, no project imports. It
        # must stay a leaf for the same reason — `app` is its only consumer
        # today, but the Run Contract is meant to be readable by
        # `ops_status.py` and anything else that reports on a run, and a
        # module those can all import must sit below all of them.
        "runsummary": set(),
        "transport": {"events"},
        "reporter": {"events", "transport"},
        "history": {"events"},
        "notion": {"events"},
        "collector": {"events", "oplog"},
        "daily": {"events", "history"},
        "scheduler": {"daily", "history"},
        "monthly": set(),
        "backup": set(),
        "agent": {"events", "oplog", "reporter", "scheduler", "transport"},
        "review_cli": {"history"},
        "app": None,  # composition root: unrestricted
    }

    def _packages(self):
        names = {
            p.name for p in SRC.iterdir() if p.is_dir() and p.name != "__pycache__"
        }
        names |= {p.stem for p in SRC.glob("*.py") if not p.stem.startswith("__")}
        return names

    def _sources(self, package):
        target = SRC / package
        return sorted(target.rglob("*.py")) if target.is_dir() else [SRC / f"{package}.py"]

    def _edges(self):
        """package -> set of sibling packages it imports."""
        packages = self._packages()
        edges = {}
        for package in packages:
            imported = set()
            for path in self._sources(package):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        names = [a.name.split(".")[0] for a in node.names]
                    elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                        names = [node.module.split(".")[0]]
                    else:
                        continue
                    imported |= {n for n in names if n in packages and n != package}
            edges[package] = imported
        return edges

    def test_every_package_on_disk_has_a_declared_layer(self):
        """The completeness property. A package added without a row here
        fails immediately, instead of being the one package no boundary test
        covers."""
        self.assertEqual(
            self._packages() - set(self.ALLOWED),
            set(),
            "a package under src/ has no entry in ALLOWED — declare its layer",
        )

    def test_no_declared_layer_refers_to_a_package_that_is_gone(self):
        """The other direction: a stale row would quietly permit nothing."""
        self.assertEqual(
            set(self.ALLOWED) - self._packages(),
            set(),
            "ALLOWED names a package that no longer exists",
        )

    def test_no_package_imports_outside_its_declared_layer(self):
        edges = self._edges()
        for package, allowed in sorted(self.ALLOWED.items()):
            if allowed is None:
                continue
            with self.subTest(package=package):
                self.assertEqual(
                    edges.get(package, set()) - allowed,
                    set(),
                    f"src/{package} imports outside its declared layer",
                )

    def test_the_declared_layers_are_not_wider_than_reality(self):
        """An entry nobody uses is a permission granted for no reason, and it
        would silently authorise the import the day someone adds it."""
        edges = self._edges()
        for package, allowed in sorted(self.ALLOWED.items()):
            if allowed is None:
                continue
            with self.subTest(package=package):
                self.assertEqual(
                    allowed - edges.get(package, set()),
                    set(),
                    f"src/{package} is allowed imports it does not make",
                )

    def test_the_dependency_graph_is_acyclic(self):
        """Not visible to any per-package test: a cycle is a property of the
        graph. Reported as a concrete path so it can be acted on."""
        edges = self._edges()
        state = {}
        cycles = []

        def visit(node, stack):
            state[node] = "open"
            stack.append(node)
            for nxt in sorted(edges.get(node, ())):
                if state.get(nxt) == "open":
                    cycles.append(" -> ".join(stack[stack.index(nxt):] + [nxt]))
                elif state.get(nxt) is None:
                    visit(nxt, stack)
            stack.pop()
            state[node] = "closed"

        for package in sorted(self._packages()):
            if state.get(package) is None:
                visit(package, [])

        self.assertEqual(cycles, [], f"import cycle(s): {cycles}")

    def test_the_shared_log_writer_sits_below_everything(self):
        """`oplog` is imported by `collector`, `agent` and `app`, and `app`
        depends on the other two — so it has to be a leaf or it closes a
        cycle. Stated separately because it is the reason the module is
        top-level rather than inside any package."""
        edges = self._edges()

        self.assertEqual(edges["oplog"], set())
        for consumer in ("collector", "agent", "app"):
            with self.subTest(consumer=consumer):
                self.assertIn("oplog", edges[consumer])


class BackupLogIsNeverPersistedTests(unittest.TestCase):
    """CHARACTERIZATION: docs/08 §68-69's Backup Log is not written anywhere.

    §68 lists nine minimum fields and §69 shows `runtime/logs/backup/` as
    its location. `BackupLogEntry` carries exactly those nine fields and has
    a complete `to_dict` / `to_json` / `from_dict` / `from_json` pair — and
    **no caller invokes any of them.** `backup.runner.run_once()` builds the
    entry, returns it, and `app/runner.py` reads a handful of attributes off
    it for the Run Manifest and the Dashboard. Nothing reaches disk.

    Found by tracing which lines of `src/` the whole suite never executes:
    all four serialisation methods came back with zero executions, which for
    a fully-built round-trip API means "written for a writer that does not
    exist".

    Why this is characterised rather than fixed. Writing it means creating
    `runtime/logs/backup/` — a new persistent artifact path. docs/14 §2
    fixes the Artifact Taxonomy and states, as a deliberate property, that
    the Run Manifest was "신설한 것은 `runtime/runs/last_run.json` 하나뿐";
    its `logs/` list names collector / notion_sync / daily_late_update and
    no backup log. Adding one means the taxonomy and `_ARTIFACT_REFS` change
    together, and docs/ is Spec. So the gap is pinned here and recorded in
    BACKLOG rather than closed by implementation.

    What is NOT lost meanwhile: every §68 field except the two timestamps
    already reaches an operator through the Run Manifest's `backup`
    component (`commit_hash`, `push_result` as the failure reason,
    `changed_files` count) and through `backup_state.json`
    (`last_successful_backup`, `last_backup_commit`, `backup_status`). The
    missing artifact is the per-run *history* of those — one row per run,
    kept over time — which is what §68 asks for.

    If this test starts failing, a writer was added; it should then be
    rewritten to assert the log's location and contents.
    """

    _SERIALISERS = ("to_dict", "to_json", "from_dict", "from_json")

    def _python_files(self):
        for path in list(SRC.rglob("*.py")) + list(REPO_ROOT.glob("*.py")):
            if "__pycache__" in str(path):
                continue
            yield path

    def test_the_entry_carries_every_field_section_68_requires(self):
        """The data exists. Only the sink is missing — which is what makes
        this a gap rather than a redesign."""
        from backup.log import BackupLogEntry

        fields = set(BackupLogEntry.__dataclass_fields__)
        for required in (
            "run_id",
            "backup_start",
            "source",
            "changed_files",
            "deleted_files",
            "commit_hash",
            "push_result",
            "backup_end",
            "final_status",
        ):
            with self.subTest(field=required):
                self.assertIn(required, fields)

    def test_the_serialisers_round_trip_correctly(self):
        """They work. Nothing calls them."""
        from backup.log import BackupLogEntry
        from backup.result import BackupStatus

        entry = BackupLogEntry(
            run_id="RUN-1",
            backup_start=datetime(2026, 8, 5, 11, 0).astimezone(),
            source="C:/master",
            changed_files=("daily/2026-08-05.md",),
            deleted_files=(),
            commit_hash="abc1234",
            push_result="SUCCESS",
            backup_end=datetime(2026, 8, 5, 11, 1).astimezone(),
            final_status=BackupStatus.SUCCESS,
        )

        self.assertEqual(BackupLogEntry.from_json(entry.to_json()), entry)

    def test_nothing_in_the_repository_serialises_a_backup_log_entry(self):
        callers = 0
        for path in self._python_files():
            text = path.read_text(encoding="utf-8")
            if "backup" not in str(path) and "BackupLogEntry" not in text:
                continue
            for name in self._SERIALISERS:
                for match in re.finditer(rf"\b{name}\s*\(", text):
                    line_start = text.rfind("\n", 0, match.start()) + 1
                    line = text[line_start : text.find("\n", match.start())]
                    if line.strip().startswith(("def ", "@classmethod")):
                        continue
                    # Other classes have same-named methods; only count the
                    # ones reached through a BackupLogEntry.
                    if "BackupLogEntry" in line or "backup_entry" in line:
                        callers += 1
        self.assertEqual(callers, 0)

    def test_no_backup_log_directory_is_ever_created(self):
        """§69's location. Nothing in the source names it."""
        for path in self._python_files():
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertNotIn('"backup"' + " / ", text)
                self.assertNotIn("logs/backup", text.replace("\\", "/"))

    def test_the_run_manifest_carries_the_fields_an_operator_needs_meanwhile(self):
        """The mitigation, pinned: losing the log is not losing the facts."""
        from app.runner import _ARTIFACT_REFS, C_BACKUP

        self.assertIn("state/backup_state.json", _ARTIFACT_REFS[C_BACKUP])


class SerialisationFidelityTests(unittest.TestCase):
    """Every persisted type must write every field it carries, and read back
    everything it wrote.

    Eight classes in `src/` persist themselves, and between them they hold
    the Event, the History Candidate, the Backup record, both retry queues
    and the Run Manifest — i.e. every durable thing this system owns except
    the Markdown. A field added to one of those dataclasses but forgotten in
    its `to_dict()` is silent data loss: it survives in memory for the run
    that created it and is gone the moment the file is read back, with no
    error anywhere.

    Audited across all eight and found clean. What is kept here is the
    guard, because "clean today" is not the property worth having — the
    failure mode only appears the day someone adds a field, and it appears
    silently.

    The coverage check reads the source rather than instantiating, so a
    class added later is covered without anyone remembering to add a sample.
    """

    SRC = Path(__file__).resolve().parents[1] / "src"

    def _classes_with_to_dict(self):
        import ast

        found = []
        for path in sorted(self.SRC.rglob("*.py")):
            if "__pycache__" in str(path):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                to_dict = next(
                    (
                        n
                        for n in node.body
                        if isinstance(n, ast.FunctionDef) and n.name == "to_dict"
                    ),
                    None,
                )
                if to_dict is None:
                    continue
                fields = [
                    n.target.id
                    for n in node.body
                    if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
                ]
                keys = set()
                for sub in ast.walk(to_dict):
                    # `{"name": ...}` — the usual shape.
                    if isinstance(sub, ast.Dict):
                        for key in sub.keys:
                            if isinstance(key, ast.Constant) and isinstance(
                                key.value, str
                            ):
                                keys.add(key.value)
                    # `data["failure"] = ...` — the conditional shape, used
                    # where a key is only written when it has a value.
                    # Missing it made this scan report `ComponentResult` as
                    # dropping `failure`, which it does not.
                    elif isinstance(sub, ast.Assign):
                        for target in sub.targets:
                            if (
                                isinstance(target, ast.Subscript)
                                and isinstance(target.slice, ast.Constant)
                                and isinstance(target.slice.value, str)
                            ):
                                keys.add(target.slice.value)
                found.append((path.name, node.name, set(fields), keys))
        return found

    def test_every_serialiser_writes_every_field_it_declares(self):
        discovered = self._classes_with_to_dict()
        # If this drops to nothing the scan broke, and a guard that scans
        # nothing passes forever.
        self.assertGreaterEqual(len(discovered), 8)

        for file_name, class_name, fields, keys in discovered:
            with self.subTest(cls=f"{file_name}:{class_name}"):
                self.assertEqual(
                    fields - keys,
                    set(),
                    f"{class_name} declares field(s) its to_dict() never writes",
                )

    def test_the_only_keys_beyond_the_fields_are_derived_ones(self):
        """`RunSummary` deliberately writes `overall_status` and `exit_code`,
        which are properties rather than fields — a reader of the file gets
        the verdict without re-implementing the fold. Anything else appearing
        here would be a key nothing can read back."""
        allowed_extra = {"RunSummary": {"overall_status", "exit_code"}}

        for file_name, class_name, fields, keys in self._classes_with_to_dict():
            with self.subTest(cls=f"{file_name}:{class_name}"):
                self.assertEqual(keys - fields, allowed_extra.get(class_name, set()))

    def test_a_fully_populated_instance_survives_every_round_trip(self):
        """Field coverage is necessary, not sufficient: a key can be written
        and then never read. These samples set every optional field to a
        distinctive value so a dropped one shows up as inequality."""
        event = Event.from_dict(
            {
                "schema_version": "1.0",
                "event_id": "RT-EVENT",
                "timestamp": "2026-08-05T10:00:00+09:00",
                "source": "DESKTOP_1",
                "role": "CTO_BACKEND",
                "project_id": "P",
                "event_type": "BLOCKED",
                "status": "BLOCKED",
                "summary": "s",
                "history_candidate": True,
                "milestone": "M",
                "blocker": "B",
                "evidence": ["e1", "e2"],
            }
        )
        samples = [
            event,
            HistoryCandidate(
                history_id="H",
                event_id="E",
                timestamp="2026-08-05T10:00:00+09:00",
                category="MILESTONE",
                project_id="P",
                role="COO",
                summary="s",
                evidence=("a", "b"),
                filter_result=HistoryDecision.KEEP,
                decision_context="dc",
                expected_outcome="eo",
                actual_outcome="ao",
                lessons_learned="ll",
            ),
            BackupLogEntry(
                run_id="R",
                backup_start=datetime(2026, 8, 5, 11, 0).astimezone(),
                source="S",
                changed_files=("a",),
                deleted_files=("b",),
                commit_hash="c0ffee",
                push_result="SUCCESS",
                backup_end=datetime(2026, 8, 5, 11, 1).astimezone(),
                final_status=BackupStatus.SUCCESS,
            ),
            PendingDashboardRecord(
                run_id="R", properties={"Run ID": {}}, queued_at="q", attempt_count=3
            ),
            RetryQueueEntry(
                event_id="E",
                project_id="P",
                event_data=event.to_dict(),
                added_at="q",
                attempt_count=2,
            ),
        ]

        for sample in samples:
            with self.subTest(cls=type(sample).__name__):
                self.assertEqual(type(sample).from_dict(sample.to_dict()), sample)

    def test_the_run_manifest_survives_the_file(self):
        """`RunSummary` has no `from_dict`; `read_summary()` is its reader,
        so the round trip that matters goes through the disk."""
        summary = RunSummary(
            run_id="RT-RUN",
            started_at="2026-08-05T11:00:00+09:00",
            finished_at="2026-08-05T11:01:00+09:00",
            components=(
                ComponentResult(
                    name="collector",
                    status=ComponentStatus.FAILED,
                    failure=Failure(
                        classification="COLLECTOR_ABORTED",
                        severity=Severity.CRITICAL,
                        retryability=Retryability.PERMANENT,
                        reason="boom",
                    ),
                    metrics={"accepted": 1, "failed": 2},
                    artifact_refs=("logs/collector.log",),
                ),
                ComponentResult(name="monthly", status=ComponentStatus.SKIPPED),
            ),
        )
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "last_run.json"

        write_summary(path, summary)

        self.assertEqual(read_summary(path), summary)

    def test_json_round_trips_survive_non_ascii_and_control_characters(self):
        """Company History is Korean, and an `event_id` crosses OneDrive from
        another Desktop with no constraint on its characters (docs/02, and
        BACKLOG A-15 records that deliberately). The persisted form has to
        carry both back unchanged — escaping belongs at the log writer, not
        here."""
        awkward = "설명 —  line sep\ttab \\ backslash \"quote\""
        candidate = HistoryCandidate(
            history_id="H",
            event_id=awkward,
            timestamp="2026-08-05T10:00:00+09:00",
            category="DECISION",
            project_id="P",
            role="COO",
            summary=awkward,
            evidence=(awkward,),
            filter_result=HistoryDecision.REVIEW,
        )

        back = HistoryCandidate.from_dict(json.loads(json.dumps(candidate.to_dict())))

        self.assertEqual(back, candidate)
        self.assertEqual(back.event_id, awkward)
