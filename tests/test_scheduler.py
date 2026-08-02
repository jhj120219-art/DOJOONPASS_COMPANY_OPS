import json
import re
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from events import Event  # noqa: E402
from history import FileHistoryRepository, HistoryDecision, HistoryFilter  # noqa: E402
from scheduler import SchedulerStatus, load_state, run_once, save_state  # noqa: E402
from scheduler.state import SchedulerState  # noqa: E402


def sample_event(**overrides):
    data = {
        "schema_version": "1.0",
        "event_id": "TEST-MILESTONE-001",
        "timestamp": "2026-08-01T20:00:00+09:00",
        "source": "DESKTOP_3",
        "role": "CTO_FRONTEND",
        "project_id": "SEARCH_FRONTEND",
        "event_type": "MILESTONE_COMPLETED",
        "status": "IN_PROGRESS",
        "milestone": "Search UI",
        "summary": "Search UI implementation completed",
        "blocker": None,
        "evidence": ["TypeScript PASS"],
        "history_candidate": True,
    }
    data.update(overrides)
    return Event.from_dict(data)


class SchedulerTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.repo = FileHistoryRepository(keep_dir=root / "keep", review_dir=root / "review")
        self.filter = HistoryFilter()
        self.state_path = root / "state" / "daily_history_state.json"
        self.lock_path = root / "locks" / "company_ops.lock"
        self.daily_dir = root / "daily"

    def _run(self, **kwargs):
        kwargs.setdefault("history_start_date", date(2026, 8, 1))
        return run_once(
            self.repo,
            state_path=self.state_path,
            lock_path=self.lock_path,
            daily_output_dir=self.daily_dir,
            **kwargs,
        )


class CatchUpTests(SchedulerTestCase):
    def test_multi_day_catch_up_runs_in_order_and_never_processes_today(self):
        # last successful close = 2026-08-01, "now" = 2026-08-04 (any time).
        # Per docs/07 section 18/21, only 08-02 and 08-03 are eligible —
        # today (08-04) is never processed regardless of clock time.
        save_state(self.state_path, SchedulerState(last_successful_daily_close=date(2026, 8, 1)))

        result = self._run(now=datetime(2026, 8, 4, 15, 0))

        self.assertEqual(result.status, SchedulerStatus.COMPLETED)
        self.assertEqual(result.generated_dates, (date(2026, 8, 2), date(2026, 8, 3)))
        self.assertTrue((self.daily_dir / "2026-08-02.md").exists())
        self.assertTrue((self.daily_dir / "2026-08-03.md").exists())
        self.assertFalse((self.daily_dir / "2026-08-04.md").exists())

    def test_state_advances_to_the_last_generated_date(self):
        save_state(self.state_path, SchedulerState(last_successful_daily_close=date(2026, 8, 1)))
        self._run(now=datetime(2026, 8, 4, 9, 0))

        state = load_state(self.state_path)
        self.assertEqual(state.last_successful_daily_close, date(2026, 8, 3))

    def test_catch_up_does_not_wait_for_11am(self):
        # docs/07 section 21: already-finished days can be closed any time
        # of day, not only at/after the regular 11:00 trigger.
        save_state(self.state_path, SchedulerState(last_successful_daily_close=date(2026, 8, 6)))
        result = self._run(now=datetime(2026, 8, 8, 9, 0))  # well before 11:00

        self.assertEqual(result.generated_dates, (date(2026, 8, 7),))


class NoOpTests(SchedulerTestCase):
    def test_already_caught_up_generates_nothing(self):
        save_state(self.state_path, SchedulerState(last_successful_daily_close=date(2026, 8, 3)))
        result = self._run(now=datetime(2026, 8, 4, 12, 0))  # yesterday already closed

        self.assertEqual(result.status, SchedulerStatus.COMPLETED)
        self.assertEqual(result.generated_dates, ())

    def test_today_is_never_included_regardless_of_time_of_day(self):
        for hour in (0, 10, 11, 23):
            with self.subTest(hour=hour):
                state_path = self.state_path.with_name(f"state-{hour}.json")
                save_state(state_path, SchedulerState(last_successful_daily_close=date(2026, 8, 3)))
                result = run_once(
                    self.repo,
                    history_start_date=date(2026, 8, 1),
                    now=datetime(2026, 8, 4, hour, 0),
                    state_path=state_path,
                    lock_path=self.lock_path.with_name(f"lock-{hour}.lock"),
                    daily_output_dir=self.daily_dir,
                )
                self.assertNotIn(date(2026, 8, 4), result.generated_dates)


