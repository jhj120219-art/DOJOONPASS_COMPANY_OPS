import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from scheduler.state import (  # noqa: E402
    SchedulerState,
    SchedulerStateError,
    load_state,
    save_state,
)


class SchedulerStateTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state_path = Path(tmp.name) / "state" / "daily_history_state.json"


class RoundTripTests(SchedulerStateTestCase):
    def test_missing_file_returns_default_state(self):
        state = load_state(self.state_path)
        self.assertIsNone(state.last_successful_daily_close)

    def test_save_then_load_round_trips(self):
        original = SchedulerState(last_successful_daily_close=date(2026, 8, 5))
        save_state(self.state_path, original)

        loaded = load_state(self.state_path)

        self.assertEqual(loaded, original)


class CorruptedStateTests(SchedulerStateTestCase):
    def test_corrupted_json_raises_and_does_not_delete_the_bad_file(self):
        # docs/10_E2E_OPERATIONS_SPEC.md §46-49: State 손상 시 프로그램이
        # 임의로 History를 재생성/삭제하면 안 된다.
        #
        # State Recovery 통일 (CEO 승인 A안): this used to characterize the
        # gap — load_state() had no dedicated corruption handling and a raw
        # json.JSONDecodeError escaped, unlike collector/state.py. Every state
        # loader now reports a damaged file as its own named error instead.
        # The file itself must still survive untouched either way.
        self.state_path.parent.mkdir(parents=True)
        self.state_path.write_text("{not valid json", encoding="utf-8")

        with self.assertRaises(SchedulerStateError):
            load_state(self.state_path)

        self.assertEqual(self.state_path.read_text(encoding="utf-8"), "{not valid json")

    def test_wrong_top_level_shape_raises_the_typed_error(self):
        self.state_path.parent.mkdir(parents=True)
        self.state_path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")

        with self.assertRaises(SchedulerStateError):
            load_state(self.state_path)

    def test_valid_json_with_malformed_date_raises(self):
        self.state_path.parent.mkdir(parents=True)
        self.state_path.write_text(
            json.dumps({"last_successful_daily_close": "not-a-date"}), encoding="utf-8"
        )

        with self.assertRaises(ValueError):
            load_state(self.state_path)


if __name__ == "__main__":
    unittest.main()
