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
import contextlib
import inspect
import io
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


def _calls_mkstemp(source: str) -> bool:
    """Whether `source` really calls `tempfile.mkstemp`, as the parser sees it.

    Not `"tempfile.mkstemp" in source`. Two gates in this file discovered the
    atomic writers by that substring, and a **docstring** naming the idiom is
    indistinguishable from a call to it. Measured: C64 added one sentence to
    `controltower/rollup.py` explaining why `processed/` has no stager, and
    both gates immediately reported that module as a writer with no cleanup
    and no staging prefix.

    The comment beside one of them already claimed the precision it did not
    have — "`tempfile.` qualified on purpose: every real call is written that
    way, and requiring it keeps prose that merely *mentions* `mkstemp(...)`
    out of the scan". Prose that spells the call out is caught by exactly
    that regex; qualifying it narrowed the false positives without removing
    them.

    Both directions matter and only one of them is loud. A false positive
    fails a green tree, which someone fixes within the hour. A false negative
    — a writer these gates skip — is silent, and the sweep exists because a
    hand-maintained roster had already gone silently stale once (C49). The
    parser answers both, and it is C61's conclusion arriving one layer up:
    that Sprint settled that dependency gates must count `Import` nodes
    rather than text, and this is the same question about a `Call`.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - everything under src/ parses
        return False
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "mkstemp"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "tempfile"
        for node in ast.walk(tree)
    )

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

    def _atomic_writers(self):
        """Every source under `src/` that stages through `tempfile.mkstemp`.

        Swept rather than listed, and the sweep is the fix (C49). The list
        was hard-coded with seven entries; the tree has **thirteen**, and
        `agent/state.py` and `monthly/state.py` were in no list at all — they
        had the idiom and no guard knew it. That is this test's own stated
        failure mode arriving from the other side: it was written so "a
        future writer that skips tempfile+os.replace" cannot pass unnoticed,
        and a future writer that *has* it can be equally unnoticed when the
        roster is maintained by hand.
        """
        return sorted(
            path
            for path in SRC.rglob("*.py")
            if "__pycache__" not in str(path)
            and _calls_mkstemp(path.read_text(encoding="utf-8"))
        )

    def test_the_sweep_finds_the_writers_we_know_exist(self):
        """Guard against the sweep silently matching nothing, and against a
        writer being deleted without anyone noticing."""
        writers = self._atomic_writers()
        # Paths, not basenames: four different packages each call their file
        # `state.py`, and counting names would report eight for thirteen
        # writers — which is exactly the kind of undercount this guard exists
        # to prevent.
        self.assertGreaterEqual(len(writers), 13)

        relative = {path.relative_to(SRC).as_posix() for path in writers}
        for known in (
            "agent/state.py",
            "monthly/state.py",
            "notion/retry_queue.py",
            "reporter/local_output.py",
            "daily/generator.py",
        ):
            with self.subTest(module=known):
                self.assertIn(known, relative)

    def test_every_state_writer_uses_the_same_atomic_idiom(self):
        """Structural guard: a future writer that skips tempfile+os.replace
        would silently lose the no-torn-file property."""
        for path in self._atomic_writers():
            with self.subTest(module=path.relative_to(SRC).as_posix()):
                source = path.read_text(encoding="utf-8")
                self.assertIn("os.replace", source)

    def test_every_state_writer_cleans_up_after_a_failed_commit(self):
        """The other half of the idiom, structurally. The behavioural proof is
        `AtomicWriteFailureCleanupTests` and the two classes beside it; this
        catches a new writer that stages and commits but drops the cleanup,
        which those cannot see because they only drive writers they know
        about."""
        for path in self._atomic_writers():
            with self.subTest(module=path.relative_to(SRC).as_posix()):
                source = path.read_text(encoding="utf-8")
                self.assertIn("except BaseException:", source)
                self.assertIn("os.remove(tmp_path)", source)


class EveryStateWriteStagesTests(unittest.TestCase):
    """The atomic-write family discovers its members by `mkstemp` — so a
    writer that never stages is not a member, and no gate says anything.

    Six classes guard this idiom — five here
    (`AtomicStateWriteInvariantTests`, `AtomicWriteFailureCleanupTests`,
    `AtomicWritesReachTheDiskBeforeTheRenameTests`,
    `IncompleteWriteInvariantTests`, `LockAtomicityCharacterizationTests`)
    and `test_repository_hygiene.py::AtomicWriteLeavesNoResidueTests` — and
    every one of them starts from "functions that call `tempfile.mkstemp`".
    That set answers "do the stagers commit and clean up?" — a good question — and cannot answer the
    one `AtomicStateWriteInvariantTests`'s own docstring poses: *"a future
    writer that skips tempfile+os.replace would silently lose the
    no-torn-file property"*. A writer that skips `tempfile` is precisely
    what a `mkstemp` sweep cannot see.

    Measured. A plausible new writer added to `collector/state.py` —

        def save_seen_summary(path, seen_ids):
            path.write_text(json.dumps({"seen": sorted(seen_ids)}), ...)

    — was reported by **none** of the six. All passed:

        *** MISSES ***  AtomicStateWriteInvariantTests                (7 passed)
        *** MISSES ***  AtomicWriteFailureCleanupTests                (2 passed)
        *** MISSES ***  AtomicWritesReachTheDiskBeforeTheRenameTests  (1 passed)
        *** MISSES ***  IncompleteWriteInvariantTests                 (5 passed)
        *** MISSES ***  LockAtomicityCharacterizationTests            (4 passed)
        *** MISSES ***  AtomicWriteLeavesNoResidueTests               (2 passed)
        DETECTS         EveryStateWriteStagesTests                    (1 failed)

    So this one discovers by the **write** instead. Every function under
    `src/` that serialises JSON and writes it must stage, because a torn
    state file is the thing C27 traced end to end: a `.tmp-` left by an
    interrupted run was read as a finished artifact by six consumers,
    promoted to an Event, and pushed to the backup remote as a truncated day
    of Company History. A *non*-staged write has the same failure with no
    `.tmp-` to notice it by.

    The rule is enforceable as it stands: every JSON writer in `src/` today
    already stages (measured — seven writing functions, and the only
    non-stager is the append-only log line below).
    """

    #: Append-only, and deliberately not staged.
    #:
    #: `oplog.append_line()` adds one line to a log; there is no "torn state"
    #: for it to leave, and staging a whole log to rewrite it would turn an
    #: O(1) append into an O(size) copy on every Event. Named here so the
    #: exemption is a decision rather than a gap the predicate happens to
    #: leave — the shape C58 found in the environment scanner.
    APPEND_ONLY = {"oplog.py::append_line"}

    #: The one writer for which staging would be the **bug**.
    #:
    #: `try_acquire_lock()` writes JSON and must not stage, because its
    #: atomicity *is* the write: a single `os.open(O_CREAT | O_EXCL)` that
    #: exactly one caller can win. Its docstring records what happened when
    #: it did stage — "check-then-write … `os.replace()` overwrites
    #: unconditionally", measured at 8 processes x 12 trials as **2-3
    #: simultaneous holders in every trial, and zero clean denials**, plus a
    #: contended `os.replace()` raising PermissionError and crashing the
    #: Runner.
    #:
    #: So the rule this class enforces has a documented counter-example, and
    #: naming it is better than letting the predicate quietly not reach it:
    #: "state writes stage" is right for files that are *replaced*, and wrong
    #: for a file whose whole purpose is that it can only be *created* once.
    EXCLUSIVE_CREATE = {"lock.py::try_acquire_lock"}

    #: How a function in this tree actually puts bytes on disk.
    #:
    #: The first draft of this set was `{"write_text", "write_bytes"}` and
    #: found **zero** writers — every real one here opens a descriptor from
    #: `tempfile.mkstemp` with `os.fdopen()` and calls `handle.write()`. A
    #: discovery predicate too narrow to see the code it is auditing is the
    #: exact failure this class was written about, arriving in the class
    #: itself; `test_the_sweep_finds_the_writers_that_exist` is what caught
    #: it, which is why that guard is not optional.
    WRITE_CALLS = {"write_text", "write_bytes", "write"}
    OPEN_CALLS = {"open", "fdopen"}

    @staticmethod
    def _serialises_json(source: str) -> bool:
        return "json.dump" in source or "json.dumps" in source

    @classmethod
    def _json_writers(cls, text: str, label: str):
        """`[(name, stages)]` for every JSON-writing function in one source.

        Written against text rather than a path so
        `test_the_predicate_notices_a_writer_that_skips_staging` can drive it
        with a synthetic module — a discovery predicate nobody exercises is a
        gate whose reach is assumed.
        """
        found = []
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = ast.get_source_segment(text, node) or ""
            if not cls._serialises_json(body):
                continue
            attrs = {
                getattr(inner.func, "attr", None)
                for inner in ast.walk(node)
                if isinstance(inner, ast.Call)
            }
            names = {
                getattr(inner.func, "id", None)
                for inner in ast.walk(node)
                if isinstance(inner, ast.Call)
            }
            if not (attrs & cls.WRITE_CALLS or (names | attrs) & cls.OPEN_CALLS):
                continue
            found.append((f"{label}::{node.name}", "mkstemp" in attrs))
        return found

    def _all_json_writers(self):
        writers = []
        for path in sorted(SRC.rglob("*.py")):
            if "__pycache__" in str(path):
                continue
            writers.extend(
                self._json_writers(path.read_text(encoding="utf-8"), path.name)
            )
        return writers

    # ----------------------------------------------------------- the gate
    def test_every_json_writer_stages_before_it_commits(self):
        offenders = [
            name
            for name, stages in self._all_json_writers()
            if not stages
            and name not in self.APPEND_ONLY
            and name not in self.EXCLUSIVE_CREATE
        ]

        self.assertEqual(
            offenders,
            [],
            "these functions serialise JSON and write it without staging "
            "through `tempfile.mkstemp`, so a crash mid-write leaves a torn "
            f"file — and the four mkstemp-based guards cannot see them: {offenders}",
        )

    def test_the_sweep_finds_the_writers_that_exist(self):
        """C57's rule: a discovery that returns nothing makes the gate above
        pass over an empty loop."""
        writers = self._all_json_writers()

        self.assertGreaterEqual(len(writers), 6)
        names = {name for name, _ in writers}
        # The writers whose own body serialises. `write_event_json()` and
        # `write_summary()` are *not* here: they delegate serialisation
        # (`event.to_json()`, `summary.to_dict()`), so this predicate does
        # not see them as JSON writers — and it does not need to, because
        # both stage and the four `mkstemp` guards already cover them. What
        # this class adds is the writers those guards cannot discover.
        for known in (
            "state.py::_save",
            "retry_queue.py::save_queue",
            "dashboard_pending.py::save_all",
            "file_repository.py::save",
        ):
            with self.subTest(writer=known):
                self.assertIn(known, names)

    # ------------------------------------------------- detector's detector
    #
    # Built by joining lines rather than written as one literal: this file
    # is edited by scripts, and a triple-quoted block nested inside another
    # is how the first attempt at this class silently truncated itself.
    SYNTHETIC_UNSTAGED = chr(10).join((
        'import json',
        '',
        '',
        'def save_state(path, data):',
        '    path.write_text(json.dumps(data), encoding="utf-8")',
    ))

    SYNTHETIC_STAGED = chr(10).join((
        'import json',
        'import os',
        'import tempfile',
        '',
        '',
        'def save_state(path, data):',
        '    fd, tmp_path = tempfile.mkstemp(dir=path.parent)',
        '    with os.fdopen(fd, "w", encoding="utf-8") as handle:',
        '        handle.write(json.dumps(data))',
        '    os.replace(tmp_path, path)',
    ))

    SYNTHETIC_NO_JSON = chr(10).join((
        'def render(path, text):',
        '    path.write_text(text, encoding="utf-8")',
    ))

    def test_the_predicate_notices_a_writer_that_skips_staging(self):
        """The detector for the detector (C58).

        The four existing guards were blind here not because their
        assertions were wrong but because their *discovery* was. A new
        discovery predicate deserves the same suspicion.
        """
        found = self._json_writers(self.SYNTHETIC_UNSTAGED, 'synthetic.py')

        self.assertEqual(found, [('synthetic.py::save_state', False)])

    def test_the_predicate_accepts_a_writer_that_stages(self):
        found = self._json_writers(self.SYNTHETIC_STAGED, 'synthetic.py')

        self.assertEqual(found, [('synthetic.py::save_state', True)])

    def test_the_predicate_ignores_a_function_that_writes_no_json(self):
        """Precision matters as much as reach: a predicate that flagged
        every write would fill this gate with markdown renderers and log
        writers, and the first response to that noise is to weaken it.
        """
        self.assertEqual(
            self._json_writers(self.SYNTHETIC_NO_JSON, 'synthetic.py'), []
        )

    def test_the_exclusive_create_exemption_is_still_exclusive_create(self):
        """An exemption is a claim about the code, so it is checked.

        If `try_acquire_lock()` ever stopped using `O_EXCL`, this exemption
        would silently excuse an ordinary unstaged write — and the failure it
        would hide is the one its docstring measured: several runs each told
        they hold the same lock.
        """
        source = (SRC / "scheduler" / "lock.py").read_text(encoding="utf-8")

        self.assertIn("O_EXCL", source)
        self.assertIn("def try_acquire_lock", source)

    def test_the_append_only_exemption_still_names_something_real(self):
        """An exemption for a function that no longer exists is one
        nobody can evaluate — and it would silently cover a future
        function that took the name.
        """
        import oplog

        self.assertTrue(callable(oplog.append_line))
        source = Path(oplog.__file__).read_text(encoding='utf-8')
        self.assertIn('def append_line', source)
        self.assertNotIn('mkstemp', source)


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

    **"Eight" is the set of sources that Sprint changed, not the set of
    atomic writers.** Sweeping for `mkstemp` finds fourteen, and a line
    coverage pass over the whole suite (C40) showed the Company History
    writers' cleanup lines never executing. Those three now have their own
    class — `CompanyHistoryWritersCleanUpTooTests` — because two of them
    report failure instead of raising it, which needs a different assertion
    for the same property.
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
        from agent.state import AgentState
        from agent.state import save_state as agent_save_state
        from backup.result import BackupStatus
        from backup.state import BackupState
        from backup.state import save_state as backup_save_state
        from collector.state import PersistentSeenEventStore
        from monthly.state import MonthlyState
        from monthly.state import save_state as monthly_save_state
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
            # C49: both use the same idiom and were in no behavioural class.
            # Found by sweeping for `tempfile.mkstemp` instead of trusting the
            # hand-kept roster above this method.
            (
                "agent/state.py::save_state",
                self.root / "agent",
                lambda d: agent_save_state(
                    d / "agent_state.json",
                    AgentState(desktop_id="DESKTOP_1"),
                ),
            ),
            (
                "monthly/state.py::save_state",
                self.root / "monthly",
                lambda d: monthly_save_state(
                    d / "monthly_history_state.json", MonthlyState()
                ),
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


class ACleanupThatFailsDoesNotHideTheOriginalFailureTests(unittest.TestCase):
    """C49: the inner half of the idiom, which nothing exercised.

    Every atomic writer ends the same way::

        except BaseException:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise

    `AtomicWriteFailureCleanupTests` proves the outer half — the commit fails,
    the temp file goes, the error propagates. A coverage pass shows the
    **inner** `except OSError` unexecuted in all of them: nothing has ever
    made the cleanup itself fail.

    That is not exotic either, and on this project's target OS it is the same
    cause as the outer failure. `os.replace` raises WinError 5 when something
    holds the destination open; the very same handle holding the *temp* file
    makes `os.remove` raise WinError 32. So the two failures arrive together
    far more often than independently.

    The property at stake is which exception a caller sees. If the `pass` were
    ever dropped, the writer would report "could not delete a temp file"
    instead of "could not write the state" — a message about the cleanup of a
    problem, in place of the problem. Every caller that classifies failures
    (`app/runner.py`'s recorder, `SyncResult`, `TransportError`) would
    classify the wrong one.

    Borrows `AtomicWriteFailureCleanupTests._writers` as a plain function
    rather than inheriting from it, so the two classes cannot drift about
    what "every atomic writer" means while this one does **not** inherit the
    parent's assertions — under a failing `os.remove` the temp file is
    supposed to survive, which is the opposite of what the parent asserts.
    """

    MARKER = AtomicWriteFailureCleanupTests.MARKER
    REMOVE_MARKER = "simulated: temp file held open too"
    _writers = AtomicWriteFailureCleanupTests._writers

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

        real_replace = os.replace
        real_remove = os.remove

        def failing_replace(src, dst):
            raise OSError(5, self.MARKER)

        def failing_remove(path):
            raise OSError(32, self.REMOVE_MARKER)

        os.replace = failing_replace
        os.remove = failing_remove
        # Restored before the TemporaryDirectory is cleaned up: cleanups run
        # last-registered-first, and `tmp.cleanup` needs a working `os.remove`.
        self.addCleanup(setattr, os, "remove", real_remove)
        self.addCleanup(setattr, os, "replace", real_replace)

    def test_the_original_error_is_what_propagates(self):
        for name, directory, write in self._writers():
            with self.subTest(writer=name):
                directory.mkdir(parents=True, exist_ok=True)
                with self.assertRaises(Exception) as caught:  # noqa: B017
                    write(directory)

                message = str(caught.exception)
                self.assertIn(self.MARKER, message, f"{name} lost the original error")
                self.assertNotIn(
                    self.REMOVE_MARKER,
                    message,
                    f"{name} reported the cleanup failure instead",
                )

    def test_no_writer_swallows_the_failure_entirely(self):
        """The other way the `pass` could go wrong: swallowing the `raise`
        as well would turn a failed write into a silent success."""
        for name, directory, write in self._writers():
            with self.subTest(writer=name):
                directory.mkdir(parents=True, exist_ok=True)
                with self.assertRaises(Exception):  # noqa: B017
                    write(directory)

    def test_the_destination_is_still_absent(self):
        """A failed cleanup must not leave a half-written state file where
        the next run will read it. The temp file survives — that is what a
        failed `os.remove` means — but nothing ever points a real name at
        it, which is the property `os.replace` was there to give."""
        for name, directory, write in self._writers():
            with self.subTest(writer=name):
                directory.mkdir(parents=True, exist_ok=True)
                with self.assertRaises(Exception):  # noqa: B017
                    write(directory)

                written = [
                    p.name
                    for p in directory.rglob("*.json")
                    if p.is_file() and not p.name.startswith(".tmp-")
                ]
                self.assertEqual(written, [], f"{name} left {written}")

    def test_the_temp_file_is_the_only_thing_left_behind(self):
        """Stated so the leak is characterised rather than implied: when the
        cleanup itself fails there is nothing more the writer can do, and the
        residue is a `.tmp-` file that `ops_status.py`'s staging-residue
        report already looks for."""
        leaked = []
        for name, directory, write in self._writers():
            with self.subTest(writer=name):
                directory.mkdir(parents=True, exist_ok=True)
                with self.assertRaises(Exception):  # noqa: B017
                    write(directory)

                residue = [
                    p.name
                    for p in directory.rglob("*")
                    if p.is_file() and p.name.startswith(".tmp-")
                ]
                leaked.append((name, len(residue)))

        self.assertTrue(
            all(count >= 1 for _name, count in leaked),
            f"the injection did not reach every writer: {leaked}",
        )


class AtomicWritesReachTheDiskBeforeTheRenameTests(unittest.TestCase):
    """Every atomic writer flushes its data to disk before committing the name.

    `test_every_state_writer_uses_the_same_atomic_idiom` proves the writers
    stage through `tempfile.mkstemp` and commit with `os.replace`, and
    `AtomicWriteFailureCleanupTests` proves the failure path cleans up. Both
    are about *atomicity* — a reader never sees a half-written file. Neither
    says anything about *durability*, and until this class existed nothing in
    this repository did: not one of the fourteen writers called `fsync`.

    The gap is the other half of an accident this repository already reasons
    about. `reporter/local_output.INCOMPLETE_WRITE_PREFIX` follows the
    *staging* file left by "a write the process never returned from — power
    loss, SIGKILL, a container stop" through every reader. The same power cut
    has a second outcome: `os.replace()` is a metadata operation NTFS
    journals while the bytes are still in the page cache, so the rename can
    land first and the file comes back under its real name, the right size,
    full of zeros. That one is worse — a leftover `.tmp-…json` is visibly not
    an artifact, while a zero-filled `2026-08-05.md` is a day of Company
    History that `_holes_in_the_daily_sequence()` (which looks for a *missing*
    file) accepts, and that Backup commits and pushes.

    Asserted by behaviour, not by grepping the source: `os.fsync` and
    `os.replace` are both recorded, and each writer must have fsynced before
    it renamed. A writer that flushed *after* the commit, or not at all,
    fails here.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

        self.events: list[str] = []
        real_fsync = os.fsync
        real_replace = os.replace

        def recording_fsync(fd):
            self.events.append("fsync")
            return real_fsync(fd)

        def recording_replace(src, dst):
            self.events.append("replace")
            return real_replace(src, dst)

        os.fsync = recording_fsync
        os.replace = recording_replace
        self.addCleanup(setattr, os, "fsync", real_fsync)
        self.addCleanup(setattr, os, "replace", real_replace)

    def _writers(self):
        """(name, directory, callable) for every `mkstemp` + `os.replace` site.

        Deliberately a superset of `AtomicWriteFailureCleanupTests._writers()`,
        which covers eight. Sweeping `src/` for `os.fdopen(fd` finds fifteen;
        fourteen are here and the fifteenth is `scheduler/lock.py`, which is
        excluded on purpose — a lock is not a durable artifact, and a lock
        whose contents did not survive a crash is read as unparseable, judged
        stale, and taken over, which is the direction that recovers.
        """
        from agent.state import AgentState
        from agent.state import save_state as agent_save_state
        from backup.result import BackupStatus
        from backup.state import BackupState
        from backup.state import save_state as backup_save_state
        from collector.state import PersistentSeenEventStore
        from daily import generate_daily_history, update_daily_history
        from monthly.generator import consolidate_month
        from monthly.state import MonthlyState
        from monthly.state import save_state as monthly_save_state
        from notion.dashboard_pending import save_pending
        from notion.retry_queue import RetryQueueEntry, save_queue
        from reporter.local_output import write_event_json
        from scheduler.state import SchedulerState
        from scheduler.state import save_state as scheduler_save_state
        from transport.onedrive import OneDriveTransport

        now = datetime(2026, 8, 6, 11, 0).astimezone()
        day = date(2026, 8, 5)
        event = create_event(
            source="DESKTOP_1",
            role="CTO_BACKEND",
            project_id="PRJ-FSYNC",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            summary="durability probe",
            milestone="M1",
            history_candidate=True,
            timestamp="2026-08-05T10:00:00+09:00",
            event_id="FSYNC-001",
        )
        candidate = _candidate(1, day)

        def daily_first_write(d):
            repository = FileHistoryRepository(keep_dir=d / "keep", review_dir=d / "review")
            repository.save(candidate)
            generate_daily_history(repository, day, output_dir=d / "daily")

        def daily_late_update(d):
            repository = FileHistoryRepository(keep_dir=d / "keep", review_dir=d / "review")
            repository.save(candidate)
            generate_daily_history(repository, day, output_dir=d / "daily")
            repository.save(_candidate(2, day))
            self.events.clear()  # only the late-update write is under test
            result = update_daily_history(repository, day, output_dir=d / "daily", now=now)
            self.assertEqual(result.outcome.value, "UPDATED_LATE_EVENT", result.error)

        def monthly_write(d):
            daily_dir = d / "daily"
            daily_dir.mkdir(parents=True, exist_ok=True)
            for stamp in ("2026-07-30", "2026-07-31"):
                (daily_dir / f"{stamp}.md").write_text(
                    f"# DOJOONPASS Company History - {stamp}\n\n"
                    "No material company history recorded.\n",
                    encoding="utf-8",
                )
            result = consolidate_month(
                year=2026,
                month=7,
                daily_dir=daily_dir,
                monthly_dir=d / "monthly",
                history_start_date=date(2026, 7, 30),
                now=datetime(2026, 8, 2, 9, 0).astimezone(),
            )
            self.assertTrue(
                (d / "monthly" / "2026-07.md").is_file(),
                f"monthly not written: {result.status} {result.error}",
            )

        return [
            (
                "collector/state.py::_save",
                self.root / "collector",
                lambda d: PersistentSeenEventStore(
                    state_path=d / "collector_state.json"
                ).mark_seen("FSYNC-001"),
            ),
            (
                "scheduler/state.py::save_state",
                self.root / "scheduler",
                lambda d: scheduler_save_state(d / "daily_history_state.json", SchedulerState()),
            ),
            (
                "backup/state.py::save_state",
                self.root / "backup",
                lambda d: backup_save_state(
                    d / "backup_state.json", BackupState(backup_status=BackupStatus.PENDING)
                ),
            ),
            (
                "monthly/state.py::save_state",
                self.root / "monthly_state",
                lambda d: monthly_save_state(d / "monthly_history_state.json", MonthlyState()),
            ),
            (
                "agent/state.py::save_state",
                self.root / "agent_state",
                lambda d: agent_save_state(d / "agent_state.json", AgentState(desktop_id="DESKTOP_1")),
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
                    run_id="RUN-FSYNC-001",
                    properties={"Name": {"title": []}},
                    now=now,
                ),
            ),
            (
                "history/file_repository.py::save",
                self.root / "history",
                lambda d: FileHistoryRepository(keep_dir=d, review_dir=d / "review").save(
                    candidate
                ),
            ),
            (
                "reporter/local_output.py::write_event_json",
                self.root / "reporter",
                lambda d: write_event_json(event, directory=d),
            ),
            (
                "transport/onedrive.py::_write_atomic",
                self.root / "onedrive",
                lambda d: OneDriveTransport(sync_folder=d / "sync", outgoing_dir=d).send(event),
            ),
            (
                "runsummary.py::write_summary",
                self.root / "runsummary",
                lambda d: write_summary(
                    d / "last_run.json",
                    RunSummary(
                        run_id="RUN-FSYNC-001",
                        started_at="2026-08-06T11:00:00+09:00",
                        finished_at="2026-08-06T11:00:01+09:00",
                        components=(
                            ComponentResult(name="intake", status=ComponentStatus.SUCCESS),
                        ),
                    ),
                ),
            ),
            ("daily/generator.py::generate_daily_history", self.root / "daily1", daily_first_write),
            ("daily/generator.py::update_daily_history", self.root / "daily2", daily_late_update),
            ("monthly/generator.py::consolidate_month", self.root / "monthly", monthly_write),
        ]

    def test_every_atomic_writer_fsyncs_before_it_renames(self):
        for name, directory, write in self._writers():
            with self.subTest(writer=name):
                directory.mkdir(parents=True, exist_ok=True)
                self.events.clear()
                write(directory)

                self.assertIn("fsync", self.events, f"{name} never called os.fsync")
                self.assertIn("replace", self.events, f"{name} never reached os.replace")
                self.assertEqual(
                    self.events[0],
                    "fsync",
                    f"{name} renamed before flushing to disk: {self.events}",
                )
                # Every rename this writer performs must be preceded by a
                # flush of the file it is about to publish, not just the
                # first one — `update_daily_history()` and `OneDriveTransport`
                # both write more than once.
                pending_fsync = 0
                for entry in self.events:
                    if entry == "fsync":
                        pending_fsync += 1
                    else:
                        self.assertGreater(
                            pending_fsync, 0, f"{name} renamed an unflushed file: {self.events}"
                        )
                        pending_fsync -= 1