class FirstRunTests(SchedulerTestCase):
    def test_first_ever_run_starts_from_history_start_date(self):
        # no state file exists yet
        result = self._run(history_start_date=date(2026, 8, 1), now=datetime(2026, 8, 3, 12, 0))
        self.assertEqual(result.generated_dates, (date(2026, 8, 1), date(2026, 8, 2)))

    def test_history_start_date_is_a_required_explicit_argument(self):
        # calling run_once without history_start_date must be a TypeError,
        # never a silently guessed default (docs/07 section 50).
        with self.assertRaises(TypeError):
            run_once(self.repo, state_path=self.state_path, lock_path=self.lock_path)


class DuplicateExecutionTests(SchedulerTestCase):
    def test_second_concurrent_run_is_skipped(self):
        save_state(self.state_path, SchedulerState(last_successful_daily_close=date(2026, 8, 1)))
        now = datetime(2026, 8, 4, 11, 0)

        first = self._run(now=now)
        # simulate a second Runner starting while the (already-released,
        # for a normal run) lock... so instead directly pre-create a fresh
        # lock to simulate genuine overlap:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text(
            json.dumps({"process_id": 999999, "created_at": now.isoformat(timespec="seconds")}),
            encoding="utf-8",
        )

        second = self._run(now=now)

        self.assertEqual(first.status, SchedulerStatus.COMPLETED)
        self.assertEqual(second.status, SchedulerStatus.SKIPPED_ALREADY_RUNNING)
        self.assertEqual(second.generated_dates, ())

    def test_lock_is_released_after_a_normal_run(self):
        self._run(now=datetime(2026, 8, 2, 11, 0))
        self.assertFalse(self.lock_path.exists())

    def test_stale_lock_is_overridden(self):
        stale_time = datetime(2026, 8, 4, 5, 0)  # far more than 1 hour before "now"
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text(
            json.dumps({"process_id": 999999, "created_at": stale_time.isoformat(timespec="seconds")}),
            encoding="utf-8",
        )

        result = self._run(now=datetime(2026, 8, 4, 11, 0))
        self.assertEqual(result.status, SchedulerStatus.COMPLETED)


class FailureIsolationTests(SchedulerTestCase):
    def test_failure_on_one_date_stops_the_batch_without_skipping_ahead(self):
        class FlakyRepository:
            def __init__(self, real, fail_on_call_index):
                self._real = real
                self._fail_on_call_index = fail_on_call_index
                self._calls = 0

            def list(self, decision=None):
                self._calls += 1
                if self._calls == self._fail_on_call_index:
                    raise RuntimeError("simulated repository failure")
                return self._real.list(decision=decision)

            def save(self, *args, **kwargs):
                return self._real.save(*args, **kwargs)

            def get(self, *args, **kwargs):
                return self._real.get(*args, **kwargs)

        save_state(self.state_path, SchedulerState(last_successful_daily_close=date(2026, 8, 1)))
        flaky = FlakyRepository(self.repo, fail_on_call_index=2)  # fails on the 2nd date (08-03)

        result = run_once(
            flaky,
            history_start_date=date(2026, 8, 1),
            now=datetime(2026, 8, 5, 11, 0),  # range: 08-02, 08-03, 08-04
            state_path=self.state_path,
            lock_path=self.lock_path,
            daily_output_dir=self.daily_dir,
        )

        self.assertEqual(result.status, SchedulerStatus.FAILED)
        self.assertEqual(result.generated_dates, (date(2026, 8, 2),))
        self.assertEqual(result.failed_date, date(2026, 8, 3))
        self.assertIn("simulated repository failure", result.error)
        self.assertFalse((self.daily_dir / "2026-08-04.md").exists())

        state = load_state(self.state_path)
        self.assertEqual(state.last_successful_daily_close, date(2026, 8, 2))

    def test_lock_is_released_even_after_a_failure(self):
        class AlwaysFailingRepository:
            def list(self, decision=None):
                raise RuntimeError("boom")

            def save(self, *a, **kw):
                raise NotImplementedError

            def get(self, *a, **kw):
                raise NotImplementedError

        run_once(
            AlwaysFailingRepository(),
            history_start_date=date(2026, 8, 1),
            now=datetime(2026, 8, 3, 11, 0),
            state_path=self.state_path,
            lock_path=self.lock_path,
            daily_output_dir=self.daily_dir,
        )
        self.assertFalse(self.lock_path.exists())


