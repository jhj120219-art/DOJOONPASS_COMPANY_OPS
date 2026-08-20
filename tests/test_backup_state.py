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

    def test_a_non_string_commit_hash_raises_the_typed_error(self):
        """C49: found by branch coverage — this guard had never been executed.

        `last_backup_commit` is read back and used as a git object name. A
        JSON number or list there is not a hash, and letting it through would
        push the type error down into `git_ops`, where it surfaces as a
        subprocess failure whose message is about git rather than about a
        corrupt state file.

        Reachable the same way every other check in this function is: the
        file is JSON a person or a half-finished write can shape freely, and
        docs/10 §46 forbids repairing it silently.
        """
        self.state_path.parent.mkdir(parents=True)
        for bad in (123, ["abc"], {"sha": "abc"}, True):
            with self.subTest(value=bad):
                self.state_path.write_text(
                    json.dumps({"last_backup_commit": bad}), encoding="utf-8"
                )

                with self.assertRaises(BackupStateError) as caught:
                    load_state(self.state_path)

                self.assertIn("last_backup_commit", str(caught.exception))

    def test_a_null_commit_hash_is_accepted(self):
        """The other side: absent is the ordinary state before the first
        successful backup, and must not be an error."""
        self.state_path.parent.mkdir(parents=True)
        self.state_path.write_text(
            json.dumps({"last_backup_commit": None}), encoding="utf-8"
        )

        self.assertIsNone(load_state(self.state_path).last_backup_commit)

    def test_the_bad_file_survives(self):
        """docs/10 §46 again — the reader never repairs or deletes."""
        self.state_path.parent.mkdir(parents=True)
        raw = json.dumps({"last_backup_commit": 123})
        self.state_path.write_text(raw, encoding="utf-8")

        with self.assertRaises(BackupStateError):
            load_state(self.state_path)

        self.assertEqual(self.state_path.read_text(encoding="utf-8"), raw)

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

    def test_an_unparseable_timestamp_raises_the_typed_error(self):
        # Coverage gap found via `python -m trace` this Sprint: the
        # `except (TypeError, ValueError)` around
        # `datetime.fromisoformat(timestamp_value)` had zero executions
        # across the whole suite -- every existing corruption test hits a
        # different branch (bad JSON, wrong top-level shape, bad status
        # enum), never a bad `last_successful_backup` value specifically.
        self.state_path.parent.mkdir(parents=True)
        self.state_path.write_text(
            json.dumps({"last_successful_backup": "not-a-real-timestamp"}), encoding="utf-8"
        )

        with self.assertRaises(BackupStateError) as caught:
            load_state(self.state_path)
        self.assertIn("last_successful_backup", str(caught.exception))

    def test_a_non_string_timestamp_raises_the_typed_error(self):
        """The `TypeError` half of the same except clause: `fromisoformat()`
        raises `TypeError`, not `ValueError`, when given a non-string."""
        self.state_path.parent.mkdir(parents=True)
        self.state_path.write_text(
            json.dumps({"last_successful_backup": 20260806}), encoding="utf-8"
        )

        with self.assertRaises(BackupStateError):
            load_state(self.state_path)


if __name__ == "__main__":
    unittest.main()