class TestDoubleFidelityTests(unittest.TestCase):
    """BUG-35: `InMemoryNotionTransport` is more permissive than the real API,
    which bounds how much any Notion test in this repository can prove.

    CHARACTERIZATION of what is still divergent, and a GUARD on the one
    divergence that has been closed.

    Interface parity is exact — both transports implement all seven methods of
    `NotionTransport` with identical signatures (asserted below). Behavioural
    parity is not. Of eight payloads the live Notion API rejects, the double
    used to accept six. **Five, since C50:**

        property name not in the schema  accepted   (real: 400)
        wrong property type              accepted   (real: 400)
        unknown database_id on query     accepted   (real: 404)
        empty properties on create       accepted   (real: 400, title required)
        select name = ""                 accepted   (real: 400)

        rich_text over 2000 chars        REJECTED, 400   (C50 — see below)
        unknown page_id on update        rejected   (matches real 404)
        properties=None                  TypeError  (real: 400 — wrong kind)

    **Why that one moved onto the double itself.** The original note here
    read "tightening the double changes what every existing Notion test
    exercises", and that was the right worry for *schema* fidelity, which
    needs a schema the double does not have. It was the wrong worry for a
    fixed character count: no test in this repository writes a 2,000-character
    property, so enforcing it changed nothing any test was doing — and the
    laxness had a measured cost. `notion/properties.py` sent **four unbounded
    authored fields** (`blocker`, `milestone`, `project_id`, `event_id`) to
    the live API for the whole life of this project, and the suite could not
    see it precisely because the double accepted them (C50 §4).

    The local-subclass mitigation was tried first and did not hold: by C50
    there were **three** copies of the same eight lines
    (`StrictNotionTransport` here, plus two more added in the same Sprint),
    each protecting one test file while every other Notion test stayed blind.
    A rule that has to be opted into is a rule most callers do not have.

    `_TypeEnforcingTransport` (test_notion_dashboard.py, C49) stays a local
    subclass, and correctly: it validates against the OPS_RUNS *schema*, which
    is a property of one database rather than of the API.

    The honest reading is unchanged for the five that remain: green Notion
    tests here demonstrate that OUR logic is self-consistent, not that Notion
    will accept what we send. Only the real connection can show that. This
    class is what keeps the list of what is still unproven from drifting.
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

    def test_the_text_limit_is_on_the_double_itself_not_a_subclass(self):
        """C50: the mitigation that used to be a subclass is now the rule.

        Asserted three ways, because each could regress on its own: the
        double really refuses an over-long item, it refuses it with the
        status the retry classifier reads, and it reads the limit from the
        module that owns it rather than restating the number.
        """
        from notion.properties import RICH_TEXT_LIMIT
        from notion.transport import InMemoryNotionTransport, NotionAPIError

        transport = InMemoryNotionTransport()
        with self.assertRaises(NotionAPIError) as caught:
            transport.create_page(
                "DB-1",
                {
                    "Project": {"title": [{"text": {"content": "P"}}]},
                    "Blocker": {
                        "rich_text": [
                            {"text": {"content": "X" * (RICH_TEXT_LIMIT + 1)}}
                        ]
                    },
                },
            )
        self.assertEqual(caught.exception.status_code, 400)

        source = (
            REPO_ROOT / "src" / "notion" / "transport.py"
        ).read_text(encoding="utf-8")
        self.assertIn("from .properties import RICH_TEXT_LIMIT", source)
        self.assertNotIn("2000", source)

    def test_exactly_at_the_limit_still_passes_the_double(self):
        """An off-by-one here would refuse a payload Notion accepts, which is
        the opposite failure and just as invisible."""
        from notion.properties import RICH_TEXT_LIMIT
        from notion.transport import InMemoryNotionTransport

        transport = InMemoryNotionTransport()
        page = transport.create_page(
            "DB-1",
            {"Blocker": {"rich_text": [{"text": {"content": "X" * RICH_TEXT_LIMIT}}]}},
        )
        self.assertIn("id", page)

    def test_no_test_module_keeps_its_own_copy_of_the_text_limit(self):
        """Three copies of these eight lines existed at one point (C50).

        The rule belongs to the double: a private strict subclass protects the
        one file that defines it and leaves every other Notion test blind,
        which is exactly how four unbounded authored fields survived.

        Scoped to **transport subclasses**, not to the number. A test class
        may perfectly well hold `LIMIT = 2000` to assert against — several do,
        and each ties that constant back to `properties.RICH_TEXT_LIMIT` so
        the two cannot drift. What must not come back is a second
        implementation of the check.
        """
        offenders = []
        for path in sorted((REPO_ROOT / "tests").glob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                bases = {
                    base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")
                    for base in node.bases
                }
                if "InMemoryNotionTransport" not in bases:
                    continue
                literals = {
                    child.value
                    for child in ast.walk(node)
                    if isinstance(child, ast.Constant) and child.value == 2000
                }
                if literals:
                    offenders.append(f"{path.name}::{node.name}")
        self.assertEqual(
            offenders,
            [],
            "a test module re-implements Notion's text limit in its own "
            "transport subclass; the double enforces it for everyone",
        )

    def test_the_scan_above_can_see_a_transport_subclass_at_all(self):
        """A guard whose scan finds nothing passes forever. There is one such
        subclass in the tree — `SchemaMismatchNotionTransport` — and it is
        legitimate: it refuses for a *schema* reason, not a length one."""
        found = []
        for path in sorted((REPO_ROOT / "tests").glob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and any(
                    (base.id if isinstance(base, ast.Name) else getattr(base, "attr", ""))
                    == "InMemoryNotionTransport"
                    for base in node.bases
                ):
                    found.append(node.name)
        self.assertIn("SchemaMismatchNotionTransport", found)

    def test_a_type_enforcing_double_exists_for_the_other_one(self):
        """C49's mitigation, recorded here for the same reason: an unused
        stricter double is deleted, and then the divergence it covered comes
        back silently."""
        dashboard_source = (
            REPO_ROOT / "tests" / "test_notion_dashboard.py"
        ).read_text(encoding="utf-8")

        self.assertIn("class _TypeEnforcingTransport", dashboard_source)
        self.assertIn("expected to be", dashboard_source)

    def test_the_plain_double_is_still_the_permissive_one(self):
        """The mitigations are local subclasses, not a change to the double
        every other Notion test uses — so the characterisation above stays
        true and this is what says so."""
        from notion.transport import InMemoryNotionTransport

        transport = InMemoryNotionTransport(
            initial_properties={"Role Mismatches": {"type": "number", "number": {}}}
        )

        page = transport.create_page(
            "DB-1", {"Role Mismatches": {"rich_text": [{"text": {"content": "1"}}]}}
        )

        self.assertIn("id", page)


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

    def test_a_notion_response_body_reaches_stdout_redacted_and_on_one_line(self):
        """NEW, **security**. The one sink for this string that was unguarded.

        `notion/transport._error_detail()` appends Notion's own response body
        to `NotionAPIError` so an operator can see which property was
        rejected (BUG-58), and `SyncResult.error` carries it verbatim.
        `oplog.append_line()` redacts and flattens exactly this class of
        string on its way to `notion_sync.log`, and its docstring says why in
        measured terms: *"a 502 page containing `Authorization: Bearer ntn_…`
        put the token straight into notion_sync.log"*.

        `_print_result()` printed the same string raw. Measured, one proxy
        502 echoing the request headers back:

            notion_sync.log   token redacted, 1 line
            this stdout       `Authorization: Bearer ntn_…` in full, 4 lines

        Both halves are asserted. The second is not cosmetic — a multi-line
        body forges further `  - <event_id> …` result lines in the report an
        operator reads to decide what happened, which is BUG-6's shape in a
        sink nobody had aimed it at.

        Fixed at the sink rather than at `notion/transport.py`, where the
        string is built: `notion` may import only `events`
        (`LayeringInvariantTests`), and widening that table is an
        architecture decision. This script sits above everything already.
        """
        import contextlib
        import importlib
        import io

        from backup.log import BackupLogEntry
        from backup.result import BackupStatus
        from collector.runtime import RuntimeSummary
        from notion.sync import SyncResult, SyncStatus
        from scheduler.result import SchedulerRunResult, SchedulerStatus

        sys.path.insert(0, str(REPO_ROOT))
        try:
            run_company_ops = importlib.import_module("run_company_ops")
        finally:
            sys.path.remove(str(REPO_ROOT))

        token = "ntn_" + "A" * 46
        body = (
            "Notion API returned 502: Bad Gateway | <html><pre>\n"
            "GET /v1/databases/db HTTP/1.1\n"
            f"Authorization: Bearer {token}\n"
            "</pre></html>"
        )
        now = datetime(2026, 8, 11, 11, 0).astimezone()
        result = (
            type("Intake", (), {"moved": ()})(),
            RuntimeSummary(accepted=1, duplicate=0, rejected=0, failed=0, files=()),
            SchedulerRunResult(status=SchedulerStatus.COMPLETED, generated_dates=()),
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
            [
                SyncResult(
                    status=SyncStatus.NOTION_RETRY_REQUIRED,
                    event_id="EVT-1",
                    project_id="PRJ",
                    error=body,
                )
            ],
        )

        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            run_company_ops._print_result(result)
        printed = out.getvalue()

        self.assertNotIn(token, printed)
        self.assertIn("[REDACTED]", printed)
        # Still diagnosable — the point of carrying the body at all.
        self.assertIn("502", printed)
        # One result line per SyncResult, whatever the body contained.
        self.assertEqual(
            [line for line in printed.splitlines() if line.startswith("  - ")],
            [line for line in printed.splitlines() if "EVT-1" in line],
        )
        self.assertEqual(sum(1 for line in printed.splitlines() if "EVT-1" in line), 1)

    def test_the_event_id_and_project_id_on_that_line_are_guarded_too(self):
        """The blind spot the fix above shipped with, found by asking the
        adjacent-boundary question of my own change.

        `r.error` was guarded because it obviously came from a remote
        response. `r.event_id` and `r.project_id` sit on the *same printed
        line*, cross the *same* transport from another Desktop, and docs/02
        constrains both to "present and non-null" only (BACKLOG A-15) — they
        were left raw. Measured, an `event_id` of
        `"EVT-1\\n  - EVT-GHOST (PRJ): SYNCED"`:

            printed rows starting `  - `   2
            the second one                 fully attacker-authored

        Guarding the obvious half of a line is half a fix. Both shapes are
        asserted here so the next person cannot fix one and miss the other.
        """
        import contextlib
        import importlib
        import io

        from backup.log import BackupLogEntry
        from backup.result import BackupStatus
        from collector.runtime import RuntimeSummary
        from notion.sync import SyncResult, SyncStatus
        from scheduler.result import SchedulerRunResult, SchedulerStatus

        sys.path.insert(0, str(REPO_ROOT))
        try:
            run_company_ops = importlib.import_module("run_company_ops")
        finally:
            sys.path.remove(str(REPO_ROOT))

        now = datetime(2026, 8, 11, 11, 0).astimezone()
        for label, event_id, project_id in (
            ("event_id", "EVT-1\n  - EVT-GHOST (PRJ): SYNCED", "PRJ"),
            ("project_id", "EVT-1", "PRJ\n  - EVT-GHOST (X): SYNCED"),
        ):
            with self.subTest(field=label):
                result = (
                    type("Intake", (), {"moved": ()})(),
                    RuntimeSummary(accepted=1, duplicate=0, rejected=0, failed=0, files=()),
                    SchedulerRunResult(
                        status=SchedulerStatus.COMPLETED, generated_dates=()
                    ),
                    BackupLogEntry(
                        run_id="RUN-1", backup_start=now, source="local_master",
                        changed_files=(), deleted_files=(), commit_hash=None,
                        push_result=None, backup_end=now,
                        final_status=BackupStatus.NOT_REQUIRED,
                    ),
                    [
                        SyncResult(
                            status=SyncStatus.NOTION_RETRY_REQUIRED,
                            event_id=event_id, project_id=project_id, error=None,
                        )
                    ],
                )
                out = io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(
                    io.StringIO()
                ):
                    run_company_ops._print_result(result)
                printed = out.getvalue()

                rows = [
                    line for line in printed.splitlines() if line.startswith("  - ")
                ]
                self.assertEqual(len(rows), 1, printed)
                self.assertNotIn("EVT-GHOST (PRJ): SYNCED\n", printed)
                self.assertIn("\\n", rows[0])

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
        # ...and 2 is only the FALLBACK. The code the process actually
        # returns comes from the Run Manifest, which `run_once()` writes in
        # its `finally` and which has already classified this failure.
        #
        # Measured before that change, against a real broken git remote: the
        # manifest said DEGRADED/exit 3 while the process exited 2. Two
        # answers to "how bad was this run", and the scheduled task only
        # ever sees the process one.
        #
        # **Where those two assertions live moved in C78, and this class is
        # where that has to be said.** They used to match `return 2` and
        # `read_summary` inside this function. C78 found the same
        # disagreement on every OTHER abort path — the process exited 1
        # while the manifest said 2 — and the fix was to give that path
        # the same answer, which meant the rule could not stay a private
        # detail of the Backup reporter. It moved to
        # `_exit_code_from_manifest()`, unchanged, and both callers use it.
        #
        # Asserted as *delegation plus the rule*, not as "the literal is
        # somewhere in the file": a copy of the body pasted back into this
        # function would satisfy a file-wide search and would be exactly the
        # regression C78 closed.
        #
        # And asserted structurally rather than as call text, because the
        # first draft of this line was `assertIn("_exit_code_from_manifest(
        # run_summary_path)", reporter)` and C82 broke it by adding a keyword
        # argument — a change that did not touch delegation at all. That is
        # the third time this Sprint that a source-string assertion failed on
        # an axis it had no opinion about, and `EncodingSafetyTests` had
        # already written down why: *a test that breaks on the wrong axis
        # stops being evidence about its own subject.*
        reporter_tree = self._function("_report_backup_failure")
        calls = {
            getattr(node.func, "id", None)
            for node in ast.walk(reporter_tree)
            if isinstance(node, ast.Call)
        }
        self.assertIn("_exit_code_from_manifest", calls, "it stopped delegating")
        self.assertNotIn(
            "read_summary", calls,
            "the reporter reads the manifest itself again — that is the "
            "second copy of the rule C78 removed",
        )

        helper = ast.unparse(self._function("_exit_code_from_manifest"))
        self.assertIn("return 2", helper)
        self.assertIn("summary.exit_code", helper)
        self.assertIn("read_summary", helper)

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


class RunnerEntrypointConfigurationTests(unittest.TestCase):
    """`run_company_ops.py`'s environment gate — the Desktop 4 mirror of
    `test_agent.py::AgentEntrypointConfigurationTests`.

    Found the same way and left in the same state: a line-coverage pass over
    the root scripts showed `_resolve_history_start_date()`'s two refusal
    paths, and `main()`'s whole body, had never executed. Nothing here is a
    new rule — docs/07 §50 already says the start date is never guessed —
    but nothing was checking that the rule still fires.

    Its consequence is the opposite shape to the Agent's and no smaller. A
    guessed start date on Desktop 4 does not fail loudly: it silently decides
    where Company History begins, on the one machine that writes it, and
    `daily_history_state.json` then advances past whatever it decided.
    """

    KEY = "COMPANY_OPS_HISTORY_START_DATE"

    def _module(self):
        import importlib.util

        path = REPO_ROOT / "run_company_ops.py"
        spec = importlib.util.spec_from_file_location("run_company_ops_config", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _with(self, value):
        original = os.environ.get(self.KEY)

        def restore():
            if original is None:
                os.environ.pop(self.KEY, None)
            else:
                os.environ[self.KEY] = original

        self.addCleanup(restore)
        if value is None:
            os.environ.pop(self.KEY, None)
        else:
            os.environ[self.KEY] = value

    def _refusal(self, value):
        self._with(value)
        module = self._module()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as caught:
                module._resolve_history_start_date()
        return caught.exception.code, err.getvalue()

    def test_a_missing_start_date_is_refused_not_guessed(self):
        code, err = self._refusal(None)

        self.assertEqual(code, 1)
        self.assertIn(self.KEY, err)

    def test_a_blank_value_is_treated_as_missing(self):
        """`if not raw`, the same reading `run_agent.py` uses. A half-edited
        `.env` produces the empty string, and `date.fromisoformat("")` would
        raise a bare ValueError out of the entrypoint instead."""
        code, err = self._refusal("")

        self.assertEqual(code, 1)
        self.assertIn(self.KEY, err)

    def test_a_malformed_value_names_the_value_it_refused(self):
        code, err = self._refusal("2026-13-45")

        self.assertEqual(code, 1)
        self.assertIn("2026-13-45", err)

    def test_a_well_formed_date_is_accepted_as_written(self):
        """The other side of both branches — and that nothing shifts the
        date on the way through."""
        self._with("2026-08-05")
        module = self._module()

        self.assertEqual(module._resolve_history_start_date(), date(2026, 8, 5))

    def test_it_exits_rather_than_returning_a_code(self):
        """1 is `main()`'s configuration-error code, and this function is
        called before `main()` has anything to report — so it raises
        SystemExit rather than returning, which is what keeps a run that
        never started from producing a Run Manifest (docs/14 §7)."""
        source = inspect.getsource(self._module()._resolve_history_start_date)

        self.assertIn("raise SystemExit(1)", source)
        self.assertNotIn("return 1", source)


class BackupFailureExitCodeFallbackTests(unittest.TestCase):
    """`_report_backup_failure()`'s two fallbacks, neither of which ran.

    The function's own comment states the rule: the exit code comes from the
    Run Manifest, not from a literal, so the process cannot disagree with
    the manifest it just wrote. What it also says — and what nothing
    exercised — is what happens when the manifest is *not* readable. A
    Backup failure with no manifest is genuinely unclassified, and 2 is the
    conservative reading of an unclassified failure; returning 0 there would
    tell Task Scheduler the run was fine.
    """

    def _module(self):
        import importlib.util

        path = REPO_ROOT / "run_company_ops.py"
        spec = importlib.util.spec_from_file_location("run_company_ops_fallback", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _report(self, path):
        module = self._module()
        from backup.git_ops import GitOperationError

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = module._report_backup_failure(
                GitOperationError("git push failed (exit 128): remote unreachable"), path
            )
        return code, err.getvalue()

    def test_no_manifest_path_is_the_conservative_two(self):
        code, _err = self._report(None)

        self.assertEqual(code, 2)

    def test_an_unreadable_manifest_is_the_conservative_two(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        broken = Path(tmp.name) / "last_run.json"
        broken.write_text("{not json", encoding="utf-8")

        code, _err = self._report(broken)

        self.assertEqual(code, 2)

    def test_a_readable_manifest_decides_instead_of_the_fallback(self):
        """The half that makes the two above meaningful: when the manifest
        IS readable, its own classification wins — and for a failed push
        that is DEGRADED/3, not the fallback's 2."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "last_run.json"
        path.write_text(
            json.dumps(
                {
                    "run_id": "R",
                    "started_at": "2026-08-18T11:00:00+09:00",
                    "finished_at": "2026-08-18T11:00:01+09:00",
                    "components": [
                        {
                            "name": "backup",
                            "status": "FAILED",
                            "failure": {
                                "classification": "BACKUP_PENDING",
                                "reason": "remote unreachable",
                                "retryability": "RETRYABLE",
                                "severity": "DEGRADED",
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        code, err = self._report(path)

        self.assertEqual(code, 3)
        self.assertIn("ops_status.py", err)


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

    def test_the_monthly_diagnostics_reach_a_sink(self):
        """BUG-39's question, asked of the one result object it never covered.

        This class sweeps `RuntimeSummary`, `BackupLogEntry` and
        `SchedulerRunResult` for fields that are computed and then discarded
        at process exit. `MonthlyResult` was not in the sweep, and C31 found
        exactly the shape BUG-39 describes sitting in it: a Daily item that
        did not reach Monthly left no trace anywhere, because `item_count`
        counts what arrived rather than what was sent.

        The five unread fields are unread *by the Runner* on purpose, and
        each is consumed elsewhere — `year`/`month` are carried by `key`,
        which the log lines use; `coverage` and `source_dates` are rendered
        into the Monthly document itself by `render_monthly_markdown()`; and
        `path` is where that document was written. Copying them into the log
        would make two records of the same run disagree the first time they
        drifted, which is the reasoning `BackupLogEntry` above already gives
        for its four.
        """
        from monthly.generator import MonthlyResult

        unread = self._unread_fields(MonthlyResult, "month_result")

        for diagnostic in ("status", "item_count", "error", "unconsolidated_days"):
            with self.subTest(field=diagnostic):
                self.assertNotIn(diagnostic, unread)
        self.assertEqual(
            sorted(unread), ["coverage", "month", "path", "source_dates", "year"]
        )

    def test_the_scheduler_still_sets_the_fields_its_consumers_read(self):
        """The producing half of the same contract: if scheduler.py stopped
        populating these, both consumers would silently print "unknown"."""
        scheduler_source = (SRC / "scheduler" / "scheduler.py").read_text(encoding="utf-8")

        self.assertIn("failed_date=", scheduler_source)
        self.assertIn("error=str(exc)", scheduler_source)


class TheTwoVerdictsAboutOneRunTests(unittest.TestCase):
    """The Run Manifest and the Dashboard both judge the same execution.
    They are allowed to differ in detail; they are not allowed to contradict.

    Two artifacts, two audiences, one run: `last_run.json` is what Task
    Scheduler and `ops_status.py` read, the OPS_RUNS row is what a person
    looks at in Notion. Nothing connected them, and C37 measured both
    directions of the resulting disagreement:

        collector failed=1     Dashboard FAIL   manifest SUCCESS  / exit 0
        late_update FAILED     Dashboard OK     manifest DEGRADED / exit 3
        monthly     FAILED     Dashboard OK     manifest DEGRADED / exit 3

    docs/14 §4 names both failure modes in one sentence — "DEGRADED를
    SUCCESS로 접으면 실제 고장이 숨고, FAILED로 접으면 늑대 소년이 되어
    아무도 안 본다" — and the row managed both at once.

    The relation pinned here is one-directional, because the two verdicts
    are not the same question. The Dashboard also warns about per-row facts
    that do not degrade a run (8 rejected Events, a queue that is not
    draining), so WARN is wider than DEGRADED. What must hold:

        Dashboard OK       => manifest SUCCESS      (never quieter)
        manifest DEGRADED  => WARN or FAIL, never OK
        Dashboard FAIL    <=> manifest FAILED

    Checked against `app.runner._SEVERITY` rather than a copy of it, over
    every component in `PIPELINE_COMPONENTS`, so a step added later is
    covered the day it is added.
    """

    def _dashboard(self, **overrides):
        from notion.dashboard import build_ops_run_properties

        kwargs = dict(
            run_id="r", run_at=datetime(2026, 8, 17, 9, 0), transport_moved=0,
            transport_blocked=0, accepted=0, duplicate=0, rejected=0, failed=0,
            scheduler_status="COMPLETED", generated_days=0, reused_days=0,
            backup_status="BACKUP_SUCCESS", notion_synced=0, notion_skipped=0,
            deleted_files=0,
            notion_retried=0, notion_unreadable=0, notion_queued=0,
        )
        kwargs.update(overrides)
        return build_ops_run_properties(**kwargs)["Overall"]["select"]["name"]

    def _manifest(self, *components):
        from runsummary import overall_status

        return overall_status(components)

    def _failed(self, name, severity):
        from runsummary import (
            ComponentResult,
            ComponentStatus,
            Failure,
            Retryability,
        )

        return ComponentResult(
            name,
            ComponentStatus.FAILED,
            failure=Failure(
                classification=f"{name.upper()}_FAILED",
                severity=severity,
                retryability=Retryability.PERMANENT,
                reason="injected",
            ),
        )

    def test_each_step_failing_alone_produces_two_compatible_verdicts(self):
        from app.runner import PIPELINE_COMPONENTS, _SEVERITY, C_DASHBOARD
        from runsummary import OverallStatus, Severity

        for name in PIPELINE_COMPONENTS:
            if name == C_DASHBOARD:
                # The Dashboard cannot report its own failure in the row it
                # failed to write. That absence is reported elsewhere — the
                # manifest's `dashboard` component and the pending queue —
                # and is the one component this comparison cannot cover.
                continue
            severity = _SEVERITY[name]
            with self.subTest(step=name, severity=severity.value):
                manifest = self._manifest(self._failed(name, severity))
                dashboard = self._dashboard(
                    failed_steps=[name],
                    critical_failed_steps=(
                        [name] if severity is Severity.CRITICAL else []
                    ),
                )

                self.assertNotEqual(
                    dashboard,
                    "OK",
                    f"{name} failed and the row calls the run OK",
                )
                self.assertEqual(
                    dashboard == "FAIL",
                    manifest is OverallStatus.FAILED,
                    f"{name}: Dashboard {dashboard} vs manifest {manifest.value}",
                )

    def test_a_run_with_nothing_failed_is_ok_on_both_sides(self):
        from runsummary import OverallStatus

        self.assertEqual(self._dashboard(), "OK")
        self.assertIs(self._manifest(), OverallStatus.SUCCESS)

    def test_a_per_file_failure_warns_without_claiming_the_run_failed(self):
        """The wolf-crying half, kept explicit because it is the one case
        where the two verdicts legitimately differ.

        `failed` counts Event files. `app/runner.py` records the collector
        SUCCESS with `failed` as a metric — docs/03 §53's per-file isolation
        — so the manifest is SUCCESS / exit 0. The row must not say FAIL,
        and must not say OK either: the file did not get processed.
        """
        from runsummary import ComponentResult, ComponentStatus, OverallStatus

        self.assertEqual(self._dashboard(failed=1, accepted=9), "WARN")
        self.assertIs(
            self._manifest(
                ComponentResult(
                    "collector",
                    ComponentStatus.SUCCESS,
                    metrics={"accepted": 9, "failed": 1},
                )
            ),
            OverallStatus.SUCCESS,
        )

    def test_the_runner_passes_both_lists_rather_than_letting_them_default(self):
        """The defaults exist for callers that do not have the numbers. The
        Runner has them — it owns the recorder — and a run that silently
        defaulted would report a healthier row than happened, which is the
        exact shape C32 removed from this module's other inputs.
        """
        source = (SRC / "app" / "runner.py").read_text(encoding="utf-8")

        self.assertIn("failed_steps=[", source)
        self.assertIn("critical_failed_steps=[", source)
        self.assertIn("Severity.CRITICAL", source)

    def test_the_severity_split_is_read_from_the_runner_not_restated(self):
        """Guards the guard: if `_SEVERITY` stopped covering every pipeline
        component, the loop above would skip the gap instead of failing."""
        from app.runner import PIPELINE_COMPONENTS, _SEVERITY

        self.assertEqual(set(_SEVERITY), set(PIPELINE_COMPONENTS))

class DashboardSchemaMappingTests(unittest.TestCase):
    """Adopted decisions: Notion Dashboard / Dashboard Bootstrap.

    These matter *because* of audit finding GAP-1: no entrypoint passes a
    dashboard_client, so none of this code has ever run against real Notion.
    A name or type mismatch between what `record_run()` emits and what
    `bootstrap_dashboard_databases()` creates would therefore stay invisible
    until the day the Dashboard is finally wired — and then every run would
    fail with an HTTP 400.

    Verified: OPS_RUNS is exact — every name present in the schema, every
    Notion type identical. Nothing extra, nothing missing.

    The property count is deliberately not restated here. It was "13", and
    C32 added `Transport Blocked` and `Notion Skipped` (two facts a run
    produced and the row could not show); a number in a docstring is one more
    place that has to be remembered, while the three tests below check the
    two sets agree, which is the property that actually matters.

    Audit finding GAP-11 (new): bootstrap creates FIVE databases, but only
    OPS_RUNS is ever written to.

        OPS_RUNS                    record_run()                  writes
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

    def test_the_caller_scan_finds_the_repository(self):
        """Guard against the guard silently matching nothing.

        `test_ops_backup_builder_has_no_caller` asserts a **negative** over this scan — "nothing in the tree
        does X" — and a negative over an empty set is true. Measured (C66):
        with tree discovery neutered, it passed while checking nothing.

        The trigger is ordinary rather than exotic, and this repository
        already names it: `TheScansThisFileTrustsAreNotEmptyTests` was
        written when `git ls-files` came back empty outside a checkout. A
        renamed or moved `src/` does the same thing to `rglob`, and this
        project is deliberately worked on from several machines
        (AGENT.md §1).
        """
        files = [
            path
            for path in list(SRC.rglob("*.py")) + list(REPO_ROOT.glob("*.py"))
            if "__pycache__" not in str(path)
        ]
        self.assertGreater(len(files), 50)

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


class ProjectsSchemaMappingTests(unittest.TestCase):
    """The same cross-check `DashboardSchemaMappingTests` runs for OPS_RUNS,
    applied to the database that is written on **every Event**.

    `notion/bootstrap.TARGET_PROPERTIES` is what one-time setup creates in
    the PROJECTS database; `properties.build_create_properties()` /
    `build_update_properties()` are what `ExecutionPlanSync` sends to it on
    every sync. Nothing held the two together. A property renamed on one
    side, or one added by `_type_specific_properties()` and not the other,
    is a 400 from the real API on the first sync after the change — and this
    repository's own `TestDoubleFidelityTests` records that
    `InMemoryNotionTransport` accepts *both* "property name not in the
    schema" and "wrong property type", so no existing Notion test could
    catch it.

    OPS_RUNS got these three checks because a mismatch there loses one
    Dashboard row per run. Here it loses the Operational Projection
    entirely: docs/04 §38 keeps the Event rather than dropping it, so the
    Events queue up in `notion_retry_queue.json` and fail identically on
    every retry, forever, because nothing about a schema mismatch changes by
    retrying.

    Every `event_type` is walked rather than one sample: the payload is not
    fixed — `_type_specific_properties()` adds `Blocker`,
    `Current Milestone` and `Completed Date` on different branches, and a
    property that only one branch emits is exactly the one a single-sample
    test would miss.
    """

    EVENT_TYPES = (
        "STARTED", "BLOCKED", "RESUMED", "DECISION_APPROVED",
        "MILESTONE_COMPLETED", "ISSUE_RESOLVED", "COMPLETED", "CANCELLED",
    )

    def _event(self, event_type):
        status = {
            "COMPLETED": "COMPLETED",
            "CANCELLED": "CANCELLED",
        }.get(event_type, "IN_PROGRESS")
        return create_event(
            source="DESKTOP_1",
            role="CTO_BACKEND",
            project_id="SEARCH_FRONTEND",
            event_type=event_type,
            status=status,
            summary="schema mapping probe",
            milestone="Search UI",
            blocker="blocked on X" if event_type == "BLOCKED" else None,
            history_candidate=True,
            timestamp="2026-08-01T10:00:00+09:00",
            event_id=f"SCHEMA-{event_type}",
        )

    def _payloads(self):
        """(label, properties) for every payload the Sync can send."""
        from notion.properties import (
            build_create_properties,
            build_update_properties,
            humanize_project_id,
        )

        for event_type in self.EVENT_TYPES:
            event = self._event(event_type)
            yield (
                f"create/{event_type}",
                build_create_properties(
                    event, project_name=humanize_project_id(event.project_id)
                ),
            )
            yield f"update/{event_type}", build_update_properties(event)

    def test_the_sync_emits_no_property_absent_from_the_projects_schema(self):
        """The mismatch that would 400 on the first real sync."""
        from notion.bootstrap import TARGET_PROPERTIES

        for label, properties in self._payloads():
            with self.subTest(payload=label):
                self.assertEqual(set(properties) - set(TARGET_PROPERTIES), set())

    def test_every_projects_schema_property_is_reachable_from_some_payload(self):
        """The other direction: a column bootstrap creates that nothing ever
        fills is a column an operator reads as "no data" forever."""
        from notion.bootstrap import TARGET_PROPERTIES

        emitted = set()
        for _label, properties in self._payloads():
            emitted |= set(properties)

        self.assertEqual(set(TARGET_PROPERTIES) - emitted, set())

    def test_every_emitted_property_uses_the_schema_declared_notion_type(self):
        from notion.bootstrap import TARGET_PROPERTIES

        for label, properties in self._payloads():
            for name, value in properties.items():
                with self.subTest(payload=label, property=name):
                    self.assertEqual(
                        next(iter(value)), next(iter(TARGET_PROPERTIES[name]))
                    )

    def test_the_title_property_is_the_one_bootstrap_renames_to(self):
        """`bootstrap._bootstrap_title_property()` renames whatever Title the
        database has to `TITLE_PROPERTY_NAME`, and the create payload has to
        use that same name or the rename buys nothing."""
        from notion.bootstrap import TARGET_PROPERTIES, TITLE_PROPERTY_NAME
        from notion.properties import build_create_properties, humanize_project_id

        event = self._event("MILESTONE_COMPLETED")
        properties = build_create_properties(
            event, project_name=humanize_project_id(event.project_id)
        )

        self.assertIn("title", TARGET_PROPERTIES[TITLE_PROPERTY_NAME])
        self.assertIn("title", properties[TITLE_PROPERTY_NAME])

    def test_the_two_readers_read_properties_the_schema_declares(self):
        """`extract_last_updated()` / `extract_last_event_id()` are the guards
        §29-30 and §62 rest on. Both key on a property *name*, and a name
        that is not in the schema reads as None forever — which is the
        direction that silently disables a guard rather than failing."""
        from notion.bootstrap import TARGET_PROPERTIES

        for name, kind in (("Last Updated", "date"), ("Last Event ID", "rich_text")):
            with self.subTest(property=name):
                self.assertIn(kind, TARGET_PROPERTIES[name])


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

    def test_the_monthly_package_scan_finds_its_modules(self):
        """Guard against the guard silently matching nothing.

        The two tests below each assert a negative over this scan, which asserts a **negative** over this scan — "nothing in the tree
        does X" — and a negative over an empty set is true. Measured (C66):
        with tree discovery neutered, both of them passed while checking nothing.

        The trigger is ordinary rather than exotic, and this repository
        already names it: `TheScansThisFileTrustsAreNotEmptyTests` was
        written when `git ls-files` came back empty outside a checkout. A
        renamed or moved `src/` does the same thing to `rglob`, and this
        project is deliberately worked on from several machines
        (AGENT.md §1).
        """
        modules = sorted((SRC / "monthly").glob("*.py"))
        self.assertGreaterEqual(len(modules), 4, modules)

    def test_monthly_imports_nothing_from_the_rest_of_the_project(self):
        for path in sorted((SRC / "monthly").glob("*.py")):
            with self.subTest(module=path.name):
                self.assertEqual(
                    self._project_imports(path),
                    set(),
                    f"src/monthly/{path.name} reaches outside the package; "
                    f"docs/09 §13 requires Monthly to consolidate Daily files only",
                )

    #: Directories Monthly may not reach for. Components, not joined strings:
    #: see `_path_components()`.
    FORBIDDEN_COMPONENTS = frozenset(
        {"history_candidates", "events", "incoming", "processed", "rejected"}
    )

    @staticmethod
    def _path_components(source: str):
        """Every literal that this source uses as a **path component**.

        Read by the parser rather than by substring, and the difference was
        measured. The check this replaced looked for three joined strings —
        `history_candidates`, `events/incoming`, `processed` — and nine
        spellings were tried against it. Four went through, all of them the
        `events/incoming` pair written the way this repository actually
        writes paths:

            ROOT / "events" / "incoming"            evaded
            Path("runtime", "events", "incoming")   evaded
            os.path.join(root, "events", "incoming") evaded
            "events\\incoming"                       evaded

        `src/` has no joined path literal anywhere; every path constant in
        the tree is component-wise (`PROJECT_ROOT / "runtime" / "events" /
        "incoming"`). So the one form the check could see was the one form
        nobody writes — the C58 shape, and the same one C66 found in
        `BackupLogIsNeverPersistedTests` an hour earlier.

        Components rather than raw tokens, so that prose is not evidence: a
        docstring saying "events" is not a coupling, and a token-level scan
        would have to be edited away the first time someone explained the
        boundary in words. Measured on HEAD — `src/monthly/` contains none
        of these words at all, in code or in prose, so the precision costs
        nothing today and is there for the day it does.
        """
        import ast

        found = []
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                for side in (node.left, node.right):
                    if isinstance(side, ast.Constant) and isinstance(side.value, str):
                        found.append((side.value, node.lineno))
            elif isinstance(node, ast.Call):
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        found.append((arg.value, node.lineno))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                flat = node.value.replace("\\", "/")
                if "/" in flat:
                    for part in flat.split("/"):
                        found.append((part, node.lineno))
        return found

    def test_monthly_reads_no_event_or_repository_path(self):
        """The same rule from the other side: even without an import, a
        hardcoded path into `history_candidates/` or `events/` would
        reintroduce the coupling."""
        offenders = []
        for path in sorted((SRC / "monthly").glob("*.py")):
            for value, line in self._path_components(
                path.read_text(encoding="utf-8")
            ):
                if value in self.FORBIDDEN_COMPONENTS:
                    offenders.append(f"{path.name}:{line}: {value!r}")

        self.assertEqual(offenders, [], offenders)

    def test_the_detector_sees_every_way_a_path_is_written_here(self):
        """Shoot at the gate. Four of these evaded the check this replaced."""
        spellings = {
            'ROOT / "events" / "incoming"': 'D = ROOT / "events" / "incoming"',
            'Path("runtime", "events", "incoming")': 'D = Path("runtime", "events", "incoming")',
            'os.path.join(root, "events", "incoming")': 'D = os.path.join(root, "events", "incoming")',
            'the joined literal': 'D = ROOT / "events/incoming"',
            'a backslash literal': "D = ROOT / 'events" + chr(92) + chr(92) + "incoming'",
            'history_candidates as a component': 'D = ROOT / "history_candidates" / "keep"',
            'processed as a component': 'D = ROOT / "processed"',
            'an aliased Path': (
                "from pathlib import Path as _P"
                + chr(10)
                + "D = _P('runtime', 'processed')"
            ),
        }
        for label, snippet in spellings.items():
            with self.subTest(spelling=label):
                found = {
                    value
                    for value, _ in self._path_components(snippet)
                    if value in self.FORBIDDEN_COMPONENTS
                }
                self.assertTrue(found, f"a path spelled {label} would not be seen")

    def test_the_detector_is_silent_on_the_package_it_guards(self):
        """Precision from the other side, and the reason components beat
        tokens: Monthly's own literals (`runtime`, `state`, `monthly`,
        section titles, regexes) must not read as a coupling."""
        for path in sorted((SRC / "monthly").glob("*.py")):
            with self.subTest(module=path.name):
                found = [
                    (value, line)
                    for value, line in self._path_components(
                        path.read_text(encoding="utf-8")
                    )
                    if value in self.FORBIDDEN_COMPONENTS
                ]
                self.assertEqual(found, [])

    def test_prose_naming_the_boundary_is_not_a_violation(self):
        """A docstring explaining what Monthly may not touch is documentation,
        not coupling. The token-level alternative would have flagged this very
        sentence."""
        prose = chr(10).join(
            [
                "def f():",
                '    """Monthly never reads events or history_candidates."""',
                "    return 1",
            ]
        )
        found = [
            value
            for value, _ in self._path_components(prose)
            if value in self.FORBIDDEN_COMPONENTS
        ]
        self.assertEqual(found, [])

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


class EveryImportIsVisibleToTheImportGuardsTests(unittest.TestCase):
    """Three gates read the import graph, and all three read it as AST
    `Import` / `ImportFrom` nodes. A dynamic import is neither.

    The gates:

      * `LayeringInvariantTests` — `events/` may import nothing local, and
        nothing may import `app/`. This is what keeps the dependency graph
        acyclic.
      * `test_repository_hygiene.py::DependencyGuardTests` — `src/` imports
        only the standard library. This is why `python -m pytest` needs no
        install step at all (docs/11 §101 Release Environment Check).
      * `test_monthly_history.py::MonthlyIsNotNotionTests` — Company History
        never reaches Notion.

    Each states a rule about what this code may depend on, and each was
    measured, one mutation at a time, to be silent about a dependency
    spelled dynamically:

        *** MISSES ***  importlib.import_module("requests")   in src/oplog.py
        *** MISSES ***  __import__("requests")                in src/oplog.py
        *** MISSES ***  importlib.import_module("notion")     in src/events/schema.py
        *** MISSES ***  importlib.import_module("notion")     in src/monthly/markdown.py

    Teaching three detectors to resolve a dynamic import is not possible in
    general — the argument can be computed. So the escape hatch is closed
    instead of chased: **production code does not import dynamically**, and
    with that held, an AST walk over `Import`/`ImportFrom` really does see
    every dependency, which is what the three gates already assume.

    Enforceable at full precision — measured over all 80 production files:
    no `__import__`, no `importlib` in any form, and no builtin `exec`,
    `eval` or `compile`. (The 17 `compile` calls here are all `re.compile`,
    which is why the check looks at the receiver and not just the name.)

    Tests are deliberately out of scope: several load `ops_status.py`
    through `importlib.util.spec_from_file_location()` on purpose, and the
    three gates above make claims about production code, not about the
    harness that reads it.
    """

    #: Attribute-spelled ways to import or execute code by name.
    DYNAMIC_ATTRS = {
        "import_module",
        "spec_from_file_location",
        "exec_module",
        "load_module",
        "SourceFileLoader",
    }

    #: Builtins that take code as a string. `re.compile` is an attribute
    #: call and so is not one of these.
    DYNAMIC_BUILTINS = {"__import__", "exec", "eval", "compile"}

    def _production_files(self):
        return [
            path
            for path in list(SRC.rglob("*.py")) + list(REPO_ROOT.glob("*.py"))
            if "__pycache__" not in str(path)
        ]

    @classmethod
    def _dynamic_imports(cls, tree):
        """`[(construct, lineno)]` for every dynamic import or exec."""
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found += [
                    (f"import {a.name}", node.lineno)
                    for a in node.names
                    if a.name.split(".")[0] == "importlib"
                ]
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] == "importlib":
                    found.append((f"from {node.module} import ...", node.lineno))
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in cls.DYNAMIC_BUILTINS:
                    found.append((f"{func.id}()", node.lineno))
                elif isinstance(func, ast.Attribute) and func.attr in cls.DYNAMIC_ATTRS:
                    found.append((f".{func.attr}()", node.lineno))
        return found

    def test_no_production_module_imports_dynamically(self):
        offenders = {}
        for path in self._production_files():
            found = self._dynamic_imports(ast.parse(path.read_text(encoding="utf-8")))
            if found:
                offenders[str(path.relative_to(REPO_ROOT))] = found

        self.assertEqual(
            offenders,
            {},
            "a dependency spelled this way is invisible to LayeringInvariantTests, "
            "DependencyGuardTests and MonthlyIsNotNotionTests, all three of which "
            f"read the import graph as AST Import nodes: {offenders}",
        )

    def test_the_sweep_reaches_the_files_it_claims_to(self):
        """C57's rule: a gate whose candidate set is empty passes without
        checking anything. This one sweeps two roots, and a wrong `SRC` or a
        renamed entrypoint would quietly empty either half."""
        files = {path.name for path in self._production_files()}

        self.assertGreaterEqual(len(files), 50)
        for expected in ("oplog.py", "schema.py", "markdown.py", "ops_status.py"):
            with self.subTest(module=expected):
                self.assertIn(expected, files)

    # --------------------------------------------- detector's detector (C61)
    DYNAMIC = {
        "__import__": ("def f():", "    return __import__('requests')"),
        "importlib.import_module": (
            "import importlib",
            "def f():",
            "    return importlib.import_module('requests')",
        ),
        "from importlib import import_module": (
            "from importlib import import_module",
            "def f():",
            "    return import_module('requests')",
        ),
        "spec_from_file_location": (
            "def f(p):",
            "    return util.spec_from_file_location('m', p)",
        ),
        "exec of a string": ("def f(src):", "    exec(src)"),
        "eval of a string": ("def f(src):", "    return eval(src)"),
    }

    STATIC = {
        "an ordinary import": ("import json", "def f():", "    return json"),
        "an ordinary from-import": (
            "from datetime import datetime",
            "def f():",
            "    return datetime",
        ),
        "re.compile": (
            "import re",
            "PATTERN = re.compile(r'x')",
        ),
        "a loader passed around but never called": (
            "def f(loader):",
            "    return loader",
        ),
    }

    def test_the_detector_recognises_every_dynamic_spelling(self):
        for label, lines in self.DYNAMIC.items():
            with self.subTest(spelling=label):
                found = self._dynamic_imports(ast.parse(chr(10).join(lines)))
                self.assertTrue(found, f"{label} was not recognised")

    def test_the_detector_leaves_ordinary_imports_alone(self):
        """Precision. `re.compile` is the case that matters: there are 17 of
        them here, and a detector that matched on the name alone would flag
        every one and be deleted within the week."""
        for label, lines in self.STATIC.items():
            with self.subTest(spelling=label):
                found = self._dynamic_imports(ast.parse(chr(10).join(lines)))
                self.assertEqual(found, [], f"{label} was wrongly flagged: {found}")


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
        # `sys.argv` handling for the four entrypoints, which read no
        # arguments at all. A leaf for the same reason as `oplog` and
        # `runsummary`: every entrypoint sits above it, so it may sit
        # under none of them.
        "cli": set(),
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
        # `cli` joined in C79, when this became the fifth entrypoint to
        # refuse a command-line argument. A leaf, like `oplog` and
        # `runsummary`, so it closes no cycle.
        #
        # `oplog` joined in C125, for `SECRET_RE`. Decision Context is the
        # third door text takes into Company History and the only one that
        # had neither a refusal nor a report — measured: a token typed here
        # reaches the Daily History markdown and the backup remote. The same
        # edge `collector` and `agent` already declare, to the same leaf, and
        # it closes no cycle either.
        "review_cli": {"history", "cli", "oplog"},
        # Read-only rollups over Execution Evidence. Three edges, each for
        # exactly one thing it must not restate (C28):
        #   events    parse the Event files
        #   notion    docs/04 §20-28's blocker/completion rule, read out of
        #             `properties._type_specific_properties()`
        #   reporter  docs/02 §8's Desktop->role table, which lives in
        #             `profiles.PROFILES` and whose own comment says it "only
        #             pairs them the way the spec already does"
        #   oplog     `dashboard.to_payload()` is the boundary where Event
        #             text leaves this machine, and `redact`/`one_line` are
        #             the two functions this project has for that. A leaf, so
        #             it closes no cycle — the same edge `collector` and
        #             `agent` already have and for the same reason.
        # It writes nothing and nothing writes to it, so it sits beside the
        # other derivations rather than under them. `reporter` is a writer
        # package, but the edge reaches `profiles.py` — pure vocabulary — and
        # `reporter` imports nothing from here, so the graph stays acyclic.
        "controltower": {"events", "notion", "oplog", "reporter"},
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
        """package -> set of sibling packages it imports.

        `ast.walk`, so a function-local import counts exactly like a
        top-level one -- this codebase uses them freely and a late import is
        the same edge in the graph.

        **Relative imports are resolved, not skipped (C97).** This read
        `node.level == 0` and dropped every relative import, which is right
        for `level == 1` (a sibling module inside the same package cannot
        cross a package boundary) and wrong for `level >= 2`, which can:

            src/agent/status.py:  from ..events import Event

        resolves to the top-level `events` package and is an ordinary
        `agent -> events` edge that this table would never have seen. The
        class docstring says why that matters more here than elsewhere --
        `ALLOWED` is deliberately a permit-list rather than a deny-list
        *because* "a forbidden-list silently permits anything nobody thought
        to forbid", and a permit-list with a shape it cannot parse has the
        same hole by a different route.

        Measured: `level >= 2` imports in `src/` today: **0**. So this
        changes no edge in the current tree -- which is the point. The gate
        is for the import nobody has written yet, and it was blind to one of
        the two ways of writing it. Injected on `src/scheduler/lock.py`, the
        layering test goes from PASS to FAIL.
        """
        packages = self._packages()
        edges = {}
        for package in packages:
            imported = set()
            for path in self._sources(package):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        names = [a.name.split(".")[0] for a in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        names = self._relative_target(path, node)
                    else:
                        continue
                    imported |= {n for n in names if n in packages and n != package}
            edges[package] = imported
        return edges

    @staticmethod
    def _relative_target(path, node):
        """The top-level package an `ImportFrom` names, absolute or not.

        Returned as a list so the caller treats every import shape the same
        way. An import that resolves to nothing -- `from . import x` inside
        a top-level module, which is not legal anyway -- returns empty
        rather than guessing.
        """
        if node.level == 0:
            return [node.module.split(".")[0]] if node.module else []
        # `path` is `src/<package>/.../<module>.py`; drop the filename to get
        # the package the import is written from, then climb `level - 1`.
        here = list(path.relative_to(SRC).parts[:-1])
        base = here[: len(here) - (node.level - 1)]
        if node.module:
            base = base + node.module.split(".")
        return base[:1]

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


class TheLayeringTableSeesBothWaysOfWritingAnImportTests(unittest.TestCase):
    """C97. The predicate behind `LayeringInvariantTests`, on its own inputs.

    That class states its own thesis: `ALLOWED` is a permit-list rather than
    a deny-list *because* "a forbidden-list silently permits anything nobody
    thought to forbid". Its edge extractor then dropped every relative
    import:

        elif isinstance(node, ast.ImportFrom) and node.level == 0 ...

    For `level == 1` that is right -- a sibling module inside the same
    package cannot cross a package boundary. For `level >= 2` it is not:

        src/scheduler/lock.py:  from ..events import Event

    is an ordinary `scheduler -> events` edge, and the table could not see
    it. A permit-list with a shape it cannot parse has the same hole as the
    deny-list it was chosen over.

    **Measured, both directions, on that exact injection:**

        scheduler edges, clean tree        daily, history
        ALLOWED['scheduler']               daily, history
        after injecting the import         daily, events, history
        outside the declared layer         events        <- the gate fires

        pre-C97 extractor sees             json, os      <- no edge at all
        post-C97 extractor sees            events, json, os, scheduler

    **And it changes nothing today**, which is the point rather than a
    caveat: `level >= 2` imports in `src/` right now number **0**. The gate
    is for the import nobody has written yet, and it was blind to one of the
    two ways of writing it. `test_the_tree_has_none_of_these_today` keeps
    that measurement honest -- if the count stops being 0, this class is
    where the reason gets written down.

    Same shape as C93, one layer up: a gate that reads one spelling of the
    thing it exists to catch.
    """

    def _node(self, source):
        """The single `ImportFrom` in `source`."""
        return next(
            node
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom)
        )

    def _target(self, source, module_path):
        return LayeringInvariantTests._relative_target(
            SRC / module_path, self._node(source)
        )

    def test_an_absolute_import_names_its_top_level_package(self):
        self.assertEqual(
            self._target("from events.schema import Event", "scheduler/lock.py"),
            ["events"],
        )

    def test_a_sibling_relative_import_stays_inside_its_package(self):
        """`level == 1` resolves to the package it is written in, which the
        caller then discards as `n != package`. It was never the problem and
        must not become one."""
        self.assertEqual(
            self._target("from .state import load_state", "scheduler/lock.py"),
            ["scheduler"],
        )

    def test_a_bare_relative_import_resolves_too(self):
        """`from . import x` carries no module name at all."""
        self.assertEqual(
            self._target("from . import state", "scheduler/lock.py"), ["scheduler"]
        )

    def test_a_relative_import_that_climbs_out_is_the_edge_it_looks_like(self):
        """The defect. Without this the import above is invisible to the
        table that exists to permit or refuse exactly it."""
        self.assertEqual(
            self._target("from ..events import Event", "scheduler/lock.py"),
            ["events"],
        )

    def test_climbing_out_of_a_nested_package_resolves_to_the_package(self):
        """Two levels of package, one level of climb -- the base is the
        package, not the tree root."""
        self.assertEqual(
            self._target("from ..other import thing", "scheduler/sub/mod.py"),
            ["scheduler"],
        )

    def test_an_import_that_resolves_to_nothing_is_not_guessed(self):
        """A relative import in a top-level module is not legal Python, and
        the honest answer to an illegal shape is nothing rather than a
        plausible package name."""
        self.assertEqual(self._target("from . import x", "oplog.py"), [])

    @staticmethod
    def _climbing_imports(source, label):
        return [
            f"{label}:{node.lineno}"
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom) and node.level >= 2
        ]

    def test_the_tree_has_none_of_these_today(self):
        """Anti-vacuity in the other direction. The change above is a no-op
        on the current tree, and that has to be a measured claim rather than
        an assumption -- if it stops being true, the new edge needs a row in
        `ALLOWED` and a sentence here.

        The scan is checked against an input that *does* contain one before
        it is trusted to report zero. A mutation that emptied the scan
        passed the tree assertion alone, for the reason every `== []` test
        has: nothing distinguishes "looked and found none" from "did not
        look". The synthetic case is what distinguishes them.

        What this still cannot catch is a mutation of the final assertion
        itself -- `assertEqual(found, found)` passes and no other test
        covers it. That is inherent to asserting a negative over a scan, and
        it is why the scan, not the assertion, is where the teeth are.
        """
        self.assertEqual(
            self._climbing_imports("from ..events import Event\n", "probe.py"),
            ["probe.py:1"],
            "the scan cannot see the thing it is scanning for",
        )
        self.assertEqual(
            self._climbing_imports("from .state import x\nimport os\n", "probe.py"),
            [],
            "the scan fires on imports that do not climb out",
        )

        found = []
        for path in sorted(SRC.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            found += self._climbing_imports(
                path.read_text(encoding="utf-8"),
                path.relative_to(SRC).as_posix(),
            )

        self.assertEqual(found, [], "a cross-package relative import appeared")


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

    def test_the_file_scan_finds_the_repository(self):
        """Guard against the guard silently matching nothing.

        Both scanning tests below assert a negative over this scan, which asserts a **negative** over this scan — "nothing in the tree
        does X" — and a negative over an empty set is true. Measured (C66):
        with tree discovery neutered, both of them passed while checking nothing.

        The trigger is ordinary rather than exotic, and this repository
        already names it: `TheScansThisFileTrustsAreNotEmptyTests` was
        written when `git ls-files` came back empty outside a checkout. A
        renamed or moved `src/` does the same thing to `rglob`, and this
        project is deliberately worked on from several machines
        (AGENT.md §1).
        """
        self.assertGreater(len(list(self._python_files())), 50)

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

    @staticmethod
    def _backup_path_expressions(source: str):
        """Every path expression in `source` naming a `backup` component.

        Read by the parser, not by substring, and the difference is measured
        rather than assumed. The two `assertNotIn`s this replaced looked for
        exactly `"backup" / ` and `logs/backup`, which is **one** of the ways
        this repository spells a path. Eight natural spellings were tried
        against the old check and five went straight through — including
        `LOGS_DIR / "backup"`, which is the idiom every other path constant
        in `src/` is written in (`PROJECT_ROOT / "runtime" / "events" /
        "incoming"` and eleven more like it).

        A gate for "nothing writes this artifact" that only sees the
        argument order nobody uses is the C58 shape: the check runs, stays
        green, and is not looking at its own subject.

        Same eight spellings against this: seven are caught. An aliased
        `Path` makes nine, and it was a mutation rather than a guess that
        added it — see the callee-agnostic note below.

        **The one that still evades, named rather than left to be
        discovered:** a component held in a variable —

            BACKUP_SUB = "backup"
            directory = LOGS_DIR / BACKUP_SUB

        Following that needs constant propagation, which is a different
        program. It is also not the shape a writer arrives in by accident,
        and `test_nothing_in_the_repository_serialises_a_backup_log_entry`
        is the half that does not depend on how a path is spelled at all.
        """
        import ast

        hits = []
        tree = ast.parse(source)
        for node in ast.walk(tree):
            # `<anything> / "backup"` and `"backup" / <anything>`
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                for side in (node.left, node.right):
                    if isinstance(side, ast.Constant) and side.value == "backup":
                        hits.append(f"line {node.lineno}: path join")
            # `Path("runtime", "logs", "backup")`, `os.path.join(x, "backup")`
            #
            # Callee-agnostic on purpose. The first version of this check
            # asked for the callee to be named `Path` or `join`, and a
            # mutation went straight through it —
            #
            #     from pathlib import Path as _P
            #     directory = _P("runtime", "logs", "backup")
            #
            # which is C61's finding arriving in the detector written to fix
            # C58's: a name check does not see an alias. Any call taking
            # `"backup"` as a bare positional argument is reported instead,
            # and the cost of that breadth was measured rather than assumed
            # — zero hits across the 80 files this scans.
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and arg.value == "backup":
                        hits.append(f"line {node.lineno}: {name}() component")
            # the directory spelled out in one literal
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "logs/backup" in node.value.replace("\\", "/"):
                    hits.append(f"line {node.lineno}: literal path")
            # an f-string whose constant part names it
            if isinstance(node, ast.JoinedStr):
                literal = "".join(
                    part.value
                    for part in node.values
                    if isinstance(part, ast.Constant) and isinstance(part.value, str)
                ).replace("\\", "/")
                if "/backup" in literal or literal.startswith("backup/"):
                    hits.append(f"line {node.lineno}: f-string component")
        return hits

    def test_no_backup_log_directory_is_ever_created(self):
        """§69's location. Nothing in the source names it."""
        offenders = []
        for path in self._python_files():
            for hit in self._backup_path_expressions(
                path.read_text(encoding="utf-8")
            ):
                offenders.append(f"{path.name}: {hit}")

        self.assertEqual(offenders, [], offenders)

    def test_the_detector_sees_the_spellings_the_old_one_missed(self):
        """Shoot at the gate, the way C58-C61 shot at theirs.

        Five of these evaded the substring check this replaced. Written out
        rather than reduced to one representative: each exercises a different
        node type, and a detector that handled only `BinOp` would pass a
        one-spelling test.
        """
        spellings = {
            'LOGS_DIR / "backup"': 'D = LOGS_DIR / "backup"',
            '"backup" / name': 'p = "backup" / run_id',
            'Path("runtime", "logs", "backup")': 'd = Path("runtime", "logs", "backup")',
            # The alias. A mutation proved the first draft could not see it.
            'an aliased Path': 'from pathlib import Path as _P\nd = _P(\'runtime\', \'logs\', \'backup\')',
            'logs_dir / "backup" / filename': 'p = logs_dir / "backup" / filename',
            'f"{logs}/backup/{run_id}.json"': 'p = f"{logs}/backup/{run_id}.json"',
            'Path("runtime/logs/backup")': 'd = Path("runtime/logs/backup")',
            'os.path.join(logs, "backup")': 'd = os.path.join(logs, "backup")',
        }
        for label, snippet in spellings.items():
            with self.subTest(spelling=label):
                self.assertTrue(
                    self._backup_path_expressions(snippet),
                    f"a writer spelled {label} would not be seen",
                )

    def test_the_detector_is_silent_on_the_tree_it_guards(self):
        """Precision, from the other side. A detector that fired on `backup`
        anywhere would flag `src/backup/` — the package this project has had
        since the beginning — and be edited away rather than read."""
        for path in self._python_files():
            with self.subTest(path=path.name):
                self.assertEqual(
                    self._backup_path_expressions(path.read_text(encoding="utf-8")),
                    [],
                )

    def test_the_one_spelling_that_still_evades_is_named(self):
        """The residual, pinned as a fact rather than left in prose.

        If this ever starts failing, constant propagation arrived and the
        docstring above should stop claiming this hole.
        """
        computed = 'BACKUP_SUB = "backup"\nd = LOGS_DIR / BACKUP_SUB'
        self.assertEqual(self._backup_path_expressions(computed), [])

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



class DuplicatedRulesStayInStepTests(unittest.TestCase):
    """The copies this repository keeps on purpose, checked rather than
    promised.

    Three rules exist in more than one module because the layering forbids
    the import that would remove the duplication. Two of the three already
    had a test comparing the copies. One did not — and it was the one whose
    docstring said it did:

        INCOMPLETE_WRITE_PREFIX   4 copies   `IncompleteWriteInvariantTests`
                                             collects the literal from each
                                             file and compares  -> covered
        safe_event_filename       2 copies   "tests assert the two copies
                                             agree"  -> no such test existed
        _is_sole_identifier       2 copies   "Mirrors
                                             daily/markdown._is_sole_identifier()"
                                             -> nothing checked it

    That is E-11's shape ("a claim in a comment that outlived the code"),
    landing on a sanitiser that exists because of a path-traversal defect
    (BUG-15) and a Windows path-length failure. `reporter.local_output` is
    the sending side's copy and `transport.onedrive` is the one that names
    the file OneDrive carries to Desktop 4; if they drifted, the same Event
    would be written under two different names by two code paths, and every
    lookup keyed on one of them would miss.

    Compared by behaviour, not by source text. "The two copies agree" is a
    statement about what they return, and a formatting change to one of them
    is not a defect — the test that fails on reindentation is a test people
    learn to edit rather than read.
    """

    # Every input here is a shape that has actually mattered somewhere in
    # this repository, not a generic fuzz list.
    EVENT_IDS = (
        "EVT-001",                       # the ordinary case
        "evt.with.dots",
        "EVT_WITH_UNDERSCORES-1",
        "../target/X",                   # BUG-15, POSIX separator
        "..\\target\\X",                 # BUG-15, Windows separator
        "/absolute/path",
        "C:\\Windows\\System32\\x",
        "a:b",                           # NTFS alternate data stream
        'weird<>:"|?*chars',
        "...",                           # sanitises to nothing
        "",                              # empty
        "   ",                           # whitespace only
        "한글-이벤트-1",                  # non-ASCII
        "E" * 119,                       # just under the stem bound
        "E" * 120,                       # exactly at it
        "E" * 121,                       # just over
        "E" * 250,                       # the WinError 123 case
        "with\nnewline",
        "with\ttab",
        "trailing.",
        "_leading",
        # Win32 device names (C64). Not a generic fuzz addition: `NUL.json`
        # is the NUL device on the deployment machine, so an id reaching a
        # copy that had not learned this rule would be written to a device
        # by one code path and to a file by the other — the exact drift this
        # class exists to catch, on the one input where the consequence is a
        # silent loss rather than a mismatch.
        "NUL",
        "com1",
        "NUL.json",
        "COM1." + "x" * 200,
        "NULx",                          # near-miss: must NOT be renamed
    )

    def test_both_safe_event_filename_copies_return_the_same_name(self):
        from reporter.local_output import safe_event_filename as sending_side
        from transport.onedrive import safe_event_filename as transport_side

        for event_id in self.EVENT_IDS:
            with self.subTest(event_id=event_id[:40]):
                self.assertEqual(sending_side(event_id), transport_side(event_id))

    def test_the_shared_rule_still_does_its_two_jobs(self):
        """Not a tautology check: two identical-but-wrong copies would pass
        the comparison above. These are the two properties the duplication
        exists to provide."""
        from reporter.local_output import safe_event_filename

        for event_id in self.EVENT_IDS:
            with self.subTest(event_id=event_id[:40]):
                name = safe_event_filename(event_id)
                # No separator survives, so no id can address another
                # directory (BUG-15).
                self.assertNotIn("/", name)
                self.assertNotIn("\\", name)
                self.assertNotIn("..", name)
                # Bounded, so no id can produce a path Windows refuses.
                self.assertLessEqual(len(name), 140)
                self.assertTrue(name.endswith(".json"))

    def test_distinct_ids_never_collide_on_one_filename(self):
        """Both sanitising and truncating are many-to-one, which is why the
        rule appends a digest whenever it changes the name at all. The two
        copies agreeing is worthless if the rule they agree on loses Events.
        """
        from reporter.local_output import safe_event_filename

        names = [safe_event_filename(event_id) for event_id in self.EVENT_IDS]

        self.assertEqual(len(names), len(set(names)))

    def test_both_is_sole_identifier_copies_agree(self):
        from daily.markdown import _is_sole_identifier as daily_side
        from monthly.parser import _is_sole_identifier as monthly_side

        cases = (
            [(0, "Event ID: EVT-1")],
            [(0, "Event ID: EVT-1"), (1, "Summary: did a thing")],
            [(0, "Event ID: EVT-1"), (1, "Event ID: EVT-2")],
            [(0, "Summary: did a thing"), (1, "Event ID: EVT-1")],
            [(0, "Summary: did a thing")],
            [(0, "Event ID: EVT-1"), (1, "Status: DONE"), (2, "Event ID: EVT-2")],
            [(0, "Event IDs: EVT-1")],          # a label that merely starts alike
            [(0, "event id: EVT-1")],           # case differs
        )
        for indexed in cases:
            with self.subTest(first=indexed[0][1]):
                self.assertEqual(daily_side(indexed), monthly_side(list(indexed)))

    def test_the_comparison_would_notice_a_drifted_copy(self):
        """Guards the guard. Two functions that never disagree on any input
        in the corpus would let a real divergence through if the corpus were
        the problem, so this checks the corpus can separate two rules that
        differ by exactly one of the properties above.
        """
        from reporter.local_output import safe_event_filename

        def drifted(event_id: str) -> str:
            """The same rule without the length bound — the half that was
            added later, and the half a copy would most plausibly miss."""
            import re

            return re.sub(r"[^A-Za-z0-9_.-]", "_", event_id).strip("._") + ".json"

        differing = [
            event_id
            for event_id in self.EVENT_IDS
            if safe_event_filename(event_id) != drifted(event_id)
        ]

        self.assertTrue(differing)
        self.assertIn("E" * 250, differing)



class NoWriterStagesIntoProcessedTests(unittest.TestCase):
    """The premise `controltower.read_events()` counts a `.tmp-…json` on.

    `is_incomplete_write()` is right about every directory a writer stages
    into, and `processed/` is not one of them. C64 removed the skip there and
    the removal is only correct while that stays true — so it is a gate
    rather than a sentence in a docstring.

    Two halves, because two different edits would break it: a new
    `tempfile.mkstemp(dir=...)` aimed at the processed directory, and a
    Collector that stopped arriving by `os.replace` of the file it validated.

    If this ever fails, `read_events()` has to skip staging names again — and
    then the divergence C64 measured (`control tower 2, company block 3,
    reconciliation 3`) comes back and needs a different answer.
    """

    @staticmethod
    def _mkstemp_calls():
        """`(module, lineno, the call as source)` for every staging write.

        **By AST, not by matching `tempfile.mkstemp(...)` in the file text
        (C98).** The regex form counted prose as code: two of its sixteen
        matches were a docstring and a comment *describing* the idiom --

            controltower/rollup.py   "Every `tempfile.mkstemp()` in ..."
            reporter/local_output.py "# `tempfile.mkstemp(dir=<the ...>)` and"

        -- so the real number of staging writers is **14**, and the
        `>= 12` guard below was being padded by two sentences. Worse in the
        other direction: the offender test asks whether `"processed"` appears
        in the matched text, so a docstring explaining *why nothing may stage
        into `processed/`* would be reported as a writer that does. The
        sentence in `controltower/rollup.py` is one edit away from saying
        exactly that.

        Fifth correction of this shape in this Sprint (C86, C88, C90, C92,
        here): a claim about what the code *does* has to be read from the
        code, and prose in the same file is not the code.

        Bare `mkstemp` counts too. Nothing writes `from tempfile import
        mkstemp` today, but the regex required the dotted spelling, and a
        gate that only recognises one way of writing a call is the shape
        C93 and C97 each found one layer up.
        """
        found = []
        for path in sorted(SRC.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            found += NoWriterStagesIntoProcessedTests._mkstemp_calls_in(
                path.read_text(encoding="utf-8"),
                path.relative_to(SRC).as_posix(),
            )
        return found

    @staticmethod
    def _mkstemp_calls_in(source, module):
        """The predicate itself, over one source text.

        Split out so it can be given inputs that are not in the tree --
        `test_the_sweep_reads_calls_rather_than_sentences` fed a *copy* of
        this logic at first, so a mutation that dropped the bare `mkstemp`
        spelling from the real one passed. A test of a copy is a second
        opinion, which is the thing this repository keeps removing.
        """
        import ast

        found = []
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else func.id if isinstance(func, ast.Name) else None
            )
            if name != "mkstemp":
                continue
            found.append(
                (module, node.lineno,
                 ast.get_source_segment(source, node) or ast.unparse(node))
            )
        return found

    @staticmethod
    def _offenders(calls):
        """The rule, in one place.

        Both the tree sweep and the synthetic case below call this. They
        each had their own copy of `"processed" in call` at first, so a
        mutation that deleted the real one passed on the strength of the
        copy -- a second opinion, which is the thing this repository keeps
        removing, appearing inside a test written to remove one.
        """
        return [
            (module, lineno, call)
            for module, lineno, call in calls
            if "processed" in call
        ]

    def test_no_mkstemp_in_the_tree_stages_into_a_processed_directory(self):
        calls = self._mkstemp_calls()

        self.assertGreaterEqual(len(calls), 12, "expected the full writer set")
        self.assertEqual(self._offenders(calls), [])

    def test_the_sweep_reads_calls_rather_than_sentences(self):
        """The correction, pinned so it cannot drift back.

        A file whose *prose* names the idiom must contribute nothing, and a
        file that calls it must contribute one -- including through the bare
        `from tempfile import mkstemp` spelling the regex could not see.
        """
        calls_in = lambda source: self._mkstemp_calls_in(source, "probe.py")

        prose = (
            "def f():\n"
            '    """Every `tempfile.mkstemp(dir=processed_dir)` is refused."""\n'
            "    # `tempfile.mkstemp(dir=processed_dir)` would be wrong here\n"
            "    return 1\n"
        )
        self.assertEqual(calls_in(prose), [], "a sentence counted as a call")

        dotted = "import tempfile\nfd, p = tempfile.mkstemp(dir=d)\n"
        bare = "from tempfile import mkstemp\nfd, p = mkstemp(dir=d)\n"
        self.assertEqual(len(calls_in(dotted)), 1)
        self.assertEqual(len(calls_in(bare)), 1, "the bare spelling was invisible")

    def test_the_offender_rule_fires_on_a_writer_that_does_stage(self):
        """The rule itself, on an input that breaks it.

        The tree has no offender -- that is the whole point of the class --
        so nothing here exercised the `"processed" in call` filter, and a
        mutation deleting it passed. A negative asserted over a clean tree
        needs a positive case somewhere or it is indistinguishable from no
        rule at all.
        """
        staging = self._mkstemp_calls_in(
            "import tempfile\n"
            "fd, path = tempfile.mkstemp(dir=processed_dir, prefix='.tmp-')\n",
            "probe.py",
        )
        self.assertEqual(len(staging), 1)
        self.assertTrue(
            self._offenders(staging),
            "the offender rule does not recognise a writer staging into processed/",
        )

        elsewhere = self._mkstemp_calls_in(
            "import tempfile\n"
            "fd, path = tempfile.mkstemp(dir=incoming_dir, prefix='.tmp-')\n",
            "probe.py",
        )
        self.assertFalse(
            self._offenders(elsewhere),
            "the offender rule fires on a writer that stages somewhere else",
        )

    def test_the_real_count_is_the_one_without_the_sentences(self):
        """The measurement, kept as a number so a future reader does not
        have to re-derive it: 16 text matches, 14 actual calls."""
        import re as _re

        text_matches = 0
        for path in sorted(SRC.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            text_matches += len(
                _re.findall(
                    r"tempfile\.mkstemp\((?:[^()]|\([^()]*\))*\)",
                    path.read_text(encoding="utf-8"),
                )
            )

        self.assertEqual(len(self._mkstemp_calls()), 14)
        self.assertGreater(
            text_matches,
            len(self._mkstemp_calls()),
            "the regex no longer over-counts -- if the prose went away, say so here",
        )

    def test_the_collector_arrives_by_replacing_the_file_it_validated(self):
        """The other half, measured rather than read: the destination name is
        the source name, so whatever `incoming/` was carrying — staging name
        included — is what lands in `processed/`, already validated."""
        from collector import Collector, InMemorySeenEventStore, run_once

        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        incoming = root / "incoming"
        processed = root / "processed"
        rejected = root / "rejected"
        for directory in (incoming, processed, rejected):
            directory.mkdir(parents=True)

        event = {
            "schema_version": "1.0",
            "event_id": "EVT-STAGED",
            "timestamp": "2026-08-20T10:00:00+09:00",
            "source": "DESKTOP_1",
            "role": "CTO_BACKEND",
            "project_id": "SEARCH",
            "event_type": "STARTED",
            "status": "IN_PROGRESS",
            "summary": "s",
            "history_candidate": True,
        }
        # The exact file a Reporter killed between `mkstemp` and `os.replace`
        # leaves behind: complete JSON under the staging name.
        (incoming / ".tmp-abc123.json").write_text(
            json.dumps(event), encoding="utf-8"
        )

        summary = run_once(
            collector=Collector(seen_store=InMemorySeenEventStore()),
            incoming_dir=incoming,
            processed_dir=processed,
            rejected_dir=rejected,
            log_path=root / "collector.log",
        )

        self.assertEqual(summary.accepted, 1)
        self.assertEqual(
            [path.name for path in processed.iterdir()], [".tmp-abc123.json"]
        )


class EveryDuplicatedConstantIsHeldInStepTests(unittest.TestCase):
    """`DuplicatedRulesStayInStepTests` names three duplicated rules. The tree
    has more, and one of them was drifting with nothing watching it.

    That class opens by listing what it covers — `INCOMPLETE_WRITE_PREFIX`,
    `safe_event_filename`, `_is_sole_identifier` — and it covers those three
    well. What nothing did was ask **how many duplications there are**, which
    is the question C66 kept finding underneath a stale answer: the rule was
    right and its roster was hand-written.

    Counted by the parser, module-level assignments only (an enum member is
    not a duplicated contract, and grouping by value rather than by name
    would hide the interesting case — a pair that has already drifted looks
    like two unrelated singletons):

        INCOMPLETE_WRITE_PREFIX   4 modules   agree   already gated
        _MAX_FILENAME_STEM        3 modules   agree   not gated directly
        METADATA_TITLE            2 modules   agree   not gated
        _EVENT_ID_LABEL           2 modules   agree   not gated
        LATE_SECTION_TITLE        2 modules   **DIFFER**

    The last one is not a bug in itself — the two are a writer and a reader
    holding two spellings of one heading:

        daily/late_events.py   "## Late Events"   the line it writes
        monthly/parser.py      "Late Events"      the title its `##` regex
                                                  captures out of that line

    They correspond today. Nothing checked that they do, and the cost of
    their falling out of step is silent. Measured through the real parser,
    on a document shaped the way `daily/markdown` renders one:

        both sides agree           -> 1 item parsed, is_late=True
        Daily heading renamed only -> 0 items, no error, no warning

    A Late Event that reached Daily History would simply not reach Monthly
    consolidation. That is the shape this repository has already paid for
    once — `_LABEL_BULLET`, where a missing space after a colon in one of two
    copies produced unbounded Late Event duplication — and the reason the
    fix there was recorded as "keep the two copies in step" rather than as a
    one-line correction.
    """

    #: Duplicated names whose copies are **not** meant to be equal, with the
    #: relationship that replaces equality. A name that starts disagreeing
    #: without an entry here fails `test_every_other_duplicate_agrees`.
    KNOWN_DIFFERENT = {
        "LATE_SECTION_TITLE": (
            "daily/late_events.py writes the whole `## ` heading line; "
            "monthly/parser.py holds the title its _HEADING2 regex captures "
            "out of it. Checked as a relationship by "
            "test_the_late_events_heading_and_its_reader_correspond."
        ),
    }

    @staticmethod
    def _module_level_constants():
        """`{NAME: {module: repr(value)}}` for literal module-level constants.

        Module level only, so an enum member (`FAILED = "FAILED"` inside a
        `class`) is not mistaken for a shared contract — measured, that alone
        was the difference between 13 apparent duplicates and 5 real ones.
        """
        import ast
        from collections import defaultdict

        found = defaultdict(dict)
        for path in sorted(SRC.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                    continue
                target = node.targets[0]
                if not isinstance(target, ast.Name):
                    continue
                if not target.id.lstrip("_").isupper():
                    continue
                try:
                    value = ast.literal_eval(node.value)
                except Exception:  # noqa: BLE001 - not a literal, not a constant
                    continue
                found[target.id][path.relative_to(SRC).as_posix()] = repr(value)
        return {
            name: modules
            for name, modules in found.items()
            if len(modules) > 1
        }

    def test_the_scan_finds_the_duplications_we_know_about(self):
        """C66 §1: this class asserts negatives over a scan, so the scan is
        checked first. Naming them also makes a *disappearance* visible — a
        duplication that got refactored away should retire its entry here
        rather than silently leave the roster describing nothing."""
        duplicated = self._module_level_constants()

        self.assertGreaterEqual(len(duplicated), 5, sorted(duplicated))
        for name in (
            "INCOMPLETE_WRITE_PREFIX",
            "_MAX_FILENAME_STEM",
            "METADATA_TITLE",
            "_EVENT_ID_LABEL",
            "LATE_SECTION_TITLE",
        ):
            with self.subTest(constant=name):
                self.assertIn(name, duplicated)

    def test_every_other_duplicate_agrees(self):
        """The property. Every copy of one name holds one value, unless the
        pair is on `KNOWN_DIFFERENT` with the relationship that replaces
        equality."""
        offenders = []
        for name, modules in sorted(self._module_level_constants().items()):
            if name in self.KNOWN_DIFFERENT:
                continue
            if len(set(modules.values())) != 1:
                offenders.append(f"{name}: {modules}")

        self.assertEqual(offenders, [], offenders)

    def test_nothing_on_the_different_roster_secretly_agrees(self):
        """The other direction. An entry that has become equal is an entry
        whose explanation is now wrong, and the pair should go back to being
        checked by plain equality."""
        duplicated = self._module_level_constants()
        for name in self.KNOWN_DIFFERENT:
            with self.subTest(constant=name):
                self.assertIn(name, duplicated)
                self.assertNotEqual(
                    len(set(duplicated[name].values())),
                    1,
                    f"{name} now agrees across modules — drop it from "
                    "KNOWN_DIFFERENT and let equality check it",
                )

    #: Values duplicated across modules under **different names**, with what
    #: holds each pair together. Grouping by name cannot see these, which is
    #: the gap C67 found in this class one Sprint after it was written.
    #:
    #: Trivial literals are excluded by `_duplicated_values()` rather than
    #: listed here — `""`, `"w"`, `0`, `1` and friends are coincidence, not
    #: contract, and a roster full of them is a roster nobody reads.
    KNOWN_ALIASED = {
        "('DECISION', 'MILESTONE', 'ISSUE', 'LEARNING')": (
            "daily/markdown._CATEGORY_ORDER, daily/role_summary.CATEGORY_ORDER "
            "and monthly/markdown.SECTION_ORDER. Not held equal — they answer "
            "to docs/06 and docs/09 and may order sections differently. Held "
            "by CategoryTableCoverageTests instead: each must cover the "
            "category vocabulary, and Monthly must cover everything Daily "
            "renders."
        ),
        "('Owner', 'Event ID', 'Category', 'Decision Context', "
        "'Expected Outcome', 'Actual Outcome', 'Lessons Learned')": (
            "daily/markdown.ITEM_LABELS writes them, monthly/parser._ITEM_LABELS "
            "reads them. Held equal by test_monthly_history.py::"
            "test_the_two_readers_of_this_format_agree."
        ),
        "{'CTO_BACKEND': 'CTO Backend', 'CTO_FRONTEND': 'CTO Frontend', "
        "'CMO': 'CMO', 'COO': 'COO'}": (
            "daily/markdown._ROLE_DISPLAY_NAMES and "
            "notion/properties.ROLE_DISPLAY_NAMES. Deliberately **not** held "
            "equal — RoleDisplayTableCoverageTests records why (different "
            "specs) and guards coverage of events.ROLES instead."
        ),
    }

    #: Literals too small or too common to be evidence of anything.
    _TRIVIAL = frozenset(
        {"''", "'w'", "'/'", "'.'", "'-'", "0", "1", "-1", "2",
         "()", "[]", "{}", "True", "False", "None"}
    )

    @classmethod
    def _duplicated_values(cls):
        """`{repr(value): {(module, name), ...}}` for values that appear as a
        module-level constant in more than one module."""
        import ast
        from collections import defaultdict

        found = defaultdict(set)
        for path in sorted(SRC.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                    continue
                target = node.targets[0]
                if not isinstance(target, ast.Name):
                    continue
                if not target.id.lstrip("_").isupper():
                    continue
                try:
                    value = ast.literal_eval(node.value)
                except Exception:  # noqa: BLE001
                    continue
                rendered = repr(value)
                if rendered in cls._TRIVIAL or len(rendered) < 4:
                    continue
                found[rendered].add(
                    (path.relative_to(SRC).as_posix(), target.id)
                )
        return {
            value: where
            for value, where in found.items()
            if len({module for module, _name in where}) > 1
        }

    def test_the_value_scan_finds_the_duplications_we_know_about(self):
        """C66 §1 again: a negative over a scan, so the scan comes first."""
        self.assertGreaterEqual(len(self._duplicated_values()), 5)

    def test_every_value_duplicated_under_another_name_is_accounted_for(self):
        """The gap this class had for one Sprint.

        Grouping by name answers "do the copies of `X` agree". It cannot ask
        "is this value already written down somewhere else under a different
        name", and three pairs were: the category order, the item labels, and
        the role display table. Two of the three already had a keeper; the
        third did not, and an item in a new category was being dropped from
        Monthly History because of it (C67 §2).

        Same-name duplications are checked for equality by
        `test_every_other_duplicate_agrees`; this asks only that a value
        living under two different names is one somebody has looked at.
        """
        unaccounted = []
        for value, where in sorted(self._duplicated_values().items()):
            names = {name for _module, name in where}
            if len(names) == 1:
                continue  # same name in several modules: checked above
            if value in self.KNOWN_ALIASED:
                continue
            unaccounted.append(f"{value[:60]} :: {sorted(where)}")

        self.assertEqual(
            unaccounted,
            [],
            "a value is a module-level constant in two modules under two "
            "different names, and nothing says whether they must agree: "
            f"{unaccounted}",
        )

    def test_the_aliased_roster_names_nothing_that_is_gone(self):
        """The other direction, as for `KNOWN_DIFFERENT`."""
        present = set(self._duplicated_values())
        self.assertEqual(sorted(set(self.KNOWN_ALIASED) - present), [])

    def test_the_late_events_heading_and_its_reader_correspond(self):
        """The relationship equality cannot express, and the one this class
        was written for.

        `monthly/parser` recognises the section by the text its `##` regex
        captures. So the contract is not "the two constants are equal", it is
        "the reader's value is what the reader's own regex gets out of the
        writer's value" — checked with that regex, not with a second copy of
        the `##` stripping.
        """
        from daily.late_events import LATE_SECTION_TITLE as WRITTEN
        from monthly.parser import _HEADING2
        from monthly.parser import LATE_SECTION_TITLE as EXPECTED

        match = _HEADING2.match(WRITTEN)
        self.assertIsNotNone(
            match, f"{WRITTEN!r} is no longer a `##` heading the reader parses"
        )
        self.assertEqual(
            match.group(1),
            EXPECTED,
            "daily/late_events writes a heading monthly/parser no longer "
            "recognises — the Late Events section would be skipped whole "
            "during consolidation, silently",
        )

    def test_the_consequence_of_that_pair_drifting_is_what_it_says(self):
        """Not a re-test of the parser: the measurement the docstring quotes,
        kept executable so the claim cannot become folklore."""
        from datetime import date as date_type

        from daily.late_events import LATE_SECTION_TITLE as WRITTEN
        from monthly.parser import parse_daily_markdown

        body = (
            "# Title\n\n{heading}\n\n### Content Os\n\n- Campaign shipped.\n"
            "- Owner: CMO\n- Event ID: EVT-LATE\n- Category: DECISION\n"
        )
        target = date_type(2026, 8, 20)

        in_step = parse_daily_markdown(
            body.format(heading=WRITTEN), target_date=target
        )
        self.assertEqual([item.event_id for item in in_step.items], ["EVT-LATE"])
        self.assertTrue(in_step.items[0].is_late)

        drifted = parse_daily_markdown(
            body.format(heading="## Late Arrivals"), target_date=target
        )
        self.assertEqual(drifted.items, ())


class CompanyHistoryWritersCleanUpTooTests(unittest.TestCase):
    """The atomic-write cleanup check, extended to the writers that matter
    most — and were not in it.

    `AtomicWriteFailureCleanupTests` above injects a failing `os.replace` and
    proves eight writers remove their staging file. Measured with a
    stdlib-only line-coverage pass over the whole suite (97.7% of `src/`),
    the cleanup lines of **the Company History writers never executed**:

        src/daily/generator.py     154-158, 301-307
        src/monthly/generator.py   291-295

    Those are `daily/YYYY-MM-DD.md` and `monthly/YYYY-MM.md` — the files this
    entire pipeline exists to produce, the only ones the backup carries, and
    the ones the Monthly consolidator reads back. A leaked `.tmp-` file there
    is worse than one in `runtime/state/`: `git add -A` stages it, so it
    reaches the remote and stays in history.

    The eight-writer list was the set of sources changed in the Sprint that
    wrote it, not the set of atomic writers. Sweeping for `mkstemp` finds
    fourteen real ones (two further hits are comments).

    Two of the three write through a `try` that *reports* rather than
    propagates — `update_daily_history()` returns `LateUpdateOutcome.FAILED`
    and `consolidate_month()` returns a failed `MonthlyResult` — so the
    injected error is asserted through the returned value instead of
    `assertRaises`. The leak assertion is identical either way, and the leak
    is the property under test.
    """

    MARKER = "simulated: destination held open by another process"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.daily_dir = self.root / "daily"
        self.monthly_dir = self.root / "monthly"
        self.daily_dir.mkdir(parents=True)
        self.monthly_dir.mkdir(parents=True)

        self.candidate = HistoryCandidate(
            history_id="HIST-ATOMIC-DAILY-1",
            event_id="ATOMIC-DAILY-1",
            timestamp="2026-08-06T10:00:00+09:00",
            category="MILESTONE",
            project_id="PRJ-ATOMIC",
            role="COO",
            summary="atomic cleanup probe",
            evidence=(),
            filter_result=HistoryDecision.KEEP,
        )
        keep = self.root / "keep"
        review = self.root / "review"
        keep.mkdir()
        review.mkdir()
        self.repository = FileHistoryRepository(keep_dir=keep, review_dir=review)
        self.repository.save(self.candidate)

    def _break_replace(self):
        real_replace = os.replace

        def failing_replace(src, dst):
            raise OSError(5, self.MARKER)

        os.replace = failing_replace
        self.addCleanup(setattr, os, "replace", real_replace)

    def _leftovers(self, directory):
        return [
            p.name
            for p in directory.rglob("*")
            if p.is_file() and p.name.startswith(".tmp-")
        ]

    def test_generate_daily_history_removes_its_staging_file(self):
        from daily.generator import generate_daily_history

        self._break_replace()

        with self.assertRaises(OSError) as caught:
            generate_daily_history(
                self.repository, date(2026, 8, 6), output_dir=self.daily_dir
            )

        self.assertIn(self.MARKER, str(caught.exception))
        self.assertEqual(self._leftovers(self.daily_dir), [])

    def test_update_daily_history_removes_its_staging_file(self):
        """Reports instead of raising, so the injected failure is checked on
        the result. The staging file must be gone all the same — this one
        runs on a date whose Daily file already exists, which is exactly the
        state a Late Event merge finds."""
        from daily.generator import update_daily_history
        from daily import LateUpdateOutcome

        generate_daily_history(
            self.repository, date(2026, 8, 6), output_dir=self.daily_dir
        )
        self._break_replace()

        result = update_daily_history(
            self.repository,
            date(2026, 8, 6),
            output_dir=self.daily_dir,
            now=datetime(2026, 8, 7, 9, 0),
            keep_candidates=[
                HistoryCandidate(
                    history_id="HIST-ATOMIC-LATE-1",
                    event_id="ATOMIC-LATE-1",
                    timestamp="2026-08-06T18:00:00+09:00",
                    category="MILESTONE",
                    project_id="PRJ-ATOMIC",
                    role="COO",
                    summary="late arrival",
                    evidence=(),
                    filter_result=HistoryDecision.KEEP,
                )
            ],
        )

        self.assertEqual(result.outcome, LateUpdateOutcome.FAILED)
        self.assertIn(self.MARKER, result.error)
        self.assertEqual(self._leftovers(self.daily_dir), [])

    def test_consolidate_month_removes_its_staging_file(self):
        from monthly.generator import consolidate_month

        # Every day of the month: `consolidate_month()` refuses a month with
        # a gap and returns before it ever reaches the write, so a single
        # day would make this test pass without exercising the path it
        # exists for. Measured while writing it — the first attempt failed
        # with "30 day(s) missing" and never staged a file.
        for day in range(1, 32):
            generate_daily_history(
                self.repository, date(2026, 8, day), output_dir=self.daily_dir
            )
        self._break_replace()

        result = consolidate_month(
            year=2026,
            month=8,
            daily_dir=self.daily_dir,
            monthly_dir=self.monthly_dir,
            history_start_date=date(2026, 8, 1),
            now=datetime(2026, 9, 1, 9, 0),
        )

        self.assertIsNotNone(result.error)
        self.assertIn(self.MARKER, result.error)
        self.assertEqual(self._leftovers(self.monthly_dir), [])

    def test_the_probe_would_notice_a_leak(self):
        """Guards the guard: with the cleanup bypassed, the same assertions
        fail. Without this, a writer that stopped staging at all — writing
        straight to the destination — would pass every check above by
        producing no temp file to leak."""
        self._break_replace()
        staged = self.daily_dir / ".tmp-leaked"
        staged.write_text("residue", encoding="utf-8")

        self.assertEqual(self._leftovers(self.daily_dir), [".tmp-leaked"])


class CompanyHistoryReportsTheRealFailureNotTheCleanupsTests(
    CompanyHistoryWritersCleanUpTooTests
):
    """C49: the inner `except OSError` of the Company History writers.

    `CompanyHistoryWritersCleanUpTooTests` breaks `os.replace`; this breaks
    `os.remove` on top of it, so the cleanup of a failed write fails too.
    On Windows that is not a second scenario — it is the *same* one. Whatever
    holds the destination open (WinError 5 on `os.replace`) commonly holds
    the staging file too (WinError 32 on `os.remove`).

    Which error survives matters more here than anywhere else in the idiom.
    Two of these three writers **report instead of raising**:
    `update_daily_history()` returns `LateUpdateOutcome.FAILED` with an
    `error` string, and `consolidate_month()` returns a failed
    `MonthlyResult` — and those strings are what reach the Run Manifest, the
    `daily_late_update.log` and `ops_status.py`. If the cleanup's error
    displaced the original, an operator investigating a lost Daily Close
    would be handed "could not delete a temp file" as the reason.

    Inherits the fixtures and re-states only the assertions that change: the
    staging file now survives (nothing can remove it), and the reported
    reason must still be the write's own.
    """

    REMOVE_MARKER = "simulated: temp file held open too"

    def _break_remove(self):
        real_remove = os.remove

        def failing_remove(path):
            raise OSError(32, self.REMOVE_MARKER)

        os.remove = failing_remove
        self.addCleanup(setattr, os, "remove", real_remove)

    # The parent's three tests assert the staging file is gone, which cannot
    # hold once `os.remove` fails. Replaced rather than inherited.
    def test_generate_daily_history_reports_the_write_failure(self):
        from daily.generator import generate_daily_history

        self._break_replace()
        self._break_remove()

        with self.assertRaises(OSError) as caught:
            generate_daily_history(
                self.repository, date(2026, 8, 6), output_dir=self.daily_dir
            )

        self.assertIn(self.MARKER, str(caught.exception))
        self.assertNotIn(self.REMOVE_MARKER, str(caught.exception))
        # The staging file survives — nothing can remove it — and no *named*
        # Daily file was created. `.tmp-` is excluded explicitly because the
        # staging name keeps the `.md` suffix, so a bare `glob("*.md")` finds
        # it: the residue looks like a Daily file to anything that does not
        # know the prefix. Every reader in this project does
        # (`controltower.read_events()`, the Monthly parser, the Collector),
        # which is what makes the residue harmless rather than a phantom day.
        self.assertTrue(self._leftovers(self.daily_dir))
        self.assertEqual(
            [
                path.name
                for path in self.daily_dir.glob("*.md")
                if not path.name.startswith(".tmp-")
            ],
            [],
        )

    def test_update_daily_history_reports_the_write_failure(self):
        from daily import LateUpdateOutcome
        from daily.generator import generate_daily_history, update_daily_history

        generate_daily_history(
            self.repository, date(2026, 8, 6), output_dir=self.daily_dir
        )
        self._break_replace()
        self._break_remove()

        result = update_daily_history(
            self.repository,
            date(2026, 8, 6),
            output_dir=self.daily_dir,
            now=datetime(2026, 8, 7, 9, 0),
            keep_candidates=[
                HistoryCandidate(
                    history_id="HIST-ATOMIC-LATE-2",
                    event_id="ATOMIC-LATE-2",
                    timestamp="2026-08-06T18:00:00+09:00",
                    category="MILESTONE",
                    project_id="PRJ-ATOMIC",
                    role="COO",
                    summary="late arrival",
                    evidence=(),
                    filter_result=HistoryDecision.KEEP,
                )
            ],
        )

        self.assertEqual(result.outcome, LateUpdateOutcome.FAILED)
        self.assertIn(self.MARKER, result.error)
        self.assertNotIn(self.REMOVE_MARKER, result.error)

    def test_consolidate_month_reports_the_write_failure(self):
        from daily.generator import generate_daily_history
        from monthly.generator import consolidate_month

        for day in range(1, 32):
            generate_daily_history(
                self.repository, date(2026, 8, day), output_dir=self.daily_dir
            )
        self._break_replace()
        self._break_remove()

        result = consolidate_month(
            year=2026,
            month=8,
            daily_dir=self.daily_dir,
            monthly_dir=self.monthly_dir,
            history_start_date=date(2026, 8, 1),
            now=datetime(2026, 9, 1, 9, 0),
        )

        self.assertIsNotNone(result.error)
        self.assertIn(self.MARKER, result.error)
        self.assertNotIn(self.REMOVE_MARKER, result.error)

    def test_the_month_file_is_still_not_created(self):
        """A failed cleanup must not leave a *named* Monthly file — the
        residue is a `.tmp-` file, and `ops_status.py`'s staging-residue
        report is what finds those."""
        from daily.generator import generate_daily_history
        from monthly.generator import consolidate_month

        for day in range(1, 32):
            generate_daily_history(
                self.repository, date(2026, 8, day), output_dir=self.daily_dir
            )
        self._break_replace()
        self._break_remove()

        consolidate_month(
            year=2026,
            month=8,
            daily_dir=self.daily_dir,
            monthly_dir=self.monthly_dir,
            history_start_date=date(2026, 8, 1),
            now=datetime(2026, 9, 1, 9, 0),
        )

        named = [
            path.name
            for path in self.monthly_dir.glob("*.md")
            if not path.name.startswith(".tmp-")
        ]
        self.assertEqual(named, [])
        self.assertTrue(self._leftovers(self.monthly_dir))

    def test_the_probe_would_notice_a_leak(self):
        """Inherited assertion no longer applies — under a failing
        `os.remove` a leak is expected, so the parent's guard-the-guard is
        replaced by its opposite: the residue must actually appear."""
        from daily.generator import generate_daily_history

        self._break_replace()
        self._break_remove()

        with self.assertRaises(OSError):
            generate_daily_history(
                self.repository, date(2026, 8, 6), output_dir=self.daily_dir
            )

        self.assertTrue(self._leftovers(self.daily_dir))


class IncompleteWriteInvariantTests(unittest.TestCase):
    """A write that never committed must not be read as a finished artifact.

    Every atomic writer here stages through
    `tempfile.mkstemp(dir=<destination>, prefix=".tmp-")` and commits with one
    `os.replace()`. `AtomicWriteFailureCleanupTests` proves the *exception*
    path removes the staging file. What nothing covered is the path where
    there is no exception to handle: a process killed between the write and
    the replace — power loss, SIGKILL, a container stop — leaves the staging
    file behind, and no code in this repository ever removes it.

    That would be inert if the readers could tell the two apart. They cannot:
    every scanner lists its directory by extension, and `.tmp-....json` matches
    `glob("*.json")` exactly as a delivered Event does. Measured before the
    fix, one abandoned staging file per directory:

        transport/    run_intake() promoted `.tmp-abc.json` into incoming/ as
                      an Event, and the Collector then processed it
        master/daily/ sync_to_working_copy() reported it as `added`, so a
                      truncated day of Company History was committed and
                      pushed to the backup remote; deleting it from Master
                      then reported it as `deleted`, and because that gate
                      applies nothing while `deleted` is non-empty, every
                      later run failed too — cleaning up the garbage was what
                      armed a permanent BACKUP_FAILED
        keep/         FileHistoryRepository.list() raised JSONDecodeError on a
                      truncated one (BUG-38: blocks every Candidate for that
                      date) and returned a duplicate Candidate for a complete
                      one
        outbox/       drain() reported it `unreadable`, which makes
                      `DrainSummary.is_clear` False permanently, so the Agent
                      stopped advancing its collection date — over a file the
                      Agent itself abandoned

    The fix is one predicate, `is_incomplete_write()`, published by the writer
    (`reporter/local_output.py`) and copied verbatim into the three leaf
    packages that may not import it. This class is what keeps the copies
    honest — a divergence would silently re-open whichever consumer drifted.
    """

    COPIES = (
        "reporter/local_output.py",
        "transport/intake.py",
        "backup/working_copy.py",
        "history/file_repository.py",
    )

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_every_copy_of_the_predicate_declares_the_same_prefix(self):
        prefixes = {}
        for module in self.COPIES:
            source = (SRC / module).read_text(encoding="utf-8")
            found = re.findall(r'^INCOMPLETE_WRITE_PREFIX = "(.*)"$', source, re.M)
            self.assertEqual(len(found), 1, f"{module} must declare it exactly once")
            prefixes[module] = found[0]
        self.assertEqual(len(set(prefixes.values())), 1, prefixes)

    def test_every_copy_of_the_predicate_behaves_the_same(self):
        import backup.working_copy as backup_copy
        import history.file_repository as history_copy
        import reporter.local_output as reporter_origin
        import transport.intake as transport_copy

        implementations = (
            reporter_origin.is_incomplete_write,
            transport_copy.is_incomplete_write,
            backup_copy.is_incomplete_write,
            history_copy.is_incomplete_write,
        )
        samples = (
            (".tmp-abc123.json", True),
            (".tmp-abc123.md", True),
            (".tmp-", True),
            ("EVT-001.json", False),
            ("2026-08-13.md", False),
            (".env", False),
            ("tmp-not-staged.json", False),
            ("a.tmp-b.json", False),
        )
        for name, expected in samples:
            verdicts = {impl.__module__: impl(name) for impl in implementations}
            with self.subTest(name=name):
                self.assertEqual(set(verdicts.values()), {expected}, verdicts)

    def test_every_writer_stages_with_the_prefix_the_readers_skip(self):
        """The predicate is only correct while it describes what the writers
        actually produce. A writer that changed its `mkstemp` prefix would
        become invisible to every reader below without failing anything else.
        """
        import reporter.local_output as reporter_origin

        prefix = reporter_origin.INCOMPLETE_WRITE_PREFIX
        writers = sorted(
            path
            for path in SRC.rglob("*.py")
            if "__pycache__" not in path.parts
            and _calls_mkstemp(path.read_text(encoding="utf-8"))
        )
        self.assertGreaterEqual(len(writers), 12, "expected the full writer set")
        for path in writers:
            source = path.read_text(encoding="utf-8")
            # `tempfile.` qualified on purpose: every real call is written
            # that way, and requiring it keeps prose that merely *mentions*
            # `mkstemp(...)` out of the scan.
            for call in re.findall(r"tempfile\.mkstemp\((?:[^()]|\([^()]*\))*\)", source):
                with self.subTest(writer=str(path.relative_to(SRC)), call=call):
                    self.assertTrue(
                        'prefix="' + prefix + '"' in call
                        or "prefix=INCOMPLETE_WRITE_PREFIX" in call,
                        f"{path.name} stages with a prefix no reader skips: {call}",
                    )

    def _staging_name(self, suffix: str) -> str:
        import reporter.local_output as reporter_origin

        return reporter_origin.INCOMPLETE_WRITE_PREFIX + "killed-mid-write" + suffix

    def test_no_consumer_treats_a_staging_file_as_an_artifact(self):
        """One abandoned staging file per directory, through every scanner
        that lists that directory. Both shapes are used: truncated (the write
        died partway) and complete-but-never-replaced (the write finished and
        the process died before the rename). The second is the dangerous one —
        it parses, so no error anywhere marks it as not-an-artifact.
        """
        import backup.working_copy as backup_copy
        from agent.outbox import pending
        from history.file_repository import FileHistoryRepository
        from transport.intake import run_intake

        for shape, payload in (
            ("truncated", '{"summary"'),
            ("complete", '{"summary": 1}'),
        ):
            root = Path(tempfile.mkdtemp(dir=self.root))

            with self.subTest(shape=shape, consumer="transport.run_intake"):
                transport_dir, incoming = root / "transport", root / "incoming"
                transport_dir.mkdir(parents=True)
                staged = transport_dir / self._staging_name(".json")
                staged.write_text(payload, encoding="utf-8")
                os.utime(staged, (0, 0))
                summary = run_intake(
                    transport_dir=transport_dir,
                    incoming_dir=incoming,
                    processed_dir=root / "processed",
                    rejected_dir=root / "rejected",
                )
                self.assertEqual(summary.moved, ())
                self.assertEqual(summary.skipped_incomplete, (staged.name,))
                self.assertEqual(list(incoming.glob("*")), [])
                # Never deleted — it may still be a *live* write by another
                # process, which is the same reason it must not be promoted.
                self.assertTrue(staged.exists())

            with self.subTest(shape=shape, consumer="backup.sync_to_working_copy"):
                master, working = root / "master", root / "wc"
                (master / "daily").mkdir(parents=True)
                (master / "daily" / "2026-08-13.md").write_text("real\n", encoding="utf-8")
                staged = master / "daily" / self._staging_name(".md")
                staged.write_text(payload, encoding="utf-8")

                first = backup_copy.sync_to_working_copy(master, working)
                self.assertEqual(first.added, (os.path.join("daily", "2026-08-13.md"),))
                self.assertEqual(
                    sorted(p.name for p in (working / "daily").iterdir()),
                    ["2026-08-13.md"],
                )
                # ...and cleaning the garbage up does not arm the deletion gate.
                staged.unlink()
                self.assertEqual(
                    backup_copy.sync_to_working_copy(master, working).deleted, ()
                )

            with self.subTest(shape=shape, consumer="history.FileHistoryRepository.list"):
                keep, review = root / "keep", root / "review"
                keep.mkdir(parents=True)
                (keep / self._staging_name(".json")).write_text(payload, encoding="utf-8")
                repo = FileHistoryRepository(keep_dir=keep, review_dir=review)
                self.assertEqual(repo.list(), [])

            with self.subTest(shape=shape, consumer="agent.outbox.pending"):
                outbox = root / "outbox"
                outbox.mkdir(parents=True)
                (outbox / self._staging_name(".json")).write_text(payload, encoding="utf-8")
                (outbox / "EVT-1.json").write_text('{"real": 1}', encoding="utf-8")
                self.assertEqual([p.name for p in pending(outbox)], ["EVT-1.json"])

    def test_the_one_consumer_this_does_not_cover_and_why(self):
        """`collector/runtime.run_once()` — deliberately outside the rule.

        `reporter.local_output.write_event_json()` defaults to
        `runtime/events/incoming/`, and `Reporter.report_and_write()` passes
        that default through. So the Desktop 4 reporter — a path
        `run_once()`'s own comment names ("the Desktop 4 reporter and the
        operator both write `incoming/` directly") — can leave a staging file
        in the one directory the Collector reads.

        It is not covered, because the outcome is different in the way that
        decided the other six. Measured, both shapes:

            complete    ACCEPTED. The Event is real and reaches Company
                        History; only its filename is the staging name
            truncated   REJECTED -> `rejected/`

        Neither loses data and neither parks anything in `incoming/` — the
        file moves on in one run, which is exactly what the other six did
        not do.

        C31 update: what C27 left behind was the *name* of the alert, not
        this boundary. ATTENTION used to say "Collector가 거부한 Event 1건"
        for the truncated shape, which is a false statement — nothing was
        rejected, a write was abandoned. C27 read that as needing the same
        pipeline decision as the boundary itself; it does not. The Collector
        still consumes exactly what it consumed before (this test), and the
        report now counts a staging file in `rejected/` separately and says
        it is safe to delete
        (`IntakeBacklog.rejected_incomplete_write`,
        `test_observability.py::RejectedStagingResidueTests`).

        So this test pins one thing and one thing only: `run_once()` does not
        skip staging files in `incoming/`. If it ever starts, this fails and
        the boundary gets revisited on purpose.
        """
        from collector.collector import Collector
        from collector.runtime import run_once as collector_run_once
        from collector.state import PersistentSeenEventStore
        from events import create_event

        payload = create_event(
            source="DESKTOP_4",
            role="COO",
            project_id="PRJ",
            event_type="MILESTONE_COMPLETED",
            status="IN_PROGRESS",
            summary="s",
            history_candidate=True,
            event_id="EVT-BOUNDARY",
            timestamp="2026-08-10T10:00:00+09:00",
        ).to_json()

        for shape, content, expect in (
            ("complete", payload, "processed"),
            ("truncated", payload[:20], "rejected"),
        ):
            with self.subTest(shape=shape):
                root = Path(tempfile.mkdtemp(dir=self.root))
                incoming, processed, rejected = (
                    root / "incoming",
                    root / "processed",
                    root / "rejected",
                )
                for directory in (incoming, processed, rejected):
                    directory.mkdir(parents=True)
                staged = incoming / self._staging_name(".json")
                staged.write_text(content, encoding="utf-8")

                collector_run_once(
                    collector=Collector(
                        seen_store=PersistentSeenEventStore(state_path=root / "seen.json")
                    ),
                    incoming_dir=incoming,
                    processed_dir=processed,
                    rejected_dir=rejected,
                    log_path=root / "collector.log",
                )

                # It does NOT stay in incoming/ — the property that made the
                # other six worth fixing.
                self.assertEqual(list(incoming.iterdir()), [])
                landed = {
                    "processed": [p.name for p in processed.iterdir()],
                    "rejected": [p.name for p in rejected.iterdir()],
                }
                self.assertEqual(landed[expect], [staged.name], landed)


class AgentExitCodeContractTests(unittest.TestCase):
    """`run_agent.py`'s exit codes were pinned by nothing.

    `ExitCodeContractTests` exists for `run_company_ops.py` because BUG-36
    established why it has to: the Runner is launched by Windows Task
    Scheduler, **stdout is not captured by default**, and the exit code is
    therefore the only automatic health signal anyone gets. Every word of
    that applies to `run_agent.py` — same Task Scheduler, same
    non-captured stdout, registered by `install_agent_task.ps1` on Desktops
    1-3, which are the machines that *produce* Company History.

    Nothing tested it. Every assertion in the suite is on `AgentStatus`, the
    in-process enum; the mapping from that enum to the number the operating
    system sees had no test at all, in either direction.

    Third instance this Sprint of one discipline applied to the Runner and
    not the Agent (after lock monitoring and run-staleness). None of the
    three was a decision — each was a check aimed at one of two targets.

    The mapping, from the script's own module docstring:

        0   COMPLETED, or skipped because another Agent run holds the lock
        1   configuration error (bad/missing environment)
        2   FAILED — nothing lost; the outbox holds the work and the next
            run resumes from the same date

    `3` is deliberately absent and is asserted to stay absent: docs/14 gives
    it a specific meaning shared by `run_company_ops.py` and `ops_status.py`
    ("something needs a person"), and the Agent reaching that state is
    `pending_dates`, not an exit code.
    """

    DOCUMENTED = {0, 1, 2}

    def _returns(self):
        source = (REPO_ROOT / "run_agent.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        main = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "main"
        )
        found = set()
        for node in ast.walk(main):
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant):
                found.add(node.value.value)
        return found

    def test_main_returns_only_the_documented_codes(self):
        self.assertEqual(self._returns(), self.DOCUMENTED)

    def test_the_module_docstring_documents_every_code_it_returns(self):
        """The script's own header is the contract an operator reads first."""
        source = (REPO_ROOT / "run_agent.py").read_text(encoding="utf-8")
        docstring = ast.get_docstring(ast.parse(source)) or ""

        self.assertIn("Exit codes:", docstring)
        for code in sorted(self._returns()):
            with self.subTest(code=code):
                self.assertRegex(docstring, rf"(?m)^\s+{code}\s")

    def test_the_operator_guide_documents_the_same_codes(self):
        """`AGENT.md` §6 states them for the operator. Two documents and one
        program must not disagree about what the scheduled task will show."""
        guide = (REPO_ROOT / "AGENT.md").read_text(encoding="utf-8")
        line = next(
            item for item in guide.splitlines() if "`run_agent.py` 종료 코드" in item
        )

        for code in sorted(self._returns()):
            with self.subTest(code=code):
                self.assertIn(f"`{code}`", line)

    def test_three_is_not_used_by_the_agent(self):
        """docs/14 reserves 3 for "a person must look", shared by
        `run_company_ops.py` and `ops_status.py`. An Agent that started
        returning it would silently change what that number means."""
        self.assertNotIn(3, self._returns())

    def test_a_lock_skip_is_success_not_failure(self):
        """The one mapping that is easy to get backwards, and the one the
        Agent Lock report earlier this Sprint depends on being true: a run
        that skipped because another holds the lock exits 0, which is why a
        *stale* lock is invisible to Task Scheduler and needed reporting of
        its own."""
        source = (REPO_ROOT / "run_agent.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        main = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "main"
        )

        skip_branch = None
        for node in ast.walk(main):
            if isinstance(node, ast.If) and "SKIPPED_ALREADY_RUNNING" in ast.dump(node.test):
                skip_branch = node
        self.assertIsNotNone(skip_branch, "the lock-skip branch moved or was renamed")

        returns = [
            child.value.value
            for child in ast.walk(skip_branch)
            if isinstance(child, ast.Return) and isinstance(child.value, ast.Constant)
        ]
        self.assertEqual(returns, [0])

    def test_a_failed_run_is_two_not_one(self):
        """1 is configuration only — a run that never started. A FAILED run
        did start and left recoverable work in the outbox, and collapsing
        the two would tell an operator to check their environment when the
        environment is fine."""
        source = (REPO_ROOT / "run_agent.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        main = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "main"
        )

        failed_branch = None
        for node in ast.walk(main):
            if isinstance(node, ast.If) and "AgentStatus" in ast.dump(node.test) \
                    and "FAILED" in ast.dump(node.test):
                failed_branch = node
        self.assertIsNotNone(failed_branch, "the FAILED branch moved or was renamed")

        returns = [
            child.value.value
            for child in ast.walk(failed_branch)
            if isinstance(child, ast.Return) and isinstance(child.value, ast.Constant)
        ]
        self.assertEqual(returns, [2])

    def test_every_configuration_error_exits_one(self):
        """Three separate paths reach it (bad profile, unusable sync folder,
        unreadable/mismatched state). All three are the same answer to the
        operator: nothing ran, fix the setup."""
        source = (REPO_ROOT / "run_agent.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        main = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "main"
        )

        handlers = [n for n in ast.walk(main) if isinstance(n, ast.ExceptHandler)]
        self.assertGreaterEqual(len(handlers), 3, "expected the config/state handlers")
        for handler in handlers:
            returns = [
                child.value.value
                for child in ast.walk(handler)
                if isinstance(child, ast.Return) and isinstance(child.value, ast.Constant)
            ]
            if returns:
                with self.subTest(handler=ast.dump(handler.type or ast.Constant(None))[:60]):
                    self.assertEqual(returns, [1])


class LockSkippedRunContractTests(unittest.TestCase):
    """E-13: what a lock-skipped run reports — pinned, since nothing did.

    docs/14 §7 states one half of this contract and not the other:

        Lock을 얻지 못한 실행은 Manifest를 **쓰지 않는다.** 한 일이 없으므로
        보고할 것이 없고, 실제로 일한 직전 실행의 기록을 빈 것으로 덮어쓰면
        안 된다.

    It does not say what the *process* returns. E-13 records that gap and
    calls it documentation completeness rather than a bug, adding that the
    behaviour "**이는 일관되며 테스트도 있다**".

    Re-checked (C30): the exit code half had **no test**. Every existing
    `_print_result()` test passes a completed run's result tuple; none passes
    `None`, which is the lock-skip path. So the claim was about the
    function's general coverage, not about this branch — the same shape C29
    found in A-10, one level smaller.

    Measured, and now asserted:

        _print_result(None) -> 0, "[SKIPPED] …" on stdout, stderr empty

    **Why the exit code matters more than "documentation completeness"
    suggests.** This is the branch a *stale* lock makes permanent (BUG-42),
    and the Runner is launched by Task Scheduler, whose only automatic health
    signal is that number. A run that skips forever reports success forever —
    which is exactly the reasoning C27 used for `run_agent.py`'s identical
    branch, where the exit code was also unpinned until it was pinned.

    Still SKIP for the fix: writing the code into docs/14 §7 is a spec edit.
    This decides nothing; it stops the undocumented half from changing
    unnoticed, and states in one place why it is worth documenting.
    """

    def _entrypoint(self):
        import importlib

        sys.path.insert(0, str(REPO_ROOT))
        try:
            return importlib.import_module("run_company_ops")
        finally:
            sys.path.remove(str(REPO_ROOT))

    def _skip_run(self):
        import contextlib
        import io

        module = self._entrypoint()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = module._print_result(None)
        return code, out.getvalue(), err.getvalue()

    def test_a_lock_skipped_run_exits_zero(self):
        code, _out, _err = self._skip_run()

        self.assertEqual(code, 0)

    def test_it_says_so_on_stdout_and_writes_nothing_to_stderr(self):
        """A skip is not an error. Putting it on stderr would make every
        contended run look like a failure to a log scraper."""
        _code, out, err = self._skip_run()

        self.assertIn("[SKIPPED]", out)
        self.assertEqual(err, "")

    def test_the_documented_half_really_is_documented(self):
        """The premise of calling this a *gap*: §7 states the manifest rule
        and says nothing about the exit code. If the spec ever gains the
        second half, this fails and E-13 can be closed."""
        spec = (REPO_ROOT / "docs" / "14_RUN_CONTRACT.md").read_text(encoding="utf-8")
        section = spec.split("## 7.", 1)[1].split("\n## ", 1)[0]

        self.assertIn("Manifest를 **쓰지 않는다.**", section)
        self.assertNotIn("Exit", section)
        self.assertNotIn("종료 코드", section)

    def test_zero_is_not_reused_for_anything_that_did_work(self):
        """0 means "nothing to report", and a run that *did* work only
        reaches 0 through the manifest's own SUCCESS mapping. Two different
        paths to the same number, both meaning "no action needed"."""
        from runsummary import OverallStatus, exit_code_for

        code, _out, _err = self._skip_run()

        self.assertEqual(code, exit_code_for(OverallStatus.SUCCESS))

    def test_the_agent_branch_agrees(self):
        """`run_agent.py` has the identical branch and C27 pinned it at 0.
        Two entrypoints, one meaning — if they ever disagree, an operator
        watching Task Scheduler would learn the wrong lesson from whichever
        they saw first."""
        source = (REPO_ROOT / "run_agent.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        main = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "main"
        )
        skip_branch = next(
            node
            for node in ast.walk(main)
            if isinstance(node, ast.If) and "SKIPPED_ALREADY_RUNNING" in ast.dump(node.test)
        )
        returns = [
            child.value.value
            for child in ast.walk(skip_branch)
            if isinstance(child, ast.Return) and isinstance(child.value, ast.Constant)
        ]

        code, _out, _err = self._skip_run()
        self.assertEqual(returns, [code])


class BackupLogFieldsThatReachAnArtifactTests(unittest.TestCase):
    """E-14's mitigation claim, measured field by field.

    E-14 records that the Backup Log (docs/08 §68-69) is unimplemented and
    SKIPs it — writing `runtime/logs/backup/` is a new permanent artifact
    path and docs/14 §2 fixes the Artifact Taxonomy. That reasoning holds.

    What it also says, as the reason the gap is tolerable, is a *claim about
    the code*:

        §68의 9개 필드 중 타임스탬프 둘을 뺀 전부는 이미 Run Manifest의
        `backup` component와 `backup_state.json`로 운영자에게 도달한다.

    Measured (C30), that is not quite right. Of the nine fields:

        run_id          the manifest carries its own, pinned equal elsewhere
        backup_start    excluded by the claim
        backup_end      excluded by the claim
        final_status    recorded (`status`, and `backup_state.json`)
        commit_hash     recorded (both paths, and `backup_state.json`)
        changed_files   recorded as a COUNT, never the list
        deleted_files   recorded as a COUNT, and only on the success path
        push_result     recorded only as the FAILURE reason — a successful
                        push records it nowhere
        source          **recorded nowhere at all**

    So the honest version is "six of nine reach an artifact, two of those six
    only as a size, one only when it fails, and one never". `source` is the
    field that says *which* Local Master a backup came from, which is the one
    that matters on a machine that has more than one.

    A second, separate reduction: C27 made failing components print their
    metrics, and deliberately left successful ones silent. A successful
    backup therefore reaches the *manifest file* but not the operator's
    screen — `backup_state.json` (surfaced since C27) is what they see.

    Still SKIP for the fix. This changes nothing about the decision; it
    replaces a remembered summary with a checked one, so the cost of the gap
    is not understated when the decision is finally made.
    """

    SPEC_FIELDS = (
        "run_id", "backup_start", "source", "changed_files", "deleted_files",
        "commit_hash", "push_result", "backup_end", "final_status",
    )

    def test_the_entry_still_carries_exactly_the_nine_spec_fields(self):
        import dataclasses

        from backup.log import BackupLogEntry

        names = tuple(f.name for f in dataclasses.fields(BackupLogEntry))

        self.assertEqual(names, self.SPEC_FIELDS)

    def _recorded_metric_names(self):
        """Metric keyword names the runner passes for the backup component."""
        source = (SRC / "app" / "runner.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        names: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr not in {"ok", "failed"}:
                continue
            if not node.args or not (
                isinstance(node.args[0], ast.Name) and node.args[0].id == "C_BACKUP"
            ):
                continue
            names.update(kw.arg for kw in node.keywords if kw.arg)
        return names

    def test_source_reaches_no_artifact(self):
        """The field E-14's summary implied was covered."""
        recorded = self._recorded_metric_names()

        self.assertNotIn("source", recorded)

    def test_push_result_reaches_an_artifact_only_when_it_failed(self):
        """It is passed as `reason` on the two failure paths and on neither
        success path, so a successful push records it nowhere."""
        source = (SRC / "app" / "runner.py").read_text(encoding="utf-8")

        self.assertIn("reason=backup_entry.push_result or \"\"", source)
        self.assertNotIn("push_result=backup_entry.push_result", source)

    def test_the_file_lists_are_recorded_as_sizes_not_contents(self):
        """§68 asks for the fields; the manifest carries their lengths."""
        source = (SRC / "app" / "runner.py").read_text(encoding="utf-8")

        self.assertIn("changed_files=len(backup_entry.changed_files)", source)
        self.assertIn("deleted_files=len(backup_entry.deleted_files)", source)

    def test_status_and_commit_hash_really_do_reach_an_artifact(self):
        """The half of the claim that holds — asserted so the correction
        cannot be read as "nothing reaches an artifact"."""
        recorded = self._recorded_metric_names()

        self.assertIn("commit_hash", recorded)
        self.assertIn("status", recorded)

    def test_backup_state_carries_the_durable_three(self):
        import dataclasses

        from backup.state import BackupState

        names = {f.name for f in dataclasses.fields(BackupState)}

        self.assertEqual(
            names,
            {"last_successful_backup", "last_backup_commit", "backup_status"},
        )

    def test_a_successful_backup_prints_none_of_this_to_the_operator(self):
        """C27's deliberate choice, stated here because it is the second half
        of "what reaches the operator": metrics print for failing components
        only, so a successful backup is visible through `backup_state.json`
        and nothing else."""
        ops_status = (REPO_ROOT / "ops_status.py").read_text(encoding="utf-8")

        self.assertIn("if component.status is ComponentStatus.SUCCESS:", ops_status)
        self.assertIn("continue", ops_status)


class ASilentlyDroppedEntryIsARosterNotAParagraphTests(unittest.TestCase):
    """C62's rule, with the count it stated turned into something checkable.

    C62 removed one conversion from `controltower/rollup.read_events()` — an
    entry whose `stat` failed used to `continue` in silence, so a real Event
    file left no trace anywhere. The fix routed it to the `unreadable`
    channel, and the docstring explained why the rest were left alone:

        "The three other `except OSError: continue` loops in this repository
         are left alone deliberately: none of them has an `unreadable`
         channel to report into, and in each the dropped entry surfaces as a
         *gap* in a sequence the view is already looking for holes in."

    **Counted (C66): there are two, not three.** The reasoning survived; the
    number did not, because the tree moved underneath it — `transport/intake`
    and `collector/runtime` both record now. That is E-11's shape exactly, a
    claim outliving the code it described, and the reason this is a roster
    rather than a sentence: a paragraph cannot notice a fourth one arriving.

    What the roster is for. A silent `continue` is only acceptable while the
    dropped entry shows up somewhere else. For both entries below that
    somewhere is `ops_status.py`'s Company-History-versus-backup comparison
    (`UnbackedCompanyHistoryTests`): a file dropped from the backup scope
    makes the two counts disagree, and the view is already looking for that
    disagreement. A new silent handler elsewhere would have no such reader,
    which is what this gate exists to make someone say out loud.
    """

    #: `module: (how many, why silence is acceptable there)`.
    #:
    #: **A count, not a permit (C68).** Keying by module alone would let a
    #: module that already has an allowed silent handler grow a tenth one
    #: unnoticed, which is the roster failing at the one job it has. The
    #: number is the roster; a new handler moves it and this fails.
    ALLOWED_SILENT = {
        # C93 widened the sweep from `continue` to `pass`, and these five
        # modules appeared with it. None is a new handler; all five have been
        # there the whole time, invisible because of how they were spelled.
        "src/agent/agent.py": (
            2,
            "**Neither is comfortable, and saying so is the point of a "
            "roster.** `_reject_signal()` swallows the failure of the "
            "`os.replace()` that moves a rejected Signal into "
            "`rejected/<date>/` and then returns the name as if it had "
            "moved, so the agent log records REJECTED_SIGNAL for a file "
            "still sitting where it was; the next run rejects it again. "
            "Nothing is lost -- the Signal stays and is re-read -- and "
            "nothing converges either. "
            "**C95 changed what an operator can see about it, and this "
            "entry said otherwise until then.** It read \"nothing "
            "accumulates anywhere an operator counts\"; measured after "
            "C95, with an invalid Signal whose move into "
            "`signals_rejected/` failed: `rejected_signal_count` 0 -- the "
            "counter that ought to see it -- but "
            "`undelivered_closed_signal_count` **1**. The handler is "
            "still silent; its consequence is not, though it surfaces "
            "under a name about delivery rather than about rejection. "
            "`_record_run()` is the settled one: its own "
            "comment states the trade, losing `last_run` costs a human one "
            "line of visibility while the run's actual result is already "
            "durable in `outbox/`.",
        ),
        "src/app/desktop_activity.py": (
            1,
            "An entry whose `stat()` refuses is not counted as "
            "`future_dated`, so that one anomaly check is skipped. Narrow on "
            "purpose and narrow in fact: the same entry is parsed and "
            "counted by the line immediately below, so it does not fall out "
            "of the tally -- only out of the future-date question.",
        ),
        "src/oplog.py": (
            1,
            "The append to the operations log itself. The function's own "
            "docstring states the trade before the handler reaches it: a "
            "failed log write costs visibility, never data. A handler that "
            "re-raised here would let a full disk take down the run it was "
            "only trying to describe.",
        ),
        "src/runsummary.py": (
            1,
            "The Run Manifest write. This one is only acceptable because "
            "two other things hold: `ops_status.py` reports a manifest it "
            "cannot read (\"Run Manifest를 읽을 수 없다\"), and C82 made a "
            "*stale* manifest unable to decide the exit code -- without "
            "that second guard a manifest that failed to write would have "
            "left the previous run's exit code standing, which is the "
            "defect C82 measured as `exit 0` for a crashed run.",
        ),
        "src/scheduler/lock.py": (
            1,
            "`release_lock()`'s unlink. A lock that cannot be released is "
            "left behind, and a left-behind lock is exactly the stale lock "
            "docs/07 section 27's takeover exists for -- the next run reads "
            "the recorded pid, finds it not running, and takes it over. The "
            "one shape that is NOT recovered (a stale lock whose file is "
            "read-only) has its own detector, `stale_lock_cannot_be_"
            "cleared()`, precisely because this handler cannot see it.",
        ),
        "src/backup/working_copy.py": (
            2,
            "the backup scope walk; a dropped file surfaces as Company "
            "History that is not backed up, which ops_status.py reports",
        ),
        "src/cli.py": (
            2,
            "**The one place on this roster where recording is impossible "
            "by construction, rather than traded away** (C118). Both "
            "handlers are inside `run_entrypoint()`, and it only reaches "
            "them after `output_is_gone()` has confirmed that stdout no "
            "longer accepts a flush -- i.e. after the program reading this "
            "process's output has exited. "
            "The first guards the one line that tells the operator so, on "
            "stderr, which is usually still a terminal (`tool | head`) and "
            "sometimes is not (`tool > log 2>&1` loses both at once). The "
            "second guards the `dup2` to `os.devnull` that stops the "
            "interpreter's shutdown flush from overriding the exit code "
            "with `120`; it can also raise `io.UnsupportedOperation` -- an "
            "`OSError` subclass -- for a `StringIO` stdout, which is every "
            "in-process test. "
            "Nothing is dropped: there is no entry here, only two "
            "best-effort writes to streams already known to be gone, and a "
            "handler that re-raised either one would replace a clean "
            "`OUTPUT_LOST_EXIT` with the traceback it exists to remove. "
            "Two rather than one on purpose -- a stderr that has also gone "
            "must not skip the `dup2`, which is the half that decides the "
            "exit code.",
        ),
        "ops_status.py": (
            3,
            "read one at a time in C68 and split three ways. All three "
            "surface elsewhere: two drop a date that then shows up as a hole "
            "in the daily sequence, and one skips a Secret-name comparison "
            "the screen reports as 'could not check'. "
            "**C88 removed four from this count and fixed none of them.** "
            "They already counted (`skipped += 1`); the classifier above "
            "could not read that spelling, so it called them silent, and "
            "this roster inherited the mistake — carrying three of them as "
            "*open* under a to-do (\"the next Sprint's first item\") that "
            "C68 had completed in the same Sprint. Traced to the line that "
            "prints each one: `_history_newer_than_the_last_backup`, "
            "`_junctions_in_scope`, `_monthly_lags_its_daily_source`, "
            "`_split_reviewed`. The lesson belongs to the classifier, not to "
            "them: a gate that reads one spelling of 'recorded' manufactures "
            "work that is already done. "
            "**C91 took it from seven to five, and this time by fixing "
            "them.** Both were inside `_monthly_lags_its_daily_source()` — "
            "the Monthly's `stat()` and its `read_text()` — and each let a "
            "month go uncompared while the screen still said the check had "
            "run. Measured on a tree whose 2026-07 Monthly genuinely lacks "
            "an Event its Daily carries: `finding () skipped 0`, which is "
            "the screen a healthy machine prints. "
            "**And the third handler C91 fixed was never in this count.** "
            "It is the per-day `read_daily_document()` call in the same "
            "function, written `except Exception`, and the classifier keys "
            "on `OSError` — so the one handler of the three that shortened "
            "the *finding itself* rather than skipping a month was the one "
            "this roster could not see. The number is a forcing function "
            "over the handlers it recognises, which is not the same set as "
            "the handlers that can lose a detection. "
            "**C92 took it from five to three**, closing the pair C91 "
            "recorded but did not fix: `_kept_but_not_rendered()` (E-17's "
            "detector) and `_reviewed_but_not_rendered()` (Decision "
            "Context). Both answered an unreadable Daily with a bare "
            "`continue`, so every Candidate of that date was treated exactly "
            "like one that IS in its file and the stranded list came back "
            "shorter by the ones nobody could check. They now return "
            "`(stranded, unreadable_dates)` and the caller prints **one** "
            "line for both -- a union, not a sum, because the two walk the "
            "same dates over the same directory and adding the counts would "
            "report one unreadable file as two. "
            "Line numbers are deliberately gone from the sentence above: "
            "they were stale twice over in the same Sprint (308/410 became "
            "319/421 and 1248/1369 became 1468/1589 through edits that "
            "changed none of them), and a number that drifts silently is "
            "what this class exists to object to. "
            "The three that remain are still not a permit to add a "
            "fourth: the count is the forcing function.",
        ),
    }

    #: Scanned in addition to `src/`. C68 found a real defect in
    #: `ops_status.py` — an unreadable Company History reported as "no gap" —
    #: of exactly the shape this class guards, in a file this class was not
    #: looking at. The operator-facing view is 219 KB and was outside every
    #: sweep that walks `SRC`.
    #:
    #: **"In addition to `src/`" is the whole rule, and C81 is what happens
    #: when an entry forgets it.** This tuple carried `"review_cli.py"`, and
    #: the file is `src/review_cli.py`. `_oserror_handlers_that_discard()` filters
    #: the roster through `.exists()`, so the entry resolved to nothing and
    #: was **dropped without a word** — the roster claimed five files and
    #: scanned four. Measured:
    #:
    #:     ops_status.py       exists=True
    #:     run_company_ops.py  exists=True
    #:     run_agent.py        exists=True
    #:     init_notion.py      exists=True
    #:     review_cli.py       exists=False   <- silently dropped
    #:
    #: No coverage was lost — `src/review_cli.py` is under `SRC` and the
    #: `SRC.rglob()` half was already reading it, which is also why the entry
    #: never belonged here. What was lost is the roster's honesty, in the one
    #: class whose entire thesis is that a silently dropped entry has to be a
    #: roster rather than a paragraph. `test_every_entry_here_resolves`
    #: is that thesis applied to this tuple.
    ALSO_SCANNED = (
        "ops_status.py",
        "run_company_ops.py",
        "run_agent.py",
        "init_notion.py",
    )

    def test_every_entry_here_resolves(self):
        """The roster must not be able to shrink quietly (C81).

        `_oserror_handlers_that_discard()` drops what does not exist, which is
        the right thing to do with a path and the wrong thing to do with a
        *declaration*: the filter cannot tell "this tool was removed" from
        "somebody wrote the path wrong", and it answered both by scanning
        less and saying nothing.
        """
        for name in self.ALSO_SCANNED:
            with self.subTest(entry=name):
                self.assertTrue(
                    (REPO_ROOT / name).is_file(),
                    f"{name} is declared here and is not there — the "
                    "`.exists()` filter in `_oserror_handlers_that_discard()` "
                    "would drop it and scan one file fewer in silence",
                )

    def test_the_src_half_still_covers_the_tool_this_roster_gave_up(self):
        """Why removing the entry was the fix rather than correcting it.

        `src/review_cli.py` belongs to the `SRC.rglob()` half. Naming it here
        too would scan it twice and re-state where it lives in a tuple whose
        contract is "files that `SRC` does not reach".
        """
        scanned = {
            str(path.relative_to(REPO_ROOT)).replace("\\", "/")
            for path in sorted(SRC.rglob("*.py"))
            if "__pycache__" not in path.parts
        }

        self.assertIn("src/review_cli.py", scanned)

        # The contract, not the one string. A first draft asserted
        # `"review_cli.py" not in ALSO_SCANNED`, and a mutation that re-added
        # it as `"src/review_cli.py"` passed -- a different string, the same
        # mistake, and the file scanned twice. What the tuple means is
        # "files `SRC.rglob()` does not reach", so that is what is checked.
        for entry in self.ALSO_SCANNED:
            with self.subTest(entry=entry):
                self.assertFalse(
                    (REPO_ROOT / entry).resolve().is_relative_to(SRC.resolve()),
                    f"{entry} is inside src/ and is already swept by the "
                    "SRC.rglob() half -- naming it here scans it twice and "
                    "restates where it lives",
                )

    @staticmethod
    def _handlers_that_discard(source, module):
        """`(module, line, records_anything)` for every OSError handler in
        `source` that **discards** the failure.

        Discarding has two spellings and this used to read one of them:

            except OSError:
                continue        <- seen
            except OSError:
                pass            <- not seen

        `continue` was the whole predicate, so `pass` was invisible.
        **C88's lesson in a second position.** There, a gate that recognised
        one spelling of *recorded* manufactured work that was already done;
        here, a gate that recognises one spelling of *silent* granted a
        standing exemption to whichever handlers happened to be written the
        other way. Measured when the predicate was widened: 3 silent
        handlers became 11, in seven modules rather than two, and five of
        those modules had never carried a line of rationale.

        **The cleanup idiom is excluded, and that is not a loophole.** This
        shape is in every writer in the tree:

            try:
                ...write, fsync, os.replace...
            except BaseException:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass          <- discarded on purpose
                raise             <- the real failure still propagates

        The inner `pass` drops a *cleanup* error while the original
        exception is re-raised one line later, so the caller is told. 16 of
        the 24 `pass` handlers in `src/` are this. Counting them would put 16
        permanently-justified entries on a roster whose entire value is that
        every entry needs a reason -- the standing-alarm failure this
        repository keeps removing. The exclusion is structural (an ancestor
        handler containing a `raise`), not a list of blessed files.

        Takes source text rather than a path so the predicate can be tested
        on inputs that need not exist in the tree
        (`TheSweepReadsBothSpellingsOfSilentTests`). Before that it could
        only be run against the real repository, which is how a predicate
        that saw half the handlers stayed green.
        """
        import ast

        tree = ast.parse(source)
        parents = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node

        def is_cleanup_for_a_reraise(handler):
            """Is this handler cleaning up inside a handler that re-raises?"""
            node = handler
            while node in parents:
                node = parents[node]
                if isinstance(node, ast.ExceptHandler) and any(
                    isinstance(inner, ast.Raise) for inner in ast.walk(node)
                ):
                    return True
                if isinstance(node, ast.FunctionDef):
                    return False
            return False

        found = []
        for handler in [
            node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)
        ]:
            kind = handler.type
            if isinstance(kind, ast.Name):
                names = {kind.id}
            elif isinstance(kind, ast.Tuple):
                names = {e.id for e in kind.elts if isinstance(e, ast.Name)}
            else:
                names = set()
            if "OSError" not in names:
                continue
            discards = any(
                isinstance(inner, ast.Continue) for inner in ast.walk(handler)
            ) or all(isinstance(stmt, ast.Pass) for stmt in handler.body)
            if not discards:
                continue
            if is_cleanup_for_a_reraise(handler):
                continue
            # What counts as "the dropped entry surfaced".
            #
            # **C88 added the AugAssign line, and the reason is the
            # rest of this class.** Recording by *calling* something
            # was the only spelling this saw — and the spelling
            # C68's own fixes used was `skipped += 1`. Four handlers
            # that count, and whose counts are read and printed,
            # were therefore classified `records=False`, and the
            # roster below carried three of them as "open" with a
            # to-do C68 had already finished.
            #
            # An augmented assignment is recording only while the
            # name it increments leaves the function. That is checked
            # rather than assumed:
            # `test_every_counted_handler_reaches_the_operator`.
            records = any(
                (
                    isinstance(inner, ast.Call)
                    and (
                        getattr(inner.func, "attr", None)
                        in ("append", "add", "setdefault")
                        or getattr(inner.func, "id", None) == "_log"
                    )
                )
                or isinstance(inner, ast.AugAssign)
                for inner in ast.walk(handler)
            )
            found.append((module, handler.lineno, records))
        return found

    @staticmethod
    def _oserror_handlers_that_discard():
        """The whole sweep: `src/` plus the tools `SRC.rglob()` cannot reach."""
        cls = ASilentlyDroppedEntryIsARosterNotAParagraphTests
        roots = [
            REPO_ROOT / name
            for name in cls.ALSO_SCANNED
            if (REPO_ROOT / name).exists()
        ]
        found = []
        for path in sorted(SRC.rglob("*.py")) + roots:
            if "__pycache__" in path.parts:
                continue
            try:
                name = path.relative_to(REPO_ROOT).as_posix()
            except ValueError:  # pragma: no cover - both are absolute
                name = path.name
            found += cls._handlers_that_discard(
                path.read_text(encoding="utf-8"), name
            )
        return found

    def test_the_sweep_finds_handlers_at_all(self):
        """C66 §1's own lesson, applied here: this class asserts a negative
        over a scan, and a scan that finds nothing would make it green."""
        self.assertGreaterEqual(len(self._oserror_handlers_that_discard()), 6)

    def test_the_sweep_reaches_outside_src(self):
        """C68. The defect that prompted this widening was in `ops_status.py`,
        and a scan that quietly stopped at `src/` would have gone on missing
        it while every other test here stayed green."""
        modules = {module for module, _line, _records in
                   self._oserror_handlers_that_discard()}

        self.assertTrue(
            any(not module.startswith("src/") for module in modules),
            f"the sweep found nothing outside src/: {sorted(modules)}",
        )

    def test_every_silent_handler_is_on_the_roster(self):
        offenders = sorted(
            f"{module}:{line}"
            for module, line, records in self._oserror_handlers_that_discard()
            if not records and module not in self.ALLOWED_SILENT
        )
        self.assertEqual(
            offenders,
            [],
            "an `except OSError: continue` that records nothing, in a module "
            "the roster does not cover — see C62: the dropped entry has to "
            f"surface somewhere, and this one has nowhere: {offenders}",
        )

    def test_no_rostered_module_grew_another_silent_handler(self):
        """C68. The half a module-keyed roster cannot do.

        `ops_status.py` is on the roster with eleven, three of which are open
        (see the roster note). Without a count, a twelfth would inherit the
        permission the eleven were granted — and the three that are already
        wrong are the argument for counting rather than trusting the module.
        """
        from collections import Counter

        actual = Counter(
            module
            for module, _line, records in self._oserror_handlers_that_discard()
            if not records
        )
        recorded = {
            module: count for module, (count, _why) in self.ALLOWED_SILENT.items()
        }

        self.assertEqual(dict(actual), recorded)

    def test_every_counted_handler_reaches_the_operator(self):
        """C88. The classifier now treats `skipped += 1` as recording, and
        that is only true while the number is *read*.

        A counter incremented in a handler and then dropped on the floor is
        exactly the silence this class exists to catch, wearing the shape of
        a fix. So every function whose `OSError` handler counts has to return
        what it counted.
        """
        source = (REPO_ROOT / "ops_status.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        counting = {}
        for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            for node in ast.walk(func):
                if not isinstance(node, ast.Try):
                    continue
                for handler in node.handlers:
                    kind = handler.type
                    if isinstance(kind, ast.Name):
                        names = {kind.id}
                    elif isinstance(kind, ast.Tuple):
                        names = {e.id for e in kind.elts if isinstance(e, ast.Name)}
                    else:
                        names = set()
                    if "OSError" not in names:
                        continue
                    if not any(isinstance(i, ast.Continue) for i in ast.walk(handler)):
                        continue
                    for inner in ast.walk(handler):
                        if isinstance(inner, ast.AugAssign) and isinstance(
                            inner.target, ast.Name
                        ):
                            counting.setdefault(func, set()).add(inner.target.id)

        self.assertTrue(
            counting,
            "no counting handler found, so the check below passes over nothing",
        )
        for func, counters in sorted(counting.items(), key=lambda kv: kv[0].name):
            with self.subTest(function=func.name):
                returned = {
                    counter
                    for node in ast.walk(func)
                    if isinstance(node, ast.Return) and node.value is not None
                    for counter in counters
                    if counter in ast.unparse(node.value)
                }
                self.assertEqual(
                    returned, counters,
                    f"{func.name} counts a dropped entry and does not return "
                    f"the count: {sorted(counters - returned)}",
                )

    def test_the_roster_names_nothing_that_is_gone(self):
        """The other direction. A roster entry for a module that no longer
        has a silent handler is the stale claim this class replaced."""
        silent_modules = {
            module
            for module, _line, records in self._oserror_handlers_that_discard()
            if not records
        }
        self.assertEqual(set(self.ALLOWED_SILENT) - silent_modules, set())

    def test_every_roster_entry_says_why(self):
        """A count with no reason is a number nobody can act on, and the
        three open handlers in `ops_status.py` are only findable because the
        note names them."""
        for module, (count, why) in self.ALLOWED_SILENT.items():
            with self.subTest(module=module):
                self.assertGreater(count, 0)
                self.assertGreater(len(why), 60, "a reason, not a label")

    def test_the_channel_that_started_this_still_reports(self):
        """`read_events()` is the one C62 converted. Not a re-test of C62 —
        a check that the conversion it made is still the reason this roster
        is short."""
        handlers = [
            (module, line, records)
            for module, line, records in self._oserror_handlers_that_discard()
            if module == "src/controltower/rollup.py"
        ]
        self.assertTrue(handlers, "rollup.py no longer has the handler at all")
        for module, line, records in handlers:
            with self.subTest(line=line):
                self.assertTrue(records, f"{module}:{line} went silent again")


class TheSweepReadsBothSpellingsOfSilentTests(unittest.TestCase):
    """C93. The predicate behind the roster, tested on its own inputs.

    `ASilentlyDroppedEntryIsARosterNotAParagraphTests` guards a count of
    silent handlers, and its predicate had never been run on anything but
    the real tree. So the one thing nothing could ask it was whether it
    recognises a silent handler at all -- and it did not recognise half of
    them:

        except OSError:
            continue        <- counted
        except OSError:
            pass            <- not counted

    Both discard the failure. `pass` is the spelling 24 of the handlers in
    `src/` use, and every one of them was outside the roster's reach while
    every test in that class stayed green. Widening the predicate moved the
    sweep from 3 silent handlers in 2 modules to 11 in 7.

    **This is C88's finding in a second position, and the pair is the
    point.** C88: a gate that read one spelling of *recorded* classified
    four handlers that do count as silent, and manufactured a to-do that was
    already finished. C93: a gate that reads one spelling of *silent*
    exempted five modules that had never been looked at. Same gate, opposite
    error, both invisible from inside the class that owns it -- because the
    only input it was ever given was the tree it was passing on.
    """

    def _handlers(self, source):
        return ASilentlyDroppedEntryIsARosterNotAParagraphTests._handlers_that_discard(
            textwrap.dedent(source), "probe.py"
        )

    def test_pass_and_continue_are_both_read_as_discarding(self):
        """The defect, stated as the two inputs that must agree."""
        with_pass = self._handlers(
            """
            def f(path):
                try:
                    path.unlink()
                except OSError:
                    pass
            """
        )
        with_continue = self._handlers(
            """
            def f(paths):
                for path in paths:
                    try:
                        path.unlink()
                    except OSError:
                        continue
            """
        )

        self.assertEqual(len(with_pass), 1, "the `pass` spelling was invisible")
        self.assertEqual(len(with_continue), 1)
        self.assertFalse(with_pass[0][2])  # records nothing
        self.assertFalse(with_continue[0][2])

    def test_a_handler_that_records_is_not_silent(self):
        """C88's half, kept: three spellings of "the dropped entry
        surfaced", and a handler using any of them is not silent."""
        for body in ("skipped += 1", "found.append(name)", '_log(path, "x")'):
            with self.subTest(body=body):
                handlers = self._handlers(
                    f"""
                    def f(paths):
                        skipped = 0
                        found = []
                        for path in paths:
                            try:
                                path.unlink()
                            except OSError:
                                {body}
                                continue
                        return skipped, found
                    """
                )

                self.assertEqual(len(handlers), 1)
                self.assertTrue(handlers[0][2], f"{body} is recording")

    def test_cleanup_inside_a_re_raising_handler_is_not_silent(self):
        """The atomic-write idiom. The inner `pass` drops a *cleanup* error
        while the original exception is re-raised one line below, so the
        caller is told and nothing is swallowed."""
        handlers = self._handlers(
            """
            def write(path, tmp_path):
                try:
                    path.write_text("x")
                except BaseException:
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
                    raise
            """
        )

        self.assertEqual(handlers, [])

    def test_the_exclusion_is_the_raise_and_not_the_nesting(self):
        """The same shape with the `raise` removed is silent again.

        Without this, "nested" and "cleans up for something that re-raises"
        are indistinguishable, and any handler could be exempted by wrapping
        it in another one.
        """
        handlers = self._handlers(
            """
            def write(path, tmp_path):
                try:
                    path.write_text("x")
                except BaseException:
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
            """
        )

        self.assertEqual(len(handlers), 1)

    def test_a_handler_that_is_not_about_OSError_is_not_the_subject(self):
        handlers = self._handlers(
            """
            def f(raw):
                try:
                    return int(raw)
                except ValueError:
                    pass
            """
        )

        self.assertEqual(handlers, [])

    def test_the_real_sweep_actually_contains_the_widened_spelling(self):
        """Anti-vacuity, and the reason the widening was worth doing.

        If every silent handler in the tree happened to use `continue`, the
        change above would be a no-op dressed as a fix. It is not: the sweep
        finds `pass`-shaped silent handlers, and they are in modules the
        roster had never named.
        """
        import ast

        by_spelling = {"pass": 0, "continue": 0}
        for module, line, records in (
            ASilentlyDroppedEntryIsARosterNotAParagraphTests
            ._oserror_handlers_that_discard()
        ):
            if records:
                continue
            source = (REPO_ROOT / module).read_text(encoding="utf-8")
            handler = next(
                node
                for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.ExceptHandler) and node.lineno == line
            )
            key = "pass" if all(
                isinstance(stmt, ast.Pass) for stmt in handler.body
            ) else "continue"
            by_spelling[key] += 1

        self.assertGreater(by_spelling["pass"], 0, "the widening changed nothing")
        self.assertGreater(by_spelling["continue"], 0, "the original half is gone")



class OneLogWriterInvariantTests(unittest.TestCase):
    """C32 §19's second lens, made enforceable: every log line in this
    repository goes through `oplog.append_line()`.

    C32 found five sinks that printed a remote- or disk-authored string
    without `one_line()`/`redact()` (§3, §16, §17 twice, §18). Every one was
    a `print()`, and the natural next question is whether the *log* files
    have the same holes — the log is where docs/04 §56 lives, and a forged
    log line is BUG-6 itself.

    Swept and clean: `collector/runtime._log`, `agent/agent._log` and
    `app/runner._append_log_line` are all aliases of, or thin wrappers over,
    `oplog.append_line()`, which applies both guards unconditionally at the
    write point. That was a claim about the code, so it is checked here
    rather than remembered — the failure mode E-11 names.

    The check is structural: **no production module may open a file in
    append mode except `oplog.py`**. Append mode is what a log writer needs
    and what nothing else in this repository does; every other writer here
    is an atomic `mkstemp` + `os.replace`. So a new log writer that
    bypasses the guards cannot be added without failing this.

    Four spellings of "append", not one (C60). The first version of this
    check asked for a *literal string mode containing "a"*, which is one way
    to say it. Mutation found three others that append just as well and were
    reported by nobody — each added to `collector/runtime.py` in turn:

        DETECTS         open(p, "a")                     <- the known spelling
        DETECTS         p.open("a")
        *** MISSES ***  os.open(p, os.O_APPEND | ...)    <- int flags, no string
        *** MISSES ***  open(p, "a" if x else "w")       <- mode not a constant
        *** MISSES ***  logging.FileHandler(p)           <- stdlib, defaults to "a"

    The last one matters most: it is the standard library's own log writer,
    it appends by default, and it is the most likely way someone adds
    logging to this repository without ever thinking about `oplog`. What it
    would write is exactly what C51 measured reaching `notion_sync.log` —
    a proxy's 502 page echoing `Authorization: Bearer ntn_...` — except with
    no `redact()` anywhere on the path.

    So the rule is now stated four ways, and each is checked by breaking it.
    """

    def _production_files(self):
        return [
            path
            for path in list(SRC.rglob("*.py")) + list(REPO_ROOT.glob("*.py"))
            if "__pycache__" not in str(path)
        ]

    #: `logging` handlers that open a file. All of them default to append.
    LOGGING_FILE_HANDLERS = (
        "FileHandler",
        "RotatingFileHandler",
        "TimedRotatingFileHandler",
        "WatchedFileHandler",
    )

    @staticmethod
    def _is_os_open(func):
        """`os.open()` — int flags, not a string mode, so it reads differently."""
        return (
            isinstance(func, ast.Attribute)
            and func.attr == "open"
            and isinstance(func.value, ast.Name)
            and func.value.id == "os"
        )

    @staticmethod
    def _is_path_open(func):
        """`some_path.open(mode)` — the mode is the first argument.

        True for any `X.open(...)` whose receiver is not the `io` module,
        which is the one attribute-spelled `open()` that still takes the
        file first.
        """
        return (
            isinstance(func, ast.Attribute)
            and func.attr == "open"
            and not (isinstance(func.value, ast.Name) and func.value.id == "io")
        )

    @classmethod
    def _append_mode_calls(cls, tree):
        """Every call that opens a file for appending, however it is spelled.

        Returns `[(spelling, lineno)]`. The spelling is in the result because
        a failure should say *which* of the four ways it found — the first
        version of this method knew only one of them.
        """
        found = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else None
            )

            # (1) os.open(path, os.O_APPEND | ...) — flags, not a mode string.
            if cls._is_os_open(func):
                flags = {
                    inner.attr
                    for inner in ast.walk(node)
                    if isinstance(inner, ast.Attribute)
                } | {
                    inner.id for inner in ast.walk(node) if isinstance(inner, ast.Name)
                }
                if "O_APPEND" in flags:
                    found.append(("os.open(O_APPEND)", node.lineno))
                continue

            # (2) logging's file handlers, every one of which defaults to "a".
            if name in cls.LOGGING_FILE_HANDLERS:
                found.append((f"logging.{name}", node.lineno))
                continue
            if name == "basicConfig" and any(
                kw.arg == "filename" for kw in node.keywords
            ):
                found.append(("logging.basicConfig(filename=)", node.lineno))
                continue

            if name not in ("open", "fdopen"):
                continue

            # (3)/(4) a string mode — literal, or one this cannot read.
            #
            # Which argument holds the mode depends on the receiver, and
            # getting this wrong is not theoretical: fixing (4) by looking
            # only at `args[1]` silently un-detected `p.open("a")`, whose
            # mode is `args[0]` because the path is the receiver. The
            # control row of the mutation matrix caught it.
            mode_index = 0 if cls._is_path_open(func) else 1
            mode_args = node.args[mode_index : mode_index + 1]
            mode_args += [kw.value for kw in node.keywords if kw.arg == "mode"]
            for arg in mode_args:
                if not isinstance(arg, ast.Constant):
                    # Cannot prove it is not append. No production call reads
                    # this way today (measured), so refusing the unprovable
                    # costs nothing and closes the `"a" if x else "w"` hole.
                    found.append((f"{name}(<computed mode>)", node.lineno))
                elif isinstance(arg.value, str) and "a" in arg.value:
                    found.append((name, node.lineno))
        return found

    def test_only_oplog_opens_a_file_in_append_mode(self):
        offenders = {}
        for path in self._production_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            calls = self._append_mode_calls(tree)
            if calls and path.name != "oplog.py":
                offenders[str(path.relative_to(REPO_ROOT))] = calls

        self.assertEqual(
            offenders,
            {},
            "a log writer that bypasses oplog.append_line()'s one_line()/redact() "
            "guards was added",
        )

    # --------------------------------------------- detector's detector (C60)
    #
    # Built by joining lines rather than as literals: this file is edited by
    # scripts, and a triple-quoted block nested inside another is how two
    # earlier generators silently truncated themselves.
    APPENDS = {
        "builtin open": (
            "def w(p, s):",
            "    with open(p, 'a', encoding='utf-8') as h:",
            "        h.write(s)",
        ),
        "Path.open": (
            "def w(p, s):",
            "    with p.open('a', encoding='utf-8') as h:",
            "        h.write(s)",
        ),
        "os.fdopen": (
            "def w(fd, s):",
            "    with os.fdopen(fd, 'a') as h:",
            "        h.write(s)",
        ),
        "os.open with O_APPEND": (
            "def w(p, s):",
            "    fd = os.open(p, os.O_APPEND | os.O_WRONLY)",
            "    os.write(fd, s.encode())",
        ),
        "mode decided at runtime": (
            "def w(p, s, x):",
            "    with open(p, 'a' if x else 'w') as h:",
            "        h.write(s)",
        ),
        "mode passed in": (
            "def w(p, s, mode):",
            "    with open(p, mode=mode) as h:",
            "        h.write(s)",
        ),
        "logging.FileHandler": (
            "def w(p):",
            "    logging.getLogger('x').addHandler(logging.FileHandler(p))",
        ),
        "logging.handlers.RotatingFileHandler": (
            "def w(p):",
            "    return RotatingFileHandler(p)",
        ),
        "logging.basicConfig(filename=)": (
            "def w(p):",
            "    logging.basicConfig(filename=p)",
        ),
    }

    #: Reads and whole-file writes. A detector that flagged these would fail
    #: on nearly every module here, and the first response to that noise is
    #: to weaken it — so precision is checked, not assumed.
    NOT_APPENDS = {
        "whole-file write": ("def w(p, s):", "    p.write_text(s, encoding='utf-8')"),
        "read via Path.open": ("def r(p):", "    return p.open('r').read()"),
        "read via open": ("def r(p):", "    return open(p, encoding='utf-8').read()"),
        "staged write": (
            "def w(p, s):",
            "    fd, tmp = tempfile.mkstemp(dir=p.parent)",
            "    with os.fdopen(fd, 'w') as h:",
            "        h.write(s)",
            "    os.replace(tmp, p)",
        ),
        "logging without a file": ("def w(s):", "    logging.getLogger('x').error(s)"),
    }

    def test_the_detector_recognises_every_way_to_append(self):
        """Nine spellings, because the first version of this gate knew two.

        Each of these was run against the real tree as a mutation before it
        was written down here; three of them passed unnoticed.
        """
        for label, lines in self.APPENDS.items():
            with self.subTest(spelling=label):
                found = self._append_mode_calls(ast.parse(chr(10).join(lines)))
                self.assertTrue(
                    found,
                    f"{label} appends to a file and the detector did not see it",
                )

    def test_the_detector_leaves_ordinary_file_access_alone(self):
        for label, lines in self.NOT_APPENDS.items():
            with self.subTest(spelling=label):
                found = self._append_mode_calls(ast.parse(chr(10).join(lines)))
                self.assertEqual(
                    found, [], f"{label} does not append, but was flagged: {found}"
                )

    def test_the_repository_still_uses_none_of_the_spellings_it_now_refuses(self):
        """The rules added in C60 are enforceable at full precision only
        because nothing here reads that way today: no `O_APPEND` anywhere, no
        `logging` in production code, and one non-constant `open()` argument
        that is `os.open`'s int flags rather than a mode string. If that ever
        stops being true the rule needs revisiting rather than silencing, and
        this is what would say so.
        """
        for path in self._production_files():
            if path.name == "oplog.py":
                continue
            with self.subTest(module=path.name):
                self.assertEqual(self._append_mode_calls(ast.parse(
                    path.read_text(encoding="utf-8"))), [])

    def test_oplog_really_is_the_one_that_appends(self):
        """The other half — if `oplog.py` stopped appending, the test above
        would pass by being vacuously true."""
        tree = ast.parse((SRC / "oplog.py").read_text(encoding="utf-8"))

        self.assertTrue(self._append_mode_calls(tree))

    def test_append_line_applies_both_guards_at_the_write_point(self):
        """Not at a caller, where one caller can forget."""
        import inspect

        import oplog

        source = inspect.getsource(oplog.append_line)

        self.assertIn("redact(one_line(body))", source)

    def test_every_log_writing_module_uses_it(self):
        """Named so the sweep's result is a list, not a claim. A module that
        writes a log line and is not here is either new or bypassing."""
        expected = {
            "src/app/runner.py",
            "src/agent/agent.py",
            "src/collector/runtime.py",
        }
        # By AST, not by substring: the four root entrypoints import
        # `one_line`/`redact` from the same module and mention
        # `append_line` in their comments, so a text search finds seven
        # "log writers" and three of them are prose.
        importers = set()
        for path in self._production_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or node.module != "oplog":
                    continue
                if any(alias.name == "append_line" for alias in node.names):
                    importers.add(
                        str(path.relative_to(REPO_ROOT)).replace("\\", "/")
                    )

        self.assertEqual(importers, expected)


class OpsStatusCannotHoldADataclassTests(unittest.TestCase):
    """C33 §4: `ops_status.py` + `@dataclass` + the test loader = 293 failures.

    Three ordinary things combine into one that is not:

      1. `ops_status.py` starts with `from __future__ import annotations`,
         so every annotation in it is a string;
      2. `dataclasses` resolves those strings against
         `sys.modules[cls.__module__]` while checking for `KW_ONLY`;
      3. every test helper that touches this file loads it with
         `importlib.util.spec_from_file_location(...)` + `exec_module()`
         and does **not** register the module in `sys.modules` first —
         which is how `RUNTIME_DIR` gets redirected per test.

    Under that loader the lookup in (2) yields `None` and the decorator dies
    with `AttributeError: 'NoneType' object has no attribute '__dict__'` —
    at *import* time, so every test that loads the module fails, whatever it
    was actually testing. Measured when `StoredCandidate` was first written
    as a dataclass: 293 failures across `test_observability.py` and
    `test_history_review.py`, none of them about candidates.

    `NamedTuple` does no such resolution, which is why `StoredCandidate` is
    one. This test exists because the next person to want a record in this
    file will reach for `@dataclass` — it is what the rest of the repository
    uses — and the failure they get will point at 293 unrelated tests rather
    than at the decorator.

    Two assertions rather than one: the ban, and the reason for the ban. If
    the `from __future__ import annotations` line ever goes, the ban can go
    with it, and the second assertion is what says so.
    """

    def test_ops_status_declares_no_dataclass(self):
        source = (REPO_ROOT / "ops_status.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        decorated = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
            for d in node.decorator_list
            if (isinstance(d, ast.Name) and d.id == "dataclass")
            or (
                isinstance(d, ast.Call)
                and isinstance(d.func, ast.Name)
                and d.func.id == "dataclass"
            )
        ]

        self.assertEqual(
            decorated,
            [],
            "ops_status.py cannot hold a @dataclass while it uses "
            "`from __future__ import annotations` and the tests load it via "
            "spec_from_file_location without registering it in sys.modules "
            "— use NamedTuple (see StoredCandidate)",
        )

    def test_the_condition_that_makes_the_ban_necessary_still_holds(self):
        source = (REPO_ROOT / "ops_status.py").read_text(encoding="utf-8")

        self.assertIn("from __future__ import annotations", source)

    def test_the_module_really_does_load_under_that_loader(self):
        """The ban is only worth having if this is the thing it protects."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "ops_status_dataclass_guard", REPO_ROOT / "ops_status.py"
        )
        module = importlib.util.module_from_spec(spec)

        spec.loader.exec_module(module)  # must not raise

        self.assertTrue(hasattr(module, "StoredCandidate"))


class EveryStepAnnouncesItselfTests(unittest.TestCase):
    """C34 §1: two of the nine steps never called `recorder.begin()`.

    `_Recorder.begin()` has one job, stated in its own class docstring: it
    "knows which step is currently in flight so that an exception escaping
    any step can still be attributed to it". `run_once()`'s `finally` reads
    `recorder.current` and, if it is set, records a `STEP_ABORTED` failure
    for that component.

    `notion_sync` (step 4) and `daily` (step 6) never called it. Both are
    steps whose **first action** reads a state file that docs/10 §46
    explicitly expects to find damaged — `notion_retry_queue.json` and
    `daily_history_state.json` — so both had a live path from "ordinary
    corruption" to "unattributed abort".

    The consequence was not misattribution. It was a false SUCCESS.
    `overall_status()` folds recorded FAILED components; recording none makes
    an aborted run identical to a clean one. Measured before the fix:

        crash in step 4   STEP_ABORTED NONE   manifest SUCCESS / exit 0
        crash in step 6   STEP_ABORTED NONE   manifest SUCCESS / exit 0
        crash in step 7   STEP_ABORTED backup manifest FAILED  / exit 2

    Step 7 is the control: it calls `begin()`, and it behaves correctly.
    `ops_status.py`'s LAST RUN block — the first thing AGENT.md §6 tells an
    operator to read — printed `종합 상태 : SUCCESS (exit 0)` for a run that
    never wrote Company History and never reached Backup.

    This class is the structural half, so a tenth step cannot repeat it.
    """

    def _run_once_source(self):
        return (SRC / "app" / "runner.py").read_text(encoding="utf-8")

    def _recorder_calls(self):
        """(kind, component) for every `recorder.<kind>(C_X, ...)` in
        `run_once()`, in source order."""
        source = self._run_once_source()
        tree = ast.parse(source)
        consts = {
            node.targets[0].id: node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        }
        fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "run_once"
        )
        calls = []
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "recorder"
                and node.func.attr in ("begin", "ok", "failed", "skipped")
                and node.args
                and isinstance(node.args[0], ast.Name)
                # Only the statically-named steps. The `finally` calls
                # `recorder.failed(in_flight, ...)` with a *variable* — that
                # is the attribution mechanism itself, not a step, and it is
                # reachable precisely because some step announced itself.
                and node.args[0].id in consts
            ):
                calls.append((node.lineno, node.func.attr, consts[node.args[0].id]))
        return sorted(calls)

    def test_every_recorded_component_is_announced_first(self):
        calls = self._recorder_calls()
        announced = {component for _, kind, component in calls if kind == "begin"}
        recorded = {
            component for _, kind, component in calls if kind in ("ok", "failed", "skipped")
        }

        self.assertEqual(
            recorded - announced,
            set(),
            "a step records an outcome without recorder.begin() — an exception "
            "escaping it would be unattributed, and an aborted run would fold "
            "to SUCCESS (C34 §1)",
        )

    def test_the_announcement_comes_before_the_outcome(self):
        """`begin()` after the work would attribute nothing: the exception
        escapes before the line runs."""
        calls = self._recorder_calls()
        first_begin, first_outcome = {}, {}
        for lineno, kind, component in calls:
            if kind == "begin":
                first_begin.setdefault(component, lineno)
            else:
                first_outcome.setdefault(component, lineno)

        for component, outcome_line in sorted(first_outcome.items()):
            with self.subTest(component=component):
                self.assertLess(first_begin[component], outcome_line)

    def test_every_pipeline_component_is_announced(self):
        """The list an operator is shown and the list the Runner announces
        must be the same set, or `never_started` reports a step that ran."""
        from app.runner import PIPELINE_COMPONENTS

        announced = {c for _, kind, c in self._recorder_calls() if kind == "begin"}

        self.assertEqual(announced, set(PIPELINE_COMPONENTS))

    def test_the_two_that_were_missing_are_named(self):
        """Pinned by name, so the fix cannot be reverted quietly."""
        source = self._run_once_source()

        self.assertIn("recorder.begin(C_NOTION_SYNC)", source)
        self.assertIn("recorder.begin(C_DAILY)", source)

    def test_begin_is_what_the_finally_reads(self):
        """The link that makes the above matter. If the `finally` stopped
        reading `recorder.current`, `begin()` would be decoration."""
        import inspect

        from app import runner

        source = inspect.getsource(runner.run_once)
        tail = source[source.index("finally:"):]

        self.assertIn("recorder.current", tail)
        self.assertIn("STEP_ABORTED", tail)


class ExecutionOrderIsTheDocumentedOrderTests(unittest.TestCase):
    """C34 §2: the step order is a specification, enforced by source layout.

    Three documents fix it — docs/07 §37 lists the twelve steps, docs/09
    §50-51 puts Monthly after Daily Catch-up and before Backup, and
    `run_once()`'s own comments state two more constraints in prose:

        6.5  "Backup(7단계)보다 먼저 실행해야 갱신된 Daily 파일이 같은
              실행에서 백업된다"
        6.7  "Monthly는 이미 이 실행에서 확정된 Daily 파일만 읽는다"

    Every one of those is a *data* dependency: Late Event Update rewrites a
    Daily file that Backup must then ship, and Monthly reads Daily files
    that must already be final. Moving a step is therefore not a stylistic
    change — it silently drops a day of Company History out of that run's
    backup, or consolidates a month from Daily files that are about to
    change.

    Nothing checked any of it. `PIPELINE_COMPONENTS` is derived from
    `_ARTIFACT_REFS`, a dict whose order is its *declaration* order, and a
    test pinned only that the two are equal and nine long. C34 §1 found the
    two lists had in fact drifted apart — `notion_sync` and `daily` were
    declared but never announced — which is how the drift became visible at
    all.

    Order is taken from `recorder.begin()` calls in source order, which is
    the same signal the `finally` uses to attribute an abort, so this test
    and the runtime behaviour cannot disagree about what "the current step"
    means.
    """

    def _execution_order(self):
        source = (SRC / "app" / "runner.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        consts = {
            node.targets[0].id: node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        }
        fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "run_once"
        )
        begins = sorted(
            (node.lineno, consts[node.args[0].id])
            for node in ast.walk(fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "recorder"
            and node.func.attr == "begin"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in consts
        )
        return [component for _, component in begins]

    def test_the_declared_order_is_the_execution_order(self):
        """`PIPELINE_COMPONENTS` is what `ops_status.py` walks to report
        "시작되지 못한 단계", and an operator reads that list as a sequence."""
        from app.runner import PIPELINE_COMPONENTS

        self.assertEqual(self._execution_order(), list(PIPELINE_COMPONENTS))

    def test_the_order_is_the_one_the_pipeline_depends_on(self):
        """Each pair below is a data dependency, not a preference."""
        order = self._execution_order()
        position = {name: i for i, name in enumerate(order)}

        must_precede = [
            # intake fills incoming/ before the Collector drains it
            ("transport", "collector"),
            # Notion Sync and History Filter both read the files the
            # Collector moved into processed/
            ("collector", "notion_sync"),
            ("collector", "history_filter"),
            # the Scheduler renders the Candidates step 5 wrote
            ("history_filter", "daily"),
            # docs/06 §37: Late Event Update merges into a Daily file the
            # Scheduler has already closed
            ("daily", "late_update"),
            # docs/09 §50-51: Monthly reads Daily files that are final,
            # which includes this run's Late Event merges
            ("late_update", "monthly"),
            # run_once 6.5's own comment: the updated Daily file has to be
            # backed up in the same run
            ("late_update", "backup"),
            ("monthly", "backup"),
            # CEO Decision 4: the Dashboard records results the other steps
            # have already produced
            ("backup", "dashboard"),
        ]
        for earlier, later in must_precede:
            with self.subTest(dependency=f"{earlier} -> {later}"):
                self.assertLess(
                    position[earlier],
                    position[later],
                    f"{earlier} must run before {later}",
                )

    def test_the_dashboard_is_last(self):
        """It reports on the run, so anything after it would go unreported —
        which is BACKLOG A-18's shape, arrived at by reordering instead of
        by an abort."""
        self.assertEqual(self._execution_order()[-1], "dashboard")

    def test_the_lock_is_taken_before_the_first_step_and_released_after(self):
        """The order's outer bracket. Every dependency above assumes one
        Runner at a time (docs/07 §25)."""
        import inspect

        from app import runner

        source = inspect.getsource(runner.run_once)
        acquire = source.index("try_acquire_lock(")
        first_begin = source.index("recorder.begin(")
        release = source.index("release_lock(")

        self.assertLess(acquire, first_begin)
        self.assertLess(first_begin, release)
        self.assertIn("finally:", source[:release])

    def test_every_documented_pair_names_a_real_component(self):
        """A typo in the table above would silently assert nothing."""
        from app.runner import PIPELINE_COMPONENTS

        order = self._execution_order()

        self.assertEqual(set(order), set(PIPELINE_COMPONENTS))
        self.assertEqual(len(order), len(set(order)), "a step announces itself twice")


class OneRuntimeRootOrRefuseTests(unittest.TestCase):
    """C34 §3: `RUNTIME_DIR` looked like a knob and moved 3 of 19 paths.

    `run_company_ops.main()` derives `local_master_dir`,
    `backup_working_copy_dir` and `runner_lock_path` from its own
    `RUNTIME_DIR`. The other sixteen path parameters of `run_once()` are
    left to defaults that belong to six *other* modules, each with its own
    `PROJECT_ROOT` frozen at import.

    In production the roots are the same directory and nothing is wrong.
    The failure mode is the other case, and it was reached for real during
    C34: rebinding `RUNTIME_DIR` — the way every test and probe in this
    repository isolates `ops_status.py` — ran a genuine pipeline that wrote
    Company History into a temp tree while advancing the **live**
    `daily_history_state.json` past those days.

        daily/        six files written under the temp root
        live pointer  2026-08-10 -> 2026-08-16
        consistency   CONSISTENT -> STATE_INCONSISTENCY

    Six days that no future run will create, because the pointer is already
    past them. A run that believes it is sandboxed and corrupts production
    instead is the worst shape a knob can have.

    C31 §10 recorded the same trap in `ops_status.py` and fixed it by
    deriving per call. That fix is unavailable here — the sixteen defaults
    belong to other modules — so the incompleteness stays and stops being
    silent.
    """

    def _entrypoint(self):
        import importlib.util

        path = REPO_ROOT / "run_company_ops.py"
        spec = importlib.util.spec_from_file_location("run_company_ops_guard", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_production_roots_agree_so_the_guard_is_invisible(self):
        """It must never fire on a real deployment."""
        module = self._entrypoint()

        module._one_runtime_root_or_refuse()  # must not raise

    def test_a_rebound_runtime_dir_is_refused(self):
        module = self._entrypoint()
        module.RUNTIME_DIR = Path(tempfile.mkdtemp()) / "runtime"
        self.addCleanup(shutil.rmtree, module.RUNTIME_DIR.parent, True)

        with self.assertRaises(SystemExit) as caught:
            module._one_runtime_root_or_refuse()

        self.assertEqual(caught.exception.code, 1)

    def test_the_refusal_names_both_roots(self):
        """An operator (or a maintainer mid-probe) has to see *which* two
        paths disagree — that is the whole content of the mistake."""
        module = self._entrypoint()
        stray = Path(tempfile.mkdtemp()) / "runtime"
        self.addCleanup(shutil.rmtree, stray.parent, True)
        module.RUNTIME_DIR = stray

        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            module._one_runtime_root_or_refuse()
        message = err.getvalue()

        self.assertIn(str(stray), message)
        self.assertIn("app.runner", message)
        self.assertIn("STATE_INCONSISTENCY", message)

    def test_the_guard_runs_before_anything_else_in_main(self):
        """After the first write it would be too late — the split has
        already happened."""
        import inspect

        module = self._entrypoint()
        body = inspect.getsource(module.main)
        first_line = body.split("\n")[1].strip()

        self.assertEqual(first_line, "_one_runtime_root_or_refuse()")

    def test_the_count_in_the_message_matches_the_real_signature(self):
        """`19 paths, 3 set here` is a claim about the code. Checked, so it
        cannot quietly stop being true."""
        import ast

        runner_source = (SRC / "app" / "runner.py").read_text(encoding="utf-8")
        run_once = next(
            n
            for n in ast.walk(ast.parse(runner_source))
            if isinstance(n, ast.FunctionDef) and n.name == "run_once"
        )
        path_params = [
            a.arg for a in run_once.args.kwonlyargs if a.arg.endswith(("_dir", "_path"))
        ]

        entry_source = (REPO_ROOT / "run_company_ops.py").read_text(encoding="utf-8")
        call = next(
            n
            for n in ast.walk(ast.parse(entry_source))
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "run_once"
        )
        supplied = {kw.arg for kw in call.keywords if kw.arg in path_params}

        self.assertEqual(len(path_params), 19)
        self.assertEqual(
            supplied,
            {"local_master_dir", "backup_working_copy_dir", "runner_lock_path"},
        )
        self.assertIn("19개 경로 중 3개", entry_source)

    def test_ops_status_keeps_its_complete_knob(self):
        """The contrast that makes this guard the right shape rather than a
        workaround: `ops_status.py` really does redirect everything from
        `RUNTIME_DIR`, which is why its tests can rebind it safely. Only the
        entrypoint cannot."""
        source = (REPO_ROOT / "ops_status.py").read_text(encoding="utf-8")

        self.assertIn("def _agent_dir()", source)
        self.assertIn("return RUNTIME_DIR /", source)


class EveryFinallyKnowsWhatRunsBeforeItTests(unittest.TestCase):
    """A `finally` promises "this happens on every exit path". It promises it
    only for paths that reach the `try:`.

    **C82 is what that costs.** `app.runner.run_once()` writes the Run
    Manifest in its `finally`, and `run_company_ops.py` was changed to read
    the exit code out of that manifest — correct for every abort *inside*
    the `try:`, and wrong for one before it. A run that died at lock
    acquisition wrote no manifest, so the process returned the **previous**
    run's exit code: measured, a crashed run reported 0.

    C83 then swept production code for the same shape and found three
    function-level `try/finally` blocks, all releasing a lock, only one with
    statements between the acquire and the `try:`. That count is the kind of
    number C66 section 4 says not to leave in prose — so it lives here.

    **What this class actually asks of a new entry.** Not "do not add a
    `finally`". Only: when one appears, someone has answered *"what runs
    before it, and what does the `finally` promise that a caller may already
    be relying on?"* The roster is the forcing function; a fourth entry
    fails until it is listed with that answer.
    """

    #: `module:function` -> why the statements before its `try:` are safe.
    KNOWN = {
        "agent/agent.py:run_once": (
            "`try_acquire_lock()` is the last statement before the `try:`, so "
            "nothing can raise between acquiring the lock and entering the "
            "block that releases it."
        ),
        "scheduler/scheduler.py:run_once": (
            "Same shape. The `already_locked` branch returns before the "
            "`try:` on purpose — the caller holds the lock and releases it."
        ),
        "app/runner.py:run_once": (
            "The one with statements between the acquire and the `try:` "
            "(`_Recorder()`, two `Path()` calls, `now_iso()`), none of which "
            "raise on anything but caller error. Its `finally` also writes "
            "the Run Manifest, and C82 is the record of what that promise "
            "was read to mean: `run_company_ops.py` now passes the pre-run "
            "`run_id` so a manifest this run did not write cannot decide its "
            "exit code."
        ),
    }

    @staticmethod
    def _blocks():
        """`{module:function: [statements before the try]}` for every
        function-body-level `try/finally` in production code."""
        found = {}
        for path in sorted(SRC.rglob("*.py")) + sorted(REPO_ROOT.glob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
            rel = rel[4:] if rel.startswith("src/") else rel
            tree = ast.parse(path.read_text(encoding="utf-8"), rel)
            for func in [
                n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]:
                for index, stmt in enumerate(func.body):
                    if isinstance(stmt, ast.Try) and stmt.finalbody:
                        found[f"{rel}:{func.name}"] = func.body[:index]
        return found

    def test_the_scan_finds_the_blocks_it_is_about(self):
        """Guards the guard: an AST walk that matched nothing would make the
        roster check below pass over an empty set."""
        blocks = self._blocks()

        self.assertGreaterEqual(len(blocks), 3)
        self.assertIn("app/runner.py:run_once", blocks)

    def test_every_finally_in_production_code_is_on_the_roster(self):
        """A fourth one is not forbidden — it is unlisted, and listing it
        means answering C82's question for it."""
        found = set(self._blocks())

        self.assertEqual(
            found - set(self.KNOWN), set(),
            "a production `try/finally` is not on this roster. Add it with "
            "the answer to: what runs before the `try:`, can it raise, and "
            "does anything outside this function rely on what the `finally` "
            "promises? (C82 is what happens when it does.)",
        )

    def test_the_roster_names_nothing_that_is_gone(self):
        """The other direction. A stale entry is an explanation for code that
        no longer exists, and the next reader would trust it."""
        found = set(self._blocks())

        self.assertEqual(set(self.KNOWN) - found, set())

    def test_only_one_of_them_acts_after_taking_the_lock(self):
        """The fact C82 turned on, kept as a check rather than as prose.

        **The first draft of this test was named right and measured the wrong
        thing.** It asked which blocks have *any* statement before the
        `try:` — all three do, since the lock acquisition itself is one — so
        it listed all three and a mutation that added a statement to one of
        them passed. Found by that mutation, which is the whole reason the
        mutation existed.

        The property that matters is narrower: statements between **taking
        the lock** and entering the block that releases it. Before the lock
        there is nothing to leak; after it, a raise leaks the lock and skips
        whatever else the `finally` promised — which for `app/runner.py` is
        the Run Manifest, and that is C82.
        """
        offenders = {}
        for name, before in self._blocks().items():
            acquired = None
            for index, stmt in enumerate(before):
                if "try_acquire_lock" in ast.unparse(stmt):
                    acquired = index
            if acquired is None:
                continue
            after = [
                stmt for stmt in before[acquired + 1:]
                if not isinstance(stmt, (ast.Expr, ast.Pass))
            ]
            if after:
                offenders[name] = [ast.unparse(s).splitlines()[0] for s in after]

        self.assertEqual(
            sorted(offenders), ["app/runner.py:run_once"],
            "the set of `finally` blocks that do work after taking the lock "
            f"changed — a raise there leaks the lock and skips everything "
            f"the `finally` promised (C82): {offenders}",
        )

    def test_every_block_here_actually_takes_a_lock(self):
        """The premise of the test above. All three of these are lock
        blocks; if one ever is not, `try_acquire_lock` will not be found in
        its preamble and it would be skipped silently rather than checked."""
        for name, before in self._blocks().items():
            with self.subTest(block=name):
                self.assertTrue(
                    any("try_acquire_lock" in ast.unparse(stmt) for stmt in before),
                    f"{name} has a `finally` but takes no lock before it — "
                    "the check above cannot see it, so say here what its "
                    "`finally` is for",
                )



if __name__ == "__main__":
    unittest.main()