class CrashRecoveryTests(SchedulerTestCase):
    def test_pre_existing_daily_file_is_treated_as_already_done(self):
        # Simulates a crash that happened after the Daily file was written
        # but before state was saved (docs/07 section 28): re-running must
        # not error out on an already-generated date, and must advance
        # state past it.
        self.daily_dir.mkdir(parents=True)
        (self.daily_dir / "2026-08-02.md").write_text("already generated", encoding="utf-8")
        save_state(self.state_path, SchedulerState(last_successful_daily_close=date(2026, 8, 1)))

        result = self._run(now=datetime(2026, 8, 4, 11, 0))

        self.assertEqual(result.status, SchedulerStatus.COMPLETED)
        self.assertEqual(result.generated_dates, (date(2026, 8, 2), date(2026, 8, 3)))
        # the pre-existing file must not have been touched/overwritten
        self.assertEqual(
            (self.daily_dir / "2026-08-02.md").read_text(encoding="utf-8"), "already generated"
        )


class KeepDataIntegrationTests(SchedulerTestCase):
    def test_generated_file_contains_keep_candidate_for_its_date(self):
        event = sample_event(event_id="TEST-SCHED-001", timestamp="2026-08-02T10:00:00+09:00")
        self.repo.save(self.filter.evaluate(event).candidate)
        save_state(self.state_path, SchedulerState(last_successful_daily_close=date(2026, 8, 1)))

        self._run(now=datetime(2026, 8, 4, 11, 0))

        content = (self.daily_dir / "2026-08-02.md").read_text(encoding="utf-8")
        self.assertIn("TEST-SCHED-001", content)


class SchedulerPathSafetyTests(unittest.TestCase):
    def test_scheduler_module_does_not_import_collector_transport_or_reporter(self):
        scheduler_src = Path(__file__).resolve().parents[1] / "src" / "scheduler"
        forbidden = (
            re.compile(r"^\s*import\s+(collector|transport|reporter)\b", re.MULTILINE),
            re.compile(r"^\s*from\s+(collector|transport|reporter)\b", re.MULTILINE),
        )
        for py_file in scheduler_src.glob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            for pattern in forbidden:
                self.assertIsNone(
                    pattern.search(content), f"{py_file} unexpectedly imports {pattern.pattern}"
                )

    def test_no_hardcoded_absolute_windows_paths_in_source(self):
        scheduler_src = Path(__file__).resolve().parents[1] / "src" / "scheduler"
        forbidden = ("C:\\Users", "D:\\", "OneDrive\\")
        for py_file in scheduler_src.glob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            code_without_docstrings = re.sub(r'""".*?"""', "", content, flags=re.DOTALL)
            for token in forbidden:
                self.assertNotIn(
                    token, code_without_docstrings, f"{token} found in {py_file} (outside docstrings)"
                )

    def test_no_thread_or_sleep_usage(self):
        scheduler_src = Path(__file__).resolve().parents[1] / "src" / "scheduler"
        forbidden = ("threading", "time.sleep", "while True", "asyncio")
        for py_file in scheduler_src.glob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, content, f"{token} found in {py_file}")


if __name__ == "__main__":
    unittest.main()
