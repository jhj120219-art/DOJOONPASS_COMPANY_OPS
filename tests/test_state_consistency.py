"""State Consistency Check tests (docs/10 §46-49).

Detection only — these tests also pin the "never repairs" contract.
"""

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from scheduler.consistency import (  # noqa: E402
    ConsistencyStatus,
    check_state_consistency,
)
from scheduler.state import SchedulerState, save_state  # noqa: E402


class StateConsistencyTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.state_path = root / "state" / "daily_history_state.json"
        self.daily_dir = root / "daily"
        self.daily_dir.mkdir(parents=True)

    def _write_state(self, close_date):
        save_state(self.state_path, SchedulerState(last_successful_daily_close=close_date))

    def _write_daily(self, day):
        (self.daily_dir / f"{day.isoformat()}.md").write_text("history", encoding="utf-8")


class ConsistentTests(StateConsistencyTestCase):
    def test_state_matching_an_existing_daily_file_is_consistent(self):
        self._write_state(date(2026, 8, 10))
        self._write_daily(date(2026, 8, 10))

        result = check_state_consistency(self.state_path, self.daily_dir)

        self.assertEqual(result.status, ConsistencyStatus.CONSISTENT)
        self.assertTrue(result.is_consistent)
        self.assertEqual(result.last_successful_daily_close, date(2026, 8, 10))


class InconsistentTests(StateConsistencyTestCase):
    def test_state_without_its_daily_file_is_flagged(self):
        # docs/10 §47's exact scenario.
        self._write_state(date(2026, 8, 10))

        result = check_state_consistency(self.state_path, self.daily_dir)

        self.assertEqual(result.status, ConsistencyStatus.STATE_INCONSISTENCY)
        self.assertFalse(result.is_consistent)
        self.assertEqual(result.last_successful_daily_close, date(2026, 8, 10))
        self.assertIn("2026-08-10", str(result.expected_history_path))

    def test_detection_never_creates_or_deletes_anything(self):
        # §46: the program must not regenerate or delete History on its own.
        self._write_state(date(2026, 8, 10))
        self._write_daily(date(2026, 8, 9))
        before = sorted(p.name for p in self.daily_dir.iterdir())
        state_before = self.state_path.read_bytes()

        check_state_consistency(self.state_path, self.daily_dir)

        self.assertEqual(sorted(p.name for p in self.daily_dir.iterdir()), before)
        self.assertEqual(self.state_path.read_bytes(), state_before)


class MissingOrDamagedStateTests(StateConsistencyTestCase):
    def test_missing_state_file_is_not_an_inconsistency(self):
        result = check_state_consistency(self.state_path, self.daily_dir)

        self.assertEqual(result.status, ConsistencyStatus.NO_STATE)

    def test_state_with_no_recorded_close_is_not_an_inconsistency(self):
        save_state(self.state_path, SchedulerState(last_successful_daily_close=None))

        result = check_state_consistency(self.state_path, self.daily_dir)

        self.assertEqual(result.status, ConsistencyStatus.NO_STATE)

    def test_corrupted_state_is_reported_not_raised(self):
        # §46: a damaged state file is an outcome to report, not a crash.
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text("{not valid json", encoding="utf-8")

        result = check_state_consistency(self.state_path, self.daily_dir)

        self.assertEqual(result.status, ConsistencyStatus.STATE_UNREADABLE)
        self.assertIsNotNone(result.detail)

    def test_corrupted_state_file_is_left_untouched(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text("{not valid json", encoding="utf-8")

        check_state_consistency(self.state_path, self.daily_dir)

        self.assertEqual(self.state_path.read_text(encoding="utf-8"), "{not valid json")


class SchedulerBehaviourUnchangedTests(StateConsistencyTestCase):
    def test_consistency_module_does_not_import_scheduler_run_once(self):
        # The check must stay a report, not a gate: wiring it into
        # Scheduler's control flow would be a policy change (docs/10 §64
        # makes an inconsistency an operator decision).
        source = (
            Path(__file__).resolve().parents[1] / "src" / "scheduler" / "consistency.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("from .scheduler import", source)
        self.assertNotIn("run_once", source)


if __name__ == "__main__":
    unittest.main()
