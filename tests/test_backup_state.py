import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backup.result import BackupStatus  # noqa: E402
from backup.state import (  # noqa: E402
    BackupState,
    BackupStateError,
    load_state,
    save_state,
)


class BackupStateTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state_path = Path(tmp.name) / "state" / "backup_state.json"


class RoundTripTests(BackupStateTestCase):
    def test_missing_file_returns_default_state(self):
        state = load_state(self.state_path)
        self.assertIsNone(state.last_successful_backup)
        self.assertIsNone(state.last_backup_commit)
        self.assertIsNone(state.backup_status)

    def test_save_then_load_round_trips(self):
        original = BackupState(
            last_successful_backup=datetime(2026, 8, 6, 12, 0, 0),
            last_backup_commit="abc123",
            backup_status=BackupStatus.SUCCESS,
        )
        save_state(self.state_path, original)

        loaded = load_state(self.state_path)

        self.assertEqual(loaded, original)


class CorruptedStateTests(BackupStateTestCase):
    def test_corrupted_json_raises_and_does_not_delete_the_bad_file(self):
        # docs/10_E2E_OPERATIONS_SPEC.md §46: "프로그램이 임의로 모든 History를
        # 삭제하거나 다시 생성하면 안 된다."
        #
        # State Recovery 통일 (CEO 승인 A안): this used to characterize the
        # gap — unlike collector/state.py's CollectorStateError, load_state()
        # let a raw json.JSONDecodeError escape. It now raises its own named
        # BackupStateError. The file itself must still survive untouched.
        self.state_path.parent.mkdir(parents=True)
        self.state_path.write_text("{not valid json", encoding="utf-8")

        with self.assertRaises(BackupStateError):
            load_state(self.state_path)

        self.assertEqual(self.state_path.read_text(encoding="utf-8"), "{not valid json")

    def test_wrong_top_level_shape_raises_the_typed_error(self):
        self.state_path.parent.mkdir(parents=True)
        self.state_path.write_text(json.dumps("not an object"), encoding="utf-8")

        with self.assertRaises(BackupStateError):
            load_state(self.state_path)

    def test_valid_json_with_wrong_shape_raises(self):
        self.state_path.parent.mkdir(parents=True)
        self.state_path.write_text(json.dumps({"backup_status": "NOT_A_REAL_STATUS"}), encoding="utf-8")

        with self.assertRaises(ValueError):
            load_state(self.state_path)


if __name__ == "__main__":
    unittest.main()
